"""Read a ReqIF XML document back into the plain data a ReqMan import needs.

This module is the counterpart of ``reqif_export.py``: it parses a ReqIF
document with the ``reqif`` package and returns the project, modules,
requirements and attribute definitions it describes, with XHTML attribute values
converted back to ReqMan's rich text format (``richtext.py``) so they can be
edited in the frontend.

It has no dependency on ``reqman.models`` / ``reqman.service`` on purpose (to
avoid circular imports) and returns plain dataclasses; ``ProjectService``
turns them into stored data.

Mapping decisions (the inverse of what ``reqif_export.py`` writes):

- Attribute definitions: one SPEC-OBJECT-TYPE is assumed (as the export writes
  one per project); each ATTRIBUTE-DEFINITION-* becomes an
  ``ImportedAttributeDefinition`` whose type comes from its
  ``DATATYPE-DEFINITION-*-REF``: XHTML -> "text", STRING -> "string", INTEGER ->
  "integer", BOOLEAN -> "boolean", DATE -> "date". AN ENUMERATION datatype is
  reported as "enum" but its values are not read (see ``_read_enum_values``).
- Modules: one SPECIFICATION per ``ImportedModule``, in document order; the
  SPEC-HIERARCHY tree becomes ``parent_id`` links (an item nested under
  another).
- Requirements: one SPEC-OBJECT per requirement, with its attribute values
  keyed by ATTRIBUTE-DEFINITION identifier. Values are typed like the service
  types them: integers as ``int``, booleans as ``bool``, dates as
  ``YYYY-MM-DD``, XHTML as rich text (or plain text if the XHTML holds no
  formatting).
- The identifiers in the file are kept as-is (a caller decides how to map
  them onto its own ids); ReqManTool's export keeps requirement ids and module
  ids stable, so a round trip through this module and back is lossless.

Known limitations (the ReqIF format is much wider than what ReqManTool
exports):

- Only the first SPEC-OBJECT-TYPE is read; a file with several (one per tool
  area) reads just that one's attributes.
- ENUMERATION attribute values and SPECIFIED-VALUES are not imported: an enum
  attribute comes in without its choices, and its values as plain strings.
  ``_read_enum_values`` is the place to add that when a source tool needs it.
- SPEC-RELATIONs (link configurations' pairs) are not imported: the format
  does not carry ReqMan's link configuration names.
- A SPEC-OBJECT no SPEC-HIERARCHY of its module refers to is not imported
  (there is no place for it in the hierarchy); one referred to twice is read
  once.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from reqif.parser import ReqIFParser
from reqif.reqif_bundle import ReqIFBundle

from . import richtext

_XHTML = "{http://www.w3.org/1999/xhtml}"

# The xhtml: prefix the export writes on every element. ``_strip_prefix``
# removes it so ``richtext.sanitize`` sees plain HTML.
_PREFIXED_TAG = re.compile(r"<(/?)xhtml:")
_SPACING_RUNS = re.compile(r"^ +| +$| {2,}")
_TAB = "\t"


# --- the plain data the service consumes --------------------------------------


@dataclass
class ImportedAttributeDefinition:
    """An ATTRIBUTE-DEFINITION of the file's SPEC-OBJECT-TYPE."""

    id: str
    name: str
    type: str  # "string" | "text" | "integer" | "boolean" | "date"
    required: bool = False
    default: Any = None
    choices: list[str] | None = None  # only for "enum", currently always None


@dataclass
class ImportedRequirement:
    """A SPEC-OBJECT, with its values keyed by ATTRIBUTE-DEFINITION identifier."""

    id: str
    attributes: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None  # another requirement's id in the same module


@dataclass
class ImportedModule:
    """A SPECIFICATION and the requirements its SPEC-HIERARCHY tree holds."""

    id: str
    name: str
    description: str = ""
    requirements: list[ImportedRequirement] = field(default_factory=list)


@dataclass
class ImportedProject:
    """A whole ReqIF document: the project and everything it holds."""

    id: str
    name: str
    description: str = ""
    attributes: list[ImportedAttributeDefinition] = field(default_factory=list)
    modules: list[ImportedModule] = field(default_factory=list)


# --- XHTML -> rich text ---------------------------------------------------------


def _visible_spaces(text: str) -> str:
    """``text`` with the spacing ``reqif_export`` made visible turned back into real spaces.

    ``reqif_export`` writes edge spaces and every second space of a run as
    ``&#160;`` (XHTML collapses white space, so the export has to encode them)
    and a tab as four of them; this reverses that so the spacing of the value
    survives the round trip.
    """
    out = []
    for line in text.split("\n"):
        # ``_SPACING_RUNS`` finds the runs the export encoded; within each, every
        # other character is a space that was a real space.
        chars = [" "] * len(line)
        for match in _SPACING_RUNS.finditer(line):
            edge = match.start() == 0 or match.end() == len(line)
            for i in range(match.start(), match.end()):
                if edge or (i - match.start()) % 2 == 1:
                    chars[i] = " "
        # A tab was written as four spaces; without the export's own marker we
        # cannot tell it from four typed spaces, so it stays four spaces.
        out.append("".join(chars).replace(_TAB, " "))
    return "\n".join(out)


def _strip_prefix(markup: str) -> str:
    """``markup`` with the ``xhtml:`` element prefix removed."""
    return _PREFIXED_TAG.sub(r"<\1", markup)


def _div_content(markup: str) -> str | None:
    """The children markup of ``markup``'s single root ``div``, or None if it is not one.

    ReqIF XHTML values are one ``xhtml:div`` (as ``reqif_export`` writes);
    anything else (a bare text value, a fragment) is returned as None so the
    caller falls back to reading it as text. An empty div comes back as
    ``""`` — the ``reqif`` library writes ``<div/>`` for one, which is handled
    as the empty element it is.
    """
    stripped = markup.strip()
    for tag in ("xhtml:div", "div"):
        # A self-closing div holds nothing.
        if stripped in (f"<{tag}/>", f"<{tag} />"):
            return ""
        if stripped.startswith(f"<{tag}") and stripped.endswith(f"</{tag}>"):
            start = stripped.index(">") + 1
            return stripped[start : -len(f"</{tag}>")]
    return None


def xhtml_to_richtext(value: str) -> str:
    """A ReqIF ATTRIBUTE-VALUE-XHTML THE-VALUE as a ReqMan rich text value.

    A value holding only paragraphs of unformatted text comes back as plain
    text (like a value never formatted in the editor), so an imported plain
    requirement stays plain text. Anything else comes back as rich text, with
    the ``xhtml:`` prefix removed and the export's visible spacing decoded.
    """
    content = _div_content(value)
    if content is None:
        # Not a div: treat the whole value as plain text (escaped markup and all).
        return richtext.from_plain(value)
    markup = _strip_prefix(content)
    # ``&#160;`` (non-breaking space) is what XHTML uses for a visible space;
    # decode it back to a real space before sanitising.
    markup = markup.replace("&#160;", " ").replace("&nbsp;", " ")
    return richtext.sanitize(richtext.RICH_TEXT_PREFIX + markup + richtext.RICH_TEXT_SUFFIX)


# --- ReqIF -> the plain data ----------------------------------------------------

# Our attribute type per reqif datatype reference.
_DATATYPE_TYPES = {
    "DATATYPE-XHTML": "text",
    "DATATYPE-STRING": "string",
    "DATATYPE-INTEGER": "integer",
    "DATATYPE-BOOLEAN": "boolean",
    "DATATYPE-DATE": "date",
}


def _datatype_ref(attribute: Any) -> str:
    """The ``DATATYPE-DEFINITION-*-REF`` of a SPEC-ATTRIBUTE-DEFINITION."""
    return str(getattr(attribute, "datatype_definition", "") or "")


def _attribute_type(attribute: Any) -> str:
    """Our attribute type for a SPEC-ATTRIBUTE-DEFINITION (from its datatype)."""
    reference = _datatype_ref(attribute)
    if "ENUMERATION" in reference:
        return "enum"
    return _DATATYPE_TYPES.get(reference, "string")


def _read_enum_values(bundle: ReqIFBundle, datatype_ref: str) -> list[str] | None:
    """The SPECIFIED-VALUES of a DATATYPE-DEFINITION-ENUMERATION.

    None today: the ``reqif`` package does not expose SPECIFIED-VALUES, so an
    enum attribute comes in without its choices. Kept as the single place to
    add that if a source tool needs it.
    """
    return None


def _read_attributes(spec_object_type: Any, bundle: ReqIFBundle) -> list[ImportedAttributeDefinition]:
    """The ATTRIBUTE-DEFINITIONs of a SPEC-OBJECT-TYPE."""
    result = []
    for attribute in getattr(spec_object_type, "attribute_definitions", None) or []:
        attribute_id = getattr(attribute, "identifier", None)
        if not attribute_id:
            continue
        attribute_type = _attribute_type(attribute)
        result.append(
            ImportedAttributeDefinition(
                id=attribute_id,
                name=getattr(attribute, "long_name", None) or attribute_id,
                type=attribute_type,
                required=bool(getattr(attribute, "multi_valued", False) and False),  # never required here
                choices=_read_enum_values(bundle, _datatype_ref(attribute)) if attribute_type == "enum" else None,
            )
        )
    return result


def _read_value(attribute: Any, value: Any) -> Any:
    """One ATTRIBUTE-VALUE as the Python value ReqMan stores for ``attribute``."""
    raw = getattr(value, "value", None)
    if raw is None:
        return None
    attribute_type = _attribute_type(attribute)
    if attribute_type == "text":
        return xhtml_to_richtext(raw)
    if attribute_type == "integer":
        try:
            return int(str(raw).strip())
        except ValueError:
            return 0
    if attribute_type == "boolean":
        return str(raw).strip().lower() in ("true", "1")
    if attribute_type == "date":
        # Written as an xsd:dateTime (YYYY-MM-DDT00:00:00); we store the date.
        text = str(raw).strip()
        return text.split("T")[0] if "T" in text else text
    return raw


def _read_attributes_of(
    spec_object: Any, spec_object_type: Any,
) -> dict[str, Any]:
    """``spec_object``'s values keyed by attribute id, typed like the service types them."""
    by_id = {getattr(a, "identifier", None): a for a in getattr(spec_object_type, "attribute_definitions", None) or []}
    result: dict[str, Any] = {}
    for value in getattr(spec_object, "attributes", None) or []:
        attribute_id = getattr(value, "definition_ref", None)
        attribute = by_id.get(attribute_id)
        if attribute is None:
            continue
        result[attribute_id] = _read_value(attribute, value)
    return result


def _first_spec_object_type(bundle: ReqIFBundle) -> Any:
    for spec_type in _spec_types(bundle):
        if hasattr(spec_type, "attribute_definitions"):  # a ReqIFSpecObjectType
            return spec_type
    return None


def _spec_types(bundle: ReqIFBundle) -> list[Any]:
    if bundle.core_content is None or bundle.core_content.req_if_content is None:
        return []
    return list(getattr(bundle.core_content.req_if_content, "spec_types", None) or [])


def _read_hierarchy(
    specification: Any,
    spec_object_type: Any,
    spec_objects: dict[str, Any],
) -> list[ImportedRequirement]:
    """The requirements of one SPECIFICATION, in SPEC-HIERARCHY order (parents before children).

    A SPEC-OBJECT the hierarchy does not reach is not imported; one reached
    twice is read once (the first occurrence wins).
    """
    result: list[ImportedRequirement] = []
    seen: set[str] = set()

    def walk(hierarchies: Iterable[Any], parent_id: str | None) -> None:
        for hierarchy in hierarchies:
            object_ref = getattr(hierarchy, "spec_object", None)
            if not object_ref or object_ref in seen:
                continue
            spec_object = spec_objects.get(object_ref)
            if spec_object is None:
                continue
            seen.add(object_ref)
            result.append(
                ImportedRequirement(
                    id=object_ref,
                    attributes=_read_attributes_of(spec_object, spec_object_type) if spec_object_type else {},
                    parent_id=parent_id,
                )
            )
            walk(getattr(hierarchy, "children", None) or [], object_ref)

    walk(getattr(specification, "children", None) or [], None)
    return result


def _read_modules(bundle: ReqIFBundle, spec_object_type: Any) -> list[ImportedModule]:
    """Every SPECIFICATION as a module with its hierarchy's requirements."""
    content = bundle.core_content.req_if_content if bundle.core_content is not None else None
    spec_objects: dict[str, Any] = {
        identifier: spec_object
        for spec_object in (getattr(content, "spec_objects", None) or [])
        if (identifier := getattr(spec_object, "identifier", None))
    }
    modules: list[ImportedModule] = []
    for specification in getattr(content, "specifications", None) or []:
        module = ImportedModule(
            id=getattr(specification, "identifier", "") or "",
            name=getattr(specification, "long_name", None) or getattr(specification, "identifier", "") or "Module",
            description=getattr(specification, "description", None) or "",
        )
        module.requirements = _read_hierarchy(specification, spec_object_type, spec_objects)
        modules.append(module)
    return modules


# --- the public entry points -----------------------------------------------------


def _parse(reqif_content: str) -> ReqIFBundle:
    if not reqif_content.strip():
        raise ValueError("The ReqIF document is empty")
    try:
        return ReqIFParser.parse_from_string(reqif_content)
    except Exception as exc:  # the parser raises many types (XML, schema, NotImplementedError)
        raise ValueError(f"Cannot read the ReqIF document: {exc}") from exc


def _to_project(bundle: ReqIFBundle) -> ImportedProject:
    """The whole bundle as an ``ImportedProject`` (raises if it has no header)."""
    header = bundle.req_if_header
    if header is None:
        raise ValueError("The ReqIF document has no header")

    spec_object_type = _first_spec_object_type(bundle)
    project = ImportedProject(
        id=getattr(header, "identifier", None) or "imported-project",
        name=getattr(header, "title", None) or "Imported project",
        # ReqIF has no project description in the header (COMMENT is a ReqIF
        # XML comment, not carried by the reqif library); ``reqif_export``
        # writes the project's description as the SPEC-OBJECT-TYPE's DESC, so
        # that is where a description comes from.
        description=getattr(spec_object_type, "description", None) or getattr(header, "comment", None) or "",
    )
    if spec_object_type is not None:
        project.attributes = _read_attributes(spec_object_type, bundle)
    project.modules = _read_modules(bundle, spec_object_type)
    return project


def import_reqif_from_string(reqif_content: str) -> ImportedProject:
    """Parse a ReqIF XML document (a string) into an ``ImportedProject``.

    Raises ``ValueError`` if the document cannot be parsed or holds no header.
    """
    return _to_project(_parse(reqif_content))


def import_reqif_from_file(path: Any) -> ImportedProject:
    """Parse a ReqIF XML file into an ``ImportedProject``.

    Raises ``ValueError`` if the file cannot be read or parsed.
    """
    try:
        # ``parse`` reads the file itself (and honours the XML declaration's
        # encoding), so a non-UTF-8 file with a proper declaration still works.
        bundle = ReqIFParser.parse(str(path))
    except Exception as exc:  # the parser raises many types (IO, XML, schema, NotImplementedError)
        raise ValueError(f"Cannot read the ReqIF file: {exc}") from exc
    return _to_project(bundle)
