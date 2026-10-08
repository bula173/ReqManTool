"""Parse automated test results (JUnit XML, Robot Framework output.xml).

``parse_test_results`` turns a result file into a flat list of
``TestResult`` -- one per executed test -- that ``ProjectService``
(``preview_test_results`` / ``import_test_results``) matches to test cases
and writes as execution records. This module knows nothing about projects:
it only reads the file. See docs/ARCHITECTURE.md "Test result import".

Parsing is safe without an extra dependency: the file is read with the
standard library's expat parser directly, and any DOCTYPE, entity
declaration or external entity reference rejects the file (JUnit and Robot
Framework results never contain one), so entity expansion attacks ("billion
laughs") and external entity reads (XXE) cannot happen. Subtrees the import
never reads (Robot keywords and log messages, JUnit captured output) are
skipped while parsing, so large logs do not build a large tree.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from xml.parsers import expat

# Upper bound on an uploaded result file (Robot output.xml files carry logs).
MAX_TEST_RESULTS_IMPORT_BYTES = 64 * 1024 * 1024

# Upper bound on the tests one file may hold (each one becomes a requirement file).
MAX_TEST_RESULTS = 10_000

# Longest failure message kept per result (preview only; never stored).
_MAX_MESSAGE = 500

# Robot Framework elements whose subtrees the import never reads.
_ROBOT_SKIPPED = frozenset({
    "kw", "for", "while", "if", "try", "group", "msg", "arg", "var", "return",
    "break", "continue", "iter", "branch", "timeout", "variable", "statistics", "errors",
})
# JUnit elements whose subtrees the import never reads (captured output can be large).
_JUNIT_SKIPPED = frozenset({"system-out", "system-err"})

_DATE_PREFIX = re.compile(r"^\s*([0-9]{4})-?([0-9]{2})-?([0-9]{2})")


class TestResultsFormatError(ValueError):
    """The file is not a readable JUnit XML or Robot Framework output.xml file."""

    __test__ = False  # not a pytest test class, despite the name


@dataclass
class TestResult:
    """One executed test of a result file."""

    __test__ = False  # not a pytest test class, despite the name

    # 0-based position in the file.
    index: int
    # The test's own name (JUnit ``name``, Robot test ``name``).
    name: str
    # Qualified name: JUnit ``classname.name``, Robot ``Suite.Sub Suite.Test``.
    full_name: str
    # ``passed | failed | not_run`` (the report result categories).
    status: str
    # The status as the file wrote it (``PASS``, ``failure``...).
    raw_status: str
    message: str = ""
    # The execution date found in the file (``YYYY-MM-DD``), if any.
    date: str | None = None
    # Further exact match keys: Robot tags, JUnit property values.
    tags: list[str] = field(default_factory=list)

    def keys(self) -> list[str]:
        """Every text the test can be matched on, most specific first."""
        keys = [self.name, self.full_name, *self.tags]
        seen: set[str] = set()
        return [k for k in keys if k and not (k in seen or seen.add(k))]


def _safe_tree(data: bytes | str) -> ET.Element:
    """The document element of ``data``, refusing DTDs and entities, skipping unread subtrees."""
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate()
    parser.buffer_text = True
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    state: dict[str, Any] = {"skip": 0, "skipped": frozenset(), "root": None}

    def refuse(*_args: Any) -> None:
        raise TestResultsFormatError(
            "The file declares a DOCTYPE or entities, which test result files never do; it is refused for safety"
        )

    def start(tag: str, attrs: dict[str, str]) -> None:
        if state["root"] is None:
            state["root"] = tag
            state["skipped"] = _ROBOT_SKIPPED if tag == "robot" else _JUNIT_SKIPPED
        if state["skip"]:
            state["skip"] += 1
            return
        if tag in state["skipped"]:
            state["skip"] = 1
            return
        builder.start(tag, attrs)

    def end(tag: str) -> None:
        if state["skip"]:
            state["skip"] -= 1
            return
        builder.end(tag)

    def data_handler(text: str) -> None:
        if not state["skip"]:
            builder.data(text)

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = data_handler
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise TestResultsFormatError(f"The file is not well-formed XML: {exc}") from exc
    try:
        return builder.close()
    except Exception as exc:  # noqa: BLE001 - an empty document
        raise TestResultsFormatError("The file is not well-formed XML: it has no root element") from exc


def _date_of(value: str | None) -> str | None:
    """The ``YYYY-MM-DD`` date at the start of a timestamp (ISO or Robot's ``20240131 10:00:00``)."""
    if not value:
        return None
    match = _DATE_PREFIX.match(value)
    if not match:
        return None
    text = "-".join(match.groups())
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def _message(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= _MAX_MESSAGE else text[: _MAX_MESSAGE - 1] + "…"


def _check_count(results: list[TestResult]) -> None:
    if len(results) > MAX_TEST_RESULTS:
        raise TestResultsFormatError(f"The file holds more than {MAX_TEST_RESULTS} tests")


# --- JUnit XML -------------------------------------------------------------------


def _junit_results(root: ET.Element) -> list[TestResult]:
    results: list[TestResult] = []

    def walk(element: ET.Element, timestamp: str | None) -> None:
        for child in element:
            if child.tag == "testsuite":
                walk(child, child.get("timestamp") or timestamp)
            elif child.tag == "testcase":
                results.append(_junit_case(child, len(results), child.get("timestamp") or timestamp))
                _check_count(results)

    if root.tag == "testsuite":
        # A single suite as the document element: walk it like a nested one.
        wrapper = ET.Element("testsuites")
        wrapper.append(root)
        walk(wrapper, None)
    else:
        walk(root, root.get("timestamp"))
    return results


def _junit_case(case: ET.Element, index: int, timestamp: str | None) -> TestResult:
    name = (case.get("name") or "").strip()
    classname = (case.get("classname") or "").strip()
    status, raw, message = "passed", "passed", ""
    for child in case:
        if child.tag in ("failure", "error"):
            status, raw = "failed", child.tag
            message = child.get("message") or child.text or ""
            break
        if child.tag == "skipped":
            status, raw = "not_run", "skipped"
            message = child.get("message") or child.text or ""
    tags = [
        (prop.get("value") or prop.text or "").strip()
        for properties in case.findall("properties")
        for prop in properties.findall("property")
    ]
    return TestResult(
        index=index,
        name=name,
        full_name=f"{classname}.{name}" if classname and name else (name or classname),
        status=status,
        raw_status=raw,
        message=_message(message),
        date=_date_of(timestamp),
        tags=[t for t in tags if t],
    )


# --- Robot Framework output.xml -----------------------------------------------------------

_ROBOT_STATUS = {"PASS": "passed", "FAIL": "failed", "SKIP": "not_run", "NOT RUN": "not_run", "NOT_RUN": "not_run"}


def _robot_results(root: ET.Element) -> list[TestResult]:
    results: list[TestResult] = []
    generated = root.get("generated")

    def walk(suite: ET.Element, names: list[str]) -> None:
        own = names + [suite.get("name") or ""]
        for child in suite:
            if child.tag == "suite":
                walk(child, own)
            elif child.tag == "test":
                results.append(_robot_test(child, own, len(results), generated))
                _check_count(results)

    for suite in root.findall("suite"):
        walk(suite, [])
    return results


def _robot_test(test: ET.Element, suites: list[str], index: int, generated: str | None) -> TestResult:
    name = (test.get("name") or "").strip()
    status_element = test.find("status")
    raw = (status_element.get("status") or "").strip().upper() if status_element is not None else ""
    start = None
    message = ""
    if status_element is not None:
        # RF 7 writes ``start`` (ISO); older versions ``starttime`` (20240131 10:00:00.000).
        start = status_element.get("start") or status_element.get("starttime")
        if start in ("N/A", ""):
            start = None
        message = status_element.text or ""
    tags = [(t.text or "").strip() for t in test.findall("tag")]
    tags += [(t.text or "").strip() for group in test.findall("tags") for t in group.findall("tag")]
    return TestResult(
        index=index,
        name=name,
        full_name=".".join(part for part in [*suites, name] if part),
        status=_ROBOT_STATUS.get(raw, "not_run"),
        raw_status=raw or "NOT RUN",
        message=_message(message),
        date=_date_of(start) or _date_of(generated),
        tags=[t for t in tags if t],
    )


def parse_test_results(data: bytes | str) -> tuple[str, list[TestResult]]:
    """``(format, results)`` of a JUnit XML or Robot Framework output.xml file.

    ``format`` is ``junit`` or ``robot``, told by the document element
    (``testsuites``/``testsuite`` or ``robot``). Raises
    ``TestResultsFormatError`` for anything else, for malformed XML, a
    DOCTYPE or entities, or more than ``MAX_TEST_RESULTS`` tests.
    """
    if isinstance(data, str):
        data = data.lstrip("﻿")
    root = _safe_tree(data)
    if root.tag in ("testsuites", "testsuite"):
        return "junit", _junit_results(root)
    if root.tag == "robot":
        return "robot", _robot_results(root)
    raise TestResultsFormatError(
        f"Unknown result file: the root element is <{root.tag}>; expected JUnit XML "
        "(<testsuites> or <testsuite>) or a Robot Framework output.xml (<robot>)"
    )
