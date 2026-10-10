"""Requirement row order shared by the flat (table) exporters.

Like the exporters, this module has no dependency on ``reqman.models`` /
``reqman.service`` (duck typing) and no side effects.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def tree_order(requirements: Sequence[Any]) -> list[Any]:
    """One module's requirements in tree order: parents first, siblings by ``order`` then ``id``.

    Each item has ``.id``, ``.parent_id`` and ``.order``. A requirement whose
    parent is not in ``requirements`` is treated as a root, and anything not
    reached from a root (a corrupt parent cycle) is appended at the end in
    ``id`` order, so no requirement is ever dropped.
    """
    ids = {r.id for r in requirements}
    children_of: dict[Any, list[Any]] = {}
    for requirement in requirements:
        parent = requirement.parent_id if requirement.parent_id in ids else None
        children_of.setdefault(parent, []).append(requirement)
    for siblings in children_of.values():
        siblings.sort(key=lambda r: (r.order, r.id))

    ordered: list[Any] = []
    visited: set[str] = set()
    stack = list(reversed(children_of.get(None, [])))
    while stack:
        requirement = stack.pop()
        if requirement.id in visited:
            continue
        visited.add(requirement.id)
        ordered.append(requirement)
        stack.extend(reversed(children_of.get(requirement.id, [])))
    ordered.extend(r for r in sorted(requirements, key=lambda r: r.id) if r.id not in visited)
    return ordered
