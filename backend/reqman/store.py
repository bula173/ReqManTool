"""Disk I/O for a data directory: read/write/list/delete the JSON files
that back projects, modules, requirements, and link-configuration pairs.

``ProjectStore`` knows the on-disk layout (see docs/ARCHITECTURE.md) and
nothing else — no business rules, no cross-entity validation. It raises
``FileNotFoundError`` for a missing resource and otherwise trusts its
caller (``ProjectService``) to have already validated the request.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import LinkPair, Module, Project, Requirement

PREFIX_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def validate_prefix(value: str) -> None:
    if not PREFIX_PATTERN.match(value):
        raise ValueError(
            f"Invalid prefix '{value}': must start with a letter and contain only letters, "
            "digits, '_' and '-'"
        )


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

    def project_exists(self, project_id: str) -> bool:
        return self._project_file(project_id).exists()

    def save_project(self, project: Project) -> None:
        self._project_dir(project.id).mkdir(parents=True, exist_ok=True)
        self._project_file(project.id).write_text(json.dumps(project.to_dict(), indent=2) + "\n")

    def load_project(self, project_id: str) -> Project:
        path = self._project_file(project_id)
        if not path.exists():
            raise FileNotFoundError(f"Project '{project_id}' not found")
        return Project.from_dict(json.loads(path.read_text()))

    def list_project_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            p.name for p in self.root.iterdir() if p.is_dir() and (p / "project.json").exists()
        )

    # --- modules -------------------------------------------------------------

    def module_exists(self, project_id: str, module_id: str) -> bool:
        return self._module_file(project_id, module_id).exists()

    def save_module(self, project_id: str, module: Module) -> None:
        self._requirements_dir(project_id, module.id).mkdir(parents=True, exist_ok=True)
        self._module_file(project_id, module.id).write_text(json.dumps(module.to_dict(), indent=2) + "\n")

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
        path.write_text(json.dumps(requirement.to_dict(), indent=2) + "\n")

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
        path.write_text(json.dumps({"pairs": [p.to_dict() for p in pairs]}, indent=2) + "\n")

    def delete_link_pairs(self, project_id: str, config_id: str) -> None:
        path = self._link_pairs_file(project_id, config_id)
        if path.exists():
            path.unlink()
