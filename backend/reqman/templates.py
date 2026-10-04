"""Project templates: JSON files that describe a project's structure
(attribute definitions, modules, link configurations between those
modules, and views) without any requirements.

Template ids are namespaced by where the file lives, so built-in and user
templates can never collide or shadow each other:

- ``builtin:<stem>`` — ``backend/reqman/templates/<stem>.json``, shipped
  with the tool and read-only;
- ``user:<stem>`` — ``<data-directory>/_templates/<stem>.json``, written by
  "Save as template" (``ProjectService.save_project_as_template``) or by
  hand.

The file name stem is the template's identity; the ``id`` key inside the
file is informational only.

Format (``format_version`` 1)::

    {
      "format_version": 1,
      "id": "<stem>",
      "name": "...",
      "description": "...",
      "attributes": [{"id", "name", "type", "required", "default", "choices"}],
      "modules": [{"key", "name", "prefix", "description"}],
      "link_configurations": [{"key", "name", "source_module", "target_module"}],
      "views": [{"name", "attribute_ids"}]
    }

``attributes`` never lists the built-in ``object_text``: every project
already has it. A view's ``attribute_ids`` entry is ``object_text``, a
template attribute id, or ``link:<key>`` for the mirror attribute of the
link configuration with that key.

Compatibility rules:

- Unknown keys are ignored at every level, so a later *optional* key (for
  example a module ``type``) can be added without bumping
  ``format_version``; an older ReqManTool simply ignores it.
- ``format_version`` is bumped only for a change an older reader would
  misinterpret. A template whose ``format_version`` is not 1 (missing, not
  an integer, or a newer version) is reported as invalid, never guessed at.

This module only checks a template's *shape* (types, required keys, unique
keys/names, module-key references). The domain rules a project enforces
(attribute names, types, choices and defaults, prefixes, link names, view
attribute references) are checked by ``ProjectService``, which builds the
project in memory with the same helpers its regular create operations use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1

BUILTIN_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

BUILTIN_SOURCE = "builtin"
USER_SOURCE = "user"
TEMPLATE_SOURCES = (BUILTIN_SOURCE, USER_SOURCE)

# A view entry ``link:<key>`` names the mirror attribute of a link configuration.
LINK_REF_PREFIX = "link:"


@dataclass
class TemplateAttribute:
    id: str
    name: Any
    type: Any = "string"
    required: bool = False
    default: Any = None
    choices: Any = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "choices": self.choices,
        }


@dataclass
class TemplateModule:
    key: str
    name: str
    prefix: Any
    description: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "prefix": self.prefix, "description": self.description}


@dataclass
class TemplateLinkConfiguration:
    key: str
    name: Any
    source_module: str
    target_module: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "source_module": self.source_module,
            "target_module": self.target_module,
        }


@dataclass
class TemplateView:
    name: str
    attribute_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "attribute_ids": list(self.attribute_ids)}


@dataclass
class Template:
    id: str  # namespaced: "builtin:<stem>" or "user:<stem>"
    source: str
    name: str
    description: str = ""
    format_version: int = FORMAT_VERSION
    attributes: list[TemplateAttribute] = field(default_factory=list)
    modules: list[TemplateModule] = field(default_factory=list)
    link_configurations: list[TemplateLinkConfiguration] = field(default_factory=list)
    views: list[TemplateView] = field(default_factory=list)

    @property
    def local_id(self) -> str:
        return split_template_id(self.id)[1]

    def to_dict(self) -> dict:
        """The on-disk JSON shape (key order is the file's key order)."""
        return {
            "format_version": self.format_version,
            "id": self.local_id,
            "name": self.name,
            "description": self.description,
            "attributes": [a.to_dict() for a in self.attributes],
            "modules": [m.to_dict() for m in self.modules],
            "link_configurations": [c.to_dict() for c in self.link_configurations],
            "views": [v.to_dict() for v in self.views],
        }


def make_template_id(source: str, local_id: str) -> str:
    return f"{source}:{local_id}"


def split_template_id(template_id: Any) -> tuple[str, str]:
    """``"user:abc"`` → ``("user", "abc")``; raises ``ValueError`` if not namespaced."""
    if not isinstance(template_id, str) or ":" not in template_id:
        raise ValueError(
            f"Invalid template id {template_id!r}: expected 'builtin:<id>' or 'user:<id>'"
        )
    source, local_id = template_id.split(":", 1)
    if source not in TEMPLATE_SOURCES or not local_id:
        raise ValueError(
            f"Invalid template id {template_id!r}: expected 'builtin:<id>' or 'user:<id>'"
        )
    return source, local_id


def list_builtin_template_ids(directory: Path = BUILTIN_TEMPLATES_DIR) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if p.is_file() and not p.name.startswith("."))


def read_builtin_template_text(local_id: str, directory: Path = BUILTIN_TEMPLATES_DIR) -> str:
    # Looked up among the listed files, never by joining a raw id into a path.
    if local_id not in list_builtin_template_ids(directory):
        raise FileNotFoundError(f"Template '{make_template_id(BUILTIN_SOURCE, local_id)}' not found")
    return (directory / f"{local_id}.json").read_text(encoding="utf-8-sig")


# --- shape validation --------------------------------------------------------


def _is_nonblank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _list_of_dicts(data: dict, key: str) -> list[dict]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"'{key}' must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{key}[{index}] must be an object")
    return value


def _check_format_version(data: dict) -> int:
    version = data.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError(f"'format_version' must be the integer {FORMAT_VERSION}, got {version!r}")
    if version > FORMAT_VERSION:
        raise ValueError(
            f"format_version {version} is newer than this ReqManTool supports ({FORMAT_VERSION}); "
            "update ReqManTool to use this template"
        )
    if version != FORMAT_VERSION:
        raise ValueError(f"Unsupported format_version {version}; expected {FORMAT_VERSION}")
    return version


def parse_template_text(text: str, template_id: str) -> Template:
    """Parse JSON text into a ``Template``; raises ``ValueError`` describing the first problem.

    A leading UTF-8 byte order mark (as some editors save files) is ignored.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    return parse_template(data, template_id)


def parse_template(data: Any, template_id: str) -> Template:
    """Check a template's shape and return it; unknown keys are ignored."""
    source, _ = split_template_id(template_id)
    if not isinstance(data, dict):
        raise ValueError("a template must be a JSON object")
    version = _check_format_version(data)

    name = data.get("name")
    if not _is_nonblank_str(name):
        raise ValueError("'name' must be a non-empty string")
    description = data.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise ValueError("'description' must be a string")

    attributes: list[TemplateAttribute] = []
    attribute_ids: set[str] = set()
    for index, raw in enumerate(_list_of_dicts(data, "attributes")):
        where = f"attributes[{index}]"
        attr_id = raw.get("id")
        if not _is_nonblank_str(attr_id):
            raise ValueError(f"{where}: 'id' must be a non-empty string")
        if attr_id in attribute_ids:
            raise ValueError(f"{where}: duplicate attribute id '{attr_id}'")
        attribute_ids.add(attr_id)
        if attr_id.startswith(LINK_REF_PREFIX):
            raise ValueError(
                f"{where}: attribute id '{attr_id}' must not start with '{LINK_REF_PREFIX}' "
                "(reserved for link configuration references in views)"
            )
        required = raw.get("required", False)
        if not isinstance(required, bool):
            raise ValueError(f"{where}: 'required' must be true or false")
        if raw.get("system", False):
            raise ValueError(
                f"{where}: template attributes cannot be system attributes; link mirror "
                "attributes come from 'link_configurations'"
            )
        attributes.append(
            TemplateAttribute(
                id=attr_id,
                name=raw.get("name"),
                type=raw.get("type", "string"),
                required=required,
                default=raw.get("default"),
                choices=raw.get("choices"),
            )
        )

    modules: list[TemplateModule] = []
    module_keys: set[str] = set()
    module_names: set[str] = set()
    for index, raw in enumerate(_list_of_dicts(data, "modules")):
        where = f"modules[{index}]"
        key = raw.get("key")
        if not _is_nonblank_str(key):
            raise ValueError(f"{where}: 'key' must be a non-empty string")
        if key in module_keys:
            raise ValueError(f"{where}: duplicate module key '{key}'")
        module_name = raw.get("name")
        if not _is_nonblank_str(module_name):
            raise ValueError(f"{where}: 'name' must be a non-empty string")
        module_name = module_name.strip()
        if module_name.casefold() in module_names:
            raise ValueError(f"{where}: duplicate module name '{module_name}'")
        module_description = raw.get("description", "")
        if module_description is None:
            module_description = ""
        if not isinstance(module_description, str):
            raise ValueError(f"{where}: 'description' must be a string")
        prefix = raw.get("prefix")
        if not isinstance(prefix, str):
            raise ValueError(f"{where}: 'prefix' must be a string")
        module_keys.add(key)
        module_names.add(module_name.casefold())
        modules.append(
            TemplateModule(key=key, name=module_name, prefix=prefix, description=module_description)
        )

    link_configurations: list[TemplateLinkConfiguration] = []
    link_keys: set[str] = set()
    for index, raw in enumerate(_list_of_dicts(data, "link_configurations")):
        where = f"link_configurations[{index}]"
        key = raw.get("key")
        if not _is_nonblank_str(key):
            raise ValueError(f"{where}: 'key' must be a non-empty string")
        if key in link_keys:
            raise ValueError(f"{where}: duplicate link configuration key '{key}'")
        for end in ("source_module", "target_module"):
            module_key = raw.get(end)
            if not isinstance(module_key, str) or module_key not in module_keys:
                raise ValueError(f"{where}: '{end}' {module_key!r} is not a module key of this template")
        link_keys.add(key)
        link_configurations.append(
            TemplateLinkConfiguration(
                key=key,
                name=raw.get("name"),
                source_module=raw["source_module"],
                target_module=raw["target_module"],
            )
        )

    views: list[TemplateView] = []
    view_names: set[str] = set()
    for index, raw in enumerate(_list_of_dicts(data, "views")):
        where = f"views[{index}]"
        view_name = raw.get("name")
        if not _is_nonblank_str(view_name):
            raise ValueError(f"{where}: 'name' must be a non-empty string")
        view_name = view_name.strip()
        if view_name.casefold() in view_names:
            raise ValueError(f"{where}: duplicate view name '{view_name}'")
        attribute_ids = raw.get("attribute_ids", [])
        if not isinstance(attribute_ids, list) or not all(isinstance(a, str) for a in attribute_ids):
            raise ValueError(f"{where}: 'attribute_ids' must be a list of strings")
        view_names.add(view_name.casefold())
        views.append(TemplateView(name=view_name, attribute_ids=list(attribute_ids)))

    return Template(
        id=template_id,
        source=source,
        name=name.strip(),
        description=description,
        format_version=version,
        attributes=attributes,
        modules=modules,
        link_configurations=link_configurations,
        views=views,
    )
