"""Export a ReqMan project into a formatted Word (.docx) spec document.

Unlike ``csv_export.py``/``xlsx_export.py`` (flat tables), this reads as a
document: project title, one heading per module, and within each module the
requirement tree (respecting ``parent_id`` nesting and ``order``) as nested
headings with each requirement's attribute values underneath.

Like the other export modules, this has no dependency on ``reqman.models``/
``reqman.service`` on purpose (duck typing) so it stays trivially testable
and side-effect free.
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Sequence, Tuple

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import RGBColor

MAX_HEADING_LEVEL = 9

# Elements that follow ``w:shd`` inside ``w:pPr`` (ECMA-376 schema order).
_PPR_AFTER_SHD = (
    "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
    "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
    "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
    "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
    "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)


def _row_style(requirement: Any, row_styles: Sequence[Any]) -> Optional[Any]:
    """The first rule whose attribute value the requirement has, or None."""
    for style in row_styles:
        if requirement.attributes.get(style.attribute_id) == style.value:
            return style
    return None


def _apply_style(paragraph: Any, style: Optional[Any]) -> None:
    """Color, shade and embolden a paragraph's runs as ``style`` says (None: unchanged)."""
    if style is None:
        return
    if style.background_color:
        shading = OxmlElement("w:shd")
        shading.set(qn("w:val"), "clear")
        shading.set(qn("w:color"), "auto")
        shading.set(qn("w:fill"), style.background_color.lstrip("#").upper())
        paragraph._p.get_or_add_pPr().insert_element_before(shading, *_PPR_AFTER_SHD)
    for run in paragraph.runs:
        if style.font_color:
            run.font.color.rgb = RGBColor.from_string(style.font_color.lstrip("#").upper())
        if style.bold:
            run.bold = True


def _add_requirement(
    document: Document, requirement: Any, project: Any, level: int, row_styles: Sequence[Any]
) -> None:
    style = _row_style(requirement, row_styles)
    _apply_style(document.add_heading(requirement.id, level=min(level, MAX_HEADING_LEVEL)), style)
    for attr in project.attributes:
        value = requirement.attributes.get(attr.id)
        if value is None or value == "":
            continue
        paragraph = document.add_paragraph()
        run = paragraph.add_run(f"{attr.name}: ")
        run.bold = True
        paragraph.add_run(str(value))
        _apply_style(paragraph, style)


def export_project_to_docx(
    project: Any,
    modules: Sequence[Tuple[Any, Sequence[Any]]],
    row_styles: Sequence[Any] = (),
) -> bytes:
    """Export a project's modules/requirements to a Word document.

    Args:
        project: object with ``.name: str`` and ``.attributes:
            list[AttributeDefinition]`` where each has ``.id: str`` and
            ``.name: str``.
        modules: list of ``(module, requirements)`` tuples, in the module
            order that should appear in the output. Each ``module`` has
            ``.name: str``. Each item of ``requirements`` has ``.id: str``,
            ``.attributes: dict[str, Any]`` (keyed by AttributeDefinition.id),
            ``.parent_id: str | None``, and ``.order: int``.
        row_styles: rules with ``.attribute_id``, ``.value``,
            ``.font_color``/``.background_color`` (``#rrggbb`` or None) and
            ``.bold``, already resolved against the project (see
            ``ProjectService._effective_row_styles``). A requirement takes
            the first rule whose value it has: its heading and attribute
            paragraphs get that font color, background shading and bold.

    Returns:
        The bytes of a .docx document: the project name as the title
        (level 0), one level-1 heading per module, and within each module
        the requirement tree (root items at level 2, each level of nesting
        one level deeper, capped at level 9) — each requirement heading is
        its id, followed by a bold-label paragraph per non-empty attribute
        value.
    """
    document = Document()
    document.add_heading(project.name, level=0)

    for module, requirements in modules:
        document.add_heading(module.name, level=1)

        children_of: Dict[Optional[str], List[Any]] = {}
        for requirement in requirements:
            children_of.setdefault(requirement.parent_id, []).append(requirement)
        for children in children_of.values():
            children.sort(key=lambda r: r.order)

        def walk(parent_id: Optional[str], depth: int) -> None:
            for requirement in children_of.get(parent_id, []):
                _add_requirement(document, requirement, project, depth + 2, row_styles)
                walk(requirement.id, depth + 1)

        walk(None, 0)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
