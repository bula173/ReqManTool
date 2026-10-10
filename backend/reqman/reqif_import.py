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

- Attribute definitions: every SPEC-OBJECT-TYPE's ATTRIBUTE-DEFINITION-* becomes
  an ``ImportedAttributeDefinition`` whose type comes from its
  ``DATATYPE-DEFINITION-*-REF``: XHTML -> "text", STRING -> "string", INTEGER ->
  "integer", BOOLEAN -> "boolean", DATE -> "date", ENUMERATION -> "enum" with
  the datatype's SPECIFIED-VALUES as its choices. A file that declares several
  SPEC-OBJECT-TYPEs (one per tool area) reads all of them; a SPEC-OBJECT's
  values are typed against the type it names, falling back to the first type
  that defines them, so an object of a type the file does not declare still
  comes in rather than losing its values.
- Modules: one SPECIFICATION per ``ImportedModule``, in document order; the
  SPEC-HIERARCHY tree becomes ``parent_id`` links (an item nested under
  another).
- Requirements: one SPEC-OBJECT per requirement, with its attribute values
  keyed by ATTRIBUTE-DEFINITION identifier. Values are typed like the service
  types them: integers as ``int``, booleans as ``bool``, dates as
  ``YYYY-MM-DD``, enumerations as the choice its ENUM-VALUE-REF names, and
  XHTML as rich text (or plain text if the XHTML holds no formatting).
- Links: one SPEC-RELATION per ``ImportedLink``, named after its
  SPEC-RELATION-TYPE (what ``reqif_export`` writes as the link configuration's
  name), so a document ReqManTool exported comes back with the same
  configuration names and pairs.
- The identifiers in the file are kept as-is (a caller decides how to map
  them onto its own ids); ReqManTool's export keeps requirement ids and module
  ids stable, so a round trip through this module and back is lossless.

Known limitations (the ReqIF format is much wider than what ReqManTool
exports):

- An ATTRIBUTE-VALUE-ENUMERATION can list several ENUM-VALUE-REFs (ReqIF
  allows it); ReqMan's enum attributes hold one value, so the first is taken
  and the rest are dropped.
- A SPEC-OBJECT no SPEC-HIERARCHY of its module refers to is not imported
  (there is no place for it in the hierarchy); one referred to twice is read
  once.
- A link configuration with no pairs has no SPEC-RELATION in the file, so it
  cannot be imported; only the configurations a document actually relates
  through come back.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from reqif.parser import ReqIFParser
from reqif.reqif_bundle import ReqIFBundle

from . import richtext

# The xhtml: prefix the export writes on every element. ``_strip_prefix``
# removes it so ``richtext.sanitize`` sees plain HTML.
_PREFIXED_TAG = re.compile(r"<(/?)xhtml:")

# How ``reqif_export`` names a SPEC-RELATION-TYPE: the prefix plus the link
# configuration's id. Recognised so a file ReqManTool wrote does not import its
# link mirror attribute as a second attribute of the same name.
_RELATION_TYPE_PREFIX = "SPEC-RELATION-TYPE-"


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
class ImportedLink:
    """A SPEC-RELATION: one SPEC-OBJECT of this document pointing at another."""

    source_id: str  # a requirement's id
    target_id: str
    type_name: str  # the SPEC-RELATION-TYPE's LONG-NAME (or identifier)
    type_identifier: str  # the SPEC-RELATION-TYPE's IDENTIFIER
    description: str = ""


@dataclass
class ImportedProject:
    """A whole ReqIF document: the project and everything it holds."""

    id: str
    name: str
    description: str = ""
    attributes: list[ImportedAttributeDefinition] = field(default_factory=list)
    modules: list[ImportedModule] = field(default_factory=list)
    links: list[ImportedLink] = field(default_factory=list)

    def link_mirror_attribute_ids(self) -> set[str]:
        """Attribute ids the document holds only as a link configuration's mirror.

        ``reqif_export`` writes a configuration's read-only mirror attribute as
        an ordinary ATTRIBUTE-DEFINITION whose id is the configuration's id, and
        names the SPEC-RELATION-TYPE that describes the configuration
        ``SPEC-RELATION-TYPE-<id>``. A file written that way would otherwise
        import the mirror as a second attribute under the configuration's own
        name, so the caller drops these and lets the configuration it recreates
        own the attribute. Only ReqManTool's own naming is recognised, so a
        foreign document's user attribute is never mistaken for one.
        """
        identifiers = {link.type_identifier for link in self.links}
        return {
            identifier.removeprefix(_RELATION_TYPE_PREFIX)
            for identifier in identifiers
            if identifier.startswith(_RELATION_TYPE_PREFIX)
        }


# --- XHTML -> rich text ---------------------------------------------------------


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
    the ``xhtml:`` prefix removed and the export's visible spacing decoded
    (``reqif_export`` writes edge spaces and every second space of a run as
    ``&#160;``, since XHTML collapses white space; this reverses that so the
    spacing of the value survives the round trip).
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

# Our attribute type per parsed DATATYPE-DEFINITION class. The identifier a
# file gives its datatypes is its own business (ReqManTool's export happens to
# name them DATATYPE-*), so the class the parser produced is what decides.
_DATATYPE_CLASSES: tuple[tuple[str, str], ...] = (
    ("ReqIFDataTypeDefinitionXHTML", "text"),
    ("ReqIFDataTypeDefinitionInteger", "integer"),
    ("ReqIFDataTypeDefinitionBoolean", "boolean"),
    ("ReqIFDataTypeDefinitionDateIdentifier", "date"),
    ("ReqIFDataTypeDefinitionEnumeration", "enum"),
    ("ReqIFDataTypeDefinitionString", "string"),
    # A REAL has no ReqMan type; its value is kept as text.
    ("ReqIFDataTypeDefinitionReal", "string"),
)


def _datatype_ref(attribute: Any) -> str:
    """The ``DATATYPE-DEFINITION-*-REF`` of a SPEC-ATTRIBUTE-DEFINITION."""
    return str(getattr(attribute, "datatype_definition", "") or "")


def _datatypes(bundle: ReqIFBundle) -> dict[str, Any]:
    """The document's DATATYPE-DEFINITIONs, by identifier."""
    if bundle.core_content is None or bundle.core_content.req_if_content is None:
        return {}
    return {
        identifier: data_type
        for data_type in (getattr(bundle.core_content.req_if_content, "data_types", None) or [])
        if (identifier := getattr(data_type, "identifier", None))
    }


def _datatype_of(attribute: Any, bundle: ReqIFBundle) -> Any:
    """The DATATYPE-DEFINITION an ATTRIBUTE-DEFINITION points at (None if the document lacks it)."""
    return _datatypes(bundle).get(_datatype_ref(attribute))


def _attribute_type(attribute: Any, bundle: ReqIFBundle) -> str:
    """Our attribute type for a SPEC-ATTRIBUTE-DEFINITION (from its datatype's class).

    An ATTRIBUTE-DEFINITION whose datatype the document does not declare (or
    which declares a kind ReqMan has no attribute type for) is read as text,
    the type that stores anything.
    """
    data_type = _datatype_of(attribute, bundle)
    if data_type is not None:
        for class_name, attribute_type in _DATATYPE_CLASSES:
            if type(data_type).__name__ == class_name:
                return attribute_type
    return "string"


def _specified_values(attribute: Any, bundle: ReqIFBundle) -> dict[str, str]:
    """``{SPECIFIED-VALUE identifier: our choice}`` of a definition's ENUMERATION datatype.

    A SPECIFIED-VALUE becomes one enum choice, read as the value the export
    would write and the requirement stores: the EMBEDDED-VALUE ``KEY`` when the
    file has one, else its LONG-NAME, else its IDENTIFIER. A file whose
    specified values are all keyed therefore round trips to the same strings a
    ReqManTool export writes.
    """
    data_type = _datatype_of(attribute, bundle)
    result: dict[str, str] = {}
    for value in getattr(data_type, "values", None) or []:
        identifier = getattr(value, "identifier", None)
        if not identifier or identifier in result:
            continue
        result[identifier] = (
            getattr(value, "key", None)
            or getattr(value, "long_name", None)
            or identifier
        )
    return result


def _read_attributes(spec_object_type: Any, bundle: ReqIFBundle) -> list[ImportedAttributeDefinition]:
    """The ATTRIBUTE-DEFINITIONs of a SPEC-OBJECT-TYPE."""
    result = []
    for attribute in getattr(spec_object_type, "attribute_definitions", None) or []:
        attribute_id = getattr(attribute, "identifier", None)
        if not attribute_id:
            continue
        attribute_type = _attribute_type(attribute, bundle)
        choices = None
        if attribute_type == "enum":
            # The choices are what the attribute holds, so an enumeration with
            # no specified values cannot be written as one; None lets the
            # service add it as a string attribute instead.
            choices = list(_specified_values(attribute, bundle).values()) or None
            if choices is None:
                attribute_type = "string"
        result.append(
            ImportedAttributeDefinition(
                id=attribute_id,
                name=getattr(attribute, "long_name", None) or attribute_id,
                type=attribute_type,
                required=False,  # ReqIF has no required flag on an attribute definition
                choices=choices,
            )
        )
    return result


def _read_value(attribute: Any, value: Any, bundle: ReqIFBundle) -> Any:
    """One ATTRIBUTE-VALUE as the Python value ReqMan stores for ``attribute``."""
    raw = getattr(value, "value", None)
    if raw is None:
        return None
    attribute_type = _attribute_type(attribute, bundle)
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
    if attribute_type == "enum":
        # An ATTRIBUTE-VALUE-ENUMERATION holds ENUM-VALUE-REFs (SPECIFIED-VALUE
        # identifiers, in the order the file lists them). ReqMan's enum
        # attributes hold one value, so the first one wins.
        refs = raw if isinstance(raw, list) else [raw]
        choices = _specified_values(attribute, bundle)
        for ref in refs:
            if (choice := choices.get(str(ref))) is not None:
                return choice
        return None
    return raw


def _read_attributes_of(
    spec_object: Any, spec_object_type: Any, bundle: ReqIFBundle
) -> dict[str, Any]:
    """``spec_object``'s values keyed by attribute id, typed like the service types them."""
    by_id = {getattr(a, "identifier", None): a for a in getattr(spec_object_type, "attribute_definitions", None) or []}
    result: dict[str, Any] = {}
    for value in getattr(spec_object, "attributes", None) or []:
        attribute_id = getattr(value, "definition_ref", None)
        attribute = by_id.get(attribute_id)
        if attribute is None:
            continue
        result[attribute_id] = _read_value(attribute, value, bundle)
    return result


def _spec_types(bundle: ReqIFBundle) -> list[Any]:
    if bundle.core_content is None or bundle.core_content.req_if_content is None:
        return []
    return list(getattr(bundle.core_content.req_if_content, "spec_types", None) or [])


def _spec_object_types(bundle: ReqIFBundle) -> dict[str, Any]:
    """The document's SPEC-OBJECT-TYPEs, by identifier (in document order).

    Told apart by class, not by having ``attribute_definitions``: a
    SPEC-RELATION-TYPE has that field too (an empty one).
    """
    result: dict[str, Any] = {}
    for spec_type in _spec_types(bundle):
        identifier = getattr(spec_type, "identifier", None)
        if identifier and type(spec_type).__name__ == "ReqIFSpecObjectType":
            result.setdefault(identifier, spec_type)
    return result


def _read_hierarchy(
    specification: Any,
    spec_object_types: dict[str, Any],
    spec_objects: dict[str, Any],
    bundle: ReqIFBundle,
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
            # The SPEC-OBJECT names the type its values belong to; a file with
            # several types (one per tool area) keeps each one's attributes, and
            # an object naming a type the document does not declare reads its
            # values against whichever type does define them.
            spec_object_type = spec_object_types.get(getattr(spec_object, "spec_object_type", ""))
            if spec_object_type is None:
                spec_object_type = next(iter(spec_object_types.values()), None)
            result.append(
                ImportedRequirement(
                    id=object_ref,
                    attributes=(
                        _read_attributes_of(spec_object, spec_object_type, bundle)
                        if spec_object_type is not None
                        else {}
                    ),
                    parent_id=parent_id,
                )
            )
            walk(getattr(hierarchy, "children", None) or [], object_ref)

    walk(getattr(specification, "children", None) or [], None)
    return result


def _read_modules(bundle: ReqIFBundle, spec_object_types: dict[str, Any]) -> list[ImportedModule]:
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
        module.requirements = _read_hierarchy(specification, spec_object_types, spec_objects, bundle)
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


def _relation_type_names(bundle: ReqIFBundle) -> dict[str, str]:
    """``{SPEC-RELATION-TYPE identifier: its name}`` — the name of a relation type.

    A relation type is a link configuration's name. ``reqif_export`` writes one
    per configuration, naming it after the configuration, so a file ReqManTool
    wrote comes back with the same names.
    """
    result: dict[str, str] = {}
    for spec_type in _spec_types(bundle):
        if type(spec_type).__name__ != "ReqIFSpecRelationType":
            continue
        identifier = getattr(spec_type, "identifier", None)
        if identifier:
            result.setdefault(identifier, getattr(spec_type, "long_name", None) or identifier)
    return result


def _read_links(bundle: ReqIFBundle) -> list[ImportedLink]:
    """Every SPEC-RELATION as a link between two SPEC-OBJECTs.

    A relation whose type the document does not declare, or that names a
    SPEC-OBJECT the document does not hold, is skipped: there is no link
    configuration to put it in, or no requirement on either end.
    """
    content = bundle.core_content.req_if_content if bundle.core_content is not None else None
    known_objects = {
        identifier
        for spec_object in (getattr(content, "spec_objects", None) or [])
        if (identifier := getattr(spec_object, "identifier", None))
    }
    type_names = _relation_type_names(bundle)
    links: list[ImportedLink] = []
    for relation in getattr(content, "spec_relations", None) or []:
        type_name = type_names.get(getattr(relation, "relation_type_ref", ""))
        source = getattr(relation, "source", None)
        target = getattr(relation, "target", None)
        if not type_name or not source or not target:
            continue
        if source not in known_objects or target not in known_objects:
            continue
        if source == target:
            continue  # ReqMan never links a requirement to itself
        links.append(
            ImportedLink(
                source_id=source,
                target_id=target,
                type_name=type_name,
                type_identifier=str(getattr(relation, "relation_type_ref", "")),
                description=getattr(relation, "description", None) or "",
            )
        )
    return links


def _to_project(bundle: ReqIFBundle) -> ImportedProject:
    """The whole bundle as an ``ImportedProject`` (raises if it has no header)."""
    header = bundle.req_if_header
    if header is None:
        raise ValueError("The ReqIF document has no header")

    spec_object_types = _spec_object_types(bundle)
    first_type = next(iter(spec_object_types.values()), None)
    project = ImportedProject(
        id=getattr(header, "identifier", None) or "imported-project",
        name=getattr(header, "title", None) or "Imported project",
        # ReqIF has no project description in the header (COMMENT is a ReqIF
        # XML comment, not carried by the reqif library); ``reqif_export``
        # writes the project's description as the SPEC-OBJECT-TYPE's DESC, so
        # that is where a description comes from.
        description=getattr(first_type, "description", None) or getattr(header, "comment", None) or "",
    )
    for spec_object_type in spec_object_types.values():
        project.attributes.extend(_read_attributes(spec_object_type, bundle))
    project.modules = _read_modules(bundle, spec_object_types)
    project.links = _read_links(bundle)
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
