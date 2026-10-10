"""Export a ReqMan project into an XLSX workbook (bytes).

Like ``csv_export.py``/``reqif_export.py``, this module has no dependency on
``reqman.models``/``reqman.service`` on purpose (duck typing) so it stays
trivially testable and side-effect free.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from openpyxl import Workbook

from .ordering import tree_order

_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
DATE_NUMBER_FORMAT = "yyyy-mm-dd"


def export_project_to_xlsx(
    project: Any,
    modules: Sequence[tuple[Any, Sequence[Any]]],
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
        formula. Integers and booleans keep their native cell types. A valid
        ``date`` attribute value (``YYYY-MM-DD``) becomes a date cell shown
        as ``yyyy-mm-dd``, so spreadsheets sort and filter it as a date; an
        invalid one (hand-edited data) stays a string cell.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Requirements"

    header = ["Id", "Module", "Parent"] + [attr.name for attr in project.attributes]
    sheet.append(header)

    for module, requirements in modules:
        for requirement in tree_order(requirements):
            row: list[Any] = [requirement.id, module.id, requirement.parent_id or ""]
            for attr in project.attributes:
                value = requirement.attributes.get(attr.id, "")
                if getattr(attr, "type", None) == "date":
                    value = _as_date(value)
                row.append("" if value is None else value)
            sheet.append(row)
            cells = sheet[sheet.max_row]
            _force_text_cells(cells, row)
            for cell, value in zip(cells, row):
                if isinstance(value, date):
                    cell.number_format = DATE_NUMBER_FORMAT

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _as_date(value: Any) -> Any:
    """A valid ``YYYY-MM-DD`` string as a ``date``; anything else unchanged."""
    if isinstance(value, str) and _ISO_DATE.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    return value


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
