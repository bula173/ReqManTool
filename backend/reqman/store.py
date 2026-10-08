"""Disk I/O for a data directory: read/write/list/delete the JSON files
that back projects, modules, requirements, and link-configuration pairs.

``ProjectStore`` knows the on-disk layout (see docs/ARCHITECTURE.md) and
nothing else — no business rules, no cross-entity validation. It raises
``FileNotFoundError`` for a missing resource and otherwise trusts its
caller (``ProjectService``) to have already validated the request.

Every write goes through ``_write_json``, which writes a temporary file in
the target's directory and then ``os.replace``s it over the target, so a
crash or I/O error never leaves a half-written JSON file behind.

``project.json`` starts with the project's ``format_version`` (see
``formats.py``): ``load_project`` upgrades older data in memory without
rewriting the file, and ``save_project`` writes the current version. Every
read or write of a project's files first checks the version stored in its
``project.json`` (``_check_project_format``), so the files of a project
written by a newer ReqManTool are never misread or overwritten. A
``project.json`` that cannot be read as a JSON object is reported as a
``ValueError`` by both, never treated as version 1. The check fails exactly
when ``load_project`` would, so a project that cannot be loaded (listed as
a stub) is fully read-only.

Every data file is read as ``utf-8-sig`` (a leading UTF-8 byte order mark,
as some editors save files, is ignored) and written as plain UTF-8 without
one.

Inside ``recording_writes`` the store notes which projects' files were
written or deleted, so ``ProjectService`` knows which project repositories
to auto-commit after a mutation (see ``git_repo.py``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .formats import PROJECT_FORMAT, stored_format_version, upgrade, with_format_version
from .models import LinkPair, Module, Project, Requirement

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

PREFIX_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

# Directories in the data-directory root that are never projects. Project ids
# are slugs (lowercase letters, digits and '-'), so they can never be equal
# to these names; ``list_project_ids``/``project_exists`` also skip them
# explicitly, so a hand-made ``project.json`` inside them is ignored.
TEMPLATES_DIR_NAME = "_templates"
# User module templates: ``<root>/_templates/modules/``.
MODULE_TEMPLATES_DIR_NAME = "modules"
# User AI prompts: ``<root>/_templates/prompts/``.
PROMPTS_DIR_NAME = "prompts"
STAGING_DIR_NAME = ".reqman-staging"
RESERVED_ROOT_NAMES = frozenset({TEMPLATES_DIR_NAME, STAGING_DIR_NAME})

# A project id as ``ProjectService`` creates it (a slug of the project name). Never starts with a dot.
PROJECT_SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# The file name length limit assumed where the file system does not report one.
DEFAULT_NAME_MAX = 255


def validate_prefix(value: str) -> None:
    if not PREFIX_PATTERN.match(value):
        raise ValueError(
            f"Invalid prefix '{value}': must start with a letter and contain only letters, "
            "digits, '_' and '-'"
        )


def _sync(fd: int) -> None:
    """Flush ``fd``'s data to stable storage.

    Where ``fcntl.F_FULLFSYNC`` exists (macOS), ``os.fsync`` only reaches the
    drive's cache, so ``F_FULLFSYNC`` is used instead; if the filesystem does
    not support it (``OSError``), this falls back to ``os.fsync``. Everywhere
    else it is ``os.fsync``.
    """
    full_fsync = getattr(fcntl, "F_FULLFSYNC", None) if fcntl is not None else None
    if full_fsync is not None:
        try:
            fcntl.fcntl(fd, full_fsync)
            return
        except OSError:
            pass
    os.fsync(fd)


def _read_json(path: Path) -> Any:
    """Parse a data file. ``utf-8-sig``: an optional leading UTF-8 BOM is ignored.

    Raises ``ValueError`` (``UnicodeDecodeError``/``JSONDecodeError``) for a
    file that is not valid UTF-8 JSON.
    """
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, data: Any) -> None:
    """Write ``data`` as two-space-indented JSON with a trailing newline, atomically.

    The temporary file lives next to ``path`` (same filesystem, so
    ``os.replace`` is atomic) and its name does not end in ``.json``, so it is
    never listed as a requirement. It is removed if anything fails.

    The temporary file is flushed and synced (``_sync``) before the replace,
    so the target is never swapped for a file whose contents are not yet on
    disk. The directory itself is not ``fsync``ed: after a power loss right after
    the replace, the target may still hold its previous (complete) contents.
    """
    text = json.dumps(data, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            _sync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class ProjectStore:
    """Reads and writes projects/modules/requirements as JSON files on disk."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # project id -> (mtime_ns, ctime_ns, size, inode) of a project.json whose version was checked
        self._format_cache: dict[str, tuple[int, int, int, int]] = {}
        # Per thread: the ids of projects whose files were written while recording (see ``recording_writes``).
        self._recording = threading.local()

    # --- write recording -------------------------------------------------------

    @contextmanager
    def recording_writes(self) -> Iterator[set[str]]:
        """Collect the ids of projects whose files are written or deleted inside the block.

        ``ProjectService`` wraps each mutation in this, so it knows which
        project repositories to auto-commit afterwards. Nested blocks share
        the outer block's set. Recording is per thread.
        """
        current = getattr(self._recording, "touched", None)
        if current is not None:
            yield current
            return
        touched: set[str] = set()
        self._recording.touched = touched
        try:
            yield touched
        finally:
            self._recording.touched = None

    def is_recording(self) -> bool:
        return getattr(self._recording, "touched", None) is not None

    def _touch(self, project_id: str) -> None:
        touched = getattr(self._recording, "touched", None)
        if touched is not None:
            touched.add(project_id)

    # --- format version guard ----------------------------------------------

    def _check_project_format(self, project_id: str) -> None:
        """Raise unless ``project_id``'s project.json loads (``load_project`` would succeed).

        Called before every read or write of a project's files, so a project
        listed as a stub is fully read-only: none of its files are read or
        written. A missing project.json (a project being created) has nothing
        to protect. Otherwise this raises the same error as ``load_project``:
        ``NewerFormatError`` for a project written by a newer ReqManTool, and
        ``ValueError`` for a project.json that cannot be read, is not valid
        UTF-8 JSON, not a JSON object, has an invalid ``format_version`` or
        the wrong shape. A successful check is cached per project until the
        file's mtime, ctime, size or inode changes (ctime also catches a
        same-size in-place edit that keeps or restores the mtime).
        """
        if not self._is_project_id(project_id):
            return
        path = self._project_file(project_id)
        try:
            stat = path.stat()
        except PermissionError as exc:  # e.g. a project folder this process cannot search
            raise self._unreadable_error(project_id, exc) from exc
        except OSError:
            return  # absent, as for ``_project_file_present``
        key = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
        if self._format_cache.get(project_id) == key:
            return
        try:
            data = self._read_project_data(project_id)
        except FileNotFoundError:
            return  # removed since the stat: nothing to protect
        self._project_from_data(project_id, data)
        # Only a loadable project.json is cached; any other one raises every time.
        self._format_cache[project_id] = key

    # --- paths -----------------------------------------------------------

    def _project_dir(self, project_id: str) -> Path:
        return self.root / project_id

    def project_path(self, project_id: str) -> Path:
        """The folder of an existing project (its git work tree). ``FileNotFoundError`` otherwise."""
        if not self.project_exists(project_id):
            raise FileNotFoundError(f"Project '{project_id}' not found")
        return self._project_dir(project_id)

    @staticmethod
    def requirement_relative_path(module_id: str, requirement_id: str) -> str:
        """A requirement file's path inside its project folder, with ``/`` separators (as git names it)."""
        return f"modules/{module_id}/requirements/{requirement_id}.json"

    def _project_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def _modules_dir(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "modules"

    def _module_dir(self, project_id: str, module_id: str) -> Path:
        return self._modules_dir(project_id) / module_id

    def _module_file(self, project_id: str, module_id: str) -> Path:
        return self._module_dir(project_id, module_id) / "module.json"

    def _requirements_dir(self, project_id: str, module_id: str) -> Path:
        return self._module_dir(project_id, module_id) / "requirements"

    def _requirement_file(self, project_id: str, module_id: str, requirement_id: str) -> Path:
        return self._requirements_dir(project_id, module_id) / f"{requirement_id}.json"

    def _links_dir(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "links"

    def _link_pairs_file(self, project_id: str, config_id: str) -> Path:
        return self._links_dir(project_id) / f"{config_id}.json"

    # --- projects ----------------------------------------------------------

    @staticmethod
    def _is_project_id(project_id: object) -> bool:
        """True if ``project_id`` can name a project: one plain path component, not reserved.

        Rejects anything that would make ``root / project_id`` point somewhere
        other than a direct child of the data directory (``/`` or ``\\``
        separators, ``.``/``..``, absolute paths, drives, NUL), so an id
        coming from a request is never joined into a path outside the root.
        """
        return (
            isinstance(project_id, str)
            and project_id not in ("", ".", "..")
            and project_id not in RESERVED_ROOT_NAMES
            and "/" not in project_id
            and "\\" not in project_id
            and "\0" not in project_id
            and not os.path.isabs(project_id)
            and not os.path.splitdrive(project_id)[0]
        )

    def project_exists(self, project_id: str) -> bool:
        if not self._is_project_id(project_id):
            return False
        return self._project_file_present(project_id)

    def max_name_length(self) -> int:
        """The longest file or folder name (in bytes) the data directory's file system allows."""
        try:
            limit = os.pathconf(self.root, "PC_NAME_MAX")
        except (AttributeError, OSError, ValueError):  # no pathconf (Windows) or no answer
            limit = -1
        return limit if limit > 0 else DEFAULT_NAME_MAX

    def project_path_exists(self, project_id: str) -> bool:
        """True if anything (a project or any other file/directory) occupies the project's path."""
        path = self._project_dir(project_id)
        return project_id in RESERVED_ROOT_NAMES or path.exists() or path.is_symlink()

    def save_project(self, project: Project) -> None:
        """Write project.json at the current ``format_version`` (its first key)."""
        self._check_project_format(project.id)
        self._touch(project.id)
        self._project_dir(project.id).mkdir(parents=True, exist_ok=True)
        _write_json(self._project_file(project.id), with_format_version(project.to_dict(), PROJECT_FORMAT))

    def read_project_text(self, project_id: str) -> str:
        """The raw text of project.json (``utf-8-sig``), with no checks; used for error reporting."""
        if not self._is_project_id(project_id):
            raise FileNotFoundError(f"Project '{project_id}' not found")
        return self._project_file(project_id).read_text(encoding="utf-8-sig")

    def _read_project_data(self, project_id: str) -> dict:
        """project.json as a JSON object (``utf-8-sig``), before any version check.

        Raises ``ValueError`` naming the project for a file that is not valid
        UTF-8, not valid JSON (including JSON nested too deeply to parse) or
        not a JSON object, and for one that cannot be read (no permission, a
        directory: any ``OSError`` but a missing file). ``FileNotFoundError``
        (the project was removed) is passed on.
        """
        what = f"Project '{project_id}' has an invalid project.json"
        try:
            data = _read_json(self._project_file(project_id))
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise self._unreadable_error(project_id, exc) from exc
        except UnicodeDecodeError as exc:
            raise ValueError(f"{what}: not valid UTF-8 ({exc})") from exc
        except ValueError as exc:
            raise ValueError(f"{what}: not valid JSON ({exc})") from exc
        except RecursionError as exc:
            raise ValueError(f"{what}: not valid JSON (nested too deeply)") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{what}: expected a JSON object, got {type(data).__name__}")
        return data

    def load_project(self, project_id: str) -> Project:
        """Read project.json, upgrading older format versions in memory (the file is not rewritten).

        Raises ``NewerFormatError`` for a project written by a newer
        ReqManTool, and ``ValueError`` for a project.json that is not valid
        UTF-8 JSON, not a JSON object, has an invalid ``format_version``, lacks
        a required key (such as ``id`` or ``name``) or holds a value of the
        wrong shape.
        """
        if not self._is_project_id(project_id) or not self._project_file_present(project_id):
            raise FileNotFoundError(f"Project '{project_id}' not found")
        return self._project_from_data(project_id, self._read_project_data(project_id))

    def stored_project_format_version(self, project_id: str) -> int:
        """The ``format_version`` recorded in project.json (before any in-memory upgrade)."""
        self._check_project_format(project_id)
        return stored_format_version(self._read_project_data(project_id), f"Project '{project_id}'")

    def _project_file_present(self, project_id: str) -> bool:
        """True if project.json exists, or may exist but cannot be reached.

        A ``PermissionError`` (a project folder this process cannot search)
        counts as present, so the project is reported as unreadable rather
        than hidden. Any other ``OSError`` (missing, ``ENOTDIR``, a name too
        long for the file system, ...) means absent.
        """
        try:
            os.stat(self._project_file(project_id))
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _unreadable_error(project_id: str, exc: OSError) -> ValueError:
        """A ``ValueError`` for an unreadable project.json, naming the project, not its absolute path."""
        reason = exc.strerror or type(exc).__name__
        return ValueError(f"Project '{project_id}' has an unreadable project.json: {reason}")

    @staticmethod
    def _project_from_data(project_id: str, raw: dict) -> Project:
        """``raw`` (a project.json object) upgraded and validated as a ``Project``.

        Raises ``NewerFormatError``/``ValueError`` exactly as ``load_project`` does.
        """
        data = upgrade(raw, PROJECT_FORMAT, f"Project '{project_id}'")
        try:
            return Project.from_dict(data)
        except KeyError as exc:
            raise ValueError(
                f"Project '{project_id}' has an invalid project.json: missing key {exc}"
            ) from exc
        except (TypeError, AttributeError) as exc:
            raise ValueError(f"Project '{project_id}' has an invalid project.json: {exc}") from exc

    def list_project_ids(self) -> list[str]:
        """Ids of the data directory's folders holding a project.json.

        A folder this process cannot search is listed too (as a stub, see
        ``_project_file_present``), but only if its name is a project slug, so
        system folders such as ``lost+found`` or ``.Spotlight-V100`` are not.
        """
        if not self.root.exists():
            return []
        ids = []
        for path in self.root.iterdir():
            if not (self._is_project_id(path.name) and path.is_dir()):
                continue
            try:
                os.stat(self._project_file(path.name))
            except PermissionError:
                if not PROJECT_SLUG_PATTERN.fullmatch(path.name):
                    continue
            except OSError:
                continue
            ids.append(path.name)
        return sorted(ids)

    @contextmanager
    def staging_store(self) -> Iterator["ProjectStore"]:
        """Yield a store rooted in a fresh staging directory inside the data directory.

        A multi-file write (a project created from a template) goes there
        first and is moved into place by ``publish_staged_project`` in one
        ``os.rename``, so a failure part-way never leaves a partial project
        in the data directory. The staging directory is
        ``<root>/.reqman-staging/<random>/``: its projects sit one level too
        deep to be listed, and it is removed on exit whether or not the
        project was published.
        """
        staging_parent = self.root / STAGING_DIR_NAME
        staging_root = staging_parent / uuid.uuid4().hex
        try:
            yield ProjectStore(staging_root)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
            try:
                staging_parent.rmdir()
            except OSError:
                pass  # not empty (another staging in progress) or already gone

    def publish_staged_project(self, staging: "ProjectStore", project_id: str) -> None:
        """Move a project written into ``staging`` to its final place, atomically.

        Raises ``FileExistsError`` if the target path is taken; nothing is
        overwritten.
        """
        target = self._project_dir(project_id)
        if self.project_path_exists(project_id):
            raise FileExistsError(f"Cannot create project '{project_id}': '{target}' already exists")
        os.rename(staging._project_dir(project_id), target)

    def delete_project(self, project_id: str) -> str | None:
        """Delete a project's folder and everything in it (including its own ``.git``).

        Never touches anything outside the folder: the folder must be a real
        directory (no symlink or junction) directly inside the data
        directory, else ``ValueError`` and nothing is deleted. It is first
        moved into the staging directory in one rename, so the project
        disappears from the data directory at once (never half-deleted),
        then removed. Returns ``None``, or the path of what was left behind
        in the staging directory when that removal failed part-way (the
        project is gone from the data directory either way).
        """
        if not self.project_exists(project_id):
            raise FileNotFoundError(f"Project '{project_id}' not found")
        folder = self._project_dir(project_id)
        is_junction = getattr(os.path, "isjunction", None)
        if folder.is_symlink() or (is_junction is not None and is_junction(folder)) or not folder.is_dir():
            raise ValueError(
                f"Project '{project_id}' is not a plain folder in the data directory (a link?); "
                "remove it by hand"
            )
        if os.path.realpath(folder.parent) != os.path.realpath(self.root):
            raise ValueError(f"Project '{project_id}' is not inside the data directory; remove it by hand")
        staging_parent = self.root / STAGING_DIR_NAME
        staging_parent.mkdir(exist_ok=True)
        trash = staging_parent / f"removed-{uuid.uuid4().hex}"
        os.rename(folder, trash)
        self._format_cache.pop(project_id, None)

        def make_writable(func: Any, path: str, _exc: Any) -> None:
            # Git object files are read-only; on Windows they cannot be deleted as they are.
            os.chmod(path, 0o700)
            func(path)

        leftover: str | None = None
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(trash, onexc=make_writable)
            else:  # pragma: no cover - older Pythons
                shutil.rmtree(trash, onerror=make_writable)
        except OSError:
            leftover = str(trash)
        try:
            staging_parent.rmdir()
        except OSError:
            pass  # not empty (a leftover or another staging in progress) or already gone
        return leftover

    # --- modules -------------------------------------------------------------

    def module_exists(self, project_id: str, module_id: str) -> bool:
        return self._module_file(project_id, module_id).exists()

    def save_module(self, project_id: str, module: Module) -> None:
        self._check_project_format(project_id)
        self._touch(project_id)
        self._requirements_dir(project_id, module.id).mkdir(parents=True, exist_ok=True)
        _write_json(self._module_file(project_id, module.id), module.to_dict())

    def load_module(self, project_id: str, module_id: str) -> Module:
        self._check_project_format(project_id)
        path = self._module_file(project_id, module_id)
        if not path.exists():
            raise FileNotFoundError(f"Module '{module_id}' not found in project '{project_id}'")
        return Module.from_dict(_read_json(path))

    def list_module_ids(self, project_id: str) -> list[str]:
        self._check_project_format(project_id)
        modules_dir = self._modules_dir(project_id)
        if not modules_dir.exists():
            return []
        return sorted(
            p.name for p in modules_dir.iterdir() if p.is_dir() and (p / "module.json").exists()
        )

    # --- requirements ----------------------------------------------------------

    def requirement_exists(self, project_id: str, module_id: str, requirement_id: str) -> bool:
        return self._requirement_file(project_id, module_id, requirement_id).exists()

    def save_requirement(self, project_id: str, module_id: str, requirement: Requirement) -> None:
        self._check_project_format(project_id)
        self._touch(project_id)
        path = self._requirement_file(project_id, module_id, requirement.id)
        _write_json(path, requirement.to_dict())

    def load_requirement(self, project_id: str, module_id: str, requirement_id: str) -> Requirement:
        self._check_project_format(project_id)
        path = self._requirement_file(project_id, module_id, requirement_id)
        if not path.exists():
            raise FileNotFoundError(
                f"Requirement '{requirement_id}' not found in module '{module_id}'"
            )
        return Requirement.from_dict(_read_json(path))

    def delete_requirement(self, project_id: str, module_id: str, requirement_id: str) -> None:
        self._check_project_format(project_id)
        path = self._requirement_file(project_id, module_id, requirement_id)
        if not path.exists():
            raise FileNotFoundError(
                f"Requirement '{requirement_id}' not found in module '{module_id}'"
            )
        self._touch(project_id)
        path.unlink()

    def list_requirement_ids(self, project_id: str, module_id: str) -> list[str]:
        self._check_project_format(project_id)
        req_dir = self._requirements_dir(project_id, module_id)
        if not req_dir.exists():
            return []
        # Hidden files (macOS AppleDouble ``._X.json``, ...) are no requirements.
        return sorted(p.stem for p in req_dir.glob("*.json") if not p.name.startswith("."))

    def load_all_requirements(self, project_id: str, module_id: str) -> list[Requirement]:
        return [
            self.load_requirement(project_id, module_id, rid)
            for rid in self.list_requirement_ids(project_id, module_id)
        ]

    def find_requirement_module(self, project_id: str, requirement_id: str) -> str | None:
        """Scan all modules in a project for the module owning a requirement id."""
        for module_id in self.list_module_ids(project_id):
            if self.requirement_exists(project_id, module_id, requirement_id):
                return module_id
        return None

    # --- user templates ----------------------------------------------------

    def _templates_dir(self) -> Path:
        return self.root / TEMPLATES_DIR_NAME

    def _user_template_file(self, template_id: str) -> Path:
        return self._templates_dir() / f"{template_id}.json"

    def list_user_template_ids(self) -> list[str]:
        """File stems of ``<root>/_templates/*.json`` (hidden/temporary files excluded)."""
        templates_dir = self._templates_dir()
        if not templates_dir.is_dir():
            return []
        return sorted(
            p.stem for p in templates_dir.glob("*.json") if p.is_file() and not p.name.startswith(".")
        )

    def user_template_path_exists(self, template_id: str) -> bool:
        """True if ``_templates/<template_id>.json`` is taken. ``template_id`` must be a slug."""
        path = self._user_template_file(template_id)
        return path.exists() or path.is_symlink()

    def read_user_template_text(self, template_id: str) -> str:
        # Looked up among the listed files rather than by joining a raw id
        # into a path, so an id can never point outside the templates folder.
        if template_id not in self.list_user_template_ids():
            raise FileNotFoundError(f"Template 'user:{template_id}' not found")
        # utf-8-sig: a file saved by an editor with a UTF-8 BOM is accepted.
        return self._user_template_file(template_id).read_text(encoding="utf-8-sig")

    def save_user_template(self, template_id: str, data: dict) -> None:
        """Write a new user template file; the caller has already chosen a free id."""
        self._templates_dir().mkdir(parents=True, exist_ok=True)
        _write_json(self._user_template_file(template_id), data)

    # --- user module templates ----------------------------------------------
    # ``<root>/_templates/modules/<stem>.json``: a subfolder, so the project
    # template listing (``_templates/*.json``) never sees them.

    def _module_templates_dir(self) -> Path:
        return self._templates_dir() / MODULE_TEMPLATES_DIR_NAME

    def list_user_module_template_ids(self) -> list[str]:
        """File stems of ``<root>/_templates/modules/*.json`` (hidden/temporary files excluded)."""
        modules_dir = self._module_templates_dir()
        if not modules_dir.is_dir():
            return []
        return sorted(p.stem for p in modules_dir.glob("*.json") if p.is_file() and not p.name.startswith("."))

    def user_module_template_path_exists(self, template_id: str) -> bool:
        """True if ``_templates/modules/<template_id>.json`` is taken. ``template_id`` must be a slug."""
        path = self._module_templates_dir() / f"{template_id}.json"
        return path.exists() or path.is_symlink()

    def read_user_module_template_text(self, template_id: str) -> str:
        # Looked up among the listed files, never by joining a raw id into a path.
        if template_id not in self.list_user_module_template_ids():
            raise FileNotFoundError(f"Module template 'user:{template_id}' not found")
        return (self._module_templates_dir() / f"{template_id}.json").read_text(encoding="utf-8-sig")

    def save_user_module_template(self, template_id: str, data: dict) -> None:
        """Write a new user module template file; the caller has already chosen a free id."""
        self._module_templates_dir().mkdir(parents=True, exist_ok=True)
        _write_json(self._module_templates_dir() / f"{template_id}.json", data)

    # --- user AI prompts ---------------------------------------------------
    # ``<root>/_templates/prompts/<stem>.json``: a subfolder of ``_templates``
    # (never a project), so the project template listing never sees them.

    def _prompts_dir(self) -> Path:
        return self._templates_dir() / PROMPTS_DIR_NAME

    def list_user_prompt_ids(self) -> list[str]:
        """File stems of ``<root>/_templates/prompts/*.json`` (hidden/temporary files excluded)."""
        prompts_dir = self._prompts_dir()
        if not prompts_dir.is_dir():
            return []
        return sorted(p.stem for p in prompts_dir.glob("*.json") if p.is_file() and not p.name.startswith("."))

    def user_prompt_path_exists(self, prompt_id: str) -> bool:
        """True if ``_templates/prompts/<prompt_id>.json`` is taken. ``prompt_id`` must be a slug."""
        path = self._prompts_dir() / f"{prompt_id}.json"
        return path.exists() or path.is_symlink()

    def read_user_prompt_text(self, prompt_id: str) -> str:
        # Looked up among the listed files, never by joining a raw id into a path.
        if prompt_id not in self.list_user_prompt_ids():
            raise FileNotFoundError(f"Prompt 'user:{prompt_id}' not found")
        return (self._prompts_dir() / f"{prompt_id}.json").read_text(encoding="utf-8-sig")

    def save_user_prompt(self, prompt_id: str, data: dict) -> None:
        """Write (create or replace) a user prompt file; the caller has checked the id."""
        self._prompts_dir().mkdir(parents=True, exist_ok=True)
        _write_json(self._prompts_dir() / f"{prompt_id}.json", data)

    def delete_user_prompt(self, prompt_id: str) -> None:
        if prompt_id not in self.list_user_prompt_ids():
            raise FileNotFoundError(f"Prompt 'user:{prompt_id}' not found")
        (self._prompts_dir() / f"{prompt_id}.json").unlink()

    # --- link pairs ("link module") ---------------------------------------

    def load_link_pairs(self, project_id: str, config_id: str) -> list[LinkPair]:
        self._check_project_format(project_id)
        path = self._link_pairs_file(project_id, config_id)
        if not path.exists():
            return []
        data = _read_json(path)
        return [LinkPair.from_dict(p) for p in data.get("pairs", [])]

    def save_link_pairs(self, project_id: str, config_id: str, pairs: list[LinkPair]) -> None:
        self._check_project_format(project_id)
        self._touch(project_id)
        path = self._link_pairs_file(project_id, config_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, {"pairs": [p.to_dict() for p in pairs]})

    def delete_link_pairs(self, project_id: str, config_id: str) -> None:
        self._check_project_format(project_id)
        path = self._link_pairs_file(project_id, config_id)
        if path.exists():
            self._touch(project_id)
            path.unlink()
