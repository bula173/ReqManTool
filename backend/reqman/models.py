"""Persisted data shapes: Project, Module, Requirement, AttributeDefinition,
LinkConfiguration, LinkPair.

Plain dataclasses only — no behavior, no validation beyond field defaults.
Each type carries its own ``to_dict``/``from_dict`` for JSON (de)serialization;
field order is the on-disk JSON key order (see docs/ARCHITECTURE.md's
"Git-friendly persistence" section for why that's kept stable).

Business rules that touch these types (id generation, nesting/link
invariants, attribute validation) live in ``service.py``, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal

AttributeType = Literal["string", "text", "integer", "boolean", "enum"]


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

    def to_dict(self) -> dict:
        return asdict(self)

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
        )


@dataclass
class LinkConfiguration:
    id: str
    name: str
    source_module_id: str
    target_module_id: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "LinkConfiguration":
        return LinkConfiguration(
            id=data["id"],
            name=data["name"],
            source_module_id=data["source_module_id"],
            target_module_id=data["target_module_id"],
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


@dataclass
class View:
    id: str
    name: str
    attribute_ids: list[str] = field(default_factory=list)
    # Optional shared column widths in pixels, keyed by attribute id (plus
    # ``ProjectService.VIEW_ID_COLUMN`` for the Id column). None (the default)
    # means the view stores no widths; it is then not written at all, so a
    # view without widths keeps exactly the keys it had before.
    column_widths: dict[str, int] | None = None

    def to_dict(self) -> dict:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "attribute_ids": list(self.attribute_ids),
        }
        if self.column_widths:
            data["column_widths"] = dict(self.column_widths)
        return data

    @staticmethod
    def from_dict(data: dict) -> "View":
        return View(
            id=data["id"],
            name=data["name"],
            attribute_ids=data.get("attribute_ids", []),
            column_widths=_column_widths(data.get("column_widths")),
        )


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

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Module":
        return Module(
            id=data["id"],
            name=data["name"],
            prefix=data["prefix"],
            description=data.get("description", ""),
        )


@dataclass
class Project:
    id: str
    name: str
    description: str = ""
    # The generic project this one specializes (subproject tree), or None
    # for a top-level project. Rules (parent exists, no cycles) live in
    # ``ProjectService``.
    parent_project_id: str | None = None
    attributes: list[AttributeDefinition] = field(default_factory=list)
    id_counters: dict[str, int] = field(default_factory=dict)
    link_configurations: list[LinkConfiguration] = field(default_factory=list)
    views: list[View] = field(default_factory=list)
    # Module ids in display order. Optional: a project without it (older
    # data) lists its modules in id order. ``ProjectService`` drops entries
    # naming missing modules and appends unlisted modules in id order.
    module_order: list[str] = field(default_factory=list)

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
        return {
            **data,
            "attributes": [a.to_dict() for a in self.attributes],
            "id_counters": self.id_counters,
            "link_configurations": [c.to_dict() for c in self.link_configurations],
            "views": [v.to_dict() for v in self.views],
            # Written only when set, like ``parent_project_id``, so a project
            # without modules (or older data) gains no empty key on save.
            **({"module_order": list(self.module_order)} if self.module_order else {}),
        }

    @staticmethod
    def from_dict(data: dict) -> "Project":
        return Project(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            parent_project_id=data.get("parent_project_id"),
            attributes=[AttributeDefinition.from_dict(a) for a in data.get("attributes", [])],
            id_counters=data.get("id_counters", {}),
            views=[View.from_dict(v) for v in data.get("views", [])],
            link_configurations=[
                LinkConfiguration.from_dict(c) for c in data.get("link_configurations", [])
            ],
            module_order=_string_list(data.get("module_order")),
        )
