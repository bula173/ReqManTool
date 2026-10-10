"""Export a ReqMan project into a CSV string.

Like ``reqif_export.py``, this module has no dependency on ``reqman.models``
/ ``reqman.service`` on purpose (duck typing) so it stays trivially testable
and side-effect free.

Cells are written verbatim: values starting with ``=``, ``+``, ``-`` or ``@``
are not escaped against spreadsheet formula injection. The CSV export is the
input format of ``csv_import.py``, and export -> import must round-trip every
value byte for byte; an escape prefix would be imported as part of the value.
Use the XLSX export to open requirements in a spreadsheet: it stores every
text value as a string cell, never as a formula.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from typing import Any

from .ordering import tree_order


def export_project_to_csv(
    project: Any,
    modules: Sequence[tuple[Any, Sequence[Any]]],
) -> str:
    """Export a project's requirements (across all its modules) to CSV.

    Args:
        project: object with ``.attributes: list[AttributeDefinition]``
            where each has ``.id: str`` and ``.name: str``.
        modules: list of ``(module, requirements)`` tuples, in the module
            order that should appear in the output. Each ``module`` has
            ``.id: str``. Each item of ``requirements`` has ``.id: str``,
            ``.attributes: dict[str, Any]`` (keyed by AttributeDefinition.id),
            ``.parent_id: str | None`` and ``.order: int``.

    Returns:
        CSV text with a header row (Id, Module, Parent, then one column per
        project attribute, using the attribute's ``name``) and one row per
        requirement. Modules keep the given order. Within a module, rows are
        written depth-first (each parent before its children) with siblings
        sorted by ``order`` then ``id`` (``ordering.tree_order``, shared with
        the XLSX export), so a CSV import recreates the manual ordering.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    header = ["Id", "Module", "Parent"] + [attr.name for attr in project.attributes]
    writer.writerow(header)

    for module, requirements in modules:
        for requirement in tree_order(requirements):
            row = [requirement.id, module.id, requirement.parent_id or ""]
            for attr in project.attributes:
                value = requirement.attributes.get(attr.id, "")
                row.append("" if value is None else value)
            writer.writerow(row)

    return buffer.getvalue()

