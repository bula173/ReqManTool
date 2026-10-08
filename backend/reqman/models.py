"""Persisted data shapes: Project, ProjectLock, Module, Requirement,
AttributeDefinition, LinkConfiguration, LinkPair, View, RowStyle.

Plain dataclasses only — no behavior, no validation beyond field defaults.
Each type carries its own ``to_dict``/``from_dict`` for JSON (de)serialization;
field order is the on-disk JSON key order (see docs/ARCHITECTURE.md's
"Git-friendly persistence" section for why that's kept stable).

Business rules that touch these types (id generation, nesting/link
invariants, attribute validation) live in ``service.py``, not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

AttributeType = Literal["string", "text", "integer", "boolean", "enum", "date"]

# What a module holds (project format 5). ``requirements`` is the default, and
# the only kind before module types existed. Rules (which link configurations
# may join which kinds) live in ``ProjectService``.
ModuleType = Literal["requirements", "test_cases", "test_execution"]
MODULE_TYPES: tuple[str, ...] = ("requirements", "test_cases", "test_execution")
DEFAULT_MODULE_TYPE = "requirements"


def _string_list(value: Any) -> list[str]:
    """The strings of a hand-editable list value; anything else reads as empty."""
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


@dataclass
class AttributeDefinition:
    id: str
    name: str
    type: AttributeType = "string"
    required: bool = False
    default: Any = None
    choices: list[str] | None = None
    system: bool = False
    # Module ids the attribute applies to (project format 2). Empty means
    # every module, so it is written only when set: an unscoped attribute
    # keeps exactly the keys it had before. Rules (ids exist, values only in
    # listed modules) live in ``ProjectService``.
    modules: list[str] = field(default_factory=list)
    # Module types the attribute also applies to (project format 5), in
    # ``MODULE_TYPES`` order. Like ``modules``, written only when set; an
    # attribute with neither list applies to every module.
    module_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        if not self.modules:
            del data["modules"]
        if not self.module_types:
            del data["module_types"]
        return data

    @staticmethod
    def from_dict(data: dict) -> "AttributeDefinition":
        return AttributeDefinition(
            id=data["id"],
            name=data["name"],
            type=data.get("type", "string"),
            required=data.get("required", False),
            default=data.get("default"),
            choices=data.get("choices"),
            system=data.get("system", False),
            modules=_string_list(data.get("modules")),
            module_types=_string_list(data.get("module_types")),
        )


@dataclass
class LinkConfiguration:
    id: str
    name: str
    source_module_id: str
    target_module_id: str
    # The project holding ``target_module_id`` for a cross-project link
    # configuration (project format 6), or None for a module of the
    # configuration's own project. Written only when set, so a configuration
    # within one project keeps exactly the keys it had before. Rules (the
    # target project is referenced, direction, cleanup) live in
    # ``ProjectService``.
    target_project_id: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.target_project_id is None:
            del data["target_project_id"]
        return data

    @staticmethod
    def from_dict(data: dict) -> "LinkConfiguration":
        target_project_id = data.get("target_project_id")
        return LinkConfiguration(
            id=data["id"],
            name=data["name"],
            source_module_id=data["source_module_id"],
            target_module_id=data["target_module_id"],
            target_project_id=target_project_id if isinstance(target_project_id, str) and target_project_id else None,
        )


@dataclass
class LinkPair:
    source_id: str
    target_id: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "LinkPair":
        return LinkPair(source_id=data["source_id"], target_id=data["target_id"])


def _column_widths(value: Any) -> dict[str, int] | None:
    """The usable entries of a hand-editable column-width mapping, or None.

    Keeps string keys with positive integer (not boolean) values; anything
    else reads as absent. Bounds and key rules are ``ProjectService``'s.
    """
    if not isinstance(value, dict):
        return None
    widths = {
        k: v for k, v in value.items()
        if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
    }
    return widths or None


def _column_keys(value: Any) -> list[str] | None:
    """The string entries of a hand-editable column-key list (first occurrence kept), or None."""
    if not isinstance(value, list):
        return None
    keys = list(dict.fromkeys(k for k in value if isinstance(k, str)))
    return keys or None


@dataclass
class View:
    id: str
    name: str
    # The view's attribute columns, in display order (Id always comes first).
    attribute_ids: list[str] = field(default_factory=list)
    # Optional shared column widths in pixels, keyed by attribute id (plus
    # ``ProjectService.VIEW_ID_COLUMN`` for the Id column). None (the default)
    # means the view stores no widths; it is then not written at all, so a
    # view without widths keeps exactly the keys it had before.
    column_widths: dict[str, int] | None = None
    # Optional column keys (as for ``column_widths``) shown on a single line
    # (truncated) instead of wrapping. None (the default): every column
    # wraps; then not written at all, like ``column_widths``.
    single_line: list[str] | None = None
    # Optional module ids the view applies to (offered on those modules'
    # pages only), in module display order; empty (the default): every
    # module. Written only when non-empty, like ``AttributeDefinition.modules``.
    modules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "attribute_ids": list(self.attribute_ids),
        }
        if self.column_widths:
            data["column_widths"] = dict(self.column_widths)
        if self.single_line:
            data["single_line"] = list(self.single_line)
        if self.modules:
            data["modules"] = list(self.modules)
        return data

    @staticmethod
    def from_dict(data: dict) -> "View":
        return View(
            id=data["id"],
            name=data["name"],
            attribute_ids=data.get("attribute_ids", []),
            column_widths=_column_widths(data.get("column_widths")),
            single_line=_column_keys(data.get("single_line")),
            modules=_column_keys(data.get("modules")) or [],
        )


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _color(value: Any) -> str | None:
    """A hand-edited ``#rrggbb`` color (lower-cased), or None for anything else."""
    return value.lower() if isinstance(value, str) and _HEX_COLOR.match(value) else None


@dataclass
class RowStyle:
    """How rows whose enum attribute has a given value are drawn (project format 3).

    ``font_color``/``background_color`` are ``#rrggbb`` or None (unchanged).
    Rules (known enum attribute, value among its choices, no duplicate
    attribute/value pair, limits) live in ``ProjectService``.
    """

    attribute_id: str
    value: str
    font_color: str | None = None
    background_color: str | None = None
    bold: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "RowStyle":
        return RowStyle(
            attribute_id=data["attribute_id"],
            value=data["value"],
            font_color=_color(data.get("font_color")),
            background_color=_color(data.get("background_color")),
            bold=data.get("bold") is True,
        )


def _row_styles(value: Any) -> list[RowStyle]:
    """The usable entries of a hand-editable ``row_styles`` list.

    An entry that is not an object with string ``attribute_id`` and ``value``
    is skipped; anything else reads as no rules.
    """
    if not isinstance(value, list):
        return []
    return [
        RowStyle.from_dict(item)
        for item in value
        if isinstance(item, dict) and isinstance(item.get("attribute_id"), str) and isinstance(item.get("value"), str)
    ]


@dataclass
class Requirement:
    id: str
    attributes: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    order: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Requirement":
        return Requirement(
            id=data["id"],
            attributes=data.get("attributes", {}),
            parent_id=data.get("parent_id"),
            order=data.get("order", 0),
        )


@dataclass
class Module:
    id: str
    name: str
    prefix: str
    description: str = ""
    # One of ``MODULE_TYPES`` (project format 5). Written only when it is not
    # the default, so a requirements module keeps exactly the keys it had
    # before module types existed (API responses omit it the same way). A
    # missing or unknown value reads as ``requirements``.
    type: str = DEFAULT_MODULE_TYPE

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.type == DEFAULT_MODULE_TYPE:
            del data["type"]
        return data

    @staticmethod
    def from_dict(data: dict) -> "Module":
        module_type = data.get("type", DEFAULT_MODULE_TYPE)
        return Module(
            id=data["id"],
            name=data["name"],
            prefix=data["prefix"],
            description=data.get("description", ""),
            type=module_type if module_type in MODULE_TYPES else DEFAULT_MODULE_TYPE,
        )


# Deepest folder nesting read from a hand-edited ``module_order`` (top-level
# folders are depth 1). A folder nested deeper is ignored on read; its
# modules are listed as unlisted modules. ``ProjectService`` enforces the
# (smaller) limit for folders it creates or moves.
MAX_READ_FOLDER_DEPTH = 64


@dataclass
class ModuleFolder:
    """A folder of a project's module tree (project format 7).

    Purely organisational: module ids, requirement ids and links do not
    depend on it. ``children`` are the folder's entries in display order:
    module ids (``str``) and nested folders. Written as ``{"folder": id,
    "name": name, "children": [...]}``. Rules (server-allocated ids, names
    unique among sibling folders, delete only when empty, no cycles, depth)
    live in ``ProjectService``.
    """

    id: str
    name: str
    children: list[str | ModuleFolder] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "folder": self.id,
            "name": self.name,
            "children": module_order_to_list(self.children),
        }


def module_order_to_list(entries: list) -> list[dict]:
    """``module_order`` entries as JSON: ``{"module": id}`` or a folder object."""
    return [e.to_dict() if isinstance(e, ModuleFolder) else {"module": e} for e in entries]


def module_order_from_list(value: Any, depth: int = 1) -> list:
    """The usable entries of a hand-editable ``module_order`` list.

    ``{"module": "<id>"}`` (or, tolerantly, a bare id string) is a module;
    ``{"folder": "<id>", "name": ..., "children": [...]}`` a folder (a
    missing or non-string name reads as the id, non-list children as none).
    Anything else, and folders nested deeper than ``MAX_READ_FOLDER_DEPTH``,
    is skipped.
    """
    if not isinstance(value, list):
        return []
    result: list = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            module_id = item.get("module")
            folder_id = item.get("folder")
            if isinstance(module_id, str):
                result.append(module_id)
            elif isinstance(folder_id, str) and folder_id and depth <= MAX_READ_FOLDER_DEPTH:
                name = item.get("name")
                result.append(
                    ModuleFolder(
                        id=folder_id,
                        name=name if isinstance(name, str) and name.strip() else folder_id,
                        children=module_order_from_list(item.get("children"), depth + 1),
                    )
                )
    return result


@dataclass
class ProjectLock:
    """Who locked a project, when and why (project format 8).

    ``by``/``email``: the git identity of the user who locked it (as for
    commits); ``at``: an ISO 8601 UTC timestamp; ``reason``: free text;
    ``baseline``: the baseline the lock was set after ("lock after
    baseline"), written only when set. The rule that a locked project
    refuses every change but unlock lives in ``ProjectService``.
    """

    by: str = ""
    email: str = ""
    at: str = ""
    reason: str = ""
    baseline: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.baseline is None:
            del data["baseline"]
        return data

    @staticmethod
    def from_dict(data: Any) -> "ProjectLock | None":
        """A lock from its stored value; ``None`` for no lock.

        Tolerant of hand edits: any other truthy value (e.g. ``true``) still
        means locked, with unknown details, so a lock is never lost by
        mistake. ``null``/``false``/a missing key mean unlocked.
        """
        if isinstance(data, dict):

            def text(key: str) -> str:
                value = data.get(key)
                return value if isinstance(value, str) else ""

            baseline = data.get("baseline")
            return ProjectLock(
                by=text("by"),
                email=text("email"),
                at=text("at"),
                reason=text("reason"),
                baseline=baseline if isinstance(baseline, str) and baseline else None,
            )
        return ProjectLock() if data else None


@dataclass
class Project:
    id: str
    name: str
    description: str = ""
    # The generic project this one specializes (subproject tree), or None
    # for a top-level project. Rules (parent exists, no cycles) live in
    # ``ProjectService``.
    parent_project_id: str | None = None
    # Ids of the other projects this one references (project format 6): link
    # configurations may target their modules. The parent is referenced
    # implicitly and need not be listed. Written only when non-empty, right
    # after ``parent_project_id``. Rules (existing project, not itself, no
    # duplicates, not dropped while a link configuration targets it) live in
    # ``ProjectService``.
    references: list[str] = field(default_factory=list)
    # The project lock (project format 8), or None. Written only when set,
    # right after ``references``. While set, ``ProjectService`` refuses every
    # change of the project except unlocking it.
    locked: ProjectLock | None = None
    attributes: list[AttributeDefinition] = field(default_factory=list)
    id_counters: dict[str, int] = field(default_factory=dict)
    link_configurations: list[LinkConfiguration] = field(default_factory=list)
    views: list[View] = field(default_factory=list)
    # The module tree in display order (project format 7): module ids
    # (``str``, written ``{"module": id}``) and ``ModuleFolder``s. Before
    # format 7 a flat list of module id strings. Optional: a project without
    # it (older data) lists its modules in id order. ``ProjectService``
    # applies it tolerantly (see ``module_tree.effective_tree``): entries
    # naming missing modules are dropped and unlisted modules follow at the
    # top level in id order.
    module_order: list[str | ModuleFolder] = field(default_factory=list)
    # Row styling rules (project format 3), in display order. Written only
    # when non-empty, after every other key, so a project without rules keeps
    # exactly the keys it had before.
    row_styles: list[RowStyle] = field(default_factory=list)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
        }
        # Written only when set, so a top-level project's project.json keeps
        # exactly the keys it had before subprojects existed (no
        # ``"parent_project_id": null`` line appears in version-controlled
        # data when an old project is next saved).
        if self.parent_project_id is not None:
            data["parent_project_id"] = self.parent_project_id
        if self.references:
            data["references"] = list(self.references)
        if self.locked is not None:
            data["locked"] = self.locked.to_dict()
        return {
            **data,
            "attributes": [a.to_dict() for a in self.attributes],
            "id_counters": self.id_counters,
            "link_configurations": [c.to_dict() for c in self.link_configurations],
            "views": [v.to_dict() for v in self.views],
            # Written only when set, like ``parent_project_id``, so a project
            # without modules (or older data) gains no empty key on save.
            **({"module_order": module_order_to_list(self.module_order)} if self.module_order else {}),
            **({"row_styles": [s.to_dict() for s in self.row_styles]} if self.row_styles else {}),
        }

    @staticmethod
    def from_dict(data: dict) -> "Project":
        project = Project(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            parent_project_id=data.get("parent_project_id"),
            references=_string_list(data.get("references")),
            locked=ProjectLock.from_dict(data.get("locked")),
            attributes=[AttributeDefinition.from_dict(a) for a in data.get("attributes", [])],
            id_counters=data.get("id_counters", {}),
            views=[View.from_dict(v) for v in data.get("views", [])],
            link_configurations=[
                LinkConfiguration.from_dict(c) for c in data.get("link_configurations", [])
            ],
            module_order=module_order_from_list(data.get("module_order")),
            row_styles=_row_styles(data.get("row_styles")),
        )
        # A (hand-edited) configuration targeting its own project is a local one.
        for config in project.link_configurations:
            if config.target_project_id == project.id:
                config.target_project_id = None
        return project
