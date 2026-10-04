"""Business logic for ReqManTool, shared by both the REST API and the
WebSocket JSON-RPC endpoint (see ``app.py``) so the two transports can
never behave differently for the same operation.

``ProjectService`` is where every cross-cutting rule lives: id generation
and allocation, attribute validation (including rejecting direct writes to
system-managed attributes), requirement nesting/cycle checks, directional
link-configuration enforcement, and the cleanup cascades that keep links
and mirrored attributes consistent when a requirement is removed, moved,
or purged. See docs/ARCHITECTURE.md for the reasoning behind each of these.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .csv_import import RESERVED_COLUMNS
from .models import AttributeDefinition, LinkConfiguration, LinkPair, Module, Project, Requirement, View
from .store import ProjectStore, validate_prefix

BUILTIN_ATTRIBUTES = [
    AttributeDefinition(id="object_text", name="ObjectText", type="text", required=True),
]

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _unique_slug(name: str, exists_fn: Callable[[str], bool], fallback: str = "item") -> str:
    base = _SLUG_NON_ALNUM.sub("-", name.strip().lower()).strip("-") or fallback
    candidate = base
    suffix = 2
    while exists_fn(candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


class ProjectService:
    """Business logic shared by the REST API and the WebSocket RPC endpoint."""

    def __init__(self, store: ProjectStore):
        self.store = store

    # --- projects ----------------------------------------------------------

    def create_project(self, name: str, description: str = "") -> Project:
        project_id = _unique_slug(name, self.store.project_exists, fallback="project")
        project = Project(
            id=project_id, name=name, description=description, attributes=list(BUILTIN_ATTRIBUTES)
        )
        self.store.save_project(project)
        return project

    def get_project(self, project_id: str) -> Project:
        return self.store.load_project(project_id)

    def list_projects(self) -> list[Project]:
        return [self.store.load_project(pid) for pid in self.store.list_project_ids()]

    def add_attribute_definition(
        self,
        project_id: str,
        attr_id: str,
        name: str,
        type: str = "string",
        required: bool = False,
        default: Any = None,
        choices: list[str] | None = None,
    ) -> Project:
        project = self.store.load_project(project_id)
        if any(a.id == attr_id for a in project.attributes):
            raise ValueError(f"Attribute '{attr_id}' already defined on project '{project_id}'")
        name = self._checked_attribute_name(project, name)
        project.attributes.append(
            AttributeDefinition(
                id=attr_id, name=name, type=type, required=required, default=default, choices=choices
            )
        )
        self.store.save_project(project)
        return project

    @staticmethod
    def _checked_attribute_name(project: Project, name: Any) -> str:
        """Return ``name`` stripped, or raise if it cannot head its own export column.

        CSV/XLSX exports head each attribute column with the attribute's name,
        after the reserved ``Id``/``Module``/``Parent`` columns, and the CSV
        import matches columns by name. A reserved or duplicate name (compared
        case-insensitively) would make a project's own export impossible to
        re-import. Existing project data is never rewritten by this check.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Attribute name must not be empty")
        stripped = name.strip()
        folded = stripped.casefold()
        if folded in {column.casefold() for column in RESERVED_COLUMNS}:
            raise ValueError(
                f"Attribute name '{stripped}' is reserved: {', '.join(RESERVED_COLUMNS)} are "
                "export/import columns"
            )
        for attr in project.attributes:
            if attr.name.strip().casefold() == folded:
                raise ValueError(f"Attribute name '{stripped}' is already used by attribute '{attr.id}'")
        return stripped

    # --- modules ----------------------------------------------------------------

    def create_module(self, project_id: str, name: str, prefix: str, description: str = "") -> Module:
        if not self.store.project_exists(project_id):
            raise FileNotFoundError(f"Project '{project_id}' not found")
        validate_prefix(prefix)
        module_id = _unique_slug(
            name, lambda c: self.store.module_exists(project_id, c), fallback="module"
        )
        module = Module(id=module_id, name=name, prefix=prefix, description=description)
        self.store.save_module(project_id, module)
        return module

    def get_module(self, project_id: str, module_id: str) -> Module:
        return self.store.load_module(project_id, module_id)

    def list_modules(self, project_id: str) -> list[Module]:
        return [self.store.load_module(project_id, mid) for mid in self.store.list_module_ids(project_id)]

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
        self, project: Project, attributes: dict[str, Any], *, is_create: bool
    ) -> list[str]:
        """Every reason ``attributes`` cannot be written, in a stable order.

        The one validation path for requirement attributes: REST/WebSocket
        add and update (via ``_validate_attributes``) and the CSV import (one
        call per row) all use it. ``attributes`` must hold typed values; the
        CSV import parses text cells before calling this.
        """
        defined = {a.id: a for a in project.attributes}
        unknown = set(attributes) - set(defined)
        if unknown:
            return [f"Unknown attribute(s) for project '{project.id}': {sorted(unknown)}"]
        system_ids = {a.id for a in project.attributes if a.system} & set(attributes)
        if system_ids:
            return [f"Attribute(s) are system-managed and cannot be set directly: {sorted(system_ids)}"]
        problems = [
            problem
            for attr_id, value in attributes.items()
            if (problem := self._value_problem(defined[attr_id], value)) is not None
        ]
        if is_create:
            for attr in project.attributes:
                if attr.required and not attr.system and attr.id not in attributes and attr.default is None:
                    problems.append(f"required attribute '{attr.name}' is empty")
        return problems

    def _validate_attributes(self, project: Project, attributes: dict[str, Any], *, is_create: bool) -> None:
        if not isinstance(attributes, dict):
            raise ValueError("Attributes must be an object")
        problems = self._attribute_problems(project, attributes, is_create=is_create)
        if problems:
            raise ValueError("; ".join(problems))

    @staticmethod
    def _merged_attributes(project: Project, attributes: dict[str, Any]) -> dict[str, Any]:
        """Attribute defaults (never for system attributes) overlaid with ``attributes``."""
        merged = {a.id: a.default for a in project.attributes if a.default is not None and not a.system}
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

        self._validate_attributes(project, attributes, is_create=True)
        merged = self._merged_attributes(project, attributes)

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

    def move_requirement(
        self, project_id: str, module_id: str, requirement_id: str, parent_id: str | None
    ) -> Requirement:
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
        requirement.order = self._next_sibling_order(
            [r for r in requirements if r.id != requirement_id], parent_id
        )
        self.store.save_requirement(project_id, module_id, requirement)
        return requirement

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

    def update_requirement(
        self, project_id: str, module_id: str, requirement_id: str, attributes: dict[str, Any]
    ) -> Requirement:
        project = self.store.load_project(project_id)
        self._validate_attributes(project, attributes, is_create=False)
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

    def remove_requirement(self, project_id: str, module_id: str, requirement_id: str) -> None:
        removed = self.store.load_requirement(project_id, module_id, requirement_id)
        for other in self.store.load_all_requirements(project_id, module_id):
            if other.parent_id == requirement_id:
                other.parent_id = removed.parent_id
                self.store.save_requirement(project_id, module_id, other)

        self._cleanup_links_for_requirement(project_id, requirement_id)
        self.store.delete_requirement(project_id, module_id, requirement_id)

    def purge_module(self, project_id: str, module_id: str) -> int:
        ids = set(self.store.list_requirement_ids(project_id, module_id))
        for requirement_id in ids:
            self._cleanup_links_for_requirement(project_id, requirement_id)
            self.store.delete_requirement(project_id, module_id, requirement_id)
        return len(ids)

    def purge_project(self, project_id: str) -> int:
        total = 0
        for module_id in self.store.list_module_ids(project_id):
            total += self.purge_module(project_id, module_id)
        return total

    # --- link configurations ("link modules") ----------------------------------

    def create_link_configuration(
        self, project_id: str, name: str, source_module_id: str, target_module_id: str
    ) -> LinkConfiguration:
        project = self.store.load_project(project_id)
        if not self.store.module_exists(project_id, source_module_id):
            raise FileNotFoundError(f"Module '{source_module_id}' not found in project '{project_id}'")
        if not self.store.module_exists(project_id, target_module_id):
            raise FileNotFoundError(f"Module '{target_module_id}' not found in project '{project_id}'")

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
        self.store.save_project(project)
        self.store.save_link_pairs(project_id, config_id, [])
        return config

    def list_link_configurations(self, project_id: str) -> list[LinkConfiguration]:
        return self.store.load_project(project_id).link_configurations

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

    def create_view(self, project_id: str, name: str, attribute_ids: list[str]) -> View:
        project = self.store.load_project(project_id)
        defined_ids = {a.id for a in project.attributes}
        unknown = set(attribute_ids) - defined_ids
        if unknown:
            raise ValueError(f"Unknown attribute(s) for project '{project_id}': {sorted(unknown)}")

        taken_ids = {v.id for v in project.views}
        view_id = _unique_slug(name, lambda c: c in taken_ids, fallback="view")

        view = View(id=view_id, name=name, attribute_ids=list(attribute_ids))
        project.views.append(view)
        self.store.save_project(project)
        return view

    def list_views(self, project_id: str) -> list[View]:
        return self.store.load_project(project_id).views

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

    # --- export ----------------------------------------------------------------

    def export_reqif(self, project_id: str) -> str:
        from .reqif_export import export_project_to_reqif

        project = self.store.load_project(project_id)
        modules = [
            (module, self.store.load_all_requirements(project_id, module.id))
            for module in self.list_modules(project_id)
        ]
        link_pairs = {
            config.id: self.store.load_link_pairs(project_id, config.id)
            for config in project.link_configurations
        }
        return export_project_to_reqif(project, modules, project.link_configurations, link_pairs)

    def export_csv(self, project_id: str) -> str:
        from .csv_export import export_project_to_csv

        project = self.store.load_project(project_id)
        modules = [
            (module, self.store.load_all_requirements(project_id, module.id))
            for module in self.list_modules(project_id)
        ]
        return export_project_to_csv(project, modules)

    def export_xlsx(self, project_id: str) -> bytes:
        from .xlsx_export import export_project_to_xlsx

        project = self.store.load_project(project_id)
        modules = [
            (module, self.store.load_all_requirements(project_id, module.id))
            for module in self.list_modules(project_id)
        ]
        return export_project_to_xlsx(project, modules)

    def export_docx(self, project_id: str) -> bytes:
        from .docx_export import export_project_to_docx

        project = self.store.load_project(project_id)
        modules = [
            (module, self.store.load_all_requirements(project_id, module.id))
            for module in self.list_modules(project_id)
        ]
        return export_project_to_docx(project, modules)

    # --- import ----------------------------------------------------------------

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
            validate=lambda values: self._attribute_problems(project, values, is_create=True),
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
            attributes = self._merged_attributes(project, row.attributes)
            created.append(Requirement(id=requirement_id, attributes=attributes, parent_id=parent_id, order=order))

        self._write_new_requirements(project, module_id, created)

        return {
            "imported": len(created),
            "requirement_ids": [new_ids[row.row_number] for row in sorted(parsed.rows, key=lambda r: r.row_number)],
            "ignored_columns": parsed.ignored_columns,
        }
