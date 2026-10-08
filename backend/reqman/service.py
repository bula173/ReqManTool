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
import threading
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .csv_import import RESERVED_COLUMNS
from .formats import PROJECT_FORMAT, NewerFormatError, upgrade
from .git_repo import DEFAULT_LOG_LIMIT, LOG_LIMIT, GitError, GitHistory, RepoConflict, local_remote_path
from .history import FileChange, build_snapshot, compare_snapshots, describe_changes
from . import module_tree, richtext
from .models import (
    DEFAULT_MODULE_TYPE,
    MODULE_TYPES,
    AttributeDefinition,
    LinkConfiguration,
    LinkPair,
    Module,
    ModuleFolder,
    Project,
    Requirement,
    RowStyle,
    View,
)
from .store import PROJECT_SLUG_PATTERN, ProjectStore, validate_prefix
from .templates import (
    BUILTIN_SOURCE,
    BUILTIN_TEMPLATES_DIR,
    LINK_REF_PREFIX,
    MAX_TEMPLATE_IMPORT_BYTES,
    USER_SOURCE,
    Template,
    TemplateAttribute,
    TemplateFolder,
    TemplateLinkConfiguration,
    TemplateModule,
    TemplateRowStyle,
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

ATTRIBUTE_TYPES = ("string", "text", "integer", "boolean", "enum", "date")

# A ``date`` attribute value: an ISO 8601 calendar date ``YYYY-MM-DD`` (a
# string, so it sorts and compares as text and round-trips through CSV).
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

# The (source module type, target module type) pairs a link configuration may
# join: requirements trace to requirements, test cases verify requirements and
# test executions execute test cases. Checked when a configuration is created
# (API or template); configurations already stored are never rejected.
LINK_TYPE_PAIRS = (
    ("requirements", "requirements"),
    ("test_cases", "requirements"),
    ("test_execution", "test_cases"),
)

# --- reports (traceability matrix, verification and test execution reports) ---
#
# The reports find the attributes they read by *role*, not by id, so they work
# for any project and not just the RBC template: for each role, the first name
# in ``names`` (priority order) that equals an applicable, non-system
# attribute's name or id wins -- both compared lower-case with punctuation and
# spaces collapsed ("Build/SW Version Under Test" -> "build sw version under
# test"). Failing that, a role with ``contains`` takes the first attribute whose
# name has one of those words; ``only_of_type`` takes the only attribute of the
# role's types, whatever its name.
REPORT_ATTRIBUTE_ROLES: dict[str, dict[str, Any]] = {
    "result": {
        "types": ("enum", "string"),
        "names": ("result", "test result", "execution result", "verdict", "outcome"),
    },
    "execution_date": {
        "types": ("date",),
        "names": ("execution date", "executed on", "date executed", "test date", "run date", "date"),
        "only_of_type": True,
    },
    "executed_by": {
        "types": ("string", "enum"),
        "names": ("executed by", "executor", "tester", "tested by", "run by"),
    },
    "version": {
        "types": ("string", "enum"),
        "names": (
            "build sw version under test", "version under test", "sw version under test",
            "software version under test", "build under test", "build", "version",
        ),
        "contains": ("version", "build"),
    },
    "evidence": {
        "types": ("string", "text"),
        "names": ("evidence log ref", "evidence", "evidence ref", "log ref", "log"),
        "contains": ("evidence",),
    },
    "object_type": {"types": ("enum", "string"), "names": ("object type",)},
    # The test case attribute a test result import matches on by default (else the requirement id).
    "test_id": {
        "types": ("string", "integer", "enum"),
        "names": (
            "test id", "test case id", "automated test id", "automation id", "external test id",
            "external id", "test key", "test ref",
        ),
    },
}

# Test result import: the pseudo attribute id meaning "match on the test
# case's requirement id", the attribute types a test case can be matched on,
# the value written to a ``string``/``text`` result attribute per result
# category, and the request options.
RESULT_MATCH_ID = "@id"
RESULT_MATCH_TYPES = ("string", "integer", "enum")
RESULT_TEXT_VALUES = {"passed": "Passed", "failed": "Failed", "blocked": "Blocked", "not_run": "Not Run"}
RESULT_IMPORT_OPTIONS = ("config_id", "match_attribute", "executed_by", "version", "execution_date", "file_name")
MAX_RESULT_IMPORT_ERRORS = 20

# Result values (compared like attribute names) per result category; any other
# value, and no value, counts as not run.
REPORT_RESULT_WORDS: dict[str, tuple[str, ...]] = {
    "passed": ("passed", "pass", "ok", "success", "succeeded"),
    "failed": ("failed", "fail", "failure", "nok", "not ok", "error"),
    "blocked": ("blocked",),
    "not_applicable": ("not applicable", "n a", "na"),
    "not_run": ("not run", "not executed", "pending", "skipped", ""),
}
REPORT_RESULTS = ("passed", "failed", "blocked", "not_run", "not_applicable")
REPORT_EXECUTED_RESULTS = ("passed", "failed", "blocked")
VERIFICATION_STATUSES = ("verified", "failed", "not_run", "not_covered")
EXECUTION_TEST_STATUSES = ("passed", "failed", "blocked", "not_run", "not_planned")

# Object Type values (compared like attribute names) of objects that are
# neither requirements nor test cases; the reports leave them out.
REPORT_EXCLUDED_OBJECT_TYPES = frozenset({"heading", "information", "definition", "title", "comment", "note"})

# The most projects one project may reference (``Project.references``).
MAX_PROJECT_REFERENCES = 100

# The most link configurations one traceability chain may have.
MAX_TRACE_CHAIN = 10

# Shared view column widths (``View.column_widths``): the key of the
# requirements table's Id column (attribute ids name the other columns) and
# the accepted width range in CSS pixels.
VIEW_ID_COLUMN = "@id"
MIN_COLUMN_WIDTH = 40
MAX_COLUMN_WIDTH = 4000

# Row styling rules (``Project.row_styles``): the most a project may have,
# the keys of one rule, and the color format.
MAX_ROW_STYLES = 200
ROW_STYLE_KEYS = ("attribute_id", "value", "font_color", "background_color", "bold")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Module folders (``Project.module_order``, project format 7): the deepest
# nesting a folder may be created or moved to (top-level folders are depth 1)
# and the longest folder name.
MAX_MODULE_FOLDER_DEPTH = 16
MAX_MODULE_FOLDER_NAME_LENGTH = 200

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
        self._last_touched.ids = set()
        if guards_project:
            project_id = args[0] if args else kwargs.get("project_id")
            if isinstance(project_id, str):
                self._require_no_sync_conflict(project_id)
        with self.store.recording_writes() as touched:
            result = method(self, *args, **kwargs)
        for project_id in sorted(touched):
            self._auto_commit(project_id)
        self._last_touched.ids = set(touched)
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
        # The projects the last successful mutation of this thread wrote
        # (``_records_history``), so the API can broadcast every one of them:
        # removing a requirement also cleans up links in referencing projects.
        self._last_touched = threading.local()

    def take_touched_projects(self) -> set[str]:
        """The ids of the projects the last successful mutation (this thread) wrote; then forgotten."""
        ids = getattr(self._last_touched, "ids", None) or set()
        self._last_touched.ids = set()
        return set(ids)

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
        order = module_tree.module_ids(module_tree.effective_tree(project.module_order, sorted(modules)))
        return [modules[m] for m in order]

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
        result = compare_snapshots(from_snapshot, to_snapshot)
        self._add_compare_row_styles(result, from_snapshot, to_snapshot)
        return {"from": from_meta, "to": to_meta, **result}

    @classmethod
    def _add_compare_row_styles(cls, result: dict[str, Any], old: dict[str, Any], new: dict[str, Any]) -> None:
        """Give each compared module the row style of its listed requirements.

        A module entry gains ``row_styles`` (requirement id -> rule) for the
        styled ones only, and only when there is one: added and modified
        requirements are styled by the "to" side's rules and values, removed
        ones by the "from" side's. A side whose project.json predates row
        styles, or cannot be read as a project, styles nothing.
        """

        def rules(snapshot: dict[str, Any]) -> list[RowStyle]:
            try:
                return cls._effective_row_styles(Project.from_dict(snapshot["project"]))
            except (KeyError, TypeError, AttributeError, ValueError):
                return []

        old_rules, new_rules = rules(old), rules(new)
        if not (old_rules or new_rules):
            return
        for module in result["modules"]:
            module_id = module["module_id"]
            styled: dict[str, Any] = {}
            listed = [(i, new, new_rules) for i in module["added"]]
            listed += [(m["id"], new, new_rules) for m in module["modified"]]
            listed += [(i, old, old_rules) for i in module["removed"]]
            for requirement_id, snapshot, side_rules in listed:
                data = snapshot["requirements"].get(module_id, {}).get(requirement_id, {})
                values = data.get("attributes") if isinstance(data, dict) else None
                style = cls._row_style_of(side_rules, values if isinstance(values, dict) else {})
                if style is not None:
                    styled[requirement_id] = style.to_dict()
            if styled:
                module["row_styles"] = styled

    @staticmethod
    def _row_style_of(rules: list[RowStyle], values: dict[str, Any]) -> RowStyle | None:
        """The first of ``rules`` whose attribute value ``values`` has (the DOCX export's rule)."""
        return next((r for r in rules if values.get(r.attribute_id) == r.value), None)

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
        self._check_references_in_use(
            project, self._effective_references(replace(project, parent_project_id=parent_project_id))
        )
        if project.parent_project_id != parent_project_id:
            project.parent_project_id = parent_project_id
            self.store.save_project(project)
        return project

    # --- project references -----------------------------------------------------

    @staticmethod
    def _effective_references(project: Project) -> list[str]:
        """The projects ``project`` references: its parent (implicitly), then ``references``.

        No duplicates and never the project itself (hand-edited data is
        filtered here, not rewritten). Link configurations may target the
        modules of these projects.
        """
        candidates = [project.parent_project_id] if isinstance(project.parent_project_id, str) else []
        refs: list[str] = []
        for ref in [*candidates, *project.references]:
            if ref and ref != project.id and ref not in refs:
                refs.append(ref)
        return refs

    def _check_references_in_use(self, project: Project, new_effective: list[str]) -> None:
        """Refuse to stop referencing a project that a link configuration of ``project`` targets.

        Removing an explicit reference, or reparenting away from the parent
        (implicitly referenced), would leave cross-project link
        configurations pointing at a project that is no longer referenced.
        The user removes those configurations first (which deletes their
        links); nothing is cleaned up implicitly.
        """
        dropped = set(self._effective_references(project)) - set(new_effective)
        in_use: dict[str, list[str]] = {}
        for config in project.link_configurations:
            if config.target_project_id in dropped:
                in_use.setdefault(config.target_project_id, []).append(config.name)
        if in_use:
            target, names = sorted(in_use.items())[0]
            listed = ", ".join(f"'{n}'" for n in names)
            raise ValueError(
                f"Project '{project.id}' cannot stop referencing project '{target}': link "
                f"configuration(s) {listed} target it. Remove them first"
            )

    def _checked_references(self, project_id: str, references: Any) -> list[str]:
        """``references`` as stored: existing, loadable project ids, never ``project_id``, no duplicates.

        ``None`` means none. A dangling id is not found (404); a project that
        cannot be loaded (newer format 409, broken file 400) cannot be
        referenced either, since its modules cannot be read.
        """
        if references is None:
            return []
        if not isinstance(references, list) or not all(isinstance(r, str) and r for r in references):
            raise ValueError("references must be a list of project ids")
        if len(references) > MAX_PROJECT_REFERENCES:
            raise ValueError(f"A project can reference at most {MAX_PROJECT_REFERENCES} projects")
        listed = set(self.store.list_project_ids())
        result: list[str] = []
        for ref in references:
            if ref == project_id:
                raise ValueError("A project cannot reference itself")
            if ref in result:
                continue
            if ref not in listed:
                raise FileNotFoundError(f"Referenced project '{ref}' not found")
            self.store.load_project(ref)
            result.append(ref)
        return result

    @_records_history
    def set_project_references(self, project_id: str, references: Any) -> Project:
        """Replace the projects ``project_id`` references explicitly (``None``/``[]``: none).

        The parent stays referenced implicitly whether or not it is listed.
        Dropping a reference that a cross-project link configuration
        targets is refused (``_check_references_in_use``).
        """
        project = self.store.load_project(project_id)
        refs = self._checked_references(project_id, references)
        self._check_references_in_use(project, self._effective_references(replace(project, references=refs)))
        if project.references != refs:
            project.references = refs
            self.store.save_project(project)
        return project

    def project_references(self, project_id: str) -> dict[str, Any]:
        """The projects ``project_id`` references and the projects that reference it (read-only).

        ``references``: the effective list (parent first, marked
        ``implicit``), each with its name (or ``error`` when it is missing
        or cannot be loaded) and the number of this project's link
        configurations targeting it. ``referenced_by``: every loadable
        project whose effective references include this one, with its
        configurations targeting this project and their link counts.
        """
        project = self.store.load_project(project_id)
        listed = set(self.store.list_project_ids())
        references: list[dict[str, Any]] = []
        for ref in self._effective_references(project):
            entry: dict[str, Any] = {
                "id": ref,
                "name": ref,
                "implicit": ref == project.parent_project_id and ref not in project.references,
                "link_configurations": sum(1 for c in project.link_configurations if c.target_project_id == ref),
            }
            try:
                if ref not in listed:
                    raise FileNotFoundError(f"Project '{ref}' not found")
                entry["name"] = self.store.load_project(ref).name
            except (OSError, ValueError) as exc:
                entry["error"] = str(exc)
            references.append(entry)
        referenced_by: list[dict[str, Any]] = []
        for other in self._other_projects(project_id):
            if project_id not in self._effective_references(other):
                continue
            configs = [
                {
                    "id": c.id,
                    "name": c.name,
                    "links": len(self.store.load_link_pairs(other.id, c.id)),
                }
                for c in other.link_configurations
                if c.target_project_id == project_id
            ]
            referenced_by.append({"id": other.id, "name": other.name, "link_configurations": configs})
        return {"project_id": project_id, "references": references, "referenced_by": referenced_by}

    def _other_projects(self, project_id: str) -> list[Project]:
        """Every loadable project except ``project_id`` (unloadable ones are skipped, never written)."""
        projects: list[Project] = []
        for other_id in self.store.list_project_ids():
            if other_id == project_id:
                continue
            try:
                projects.append(self.store.load_project(other_id))
            except (OSError, ValueError):
                continue
        return projects

    def _load_link_target_project(self, project: Project, config: LinkConfiguration) -> str:
        """The project holding a configuration's target module; checks a cross-project one is referenced."""
        if config.target_project_id is None:
            return project.id
        if config.target_project_id not in self._effective_references(project):
            raise ValueError(
                f"Link configuration '{config.name}' targets project '{config.target_project_id}', "
                f"which project '{project.id}' no longer references"
            )
        if not self.store.project_exists(config.target_project_id):
            raise FileNotFoundError(f"Project '{config.target_project_id}' not found")
        self.store.load_project(config.target_project_id)
        return config.target_project_id

    @staticmethod
    def _local_configs(project: Project) -> list[LinkConfiguration]:
        """The project's link configurations between its own modules (not cross-project ones)."""
        return [c for c in project.link_configurations if c.target_project_id is None]

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
        module_types: list[str] | None = None,
    ) -> Project:
        """Add an attribute definition, optionally scoped to some modules and/or module types.

        ``modules`` lists the module ids the attribute applies to and
        ``module_types`` the module types it applies to (every module of
        such a type); the attribute applies to a module named by either.
        ``None`` or empty for both means every module (see
        ``_attribute_applies``). Only an optional attribute can be scoped
        (``_check_required_scope``).
        """
        project = self.store.load_project(project_id)
        definition = self._new_attribute_definition(project, attr_id, name, type, required, default, choices)
        definition.modules = self._checked_attribute_modules(project, modules)
        definition.module_types = self._checked_module_types(module_types)
        self._check_required_scope(
            definition.name, definition.required, [*definition.modules, *definition.module_types]
        )
        project.attributes.append(definition)
        self.store.save_project(project)
        return project

    @_records_history
    def set_attribute_modules(
        self, project_id: str, attr_id: str, modules: Any, drop_values: Any = False, module_types: Any = None
    ) -> dict[str, Any]:
        """Change the modules and module types an attribute applies to (both empty: every module).

        The whole scope is replaced: ``module_types`` omitted (``None``)
        means no type scope, like an empty list. Values the attribute has in
        requirements of modules outside the new
        scope cannot stay: a non-blank value is refused (``ValueError``)
        unless ``drop_values`` is true, in which case it is removed and
        reported in ``dropped_values``. Blank values there are removed
        silently. System (link-mirror) attributes keep their own scope and
        the built-in ObjectText applies to every module. A required attribute
        applies to every module: only ``[]``/``None`` is accepted for it.
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
        type_scope = self._checked_module_types(module_types)
        self._check_required_scope(attr.name, attr.required, [*scope, *type_scope])
        scoped = replace(attr, modules=scope, module_types=type_scope)

        outside: list[tuple[str, Requirement]] = []
        if scope or type_scope:
            for module in self.list_modules(project_id):
                module_id = module.id
                if self._attribute_applies(scoped, module_id, module.type):
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
        attr.module_types = type_scope
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
    def _checked_module_types(module_types: Any) -> list[str]:
        """``module_types`` as a stored attribute type scope, in ``MODULE_TYPES`` order.

        ``None`` or an empty list means no type scope (stored as ``[]``).
        Duplicates collapse; an unknown type is refused.
        """
        if module_types is None:
            return []
        if not isinstance(module_types, list) or not all(isinstance(t, str) for t in module_types):
            raise ValueError("Attribute module_types must be a list of module types")
        unknown = sorted(set(module_types) - set(MODULE_TYPES))
        if unknown:
            raise ValueError(f"Unknown module type(s) {unknown}; expected some of {', '.join(MODULE_TYPES)}")
        return [t for t in MODULE_TYPES if t in module_types]

    @staticmethod
    def _checked_module_type(module_type: Any) -> str:
        """``module_type`` if it is one of ``MODULE_TYPES`` (``None``: the default ``requirements``)."""
        if module_type is None:
            return DEFAULT_MODULE_TYPE
        if not isinstance(module_type, str) or module_type not in MODULE_TYPES:
            raise ValueError(f"Unknown module type {module_type!r}; expected one of {', '.join(MODULE_TYPES)}")
        return module_type

    @staticmethod
    def _check_required_scope(name: Any, required: Any, scope: list[str]) -> None:
        """Refuse to limit a required attribute to some modules.

        A required attribute applies to every module; only optional
        attributes can be scoped. Shared by ``add_attribute_definition``,
        ``set_attribute_modules`` and templates (``_build_from_template``).
        """
        if required and scope:
            raise ValueError(
                f"Required attribute '{name}' applies to every module; "
                "only optional attributes can be limited to modules"
            )

    @staticmethod
    def _attribute_applies(attr: AttributeDefinition, module_id: str, module_type: str) -> bool:
        """Whether ``attr`` applies to requirements of module ``module_id`` of type ``module_type``.

        An attribute without a module list and without a module type list
        applies everywhere. Otherwise it applies to the modules it lists and
        to every module of a type it lists. Ids or types in the lists that
        name nothing (hand-edited data) are harmless. A required attribute
        applies everywhere even if a hand-edited file gives it a scope: the
        API never lets one be scoped, and the stored lists are kept (not
        rewritten) until the scope is reset.
        """
        if attr.required or (not attr.modules and not attr.module_types):
            return True
        return module_id in attr.modules or module_type in attr.module_types

    def _module_type(self, project_id: str, module_id: str) -> str:
        """The type of an existing module (``FileNotFoundError`` if it does not exist)."""
        return self.store.load_module(project_id, module_id).type

    @classmethod
    def _applicable_values(
        cls, project: Project, module: Module, attributes: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """``attributes`` without the values of attributes that do not apply to ``module``.

        Returns the kept values and the dropped attribute ids (in project
        attribute order). Unknown keys are kept. Used by the exports; moving
        or copying a requirement to another module must use it too.
        """
        excluded = [
            a.id
            for a in project.attributes
            if a.id in attributes and not cls._attribute_applies(a, module.id, module.type)
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
        if type == "text" and isinstance(default, str):
            default = richtext.sanitize(default)
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

    # --- row styles ----------------------------------------------------------------

    @_records_history
    def set_row_styles(self, project_id: str, row_styles: Any) -> Project:
        """Replace the project's row styling rules (None or ``[]`` clears them).

        Each rule styles the rows whose enum attribute ``attribute_id`` has
        ``value``: ``font_color``/``background_color`` (``#rrggbb``, stored
        lower-case, or null) and ``bold``. Validated by ``_checked_row_styles``;
        an invalid list raises ``ValueError`` and nothing is written.
        """
        project = self.store.load_project(project_id)
        project.row_styles = self._checked_row_styles(project, row_styles)
        self.store.save_project(project)
        return project

    @staticmethod
    def _checked_row_styles(project: Project, row_styles: Any) -> list[RowStyle]:
        """``row_styles`` as stored rules for ``project``, in the order given.

        A list of at most ``MAX_ROW_STYLES`` objects with exactly the keys of
        ``ROW_STYLE_KEYS`` (``font_color``, ``background_color`` and ``bold``
        may be omitted). The attribute must be a (non-system) enum attribute
        of the project and the value one of its choices; an (attribute,
        value) pair may appear once, and a rule must set at least one of
        font color, background color or bold. Rules are checked only when
        written: attributes and choices cannot be removed or renamed through
        the API, and a rule that hand-edited data no longer resolves is
        ignored where styles are applied (``_effective_row_styles``).
        """
        if row_styles is None:
            return []
        if not isinstance(row_styles, list):
            raise ValueError("row_styles must be a list of row style rules")
        if len(row_styles) > MAX_ROW_STYLES:
            raise ValueError(f"A project can have at most {MAX_ROW_STYLES} row styles")
        attributes = {a.id: a for a in project.attributes}
        seen: set[tuple[str, str]] = set()
        result: list[RowStyle] = []
        for index, raw in enumerate(row_styles):
            where = f"row_styles[{index}]"
            if not isinstance(raw, dict):
                raise ValueError(f"{where}: must be an object")
            unknown = sorted(str(k) for k in raw if k not in ROW_STYLE_KEYS)
            if unknown:
                raise ValueError(f"{where}: unknown key(s) {unknown}")
            attr_id = raw.get("attribute_id")
            attr = attributes.get(attr_id) if isinstance(attr_id, str) else None
            if attr is None:
                raise ValueError(f"{where}: unknown attribute {attr_id!r}")
            if attr.type != "enum" or attr.system:
                raise ValueError(f"{where}: attribute '{attr.name}' is not an enum attribute")
            value = raw.get("value")
            if not isinstance(value, str) or value not in (attr.choices or []):
                raise ValueError(f"{where}: {value!r} is not a choice of attribute '{attr.name}'")
            if (attr.id, value) in seen:
                raise ValueError(f"{where}: attribute '{attr.name}' value '{value}' already has a row style")
            seen.add((attr.id, value))
            colors: dict[str, str | None] = {}
            for key in ("font_color", "background_color"):
                color = raw.get(key)
                if color is not None and not (isinstance(color, str) and _HEX_COLOR.match(color)):
                    raise ValueError(f"{where}: '{key}' must be a color '#rrggbb' or null")
                colors[key] = color.lower() if color is not None else None
            bold = raw.get("bold", False)
            if not isinstance(bold, bool):
                raise ValueError(f"{where}: 'bold' must be true or false")
            if colors["font_color"] is None and colors["background_color"] is None and not bold:
                raise ValueError(
                    f"{where}: attribute '{attr.name}' value '{value}' sets no font color, background color or bold"
                )
            result.append(RowStyle(attribute_id=attr.id, value=value, bold=bold, **colors))
        return result

    @staticmethod
    def _effective_row_styles(project: Project) -> list[RowStyle]:
        """The project's rules that still resolve: enum attribute, value among its choices.

        Hand-edited data can leave a rule naming a missing attribute or
        choice; such a rule is ignored (not rewritten). The first rule for
        an (attribute, value) pair wins.
        """
        attributes = {a.id: a for a in project.attributes}
        seen: set[tuple[str, str]] = set()
        result: list[RowStyle] = []
        for style in project.row_styles:
            attr = attributes.get(style.attribute_id)
            key = (style.attribute_id, style.value)
            if attr is None or attr.type != "enum" or attr.system or key in seen:
                continue
            if style.value not in (attr.choices or []):
                continue
            if style.font_color is None and style.background_color is None and not style.bold:
                continue
            seen.add(key)
            result.append(style)
        return result

    # --- modules ----------------------------------------------------------------

    @_records_history
    def create_module(
        self,
        project_id: str,
        name: str,
        prefix: str,
        description: str = "",
        type: Any = None,
        folder_id: Any = None,
    ) -> Module:
        """Create a module, appended at the end of the top level or of folder ``folder_id``.

        ``type`` is one of ``MODULE_TYPES`` (``None``: ``requirements``); it
        is chosen at creation and cannot be changed later, so the link
        configurations checked against it stay valid. ``folder_id`` names a
        module folder of the project (``None``: the top level); it is
        checked before anything is written.

        The module file is written first, then project.json with the new
        tree. If the second write fails, the module is still listed: an
        existing module missing from the tree is appended in id order.
        """
        project = self.store.load_project(project_id)
        validate_prefix(prefix)
        module_type = self._checked_module_type(type)
        tree = self._module_tree(project)
        container = self._folder_container(project, tree, folder_id)
        module_id = _unique_slug(
            name,
            lambda c: self.store.module_exists(project_id, c),
            fallback="module",
            max_length=self.store.max_name_length(),
        )
        module = Module(id=module_id, name=name, prefix=prefix, description=description, type=module_type)
        self.store.save_module(project_id, module)
        container.append(module_id)
        project.module_order = tree
        self.store.save_project(project)
        return module

    def get_module(self, project_id: str, module_id: str) -> Module:
        return self.store.load_module(project_id, module_id)

    def _module_tree(self, project: Project) -> list:
        """The project's module tree as displayed (``module_tree.effective_tree``).

        ``project.module_order`` is applied tolerantly, so hand-edited or
        older data always lists every module exactly once: entries naming a
        module that does not exist (or repeating one) are skipped, and
        modules the tree does not mention follow at the top level in id
        order. A project without a stored tree (older data) lists its
        modules in id order. The stored value is not rewritten here; the
        result holds new folder objects, safe to modify.
        """
        return module_tree.effective_tree(project.module_order, self.store.list_module_ids(project.id))

    def _module_order(self, project: Project) -> list[str]:
        """The project's module ids in display order: the tree depth-first (see ``_module_tree``)."""
        return module_tree.module_ids(self._module_tree(project))

    def list_modules(self, project_id: str) -> list[Module]:
        """The project's modules in display order (see ``_module_order``)."""
        if not self.store.project_exists(project_id):
            return []
        project = self.store.load_project(project_id)
        return [self.store.load_module(project_id, mid) for mid in self._module_order(project)]

    def get_module_tree(self, project_id: str) -> list[dict[str, Any]]:
        """The project's module tree as displayed, in the ``project.json`` entry shape.

        ``[{"module": id} | {"folder": id, "name": ..., "children": [...]}]``:
        every module exactly once (see ``_module_tree``).
        """
        project = self.store.load_project(project_id)
        return [e.to_dict() if isinstance(e, ModuleFolder) else {"module": e} for e in self._module_tree(project)]

    def _store_module_tree(self, project: Project, tree: list) -> None:
        """Save ``tree`` as the project's module tree unless it equals the effective one (no write)."""
        if tree != self._module_tree(project):
            project.module_order = tree
            self.store.save_project(project)

    def _folder_container(self, project: Project, tree: list, folder_id: Any) -> list:
        """The entry list of folder ``folder_id`` in ``tree`` (``None``: the top level); 404 if unknown."""
        if folder_id is None:
            return tree
        if not isinstance(folder_id, str):
            raise ValueError("Folder id must be a string or null")
        folder = module_tree.find_folder(tree, folder_id)
        if folder is None:
            raise FileNotFoundError(f"Module folder '{folder_id}' not found in project '{project.id}'")
        return folder.children

    def _locate_folder(self, project: Project, tree: list, folder_id: Any) -> tuple[list, ModuleFolder, ModuleFolder | None]:
        """The list holding folder ``folder_id``, the folder and its parent folder (``None``: top level)."""
        if not isinstance(folder_id, str):
            raise ValueError("Folder id must be a string")
        found = module_tree.find_container(tree, "folder", folder_id)
        if found is None:
            raise FileNotFoundError(f"Module folder '{folder_id}' not found in project '{project.id}'")
        container, parent = found
        folder = container[module_tree.index_of(container, "folder", folder_id)]
        return container, folder, parent

    @staticmethod
    def _checked_folder_name(name: Any, siblings: list, folder_id: str | None = None) -> str:
        """``name`` stripped, if it is a valid name for a folder among ``siblings`` (excluding ``folder_id``).

        Not empty, at most ``MAX_MODULE_FOLDER_NAME_LENGTH`` characters, and
        not the name of a sibling folder (compared case-insensitively).
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Folder name must not be empty")
        name = name.strip()
        if len(name) > MAX_MODULE_FOLDER_NAME_LENGTH:
            raise ValueError(f"Folder name is too long: at most {MAX_MODULE_FOLDER_NAME_LENGTH} characters")
        for entry in siblings:
            if isinstance(entry, ModuleFolder) and entry.id != folder_id and entry.name.strip().casefold() == name.casefold():
                raise ValueError(f"A folder named '{entry.name}' already exists here")
        return name

    @staticmethod
    def _checked_entry_index(index: Any) -> int | None:
        if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index < 0):
            raise ValueError(f"Invalid index {index!r}: must be a non-negative integer")
        return index

    @staticmethod
    def _folder_summary(folder: ModuleFolder, parent: ModuleFolder | None) -> dict[str, Any]:
        """A folder as the folder operations return it."""
        return {"id": folder.id, "name": folder.name, "parent_id": parent.id if parent else None}

    @_records_history
    def create_module_folder(self, project_id: str, name: Any, parent_id: Any = None) -> dict[str, Any]:
        """Create an empty module folder at the end of the top level or of folder ``parent_id``.

        The id is allocated by the server: a slug of the name, suffixed on
        collision with any folder of the project. The name must be unique
        among the sibling folders; nesting is limited to
        ``MAX_MODULE_FOLDER_DEPTH`` levels. Returns ``{id, name, parent_id}``.
        """
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        container = self._folder_container(project, tree, parent_id)
        name = self._checked_folder_name(name, container)
        depth = module_tree.depth_of(tree, parent_id) + 1 if parent_id is not None else 1
        if depth > MAX_MODULE_FOLDER_DEPTH:
            raise ValueError(f"Module folders can be nested at most {MAX_MODULE_FOLDER_DEPTH} levels deep")
        taken = {folder.id for folder, _ in module_tree.iter_folders(tree)}
        folder = ModuleFolder(id=_unique_slug(name, lambda c: c in taken, fallback="folder"), name=name)
        container.append(folder)
        project.module_order = tree
        self.store.save_project(project)
        parent = module_tree.find_folder(tree, parent_id) if parent_id is not None else None
        return self._folder_summary(folder, parent)

    @_records_history
    def rename_module_folder(self, project_id: str, folder_id: Any, name: Any) -> dict[str, Any]:
        """Rename a module folder (its id stays). The new name must be unique among its sibling folders."""
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        container, folder, parent = self._locate_folder(project, tree, folder_id)
        folder.name = self._checked_folder_name(name, container, folder.id)
        self._store_module_tree(project, tree)
        return self._folder_summary(folder, parent)

    @_records_history
    def delete_module_folder(self, project_id: str, folder_id: Any) -> dict[str, Any]:
        """Delete an empty module folder. A folder holding modules or folders is refused (400)."""
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        container, folder, _ = self._locate_folder(project, tree, folder_id)
        if folder.children:
            raise ValueError(
                f"Module folder '{folder.name}' is not empty: move its modules and folders out first"
            )
        container.remove(folder)
        project.module_order = tree
        self.store.save_project(project)
        return {"id": folder.id, "deleted": True}

    @_records_history
    def move_module(self, project_id: str, module_id: str, folder_id: Any = None, index: Any = None) -> Module:
        """Move a module into folder ``folder_id`` (``None``: the top level).

        Without ``index`` it is appended after the folder's last entry; with
        ``index`` (a non-negative int) it is placed at that 0-based position
        among the folder's entries (modules and folders) after the move; an
        index past the end appends. Moving within the same folder with an
        index repositions it. Module ids, requirements and links are
        unaffected. A move that changes nothing writes nothing.
        """
        index = self._checked_entry_index(index)
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        tree = self._module_tree(project)
        target = self._folder_container(project, tree, folder_id)
        found = module_tree.find_container(tree, "module", module_id)
        if found is None:  # every existing module is in the effective tree
            raise FileNotFoundError(f"Module '{module_id}' not found in project '{project_id}'")
        source, _ = found
        source.remove(module_id)
        target.insert(len(target) if index is None else min(index, len(target)), module_id)
        self._store_module_tree(project, tree)
        return module

    @_records_history
    def move_module_folder(
        self, project_id: str, folder_id: Any, parent_id: Any = None, index: Any = None
    ) -> dict[str, Any]:
        """Move a folder (with everything in it) into folder ``parent_id`` (``None``: the top level).

        ``index`` works as for ``move_module``. A folder cannot be moved into
        itself or one of its subfolders, its name must be unique among the
        new sibling folders, and the result must not nest deeper than
        ``MAX_MODULE_FOLDER_DEPTH``. A move that changes nothing writes
        nothing.
        """
        index = self._checked_entry_index(index)
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        source, folder, _ = self._locate_folder(project, tree, folder_id)
        if parent_id is not None:
            if parent_id == folder.id:
                raise ValueError("A folder cannot be moved into itself")
            if module_tree.find_folder(folder.children, parent_id) is not None:
                raise ValueError("Cannot move a folder into one of its own subfolders")
        target = self._folder_container(project, tree, parent_id)
        self._checked_folder_name(folder.name, target, folder.id)
        parent_depth = module_tree.depth_of(tree, parent_id) if parent_id is not None else 0
        if parent_depth + module_tree.height(folder) > MAX_MODULE_FOLDER_DEPTH:
            raise ValueError(f"Module folders can be nested at most {MAX_MODULE_FOLDER_DEPTH} levels deep")
        source.remove(folder)
        target.insert(len(target) if index is None else min(index, len(target)), folder)
        self._store_module_tree(project, tree)
        parent = module_tree.find_folder(tree, parent_id) if parent_id is not None else None
        return self._folder_summary(folder, parent)

    @staticmethod
    def _swap_with_neighbor(container: list, index: int, direction: str) -> bool:
        neighbor_index = index - 1 if direction == "up" else index + 1
        if not 0 <= neighbor_index < len(container):
            return False
        container[index], container[neighbor_index] = container[neighbor_index], container[index]
        return True

    @_records_history
    def reorder_module(self, project_id: str, module_id: str, direction: str) -> Module:
        """Move a module one place up or down among the entries of its folder (or the top level).

        Its siblings are the modules and folders of the same folder. Moving
        the first entry up or the last one down changes nothing and writes
        nothing. Otherwise the whole effective tree is stored, which also
        drops entries naming missing modules.
        """
        if direction not in ("up", "down"):
            raise ValueError(f"Invalid direction '{direction}': must be 'up' or 'down'")
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        tree = self._module_tree(project)
        found = module_tree.find_container(tree, "module", module_id)
        if found is not None:
            container, _ = found
            if self._swap_with_neighbor(container, module_tree.index_of(container, "module", module_id), direction):
                project.module_order = tree
                self.store.save_project(project)
        return module

    @_records_history
    def reorder_module_folder(self, project_id: str, folder_id: Any, direction: str) -> dict[str, Any]:
        """Move a folder one place up or down among the entries of its parent folder (or the top level)."""
        if direction not in ("up", "down"):
            raise ValueError(f"Invalid direction '{direction}': must be 'up' or 'down'")
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        container, folder, parent = self._locate_folder(project, tree, folder_id)
        if self._swap_with_neighbor(container, module_tree.index_of(container, "folder", folder.id), direction):
            project.module_order = tree
            self.store.save_project(project)
        return self._folder_summary(folder, parent)

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
        elif attr.type == "date":
            if not ProjectService._is_iso_date(value):
                return f"{attr.name}: '{value}' is not a date (expected YYYY-MM-DD)"
        return None

    @staticmethod
    def _is_iso_date(value: Any) -> bool:
        """Whether ``value`` is an existing calendar date written ``YYYY-MM-DD``."""
        if not isinstance(value, str) or not _ISO_DATE.match(value):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True

    def _attribute_problems(
        self, project: Project, attributes: dict[str, Any], *, is_create: bool, module_id: str, module_type: str
    ) -> list[str]:
        """Every reason ``attributes`` cannot be written, in a stable order.

        The one validation path for requirement attributes: REST/WebSocket
        add and update (via ``_validate_attributes``) and the CSV import (one
        call per row) all use it. ``attributes`` must hold typed values; the
        CSV import parses text cells before calling this. ``module_id`` is the
        requirement's module (of type ``module_type``): an attribute that does
        not apply to it cannot be written, and is neither required nor
        defaulted there.
        """
        defined = {a.id: a for a in project.attributes}
        unknown = set(attributes) - set(defined)
        if unknown:
            return [f"Unknown attribute(s) for project '{project.id}': {sorted(unknown)}"]
        system_ids = {a.id for a in project.attributes if a.system} & set(attributes)
        if system_ids:
            return [f"Attribute(s) are system-managed and cannot be set directly: {sorted(system_ids)}"]
        not_applicable = [
            a.name
            for a in project.attributes
            if a.id in attributes and not self._attribute_applies(a, module_id, module_type)
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
        effective = self._merged_attributes(project, attributes, module_id, module_type) if is_create else attributes
        for attr in project.attributes:
            if not attr.required or attr.system or attr.id in invalid_ids:
                continue
            if not self._attribute_applies(attr, module_id, module_type):
                continue
            if (is_create or attr.id in attributes) and self._is_blank(effective.get(attr.id)):
                problems.append(f"required attribute '{attr.name}' is empty")
        return problems

    @staticmethod
    def _is_blank(value: Any) -> bool:
        """``None``, ``""`` and whitespace-only strings (rich text: its plain projection) count as no value."""
        return value is None or (isinstance(value, str) and not richtext.to_plain(value).strip())

    @staticmethod
    def _sanitized_text_values(project: Project, attributes: Any) -> Any:
        """``attributes`` with every string value of a ``text`` attribute sanitised.

        Rich text values (see ``richtext.py``) are reduced to the allowed
        XHTML subset (disallowed parts are stripped, never rejected); plain
        values are unchanged. Every write path that stores requirement values
        given by a client or copied from disk goes through this: add, update,
        copy (and the CSV import via ``richtext.from_plain``). Anything that
        is not a dict, unknown keys, system attributes and non-string values
        are returned unchanged for validation to judge.
        """
        if not isinstance(attributes, dict):
            return attributes
        text_ids = {a.id for a in project.attributes if a.type == "text" and not a.system}
        return {
            k: richtext.sanitize(v) if k in text_ids and isinstance(v, str) else v for k, v in attributes.items()
        }

    def _validate_attributes(
        self, project: Project, attributes: dict[str, Any], *, is_create: bool, module_id: str, module_type: str
    ) -> None:
        if not isinstance(attributes, dict):
            raise ValueError("Attributes must be an object")
        problems = self._attribute_problems(
            project, attributes, is_create=is_create, module_id=module_id, module_type=module_type
        )
        if problems:
            raise ValueError("; ".join(problems))

    @classmethod
    def _merged_attributes(
        cls, project: Project, attributes: dict[str, Any], module_id: str, module_type: str
    ) -> dict[str, Any]:
        """Attribute defaults overlaid with ``attributes``.

        System attributes, and attributes that do not apply to ``module_id``,
        never get a default. A stored default that is not
        valid for its attribute (legacy data written before defaults were
        checked) is skipped rather than rewritten, so it cannot block or
        corrupt new requirements. A ``text`` default is sanitised like a
        given value (a hand-edited project.json may hold unsanitised markup);
        one ``sanitize`` rejects (over the size limits) is skipped the same
        way. A default the client overrides is not looked at.
        """
        merged: dict[str, Any] = {}
        for a in project.attributes:
            if (
                a.id in attributes
                or a.default is None
                or a.system
                or not cls._attribute_applies(a, module_id, module_type)
                or cls._value_problem(a, a.default) is not None
            ):
                continue
            default = a.default
            if a.type == "text" and isinstance(default, str):
                try:
                    default = richtext.sanitize(default)
                except ValueError:
                    continue
            merged[a.id] = default
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

        attributes = self._sanitized_text_values(project, attributes)
        self._validate_attributes(project, attributes, is_create=True, module_id=module_id, module_type=module.type)
        merged = self._merged_attributes(project, attributes, module_id, module.type)

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
            attributes = self._sanitized_text_values(
                project, {k: v for k, v in original.attributes.items() if k not in system_ids}
            )
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
        module_type = self._module_type(project_id, module_id)
        attributes = self._sanitized_text_values(project, attributes)
        self._validate_attributes(project, attributes, is_create=False, module_id=module_id, module_type=module_type)
        requirement = self.store.load_requirement(project_id, module_id, requirement_id)
        self._mark_rich_text_format(project, attributes)
        requirement.attributes.update(attributes)
        self.store.save_requirement(project_id, module_id, requirement)
        return requirement

    def _mark_rich_text_format(self, project: Project, values: dict[str, Any]) -> None:
        """Rewrite project.json at the current format if ``values`` store rich text in an older-format project.

        Rich text needs project format 4 (an older ReqManTool would show it as
        raw markup), but loading upgrades only in memory. Add, copy and
        import always save the project (its id counters); a write that
        does not, such as an update, calls this before writing the values,
        within the same history commit. A plain value leaves the stored
        version as it is.
        """
        text_ids = {a.id for a in project.attributes if a.type == "text" and not a.system}
        if not any(k in text_ids and richtext.is_rich(v) for k, v in values.items()):
            return
        if self.store.stored_project_format_version(project.id) < PROJECT_FORMAT.current:
            self.store.save_project(project)

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
        """Remove the project's own link pairs with ``requirement_id`` at either end; refresh the mirrors.

        A cross-project configuration's targets live in another project, so
        only its sources can be ``requirement_id`` here (a target with the
        same id is another project's requirement).
        """
        project = self.store.load_project(project_id)
        for config in project.link_configurations:
            cross = config.target_project_id is not None
            pairs = self.store.load_link_pairs(project_id, config.id)
            remaining = []
            affected: list[str] = []
            for pair in pairs:
                if pair.source_id == requirement_id or (not cross and pair.target_id == requirement_id):
                    other_id = pair.target_id if pair.source_id == requirement_id else pair.source_id
                    affected.append(other_id)
                    continue
                remaining.append(pair)
            if len(remaining) != len(pairs):
                self.store.save_link_pairs(project_id, config.id, remaining)
                if cross:
                    continue  # the other ends are in the target project, which stores no mirror
                for other_id in affected:
                    other_module = self.store.find_requirement_module(project_id, other_id)
                    if other_module is not None:
                        self._refresh_link_attribute(project_id, config.id, other_module, other_id, config)

    def _incoming_link_cleanup(
        self, project_id: str, requirement_ids: set[str]
    ) -> list[tuple[Project, LinkConfiguration, list[LinkPair]]]:
        """The cross-project link pairs of other projects that target ``requirement_ids`` of ``project_id``.

        Each as (referencing project, its configuration, all its pairs).
        Projects that cannot be loaded are skipped (never written).
        """
        affected: list[tuple[Project, LinkConfiguration, list[LinkPair]]] = []
        if not requirement_ids:
            return affected
        for other in self._other_projects(project_id):
            for config in other.link_configurations:
                if config.target_project_id != project_id:
                    continue
                pairs = self.store.load_link_pairs(other.id, config.id)
                if any(p.target_id in requirement_ids for p in pairs):
                    affected.append((other, config, pairs))
        return affected

    def _check_incoming_cleanup(self, affected: list[tuple[Project, LinkConfiguration, list[LinkPair]]]) -> None:
        """Refuse (409) before anything is written if a referencing project to clean up has a git sync conflict."""
        for other_id in sorted({other.id for other, _, _ in affected}):
            self._require_no_sync_conflict(other_id)

    def _apply_incoming_cleanup(
        self, affected: list[tuple[Project, LinkConfiguration, list[LinkPair]]], requirement_ids: set[str]
    ) -> None:
        """Delete the pairs targeting ``requirement_ids`` in the referencing projects; refresh their source mirrors.

        This writes into the referencing (source) projects, whose
        repositories commit it like any change of theirs; the target
        project never stores anything about links pointing at it.
        """
        for other, config, pairs in affected:
            remaining = [p for p in pairs if p.target_id not in requirement_ids]
            self.store.save_link_pairs(other.id, config.id, remaining)
            for source_id in sorted({p.source_id for p in pairs if p.target_id in requirement_ids}):
                module_id = self.store.find_requirement_module(other.id, source_id)
                if module_id is not None:
                    self._refresh_link_attribute(other.id, config.id, module_id, source_id, config)

    @_records_history
    def remove_requirement(self, project_id: str, module_id: str, requirement_id: str) -> None:
        """Remove a requirement: children are promoted, its links are deleted.

        Links to it from other projects (cross-project link configurations
        of projects referencing this one) are deleted too, in those
        projects, so no project keeps a link to a requirement that no longer
        exists.
        """
        removed = self.store.load_requirement(project_id, module_id, requirement_id)
        incoming = self._incoming_link_cleanup(project_id, {requirement_id})
        self._check_incoming_cleanup(incoming)
        for other in self.store.load_all_requirements(project_id, module_id):
            if other.parent_id == requirement_id:
                other.parent_id = removed.parent_id
                self.store.save_requirement(project_id, module_id, other)

        self._cleanup_links_for_requirement(project_id, requirement_id)
        self._apply_incoming_cleanup(incoming, {requirement_id})
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
        """Delete a module's requirements, their links and the links to them from referencing projects."""
        ids = set(self.store.list_requirement_ids(project_id, module_id))
        incoming = self._incoming_link_cleanup(project_id, ids)
        self._check_incoming_cleanup(incoming)
        for requirement_id in ids:
            self._cleanup_links_for_requirement(project_id, requirement_id)
            self.store.delete_requirement(project_id, module_id, requirement_id)
        self._apply_incoming_cleanup(incoming, ids)
        return len(ids)

    @_records_history
    def purge_project(self, project_id: str) -> int:
        # A missing project is not found and a stub (unloadable project) is
        # refused, even when there is nothing to delete.
        self.store.load_project(project_id)
        module_ids = self.store.list_module_ids(project_id)
        # Every referencing project to clean up is checked before anything is deleted.
        all_ids = {rid for mid in module_ids for rid in self.store.list_requirement_ids(project_id, mid)}
        self._check_incoming_cleanup(self._incoming_link_cleanup(project_id, all_ids))
        total = 0
        for module_id in module_ids:
            total += self._purge_module(project_id, module_id)
        return total

    # --- link configurations ("link modules") ----------------------------------

    @_records_history
    def create_link_configuration(
        self,
        project_id: str,
        name: str,
        source_module_id: str,
        target_module_id: str,
        target_project_id: Any = None,
    ) -> LinkConfiguration:
        """Create a link configuration from a module of the project to a module of it or of a referenced project.

        ``target_project_id`` (``None`` or the project itself: a module of
        this project) must be one of the projects this project references
        (its parent or ``references``) for a cross-project configuration.
        The configuration, its link pairs and its mirror attribute all live
        in this (the source) project; the target project is not written.
        """
        project = self.store.load_project(project_id)
        if target_project_id == project_id:
            target_project_id = None
        if target_project_id is not None:
            if not isinstance(target_project_id, str):
                raise ValueError("target_project_id must be a project id or null")
            if target_project_id not in self._effective_references(project):
                raise ValueError(
                    f"Project '{project_id}' does not reference project '{target_project_id}'; "
                    "add it to the project references first"
                )
            if not self.store.project_exists(target_project_id):
                raise FileNotFoundError(f"Project '{target_project_id}' not found")
            self.store.load_project(target_project_id)
        target_owner = target_project_id or project_id
        if not self.store.module_exists(project_id, source_module_id):
            raise FileNotFoundError(f"Module '{source_module_id}' not found in project '{project_id}'")
        if not self.store.module_exists(target_owner, target_module_id):
            raise FileNotFoundError(f"Module '{target_module_id}' not found in project '{target_owner}'")
        config = self._add_link_configuration(
            project,
            name,
            self.store.load_module(project_id, source_module_id),
            self.store.load_module(target_owner, target_module_id),
            target_project_id,
        )
        self.store.save_project(project)
        self.store.save_link_pairs(project_id, config.id, [])
        return config

    def _add_link_configuration(
        self,
        project: Project,
        name: Any,
        source: Module,
        target: Module,
        target_project_id: str | None = None,
    ) -> LinkConfiguration:
        """Add a link configuration and its system mirror attribute to ``project`` in memory.

        The caller passes the two (existing) modules and saves the project.
        The modules' types must form one of ``LINK_TYPE_PAIRS``
        (``_check_link_types``).
        """
        # The link's mirror attribute heads its own export column, so the link
        # name follows the attribute name rule.
        name = self._checked_attribute_name(project, name, kind="Link")
        self._check_link_types(source, target)
        source_module_id, target_module_id = source.id, target.id

        taken_ids = {c.id for c in project.link_configurations} | {a.id for a in project.attributes}
        config_id = _unique_slug(name, lambda c: c in taken_ids, fallback="link")

        config = LinkConfiguration(
            id=config_id,
            name=name,
            source_module_id=source_module_id,
            target_module_id=target_module_id,
            target_project_id=target_project_id,
        )
        project.link_configurations.append(config)
        project.attributes.append(
            AttributeDefinition(id=config_id, name=name, type="text", required=False, system=True)
        )
        return config

    @staticmethod
    def _check_link_types(source: Module, target: Module) -> None:
        """Refuse a link configuration between module types that do not trace to each other.

        Allowed (source → target): requirements → requirements, test cases →
        requirements, test execution → test cases (``LINK_TYPE_PAIRS``).
        Only new configurations are checked: one already stored (hand-edited
        data) keeps loading and working.
        """
        if (source.type, target.type) in LINK_TYPE_PAIRS:
            return
        allowed = ", ".join(f"{s} → {t}" for s, t in LINK_TYPE_PAIRS)
        raise ValueError(
            f"A link from module '{source.name}' ({source.type}) to module '{target.name}' ({target.type}) "
            f"is not allowed; link configurations join {allowed}"
        )

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
        self,
        project_id: str,
        config_id: str,
        module_id: str,
        requirement_id: str,
        config: LinkConfiguration | None = None,
    ) -> None:
        """Recompute one requirement's read-only mirror of a link configuration.

        ``"> target"`` per outgoing and ``"< source"`` per incoming link,
        ``"; "``-joined. A cross-project configuration's targets are
        project-qualified (``"> rbc-gp:SYRS-0001"``: requirement ids are only
        unique per project) and it has no incoming entries here: its
        targets live in the other project, which stores no mirror (see
        ``incoming_links``).
        """
        if config is None:
            config = next(
                (c for c in self.store.load_project(project_id).link_configurations if c.id == config_id), None
            )
        pairs = self.store.load_link_pairs(project_id, config_id)
        outgoing = [p.target_id for p in pairs if p.source_id == requirement_id]
        if config is not None and config.target_project_id is not None:
            entries = [f"> {config.target_project_id}:{t}" for t in outgoing]
        else:
            incoming = [p.source_id for p in pairs if p.target_id == requirement_id]
            entries = [f"> {t}" for t in outgoing] + [f"< {s}" for s in incoming]

        requirement = self.store.load_requirement(project_id, module_id, requirement_id)
        if entries:
            requirement.attributes[config_id] = "; ".join(entries)
        else:
            requirement.attributes.pop(config_id, None)
        self.store.save_requirement(project_id, module_id, requirement)

    @staticmethod
    def _plain_target_id(config: LinkConfiguration, target_id: str) -> str:
        """``target_id`` without a ``<project>:`` qualifier, which must name the configuration's target project.

        Clients may pass a cross-project target as shown in the mirror
        (``rbc-gp:SYRS-0001``) or plain; a configuration within one project
        takes plain ids only (a requirement id never contains ``:``).
        """
        if ":" not in target_id:
            return target_id
        owner, _, plain = target_id.partition(":")
        if config.target_project_id is None or owner != config.target_project_id:
            raise ValueError(
                f"Target '{target_id}' is not in the target project of link configuration '{config.name}'"
            )
        return plain

    @_records_history
    def link_requirements(
        self, project_id: str, config_id: str, source_id: str, target_id: str
    ) -> dict[str, Requirement]:
        """Link two requirements through a configuration of ``project_id`` (the source project).

        The source must be in the configuration's source module and the
        target in its target module, in the target project for a
        cross-project configuration (which must still be referenced). Only
        this project's files are written: the pair and the source's mirror
        (plus the target's mirror for a configuration within the project).
        """
        project = self.store.load_project(project_id)
        config = self._get_link_configuration(project, config_id)
        target_id = self._plain_target_id(config, target_id)
        target_project = self._load_link_target_project(project, config)

        source_module = self.store.find_requirement_module(project_id, source_id)
        if source_module is None:
            raise FileNotFoundError(f"Requirement '{source_id}' not found in project '{project_id}'")
        target_module = self.store.find_requirement_module(target_project, target_id)
        if target_module is None:
            raise FileNotFoundError(f"Requirement '{target_id}' not found in project '{target_project}'")

        if source_module != config.source_module_id or target_module != config.target_module_id:
            target_where = (
                f"'{config.target_module_id}'" if config.target_project_id is None
                else f"'{config.target_module_id}' of project '{config.target_project_id}'"
            )
            raise ValueError(
                f"Link configuration '{config.name}' requires source in module "
                f"'{config.source_module_id}' and target in module {target_where}"
            )

        pairs = self.store.load_link_pairs(project_id, config_id)
        if any(p.source_id == source_id and p.target_id == target_id for p in pairs):
            shown = target_id if config.target_project_id is None else f"{config.target_project_id}:{target_id}"
            raise ValueError(f"Link '{config.name}' from '{source_id}' to '{shown}' already exists")
        pairs.append(LinkPair(source_id=source_id, target_id=target_id))
        self.store.save_link_pairs(project_id, config_id, pairs)

        self._refresh_link_attribute(project_id, config_id, source_module, source_id, config)
        if config.target_project_id is None:
            self._refresh_link_attribute(project_id, config_id, target_module, target_id, config)

        return {
            "source": self.store.load_requirement(project_id, source_module, source_id),
            "target": self.store.load_requirement(target_project, target_module, target_id),
        }

    def link_options(
        self, project_id: str, from_id: str, to_id: str, from_project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """The links that may join two requirements, in either direction.

        Used by drag-and-drop linking: ``from_id`` is the dragged requirement
        (of ``from_project_id``, default ``project_id``), ``to_id`` the one it
        was dropped on (of ``project_id``). Every link configuration between
        the two requirements' modules is an option, oriented the configured
        way: a configuration from the dragged requirement's module to the drop
        target's module links ``from_id`` → ``to_id``; one in the opposite
        direction links ``to_id`` → ``from_id``. A configuration within one
        module (both requirements in it) is offered once, in drag direction.

        Across projects, the options are the cross-project configurations of
        either project that target the other one (and that it still
        references); ``project_id`` of an option is the project holding the
        configuration, where the link is created (``POST
        /projects/{project_id}/links``). ``exists`` marks a link that is
        already there (creating it again is rejected by
        ``link_requirements``). No configuration → empty list. Read-only;
        options are listed in configuration order, the drop target's
        project first.
        """
        if from_project_id is None:
            from_project_id = project_id
        if from_id == to_id and from_project_id == project_id:
            raise ValueError("A requirement cannot be linked to itself")
        project = self.store.load_project(project_id)
        from_project = project if from_project_id == project_id else self.store.load_project(from_project_id)
        from_module = self.store.find_requirement_module(from_project_id, from_id)
        if from_module is None:
            raise FileNotFoundError(f"Requirement '{from_id}' not found in project '{from_project_id}'")
        to_module = self.store.find_requirement_module(project_id, to_id)
        if to_module is None:
            raise FileNotFoundError(f"Requirement '{to_id}' not found in project '{project_id}'")

        def option(holder: Project, config: LinkConfiguration, source: tuple[str, str], target: tuple[str, str]) -> dict[str, Any]:
            pairs = self.store.load_link_pairs(holder.id, config.id)
            exists = any(p.source_id == source[1] and p.target_id == target[1] for p in pairs)
            return {
                "config": config,
                "project_id": holder.id,
                "source_id": source[1],
                "target_id": target[1],
                "source_project_id": source[0],
                "target_project_id": target[0],
                "source_module_id": config.source_module_id,
                "target_module_id": config.target_module_id,
                "exists": exists,
            }

        options: list[dict[str, Any]] = []
        if from_project_id == project_id:
            for config in self._local_configs(project):
                if config.source_module_id == from_module and config.target_module_id == to_module:
                    source, target = from_id, to_id
                elif config.source_module_id == to_module and config.target_module_id == from_module:
                    source, target = to_id, from_id
                else:
                    continue
                options.append(option(project, config, (project_id, source), (project_id, target)))
            return options

        # The drop target's project links to the dragged requirement's project ...
        if from_project_id in self._effective_references(project):
            for config in project.link_configurations:
                if (
                    config.target_project_id == from_project_id
                    and config.source_module_id == to_module
                    and config.target_module_id == from_module
                ):
                    options.append(option(project, config, (project_id, to_id), (from_project_id, from_id)))
        # ... or the dragged requirement's project links to the drop target's.
        if project_id in self._effective_references(from_project):
            for config in from_project.link_configurations:
                if (
                    config.target_project_id == project_id
                    and config.source_module_id == from_module
                    and config.target_module_id == to_module
                ):
                    options.append(option(from_project, config, (from_project_id, from_id), (project_id, to_id)))
        return options

    @_records_history
    def unlink_requirements(
        self, project_id: str, config_id: str, source_id: str, target_id: str
    ) -> dict[str, Requirement | None]:
        """Remove one link of a configuration of ``project_id`` (the source project).

        For a cross-project configuration the target may already be gone
        (or its project no longer readable): the pair is removed anyway and
        ``target`` is None in the result.
        """
        project = self.store.load_project(project_id)
        config = self._get_link_configuration(project, config_id)
        target_id = self._plain_target_id(config, target_id)

        source_module = self.store.find_requirement_module(project_id, source_id)
        if source_module is None:
            raise FileNotFoundError(f"Requirement '{source_id}' not found in project '{project_id}'")
        target_project = config.target_project_id or project_id
        target_module: str | None = None
        if config.target_project_id is None:
            target_module = self.store.find_requirement_module(project_id, target_id)
            if target_module is None:
                raise FileNotFoundError(f"Requirement '{target_id}' not found in project '{project_id}'")
        else:
            try:
                target_module = self.store.find_requirement_module(target_project, target_id)
            except (OSError, ValueError):
                target_module = None

        pairs = self.store.load_link_pairs(project_id, config_id)
        pairs = [p for p in pairs if not (p.source_id == source_id and p.target_id == target_id)]
        self.store.save_link_pairs(project_id, config_id, pairs)

        self._refresh_link_attribute(project_id, config_id, source_module, source_id, config)
        if config.target_project_id is None and target_module is not None:
            self._refresh_link_attribute(project_id, config_id, target_module, target_id, config)

        return {
            "source": self.store.load_requirement(project_id, source_module, source_id),
            "target": (
                self.store.load_requirement(target_project, target_module, target_id)
                if target_module is not None
                else None
            ),
        }

    def incoming_links(self, project_id: str, module_id: str | None = None) -> list[dict[str, Any]]:
        """Links into ``project_id`` from other projects (cross-project configurations), read-only.

        Cross-project links are stored only in the source (referencing)
        project, so the target project's requirement files carry no mirror
        of them; this computes them on read. Each entry: the source project
        (id, name), configuration (id, name), source module and requirement,
        and the target module and requirement of this project. With
        ``module_id``, only links into that module. Projects that cannot be
        loaded are skipped. Sorted by target id, then source project and
        source id.
        """
        self.store.load_project(project_id)
        if module_id is not None and not self.store.module_exists(project_id, module_id):
            raise FileNotFoundError(f"Module '{module_id}' not found in project '{project_id}'")
        result: list[dict[str, Any]] = []
        for other in self._other_projects(project_id):
            if project_id not in self._effective_references(other):
                continue
            for config in other.link_configurations:
                if config.target_project_id != project_id:
                    continue
                if module_id is not None and config.target_module_id != module_id:
                    continue
                for pair in self.store.load_link_pairs(other.id, config.id):
                    result.append(
                        {
                            "project_id": other.id,
                            "project_name": other.name,
                            "config_id": config.id,
                            "config_name": config.name,
                            "source_module_id": config.source_module_id,
                            "source_id": pair.source_id,
                            "target_module_id": config.target_module_id,
                            "target_id": pair.target_id,
                        }
                    )
        result.sort(key=lambda e: (e["target_id"], e["project_id"], e["source_id"], e["config_id"]))
        return result

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
            try:
                module_type = self._checked_module_type(tmodule.type)
            except ValueError as exc:
                raise ValueError(f"modules[{index}] ('{tmodule.name}'): {exc}") from exc
            modules.append(
                Module(
                    id=module_id,
                    name=tmodule.name,
                    prefix=tmodule.prefix,
                    description=tmodule.description,
                    type=module_type,
                )
            )
        # The template's declared module order (and folders), not the modules' id order.
        project.module_order = self._tree_from_template(template.module_tree, module_ids, [m.id for m in modules])
        # Attribute scopes name module keys (checked by ``parse_template``);
        # stored as module ids in module order.
        for index, tattr in enumerate(template.attributes):
            if tattr.modules or tattr.module_types:
                definition = next(a for a in project.attributes if a.id == tattr.id)
                try:
                    definition.module_types = self._checked_module_types(tattr.module_types)
                    self._check_required_scope(
                        definition.name, definition.required, [*tattr.modules, *definition.module_types]
                    )
                except ValueError as exc:
                    raise ValueError(f"attributes[{index}] ('{tattr.id}'): {exc}") from exc
                wanted = {module_ids[key] for key in tattr.modules}
                definition.modules = [m.id for m in modules if m.id in wanted]

        link_ids: dict[str, str] = {}
        modules_by_id = {m.id: m for m in modules}
        for index, tconfig in enumerate(template.link_configurations):
            try:
                config = self._add_link_configuration(
                    project,
                    tconfig.name,
                    modules_by_id[module_ids[tconfig.source_module]],
                    modules_by_id[module_ids[tconfig.target_module]],
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

        # Rule messages name the entry (``row_styles[<index>]``).
        project.row_styles = self._checked_row_styles(project, [s.to_dict() for s in template.row_styles])

        return project, modules

    @classmethod
    def _tree_from_template(cls, entries: list, module_ids: dict[str, str], order: list[str]) -> list:
        """A template's ``module_tree`` as a project's module tree; unplaced modules follow in ``order``.

        Folder ids are slugged from the names (unique in the project); names
        and depth follow the same rules as ``create_module_folder``.
        """
        taken: set[str] = set()

        def build(items: list, depth: int, where: str) -> list:
            result: list = []
            for index, entry in enumerate(items):
                if isinstance(entry, TemplateFolder):
                    at = f"{where}[{index}] ('{entry.name}')"
                    if depth > MAX_MODULE_FOLDER_DEPTH:
                        raise ValueError(
                            f"{at}: module folders can be nested at most {MAX_MODULE_FOLDER_DEPTH} levels deep"
                        )
                    try:
                        name = cls._checked_folder_name(entry.name, result)
                    except ValueError as exc:
                        raise ValueError(f"{at}: {exc}") from exc
                    folder_id = _unique_slug(name, lambda c: c in taken, fallback="folder")
                    taken.add(folder_id)
                    folder = ModuleFolder(id=folder_id, name=name)
                    result.append(folder)
                    folder.children = build(entry.children, depth + 1, f"{at}.children")
                else:
                    result.append(module_ids[entry])
            return result

        tree = build(entries, 1, "module_tree")
        placed = set(module_tree.module_ids(tree))
        tree.extend(module_id for module_id in order if module_id not in placed)
        return tree

    def _validate_template(self, template: Template) -> None:
        self._build_from_template(template, _TEMPLATE_CHECK_PROJECT_ID, template.name, "", None)

    @staticmethod
    def _template_summary(template: Template) -> dict[str, Any]:
        return {
            "id": template.id,
            "source": template.source,
            "name": template.name,
            "description": template.description,
            "modules": [
                {"key": m.key, "name": m.name, "prefix": m.prefix, "type": m.type or DEFAULT_MODULE_TYPE}
                for m in template.modules
            ],
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
        project: Project, modules: list[Module], template_id: str, name: str, description: str, tree: list | None = None
    ) -> Template:
        """A project's structure as a template; no requirements, no id counters, no parent."""
        builtin_ids = {a.id for a in BUILTIN_ATTRIBUTES}
        # Cross-project configurations are not saved (their target module is
        # not part of the template); views drop their mirror columns.
        local_configs = ProjectService._local_configs(project)
        link_ids = {c.id for c in local_configs}
        attributes = [
            TemplateAttribute(
                id=a.id,
                name=a.name,
                type=a.type,
                required=a.required,
                default=a.default,
                choices=list(a.choices) if isinstance(a.choices, list) else a.choices,
                # A required attribute applies to every module, whatever a
                # hand-edited file lists (see ``_attribute_applies``).
                modules=[] if a.required else [m.id for m in modules if m.id in a.modules],
                module_types=[] if a.required else [t for t in MODULE_TYPES if t in a.module_types],
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
                TemplateModule(key=m.id, name=m.name, prefix=m.prefix, description=m.description, type=m.type)
                for m in modules
            ],
            # Folders keep their ids as keys; written only when there is one.
            module_tree=ProjectService._template_tree(tree) if module_tree.has_folders(tree) else [],
            link_configurations=[
                TemplateLinkConfiguration(
                    key=c.id, name=c.name, source_module=c.source_module_id, target_module=c.target_module_id
                )
                for c in local_configs
            ],
            views=views,
            # Rules hand-edited data no longer resolves are not carried over.
            row_styles=[
                TemplateRowStyle(**style.to_dict()) for style in ProjectService._effective_row_styles(project)
            ],
        )

    @staticmethod
    def _template_tree(entries: list) -> list:
        """A project's (effective) module tree as template ``module_tree`` entries (module and folder ids as keys)."""
        return [
            TemplateFolder(key=e.id, name=e.name, children=ProjectService._template_tree(e.children))
            if isinstance(e, ModuleFolder)
            else e
            for e in entries
        ]

    def save_project_as_template(self, project_id: str, name: Any, description: Any = "") -> dict[str, Any]:
        """Write a project's structure as a new user template (``user:<slug>``).

        Saves non-system attributes (except the built-in ``object_text``),
        modules (key = module id), link configurations (between module keys),
        views (mirror attributes written as ``link:<key>``; references to
        attributes the project no longer has are dropped) and row styles
        (rules that no longer resolve are dropped). Requirements, id
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
            project, self.list_modules(project_id), template_id, name, description, self._module_tree(project)
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

    # --- reports ---------------------------------------------------------------
    #
    # Read-only views computed from the stored modules, requirements and link
    # pairs: the traceability matrix, the verification report and the test
    # execution report (see docs/ARCHITECTURE.md "Reports"). Nothing is
    # written and nothing is broadcast.

    @staticmethod
    def _report_key(text: str) -> str:
        """``text`` lower-cased with every run of non-alphanumerics as one space."""
        return " ".join(re.sub(r"[^0-9a-z]+", " ", str(text).lower()).split())

    @classmethod
    def _report_attribute(
        cls, project: Project, module: Module, role: str
    ) -> AttributeDefinition | None:
        """The attribute playing ``role`` (see ``REPORT_ATTRIBUTE_ROLES``) in ``module``.

        Only non-system attributes that apply to the module and have one of
        the role's types are considered. The first role name (in priority
        order) matching an attribute's name or id (both compared by
        ``_report_key``) wins; a role with ``contains`` words then takes the
        first attribute (project order) whose name contains one of them.
        """
        spec = REPORT_ATTRIBUTE_ROLES[role]
        candidates = [
            a for a in project.attributes
            if not a.system and a.type in spec["types"] and cls._attribute_applies(a, module.id, module.type)
        ]
        for wanted in spec["names"]:
            for attr in candidates:
                if wanted in (cls._report_key(attr.name), cls._report_key(attr.id)):
                    return attr
        for word in spec.get("contains", ()):
            for attr in candidates:
                if word in cls._report_key(attr.name).split():
                    return attr
        if spec.get("only_of_type"):
            # e.g. the one date attribute of an execution module, whatever its name
            if len(candidates) == 1:
                return candidates[0]
        return None

    @classmethod
    def _report_result(cls, value: Any) -> str:
        """A stored result value as ``passed | failed | blocked | not_run | not_applicable``."""
        key = cls._report_key(value) if isinstance(value, str) else ""
        for category, words in REPORT_RESULT_WORDS.items():
            if key in words:
                return category
        return "not_run"

    @staticmethod
    def _report_text(requirement: Requirement) -> str:
        value = requirement.attributes.get("object_text")
        if value is None:
            return ""
        text = richtext.to_plain(value) if isinstance(value, str) else str(value)
        return " ".join(str(text).split())

    def _report_items(self, project: Project, module: Module) -> tuple[list[dict[str, Any]], int]:
        """The module's reportable objects in tree order, and how many were left out.

        Objects whose Object Type (an enum found like any report attribute)
        is one of ``REPORT_EXCLUDED_OBJECT_TYPES`` (headings, information,
        definitions...) are not requirements or test cases and are left out
        of every report. Each item: ``id``, ``text`` (plain), ``level``
        (nesting depth) and the requirement itself under ``requirement``.
        """
        from .ordering import tree_order

        object_type = self._report_attribute(project, module, "object_type")
        requirements = self.store.load_all_requirements(project.id, module.id)
        by_id = {r.id: r for r in requirements}
        items: list[dict[str, Any]] = []
        excluded = 0
        for requirement in tree_order(requirements):
            if object_type is not None:
                kind = requirement.attributes.get(object_type.id)
                if isinstance(kind, str) and self._report_key(kind) in REPORT_EXCLUDED_OBJECT_TYPES:
                    excluded += 1
                    continue
            level, parent, seen = 0, requirement.parent_id, {requirement.id}
            while parent in by_id and parent not in seen:
                seen.add(parent)
                level += 1
                parent = by_id[parent].parent_id
            items.append({
                "id": requirement.id,
                "text": self._report_text(requirement),
                "level": level,
                "requirement": requirement,
            })
        return items, excluded

    @staticmethod
    def _report_module(module: Module) -> dict[str, Any]:
        return {"id": module.id, "name": module.name, "type": module.type}

    def _report_links(
        self, project: Project, from_id: str, to_id: str
    ) -> dict[str, set[str]]:
        """Requirement id in module ``from_id`` -> linked ids in module ``to_id``.

        Every link configuration joining the two modules counts, in either
        direction (a configuration from a module to itself counts once).
        """
        linked: dict[str, set[str]] = {}
        for config in self._local_configs(project):
            ends = (config.source_module_id, config.target_module_id)
            if ends == (from_id, to_id):
                forward = True
            elif ends == (to_id, from_id):
                forward = False
            else:
                continue
            for pair in self.store.load_link_pairs(project.id, config.id):
                a, b = (pair.source_id, pair.target_id) if forward else (pair.target_id, pair.source_id)
                linked.setdefault(a, set()).add(b)
        return linked

    def _report_linked_modules(self, project: Project, module: Module, module_type: str) -> list[Module]:
        """Modules of ``module_type`` joined to ``module`` by a link configuration (display order)."""
        joined: set[str] = set()
        for config in self._local_configs(project):
            if config.source_module_id == module.id:
                joined.add(config.target_module_id)
            if config.target_module_id == module.id:
                joined.add(config.source_module_id)
        return [
            m for m in self.list_modules(project.id)
            if m.id in joined and m.id != module.id and m.type == module_type
        ]

    def _execution_reader(self, project: Project, module: Module) -> dict[str, Any]:
        """How the execution records of ``module`` are read: its role attributes."""
        roles = {
            role: self._report_attribute(project, module, role)
            for role in ("result", "execution_date", "executed_by", "version", "evidence")
        }
        return {
            "module": module,
            "roles": roles,
            "attributes": {
                role: ({"id": attr.id, "name": attr.name} if attr else None) for role, attr in roles.items()
            },
        }

    def _execution_record(
        self, reader: dict[str, Any], item: dict[str, Any], module_index: int, item_index: int
    ) -> dict[str, Any]:
        """One execution record (an object of a test_execution module) as report data."""
        values = item["requirement"].attributes
        roles = reader["roles"]

        def value(role: str) -> Any:
            attr = roles[role]
            raw = values.get(attr.id) if attr else None
            return None if raw is None or raw == "" else raw

        raw_result = value("result")
        execution_date = value("execution_date")
        if not self._is_iso_date(execution_date):
            execution_date = None
        return {
            "id": item["id"],
            "module_id": reader["module"].id,
            "text": item["text"],
            "result": self._report_result(raw_result),
            "result_value": raw_result,
            "execution_date": execution_date,
            "executed_by": value("executed_by"),
            "version": value("version"),
            "evidence": value("evidence"),
            # sort key of "latest": date, then module order, then tree order
            "_key": (execution_date or "", module_index, item_index),
        }

    @staticmethod
    def _latest_execution(records: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The latest *executed* record (Passed, Failed or Blocked), or None.

        Latest = greatest Execution Date (a record without one sorts before
        every dated record), then the later module in the project's module
        order, then the later position in that module's tree order. Records
        still Not Run (or Not Applicable) are planned, not executed, and are
        never the latest execution.
        """
        executed = [r for r in records if r["result"] in REPORT_EXECUTED_RESULTS]
        return max(executed, key=lambda r: r["_key"]) if executed else None

    @staticmethod
    def _public_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
        if record is None:
            return None
        return {k: v for k, v in record.items() if not k.startswith("_")}

    @staticmethod
    def _percent(part: int, whole: int) -> float:
        return round(100.0 * part / whole, 1) if whole else 0.0

    def traceability_matrix(self, project_id: str, config_ids: Any) -> dict[str, Any]:
        """A traceability matrix over one link configuration or a chain of them.

        ``config_ids`` lists link configuration ids; each one after the first
        must share a module with the end of the chain so far (e.g. Satisfies
        StRS, Satisfies SyRS, Verifies SwRS, Executes SwTest gives StRS ->
        SyRS -> SwRS -> SwTest -> SwTestExec). The chain starts at the
        first configuration's *target* module (links point downstream ->
        upstream: a test case verifies a requirement), unless the second
        configuration joins that module, in which case it starts at the
        first configuration's source module.

        Returns the module sequence (each with its reportable items), one
        step per configuration (grid links row -> column, covered rows,
        uncovered rows, orphan columns), the trace of every first-module item
        through the whole chain, totals, and the unverified items: items of a
        ``requirements`` module of the chain that reach no item of a later
        ``test_cases`` module.
        """
        project = self.store.load_project(project_id)
        if not isinstance(config_ids, list) or not config_ids or not all(isinstance(c, str) for c in config_ids):
            raise ValueError("Pick at least one link configuration")
        if len(config_ids) > MAX_TRACE_CHAIN:
            raise ValueError(f"A traceability chain has at most {MAX_TRACE_CHAIN} link configurations")
        if len(set(config_ids)) != len(config_ids):
            raise ValueError("A link configuration appears twice in the chain")
        configs = [self._get_link_configuration(project, cid) for cid in config_ids]
        for config in configs:
            if config.target_project_id is not None:
                raise ValueError(
                    f"Link configuration '{config.name}' links to another project; cross-project "
                    "configurations cannot be part of a traceability matrix yet"
                )
        module_by_id = {m.id: m for m in self.list_modules(project_id)}

        first = configs[0]
        chain = [first.target_module_id, first.source_module_id]
        if len(configs) > 1:
            nxt = configs[1]
            if chain[1] not in (nxt.source_module_id, nxt.target_module_id) and chain[0] in (
                nxt.source_module_id, nxt.target_module_id
            ):
                chain.reverse()
        # (config, row side is the config's target?) per step
        orient: list[bool] = [first.target_module_id == chain[0]]
        for config in configs[1:]:
            end = chain[-1]
            if config.target_module_id == end:
                chain.append(config.source_module_id)
                orient.append(True)
            elif config.source_module_id == end:
                chain.append(config.target_module_id)
                orient.append(False)
            else:
                raise ValueError(
                    f"Link configuration '{config.name}' does not continue the chain: "
                    f"it does not join module '{module_by_id[end].name if end in module_by_id else end}'"
                )

        for module_id in chain:
            if module_id not in module_by_id:
                raise FileNotFoundError(f"Module '{module_id}' not found")
        modules = [module_by_id[mid] for mid in chain]

        items_of: list[list[dict[str, Any]]] = []
        excluded_of: list[int] = []
        for module in modules:
            items, excluded = self._report_items(project, module)
            items_of.append(items)
            excluded_of.append(excluded)
        ids_of = [[it["id"] for it in items] for items in items_of]
        id_sets = [set(ids) for ids in ids_of]

        steps: list[dict[str, Any]] = []
        forward: list[dict[str, set[str]]] = []
        for index, (config, row_is_target) in enumerate(zip(configs, orient)):
            rows, columns = ids_of[index], ids_of[index + 1]
            row_set, column_set = id_sets[index], id_sets[index + 1]
            links: dict[str, set[str]] = {}
            for pair in self.store.load_link_pairs(project_id, config.id):
                row, column = (pair.target_id, pair.source_id) if row_is_target else (pair.source_id, pair.target_id)
                if row in row_set and column in column_set:
                    links.setdefault(row, set()).add(column)
            forward.append(links)
            column_index = {cid: i for i, cid in enumerate(columns)}
            linked_columns = {c for targets in links.values() for c in targets}
            uncovered = [r for r in rows if not links.get(r)]
            steps.append({
                "config": config.to_dict(),
                "from_module_id": chain[index],
                "to_module_id": chain[index + 1],
                "links": [
                    [row, column]
                    for row in rows
                    for column in sorted(links.get(row, ()), key=lambda c: column_index[c])
                ],
                "total": len(rows),
                "covered": len(rows) - len(uncovered),
                "covered_percent": self._percent(len(rows) - len(uncovered), len(rows)),
                "uncovered": uncovered,
                "orphans": [c for c in columns if c not in linked_columns],
            })

        def reach(start: str, from_index: int) -> list[set[str]]:
            """Items reached from ``start`` (in module ``from_index``) in each later module."""
            reached: list[set[str]] = []
            frontier = {start}
            for links in forward[from_index:]:
                frontier = {t for s in frontier for t in links.get(s, ())}
                reached.append(frontier)
            return reached

        trace = []
        for item_id in ids_of[0]:
            reached = reach(item_id, 0)
            trace.append({
                "id": item_id,
                "reached": [[i for i in ids_of[k + 1] if i in ids] for k, ids in enumerate(reached)],
                "complete": bool(reached[-1]),
            })

        unverified = []
        for index, module in enumerate(modules):
            later_tests = [k for k in range(index + 1, len(modules)) if modules[k].type == "test_cases"]
            if module.type != "requirements" or not later_tests:
                continue
            test_index = later_tests[0]
            missing = [
                item_id for item_id in ids_of[index]
                if not reach(item_id, index)[test_index - index - 1]
            ]
            unverified.append({
                "module_id": module.id,
                "test_module_id": modules[test_index].id,
                "total": len(ids_of[index]),
                "ids": missing,
            })

        traced = sum(1 for row in trace if row["complete"])
        return {
            "project_id": project_id,
            "modules": [
                {
                    **self._report_module(module),
                    "items": [{k: it[k] for k in ("id", "text", "level")} for it in items_of[i]],
                    "excluded": excluded_of[i],
                }
                for i, module in enumerate(modules)
            ],
            "steps": steps,
            "trace": trace,
            "summary": {
                "total": len(ids_of[0]),
                "covered": steps[0]["covered"],
                "covered_percent": steps[0]["covered_percent"],
                "traced": traced,
                "traced_percent": self._percent(traced, len(ids_of[0])),
            },
            "unverified": unverified,
        }

    def _test_case_results(
        self, project: Project, test_module: Module, exec_modules: list[Module]
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        """Execution records per test case id of ``test_module``, and the readers used."""
        records: dict[str, list[dict[str, Any]]] = {}
        readers = []
        order = {m.id: i for i, m in enumerate(self.list_modules(project.id))}
        for exec_module in exec_modules:
            reader = self._execution_reader(project, exec_module)
            readers.append(reader)
            links = self._report_links(project, exec_module.id, test_module.id)
            items, _ = self._report_items(project, exec_module)
            for position, item in enumerate(items):
                targets = links.get(item["id"])
                if not targets:
                    continue
                record = self._execution_record(reader, item, order.get(exec_module.id, 0), position)
                for test_id in targets:
                    records.setdefault(test_id, []).append(record)
        return records, readers

    def verification_report(self, project_id: str, module_id: str) -> dict[str, Any]:
        """Verification status of every requirement of a ``requirements`` module.

        Covering test cases: objects of ``test_cases`` modules linked to the
        requirement directly (any link configuration between the modules,
        either direction). A test case's result is its latest execution
        (``_latest_execution``) among the objects of ``test_execution``
        modules linked to it; a test case without any execution record falls
        back to its own result attribute (e.g. RBC "Test Result"), if it has
        one and it says Passed, Failed or Blocked.

        Status: ``not_covered`` (no covering test case), ``failed`` (a
        covering test case's result is Failed), ``not_run`` (otherwise, a
        covering test case is Blocked or has no execution), ``verified``
        (every covering test case Passed).
        """
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        if module.type != "requirements":
            raise ValueError(
                f"Module '{module.name}' is a {module.type} module; "
                "the verification report is for a requirements module"
            )
        items, excluded = self._report_items(project, module)
        test_modules = self._report_linked_modules(project, module, "test_cases")

        covering: dict[str, list[dict[str, Any]]] = {}
        exec_modules_seen: dict[str, dict[str, Any]] = {}
        for module_position, test_module in enumerate(test_modules):
            links = self._report_links(project, module.id, test_module.id)
            exec_modules =self._report_linked_modules(project, test_module, "test_execution")
            records, readers = self._test_case_results(project, test_module, exec_modules)
            for reader in readers:
                exec_modules_seen.setdefault(reader["module"].id, {
                    **self._report_module(reader["module"]),
                    "test_module_id": test_module.id,
                    "attributes": reader["attributes"],
                })
            own_result = self._report_attribute(project, test_module, "result")
            tests, _ = self._report_items(project, test_module)
            test_by_id = {t["id"]: (position, t) for position, t in enumerate(tests)}
            for requirement_id, test_ids in links.items():
                for test_id in test_ids:
                    if test_id not in test_by_id:
                        continue
                    position, test = test_by_id[test_id]
                    test_records = records.get(test_id, [])
                    latest = self._latest_execution(test_records)
                    source = "execution" if latest else None
                    result = latest["result"] if latest else "not_run"
                    if not test_records and own_result is not None:
                        own = self._report_result(test["requirement"].attributes.get(own_result.id))
                        if own in REPORT_EXECUTED_RESULTS:
                            result, source = own, "test_case"
                    covering.setdefault(requirement_id, []).append({
                        "id": test_id,
                        "module_id": test_module.id,
                        "text": test["text"],
                        "result": result,
                        "source": source,
                        "executions": len(test_records),
                        "latest": self._public_record(latest),
                        "_order": (module_position, position),
                    })

        rows = []
        totals = {status: 0 for status in VERIFICATION_STATUSES}
        for item in items:
            tests = sorted(covering.get(item["id"], []), key=lambda t: t["_order"])
            results = {t["result"] for t in tests}
            if not tests:
                status = "not_covered"
            elif "failed" in results:
                status = "failed"
            elif results == {"passed"}:
                status = "verified"
            else:
                status = "not_run"
            totals[status] += 1
            rows.append({
                "id": item["id"],
                "text": item["text"],
                "level": item["level"],
                "status": status,
                "tests": [{k: v for k, v in t.items() if not k.startswith("_")} for t in tests],
            })
        total = len(rows)
        return {
            "project_id": project_id,
            "module": self._report_module(module),
            "test_modules": [self._report_module(m) for m in test_modules],
            "execution_modules": list(exec_modules_seen.values()),
            "requirements": rows,
            "totals": {"total": total, **totals, "excluded": excluded},
            "percent": {status: self._percent(totals[status], total) for status in VERIFICATION_STATUSES},
        }

    def execution_report(self, project_id: str, module_id: str) -> dict[str, Any]:
        """The test execution report of a ``test_execution`` module (one campaign).

        Planned test cases: the objects of the ``test_cases`` modules linked
        to this module. Each one's result in this campaign is its latest
        execution among the module's records linked to it (not_planned when
        no record links to it, not_run when its records are all still Not
        Run). Also: record counts per result, the failed and blocked test
        cases with the requirements they verify (objects of ``requirements``
        modules linked to the test case), records linked to no test case, and
        the distinct builds/versions under test, executors and the execution
        date range of the executed records.
        """
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        if module.type != "test_execution":
            raise ValueError(
                f"Module '{module.name}' is a {module.type} module; "
                "the test execution report is for a test_execution module"
            )
        reader = self._execution_reader(project, module)
        items, excluded = self._report_items(project, module)
        test_modules = self._report_linked_modules(project, module, "test_cases")
        links_by_test_module = {tm.id: self._report_links(project, module.id, tm.id) for tm in test_modules}

        records = []
        by_test: dict[str, list[dict[str, Any]]] = {}
        for position, item in enumerate(items):
            record = self._execution_record(reader, item, 0, position)
            test_ids = []
            for test_module in test_modules:
                test_ids.extend(sorted(links_by_test_module[test_module.id].get(item["id"], ())))
            record["test_case_ids"] = test_ids
            records.append(record)
            for test_id in test_ids:
                by_test.setdefault(test_id, []).append(record)

        record_counts = {r: 0 for r in REPORT_RESULTS}
        for record in records:
            record_counts[record["result"]] += 1

        test_cases = []
        test_counts = {r: 0 for r in EXECUTION_TEST_STATUSES}
        problems = []
        for test_module in test_modules:
            tests, _ = self._report_items(project, test_module)
            requirement_modules = self._report_linked_modules(project, test_module, "requirements")
            req_links = [
                (rm, self._report_links(project, test_module.id, rm.id), {
                    it["id"]: it["text"] for it in self._report_items(project, rm)[0]
                })
                for rm in requirement_modules
            ]
            for test in tests:
                test_records = by_test.get(test["id"], [])
                latest = self._latest_execution(test_records)
                if latest:
                    status = latest["result"]
                elif test_records:
                    status = "not_run"
                else:
                    status = "not_planned"
                test_counts[status] += 1
                entry = {
                    "id": test["id"],
                    "module_id": test_module.id,
                    "text": test["text"],
                    "status": status,
                    "records": [r["id"] for r in test_records],
                    "latest": self._public_record(latest),
                }
                test_cases.append(entry)
                if status in ("failed", "blocked"):
                    affected = []
                    for rm, links, texts in req_links:
                        affected.extend(
                            {"id": rid, "module_id": rm.id, "text": texts[rid]}
                            for rid in sorted(links.get(test["id"], ()))
                            if rid in texts
                        )
                    problems.append({**entry, "requirements": affected})

        executed_records = [r for r in records if r["result"] in REPORT_EXECUTED_RESULTS]
        dates = sorted(r["execution_date"] for r in executed_records if r["execution_date"])

        def distinct(key: str) -> list[str]:
            return sorted({str(r[key]) for r in records if r[key] is not None})

        planned = len(test_cases) - test_counts["not_planned"]
        executed = sum(test_counts[s] for s in REPORT_EXECUTED_RESULTS)
        return {
            "project_id": project_id,
            "module": self._report_module(module),
            "attributes": reader["attributes"],
            "test_modules": [self._report_module(m) for m in test_modules],
            "records": [self._public_record(r) for r in records],
            "record_counts": {"total": len(records), **record_counts, "excluded": excluded},
            "test_cases": test_cases,
            "test_counts": {
                "total": len(test_cases),
                "planned": planned,
                "executed": executed,
                "executed_percent": self._percent(executed, planned),
                "pass_percent": self._percent(test_counts["passed"], executed),
                **test_counts,
            },
            "problems": problems,
            "unlinked_records": [r["id"] for r in records if not r["test_case_ids"]],
            "versions": distinct("version"),
            "executors": distinct("executed_by"),
            "date_range": [dates[0], dates[-1]] if dates else None,
        }

    def export_traceability(self, project_id: str, config_ids: Any, fmt: str) -> bytes | str:
        from .report_export import traceability_to_csv, traceability_to_xlsx

        report = self.traceability_matrix(project_id, config_ids)
        project = self.store.load_project(project_id)
        if fmt == "csv":
            return traceability_to_csv(report)
        if fmt == "xlsx":
            return traceability_to_xlsx(report, project.name)
        raise ValueError(f"Unknown traceability export format '{fmt}'; expected csv or xlsx")

    def export_verification_report(self, project_id: str, module_id: str, fmt: str) -> bytes:
        from .report_export import verification_to_docx, verification_to_xlsx

        if fmt not in ("docx", "xlsx"):
            raise ValueError(f"Unknown report export format '{fmt}'; expected docx or xlsx")
        report = self.verification_report(project_id, module_id)
        project = self.store.load_project(project_id)
        writer = verification_to_docx if fmt == "docx" else verification_to_xlsx
        return writer(report, project.name)

    def export_execution_report(self, project_id: str, module_id: str, fmt: str) -> bytes:
        from .report_export import execution_to_docx, execution_to_xlsx

        if fmt not in ("docx", "xlsx"):
            raise ValueError(f"Unknown report export format '{fmt}'; expected docx or xlsx")
        report = self.execution_report(project_id, module_id)
        project = self.store.load_project(project_id)
        writer = execution_to_docx if fmt == "docx" else execution_to_xlsx
        return writer(report, project.name)

    def verification_statuses(self, project_id: str, module_id: str) -> dict[str, Any]:
        """The computed, read-only "Verification status" of every requirement of a requirements module.

        The same status as ``verification_report`` (``verified | failed |
        not_run | not_covered``) per requirement id, for the table column and
        the detail panel. Objects the reports leave out (headings...) have
        none. Never stored, never exported, not an attribute definition: it
        cannot be written, imported or put in a view's stored attributes.
        """
        report = self.verification_report(project_id, module_id)
        return {
            "project_id": project_id,
            "module_id": module_id,
            "statuses": {row["id"]: row["status"] for row in report["requirements"]},
        }

    # --- test result import (JUnit XML, Robot Framework output.xml) ---------------
    #
    # Results of automated test runs become execution records (requirements)
    # of a test_execution module, each linked to the test case it executes.
    # See docs/ARCHITECTURE.md "Test result import".

    def _result_import_links(
        self, project: Project, module: Module
    ) -> list[tuple[LinkConfiguration, Module, bool]]:
        """The link configurations joining ``module`` to a test_cases module.

        Each as ``(configuration, test cases module, records are the link
        source)``; new configurations always go test_execution -> test_cases,
        but a stored one the other way round works too.
        """
        modules = {m.id: m for m in self.list_modules(project.id)}
        joined: list[tuple[LinkConfiguration, Module, bool]] = []
        for config in self._local_configs(project):
            if config.source_module_id == module.id and config.target_module_id != module.id:
                other, record_is_source = config.target_module_id, True
            elif config.target_module_id == module.id and config.source_module_id != module.id:
                other, record_is_source = config.source_module_id, False
            else:
                continue
            test_module = modules.get(other)
            if test_module is not None and test_module.type == "test_cases":
                joined.append((config, test_module, record_is_source))
        return joined

    @staticmethod
    def _checked_result_options(options: Any) -> dict[str, Any]:
        """The import options with blanks as None; ``execution_date`` must be a date."""
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise ValueError("Import options must be an object")
        checked: dict[str, Any] = {}
        for key in RESULT_IMPORT_OPTIONS:
            value = options.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Option '{key}' must be a string")
            checked[key] = value.strip() if isinstance(value, str) and value.strip() else None
        if checked["execution_date"] is not None and not ProjectService._is_iso_date(checked["execution_date"]):
            raise ValueError(f"Execution date '{checked['execution_date']}' is not a date (expected YYYY-MM-DD)")
        name = checked["file_name"] or "results.xml"
        checked["file_name"] = re.split(r"[\\/]", name)[-1][:200] or "results.xml"
        return checked

    @classmethod
    def _result_value(cls, attr: AttributeDefinition, category: str) -> str | None:
        """The value of result attribute ``attr`` meaning ``category``, or None when it has none."""
        if attr.type == "enum":
            for choice in attr.choices or []:
                if isinstance(choice, str) and cls._report_result(choice) == category:
                    return choice
            return None
        return RESULT_TEXT_VALUES.get(category)

    def _test_results_plan(
        self, project_id: str, module_id: str, content: Any, options: Any
    ) -> dict[str, Any]:
        """Parse a result file and match it to test cases, writing nothing.

        Shared by ``preview_test_results`` and ``import_test_results``: the
        import writes exactly what the preview showed, or nothing when the
        plan has ``errors``.
        """
        from .test_results_import import MAX_TEST_RESULTS_IMPORT_BYTES, parse_test_results

        if not isinstance(content, (str, bytes)):
            raise ValueError("The result file content must be a string")
        if len(content) > MAX_TEST_RESULTS_IMPORT_BYTES:
            raise ValueError(
                f"The result file is too large: the limit is {MAX_TEST_RESULTS_IMPORT_BYTES // (1024 * 1024)} MiB"
            )
        opts = self._checked_result_options(options)
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        if module.type != "test_execution":
            raise ValueError(
                f"Module '{module.name}' is a {module.type} module; "
                "test results are imported into a test_execution module"
            )

        joined = self._result_import_links(project, module)
        if not joined:
            raise ValueError(
                f"Module '{module.name}' is not linked to a test cases module: "
                "add a link configuration from it to a test_cases module first"
            )
        if opts["config_id"] is None:
            config, test_module, record_is_source = joined[0]
        else:
            chosen = next((j for j in joined if j[0].id == opts["config_id"]), None)
            if chosen is None:
                self._get_link_configuration(project, opts["config_id"])  # 404 when unknown
                raise ValueError(
                    f"Link configuration '{opts['config_id']}' does not join module '{module.name}' "
                    "to a test cases module"
                )
            config, test_module, record_is_source = chosen

        match_options = [{"id": RESULT_MATCH_ID, "name": "Requirement id"}] + [
            {"id": a.id, "name": a.name}
            for a in project.attributes
            if not a.system and a.type in RESULT_MATCH_TYPES
            and self._attribute_applies(a, test_module.id, test_module.type)
        ]
        default_match = self._report_attribute(project, test_module, "test_id")
        match = opts["match_attribute"] or (default_match.id if default_match else RESULT_MATCH_ID)
        if match not in {o["id"] for o in match_options}:
            raise ValueError(
                f"Cannot match on '{match}': pick the requirement id or a string, integer or enum "
                f"attribute of module '{test_module.name}'"
            )

        file_format, results = parse_test_results(content)

        # Test cases by their match key (lower-cased, punctuation collapsed).
        tests, _ = self._report_items(project, test_module)
        by_key: dict[str, list[dict[str, Any]]] = {}
        for test in tests:
            raw = test["id"] if match == RESULT_MATCH_ID else test["requirement"].attributes.get(match)
            if raw is None or isinstance(raw, bool):
                continue
            key = self._report_key(str(raw))
            if key:
                by_key.setdefault(key, []).append(test)

        def lookup(keys: list[str], partial: bool) -> dict[str, dict[str, Any]]:
            found: dict[str, dict[str, Any]] = {}
            for text in keys:
                tokens = self._report_key(text).split()
                if not partial:
                    spans = [" ".join(tokens)]
                else:
                    # every run of whole words: "SWT-0001 Login" contains "swt 0001"
                    spans = [
                        " ".join(tokens[i:j]) for i in range(len(tokens)) for j in range(i + 1, len(tokens) + 1)
                    ]
                for span in spans:
                    for test in by_key.get(span, ()):
                        found.setdefault(test["id"], test)
            return found

        reader = self._execution_reader(project, module)
        roles: dict[str, AttributeDefinition | None] = reader["roles"]
        errors: list[str] = []
        warnings: list[str] = []
        result_attr = roles["result"]
        if result_attr is None:
            errors.append(
                f"Module '{module.name}' has no result attribute (e.g. 'Result') to store the test results in"
            )
        for role, label, value in (
            ("executed_by", "Executed by", opts["executed_by"]),
            ("version", "Build/version", opts["version"]),
        ):
            if value is not None and roles[role] is None:
                warnings.append(f"{label} is not stored: module '{module.name}' has no such attribute")
        if roles["execution_date"] is None:
            warnings.append(f"Execution dates are not stored: module '{module.name}' has no date attribute")
        if roles["evidence"] is None:
            warnings.append(f"Evidence is not stored: module '{module.name}' has no evidence/log attribute")

        today = date.today().isoformat()
        text_ids = {a.id for a in project.attributes if a.type == "text"}
        rows: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        seen_errors: set[str] = set()
        matched_tests: set[str] = set()
        for result in results:
            keys = result.keys()
            found = lookup(keys, partial=False)
            how = "exact" if found else None
            if not found:
                found = lookup(keys, partial=True)
                how = "partial" if found else None
            state = "matched" if len(found) == 1 else ("ambiguous" if found else "unmatched")
            test = next(iter(found.values())) if state == "matched" else None
            execution_date = opts["execution_date"] or result.date or today
            row = {
                "index": result.index,
                "name": result.name,
                "full_name": result.full_name,
                "status": result.status,
                "raw_status": result.raw_status,
                "message": result.message,
                "execution_date": execution_date,
                "state": state,
                "match": how if state == "matched" else None,
                "test_case_id": test["id"] if test else None,
                "candidates": sorted(found) if state == "ambiguous" else [],
            }
            rows.append(row)
            if test is None or result_attr is None:
                continue
            matched_tests.add(test["id"])

            result_value = self._result_value(result_attr, result.status)
            if result_value is None:
                problem = (
                    f"Result attribute '{result_attr.name}' has no value for {result.status.replace('_', ' ')} "
                    f"results (choices: {', '.join(map(str, result_attr.choices or []))})"
                )
                if problem not in seen_errors:
                    seen_errors.add(problem)
                    errors.append(problem)
                continue
            values: dict[str, Any] = {
                "object_text": result.name or result.full_name or test["id"],
                result_attr.id: result_value,
            }
            if roles["execution_date"] is not None:
                values[roles["execution_date"].id] = execution_date
            if roles["executed_by"] is not None and opts["executed_by"] is not None:
                values[roles["executed_by"].id] = opts["executed_by"]
            if roles["version"] is not None and opts["version"] is not None:
                values[roles["version"].id] = opts["version"]
            if roles["evidence"] is not None:
                values[roles["evidence"].id] = f"{opts['file_name']}: {result.full_name or result.name}"
            # Result files are plain text: never read a value as rich text markup.
            values = {k: richtext.from_plain(v) if k in text_ids and isinstance(v, str) else v for k, v in values.items()}
            for problem in self._attribute_problems(
                project, values, is_create=True, module_id=module.id, module_type=module.type
            ):
                if problem not in seen_errors:
                    seen_errors.add(problem)
                    errors.append(problem)
            records.append({"row": row, "values": values, "test_id": test["id"]})

        counts = {state: sum(1 for r in rows if r["state"] == state) for state in ("matched", "unmatched", "ambiguous")}
        if not errors and counts["matched"] == 0:
            errors.append(
                f"No test result matches a test case of module '{test_module.name}' by "
                f"{next(o['name'] for o in match_options if o['id'] == match)}"
            )
        if counts["ambiguous"]:
            warnings.append(
                f"{counts['ambiguous']} result(s) match several test cases and are skipped; "
                "pick a more specific match attribute"
            )
        if counts["unmatched"]:
            warnings.append(f"{counts['unmatched']} result(s) match no test case and are skipped")
        if len(errors) > MAX_RESULT_IMPORT_ERRORS:
            hidden = len(errors) - MAX_RESULT_IMPORT_ERRORS
            errors = errors[:MAX_RESULT_IMPORT_ERRORS] + [f"... and {hidden} more problem(s)"]

        return {
            "project": project,
            "module": module,
            "config": config,
            "test_module": test_module,
            "record_is_source": record_is_source,
            "records": records,
            "public": {
                "project_id": project_id,
                "format": file_format,
                "file_name": opts["file_name"],
                "module": self._report_module(module),
                "test_module": self._report_module(test_module),
                "config": config.to_dict(),
                "link_configurations": [
                    {"id": c.id, "name": c.name, "test_module": self._report_module(tm)} for c, tm, _ in joined
                ],
                "match_attribute": match,
                "match_options": match_options,
                "options": {k: opts[k] for k in ("executed_by", "version", "execution_date")},
                "attributes": reader["attributes"],
                "results": rows,
                "summary": {
                    "total": len(rows),
                    **counts,
                    **{s: sum(1 for r in rows if r["status"] == s) for s in ("passed", "failed", "not_run")},
                },
                "test_cases_not_in_file": [
                    {"id": t["id"], "text": t["text"]} for t in tests if t["id"] not in matched_tests
                ],
                "errors": errors,
                "warnings": warnings,
            },
        }

    def preview_test_results(
        self, project_id: str, module_id: str, content: Any, options: Any = None
    ) -> dict[str, Any]:
        """What ``import_test_results`` would do with a result file; writes nothing.

        Lists every result with its match (``matched`` to one test case,
        ``ambiguous``, ``unmatched``), the test cases the file does not
        cover, the link configuration and match attribute used (and the
        alternatives), and the ``errors`` that would refuse the import.
        """
        return self._test_results_plan(project_id, module_id, content, options)["public"]

    @_records_history
    def import_test_results(
        self, project_id: str, module_id: str, content: Any, options: Any = None
    ) -> dict[str, Any]:
        """Create one execution record per matched test result, linked to its test case.

        ``content`` is a JUnit XML or Robot Framework output.xml file
        (``test_results_import.py``). Options (all optional): ``config_id``
        (the link configuration to a test_cases module; default the first),
        ``match_attribute`` (``@id`` or a test case attribute id; default the
        test case's "Test ID"-like attribute, else ``@id``), ``executed_by``,
        ``version``, ``execution_date`` (``YYYY-MM-DD``; default the date in
        the file, else today) and ``file_name`` (for Evidence/Log Ref).

        A result matches a test case whose key equals its name, qualified name,
        a Robot tag or a JUnit property value, else whose key is a run of whole
        words of one of those (``SWT-0001 Login`` matches ``SWT-0001``).
        Results matching no or several test cases are skipped (reported in the
        preview). All-or-nothing: any validation problem refuses the whole
        file before anything is written. Records get server-allocated ids and
        are appended at the module root in file order.
        """
        plan = self._test_results_plan(project_id, module_id, content, options)
        public = plan["public"]
        if public["errors"]:
            raise ValueError("; ".join(public["errors"]))
        project: Project = plan["project"]
        module: Module = plan["module"]
        config: LinkConfiguration = plan["config"]
        test_module: Module = plan["test_module"]
        record_is_source: bool = plan["record_is_source"]

        existing = self.store.load_all_requirements(project_id, module.id)
        order = self._next_sibling_order(existing, None)
        created: list[Requirement] = []
        new_pairs: list[LinkPair] = []
        for record in plan["records"]:
            requirement_id = self._allocate_requirement_id(project, module)
            attributes = self._merged_attributes(project, record["values"], module.id, module.type)
            test_id = record["test_id"]
            attributes[config.id] = f"> {test_id}" if record_is_source else f"< {test_id}"
            created.append(Requirement(id=requirement_id, attributes=attributes, parent_id=None, order=order))
            order += 1
            new_pairs.append(
                LinkPair(source_id=requirement_id, target_id=test_id)
                if record_is_source
                else LinkPair(source_id=test_id, target_id=requirement_id)
            )

        self._write_new_requirements(project, module.id, created)
        pairs = self.store.load_link_pairs(project_id, config.id) + new_pairs
        self.store.save_link_pairs(project_id, config.id, pairs)
        # Refresh the linked test cases' mirror attribute (like ``_refresh_link_attribute``,
        # but from the pairs in memory: one file read for any number of test cases).
        linked_tests = set(r["test_id"] for r in plan["records"])
        outgoing: dict[str, list[str]] = {}
        incoming: dict[str, list[str]] = {}
        for pair in pairs:
            if pair.source_id in linked_tests:
                outgoing.setdefault(pair.source_id, []).append(pair.target_id)
            if pair.target_id in linked_tests:
                incoming.setdefault(pair.target_id, []).append(pair.source_id)
        for test_id in sorted(linked_tests):
            entries = [f"> {t}" for t in outgoing.get(test_id, [])] + [f"< {s}" for s in incoming.get(test_id, [])]
            test_case = self.store.load_requirement(project_id, test_module.id, test_id)
            test_case.attributes[config.id] = "; ".join(entries)
            self.store.save_requirement(project_id, test_module.id, test_case)

        return {
            "imported": len(created),
            "requirement_ids": [r.id for r in created],
            "linked": len(new_pairs),
            "skipped": public["summary"]["unmatched"] + public["summary"]["ambiguous"],
            "format": public["format"],
            "config_id": config.id,
            "test_module_id": test_module.id,
            "warnings": public["warnings"],
        }

    # --- export ----------------------------------------------------------------

    def _export_modules(
        self, project: Project, keep_rich: bool = False
    ) -> list[tuple[Module, list[Requirement]]]:
        """Each module (display order) with its requirements, as the exports see them.

        A requirement shows only the values of attributes that apply to its
        module (a value left over from hand-edited data is not exported).

        Rich text values of ``text`` attributes are exported as their
        plain-text projection (``richtext.to_plain``) by the table exports
        (CSV, XLSX). The formatted exports (DOCX, ReqIF) pass ``keep_rich``
        and get rich values as stored, except one with no visible text,
        which is projected like before (so it reads as empty, not as markup).
        A rich-looking value of any other attribute type is plain text and
        is never touched.
        """
        text_ids = {a.id for a in project.attributes if a.type == "text"}
        result: list[tuple[Module, list[Requirement]]] = []
        for module in self.list_modules(project.id):
            requirements = []
            for requirement in self.store.load_all_requirements(project.id, module.id):
                kept, dropped = self._applicable_values(project, module, requirement.attributes)
                rich = [
                    k for k in text_ids
                    if richtext.is_rich(kept.get(k)) and not (keep_rich and richtext.blocks(kept[k]))
                ]
                if rich:
                    kept = {k: richtext.to_plain(v) if k in rich else v for k, v in kept.items()}
                requirements.append(replace(requirement, attributes=kept) if dropped or rich else requirement)
            result.append((module, requirements))
        return result

    def export_reqif(self, project_id: str) -> str:
        from .reqif_export import export_project_to_reqif

        project = self.store.load_project(project_id)
        modules = self._export_modules(project, keep_rich=True)
        # Cross-project links become no SPEC-RELATION: their targets are not
        # in this file. Their mirror attribute (project-qualified targets,
        # "> rbc-gp:SYRS-0001") is exported like every attribute.
        local_configs = self._local_configs(project)
        link_pairs = {config.id: self.store.load_link_pairs(project_id, config.id) for config in local_configs}
        return export_project_to_reqif(project, modules, local_configs, link_pairs)

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
        modules = self._export_modules(project, keep_rich=True)
        return export_project_to_docx(project, modules, self._effective_row_styles(project))

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
                project, values, is_create=True, module_id=module_id, module_type=module.type
            ),
            applies=lambda attr: self._attribute_applies(attr, module_id, module.type),
        )

        existing = self.store.load_all_requirements(project_id, module_id)
        next_order: dict[str | None, int] = {None: self._next_sibling_order(existing, None)}

        text_ids = {a.id for a in project.attributes if a.type == "text"}
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
            # CSV cells are plain text: one that looks like rich text is kept
            # as the literal text (``richtext.from_plain``), never as markup.
            values = {
                k: richtext.from_plain(v) if k in text_ids and isinstance(v, str) else v
                for k, v in row.attributes.items()
            }
            attributes = self._merged_attributes(project, values, module_id, module.type)
            created.append(Requirement(id=requirement_id, attributes=attributes, parent_id=parent_id, order=order))

        self._write_new_requirements(project, module_id, created)

        return {
            "imported": len(created),
            "requirement_ids": [new_ids[row.row_number] for row in sorted(parsed.rows, key=lambda r: r.row_number)],
            "ignored_columns": parsed.ignored_columns,
        }
