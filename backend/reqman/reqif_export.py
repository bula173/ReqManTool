"""Export a ReqMan project into a ReqIF XML string.

This module has no dependency on ``reqman.models`` / ``reqman.service`` on
purpose (to avoid circular imports): callers pass in plain data — a
``project``-like object and a list of ``(module, requirements)`` pairs — and
this module only relies on the attributes it actually reads (duck typing),
which are documented on ``export_project_to_reqif`` below.

It builds a `reqif` (strictdoc-project/reqif) ``ReqIFBundle`` and renders it
with ``reqif.unparser.ReqIFUnparser``.

Mapping decisions (see also the docstring of ``export_project_to_reqif``):

- Every ReqMan attribute type is mapped to a *simple* ReqIF datatype/attribute
  kind that this package can unparse (and re-parse) without any special
  handling:
    - "string", "enum"  -> ATTRIBUTE-DEFINITION-STRING / DATATYPE-DEFINITION-STRING
    - "text"            -> ATTRIBUTE-DEFINITION-XHTML / DATATYPE-DEFINITION-XHTML
    - "integer"         -> ATTRIBUTE-DEFINITION-INTEGER / DATATYPE-DEFINITION-INTEGER
    - "boolean"         -> ATTRIBUTE-DEFINITION-BOOLEAN / DATATYPE-DEFINITION-BOOLEAN
    - "date"            -> ATTRIBUTE-DEFINITION-DATE / DATATYPE-DEFINITION-DATE
  A date value ``YYYY-MM-DD`` is written as the ``xsd:dateTime``
  ``YYYY-MM-DDT00:00:00`` (no time zone: a calendar date, not an instant);
  a stored value that is not a valid date (hand-edited data) is omitted.
  DATATYPE-DATE is declared only when the project has a date attribute, so
  exports of projects without one are unchanged.
  "enum" is NOT exported as ENUMERATION with a proper enum-values datatype
  definition but as a plain string, losing the "one of a fixed set of
  choices" information in the ReqIF file itself.
- Every "text" attribute (``ObjectText`` included) is XHTML, whatever its
  values: ReqIF gives each attribute one datatype, and a text attribute may
  hold plain and rich values side by side. Each value's THE-VALUE is one
  ``xhtml:div`` (the ``xhtml`` prefix is declared on the REQ-IF root):
    - a rich value (``richtext.py``) is written from its canonical tree:
      ``p``, ``br``, ``strong``, ``em``, ``ul``, ``ol``, ``li`` and ``span``
      (with its ``color``/``font-size`` style) keep their names; ``u`` and
      ``s`` become ``span`` with ``text-decoration: underline`` /
      ``line-through``: the ReqIF XHTML subset (XHTML 1.1 modules) has no
      ``xhtml:u`` or ``xhtml:s``, and its ``xhtml:del`` means a deletion,
      not a strike-through;
    - a plain value is its text, escaped, with ``xhtml:br`` for line breaks;
      its spacing stays visible although XHTML collapses white space: on
      each line, leading and trailing spaces become ``&#160;``, every second
      space of a run inside the line becomes ``&#160;``, and a tab becomes
      four ``&#160;``. (Rich values keep their text nodes as stored: the
      editor already writes visible runs of spaces as non-breaking spaces.)
    - a value with no text (``""``, a rich value of blanks) is an empty div.
  Text is escaped and characters XML 1.0 cannot hold are dropped, so the
  document is always well-formed; "string" and "enum" values are filtered
  the same way.
- One SPEC-OBJECT-TYPE is created for the whole project, with one
  ATTRIBUTE-DEFINITION-* per ``AttributeDefinition``.
- One SPEC-OBJECT is created per requirement (across all modules).
- One SPECIFICATION is created per module, with a nested SPEC-HIERARCHY tree
  built from each requirement's ``parent_id`` (root items become top-level
  children of the SPECIFICATION; nested items become nested SPEC-HIERARCHY
  children), in the given order.
- One SPEC-RELATION-TYPE is created per project link configuration, and one
  SPEC-RELATION per pair in that configuration's link store. Requirement ids
  are unique project-wide, so a link's target can live in a different module
  than its source.
"""

from __future__ import annotations

import re
from datetime import date as _date
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from reqif.models.reqif_core_content import ReqIFCoreContent
from reqif.models.reqif_data_type import (
    ReqIFDataTypeDefinitionBoolean,
    ReqIFDataTypeDefinitionDateIdentifier,
    ReqIFDataTypeDefinitionInteger,
    ReqIFDataTypeDefinitionString,
    ReqIFDataTypeDefinitionXHTML,
)
from reqif.models.reqif_namespace_info import ReqIFNamespaceInfo
from reqif.models.reqif_req_if_content import ReqIFReqIFContent
from reqif.models.reqif_reqif_header import ReqIFReqIFHeader
from reqif.models.reqif_spec_hierarchy import ReqIFSpecHierarchy
from reqif.models.reqif_spec_object import ReqIFSpecObject, SpecObjectAttribute
from reqif.models.reqif_spec_object_type import (
    ReqIFSpecObjectType,
    SpecAttributeDefinition,
)
from reqif.models.reqif_spec_relation import ReqIFSpecRelation
from reqif.models.reqif_spec_relation_type import ReqIFSpecRelationType
from reqif.models.reqif_specification import ReqIFSpecification
from reqif.models.reqif_types import SpecObjectAttributeType
from reqif.object_lookup import ReqIFObjectLookup
from reqif.reqif_bundle import ReqIFBundle
from reqif.unparser import ReqIFUnparser

from . import richtext

# --- Fixed identifiers for the (always present) simple datatypes. --------

DATATYPE_STRING_ID = "DATATYPE-STRING"
DATATYPE_INTEGER_ID = "DATATYPE-INTEGER"
DATATYPE_BOOLEAN_ID = "DATATYPE-BOOLEAN"
DATATYPE_XHTML_ID = "DATATYPE-XHTML"
DATATYPE_DATE_ID = "DATATYPE-DATE"

_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

SPEC_OBJECT_TYPE_ID = "SPEC-OBJECT-TYPE-REQUIREMENT"

# Our AttributeDefinition.type -> reqif SpecObjectAttributeType / datatype id.
_ATTRIBUTE_TYPE_MAP = {
    "string": (SpecObjectAttributeType.STRING, DATATYPE_STRING_ID),
    "text": (SpecObjectAttributeType.XHTML, DATATYPE_XHTML_ID),
    "enum": (SpecObjectAttributeType.STRING, DATATYPE_STRING_ID),
    "integer": (SpecObjectAttributeType.INTEGER, DATATYPE_INTEGER_ID),
    "boolean": (SpecObjectAttributeType.BOOLEAN, DATATYPE_BOOLEAN_ID),
    "date": (SpecObjectAttributeType.DATE, DATATYPE_DATE_ID),
}


def _date_value(value: Any) -> Optional[str]:
    """A ``YYYY-MM-DD`` value as an ``xsd:dateTime`` THE-VALUE, or None if it is not a valid date."""
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        return None
    try:
        _date.fromisoformat(value)
    except ValueError:
        return None
    return f"{value}T00:00:00"


def _relation_type_id(config_id: str) -> str:
    return f"SPEC-RELATION-TYPE-{config_id}"


def _spec_hierarchy_id(module_id: str, requirement_id: str) -> str:
    return f"SPEC-HIERARCHY-{module_id}-{requirement_id}"


# Inline marks the ReqIF XHTML subset lacks, written as a styled span.
_MARK_SPANS = {"u": "text-decoration: underline", "s": "text-decoration: line-through"}


def _xhtml_escape(text: str) -> str:
    return richtext.xml_text(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xhtml_inline(nodes: Iterable[Any]) -> str:
    out = []
    for node in nodes:
        if isinstance(node, str):
            out.append(_xhtml_escape(node))
        elif isinstance(node, richtext.LineBreak):
            out.append("<xhtml:br/>")
        elif node.tag in ("strong", "em"):
            out.append(f"<xhtml:{node.tag}>{_xhtml_inline(node.children)}</xhtml:{node.tag}>")
        else:
            style = _MARK_SPANS.get(node.tag, node.style)
            out.append(f'<xhtml:span style="{style}">{_xhtml_inline(node.children)}</xhtml:span>')
    return "".join(out)


def _xhtml_blocks(blocks: Iterable[Any]) -> str:
    out = []
    for block in blocks:
        if isinstance(block, richtext.Paragraph):
            out.append(f"<xhtml:p>{_xhtml_inline(block.children)}</xhtml:p>")
        else:
            items = "".join(f"<xhtml:li>{_xhtml_blocks(item)}</xhtml:li>" for item in block.items)
            out.append(f"<xhtml:{block.tag}>{items}</xhtml:{block.tag}>")
    return "".join(out)


_NBSP = "&#160;"
_SPACE_RUNS = re.compile(r"^ +| +$| {2,}")


def _spaced(match: "re.Match[str]") -> str:
    """A run of spaces of a plain line, kept visible (see the module docstring)."""
    count = len(match.group())
    if match.start() == 0 or match.end() == len(match.string):
        return _NBSP * count
    return "".join(" " if i % 2 == 0 else _NBSP for i in range(count))


def _xhtml_plain_line(line: str) -> str:
    """One line of a plain value: escaped, with its spaces and tabs kept visible."""
    return _SPACE_RUNS.sub(_spaced, _xhtml_escape(line)).replace("\t", _NBSP * 4)


def _xhtml_value(value: Any) -> str:
    """A text attribute value as the content of an ATTRIBUTE-VALUE-XHTML's THE-VALUE."""
    if richtext.is_rich(value):
        body = _xhtml_blocks(richtext.blocks(value))
    else:
        lines = re.split(r"\r\n|\r|\n", str(value))
        body = "<xhtml:br/>".join(_xhtml_plain_line(line) for line in lines) if str(value) else ""
    return f"<xhtml:div>{body}</xhtml:div>"


def _format_attribute_value(attr_type: str, value: Any) -> str:
    """Serialize a requirement's raw attribute value to the ReqIF THE-VALUE string."""
    if attr_type == "boolean":
        return "true" if value else "false"
    if attr_type == "integer":
        return str(int(value))
    if attr_type == "text":
        return _xhtml_value(value)
    # "string", "enum", and anything else: plain text, without the
    # characters XML 1.0 cannot hold.
    return richtext.xml_text(str(value))


def export_project_to_reqif(
    project: Any,
    modules: Sequence[Tuple[Any, Sequence[Any]]],
    link_configurations: Sequence[Any] = (),
    link_pairs: Optional[dict] = None,
) -> str:
    """Export a project + its modules/requirements to a ReqIF XML string.

    Args:
        project: object with ``.id: str``, ``.name: str``, ``.description: str``,
            and ``.attributes: list[AttributeDefinition]`` where each
            AttributeDefinition has ``.id: str``, ``.name: str``,
            ``.type: "string"|"text"|"integer"|"boolean"|"enum"|"date"``,
            ``.required: bool``, ``.default: Any``, ``.choices: list[str] | None``.
        modules: list of ``(module, requirements)`` tuples, in the module
            order that should appear in the output. Each ``module`` has
            ``.id: str``, ``.name: str``, ``.description: str``. Each item of
            ``requirements`` has ``.id: str`` (unique project-wide),
            ``.attributes: dict[str, Any]`` (keyed by AttributeDefinition.id),
            and ``.parent_id: str | None`` (another requirement's id in the
            *same* module, or ``None`` for a root-level item — this drives
            the nesting of the module's SPEC-HIERARCHY).
        link_configurations: the project's link configurations, each with
            ``.id: str`` and ``.name: str``.
        link_pairs: ``{config_id: [pair, ...]}`` where each pair has
            ``.source_id: str`` and ``.target_id: str`` — the link
            configuration's pairs store ("link module").

    Returns:
        A ReqIF XML document as a string.

    Limitations (see module docstring for more detail):
        - "text" attributes are exported as ATTRIBUTE-VALUE-XHTML (rich
          values with their formatting, plain values as escaped text).
        - "enum" attributes are exported as ATTRIBUTE-VALUE-STRING (the raw
          value, as ``str()``), not as a proper ATTRIBUTE-VALUE-ENUMERATION
          backed by a DATATYPE-DEFINITION-ENUMERATION with SPECIFIED-VALUES.
          ``AttributeDefinition.choices`` is not represented in the ReqIF
          output at all.
        - A requirement attribute whose key is missing from
          ``requirement.attributes`` (or whose value is ``None``) is simply
          omitted from that SPEC-OBJECT's VALUES; ``AttributeDefinition.default``
          is not substituted in.
        - SPECIFICATION hierarchies mirror each requirement's ``parent_id``
          within its module: root-level requirements (``parent_id is None``)
          become direct children of the module's SPECIFICATION, and nested
          requirements become nested SPEC-HIERARCHY children, in the given
          order.
    """
    all_requirements: List[Any] = []
    for _module, requirements in modules:
        all_requirements.extend(requirements)
    link_pairs = link_pairs or {}

    # --- DATATYPES -----------------------------------------------------
    data_types: List[Any] = [
        ReqIFDataTypeDefinitionString.create(DATATYPE_STRING_ID),
        ReqIFDataTypeDefinitionInteger(identifier=DATATYPE_INTEGER_ID),
        ReqIFDataTypeDefinitionBoolean.create(DATATYPE_BOOLEAN_ID),
    ]
    if any(attr.type == "text" for attr in project.attributes):
        data_types.append(ReqIFDataTypeDefinitionXHTML(identifier=DATATYPE_XHTML_ID, is_self_closed=True))
    if any(attr.type == "date" for attr in project.attributes):
        data_types.append(ReqIFDataTypeDefinitionDateIdentifier(identifier=DATATYPE_DATE_ID, is_self_closed=True))

    # --- SPEC-TYPES: one SPEC-OBJECT-TYPE for the whole project ---------
    attribute_definitions = []
    for attr in project.attributes:
        spec_attr_type, datatype_id = _ATTRIBUTE_TYPE_MAP[attr.type]
        attribute_definitions.append(
            SpecAttributeDefinition.create(
                attribute_type=spec_attr_type,
                identifier=attr.id,
                datatype_definition=datatype_id,
                long_name=attr.name,
            )
        )

    spec_object_type = ReqIFSpecObjectType.create(
        identifier=SPEC_OBJECT_TYPE_ID,
        long_name=project.name,
        description=project.description or None,
        attribute_definitions=attribute_definitions or None,
    )

    # --- SPEC-TYPES: one SPEC-RELATION-TYPE per link configuration ------
    spec_relation_types = [
        ReqIFSpecRelationType(
            identifier=_relation_type_id(config.id),
            long_name=config.name,
        )
        for config in link_configurations
    ]

    spec_types: List[Any] = [spec_object_type, *spec_relation_types]

    # --- SPEC-OBJECTS: one per requirement, across all modules ----------
    spec_objects = []
    for requirement in all_requirements:
        values = []
        for attr in project.attributes:
            if attr.id not in requirement.attributes:
                continue
            raw_value = requirement.attributes[attr.id]
            if raw_value is None:
                continue
            if attr.type == "date":
                the_value = _date_value(raw_value)
                if the_value is None:
                    continue
            else:
                the_value = _format_attribute_value(attr.type, raw_value)
            spec_attr_type, _datatype_id = _ATTRIBUTE_TYPE_MAP[attr.type]
            values.append(
                SpecObjectAttribute(
                    attribute_type=spec_attr_type,
                    definition_ref=attr.id,
                    value=the_value,
                )
            )
        spec_objects.append(
            ReqIFSpecObject.create(
                identifier=requirement.id,
                spec_object_type=SPEC_OBJECT_TYPE_ID,
                attributes=values,
            )
        )

    # --- SPECIFICATIONS: one per module, nested hierarchy via parent_id ----
    specifications = []
    for module, requirements in modules:
        children_of: dict[Optional[str], List[Any]] = {}
        for requirement in requirements:
            children_of.setdefault(getattr(requirement, "parent_id", None), []).append(requirement)

        def build_level(parent_id: Optional[str], level: int) -> List[ReqIFSpecHierarchy]:
            return [
                ReqIFSpecHierarchy(
                    identifier=_spec_hierarchy_id(module.id, requirement.id),
                    spec_object=requirement.id,
                    level=level,
                    children=build_level(requirement.id, level + 1) or None,
                )
                for requirement in children_of.get(parent_id, [])
            ]

        specifications.append(
            ReqIFSpecification(
                identifier=module.id,
                long_name=module.name,
                description=module.description or None,
                children=build_level(None, 1),
            )
        )

    # --- SPEC-RELATIONS: one per pair in each link configuration --------
    spec_relations = []
    relation_counter = 0
    for config in link_configurations:
        for pair in link_pairs.get(config.id, []):
            relation_counter += 1
            spec_relations.append(
                ReqIFSpecRelation(
                    identifier=f"SPEC-RELATION-{relation_counter}",
                    relation_type_ref=_relation_type_id(config.id),
                    source=pair.source_id,
                    target=pair.target_id,
                )
            )

    # --- Assemble the bundle ---------------------------------------------
    req_if_content = ReqIFReqIFContent(
        data_types=data_types,
        spec_types=spec_types,
        spec_objects=spec_objects,
        spec_relations=spec_relations or None,
        specifications=specifications,
    )

    bundle = ReqIFBundle(
        namespace_info=ReqIFNamespaceInfo.create_default(),
        req_if_header=ReqIFReqIFHeader(
            identifier=project.id,
            title=project.name,
        ),
        core_content=ReqIFCoreContent(req_if_content=req_if_content),
        tool_extensions_tag_exists=False,
        lookup=ReqIFObjectLookup.empty(),
        exceptions=[],
    )

    return ReqIFUnparser.unparse(bundle)
