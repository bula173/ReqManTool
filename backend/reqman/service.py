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
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from .csv_import import RESERVED_COLUMNS
from .formats import NewerFormatError
from .models import AttributeDefinition, LinkConfiguration, LinkPair, Module, Project, Requirement, View
from .store import ProjectStore, validate_prefix
from .templates import (
    BUILTIN_SOURCE,
    BUILTIN_TEMPLATES_DIR,
    LINK_REF_PREFIX,
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

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Project id used while dry-running a template build to validate it; never written.
_TEMPLATE_CHECK_PROJECT_ID = "template-check"


class ConflictError(ValueError):
    """The request clashes with existing data (REST 409; WebSocket treats it like any ValueError)."""


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

    def __init__(self, store: ProjectStore, builtin_templates_dir: Path | None = None):
        self.store = store
        self.builtin_templates_dir = (
            builtin_templates_dir if builtin_templates_dir is not None else BUILTIN_TEMPLATES_DIR
        )

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
            project_id = _unique_slug(name, self.store.project_exists, fallback="project")
            project = Project(
                id=project_id,
                name=name,
                description=description,
                parent_project_id=parent_project_id,
                attributes=list(BUILTIN_ATTRIBUTES),
            )
            self.store.save_project(project)
            return project

        template = self._load_template(template_id)
        # Skip any path that is taken, even by a non-project directory: the
        # staged project is moved there by a rename, which must not merge
        # into (or clobber) existing files.
        project_id = _unique_slug(name, self.store.project_path_exists, fallback="project")
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
        return project

    def get_project(self, project_id: str) -> Project:
        return self.store.load_project(project_id)

    def list_projects(self) -> list[Project]:
        """Every project, with ``parent_project_id`` as the effective subproject tree.

        Hand-edited data can hold a parent that does not exist, or a cycle;
        both are shown as top level here (only in the returned objects, the
        files are not rewritten), so the tree always terminates.
        ``get_project`` returns the stored value unchanged.
        """
        projects = [self.store.load_project(pid) for pid in self.store.list_project_ids()]
        broken = self._broken_parent_ids(projects)
        for project in projects:
            if project.id in broken:
                project.parent_project_id = None
        return projects

    @staticmethod
    def _broken_parent_ids(projects: list[Project]) -> set[str]:
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

    def set_project_parent(self, project_id: str, parent_project_id: str | None) -> Project:
        """Move a project in the subproject tree; ``None`` makes it top level."""
        project = self.store.load_project(project_id)
        self._check_parent(project_id, parent_project_id)
        if project.parent_project_id != parent_project_id:
            project.parent_project_id = parent_project_id
            self.store.save_project(project)
        return project

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
        project.attributes.append(
            self._new_attribute_definition(project, attr_id, name, type, required, default, choices)
        )
        self.store.save_project(project)
        return project

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

    def create_module(self, project_id: str, name: str, prefix: str, description: str = "") -> Module:
        """Create a module, appended at the end of the project's module order.

        The module file is written first, then project.json with the new
        order. If the second write fails, the module is still listed: an
        existing module missing from the order is appended in id order.
        """
        project = self.store.load_project(project_id)
        validate_prefix(prefix)
        module_id = _unique_slug(
            name, lambda c: self.store.module_exists(project_id, c), fallback="module"
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
        effective = self._merged_attributes(project, attributes) if is_create else attributes
        for attr in project.attributes:
            if not attr.required or attr.system or attr.id in invalid_ids:
                continue
            if (is_create or attr.id in attributes) and self._is_blank(effective.get(attr.id)):
                problems.append(f"required attribute '{attr.name}' is empty")
        return problems

    @staticmethod
    def _is_blank(value: Any) -> bool:
        """``None``, ``""`` and whitespace-only strings count as no value."""
        return value is None or (isinstance(value, str) and not value.strip())

    def _validate_attributes(self, project: Project, attributes: dict[str, Any], *, is_create: bool) -> None:
        if not isinstance(attributes, dict):
            raise ValueError("Attributes must be an object")
        problems = self._attribute_problems(project, attributes, is_create=is_create)
        if problems:
            raise ValueError("; ".join(problems))

    @classmethod
    def _merged_attributes(cls, project: Project, attributes: dict[str, Any]) -> dict[str, Any]:
        """Attribute defaults overlaid with ``attributes``.

        System attributes never get a default. A stored default that is not
        valid for its attribute (legacy data written before defaults were
        checked) is skipped rather than rewritten, so it cannot block or
        corrupt new requirements.
        """
        merged = {
            a.id: a.default
            for a in project.attributes
            if a.default is not None and not a.system and cls._value_problem(a, a.default) is None
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
        view = self._add_view(project, name, attribute_ids)
        self.store.save_project(project)
        return view

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
        try:
            name = json.loads(text)["name"] if text is not None else None
        except (ValueError, TypeError, KeyError, IndexError):
            name = None
        return name.strip() if isinstance(name, str) and name.strip() else local_id

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
