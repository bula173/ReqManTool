"""Export a ReqMan project into a CSV string.

Like ``reqif_export.py``, this module has no dependency on ``reqman.models``
/ ``reqman.service`` on purpose (duck typing) so it stays trivially testable
and side-effect free.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Sequence, Tuple


def export_project_to_csv(
    project: Any,
    modules: Sequence[Tuple[Any, Sequence[Any]]],
) -> str:
    """Export a project's requirements (across all its modules) to CSV.

    Args:
        project: object with ``.attributes: list[AttributeDefinition]``
            where each has ``.id: str`` and ``.name: str``.
        modules: list of ``(module, requirements)`` tuples, in the module
            order that should appear in the output. Each ``module`` has
            ``.id: str``. Each item of ``requirements`` has ``.id: str``,
            ``.attributes: dict[str, Any]`` (keyed by AttributeDefinition.id),
            and ``.parent_id: str | None``.

    Returns:
        CSV text with a header row (Id, Module, Parent, then one column per
        project attribute, using the attribute's ``name``) and one row per
        requirement, in the given module/requirement order.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    header = ["Id", "Module", "Parent"] + [attr.name for attr in project.attributes]
    writer.writerow(header)

    for module, requirements in modules:
        for requirement in requirements:
            row = [requirement.id, module.id, requirement.parent_id or ""]
            for attr in project.attributes:
                value = requirement.attributes.get(attr.id, "")
                row.append("" if value is None else value)
            writer.writerow(row)

    return buffer.getvalue()
