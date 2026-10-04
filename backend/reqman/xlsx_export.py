"""Export a ReqMan project into an XLSX workbook (bytes).

Like ``csv_export.py``/``reqif_export.py``, this module has no dependency on
``reqman.models``/``reqman.service`` on purpose (duck typing) so it stays
trivially testable and side-effect free.
"""

from __future__ import annotations

import io
from typing import Any, Sequence, Tuple

from openpyxl import Workbook


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
            and ``.parent_id: str | None``.

    Returns:
        The bytes of an .xlsx workbook with a single sheet: a header row
        (Id, Module, Parent, then one column per project attribute, using
        the attribute's ``name``) and one row per requirement, in the given
        module/requirement order — same content/order as
        ``csv_export.export_project_to_csv``.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Requirements"

    header = ["Id", "Module", "Parent"] + [attr.name for attr in project.attributes]
    sheet.append(header)

    for module, requirements in modules:
        for requirement in requirements:
            row = [requirement.id, module.id, requirement.parent_id or ""]
            for attr in project.attributes:
                value = requirement.attributes.get(attr.id, "")
                row.append("" if value is None else value)
            sheet.append(row)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
