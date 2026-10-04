"""Disk I/O for a data directory: read/write/list/delete the JSON files
that back projects, modules, requirements, and link-configuration pairs.

``ProjectStore`` knows the on-disk layout (see docs/ARCHITECTURE.md) and
nothing else — no business rules, no cross-entity validation. It raises
``FileNotFoundError`` for a missing resource and otherwise trusts its
caller (``ProjectService``) to have already validated the request.

Every write goes through ``_write_json``, which writes a temporary file in
the target's directory and then ``os.replace``s it over the target, so a
crash or I/O error never leaves a half-written JSON file behind.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

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
STAGING_DIR_NAME = ".reqman-staging"
RESERVED_ROOT_NAMES = frozenset({TEMPLATES_DIR_NAME, STAGING_DIR_NAME})


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

    # --- paths -----------------------------------------------------------

    def _project_dir(self, project_id: str) -> Path:
        return self.root / project_id

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
        return self._project_file(project_id).exists()

    def project_path_exists(self, project_id: str) -> bool:
        """True if anything (a project or any other file/directory) occupies the project's path."""
        path = self._project_dir(project_id)
        return project_id in RESERVED_ROOT_NAMES or path.exists() or path.is_symlink()

    def save_project(self, project: Project) -> None:
        self._project_dir(project.id).mkdir(parents=True, exist_ok=True)
        _write_json(self._project_file(project.id), project.to_dict())

    def load_project(self, project_id: str) -> Project:
        if not self._is_project_id(project_id) or not self._project_file(project_id).exists():
            raise FileNotFoundError(f"Project '{project_id}' not found")
        return Project.from_dict(json.loads(self._project_file(project_id).read_text()))

    def list_project_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            p.name
            for p in self.root.iterdir()
            if self._is_project_id(p.name) and p.is_dir() and (p / "project.json").exists()
        )

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

    # --- modules -------------------------------------------------------------

    def module_exists(self, project_id: str, module_id: str) -> bool:
        return self._module_file(project_id, module_id).exists()

    def save_module(self, project_id: str, module: Module) -> None:
        self._requirements_dir(project_id, module.id).mkdir(parents=True, exist_ok=True)
        _write_json(self._module_file(project_id, module.id), module.to_dict())

    def load_module(self, project_id: str, module_id: str) -> Module:
        path = self._module_file(project_id, module_id)
        if not path.exists():
            raise FileNotFoundError(f"Module '{module_id}' not found in project '{project_id}'")
        return Module.from_dict(json.loads(path.read_text()))

    def list_module_ids(self, project_id: str) -> list[str]:
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
        path = self._requirement_file(project_id, module_id, requirement.id)
        _write_json(path, requirement.to_dict())

    def load_requirement(self, project_id: str, module_id: str, requirement_id: str) -> Requirement:
        path = self._requirement_file(project_id, module_id, requirement_id)
        if not path.exists():
            raise FileNotFoundError(
                f"Requirement '{requirement_id}' not found in module '{module_id}'"
            )
        return Requirement.from_dict(json.loads(path.read_text()))

    def delete_requirement(self, project_id: str, module_id: str, requirement_id: str) -> None:
        path = self._requirement_file(project_id, module_id, requirement_id)
        if not path.exists():
            raise FileNotFoundError(
                f"Requirement '{requirement_id}' not found in module '{module_id}'"
            )
        path.unlink()

    def list_requirement_ids(self, project_id: str, module_id: str) -> list[str]:
        req_dir = self._requirements_dir(project_id, module_id)
        if not req_dir.exists():
            return []
        return sorted(p.stem for p in req_dir.glob("*.json"))

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
        return self._user_template_file(template_id).read_text(encoding="utf-8")

    def save_user_template(self, template_id: str, data: dict) -> None:
        """Write a new user template file; the caller has already chosen a free id."""
        self._templates_dir().mkdir(parents=True, exist_ok=True)
        _write_json(self._user_template_file(template_id), data)

    # --- link pairs ("link module") ---------------------------------------

    def load_link_pairs(self, project_id: str, config_id: str) -> list[LinkPair]:
        path = self._link_pairs_file(project_id, config_id)
        if not path.exists():
            return []
        data = json.loads(path.read_text())
        return [LinkPair.from_dict(p) for p in data.get("pairs", [])]

    def save_link_pairs(self, project_id: str, config_id: str, pairs: list[LinkPair]) -> None:
        path = self._link_pairs_file(project_id, config_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, {"pairs": [p.to_dict() for p in pairs]})

    def delete_link_pairs(self, project_id: str, config_id: str) -> None:
        path = self._link_pairs_file(project_id, config_id)
        if path.exists():
            path.unlink()
