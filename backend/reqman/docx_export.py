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

MAX_HEADING_LEVEL = 9


def _add_requirement(document: Document, requirement: Any, project: Any, level: int) -> None:
    document.add_heading(requirement.id, level=min(level, MAX_HEADING_LEVEL))
    for attr in project.attributes:
        value = requirement.attributes.get(attr.id)
        if value is None or value == "":
            continue
        paragraph = document.add_paragraph()
        run = paragraph.add_run(f"{attr.name}: ")
        run.bold = True
        paragraph.add_run(str(value))


def export_project_to_docx(
    project: Any,
    modules: Sequence[Tuple[Any, Sequence[Any]]],
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
                _add_requirement(document, requirement, project, level=depth + 2)
                walk(requirement.id, depth + 1)

        walk(None, 0)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
