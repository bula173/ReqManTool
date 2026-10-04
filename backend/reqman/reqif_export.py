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
    - "string", "text", "enum"  -> ATTRIBUTE-DEFINITION-STRING / DATATYPE-DEFINITION-STRING
    - "integer"                 -> ATTRIBUTE-DEFINITION-INTEGER / DATATYPE-DEFINITION-INTEGER
    - "boolean"                 -> ATTRIBUTE-DEFINITION-BOOLEAN / DATATYPE-DEFINITION-BOOLEAN
  In particular, "text" is NOT exported as XHTML, and "enum" is NOT exported
  as ENUMERATION with a proper enum-values datatype definition: both are
  exported as plain strings. This keeps the export simple and guarantees a
  clean round trip, at the cost of losing the "this is markup" / "this is one
  of a fixed set of choices" information in the ReqIF file itself.
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

from typing import Any, Iterable, List, Optional, Sequence, Tuple

from reqif.models.reqif_core_content import ReqIFCoreContent
from reqif.models.reqif_data_type import (
    ReqIFDataTypeDefinitionBoolean,
    ReqIFDataTypeDefinitionInteger,
    ReqIFDataTypeDefinitionString,
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

# --- Fixed identifiers for the (always present) simple datatypes. --------

DATATYPE_STRING_ID = "DATATYPE-STRING"
DATATYPE_INTEGER_ID = "DATATYPE-INTEGER"
DATATYPE_BOOLEAN_ID = "DATATYPE-BOOLEAN"

SPEC_OBJECT_TYPE_ID = "SPEC-OBJECT-TYPE-REQUIREMENT"

# Our AttributeDefinition.type -> reqif SpecObjectAttributeType / datatype id.
_ATTRIBUTE_TYPE_MAP = {
    "string": (SpecObjectAttributeType.STRING, DATATYPE_STRING_ID),
    "text": (SpecObjectAttributeType.STRING, DATATYPE_STRING_ID),
    "enum": (SpecObjectAttributeType.STRING, DATATYPE_STRING_ID),
    "integer": (SpecObjectAttributeType.INTEGER, DATATYPE_INTEGER_ID),
    "boolean": (SpecObjectAttributeType.BOOLEAN, DATATYPE_BOOLEAN_ID),
}


def _relation_type_id(config_id: str) -> str:
    return f"SPEC-RELATION-TYPE-{config_id}"


def _spec_hierarchy_id(module_id: str, requirement_id: str) -> str:
    return f"SPEC-HIERARCHY-{module_id}-{requirement_id}"


def _format_attribute_value(attr_type: str, value: Any) -> str:
    """Serialize a requirement's raw attribute value to the ReqIF THE-VALUE string."""
    if attr_type == "boolean":
        return "true" if value else "false"
    if attr_type == "integer":
        return str(int(value))
    # "string", "text", "enum", and anything else: plain text.
    return str(value)


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
            ``.type: "string"|"text"|"integer"|"boolean"|"enum"``,
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
        - "text" attributes are exported as ATTRIBUTE-VALUE-STRING, not XHTML.
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
    data_types = [
        ReqIFDataTypeDefinitionString.create(DATATYPE_STRING_ID),
        ReqIFDataTypeDefinitionInteger(identifier=DATATYPE_INTEGER_ID),
        ReqIFDataTypeDefinitionBoolean.create(DATATYPE_BOOLEAN_ID),
    ]

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
            spec_attr_type, _datatype_id = _ATTRIBUTE_TYPE_MAP[attr.type]
            values.append(
                SpecObjectAttribute(
                    attribute_type=spec_attr_type,
                    definition_ref=attr.id,
                    value=_format_attribute_value(attr.type, raw_value),
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
