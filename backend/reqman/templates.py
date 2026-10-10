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

Format (``format_version`` 2; version 1 is read and migrated, see below)::

    {
      "format_version": 1,
      "id": "<stem>",
      "name": "...",
      "description": "...",
      "attributes": [{"id", "name", "type", "required", "default", "choices", "modules"?, "module_types"?}],
      "modules": [{"key", "name", "prefix", "description", "type"?, "module_template"?, "skeleton"?}],
      "module_tree"?: [{"module": "<key>"} | {"folder": "<key>", "name", "children": [...]}],
      "link_configurations": [{"key", "name", "source_module", "target_module"}],
      "views": [{"name", "attribute_ids", "column_widths"?, "single_line"?, "modules"?}],
      "row_styles"?: [{"attribute_id", "value", "font_color", "background_color", "bold"}]
    }

``attributes`` never lists the built-in ``object_text``: every project
already has it. A view's ``attribute_ids`` entry is ``object_text``, a
template attribute id, or ``link:<key>`` for the mirror attribute of the
link configuration with that key. ``attribute_ids`` is ordered (the view's
column order). A view's optional ``column_widths`` (column key to width in
pixels) and ``single_line`` (column keys shown without wrapping) are keyed
by ``"@id"`` (the Id column) or one of the view's ``attribute_ids``
entries; like ``modules`` they were added without a ``format_version`` bump
(an older ReqManTool creates the view without them) and are written only
when set. Module template views take them too. A project template view's
optional ``modules`` lists the module keys it applies to (absent or empty:
every module), like an attribute's ``modules`` (no bump; written only when
non-empty).

An attribute's optional ``modules`` lists the module *keys* of this template
the attribute applies to (absent or empty: every module). It was added
without a ``format_version`` bump under the rule below: an older ReqManTool
ignores it and creates the attribute for every module. It is written only
when non-empty. Only an optional attribute may have one: a required
attribute applies to every module, so ``ProjectService`` reports a template
with a required, scoped attribute as invalid.

An attribute's optional ``module_types`` lists module types
(``requirements``, ``test_cases``, ``test_execution``,
``operational_scenarios``) the attribute also
applies to, and a module's optional ``type`` is one of those
(absent: ``requirements``). Both were added without a ``format_version``
bump under the rule below: an older ReqManTool ignores them and creates
requirements modules and unscoped attributes. Both are written only when
they differ from the default. Values are checked by ``ProjectService``.

The optional ``row_styles`` list styles rows by enum attribute value: a
template attribute ``attribute_id``, one of its choices as ``value``,
``#rrggbb`` or null colors and a boolean ``bold`` (see
``ProjectService.set_row_styles``). Like ``modules`` it was added without a
``format_version`` bump (an older ReqManTool creates the project unstyled)
and is written only when non-empty.

The optional ``module_tree`` arranges the modules in nested folders, in
display order: ``{"module": "<module key>"}`` places a module, and
``{"folder": "<folder key>", "name": "...", "children": [...]}`` a folder
holding further entries. Folder keys are template-local (a project gets
server-allocated folder ids slugged from the names); every module key and
folder key appears at most once, and modules it does not place follow at
the top level in ``modules`` order. Like ``row_styles`` it was added
without a ``format_version`` bump (an older ReqManTool creates the modules
without folders, in ``modules`` order, which "Save as template" writes in
tree order) and is written only when there is a folder. Folder names,
depth and sibling uniqueness are checked by ``ProjectService``.

A module's optional ``module_template`` (version 2) names a module template
(``builtin:<stem>`` or ``user:<stem>``, see "Module templates" below)
instead of repeating it: the module's ``name``, ``prefix``, ``description``
and ``type`` default to the module template's (each may still be given,
except a ``type`` other than the module template's), the module template's
attributes are merged into the project like "Add module from template" does
(``ProjectService._merge_template_attributes``), its view is added unless a
view of that name exists, and its heading skeleton is created. Because
``name`` and ``prefix`` may then be omitted, which a version 1 reader would
reject, this needed ``format_version`` 2. A module's optional ``skeleton``
(version 2) is a heading skeleton (see below) that replaces the module
template's (``[]``: none).

Version 1 files are still read: ``migrate_v1`` turns one into version 2
in memory (a version 1 reader ignored unknown keys, so ``module_template``
and ``skeleton`` keys in a version 1 file are dropped). Files on disk are
never rewritten; templates saved or imported now are written as version 2.

Heading skeletons: a list of ``{"heading": "...", "text"?: "...",
"children"?: [...]}`` nodes. When the module is created, every node becomes a
requirement with Object Type = Heading (in tree order, ids allocated by the
server), its optional ``text`` a first child with Object Type = Information.

Module templates (``kind`` ``"module"``) describe one module and live in a
``modules`` subfolder: ``backend/reqman/templates/modules/<stem>.json`` and
``<data-directory>/_templates/modules/<stem>.json``; ids are namespaced the
same way (their own namespace: a module template and a project template may
share a stem). Format (``format_version`` 1)::

    {
      "format_version": 1,
      "kind": "module",
      "id": "<stem>",
      "name": "...",
      "description": "...",
      "module": {"name", "prefix", "description", "type"?},
      "attributes": [{"id", "name", "type", "default", "choices"}],
      "view"?: {"name", "attribute_ids", "column_widths"?, "single_line"?},
      "skeleton": [<heading node>, ...]
    }

A module template's attributes are the attributes that apply to the module;
there is no ``required`` (merged attributes are always optional, see
``ProjectService._merge_template_attributes``) and no link configuration
(cross-project or otherwise). ``view.attribute_ids`` name ``object_text`` or
the template's attribute ids.

Compatibility rules:

- Unknown keys are ignored at every level, so a later *optional* key (for
  example a module ``type``) can be added without bumping
  ``format_version``; an older ReqManTool simply ignores it.
- ``format_version`` is bumped only for a change an older reader would
  misinterpret. A template whose ``format_version`` is missing, not an
  integer, or newer than this ReqManTool supports is reported as invalid,
  never guessed at.

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

FORMAT_VERSION = 2
# Project template versions this ReqManTool reads (older ones are migrated).
SUPPORTED_FORMAT_VERSIONS = (1, 2)
MODULE_FORMAT_VERSION = 1
MODULE_TEMPLATE_KIND = "module"

# Most heading nodes one skeleton may have, and the longest heading/text.
MAX_SKELETON_NODES = 2000
MAX_SKELETON_TEXT_LENGTH = 10_000

# Upper bound for an imported template file ("Load template ▸ Import"). Real
# templates are a few KiB; the limit only guards against a wrong file.
MAX_TEMPLATE_IMPORT_BYTES = 1024 * 1024

# Deepest JSON nesting a template file may have (the top-level object is
# depth 1). The format needs 4 (template → "attributes" → attribute →
# "choices"); unknown keys may nest further, up to this limit. ``json.loads``
# accepts ~100k levels, far more than ``json.dumps`` or ``repr`` (used in
# error messages) can handle, so a deeper file is rejected right after parsing.
MAX_TEMPLATE_JSON_DEPTH = 100

BUILTIN_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
# Module templates: a subfolder of the (built-in or user) templates folder.
MODULE_TEMPLATES_SUBDIR = "modules"

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
    # Module keys of the template the attribute applies to; empty = every module.
    modules: list[str] = field(default_factory=list)
    # Module types the attribute applies to (with ``modules``: either one).
    module_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "choices": self.choices,
        }
        if self.modules:
            data["modules"] = list(self.modules)
        if self.module_types:
            data["module_types"] = list(self.module_types)
        return data


@dataclass
class SkeletonNode:
    """One heading of a heading skeleton, with optional informative text."""

    heading: str
    text: str | None = None
    children: list[SkeletonNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {"heading": self.heading}
        if self.text is not None:
            data["text"] = self.text
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data


def skeleton_size(nodes: list[SkeletonNode]) -> int:
    """The number of heading nodes in ``nodes`` (all levels)."""
    count = 0
    stack = list(nodes)
    while stack:
        node = stack.pop()
        count += 1
        stack.extend(node.children)
    return count


@dataclass
class TemplateModule:
    key: str
    # None only with ``module_template`` (then taken from the module template).
    name: str | None
    prefix: Any
    description: str | None = ""
    # A module type; None (absent) means ``requirements`` (or the module template's).
    type: Any = None
    # A module template id (format version 2), or None.
    module_template: str | None = None
    # A heading skeleton (format version 2); None: the module template's (or none).
    skeleton: list[SkeletonNode] | None = None

    def to_dict(self) -> dict:
        data: dict[str, Any] = {"key": self.key}
        if self.name is not None:
            data["name"] = self.name
        if self.prefix is not None:
            data["prefix"] = self.prefix
        if self.description is not None:
            data["description"] = self.description
        if self.type not in (None, "requirements") or (self.module_template and self.type is not None):
            data["type"] = self.type
        if self.module_template is not None:
            data["module_template"] = self.module_template
        if self.skeleton is not None and (self.skeleton or self.module_template is not None):
            data["skeleton"] = [n.to_dict() for n in self.skeleton]
        return data


@dataclass
class TemplateFolder:
    """A folder of a template's ``module_tree``: entries are module keys (``str``) and folders."""

    key: str
    name: str
    children: list[str | TemplateFolder] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"folder": self.key, "name": self.name, "children": tree_to_list(self.children)}


def tree_to_list(entries: list) -> list[dict]:
    """``module_tree`` entries as JSON."""
    return [e.to_dict() if isinstance(e, TemplateFolder) else {"module": e} for e in entries]


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
    # Ordered: the view's column order.
    attribute_ids: list[str] = field(default_factory=list)
    # Optional shared layout, keyed by ``"@id"`` (the Id column) or an
    # ``attribute_ids`` reference; written only when set (no version bump:
    # an older ReqManTool ignores them).
    column_widths: dict[str, int] | None = None
    single_line: list[str] | None = None
    # Project templates only: the module keys the view applies to (empty: every module).
    modules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data: dict[str, Any] = {"name": self.name, "attribute_ids": list(self.attribute_ids)}
        if self.column_widths:
            data["column_widths"] = dict(self.column_widths)
        if self.single_line:
            data["single_line"] = list(self.single_line)
        if self.modules:
            data["modules"] = list(self.modules)
        return data


def _parse_view_layout(raw: dict, where: str) -> tuple[dict[str, int] | None, list[str] | None]:
    """The shape-checked optional ``column_widths`` and ``single_line`` of a template view.

    Keys and width bounds are checked by ``ProjectService`` against the view's columns.
    """
    widths = raw.get("column_widths")
    if widths is not None and (
        not isinstance(widths, dict)
        or not all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in widths.items())
    ):
        raise ValueError(f"{where}: 'column_widths' must be an object mapping column keys to integer widths")
    single_line = raw.get("single_line")
    if single_line is not None and (
        not isinstance(single_line, list) or not all(isinstance(k, str) for k in single_line)
    ):
        raise ValueError(f"{where}: 'single_line' must be a list of column keys")
    return (dict(widths) if widths else None), (list(single_line) if single_line else None)


@dataclass
class TemplateRowStyle:
    attribute_id: str
    value: str
    font_color: str | None = None
    background_color: str | None = None
    bold: bool = False

    def to_dict(self) -> dict:
        return {
            "attribute_id": self.attribute_id,
            "value": self.value,
            "font_color": self.font_color,
            "background_color": self.background_color,
            "bold": self.bold,
        }


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
    row_styles: list[TemplateRowStyle] = field(default_factory=list)
    # Module keys and folders; empty = no folders (modules in ``modules`` order).
    module_tree: list[str | TemplateFolder] = field(default_factory=list)

    @property
    def local_id(self) -> str:
        return split_template_id(self.id)[1]

    def to_dict(self) -> dict:
        """The on-disk JSON shape (key order is the file's key order)."""
        data = {
            "format_version": self.format_version,
            "id": self.local_id,
            "name": self.name,
            "description": self.description,
            "attributes": [a.to_dict() for a in self.attributes],
            "modules": [m.to_dict() for m in self.modules],
        }
        if any(isinstance(e, TemplateFolder) for e in self.module_tree):
            data["module_tree"] = tree_to_list(self.module_tree)
        data |= {
            "link_configurations": [c.to_dict() for c in self.link_configurations],
            "views": [v.to_dict() for v in self.views],
        }
        if self.row_styles:
            data["row_styles"] = [s.to_dict() for s in self.row_styles]
        return data


@dataclass
class ModuleTemplateModule:
    """The module a module template creates (defaults; "Add module from template" may override)."""

    name: str
    prefix: str
    description: str = ""
    type: Any = None

    def to_dict(self) -> dict:
        data = {"name": self.name, "prefix": self.prefix, "description": self.description}
        if self.type not in (None, "requirements"):
            data["type"] = self.type
        return data


@dataclass
class ModuleTemplateAttribute:
    """An attribute that applies to a module template's module (always optional)."""

    id: str
    name: Any
    type: Any = "string"
    default: Any = None
    choices: Any = None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "type": self.type, "default": self.default, "choices": self.choices}


@dataclass
class ModuleTemplate:
    id: str  # namespaced: "builtin:<stem>" or "user:<stem>"
    source: str
    name: str
    module: ModuleTemplateModule
    description: str = ""
    format_version: int = MODULE_FORMAT_VERSION
    attributes: list[ModuleTemplateAttribute] = field(default_factory=list)
    view: TemplateView | None = None
    skeleton: list[SkeletonNode] = field(default_factory=list)

    @property
    def local_id(self) -> str:
        return split_template_id(self.id)[1]

    def to_dict(self) -> dict:
        """The on-disk JSON shape (key order is the file's key order)."""
        data: dict[str, Any] = {
            "format_version": self.format_version,
            "kind": MODULE_TEMPLATE_KIND,
            "id": self.local_id,
            "name": self.name,
            "description": self.description,
            "module": self.module.to_dict(),
            "attributes": [a.to_dict() for a in self.attributes],
        }
        if self.view is not None:
            data["view"] = self.view.to_dict()
        data["skeleton"] = [n.to_dict() for n in self.skeleton]
        return data


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


def _check_format_version(data: dict, current: int = FORMAT_VERSION, supported: tuple[int, ...] = SUPPORTED_FORMAT_VERSIONS) -> int:
    version = data.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError(f"'format_version' must be the integer {current}, got {version!r}")
    if version > current:
        raise ValueError(
            f"format_version {version} is newer than this ReqManTool supports ({current}); "
            "update ReqManTool to use this template"
        )
    if version not in supported:
        raise ValueError(f"Unsupported format_version {version}; expected {current}")
    return version


def migrate_v1(data: dict) -> dict:
    """A version 1 project template as version 2 (a shallow copy; ``data`` is not changed).

    Version 2 only added optional module keys, so the migration keeps
    everything except module ``module_template`` and ``skeleton`` keys: a
    version 1 reader ignored unknown keys, so they never meant anything there.
    """
    migrated = dict(data)
    migrated["format_version"] = FORMAT_VERSION
    modules = data.get("modules")
    if isinstance(modules, list):
        migrated["modules"] = [
            {k: v for k, v in m.items() if k not in ("module_template", "skeleton")} if isinstance(m, dict) else m
            for m in modules
        ]
    return migrated


def parse_template_text(text: str, template_id: str) -> Template:
    """Parse JSON text into a ``Template``; raises ``ValueError`` describing the first problem.

    A leading UTF-8 byte order mark (as some editors save files) is ignored.
    JSON nested deeper than ``MAX_TEMPLATE_JSON_DEPTH`` (or too deeply for
    the parser), and strings that cannot be encoded as UTF-8 (a lone
    surrogate escape such as ``"\\ud800"``), are rejected so that such a
    template is never written, listed or used.
    """
    text = text.removeprefix("\ufeff")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise ValueError(_TOO_DEEP) from exc
    _check_json_values(data)
    return parse_template(data, template_id)


_TOO_DEEP = f"nested too deeply (more than {MAX_TEMPLATE_JSON_DEPTH} levels)"


def _check_json_values(data: Any) -> None:
    """Reject nesting deeper than ``MAX_TEMPLATE_JSON_DEPTH`` and unencodable strings.

    Walks the parsed data with an explicit stack, in document order, so it
    never recurses itself. Bounding the depth here keeps every later step
    (validation and its error messages, writing) safe from ``RecursionError``.
    """
    stack: list[tuple[Any, int]] = [(data, 1)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, str):
            _check_utf8_encodable(value)
        elif isinstance(value, (dict, list)):
            if depth > MAX_TEMPLATE_JSON_DEPTH:
                raise ValueError(_TOO_DEEP)
            if isinstance(value, dict):
                for key, item in reversed(value.items()):
                    stack.append((item, depth + 1))
                    stack.append((key, depth + 1))
            else:
                stack.extend((item, depth + 1) for item in reversed(value))


def _check_utf8_encodable(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        code_point = ord(exc.object[exc.start])
        raise ValueError(
            f"contains a character that cannot be encoded as UTF-8 (unpaired surrogate \\u{code_point:04x})"
        ) from exc


def parse_template(data: Any, template_id: str) -> Template:
    """Check a template's shape and return it; unknown keys are ignored."""
    source, _ = split_template_id(template_id)
    if not isinstance(data, dict):
        raise ValueError("a template must be a JSON object")
    if data.get("kind") == MODULE_TEMPLATE_KIND:
        raise ValueError("this is a module template, not a project template")
    version = _check_format_version(data)
    if version == 1:
        data = migrate_v1(data)
        version = FORMAT_VERSION

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
        scope = raw.get("modules", [])
        if scope is None:
            scope = []
        if not isinstance(scope, list) or not all(_is_nonblank_str(k) for k in scope):
            raise ValueError(f"{where}: 'modules' must be a list of module keys")
        if len(set(scope)) != len(scope):
            raise ValueError(f"{where}: 'modules' lists a module key more than once")
        type_scope = raw.get("module_types", [])
        if type_scope is None:
            type_scope = []
        if not isinstance(type_scope, list) or not all(isinstance(t, str) for t in type_scope):
            raise ValueError(f"{where}: 'module_types' must be a list of module types")
        if len(set(type_scope)) != len(type_scope):
            raise ValueError(f"{where}: 'module_types' lists a module type more than once")
        attributes.append(
            TemplateAttribute(
                id=attr_id,
                name=raw.get("name"),
                type=raw.get("type", "string"),
                required=required,
                default=raw.get("default"),
                choices=raw.get("choices"),
                modules=list(scope),
                module_types=list(type_scope),
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
        reference = raw.get("module_template")
        if reference is not None:
            try:
                split_template_id(reference)
            except ValueError as exc:
                raise ValueError(f"{where}: 'module_template': {exc}") from exc
        # With a module template, name/prefix/description default to its own.
        module_name = raw.get("name")
        if module_name is None and reference is not None:
            pass
        elif not _is_nonblank_str(module_name):
            raise ValueError(f"{where}: 'name' must be a non-empty string")
        else:
            module_name = module_name.strip()
            if module_name.casefold() in module_names:
                raise ValueError(f"{where}: duplicate module name '{module_name}'")
            module_names.add(module_name.casefold())
        module_description = raw.get("description", None if reference is not None else "")
        if module_description is None and reference is None:
            module_description = ""
        if module_description is not None and not isinstance(module_description, str):
            raise ValueError(f"{where}: 'description' must be a string")
        prefix = raw.get("prefix")
        if not isinstance(prefix, str) and not (prefix is None and reference is not None):
            raise ValueError(f"{where}: 'prefix' must be a string")
        module_type = raw.get("type")
        if module_type is not None and not isinstance(module_type, str):
            raise ValueError(f"{where}: 'type' must be a module type string")
        skeleton = parse_skeleton(raw["skeleton"], f"{where}.skeleton") if "skeleton" in raw else None
        module_keys.add(key)
        modules.append(
            TemplateModule(
                key=key,
                name=module_name,
                prefix=prefix,
                description=module_description,
                type=module_type,
                module_template=reference,
                skeleton=skeleton,
            )
        )

    for index, attr in enumerate(attributes):
        for key in attr.modules:
            if key not in module_keys:
                raise ValueError(f"attributes[{index}]: 'modules' entry {key!r} is not a module key of this template")

    module_tree = _parse_module_tree(data.get("module_tree"), module_keys)

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
        widths, single_line = _parse_view_layout(raw, where)
        view_modules = raw.get("modules", [])
        if not isinstance(view_modules, list):
            raise ValueError(f"{where}: 'modules' must be a list of module keys")
        for key in view_modules:
            if not isinstance(key, str) or key not in module_keys:
                raise ValueError(f"{where}: 'modules' entry {key!r} is not a module key of this template")
        views.append(
            TemplateView(
                name=view_name,
                attribute_ids=list(attribute_ids),
                column_widths=widths,
                single_line=single_line,
                modules=list(dict.fromkeys(view_modules)),
            )
        )

    row_styles: list[TemplateRowStyle] = []
    for index, raw in enumerate(_list_of_dicts(data, "row_styles")):
        where = f"row_styles[{index}]"
        attr_id = raw.get("attribute_id")
        if not _is_nonblank_str(attr_id):
            raise ValueError(f"{where}: 'attribute_id' must be a non-empty string")
        value = raw.get("value")
        if not isinstance(value, str):
            raise ValueError(f"{where}: 'value' must be a string")
        for key in ("font_color", "background_color"):
            if raw.get(key) is not None and not isinstance(raw.get(key), str):
                raise ValueError(f"{where}: '{key}' must be a color string or null")
        bold = raw.get("bold", False)
        if not isinstance(bold, bool):
            raise ValueError(f"{where}: 'bold' must be true or false")
        row_styles.append(
            TemplateRowStyle(
                attribute_id=attr_id,
                value=value,
                font_color=raw.get("font_color"),
                background_color=raw.get("background_color"),
                bold=bold,
            )
        )

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
        row_styles=row_styles,
        module_tree=module_tree,
    )


def _parse_module_tree(value: Any, module_keys: set[str]) -> list[str | TemplateFolder]:
    """Check the optional ``module_tree`` shape; module entries must name module keys, once each."""
    if value is None:
        return []
    placed: set[str] = set()
    folder_keys: set[str] = set()

    def parse(items: Any, where: str) -> list[str | TemplateFolder]:
        if not isinstance(items, list):
            raise ValueError(f"'{where}' must be a list")
        result: list[str | TemplateFolder] = []
        for index, item in enumerate(items):
            at = f"{where}[{index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{at} must be an object")
            if "module" in item:
                key = item["module"]
                if not isinstance(key, str) or key not in module_keys:
                    raise ValueError(f"{at}: 'module' {key!r} is not a module key of this template")
                if key in placed:
                    raise ValueError(f"{at}: module key '{key}' is placed more than once")
                placed.add(key)
                result.append(key)
            elif "folder" in item:
                key = item["folder"]
                if not _is_nonblank_str(key):
                    raise ValueError(f"{at}: 'folder' must be a non-empty string")
                if key in folder_keys:
                    raise ValueError(f"{at}: duplicate folder key '{key}'")
                folder_keys.add(key)
                folder_name = item.get("name")
                if not _is_nonblank_str(folder_name):
                    raise ValueError(f"{at}: 'name' must be a non-empty string")
                children = item.get("children", [])
                result.append(
                    TemplateFolder(
                        key=key,
                        name=folder_name.strip(),
                        children=parse([] if children is None else children, f"{at}.children"),
                    )
                )
            else:
                raise ValueError(f"{at} must have a 'module' or a 'folder' key")
        return result

    # Nesting is bounded by MAX_TEMPLATE_JSON_DEPTH (checked before parsing), so this recursion is too.
    return parse(value, "module_tree")


# --- heading skeletons and module templates ------------------------------------


def parse_skeleton(value: Any, where: str = "skeleton") -> list[SkeletonNode]:
    """Check a heading skeleton's shape (``None``: empty) and return its nodes.

    Nesting is bounded by ``MAX_TEMPLATE_JSON_DEPTH`` (checked when a file is
    parsed) and the node count by ``MAX_SKELETON_NODES``.
    """
    if value is None:
        return []
    count = [0]

    def parse(items: Any, at: str) -> list[SkeletonNode]:
        if not isinstance(items, list):
            raise ValueError(f"'{at}' must be a list")
        nodes: list[SkeletonNode] = []
        for index, item in enumerate(items):
            here = f"{at}[{index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{here} must be an object")
            count[0] += 1
            if count[0] > MAX_SKELETON_NODES:
                raise ValueError(f"'{where}' has more than {MAX_SKELETON_NODES} headings")
            heading = item.get("heading")
            if not _is_nonblank_str(heading):
                raise ValueError(f"{here}: 'heading' must be a non-empty string")
            text = item.get("text")
            if text is not None and not isinstance(text, str):
                raise ValueError(f"{here}: 'text' must be a string")
            if text is not None and not text.strip():
                text = None
            for label, string in (("heading", heading), ("text", text)):
                if string is not None and len(string) > MAX_SKELETON_TEXT_LENGTH:
                    raise ValueError(f"{here}: '{label}' is longer than {MAX_SKELETON_TEXT_LENGTH} characters")
            children = parse(item["children"], f"{here}.children") if item.get("children") is not None else []
            nodes.append(SkeletonNode(heading=heading.strip(), text=text, children=children))
        return nodes

    return parse(value, where)


def list_builtin_module_template_ids(directory: Path = BUILTIN_TEMPLATES_DIR) -> list[str]:
    return list_builtin_template_ids(directory / MODULE_TEMPLATES_SUBDIR)


def read_builtin_module_template_text(local_id: str, directory: Path = BUILTIN_TEMPLATES_DIR) -> str:
    modules_dir = directory / MODULE_TEMPLATES_SUBDIR
    if local_id not in list_builtin_template_ids(modules_dir):
        raise FileNotFoundError(f"Module template '{make_template_id(BUILTIN_SOURCE, local_id)}' not found")
    return (modules_dir / f"{local_id}.json").read_text(encoding="utf-8-sig")


def parse_module_template_text(text: str, template_id: str) -> ModuleTemplate:
    """Parse JSON text into a ``ModuleTemplate`` (same JSON checks as ``parse_template_text``)."""
    text = text.removeprefix("\ufeff")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise ValueError(_TOO_DEEP) from exc
    _check_json_values(data)
    return parse_module_template(data, template_id)


def parse_module_template(data: Any, template_id: str) -> ModuleTemplate:
    """Check a module template's shape and return it; unknown keys are ignored."""
    source, _ = split_template_id(template_id)
    if not isinstance(data, dict):
        raise ValueError("a module template must be a JSON object")
    if data.get("kind") != MODULE_TEMPLATE_KIND:
        raise ValueError(f"'kind' must be '{MODULE_TEMPLATE_KIND}' (this is not a module template)")
    version = _check_format_version(data, MODULE_FORMAT_VERSION, (MODULE_FORMAT_VERSION,))

    name = data.get("name")
    if not _is_nonblank_str(name):
        raise ValueError("'name' must be a non-empty string")
    description = data.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise ValueError("'description' must be a string")

    raw_module = data.get("module")
    if not isinstance(raw_module, dict):
        raise ValueError("'module' must be an object")
    module_name = raw_module.get("name")
    if not _is_nonblank_str(module_name):
        raise ValueError("module: 'name' must be a non-empty string")
    prefix = raw_module.get("prefix")
    if not isinstance(prefix, str):
        raise ValueError("module: 'prefix' must be a string")
    module_description = raw_module.get("description", "")
    if module_description is None:
        module_description = ""
    if not isinstance(module_description, str):
        raise ValueError("module: 'description' must be a string")
    module_type = raw_module.get("type")
    if module_type is not None and not isinstance(module_type, str):
        raise ValueError("module: 'type' must be a module type string")

    attributes: list[ModuleTemplateAttribute] = []
    attribute_ids: set[str] = set()
    attribute_names: set[str] = set()
    for index, raw in enumerate(_list_of_dicts(data, "attributes")):
        where = f"attributes[{index}]"
        attr_id = raw.get("id")
        if not _is_nonblank_str(attr_id):
            raise ValueError(f"{where}: 'id' must be a non-empty string")
        if attr_id in attribute_ids:
            raise ValueError(f"{where}: duplicate attribute id '{attr_id}'")
        attr_name = raw.get("name")
        if not _is_nonblank_str(attr_name):
            raise ValueError(f"{where}: 'name' must be a non-empty string")
        if attr_name.strip().casefold() in attribute_names:
            raise ValueError(f"{where}: duplicate attribute name '{attr_name.strip()}'")
        if raw.get("system", False):
            raise ValueError(f"{where}: template attributes cannot be system attributes")
        attribute_ids.add(attr_id)
        attribute_names.add(attr_name.strip().casefold())
        attributes.append(
            ModuleTemplateAttribute(
                id=attr_id,
                name=attr_name.strip(),
                type=raw.get("type", "string"),
                default=raw.get("default"),
                choices=raw.get("choices"),
            )
        )

    view: TemplateView | None = None
    raw_view = data.get("view")
    if raw_view is not None:
        if not isinstance(raw_view, dict):
            raise ValueError("'view' must be an object or null")
        view_name = raw_view.get("name")
        if not _is_nonblank_str(view_name):
            raise ValueError("view: 'name' must be a non-empty string")
        refs = raw_view.get("attribute_ids", [])
        if not isinstance(refs, list) or not all(isinstance(a, str) for a in refs):
            raise ValueError("view: 'attribute_ids' must be a list of strings")
        for ref in refs:
            if ref != "object_text" and ref not in attribute_ids:
                raise ValueError(
                    f"view: unknown attribute '{ref}' (expected 'object_text' or a template attribute id)"
                )
        widths, single_line = _parse_view_layout(raw_view, "view")
        view = TemplateView(
            name=view_name.strip(), attribute_ids=list(refs), column_widths=widths, single_line=single_line
        )

    return ModuleTemplate(
        id=template_id,
        source=source,
        name=name.strip(),
        description=description,
        format_version=version,
        module=ModuleTemplateModule(
            name=module_name.strip(), prefix=prefix, description=module_description, type=module_type
        ),
        attributes=attributes,
        view=view,
        skeleton=parse_skeleton(data.get("skeleton"), "skeleton"),
    )
