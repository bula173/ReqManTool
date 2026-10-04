"""Export a ReqMan project into an XLSX workbook (bytes).

Like ``csv_export.py``/``reqif_export.py``, this module has no dependency on
``reqman.models``/``reqman.service`` on purpose (duck typing) so it stays
trivially testable and side-effect free.
"""

from __future__ import annotations

import io
from typing import Any, Sequence, Tuple

from openpyxl import Workbook

from .ordering import tree_order


def export_project_to_xlsx(
    project: Any,
    modules: Sequence[Tuple[Any, Sequence[Any]]],
) -> bytes:
    """Export a project's requirements (across all its modules) to XLSX.

    Args:
        project: object with ``.attributes: list[AttributeDefinition]``
            where each has ``.id: str`` and ``.name: str``.
        modules: list of ``(module, requirements)`` tuples, in the module
            order that should appear in the output. Each ``module`` has
            ``.id: str``. Each item of ``requirements`` has ``.id: str``,
            ``.attributes: dict[str, Any]`` (keyed by AttributeDefinition.id),
            ``.parent_id: str | None`` and ``.order: int``.

    Returns:
        The bytes of an .xlsx workbook with a single sheet: a header row
        (Id, Module, Parent, then one column per project attribute, using
        the attribute's ``name``) and one row per requirement — the same
        columns, content and row order as
        ``csv_export.export_project_to_csv``: modules in the given order,
        and within a module depth-first (each parent before its children)
        with siblings sorted by ``order`` then ``id`` (``ordering.tree_order``).

        Every string value is stored as a string cell (``data_type`` ``'s'``),
        so text such as ``=1+1`` is shown verbatim and never becomes a live
        formula. Integers and booleans keep their native cell types.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Requirements"

    header = ["Id", "Module", "Parent"] + [attr.name for attr in project.attributes]
    sheet.append(header)

    for module, requirements in modules:
        for requirement in tree_order(requirements):
            row = [requirement.id, module.id, requirement.parent_id or ""]
            for attr in project.attributes:
                value = requirement.attributes.get(attr.id, "")
                row.append("" if value is None else value)
            sheet.append(row)
            _force_text_cells(sheet[sheet.max_row], row)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _force_text_cells(cells: Sequence[Any], values: Sequence[Any]) -> None:
    """Store every ``str`` value as a string cell.

    openpyxl turns any string starting with ``=`` into a formula, so a
    requirement text could otherwise run as a formula (formula injection)
    when the workbook is opened. Setting ``data_type`` keeps the text
    unchanged and writes it as a plain string.
    """
    for cell, value in zip(cells, values):
        if isinstance(value, str):
            cell.data_type = "s"
