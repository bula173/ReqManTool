"""Business logic for ReqManTool, shared by both the REST API and the
WebSocket JSON-RPC endpoint (see ``app.py``) so the two transports can
never behave differently for the same operation.

``ProjectService`` is where every cross-cutting rule lives: id generation
and allocation, attribute validation (including rejecting direct writes to
system-managed attributes), requirement nesting/cycle checks, directional
link-configuration enforcement, and the cleanup cascades that keep links
and mirrored attributes consistent when a requirement is removed, moved,
or purged. It also owns project templates (create a project from one, save
a project as one) and the subproject tree (``parent_project_id``). See
docs/ARCHITECTURE.md for the reasoning behind each of these.

It also decides when project history is recorded (``git_repo.py``): every
public mutating method is wrapped in ``_records_history``, which notes the
projects whose files the operation wrote (``ProjectStore.recording_writes``)
and, after the operation succeeded, auto-commits them with a message
generated from the changed files (``history.describe_changes``). A new
project gets its repository right after it is created.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .csv_import import RESERVED_COLUMNS
from .formats import PROJECT_FORMAT, NewerFormatError, upgrade
from .git_repo import DEFAULT_LOG_LIMIT, LOG_LIMIT, GitError, GitHistory, RepoConflict, local_remote_path
from .history import FileChange, build_snapshot, compare_snapshots, describe_changes
from .models import AttributeDefinition, LinkConfiguration, LinkPair, Module, Project, Requirement, View
from .store import PROJECT_SLUG_PATTERN, ProjectStore, validate_prefix
from .templates import (
    BUILTIN_SOURCE,
    BUILTIN_TEMPLATES_DIR,
    LINK_REF_PREFIX,
    MAX_TEMPLATE_IMPORT_BYTES,
    USER_SOURCE,
    Template,
    TemplateAttribute,
    TemplateLinkConfiguration,
    TemplateModule,
    TemplateView,
    list_builtin_template_ids,
    make_template_id,
    parse_template,
    parse_template_text,
    read_builtin_template_text,
    split_template_id,
)

logger = logging.getLogger("reqman.service")

BUILTIN_ATTRIBUTES = [
    AttributeDefinition(id="object_text", name="ObjectText", type="text", required=True),
]

ATTRIBUTE_TYPES = ("string", "text", "integer", "boolean", "enum")

# Shared view column widths (``View.column_widths``): the key of the
# requirements table's Id column (attribute ids name the other columns) and
# the accepted width range in CSS pixels.
VIEW_ID_COLUMN = "@id"
MIN_COLUMN_WIDTH = 40
MAX_COLUMN_WIDTH = 4000

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Upper bound on the candidates ``_unique_slug`` tries (``<slug>``, ``<slug>-2``, ...).
_MAX_SLUG_ATTEMPTS = 10_000

# Project id used while dry-running a template build to validate it; never written.
_TEMPLATE_CHECK_PROJECT_ID = "template-check"


class ConflictError(ValueError):
    """The request clashes with existing data (REST 409; WebSocket treats it like any ValueError)."""


def _records_history(method: Callable) -> Callable:
    """Auto-commit the projects a successful mutation wrote (see ``ProjectService._auto_commit``).

    Applied to every public method that writes project files. Nested calls
    commit once, after the outermost one. A failed operation commits
    nothing; whatever it already wrote is included in the next commit.

    A method whose first parameter is ``project_id`` (every mutation of an
    existing project) is also refused with ``ConflictError`` (409), before
    anything is written, while that project has a git sync conflict — its
    ``current`` branch and the configured remote's have diverged (see
    ``GitHistory.sync_conflict_message``); the user resolves it in git.
    """
    parameters = list(inspect.signature(method).parameters)
    guards_project = len(parameters) > 1 and parameters[1] == "project_id"

    @functools.wraps(method)
    def wrapper(self: "ProjectService", *args: Any, **kwargs: Any) -> Any:
        if self.store.is_recording():
            return method(self, *args, **kwargs)
        if guards_project:
            project_id = args[0] if args else kwargs.get("project_id")
            if isinstance(project_id, str):
                self._require_no_sync_conflict(project_id)
        with self.store.recording_writes() as touched:
            result = method(self, *args, **kwargs)
        for project_id in sorted(touched):
            self._auto_commit(project_id)
        return result

    return wrapper


@dataclass
class ProjectStub:
    """A project listed by ``list_projects`` whose project.json cannot be loaded.

    For example one written by a newer ReqManTool, or not valid JSON. It is
    listed read-only, with the reason in ``error``, so one such project never
    breaks the whole list; opening it (``get_project`` and every other
    operation) fails with the same error and its files are never written.
    Its stored parent cannot be trusted, so it is always listed top level.
    Never persisted.
    """

    id: str
    name: str
    error: str
    parent_project_id: None = None

    def to_dict(self) -> dict[str, Any]:
        # ``parent_project_id`` is omitted like a top-level Project's.
        return {"id": self.id, "name": self.name, "error": self.error}


def _display_name(text: str | None, fallback: str) -> str:
    """The ``name`` string of a JSON object in ``text``, stripped, if usable; else ``fallback``.

    Best effort, for listing a file that cannot otherwise be loaded.
    """
    try:
        name = json.loads(text)["name"] if text is not None else None
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        name = None
    if isinstance(name, str):
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:  # e.g. a lone surrogate escape: unlistable
            name = None
    return name.strip() if isinstance(name, str) and name.strip() else fallback


def _unique_slug(
    name: str,
    exists_fn: Callable[[str], bool],
    fallback: str = "item",
    max_length: int | None = None,
) -> str:
    """A slug of ``name`` not taken per ``exists_fn``: the slug itself, else ``<slug>-2``, ``-3``, ...

    With ``max_length`` (for an id used as a file or folder name), a
    candidate longer than that raises ``ValueError``; ids are never
    truncated. At most ``_MAX_SLUG_ATTEMPTS`` candidates are tried, so
    this always ends.
    """
    base = _SLUG_NON_ALNUM.sub("-", name.strip().lower()).strip("-") or fallback
    candidate = base
    for suffix in range(2, _MAX_SLUG_ATTEMPTS + 2):
        if max_length is not None and len(candidate.encode("utf-8")) > max_length:
            raise ValueError(
                f"{fallback.capitalize()} name is too long: its id would be {len(candidate)} "
                f"characters, but the file system allows at most {max_length}"
            )
        if not exists_fn(candidate):
            return candidate
        candidate = f"{base}-{suffix}"
    raise ValueError(f"Cannot find a free id for {fallback} '{base}': too many are taken")


class ProjectService:
    """Business logic shared by the REST API and the WebSocket RPC endpoint."""

    def __init__(
        self,
        store: ProjectStore,
        builtin_templates_dir: Path | None = None,
        history: GitHistory | None = None,
    ):
        self.store = store
        self.builtin_templates_dir = (
            builtin_templates_dir if builtin_templates_dir is not None else BUILTIN_TEMPLATES_DIR
        )
        # Without one, git history is off (plain files): the default for direct use and tests.
        self.history = history if history is not None else GitHistory()

    # --- history (git) -----------------------------------------------------------

    def _describer(self, project_id: str) -> Callable[[list[FileChange]], list[str]]:
        """Commit message entries for a project's changed files, named after its current attributes."""

        def describe(changes: list[FileChange]) -> list[str]:
            try:
                project: Any = self.store.load_project(project_id).to_dict()
            except (OSError, ValueError):
                project = None
            return describe_changes(changes, project)

        return describe

    def _auto_commit(self, project_id: str) -> None:
        """Commit a project's changed data files; a git problem is recorded, never raised."""
        if not self.history.enabled:
            return
        try:
            project_dir = self.store.project_path(project_id)
        except FileNotFoundError:
            return
        self.history.auto_commit(project_dir, self._describer(project_id))

    def git_status(self, project_id: str) -> dict[str, Any]:
        """The project's history (git) status: state, explanation, branch, HEAD, pending changes.

        Read-only. A missing project is 404 and a stub (unloadable project)
        fails with its load error, like every other project operation.
        """
        self.store.load_project(project_id)
        return self.history.status(self.store.project_path(project_id))

    def enable_history(self, project_id: str) -> dict[str, Any]:
        """Start recording git history for an existing project; returns the new status.

        See ``GitHistory.enable``. A project inside another repository's work
        tree, whose ``.git`` points to another repository (``foreign``), with
        a merge/cherry-pick/revert/rebase in progress or unresolved
        conflicts, a detached HEAD, or on another branch while a ``current`` branch
        exists is refused with ``ConflictError`` (409); git turned off or
        missing is a ``ValueError`` (400), and so is a failed step
        (``EnableFailed``, whose ``project_changed`` tells the API to
        broadcast anyway). Enabling an already enabled project only commits
        pending changes.
        """
        project = self.store.load_project(project_id)
        project_dir = self.store.project_path(project_id)
        try:
            self.history.enable(project_dir, project.name, self._describer(project_id))
        except RepoConflict as exc:
            raise ConflictError(str(exc)) from exc
        return self.history.status(project_dir)

    def requirement_history(self, project_id: str, module_id: str, requirement_id: str) -> dict[str, Any]:
        """A requirement's change list with per-attribute diffs, and the attribute blame.

        Read-only. The requirement must exist (404 otherwise). A project
        without a readable repository answers ``available: false`` with the
        reason instead of failing.
        """
        self.store.load_requirement(project_id, module_id, requirement_id)
        return self.history.requirement_history(
            self.store.project_path(project_id),
            self.store.requirement_relative_path(module_id, requirement_id),
            requirement_id,
        )

    def git_log(self, project_id: str, limit: int | None = None) -> dict[str, Any]:
        """The newest commits of the project's ``current`` branch (read-only, for the Git panel).

        ``limit`` defaults to ``DEFAULT_LOG_LIMIT`` (20) and is capped at
        ``LOG_LIMIT`` (200). A project without a readable repository answers
        ``available: false`` with the reason instead of failing.
        """
        self.store.load_project(project_id)
        if limit is None:
            limit = DEFAULT_LOG_LIMIT
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError(f"Invalid limit {limit!r}: expected an integer >= 1")
        return self.history.log(self.store.project_path(project_id), min(limit, LOG_LIMIT))

    # --- remotes (git) -----------------------------------------------------------

    def _require_no_sync_conflict(self, project_id: str) -> None:
        """Refuse a mutation (409) while the project's git sync conflict is unresolved."""
        if not self.history.enabled:
            return
        try:
            project_dir = self.store.project_path(project_id)
        except FileNotFoundError:
            return  # the mutation itself reports the missing project
        message = self.history.sync_conflict_message(project_dir)
        if message is not None:
            raise ConflictError(message)

    def _git_remote_call(self, call: Callable[[], Any]) -> Any:
        """Run a remote git operation, mapping its errors to the service's.

        ``RepoConflict`` becomes ``ConflictError`` (409), carrying over
        ``project_changed`` (a failed pull that already fetched: broadcast
        anyway); ``GitError`` (network, access, git itself) becomes
        ``ValueError`` (400).
        """
        try:
            return call()
        except RepoConflict as exc:
            error = ConflictError(str(exc))
            if getattr(exc, "project_changed", False):
                error.project_changed = True  # type: ignore[attr-defined]
            raise error from exc
        except GitError as exc:
            raise ValueError(str(exc)) from exc

    def _refuse_remote_in_data_dir(self, url: str | None) -> None:
        """Refuse (400) a local-folder remote in the data directory (or the directory itself).

        Pushing there would write another project's refs outside its lock,
        and a project cloned from there would push back into it. Symlinks
        are resolved; ssh/git/https remotes are never checked.
        """
        path = local_remote_path(url or "")
        if path is None or not path.is_absolute():
            return  # a relative path is refused by the URL validator
        data_dir = os.path.realpath(self.store.root)
        target = Path(os.path.realpath(path))
        inside = target == Path(data_dir) or target.is_relative_to(data_dir)
        if not inside:
            # Case-insensitive file systems, bind mounts: compare the folders themselves.
            for folder in (target, *target.parents):
                try:
                    if os.path.samefile(folder, data_dir):
                        inside = True
                        break
                except OSError:
                    continue
        if inside:
            raise ValueError(
                f"Invalid remote URL '{(url or '').strip()}': a local-folder remote must not be "
                f"inside the data directory ({data_dir}); use a repository outside it"
            )

    def set_git_remote(self, project_id: str, url: str | None) -> dict[str, Any]:
        """Set (or remove, with ``None``/empty) the project's remote URL; returns the new git status.

        The URL is validated with ``git ls-remote`` (400 when unreachable).
        Credentials are never stored: they stay with the user's git (ssh
        agent / credential helper).
        """
        self.store.load_project(project_id)
        project_dir = self.store.project_path(project_id)
        self._refuse_remote_in_data_dir(url)
        self._git_remote_call(lambda: self.history.set_remote(project_dir, url))
        return self.history.status(project_dir)

    def set_git_auto_push(self, project_id: str, enabled: bool) -> dict[str, Any]:
        """Turn the per-project auto-push opt-in on or off; returns the new git status."""
        self.store.load_project(project_id)
        project_dir = self.store.project_path(project_id)
        self._git_remote_call(lambda: self.history.set_auto_push(project_dir, bool(enabled)))
        return self.history.status(project_dir)

    def git_push(self, project_id: str) -> dict[str, Any]:
        """Push ``current``, ``baseline`` and the ``baseline/*`` tags to the project's remote.

        A non-fast-forward rejection (another instance pushed first) is 409:
        pull first. 400 without a configured remote or when git fails.
        """
        self.store.load_project(project_id)
        return self._git_remote_call(lambda: self.history.push(self.store.project_path(project_id)))

    def git_pull(self, project_id: str) -> dict[str, Any]:
        """Fetch the project's remote and fast-forward ``current`` (never a merge commit).

        Diverged histories are 409 with ``project_changed`` (the fetch
        already happened, so the status now shows the sync conflict and
        mutations are blocked until the user reconciles the branches in git).
        """
        self.store.load_project(project_id)
        return self._git_remote_call(
            lambda: self.history.pull(self.store.project_path(project_id), self._describer(project_id))
        )

    def add_project_from_remote(self, url: str) -> Project:
        """Clone a remote repository into the data directory as a project ("Add project from remote").

        The clone goes into a staging directory first and is validated like
        any loaded project (its project.json must load); only then is it
        moved into place under the id stored in its project.json, in one
        rename. A taken id is 409 and an invalid clone is 400; either way
        nothing is left behind.
        """
        cleaned = (url or "").strip()
        self._refuse_remote_in_data_dir(cleaned)
        with self.store.staging_store() as staging:
            incoming = staging.root / "incoming"
            self._git_remote_call(lambda: self.history.clone(cleaned, incoming))
            project = staging.load_project("incoming")
            project_id = project.id
            if not isinstance(project_id, str) or not PROJECT_SLUG_PATTERN.fullmatch(project_id):
                raise ValueError(
                    f"The cloned repository's project.json has an unusable project id {project_id!r}"
                )
            # The staged project's folder must carry its final id for the rename.
            incoming.rename(staging.root / project_id)
            try:
                self.store.publish_staged_project(staging, project_id)
            except FileExistsError as exc:
                raise ConflictError(
                    f"A project '{project_id}' already exists in this data directory: {exc}"
                ) from exc
        return self.store.load_project(project_id)

    # --- baselines (git) ---------------------------------------------------------

    def create_baseline(self, project_id: str, name: str, description: str = "") -> dict[str, Any]:
        """Create a named baseline: commit pending changes, tag ``baseline/<name>``, fast-forward ``baseline``.

        See ``GitHistory.create_baseline`` for the rules. A nested/foreign/
        paused/absent repository, a duplicate name or a hand-moved
        ``baseline`` branch is ``ConflictError`` (409); an invalid name or
        git off/missing is ``ValueError`` (400).
        """
        self.store.load_project(project_id)
        self._require_no_sync_conflict(project_id)
        from . import __version__

        try:
            return self.history.create_baseline(
                self.store.project_path(project_id),
                name,
                description or "",
                __version__,
                PROJECT_FORMAT.current,
                self._describer(project_id),
            )
        except RepoConflict as exc:
            raise ConflictError(str(exc)) from exc

    def list_baselines(self, project_id: str) -> dict[str, Any]:
        """The project's baselines (from its ``baseline/*`` tags), newest first. Read-only."""
        self.store.load_project(project_id)
        return self.history.list_baselines(self.store.project_path(project_id))

    def _baseline_files(self, project_id: str, name: str) -> tuple[str, dict[str, bytes | None]]:
        self.store.load_project(project_id)
        return self.history.baseline_files(self.store.project_path(project_id), name)

    def _baseline_project(self, project_id: str, name: str, files: dict[str, bytes | None]) -> Project:
        """The baseline's project.json as a ``Project``, upgraded in memory like ``load_project``.

        A baseline written by a newer ReqManTool raises ``NewerFormatError``
        (REST 409); an unreadable one raises ``ValueError``.
        """
        what = f"Baseline '{name}' of project '{project_id}'"
        raw = files.get("project.json")
        if raw is None:
            raise ValueError(f"{what} has no readable project.json")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"{what} has an invalid project.json: not valid JSON ({exc})") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{what} has an invalid project.json: expected a JSON object")
        try:
            return Project.from_dict(upgrade(data, PROJECT_FORMAT, what))
        except KeyError as exc:
            raise ValueError(f"{what} has an invalid project.json: missing key {exc}") from exc
        except (TypeError, AttributeError) as exc:
            raise ValueError(f"{what} has an invalid project.json: {exc}") from exc

    @staticmethod
    def _baseline_modules(project: Project, files: dict[str, bytes | None]) -> list[Module]:
        """The baseline's modules, in the baseline project's (tolerant) module order."""
        modules: dict[str, Module] = {}
        for path, content in files.items():
            match = re.match(r"^modules/([^/]+)/module\.json$", path)
            if match is None or content is None:
                continue
            try:
                modules[match.group(1)] = Module.from_dict(json.loads(content.decode("utf-8-sig")))
            except (ValueError, TypeError, KeyError, RecursionError):
                continue  # an unreadable module file is skipped, like an unreadable requirement
        order = [m for m in project.module_order if m in modules]
        order += [m for m in sorted(modules) if m not in order]
        seen: set[str] = set()
        return [modules[m] for m in order if not (m in seen or seen.add(m))]

    def get_baseline(self, project_id: str, name: str) -> dict[str, Any]:
        """The project and modules as they were at baseline ``name``. Read-only.

        Read from the tag's commit in memory, never from a checkout; a
        missing baseline is 404 and a baseline written by a newer ReqManTool
        is 409 (``NewerFormatError``).
        """
        commit, files = self._baseline_files(project_id, name)
        project = self._baseline_project(project_id, name, files)
        return {
            "name": name,
            "commit": commit,
            "project": project.to_dict(),
            "modules": [m.to_dict() for m in self._baseline_modules(project, files)],
        }

    def baseline_requirements(self, project_id: str, name: str, module_id: str) -> list[Requirement]:
        """A module's requirements as they were at baseline ``name`` (read-only), sorted by id."""
        commit, files = self._baseline_files(project_id, name)
        project = self._baseline_project(project_id, name, files)  # also rejects a newer format
        del commit, project
        if f"modules/{module_id}/module.json" not in files:
            raise FileNotFoundError(f"Module '{module_id}' not found in baseline '{name}'")
        requirements = []
        prefix = f"modules/{module_id}/requirements/"
        for path, content in files.items():
            if not (path.startswith(prefix) and content is not None):
                continue
            try:
                requirements.append(Requirement.from_dict(json.loads(content.decode("utf-8-sig"))))
            except (ValueError, TypeError, KeyError, RecursionError):
                continue
        return sorted(requirements, key=lambda r: r.id)

    def _working_files(self, project_id: str) -> dict[str, bytes | None]:
        """The project's data files as they are on disk now (including uncommitted changes)."""
        root = self.store.project_path(project_id)
        files: dict[str, bytes | None] = {}

        def add(relative: str) -> None:
            try:
                files[relative] = (root / relative).read_bytes()
            except OSError:
                pass

        add("project.json")
        for module_id in self.store.list_module_ids(project_id):
            add(f"modules/{module_id}/module.json")
            for requirement_id in self.store.list_requirement_ids(project_id, module_id):
                add(self.store.requirement_relative_path(module_id, requirement_id))
        links_dir = root / "links"
        if links_dir.is_dir():
            for path in sorted(links_dir.glob("*.json")):
                if not path.name.startswith("."):
                    add(f"links/{path.name}")
        return files

    def compare_baselines(self, project_id: str, from_name: str, to_name: str) -> dict[str, Any]:
        """Requirement-level differences between two baselines, or a baseline and ``current``.

        ``current`` (either side) means the working files on disk, including
        uncommitted changes. Read-only; a missing baseline is 404, a baseline
        written by a newer ReqManTool is 409.
        """
        self.store.load_project(project_id)  # 404 / stub / newer-format of the live project

        def side(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
            if name == "current":
                files = self._working_files(project_id)
                meta: dict[str, Any] = {"name": "current", "commit": None}
            else:
                commit, files = self._baseline_files(project_id, name)
                meta = {"name": name, "commit": commit}
            snapshot = build_snapshot(files)
            if snapshot["project"] is not None:
                what = (
                    f"Project '{project_id}'" if name == "current" else f"Baseline '{name}' of project '{project_id}'"
                )
                snapshot["project"] = upgrade(snapshot["project"], PROJECT_FORMAT, what)
            return meta, snapshot

        from_meta, from_snapshot = side(from_name)
        to_meta, to_snapshot = side(to_name)
        return {"from": from_meta, "to": to_meta, **compare_snapshots(from_snapshot, to_snapshot)}

    # --- projects ----------------------------------------------------------

    def create_project(
        self,
        name: str,
        description: str = "",
        template_id: str | None = None,
        parent_project_id: str | None = None,
    ) -> Project:
        """Create a project, blank or from a template, optionally as a subproject.

        From a template, the whole project (``project.json``, modules, empty
        link-pair files) is built and validated in memory with the same
        helpers as the individual create operations, written into a staging
        directory, and moved into the data directory in one rename: an
        invalid template or a failed write leaves nothing behind.
        """
        self._check_parent(None, parent_project_id)
        if template_id is None:
            project_id = _unique_slug(
                name, self.store.project_exists, fallback="project", max_length=self.store.max_name_length()
            )
            project = Project(
                id=project_id,
                name=name,
                description=description,
                parent_project_id=parent_project_id,
                attributes=list(BUILTIN_ATTRIBUTES),
            )
            self.store.save_project(project)
            self._start_history(project)
            return project

        template = self._load_template(template_id)
        # Skip any path that is taken, even by a non-project directory: the
        # staged project is moved there by a rename, which must not merge
        # into (or clobber) existing files.
        project_id = _unique_slug(
            name, self.store.project_path_exists, fallback="project", max_length=self.store.max_name_length()
        )
        try:
            project, modules = self._build_from_template(
                template, project_id, name, description, parent_project_id
            )
        except ValueError as exc:
            raise ValueError(f"Template '{template_id}' is invalid: {exc}") from exc

        with self.store.staging_store() as staging:
            for module in modules:
                staging.save_module(project.id, module)
            for config in project.link_configurations:
                staging.save_link_pairs(project.id, config.id, [])
            staging.save_project(project)
            try:
                self.store.publish_staged_project(staging, project.id)
            except FileExistsError as exc:
                raise ValueError(f"{exc}; try again") from exc
        # The repository is created in the final folder, after the rename.
        self._start_history(project)
        return project

    def _start_history(self, project: Project) -> None:
        """Give a new project its git repository and initial commit (skipped when not possible)."""
        if self.history.enabled:
            self.history.init_new_project(self.store.project_path(project.id), project.name)

    def get_project(self, project_id: str) -> Project:
        return self.store.load_project(project_id)

    def list_projects(self) -> list[Project | ProjectStub]:
        """Every project, with ``parent_project_id`` as the effective subproject tree.

        Hand-edited data can hold a parent that does not exist, or a cycle;
        both are shown as top level here (only in the returned objects, the
        files are not rewritten), so the tree always terminates.
        ``get_project`` returns the stored value unchanged.

        A project whose project.json cannot be loaded (written by a newer
        ReqManTool, not valid UTF-8 JSON, wrong shape, invalid
        ``format_version``, unreadable) is listed as a ``ProjectStub``
        carrying the error, at the top level, instead of failing the whole
        list. Its subprojects stay under it. Nothing is written.
        """
        projects: list[Project | ProjectStub] = []
        for project_id in self.store.list_project_ids():
            try:
                projects.append(self.store.load_project(project_id))
            except FileNotFoundError:
                continue  # removed since it was listed
            except (OSError, ValueError) as exc:
                logger.debug("Project %s cannot be loaded: %s", project_id, exc)
                projects.append(ProjectStub(id=project_id, name=self._stub_name(project_id), error=str(exc)))
        broken = self._broken_parent_ids(projects)
        for project in projects:
            if project.id in broken:
                project.parent_project_id = None
        return projects

    def _stub_name(self, project_id: str) -> str:
        """The ``name`` stored in an unloadable project.json, if it has a usable one; else the id."""
        try:
            text: str | None = self.store.read_project_text(project_id)
        except (OSError, ValueError):
            text = None
        return _display_name(text, project_id)

    @staticmethod
    def _broken_parent_ids(projects: list[Project | ProjectStub]) -> set[str]:
        """Ids of projects whose stored parent is dangling, themselves, or part of a cycle."""
        ids = {p.id for p in projects}
        parent_of: dict[str, str | None] = {}
        broken: set[str] = set()
        for project in projects:
            parent = project.parent_project_id
            if parent is None:
                parent_of[project.id] = None
            elif not isinstance(parent, str) or parent not in ids or parent == project.id:
                parent_of[project.id] = None
                broken.add(project.id)
            else:
                parent_of[project.id] = parent
        done: set[str] = set()
        for start in parent_of:
            path: list[str] = []
            on_path: set[str] = set()
            node: str | None = start
            while node is not None and node not in done and node not in on_path:
                path.append(node)
                on_path.add(node)
                node = parent_of[node]
            if node is not None and node in on_path:
                broken.update(path[path.index(node):])
            done.update(path)
        return broken

    def _check_parent(self, project_id: str | None, parent_project_id: Any) -> None:
        """Raise unless ``parent_project_id`` may be the parent of ``project_id``.

        ``None`` (top level) is always allowed. Otherwise the parent must be
        one of the ids ``list_project_ids`` returns (never a path such as
        ``a/../b``), other than the project itself, and (for an existing
        project) must not be one of its descendants. The ancestor walk only
        follows ids of that set and stops at a missing, unreadable or
        already-visited project, so hand-edited dangling, path-like or cyclic
        data cannot make it loop or read outside the data directory. A parent
        (or, when moving, any ancestor) written by a newer ReqManTool raises
        ``NewerFormatError``: its own parent cannot be read, so "create under"
        and "move under" it are both refused and nothing is written.
        """
        if parent_project_id is None:
            return
        if not isinstance(parent_project_id, str) or not parent_project_id:
            raise ValueError("parent_project_id must be a project id or null")
        if parent_project_id == project_id:
            raise ValueError("A project cannot be its own parent")
        project_ids = set(self.store.list_project_ids())
        if parent_project_id not in project_ids:
            raise FileNotFoundError(f"Parent project '{parent_project_id}' not found")
        seen: set[str] = set()
        node: Any = parent_project_id
        while isinstance(node, str) and node in project_ids and node not in seen:
            if node == project_id:
                raise ValueError(
                    f"Cannot make '{parent_project_id}' the parent of '{project_id}': "
                    f"'{parent_project_id}' is a subproject of '{project_id}'"
                )
            seen.add(node)
            try:
                node = self.store.load_project(node).parent_project_id
            except NewerFormatError:
                raise  # its parent is unknown, so a cycle cannot be ruled out: refuse (409)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
                break  # missing or malformed project.json: its parent is unknown
            if project_id is None:
                break  # a new project has no subprojects: only its parent's format matters

    @_records_history
    def set_project_parent(self, project_id: str, parent_project_id: str | None) -> Project:
        """Move a project in the subproject tree; ``None`` makes it top level."""
        project = self.store.load_project(project_id)
        self._check_parent(project_id, parent_project_id)
        if project.parent_project_id != parent_project_id:
            project.parent_project_id = parent_project_id
            self.store.save_project(project)
        return project

    @_records_history
    def add_attribute_definition(
        self,
        project_id: str,
        attr_id: str,
        name: str,
        type: str = "string",
        required: bool = False,
        default: Any = None,
        choices: list[str] | None = None,
        modules: list[str] | None = None,
    ) -> Project:
        """Add an attribute definition, optionally scoped to some modules.

        ``modules`` lists the module ids the attribute applies to; ``None``
        or an empty list means every module (see ``_attribute_applies``).
        """
        project = self.store.load_project(project_id)
        definition = self._new_attribute_definition(project, attr_id, name, type, required, default, choices)
        definition.modules = self._checked_attribute_modules(project, modules)
        project.attributes.append(definition)
        self.store.save_project(project)
        return project

    @_records_history
    def set_attribute_modules(
        self, project_id: str, attr_id: str, modules: Any, drop_values: Any = False
    ) -> dict[str, Any]:
        """Change the modules an attribute applies to (empty: every module).

        Values the attribute has in requirements of modules outside the new
        scope cannot stay: a non-blank value is refused (``ValueError``)
        unless ``drop_values`` is true, in which case it is removed and
        reported in ``dropped_values``. Blank values there are removed
        silently. System (link-mirror) attributes keep their own scope and
        the built-in ObjectText applies to every module.
        """
        if not isinstance(drop_values, bool):
            raise ValueError("drop_values must be true or false")
        project = self.store.load_project(project_id)
        attr = next((a for a in project.attributes if a.id == attr_id), None)
        if attr is None:
            raise FileNotFoundError(f"Attribute '{attr_id}' not found in project '{project_id}'")
        if attr.system:
            raise ValueError(f"Attribute '{attr.name}' is system-managed; its modules cannot be changed")
        if attr.id in {a.id for a in BUILTIN_ATTRIBUTES}:
            raise ValueError(f"Attribute '{attr.name}' is built in and applies to every module")
        scope = self._checked_attribute_modules(project, modules)

        outside: list[tuple[str, Requirement]] = []
        if scope:
            for module_id in self._module_order(project):
                if module_id in scope:
                    continue
                outside.extend(
                    (module_id, r)
                    for r in self.store.load_all_requirements(project_id, module_id)
                    if attr_id in r.attributes
                )
        valued = [(m, r) for m, r in outside if not self._is_blank(r.attributes[attr_id])]
        if valued and not drop_values:
            ids = [r.id for _, r in valued]
            shown = ", ".join(ids[:10]) + (f" and {len(ids) - 10} more" if len(ids) > 10 else "")
            raise ValueError(
                f"Attribute '{attr.name}' has values in {len(ids)} requirement(s) of modules it would "
                f"no longer apply to ({shown}); clear them first or drop them"
            )

        attr.modules = scope
        for module_id, requirement in outside:
            del requirement.attributes[attr_id]
            self.store.save_requirement(project_id, module_id, requirement)
        self.store.save_project(project)
        return {
            "project": project,
            "dropped_values": [{"module_id": m, "requirement_id": r.id} for m, r in valued],
        }

    def _checked_attribute_modules(self, project: Project, modules: Any) -> list[str]:
        """``modules`` as a stored attribute scope: existing module ids in display order.

        ``None`` or an empty list means every module (stored as ``[]``).
        Duplicates collapse; an id naming no module of the project is refused.
        """
        if modules is None:
            return []
        if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
            raise ValueError("Attribute modules must be a list of module ids")
        order = self._module_order(project)
        unknown = sorted(set(modules) - set(order))
        if unknown:
            raise ValueError(f"Unknown module(s) for project '{project.id}': {unknown}")
        wanted = set(modules)
        return [module_id for module_id in order if module_id in wanted]

    @staticmethod
    def _attribute_applies(attr: AttributeDefinition, module_id: str) -> bool:
        """Whether ``attr`` applies to requirements of ``module_id``.

        An attribute without a module list applies everywhere. Ids in the
        list that name no existing module (hand-edited data) are harmless.
        """
        return not attr.modules or module_id in attr.modules

    @classmethod
    def _applicable_values(
        cls, project: Project, module_id: str, attributes: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """``attributes`` without the values of attributes that do not apply to ``module_id``.

        Returns the kept values and the dropped attribute ids (in project
        attribute order). Unknown keys are kept. Used by the exports; moving
        or copying a requirement to another module must use it too.
        """
        excluded = [
            a.id for a in project.attributes if a.id in attributes and not cls._attribute_applies(a, module_id)
        ]
        if not excluded:
            return attributes, []
        return {k: v for k, v in attributes.items() if k not in excluded}, excluded

    def _new_attribute_definition(
        self,
        project: Project,
        attr_id: str,
        name: Any,
        type: Any,
        required: bool,
        default: Any,
        choices: Any,
    ) -> AttributeDefinition:
        """A validated new attribute definition for ``project`` (not yet added or saved).

        The one rule set for attribute definitions: ``add_attribute_definition``
        and templates (create from / save as) both go through it.
        """
        if any(a.id == attr_id for a in project.attributes):
            raise ValueError(f"Attribute '{attr_id}' already defined on project '{project.id}'")
        name = self._checked_attribute_name(project, name)
        if type not in ATTRIBUTE_TYPES:
            raise ValueError(f"Unknown attribute type '{type}'; expected one of {', '.join(ATTRIBUTE_TYPES)}")
        definition = AttributeDefinition(
            id=attr_id, name=name, type=type, required=required, default=default, choices=choices
        )
        self._check_choices_and_default(definition)
        return definition

    @classmethod
    def _check_choices_and_default(cls, attr: AttributeDefinition) -> None:
        """Raise if ``attr``'s choices or default do not fit its type.

        An enum needs a non-empty list of string choices; other types take no
        choices (``None``). The default must be a valid value for the type
        (``None`` means no default), so it can never be merged into a new
        requirement as an invalid value. A required attribute's default must
        not be blank, or no requirement could ever omit the attribute.
        """
        if attr.type == "enum":
            if (
                not isinstance(attr.choices, list)
                or not attr.choices
                or not all(isinstance(c, str) for c in attr.choices)
            ):
                raise ValueError(f"Enum attribute '{attr.name}' needs a non-empty list of choices")
        elif attr.choices is not None:
            raise ValueError(f"Attribute '{attr.name}' of type '{attr.type}' cannot have choices")
        problem = cls._value_problem(attr, attr.default)
        if problem is not None:
            raise ValueError(f"Invalid default value: {problem}")
        if attr.required and attr.default is not None and cls._is_blank(attr.default):
            raise ValueError(f"Attribute '{attr.name}' is required, so its default must not be empty")

    @staticmethod
    def _checked_attribute_name(project: Project, name: Any, kind: str = "Attribute") -> str:
        """Return ``name`` stripped, or raise if it cannot head its own export column.

        CSV/XLSX exports head each attribute column with the attribute's name,
        after the reserved ``Id``/``Module``/``Parent`` columns, and the CSV
        import matches columns by name. A reserved or duplicate name (compared
        case-insensitively) would make a project's own export impossible to
        re-import. Link configuration names are checked too, because each link
        has a mirror attribute with the same name. ``kind`` ("Attribute" or
        "Link") starts the error message. Existing project data is never
        rewritten by this check.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{kind} name must not be empty")
        stripped = name.strip()
        folded = stripped.casefold()
        if folded in {column.casefold() for column in RESERVED_COLUMNS}:
            raise ValueError(
                f"{kind} name '{stripped}' is reserved: {', '.join(RESERVED_COLUMNS)} are "
                "export/import columns"
            )
        for attr in project.attributes:
            if attr.name.strip().casefold() == folded:
                raise ValueError(f"{kind} name '{stripped}' is already used by attribute '{attr.id}'")
        return stripped

    # --- modules ----------------------------------------------------------------

    @_records_history
    def create_module(self, project_id: str, name: str, prefix: str, description: str = "") -> Module:
        """Create a module, appended at the end of the project's module order.

        The module file is written first, then project.json with the new
        order. If the second write fails, the module is still listed: an
        existing module missing from the order is appended in id order.
        """
        project = self.store.load_project(project_id)
        validate_prefix(prefix)
        module_id = _unique_slug(
            name,
            lambda c: self.store.module_exists(project_id, c),
            fallback="module",
            max_length=self.store.max_name_length(),
        )
        module = Module(id=module_id, name=name, prefix=prefix, description=description)
        order = self._module_order(project)
        self.store.save_module(project_id, module)
        project.module_order = [*order, module_id]
        self.store.save_project(project)
        return module

    def get_module(self, project_id: str, module_id: str) -> Module:
        return self.store.load_module(project_id, module_id)

    def _module_order(self, project: Project) -> list[str]:
        """The project's module ids in display order.

        ``project.module_order`` is applied tolerantly, so hand-edited or
        older data always lists every module exactly once: entries naming a
        module that does not exist (or repeating one) are skipped, and
        modules the order does not mention follow in id order. A project
        without a stored order (older data) lists its modules in id order.
        The stored value is not rewritten here.
        """
        existing = self.store.list_module_ids(project.id)
        existing_set = set(existing)
        order: list[str] = []
        for module_id in project.module_order:
            if module_id in existing_set and module_id not in order:
                order.append(module_id)
        listed = set(order)
        order.extend(module_id for module_id in existing if module_id not in listed)
        return order

    def list_modules(self, project_id: str) -> list[Module]:
        """The project's modules in display order (see ``_module_order``)."""
        if not self.store.project_exists(project_id):
            return []
        project = self.store.load_project(project_id)
        return [self.store.load_module(project_id, mid) for mid in self._module_order(project)]

    @_records_history
    def reorder_module(self, project_id: str, module_id: str, direction: str) -> Module:
        """Move a module one place up or down in the project's module order.

        Moving the first module up or the last one down changes nothing and
        writes nothing. Otherwise the whole effective order is stored, which
        also drops entries naming missing modules.
        """
        if direction not in ("up", "down"):
            raise ValueError(f"Invalid direction '{direction}': must be 'up' or 'down'")
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        order = self._module_order(project)
        index = order.index(module_id)
        neighbor_index = index - 1 if direction == "up" else index + 1
        if 0 <= neighbor_index < len(order):
            order[index], order[neighbor_index] = order[neighbor_index], order[index]
            project.module_order = order
            self.store.save_project(project)
        return module

    # --- requirements ----------------------------------------------------------------

    @staticmethod
    def _value_problem(attr: AttributeDefinition, value: Any) -> str | None:
        """Why ``value`` is not valid for ``attr``, or None. ``None`` (unset) is always valid."""
        if value is None:
            return None
        if attr.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                return f"{attr.name}: '{value}' is not an integer"
        elif attr.type == "boolean":
            if not isinstance(value, bool):
                return f"{attr.name}: '{value}' is not a boolean"
        elif attr.type == "enum":
            choices = list(attr.choices or [])
            if not isinstance(value, str) or value not in choices:
                return f"{attr.name}: '{value}' is not one of {choices}"
        elif attr.type in ("string", "text"):
            if not isinstance(value, str):
                return f"{attr.name}: expected a string, got {type(value).__name__}"
        return None

    def _attribute_problems(
        self, project: Project, attributes: dict[str, Any], *, is_create: bool, module_id: str
    ) -> list[str]:
        """Every reason ``attributes`` cannot be written, in a stable order.

        The one validation path for requirement attributes: REST/WebSocket
        add and update (via ``_validate_attributes``) and the CSV import (one
        call per row) all use it. ``attributes`` must hold typed values; the
        CSV import parses text cells before calling this. ``module_id`` is the
        requirement's module: an attribute that does not apply to it cannot be
        written, and is neither required nor defaulted there.
        """
        defined = {a.id: a for a in project.attributes}
        unknown = set(attributes) - set(defined)
        if unknown:
            return [f"Unknown attribute(s) for project '{project.id}': {sorted(unknown)}"]
        system_ids = {a.id for a in project.attributes if a.system} & set(attributes)
        if system_ids:
            return [f"Attribute(s) are system-managed and cannot be set directly: {sorted(system_ids)}"]
        not_applicable = [
            a.name for a in project.attributes if a.id in attributes and not self._attribute_applies(a, module_id)
        ]
        if not_applicable:
            return [f"Attribute(s) do not apply to module '{module_id}': {not_applicable}"]
        problems: list[str] = []
        invalid_ids: set[str] = set()
        for attr_id, value in attributes.items():
            problem = self._value_problem(defined[attr_id], value)
            if problem is not None:
                problems.append(problem)
                invalid_ids.add(attr_id)
        # A required attribute must end up with a non-blank value. On create,
        # an omitted attribute gets its (valid) default; an explicit blank
        # value overrides the default and so is rejected. On update, only the
        # attributes being written are checked.
        effective = self._merged_attributes(project, attributes, module_id) if is_create else attributes
        for attr in project.attributes:
            if not attr.required or attr.system or attr.id in invalid_ids:
                continue
            if not self._attribute_applies(attr, module_id):
                continue
            if (is_create or attr.id in attributes) and self._is_blank(effective.get(attr.id)):
                problems.append(f"required attribute '{attr.name}' is empty")
        return problems

    @staticmethod
    def _is_blank(value: Any) -> bool:
        """``None``, ``""`` and whitespace-only strings count as no value."""
        return value is None or (isinstance(value, str) and not value.strip())

    def _validate_attributes(
        self, project: Project, attributes: dict[str, Any], *, is_create: bool, module_id: str
    ) -> None:
        if not isinstance(attributes, dict):
            raise ValueError("Attributes must be an object")
        problems = self._attribute_problems(project, attributes, is_create=is_create, module_id=module_id)
        if problems:
            raise ValueError("; ".join(problems))

    @classmethod
    def _merged_attributes(cls, project: Project, attributes: dict[str, Any], module_id: str) -> dict[str, Any]:
        """Attribute defaults overlaid with ``attributes``.

        System attributes, and attributes that do not apply to ``module_id``,
        never get a default. A stored default that is not
        valid for its attribute (legacy data written before defaults were
        checked) is skipped rather than rewritten, so it cannot block or
        corrupt new requirements.
        """
        merged = {
            a.id: a.default
            for a in project.attributes
            if a.default is not None
            and not a.system
            and cls._attribute_applies(a, module_id)
            and cls._value_problem(a, a.default) is None
        }
        merged.update(attributes)
        return merged

    def _write_new_requirements(
        self, project: Project, module_id: str, requirements: list[Requirement]
    ) -> None:
        """Write new requirement files, then the project (with its advanced id counters).

        If any write fails, the requirement files written so far are deleted
        (best effort) and the error is re-raised, so the module and the
        counters stay as they were. Should the process die between the two
        steps, ``_allocate_requirement_id`` skips ids already on disk, so an
        id is never reused.
        """
        written: list[str] = []
        try:
            for requirement in requirements:
                self.store.save_requirement(project.id, module_id, requirement)
                written.append(requirement.id)
            self.store.save_project(project)
        except BaseException:
            for requirement_id in written:
                try:
                    self.store.delete_requirement(project.id, module_id, requirement_id)
                except OSError:
                    pass
            raise

    @staticmethod
    def _next_sibling_order(requirements: list[Requirement], parent_id: str | None) -> int:
        """Order for a requirement appended after the last sibling under ``parent_id``.

        Orders are 0-based. Removing a requirement leaves a gap, so the count of
        siblings could collide with an existing order; use the highest one + 1.
        """
        return max((r.order for r in requirements if r.parent_id == parent_id), default=-1) + 1

    def _allocate_requirement_id(self, project: Project, module: Module) -> str:
        counter = project.id_counters.get(module.prefix, 0)
        while True:
            counter += 1
            candidate = f"{module.prefix}-{counter:04d}"
            if self.store.find_requirement_module(project.id, candidate) is None:
                project.id_counters[module.prefix] = counter
                return candidate

    @_records_history
    def add_requirement(
        self,
        project_id: str,
        module_id: str,
        attributes: dict[str, Any],
        parent_id: str | None = None,
    ) -> Requirement:
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)

        if parent_id is not None and not self.store.requirement_exists(project_id, module_id, parent_id):
            raise FileNotFoundError(f"Requirement '{parent_id}' not found in module '{module_id}'")

        self._validate_attributes(project, attributes, is_create=True, module_id=module_id)
        merged = self._merged_attributes(project, attributes, module_id)

        requirement_id = self._allocate_requirement_id(project, module)
        siblings = self.store.load_all_requirements(project_id, module_id)
        order = self._next_sibling_order(siblings, parent_id)

        requirement = Requirement(id=requirement_id, attributes=merged, parent_id=parent_id, order=order)
        self._write_new_requirements(project, module_id, [requirement])
        return requirement

    def _descendant_ids(self, requirements: list[Requirement], requirement_id: str) -> set[str]:
        children_of: dict[str | None, list[str]] = {}
        for req in requirements:
            children_of.setdefault(req.parent_id, []).append(req.id)
        descendants: set[str] = set()
        stack = list(children_of.get(requirement_id, []))
        while stack:
            child_id = stack.pop()
            if child_id in descendants:
                continue
            descendants.add(child_id)
            stack.extend(children_of.get(child_id, []))
        return descendants

    @_records_history
    def move_requirement(
        self,
        project_id: str,
        module_id: str,
        requirement_id: str,
        parent_id: str | None,
        index: Any = None,
    ) -> Requirement:
        """Make ``requirement_id`` a child of ``parent_id`` (``None``: top level).

        Without ``index`` the requirement is appended after the new parent's
        last child and no other requirement is touched. With ``index`` (a
        non-negative int) it is placed at that 0-based position among the new
        parent's children after the move; an index past the end appends. The
        new parent's children are then renumbered ``0..n-1`` in their current
        order (ties by id), which also repairs duplicate or gapped orders. Moving
        within the same parent with an index repositions the requirement.
        """
        if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index < 0):
            raise ValueError(f"Invalid index {index!r}: must be a non-negative integer")

        requirements = self.store.load_all_requirements(project_id, module_id)
        if not any(r.id == requirement_id for r in requirements):
            raise FileNotFoundError(f"Requirement '{requirement_id}' not found in module '{module_id}'")

        if parent_id is not None:
            if parent_id == requirement_id:
                raise ValueError("A requirement cannot be its own parent")
            if not any(r.id == parent_id for r in requirements):
                raise FileNotFoundError(f"Requirement '{parent_id}' not found in module '{module_id}'")
            if parent_id in self._descendant_ids(requirements, requirement_id):
                raise ValueError("Cannot move a requirement under one of its own descendants")

        requirement = self.store.load_requirement(project_id, module_id, requirement_id)
        requirement.parent_id = parent_id
        if index is None:
            requirement.order = self._next_sibling_order(
                [r for r in requirements if r.id != requirement_id], parent_id
            )
            self.store.save_requirement(project_id, module_id, requirement)
            return requirement

        siblings = sorted(
            (r for r in requirements if r.parent_id == parent_id and r.id != requirement_id),
            key=lambda r: (r.order, r.id),
        )
        siblings.insert(min(index, len(siblings)), requirement)
        for position, sibling in enumerate(siblings):
            if sibling is requirement or sibling.order != position:
                sibling.order = position
                self.store.save_requirement(project_id, module_id, sibling)
        return requirement

    @_records_history
    def reorder_requirement(
        self, project_id: str, module_id: str, requirement_id: str, direction: str
    ) -> Requirement:
        if direction not in ("up", "down"):
            raise ValueError(f"Invalid direction '{direction}': must be 'up' or 'down'")

        requirements = self.store.load_all_requirements(project_id, module_id)
        target = next((r for r in requirements if r.id == requirement_id), None)
        if target is None:
            raise FileNotFoundError(f"Requirement '{requirement_id}' not found in module '{module_id}'")

        siblings = sorted(
            (r for r in requirements if r.parent_id == target.parent_id), key=lambda r: r.order
        )
        index = next(i for i, r in enumerate(siblings) if r.id == requirement_id)
        neighbor_index = index - 1 if direction == "up" else index + 1

        if 0 <= neighbor_index < len(siblings):
            neighbor = siblings[neighbor_index]
            target.order, neighbor.order = neighbor.order, target.order
            self.store.save_requirement(project_id, module_id, target)
            self.store.save_requirement(project_id, module_id, neighbor)

        return self.store.load_requirement(project_id, module_id, requirement_id)

    @_records_history
    def copy_requirement(
        self, project_id: str, module_id: str, requirement_id: str, target_parent_id: str | None
    ) -> Requirement:
        requirements = self.store.load_all_requirements(project_id, module_id)
        if not any(r.id == requirement_id for r in requirements):
            raise FileNotFoundError(f"Requirement '{requirement_id}' not found in module '{module_id}'")

        if target_parent_id is not None:
            if not any(r.id == target_parent_id for r in requirements):
                raise FileNotFoundError(f"Requirement '{target_parent_id}' not found in module '{module_id}'")
            if target_parent_id == requirement_id or target_parent_id in self._descendant_ids(
                requirements, requirement_id
            ):
                raise ValueError("Cannot copy a requirement under itself or one of its own descendants")

        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        system_ids = {a.id for a in project.attributes if a.system}
        children_of: dict[str | None, list[Requirement]] = {}
        for r in requirements:
            children_of.setdefault(r.parent_id, []).append(r)

        created: list[Requirement] = []

        def duplicate(original: Requirement, new_parent_id: str | None) -> Requirement:
            attributes = {k: v for k, v in original.attributes.items() if k not in system_ids}
            new_id = self._allocate_requirement_id(project, module)
            order = self._next_sibling_order(requirements, new_parent_id)
            copy = Requirement(id=new_id, attributes=attributes, parent_id=new_parent_id, order=order)
            created.append(copy)
            requirements.append(copy)
            for child in sorted(children_of.get(original.id, []), key=lambda r: r.order):
                duplicate(child, new_id)
            return copy

        root_original = next(r for r in requirements if r.id == requirement_id)
        new_root = duplicate(root_original, target_parent_id)
        self._write_new_requirements(project, module_id, created)

        return self.store.load_requirement(project_id, module_id, new_root.id)

    @_records_history
    def update_requirement(
        self, project_id: str, module_id: str, requirement_id: str, attributes: dict[str, Any]
    ) -> Requirement:
        project = self.store.load_project(project_id)
        self._validate_attributes(project, attributes, is_create=False, module_id=module_id)
        requirement = self.store.load_requirement(project_id, module_id, requirement_id)
        requirement.attributes.update(attributes)
        self.store.save_requirement(project_id, module_id, requirement)
        return requirement

    def get_requirement(self, project_id: str, module_id: str, requirement_id: str) -> Requirement:
        return self.store.load_requirement(project_id, module_id, requirement_id)

    def list_requirements(self, project_id: str, module_id: str) -> list[Requirement]:
        return self.store.load_all_requirements(project_id, module_id)

    def find_requirement(self, project_id: str, requirement_id: str) -> tuple[str, Requirement]:
        """Locate a requirement anywhere in a project, without knowing its module."""
        module_id = self.store.find_requirement_module(project_id, requirement_id)
        if module_id is None:
            raise FileNotFoundError(f"Requirement '{requirement_id}' not found in project '{project_id}'")
        return module_id, self.store.load_requirement(project_id, module_id, requirement_id)

    def _cleanup_links_for_requirement(self, project_id: str, requirement_id: str) -> None:
        project = self.store.load_project(project_id)
        for config in project.link_configurations:
            pairs = self.store.load_link_pairs(project_id, config.id)
            remaining = []
            affected: list[str] = []
            for pair in pairs:
                if pair.source_id == requirement_id or pair.target_id == requirement_id:
                    other_id = pair.target_id if pair.source_id == requirement_id else pair.source_id
                    affected.append(other_id)
                    continue
                remaining.append(pair)
            if len(remaining) != len(pairs):
                self.store.save_link_pairs(project_id, config.id, remaining)
                for other_id in affected:
                    other_module = self.store.find_requirement_module(project_id, other_id)
                    if other_module is not None:
                        self._refresh_link_attribute(project_id, config.id, other_module, other_id)

    @_records_history
    def remove_requirement(self, project_id: str, module_id: str, requirement_id: str) -> None:
        removed = self.store.load_requirement(project_id, module_id, requirement_id)
        for other in self.store.load_all_requirements(project_id, module_id):
            if other.parent_id == requirement_id:
                other.parent_id = removed.parent_id
                self.store.save_requirement(project_id, module_id, other)

        self._cleanup_links_for_requirement(project_id, requirement_id)
        self.store.delete_requirement(project_id, module_id, requirement_id)

    @_records_history
    def purge_module(self, project_id: str, module_id: str) -> int:
        """Delete every requirement of a module (and its links); the number deleted.

        A missing project is not found and a stub (unloadable project) is
        refused, even when there is nothing to delete. A module that does not
        exist in a loadable project has nothing to purge: 0.
        """
        self.store.load_project(project_id)
        return self._purge_module(project_id, module_id)

    def _purge_module(self, project_id: str, module_id: str) -> int:
        ids = set(self.store.list_requirement_ids(project_id, module_id))
        for requirement_id in ids:
            self._cleanup_links_for_requirement(project_id, requirement_id)
            self.store.delete_requirement(project_id, module_id, requirement_id)
        return len(ids)

    @_records_history
    def purge_project(self, project_id: str) -> int:
        # A missing project is not found and a stub (unloadable project) is
        # refused, even when there is nothing to delete.
        self.store.load_project(project_id)
        total = 0
        for module_id in self.store.list_module_ids(project_id):
            total += self._purge_module(project_id, module_id)
        return total

    # --- link configurations ("link modules") ----------------------------------

    @_records_history
    def create_link_configuration(
        self, project_id: str, name: str, source_module_id: str, target_module_id: str
    ) -> LinkConfiguration:
        project = self.store.load_project(project_id)
        if not self.store.module_exists(project_id, source_module_id):
            raise FileNotFoundError(f"Module '{source_module_id}' not found in project '{project_id}'")
        if not self.store.module_exists(project_id, target_module_id):
            raise FileNotFoundError(f"Module '{target_module_id}' not found in project '{project_id}'")
        config = self._add_link_configuration(project, name, source_module_id, target_module_id)
        self.store.save_project(project)
        self.store.save_link_pairs(project_id, config.id, [])
        return config

    def _add_link_configuration(
        self, project: Project, name: Any, source_module_id: str, target_module_id: str
    ) -> LinkConfiguration:
        """Add a link configuration and its system mirror attribute to ``project`` in memory.

        The caller has checked that both modules exist and saves the project.
        """
        # The link's mirror attribute heads its own export column, so the link
        # name follows the attribute name rule.
        name = self._checked_attribute_name(project, name, kind="Link")

        taken_ids = {c.id for c in project.link_configurations} | {a.id for a in project.attributes}
        config_id = _unique_slug(name, lambda c: c in taken_ids, fallback="link")

        config = LinkConfiguration(
            id=config_id,
            name=name,
            source_module_id=source_module_id,
            target_module_id=target_module_id,
        )
        project.link_configurations.append(config)
        project.attributes.append(
            AttributeDefinition(id=config_id, name=name, type="text", required=False, system=True)
        )
        return config

    def list_link_configurations(self, project_id: str) -> list[LinkConfiguration]:
        return self.store.load_project(project_id).link_configurations

    @_records_history
    def remove_link_configuration(self, project_id: str, config_id: str) -> None:
        project = self.store.load_project(project_id)
        if not any(c.id == config_id for c in project.link_configurations):
            raise FileNotFoundError(f"Link configuration '{config_id}' not found in project '{project_id}'")
        project.link_configurations = [c for c in project.link_configurations if c.id != config_id]
        project.attributes = [a for a in project.attributes if a.id != config_id]
        self.store.save_project(project)
        self.store.delete_link_pairs(project_id, config_id)

        for module_id in self.store.list_module_ids(project_id):
            for requirement in self.store.load_all_requirements(project_id, module_id):
                if config_id in requirement.attributes:
                    del requirement.attributes[config_id]
                    self.store.save_requirement(project_id, module_id, requirement)

    # --- views ----------------------------------------------------------------

    @_records_history
    def create_view(
        self,
        project_id: str,
        name: str,
        attribute_ids: list[str],
        column_widths: Any = None,
    ) -> View:
        project = self.store.load_project(project_id)
        view = self._add_view(project, name, attribute_ids)
        view.column_widths = self._checked_column_widths(column_widths, view)
        self.store.save_project(project)
        return view

    @_records_history
    def set_view_column_widths(self, project_id: str, view_id: str, column_widths: Any) -> View:
        """Store (or, with None or an empty mapping, clear) a view's shared column widths."""
        project = self.store.load_project(project_id)
        view = next((v for v in project.views if v.id == view_id), None)
        if view is None:
            raise FileNotFoundError(f"View '{view_id}' not found in project '{project_id}'")
        view.column_widths = self._checked_column_widths(column_widths, view)
        self.store.save_project(project)
        return view

    @staticmethod
    def _checked_column_widths(column_widths: Any, view: View) -> dict[str, int] | None:
        """Validated column widths for ``view``, or None when there are none.

        A mapping of column key to an integer width in pixels within
        [MIN_COLUMN_WIDTH, MAX_COLUMN_WIDTH]. Keys are the view's own
        columns: ``VIEW_ID_COLUMN`` or one of its ``attribute_ids``. None or
        an empty mapping means "no widths" (the key is then not written).
        Widths are per-browser display preferences everywhere else; only a
        view can share them through project.json.
        """
        if column_widths is None:
            return None
        if not isinstance(column_widths, dict):
            raise ValueError("column_widths must be an object mapping column keys to widths")
        allowed = {VIEW_ID_COLUMN, *view.attribute_ids}
        widths: dict[str, int] = {}
        for key, width in column_widths.items():
            if key not in allowed:
                raise ValueError(
                    f"Unknown column '{key}' for view '{view.name}'; expected '{VIEW_ID_COLUMN}' "
                    "or one of the view's attribute ids"
                )
            if (
                not isinstance(width, int)
                or isinstance(width, bool)
                or not MIN_COLUMN_WIDTH <= width <= MAX_COLUMN_WIDTH
            ):
                raise ValueError(
                    f"Width of column '{key}' must be an integer from {MIN_COLUMN_WIDTH} "
                    f"to {MAX_COLUMN_WIDTH}"
                )
            widths[key] = width
        # Stored in the view's column order, whatever order the client sent,
        # so the same widths always serialize identically.
        ordered = {key: widths[key] for key in (VIEW_ID_COLUMN, *view.attribute_ids) if key in widths}
        return ordered or None

    @staticmethod
    def _checked_view_name(project: Project, name: Any, view_id: str | None = None) -> str:
        """Return ``name`` stripped, or raise if it is blank or another view's name.

        Names are compared case-insensitively after stripping, like view
        names in templates, so a project can always be saved as a template.
        ``view_id`` is the view being renamed (its own name is not a clash);
        no caller passes it yet: it is reserved for a future view rename.
        Existing duplicates in older data are left as they are.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("View name must not be empty")
        stripped = name.strip()
        folded = stripped.casefold()
        for view in project.views:
            if view.id != view_id and isinstance(view.name, str) and view.name.strip().casefold() == folded:
                raise ValueError(f"View name '{stripped}' is already used by view '{view.id}'")
        return stripped

    @classmethod
    def _add_view(cls, project: Project, name: str, attribute_ids: list[str]) -> View:
        """Add a view to ``project`` in memory; the name must be new, every attribute id defined."""
        name = cls._checked_view_name(project, name)
        defined_ids = {a.id for a in project.attributes}
        unknown = set(attribute_ids) - defined_ids
        if unknown:
            raise ValueError(f"Unknown attribute(s) for project '{project.id}': {sorted(unknown)}")

        taken_ids = {v.id for v in project.views}
        view_id = _unique_slug(name, lambda c: c in taken_ids, fallback="view")

        view = View(id=view_id, name=name, attribute_ids=list(attribute_ids))
        project.views.append(view)
        return view

    def list_views(self, project_id: str) -> list[View]:
        return self.store.load_project(project_id).views

    @_records_history
    def remove_view(self, project_id: str, view_id: str) -> None:
        project = self.store.load_project(project_id)
        if not any(v.id == view_id for v in project.views):
            raise FileNotFoundError(f"View '{view_id}' not found in project '{project_id}'")
        project.views = [v for v in project.views if v.id != view_id]
        self.store.save_project(project)

    # --- links ----------------------------------------------------------------

    def _get_link_configuration(self, project: Project, config_id: str) -> LinkConfiguration:
        for config in project.link_configurations:
            if config.id == config_id:
                return config
        raise FileNotFoundError(f"Link configuration '{config_id}' not found in project '{project.id}'")

    def _refresh_link_attribute(
        self, project_id: str, config_id: str, module_id: str, requirement_id: str
    ) -> None:
        pairs = self.store.load_link_pairs(project_id, config_id)
        outgoing = [p.target_id for p in pairs if p.source_id == requirement_id]
        incoming = [p.source_id for p in pairs if p.target_id == requirement_id]
        entries = [f"> {t}" for t in outgoing] + [f"< {s}" for s in incoming]

        requirement = self.store.load_requirement(project_id, module_id, requirement_id)
        if entries:
            requirement.attributes[config_id] = "; ".join(entries)
        else:
            requirement.attributes.pop(config_id, None)
        self.store.save_requirement(project_id, module_id, requirement)

    @_records_history
    def link_requirements(
        self, project_id: str, config_id: str, source_id: str, target_id: str
    ) -> dict[str, Requirement]:
        project = self.store.load_project(project_id)
        config = self._get_link_configuration(project, config_id)

        source_module = self.store.find_requirement_module(project_id, source_id)
        if source_module is None:
            raise FileNotFoundError(f"Requirement '{source_id}' not found in project '{project_id}'")
        target_module = self.store.find_requirement_module(project_id, target_id)
        if target_module is None:
            raise FileNotFoundError(f"Requirement '{target_id}' not found in project '{project_id}'")

        if source_module != config.source_module_id or target_module != config.target_module_id:
            raise ValueError(
                f"Link configuration '{config.name}' requires source in module "
                f"'{config.source_module_id}' and target in module '{config.target_module_id}'"
            )

        pairs = self.store.load_link_pairs(project_id, config_id)
        if any(p.source_id == source_id and p.target_id == target_id for p in pairs):
            raise ValueError(f"Link '{config.name}' from '{source_id}' to '{target_id}' already exists")
        pairs.append(LinkPair(source_id=source_id, target_id=target_id))
        self.store.save_link_pairs(project_id, config_id, pairs)

        self._refresh_link_attribute(project_id, config_id, source_module, source_id)
        self._refresh_link_attribute(project_id, config_id, target_module, target_id)

        return {
            "source": self.store.load_requirement(project_id, source_module, source_id),
            "target": self.store.load_requirement(project_id, target_module, target_id),
        }

    @_records_history
    def unlink_requirements(
        self, project_id: str, config_id: str, source_id: str, target_id: str
    ) -> dict[str, Requirement]:
        project = self.store.load_project(project_id)
        self._get_link_configuration(project, config_id)

        source_module = self.store.find_requirement_module(project_id, source_id)
        target_module = self.store.find_requirement_module(project_id, target_id)
        if source_module is None:
            raise FileNotFoundError(f"Requirement '{source_id}' not found in project '{project_id}'")
        if target_module is None:
            raise FileNotFoundError(f"Requirement '{target_id}' not found in project '{project_id}'")

        pairs = self.store.load_link_pairs(project_id, config_id)
        pairs = [p for p in pairs if not (p.source_id == source_id and p.target_id == target_id)]
        self.store.save_link_pairs(project_id, config_id, pairs)

        self._refresh_link_attribute(project_id, config_id, source_module, source_id)
        self._refresh_link_attribute(project_id, config_id, target_module, target_id)

        return {
            "source": self.store.load_requirement(project_id, source_module, source_id),
            "target": self.store.load_requirement(project_id, target_module, target_id),
        }

    # --- templates ------------------------------------------------------------

    def _read_template_text(self, source: str, local_id: str) -> str:
        if source == BUILTIN_SOURCE:
            return read_builtin_template_text(local_id, self.builtin_templates_dir)
        return self.store.read_user_template_text(local_id)

    def _load_template(self, template_id: Any) -> Template:
        """Read and shape-check a template (404 if missing, 400 if malformed)."""
        source, local_id = split_template_id(template_id)
        text = self._read_template_text(source, local_id)
        try:
            return parse_template_text(text, template_id)
        except ValueError as exc:
            raise ValueError(f"Template '{template_id}' is invalid: {exc}") from exc

    def _build_from_template(
        self,
        template: Template,
        project_id: str,
        name: str,
        description: str,
        parent_project_id: str | None,
    ) -> tuple[Project, list[Module]]:
        """Build a project and its modules from ``template`` in memory, validating everything.

        Uses the same helpers as ``add_attribute_definition``,
        ``create_module``, ``create_link_configuration`` and ``create_view``,
        so a template can never produce a project those operations would
        reject. Raises ``ValueError`` naming the offending template entry.
        Nothing is written.
        """
        project = Project(
            id=project_id,
            name=name,
            description=description,
            parent_project_id=parent_project_id,
            attributes=list(BUILTIN_ATTRIBUTES),
        )
        builtin_ids = {a.id for a in BUILTIN_ATTRIBUTES}
        for index, attr in enumerate(template.attributes):
            if attr.id in builtin_ids:
                # Checked here, not by _new_attribute_definition, whose message
                # names the project (an internal id while listing templates).
                raise ValueError(
                    f"attributes[{index}]: '{attr.id}' is a built-in attribute every project already has"
                )
            try:
                project.attributes.append(
                    self._new_attribute_definition(
                        project, attr.id, attr.name, attr.type, attr.required, attr.default, attr.choices
                    )
                )
            except ValueError as exc:
                raise ValueError(f"attributes[{index}] ('{attr.id}'): {exc}") from exc

        modules: list[Module] = []
        module_ids: dict[str, str] = {}
        for index, tmodule in enumerate(template.modules):
            try:
                validate_prefix(tmodule.prefix)
            except ValueError as exc:
                raise ValueError(f"modules[{index}] ('{tmodule.name}'): {exc}") from exc
            taken = set(module_ids.values())
            module_id = _unique_slug(tmodule.name, lambda c: c in taken, fallback="module")
            module_ids[tmodule.key] = module_id
            modules.append(
                Module(id=module_id, name=tmodule.name, prefix=tmodule.prefix, description=tmodule.description)
            )
        # The template's declared module order, not the modules' id order.
        project.module_order = [m.id for m in modules]
        # Attribute scopes name module keys (checked by ``parse_template``);
        # stored as module ids in module order.
        for tattr in template.attributes:
            if tattr.modules:
                definition = next(a for a in project.attributes if a.id == tattr.id)
                wanted = {module_ids[key] for key in tattr.modules}
                definition.modules = [m.id for m in modules if m.id in wanted]

        link_ids: dict[str, str] = {}
        for index, tconfig in enumerate(template.link_configurations):
            try:
                config = self._add_link_configuration(
                    project,
                    tconfig.name,
                    module_ids[tconfig.source_module],
                    module_ids[tconfig.target_module],
                )
            except ValueError as exc:
                raise ValueError(f"link_configurations[{index}] ('{tconfig.key}'): {exc}") from exc
            link_ids[tconfig.key] = config.id

        attribute_ids = {a.id for a in BUILTIN_ATTRIBUTES} | {a.id for a in template.attributes}
        for index, tview in enumerate(template.views):
            mapped: list[str] = []
            for ref in tview.attribute_ids:
                if ref.startswith(LINK_REF_PREFIX):
                    key = ref[len(LINK_REF_PREFIX):]
                    if key not in link_ids:
                        raise ValueError(
                            f"views[{index}] ('{tview.name}'): '{ref}' does not name a link "
                            "configuration key of this template"
                        )
                    mapped.append(link_ids[key])
                elif ref in attribute_ids:
                    mapped.append(ref)
                else:
                    raise ValueError(
                        f"views[{index}] ('{tview.name}'): unknown attribute '{ref}' (expected "
                        f"'object_text', a template attribute id, or '{LINK_REF_PREFIX}<key>')"
                    )
            self._add_view(project, tview.name, mapped)

        return project, modules

    def _validate_template(self, template: Template) -> None:
        self._build_from_template(template, _TEMPLATE_CHECK_PROJECT_ID, template.name, "", None)

    @staticmethod
    def _template_summary(template: Template) -> dict[str, Any]:
        return {
            "id": template.id,
            "source": template.source,
            "name": template.name,
            "description": template.description,
            "modules": [{"key": m.key, "name": m.name, "prefix": m.prefix} for m in template.modules],
            "error": None,
        }

    @staticmethod
    def _fallback_template_name(text: str | None, local_id: str) -> str:
        return _display_name(text, local_id)

    def list_templates(self) -> list[dict[str, Any]]:
        """Built-in templates, then user templates, each sorted by name.

        Every template is fully validated. An invalid one (unreadable, bad
        JSON, wrong shape, unsupported ``format_version``, or a rule a
        project would reject) is still listed, with the reason in ``error``
        and no modules, so one bad file never hides the others; creating a
        project from it fails with the same reason.
        """
        sources = (
            (BUILTIN_SOURCE, list_builtin_template_ids(self.builtin_templates_dir)),
            (USER_SOURCE, self.store.list_user_template_ids()),
        )
        result: list[dict[str, Any]] = []
        for source, local_ids in sources:
            entries: list[dict[str, Any]] = []
            for local_id in local_ids:
                template_id = make_template_id(source, local_id)
                text: str | None = None
                try:
                    text = self._read_template_text(source, local_id)
                    template = parse_template_text(text, template_id)
                    self._validate_template(template)
                    entries.append(self._template_summary(template))
                except (OSError, ValueError) as exc:
                    logger.debug("Template %s is invalid: %s", template_id, exc)
                    entries.append(
                        {
                            "id": template_id,
                            "source": source,
                            "name": self._fallback_template_name(text, local_id),
                            "description": "",
                            "modules": [],
                            "error": str(exc),
                        }
                    )
            entries.sort(key=lambda e: (e["name"].casefold(), e["id"]))
            result.extend(entries)
        return result

    @staticmethod
    def _template_from_project(
        project: Project, modules: list[Module], template_id: str, name: str, description: str
    ) -> Template:
        """A project's structure as a template; no requirements, no id counters, no parent."""
        builtin_ids = {a.id for a in BUILTIN_ATTRIBUTES}
        link_ids = {c.id for c in project.link_configurations}
        attributes = [
            TemplateAttribute(
                id=a.id,
                name=a.name,
                type=a.type,
                required=a.required,
                default=a.default,
                choices=list(a.choices) if isinstance(a.choices, list) else a.choices,
                modules=[m.id for m in modules if m.id in a.modules],
            )
            for a in project.attributes
            if not a.system and a.id not in builtin_ids
        ]
        kept_ids = builtin_ids | {a.id for a in attributes}
        views = []
        for view in project.views:
            refs: list[str] = []
            for attr_id in view.attribute_ids:
                if attr_id in link_ids:
                    refs.append(f"{LINK_REF_PREFIX}{attr_id}")
                elif attr_id in kept_ids:
                    refs.append(attr_id)
                # else: a reference the project itself no longer resolves
                # (e.g. its link configuration was removed) is dropped.
            views.append(TemplateView(name=view.name, attribute_ids=refs))
        return Template(
            id=template_id,
            source=USER_SOURCE,
            name=name,
            description=description,
            attributes=attributes,
            modules=[
                TemplateModule(key=m.id, name=m.name, prefix=m.prefix, description=m.description)
                for m in modules
            ],
            link_configurations=[
                TemplateLinkConfiguration(
                    key=c.id, name=c.name, source_module=c.source_module_id, target_module=c.target_module_id
                )
                for c in project.link_configurations
            ],
            views=views,
        )

    def save_project_as_template(self, project_id: str, name: Any, description: Any = "") -> dict[str, Any]:
        """Write a project's structure as a new user template (``user:<slug>``).

        Saves non-system attributes (except the built-in ``object_text``),
        modules (key = module id), link configurations (between module keys)
        and views (mirror attributes written as ``link:<key>``; references to
        attributes the project no longer has are dropped). Requirements, id
        counters and the parent are not saved. The result is validated
        exactly like a loaded template before anything is written. A name
        already used by any template (built-in or user, case-insensitive)
        raises ``ConflictError``; existing files are never overwritten.
        """
        project = self.store.load_project(project_id)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Template name must not be empty")
        name = name.strip()
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ValueError("Template description must be a string")

        for entry in self.list_templates():
            if entry["name"].casefold() == name.casefold():
                raise ConflictError(f"A template named '{entry['name']}' already exists ({entry['id']})")

        local_id = _unique_slug(name, self.store.user_template_path_exists, fallback="template")
        template_id = make_template_id(USER_SOURCE, local_id)
        template = self._template_from_project(
            project, self.list_modules(project_id), template_id, name, description
        )
        data = template.to_dict()
        try:
            # Round-trip through the loader so the saved file is exactly what
            # a later "create from template" will accept.
            self._validate_template(parse_template(data, template_id))
        except ValueError as exc:
            raise ValueError(f"Project '{project_id}' cannot be saved as a template: {exc}") from exc
        self.store.save_user_template(local_id, data)
        return self._template_summary(template)

    def import_template(self, text: Any) -> dict[str, Any]:
        """Save an uploaded template JSON document as a new user template.

        ``text`` is the file's content (a leading UTF-8 byte order mark is
        ignored). It is checked exactly like a template loaded from disk —
        shape and ``format_version`` (``parse_template_text``), then every
        project rule (``_validate_template``) — so an imported template can
        always be used to create a project. Invalid input raises
        ``ValueError`` and writes nothing.

        Like "Save as template", an import never overwrites anything: a
        template whose name is already used by any template (built-in or
        user, case-insensitive) raises ``ConflictError``. The file name stem
        (the template's identity, ``user:<slug>``) is slugged from the name
        and suffixed on collision; the file's own ``id`` key is
        informational and replaced. The file is written in the canonical
        template shape (stable key order; unknown keys are not kept).
        """
        if not isinstance(text, str):
            raise ValueError("The template file content must be a string")
        # The limit is in UTF-8 bytes, as over REST (a BOM counts, as it does
        # in the request body). "surrogatepass" cannot fail here; lone
        # surrogates are rejected by ``parse_template_text``.
        if len(text.encode("utf-8", "surrogatepass")) > MAX_TEMPLATE_IMPORT_BYTES:
            raise ValueError(
                f"The template file is too large: the limit is {MAX_TEMPLATE_IMPORT_BYTES // (1024 * 1024)} MiB"
            )
        # A provisional id: the real one depends on the (validated) name.
        provisional_id = make_template_id(USER_SOURCE, "import")
        try:
            template = parse_template_text(text, provisional_id)
            self._validate_template(template)
        except ValueError as exc:
            raise ValueError(f"The template file is invalid: {exc}") from exc

        for entry in self.list_templates():
            if entry["name"].casefold() == template.name.casefold():
                raise ConflictError(f"A template named '{entry['name']}' already exists ({entry['id']})")

        local_id = _unique_slug(template.name, self.store.user_template_path_exists, fallback="template")
        template.id = make_template_id(USER_SOURCE, local_id)
        self.store.save_user_template(local_id, template.to_dict())
        return self._template_summary(template)

    # --- export ----------------------------------------------------------------

    def _export_modules(self, project: Project) -> list[tuple[Module, list[Requirement]]]:
        """Each module (display order) with its requirements, as the exports see them.

        A requirement shows only the values of attributes that apply to its
        module (a value left over from hand-edited data is not exported).
        """
        result: list[tuple[Module, list[Requirement]]] = []
        for module in self.list_modules(project.id):
            requirements = []
            for requirement in self.store.load_all_requirements(project.id, module.id):
                kept, dropped = self._applicable_values(project, module.id, requirement.attributes)
                requirements.append(replace(requirement, attributes=kept) if dropped else requirement)
            result.append((module, requirements))
        return result

    def export_reqif(self, project_id: str) -> str:
        from .reqif_export import export_project_to_reqif

        project = self.store.load_project(project_id)
        modules = self._export_modules(project)
        link_pairs = {
            config.id: self.store.load_link_pairs(project_id, config.id)
            for config in project.link_configurations
        }
        return export_project_to_reqif(project, modules, project.link_configurations, link_pairs)

    def export_csv(self, project_id: str) -> str:
        from .csv_export import export_project_to_csv

        project = self.store.load_project(project_id)
        modules = self._export_modules(project)
        return export_project_to_csv(project, modules)

    def export_xlsx(self, project_id: str) -> bytes:
        from .xlsx_export import export_project_to_xlsx

        project = self.store.load_project(project_id)
        modules = self._export_modules(project)
        return export_project_to_xlsx(project, modules)

    def export_docx(self, project_id: str) -> bytes:
        from .docx_export import export_project_to_docx

        project = self.store.load_project(project_id)
        modules = self._export_modules(project)
        return export_project_to_docx(project, modules)

    # --- import ----------------------------------------------------------------

    @_records_history
    def import_csv(self, project_id: str, module_id: str, csv_text: str) -> dict[str, Any]:
        """Create requirements in one module from CSV text (see ``csv_import.py``).

        All-or-nothing: the whole file is validated before anything is
        written, so an invalid file leaves the project unchanged. Rows are
        checked with the same ``_attribute_problems`` as add-requirement. If
        writing fails part-way, ``_write_new_requirements`` removes the files
        already written and the id counters stay unchanged. New
        requirements get server-allocated ids. Root rows are appended after
        the module's existing root requirements. Nested rows keep their file
        order under their parent.
        """
        from .csv_import import MAX_CSV_IMPORT_BYTES, parse_requirements_csv

        if not isinstance(csv_text, str):
            raise ValueError("CSV content must be a string")
        if len(csv_text) > MAX_CSV_IMPORT_BYTES:
            raise ValueError(
                f"The CSV file is too large: the limit is {MAX_CSV_IMPORT_BYTES // (1024 * 1024)} MiB"
            )
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        parsed = parse_requirements_csv(
            project.attributes,
            csv_text,
            validate=lambda values: self._attribute_problems(
                project, values, is_create=True, module_id=module_id
            ),
            applies=lambda attr: self._attribute_applies(attr, module_id),
        )

        existing = self.store.load_all_requirements(project_id, module_id)
        next_order: dict[str | None, int] = {None: self._next_sibling_order(existing, None)}

        new_ids: dict[int, str] = {}
        key_to_id: dict[str, str] = {}
        created: list[Requirement] = []
        for row in parsed.rows:
            requirement_id = self._allocate_requirement_id(project, module)
            new_ids[row.row_number] = requirement_id
            if row.key is not None:
                key_to_id[row.key] = requirement_id
            parent_id = key_to_id[row.parent_key] if row.parent_key is not None else None
            order = next_order.get(parent_id, 0)
            next_order[parent_id] = order + 1
            attributes = self._merged_attributes(project, row.attributes, module_id)
            created.append(Requirement(id=requirement_id, attributes=attributes, parent_id=parent_id, order=order))

        self._write_new_requirements(project, module_id, created)

        return {
            "imported": len(created),
            "requirement_ids": [new_ids[row.row_number] for row in sorted(parsed.rows, key=lambda r: r.row_number)],
            "ignored_columns": parsed.ignored_columns,
        }
