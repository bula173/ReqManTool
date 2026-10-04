"""Parse CSV text into requirement rows for import into a single module.

The inverse of ``csv_export.py``: it accepts the same layout (``Id``,
``Module``, ``Parent``, then one column per project attribute, headed by the
attribute's ``name``). Like the exporters, it has no dependency on
``reqman.models`` / ``reqman.service`` (duck typing) and no side effects. It
parses the file and converts text cells to typed values. Attribute-value
rules (enum choices, required attributes, ...) are not repeated here: the
caller passes ``validate``, and ``ProjectService.import_csv`` passes the same
check that REST/WebSocket add-requirement uses. ``ProjectService.import_csv``
then allocates ids and writes the requirements.

Rules:

- **Id**: optional. It is never used as the stored id, because requirement ids
  are always allocated by the server. A non-empty ``Id`` is only a key within
  the file, so that ``Parent`` cells can refer to that row. Non-empty values
  must be unique within the file.
- **Parent**: optional. An empty cell puts the row at the module root.
  Otherwise the value must match the ``Id`` of another row in the same file.
  It cannot refer to the row itself, and the parent chain cannot form a cycle.
- **Module**: informational. The target module is chosen by the caller. A
  file whose ``Module`` column has more than one distinct non-empty value
  (for example, a whole-project export) is rejected, so that several modules
  are not merged into one by accident.
- **Attribute columns**: matched to a project attribute by ``name`` first,
  then by ``id``. ``Id``, ``Module`` and ``Parent`` always mean the reserved
  columns, even in an older project that has an attribute with such a name
  (new attributes cannot get one); such an attribute's column must be headed
  with the attribute's ``id``. The same applies when several attributes
  share a name. Columns for system-managed attributes (link mirrors) are
  ignored and reported back, so that a CSV export can be re-imported. Unknown,
  ambiguous and duplicate columns reject the whole file.
- **Values**: an empty cell means "not set". ``integer`` accepts an optional
  sign and ASCII digits 0-9. ``boolean`` accepts true/false, yes/no, y/n and
  1/0 (any case). ``string``/``text``/``enum`` are kept verbatim.
- **Row validation**: once every cell of a row has been converted, the row's
  values go to ``validate`` (enum choices, required attributes without a
  default, ...), and each problem it returns is reported for that row.
- Rows where every cell is empty are skipped. Columns with an empty header
  (for example, trailing separators written by a spreadsheet) are allowed
  only while all their cells are empty.

Validation is all-or-nothing. Every problem is collected with its row number
(the header is row 1, as in a spreadsheet) and raised together as a single
``CsvImportError``. Nothing is returned for partial use.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

ID_COLUMN = "Id"
MODULE_COLUMN = "Module"
PARENT_COLUMN = "Parent"
RESERVED_COLUMNS = (ID_COLUMN, MODULE_COLUMN, PARENT_COLUMN)

MAX_REPORTED_ERRORS = 20

MAX_CSV_IMPORT_BYTES = 10 * 1024 * 1024
"""Largest accepted CSV file (10 MiB). REST rejects larger request bodies with
413, and ``ProjectService.import_csv`` rejects longer text with ValueError."""

# The default per-cell limit (131072 characters) is smaller than cells that the
# CSV export can write, so export -> import would fail for large text. A cell
# can never be longer than the whole file, so the file cap is a safe limit.
# ``csv.field_size_limit`` is process-wide, so never lower a larger limit.
csv.field_size_limit(max(csv.field_size_limit(), MAX_CSV_IMPORT_BYTES))

_INTEGER_PATTERN = re.compile(r"^[+-]?[0-9]+$")
_TRUE_VALUES = {"true", "yes", "y", "1"}
_FALSE_VALUES = {"false", "no", "n", "0"}


class CsvImportError(ValueError):
    """Raised when a CSV file cannot be imported. ``errors`` lists every problem."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        shown = errors[:MAX_REPORTED_ERRORS]
        lines = ["CSV import failed; nothing was imported."] + shown
        if len(errors) > len(shown):
            lines.append(f"... and {len(errors) - len(shown)} more error(s)")
        super().__init__("\n".join(lines))


@dataclass
class ImportRow:
    row_number: int
    key: str | None
    parent_key: str | None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedCsv:
    rows: list[ImportRow]
    """Rows in creation order: every parent comes before its children, and
    siblings keep their file order."""
    ignored_columns: list[str]


def _coerce(attr: Any, raw: str) -> Any:
    """Convert a non-empty text cell to the attribute's value type. Raises ``ValueError``.

    Only parses text; whether the value is allowed (for example an enum
    choice) is checked by the ``validate`` callback.
    """
    if attr.type == "integer":
        text = raw.strip()
        if not _INTEGER_PATTERN.match(text):
            raise ValueError(f"'{raw}' is not an integer")
        return int(text)
    if attr.type == "boolean":
        text = raw.strip().lower()
        if text in _TRUE_VALUES:
            return True
        if text in _FALSE_VALUES:
            return False
        raise ValueError(f"'{raw}' is not a boolean (use true/false, yes/no or 1/0)")
    return raw


def parse_requirements_csv(
    attributes: Sequence[Any],
    csv_text: str,
    validate: Callable[[dict[str, Any]], list[str]] | None = None,
) -> ParsedCsv:
    """Validate and convert CSV text into requirement rows.

    Args:
        attributes: the project's attribute definitions. Each has ``.id``,
            ``.name``, ``.type`` and ``.system``.
        csv_text: the file contents. A leading UTF-8 BOM is ignored.
        validate: called with each row's typed values (keyed by attribute
            id) once all of its cells converted; returns the row's problems
            (an empty list if it is valid). Each problem is reported with the
            row number.

    Raises:
        CsvImportError: if anything in the file is invalid. Nothing is
            returned in that case.
    """
    if csv_text.startswith("﻿"):
        csv_text = csv_text[1:]

    try:
        records = list(csv.reader(io.StringIO(csv_text, newline=""), strict=True))
    except csv.Error as exc:
        raise CsvImportError([f"Malformed CSV: {exc}"]) from exc

    if not records or not any(value.strip() for value in records[0]):
        raise CsvImportError(["The file is empty or has no header row"])

    errors: list[str] = []
    header = [cell.strip() for cell in records[0]]

    by_name: dict[str, list[Any]] = {}
    for a in attributes:
        by_name.setdefault(a.name, []).append(a)
    by_id = {a.id: a for a in attributes}

    def use_id_hint(name: str) -> str:
        reserved = name in RESERVED_COLUMNS
        named = by_name.get(name, [])
        if reserved and named:
            ids = [a.id for a in named]
            return (
                f"Attribute {ids[0]!r} is also named {name!r}, which is reserved for the "
                f"{name} column; head that attribute's column with its id ({ids[0]!r})."
                if len(ids) == 1
                else f"Attributes {ids} are also named {name!r}, which is reserved for the "
                f"{name} column; head each attribute's column with its id."
            )
        if len(named) > 1:
            return (
                f"Several attributes are named {name!r}: {[a.id for a in named]}; head each "
                "column with the attribute's id instead."
            )
        return ""

    reserved_index: dict[str, int] = {}
    attr_columns: list[tuple[int, Any]] = []
    ignored_columns: list[str] = []
    seen_headers: set[str] = set()
    seen_attr_ids: set[str] = set()
    unknown: list[str] = []
    blank_columns: list[int] = []

    for index, name in enumerate(header):
        if not name:
            blank_columns.append(index)
            continue
        if name in seen_headers:
            hint = use_id_hint(name)
            errors.append(f"Duplicate column '{name}'" + (f". {hint}" if hint else ""))
            continue
        seen_headers.add(name)

        if name in RESERVED_COLUMNS:
            reserved_index[name] = index
            continue

        named = by_name.get(name, [])
        if len(named) > 1:
            errors.append(
                f"Column '{name}' matches several attributes named '{name}': "
                f"{[a.id for a in named]}. Head the column with the attribute's id instead."
            )
            continue
        attr = named[0] if named else by_id.get(name)
        if attr is None:
            unknown.append(name)
            continue
        if attr.system:
            ignored_columns.append(name)
            continue
        if attr.id in seen_attr_ids:
            errors.append(f"Column '{name}' maps to attribute '{attr.id}', which another column already uses")
            continue
        seen_attr_ids.add(attr.id)
        attr_columns.append((index, attr))

    if unknown:
        known = [a.name for a in attributes if not a.system]
        errors.append(
            f"Unknown column(s) {unknown}. Columns must be Id, Module, Parent or a "
            f"project attribute: {known}"
        )

    if errors:
        raise CsvImportError(errors)

    def cell(record: list[str], index: int | None) -> str:
        if index is None or index >= len(record):
            return ""
        return record[index]

    id_index = reserved_index.get(ID_COLUMN)
    module_index = reserved_index.get(MODULE_COLUMN)
    parent_index = reserved_index.get(PARENT_COLUMN)

    rows: list[ImportRow] = []
    module_values: set[str] = set()
    key_rows: dict[str, int] = {}

    for offset, record in enumerate(records[1:]):
        row_number = offset + 2
        if not any(value.strip() for value in record):
            continue
        if any(value.strip() for value in record[len(header):]):
            errors.append(
                f"Row {row_number}: has {len(record)} cells but the header has {len(header)} columns"
            )
            continue
        unlabeled = [i + 1 for i in blank_columns if cell(record, i).strip()]
        if unlabeled:
            errors.append(f"Row {row_number}: column(s) {unlabeled} have a value but no header")
            continue

        key = cell(record, id_index).strip() or None
        parent_key = cell(record, parent_index).strip() or None
        module_value = cell(record, module_index).strip()
        if module_value:
            module_values.add(module_value)

        if key is not None:
            if key in key_rows:
                errors.append(f"Row {row_number}: Id '{key}' is already used by row {key_rows[key]}")
            else:
                key_rows[key] = row_number

        values: dict[str, Any] = {}
        converted = True
        for index, attr in attr_columns:
            raw = cell(record, index)
            if raw.strip() == "":
                continue
            try:
                values[attr.id] = _coerce(attr, raw)
            except ValueError as exc:
                errors.append(f"Row {row_number}: {attr.name}: {exc}")
                converted = False

        if converted and validate is not None:
            errors.extend(f"Row {row_number}: {problem}" for problem in validate(values))

        rows.append(ImportRow(row_number=row_number, key=key, parent_key=parent_key, attributes=values))

    if len(module_values) > 1:
        errors.append(
            f"The Module column contains several modules {sorted(module_values)}. Import one "
            "module at a time, or remove the Module column."
        )

    if not rows and not errors:
        errors.append("The file has a header row but no requirement rows")

    by_key = {row.key: row for row in rows if row.key is not None}
    for row in rows:
        if row.parent_key is None:
            continue
        if row.parent_key == row.key:
            errors.append(f"Row {row.row_number}: a requirement cannot be its own parent")
        elif row.parent_key not in by_key:
            errors.append(
                f"Row {row.row_number}: Parent '{row.parent_key}' does not match the Id of any row in this file"
            )

    if errors:
        raise CsvImportError(errors)

    depth: dict[int, int] = {}
    for row in rows:
        chain: list[ImportRow] = []
        current: ImportRow | None = row
        visited: set[int] = set()
        while current is not None and current.row_number not in depth:
            if current.row_number in visited:
                errors.append(f"Row {row.row_number}: the Parent references form a cycle")
                break
            visited.add(current.row_number)
            chain.append(current)
            current = by_key.get(current.parent_key) if current.parent_key else None
        else:
            base = depth[current.row_number] + 1 if current is not None else 0
            for offset, item in enumerate(reversed(chain)):
                depth[item.row_number] = base + offset

    if errors:
        raise CsvImportError(errors)

    ordered = sorted(rows, key=lambda r: depth[r.row_number])
    return ParsedCsv(rows=ordered, ignored_columns=ignored_columns)
