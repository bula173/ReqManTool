"""Test result import (JUnit XML, Robot Framework output.xml) into a test_execution module.

See docs/ARCHITECTURE.md "Test result import". Git history is off except in
the commit test, which uses its own repository under ``tmp_path``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json
import os
import subprocess
from datetime import date

import pytest
from fastapi.testclient import TestClient

import reqman.test_results_import as results_module
from reqman.git_repo import GitHistory, GitSettings
from reqman.service import ProjectService
from reqman.store import ProjectStore
from reqman.test_results_import import TestResultsFormatError, parse_test_results

CHECKOUT = Path(__file__).resolve().parents[2]


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def _rpc(ws, method, params, request_id=1):
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    return json.loads(ws.receive_text())


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(directory).parts
    }


class Bench:
    """Tests (test_cases, "Test ID" attribute) and Runs (test_execution) joined by "Executes"."""

    def __init__(self, service: ProjectService, *, result_choices=("Passed", "Failed", "Blocked", "Not Run"),
                 executed_by_choices=None, version_choices=None, evidence=True, test_id_type="string"):
        self.service = service
        self.pid = service.create_project("Bench").id
        pid = self.pid
        service.add_attribute_definition(
            pid, "object_type", "Object Type", type="enum", choices=["Heading", "Test Case"]
        )
        service.add_attribute_definition(pid, "test_id", "Test ID", type=test_id_type, module_types=["test_cases"])
        service.add_attribute_definition(
            pid, "result", "Result", type="enum", choices=list(result_choices),
            default="Not Run" if "Not Run" in result_choices else None, module_types=["test_execution"],
        )
        service.add_attribute_definition(pid, "execution_date", "Execution Date", type="date", module_types=["test_execution"])
        if executed_by_choices:
            service.add_attribute_definition(
                pid, "executed_by", "Executed By", type="enum", choices=list(executed_by_choices),
                module_types=["test_execution"],
            )
        else:
            service.add_attribute_definition(pid, "executed_by", "Executed By", module_types=["test_execution"])
        if version_choices:
            service.add_attribute_definition(
                pid, "version", "Build/SW Version Under Test", type="enum", choices=list(version_choices),
                module_types=["test_execution"],
            )
        else:
            service.add_attribute_definition(pid, "version", "Build/SW Version Under Test", module_types=["test_execution"])
        if evidence:
            service.add_attribute_definition(pid, "evidence", "Evidence/Log Ref", module_types=["test_execution"])
        service.add_attribute_definition(pid, "notes", "Notes", default="imported", module_types=["test_execution"])
        self.tests = service.create_module(pid, "Tests", "TC", type="test_cases").id
        self.runs = service.create_module(pid, "Runs", "RUN", type="test_execution").id
        self.config = service.create_link_configuration(pid, "Executes", self.runs, self.tests).id

    def test_case(self, text: str, test_id=None, **values) -> str:
        attrs = {"object_text": text, **values}
        if test_id is not None:
            attrs["test_id"] = test_id
        return self.service.add_requirement(self.pid, self.tests, attrs).id

    def records(self):
        return self.service.list_requirements(self.pid, self.runs)


JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="all" timestamp="2026-04-01T09:00:00">
  <testsuite name="outer" timestamp="2026-04-02T10:00:00">
    <testcase classname="login.Suite" name="SWT-0001 login works" time="0.1"/>
    <testsuite name="inner">
      <testcase classname="login.Inner" name="test_swt_0002_logout">
        <failure message="boom">trace</failure>
      </testcase>
      <testcase classname="x" name="SWT-0003"><error message="crash"/></testcase>
    </testsuite>
  </testsuite>
  <testsuite name="late" timestamp="2026-04-05T00:00:00Z">
    <testcase classname="y" name="SWT-0004"><skipped message="later"/></testcase>
    <testcase classname="y" name="unknown test"><system-out>lots of output</system-out></testcase>
  </testsuite>
</testsuites>
"""


def _bench_with_cases(service, **kwargs):
    bench = Bench(service, **kwargs)
    cases = {n: bench.test_case(f"Case {n}", f"SWT-000{n}") for n in (1, 2, 3, 4, 5)}
    return bench, cases


# --- parser -------------------------------------------------------------------------


def test_junit_nested_suites_statuses_names_and_suite_timestamps():
    file_format, results = parse_test_results(JUNIT)
    assert file_format == "junit"
    assert [(r.index, r.name, r.full_name, r.status, r.raw_status, r.date) for r in results] == [
        (0, "SWT-0001 login works", "login.Suite.SWT-0001 login works", "passed", "passed", "2026-04-02"),
        (1, "test_swt_0002_logout", "login.Inner.test_swt_0002_logout", "failed", "failure", "2026-04-02"),
        (2, "SWT-0003", "x.SWT-0003", "failed", "error", "2026-04-02"),
        (3, "SWT-0004", "y.SWT-0004", "not_run", "skipped", "2026-04-05"),
        (4, "unknown test", "y.unknown test", "passed", "passed", "2026-04-05"),
    ]
    assert results[1].message == "boom"
    assert results[2].message == "crash"


def test_junit_single_testsuite_root_and_properties_as_keys():
    text = """<testsuite name="only" timestamp="2025-12-31T23:59:59">
      <testcase name="first">
        <properties><property name="test_id" value="REQ-77"/><property name="other">inline</property></properties>
      </testcase>
      <testcase name="second" timestamp="2026-01-15T00:00:00"/>
    </testsuite>"""
    file_format, results = parse_test_results(text)
    assert file_format == "junit"
    assert [(r.name, r.full_name, r.date) for r in results] == [
        ("first", "first", "2025-12-31"), ("second", "second", "2026-01-15"),
    ]
    assert results[0].tags == ["REQ-77", "inline"]
    assert results[0].keys() == ["first", "REQ-77", "inline"]


def test_junit_without_timestamp_has_no_date_and_bytes_input_with_bom():
    file_format, results = parse_test_results("\ufeff<testsuites><testsuite><testcase name='a'/></testsuite></testsuites>".encode())
    assert file_format == "junit"
    assert results[0].date is None


ROBOT_RF7 = """<?xml version="1.0" encoding="UTF-8"?>
<robot generator="Robot 7.0" generated="2026-05-01T08:00:00.000000" rpa="false" schemaversion="5">
<suite id="s1" name="Top">
  <suite id="s1-s1" name="Login">
    <test id="s1-s1-t1" name="Valid login">
      <kw name="Open Browser"><msg time="2026-05-02T09:00:00">noise</msg><status status="FAIL"/></kw>
      <tag>SWT-0001</tag>
      <tag>smoke</tag>
      <status status="PASS" start="2026-05-03T10:00:00.123456" elapsed="1.0"/>
    </test>
    <test id="s1-s1-t2" name="Invalid login">
      <status status="FAIL" start="2026-05-04T10:00:00.000">Expected 1 but was 2</status>
    </test>
    <test id="s1-s1-t3" name="Skipped one"><status status="SKIP"/></test>
    <test id="s1-s1-t4" name="Never"><status status="NOT RUN"/></test>
  </suite>
</suite>
<statistics><total><stat pass="1" fail="1">All Tests</stat></total></statistics>
<errors/>
</robot>
"""

ROBOT_RF3 = """<?xml version="1.0" encoding="UTF-8"?>
<robot generator="Robot 3.2.2 (Python 3.8.10 on linux)" generated="20240130 07:00:00.000">
<suite id="s1" name="Old Suite">
  <test id="s1-t1" name="Legacy">
    <tags><tag>SWT-0002</tag></tags>
    <status status="PASS" starttime="20240131 10:00:00.000" endtime="20240131 10:00:01.000"/>
  </test>
  <test id="s1-t2" name="No start"><status status="FAIL" starttime="N/A" endtime="N/A">x</status></test>
</suite>
</robot>
"""


def test_robot_rf7_start_tags_statuses_and_qualified_names():
    file_format, results = parse_test_results(ROBOT_RF7)
    assert file_format == "robot"
    assert [(r.name, r.full_name, r.status, r.raw_status, r.date) for r in results] == [
        ("Valid login", "Top.Login.Valid login", "passed", "PASS", "2026-05-03"),
        ("Invalid login", "Top.Login.Invalid login", "failed", "FAIL", "2026-05-04"),
        ("Skipped one", "Top.Login.Skipped one", "not_run", "SKIP", "2026-05-01"),
        ("Never", "Top.Login.Never", "not_run", "NOT RUN", "2026-05-01"),
    ]
    assert results[0].tags == ["SWT-0001", "smoke"]
    assert results[1].message == "Expected 1 but was 2"


def test_robot_old_starttime_tags_group_and_generated_fallback():
    file_format, results = parse_test_results(ROBOT_RF3)
    assert file_format == "robot"
    assert [(r.full_name, r.status, r.date, r.tags) for r in results] == [
        ("Old Suite.Legacy", "passed", "2024-01-31", ["SWT-0002"]),
        ("Old Suite.No start", "failed", "2024-01-30", []),
    ]


@pytest.mark.parametrize(
    "text",
    [
        '<?xml version="1.0"?><!DOCTYPE testsuites><testsuites/>',
        '<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "x">]><testsuites><testsuite name="&a;"/></testsuites>',
        '<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "aa"><!ENTITY b "&a;&a;&a;">]><testsuites/>',
        '<?xml version="1.0"?><!DOCTYPE t SYSTEM "http://example.invalid/x.dtd"><testsuites/>',
        '<?xml version="1.0"?><!DOCTYPE t [<!ENTITY x SYSTEM "file:///etc/passwd">]><testsuites>&x;</testsuites>',
    ],
)
def test_doctype_and_entities_are_refused(text):
    with pytest.raises(TestResultsFormatError) as excinfo:
        parse_test_results(text)
    assert str(excinfo.value) == (
        "The file declares a DOCTYPE or entities, which test result files never do; it is refused for safety"
    )


@pytest.mark.parametrize(
    "text, message",
    [
        ("<testsuites><testcase></testsuites>", "The file is not well-formed XML"),
        ("", "The file is not well-formed XML"),
        ("<html><body/></html>", "Unknown result file: the root element is <html>"),
    ],
)
def test_unreadable_files_are_refused(text, message):
    with pytest.raises(TestResultsFormatError) as excinfo:
        parse_test_results(text)
    assert str(excinfo.value).startswith(message)


def test_test_count_limit(monkeypatch):
    monkeypatch.setattr(results_module, "MAX_TEST_RESULTS", 2)
    two = "<testsuite><testcase name='a'/><testcase name='b'/></testsuite>"
    assert len(parse_test_results(two)[1]) == 2
    with pytest.raises(TestResultsFormatError, match="The file holds more than 2 tests"):
        parse_test_results("<testsuite><testcase name='a'/><testcase name='b'/><testcase name='c'/></testsuite>")
    robot = "<robot><suite name='s'>" + "<test name='t'><status status='PASS'/></test>" * 3 + "</suite></robot>"
    with pytest.raises(TestResultsFormatError, match="more than 2 tests"):
        parse_test_results(robot)


# --- preview / import: matching ---------------------------------------------------------------


def test_preview_matches_exact_then_whole_word_and_reports_unmatched(service):
    bench, cases = _bench_with_cases(service)
    preview = service.preview_test_results(bench.pid, bench.runs, JUNIT, {"file_name": "C:\\ci\\out\\junit.xml"})
    assert preview["format"] == "junit"
    assert preview["file_name"] == "junit.xml"
    assert preview["config"]["id"] == bench.config
    assert preview["match_attribute"] == "test_id"
    assert [o["id"] for o in preview["match_options"]] == ["@id", "object_type", "test_id"]
    states = [(r["name"], r["state"], r["match"], r["test_case_id"]) for r in preview["results"]]
    assert states == [
        ("SWT-0001 login works", "matched", "partial", cases[1]),
        ("test_swt_0002_logout", "matched", "partial", cases[2]),
        ("SWT-0003", "matched", "exact", cases[3]),
        ("SWT-0004", "matched", "exact", cases[4]),
        ("unknown test", "unmatched", None, None),
    ]
    assert preview["summary"] == {
        "total": 5, "matched": 4, "unmatched": 1, "ambiguous": 0, "passed": 2, "failed": 2, "not_run": 1,
    }
    assert preview["test_cases_not_in_file"] == [{"id": cases[5], "text": "Case 5"}]
    assert preview["errors"] == []
    assert preview["warnings"] == ["1 result(s) match no test case and are skipped"]
    assert preview["attributes"]["result"] == {"id": "result", "name": "Result"}


def test_whole_word_match_does_not_match_inside_a_word(service):
    bench = Bench(service)
    bench.test_case("Case", "SWT-0001")
    text = "<testsuite><testcase name='SWT-00011 other'/><testcase name='xSWT-0001'/></testsuite>"
    preview = service.preview_test_results(bench.pid, bench.runs, text)
    assert [r["state"] for r in preview["results"]] == ["unmatched", "unmatched"]
    assert preview["errors"] == ["No test result matches a test case of module 'Tests' by Test ID"]
    with pytest.raises(ValueError, match="No test result matches a test case of module 'Tests' by Test ID"):
        service.import_test_results(bench.pid, bench.runs, text)
    assert bench.records() == []


def test_exact_match_wins_over_partial_and_ambiguous_partial_is_skipped(service):
    bench = Bench(service)
    a = bench.test_case("A", "Login")
    b = bench.test_case("B", "Login Admin")
    c = bench.test_case("C", "Logout")
    text = """<testsuite>
      <testcase name="Login Admin"/>
      <testcase name="Login Logout"/>
      <testcase name="login"/>
    </testsuite>"""
    preview = service.preview_test_results(bench.pid, bench.runs, text)
    rows = preview["results"]
    assert (rows[0]["state"], rows[0]["match"], rows[0]["test_case_id"]) == ("matched", "exact", b)
    assert (rows[1]["state"], rows[1]["candidates"], rows[1]["test_case_id"]) == ("ambiguous", sorted([a, c]), None)
    assert (rows[2]["state"], rows[2]["test_case_id"]) == ("matched", a)
    assert "1 result(s) match several test cases and are skipped; pick a more specific match attribute" in preview["warnings"]
    result = service.import_test_results(bench.pid, bench.runs, text)
    assert result["imported"] == 2 and result["skipped"] == 1


def test_match_on_requirement_id_and_integer_attribute(service):
    bench = Bench(service, test_id_type="integer")
    first = bench.test_case("First", 42)
    second = bench.test_case("Second", 7)
    text = f"""<testsuite>
      <testcase name="case 42"/>
      <testcase name="x"><properties><property name="id" value="7"/></properties></testcase>
      <testcase name="{first} by id"/>
    </testsuite>"""
    by_attr = service.preview_test_results(bench.pid, bench.runs, text)
    assert by_attr["match_attribute"] == "test_id"
    assert [r["test_case_id"] for r in by_attr["results"]] == [first, second, None]
    by_id = service.preview_test_results(bench.pid, bench.runs, text, {"match_attribute": "@id"})
    assert [r["test_case_id"] for r in by_id["results"]] == [None, None, first]


def test_default_match_is_requirement_id_without_test_id_attribute(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "result", "Result", type="string", module_types=["test_execution"])
    tests = service.create_module(project.id, "Tests", "SWT", type="test_cases").id
    runs = service.create_module(project.id, "Runs", "RUN", type="test_execution").id
    service.create_link_configuration(project.id, "Executes", runs, tests)
    case = service.add_requirement(project.id, tests, {"object_text": "x"}).id
    preview = service.preview_test_results(project.id, runs, f"<testsuite><testcase name='test_{case}_x'/></testsuite>")
    assert preview["match_attribute"] == "@id"
    assert preview["results"][0]["test_case_id"] == case
    # A string result attribute gets the plain category value.
    service.import_test_results(project.id, runs, f"<testsuite><testcase name='{case}'><skipped/></testcase></testsuite>")
    record = service.list_requirements(project.id, runs)[0]
    assert record.attributes["result"] == "Not Run"


def test_headings_are_never_match_candidates(service):
    bench = Bench(service)
    bench.test_case("Heading", "SWT-0001", object_type="Heading")
    preview = service.preview_test_results(bench.pid, bench.runs, "<testsuite><testcase name='SWT-0001'/></testsuite>")
    assert preview["results"][0]["state"] == "unmatched"
    assert preview["test_cases_not_in_file"] == []


def test_invalid_match_attribute_is_refused(service):
    bench = Bench(service)
    bench.service.add_attribute_definition(bench.pid, "flag", "Flag", type="boolean")
    for attribute in ("flag", "nope", "result"):
        with pytest.raises(ValueError, match=f"Cannot match on '{attribute}'"):
            service.preview_test_results(bench.pid, bench.runs, JUNIT, {"match_attribute": attribute})


# --- import: records ---------------------------------------------------------------------------


def test_import_writes_records_links_mirrors_and_counters(service, tmp_path):
    bench, cases = _bench_with_cases(service)
    existing = service.add_requirement(bench.pid, bench.runs, {"object_text": "manual run"}).id
    result = service.import_test_results(
        bench.pid, bench.runs, JUNIT, {"executed_by": "  Ann  ", "version": "1.2.3", "file_name": "junit.xml"}
    )
    assert result == {
        "imported": 4,
        "requirement_ids": ["RUN-0002", "RUN-0003", "RUN-0004", "RUN-0005"],
        "linked": 4,
        "skipped": 1,
        "format": "junit",
        "config_id": bench.config,
        "test_module_id": bench.tests,
        "warnings": ["1 result(s) match no test case and are skipped"],
    }

    fresh = ProjectService(ProjectStore(tmp_path))
    records = {r.id: r for r in fresh.list_requirements(bench.pid, bench.runs)}
    first = records["RUN-0002"]
    assert first.parent_id is None
    assert [records[i].order for i in (existing, "RUN-0002", "RUN-0003", "RUN-0004", "RUN-0005")] == [0, 1, 2, 3, 4]
    assert first.attributes == {
        "object_text": "SWT-0001 login works",
        "result": "Passed",
        "execution_date": "2026-04-02",
        "executed_by": "Ann",
        "version": "1.2.3",
        "evidence": "junit.xml: login.Suite.SWT-0001 login works",
        "notes": "imported",
        bench.config: f"> {cases[1]}",
    }
    assert [records[i].attributes["result"] for i in ("RUN-0003", "RUN-0004", "RUN-0005")] == ["Failed", "Failed", "Not Run"]
    assert records["RUN-0005"].attributes["execution_date"] == "2026-04-05"

    pairs = json.loads((tmp_path / bench.pid / "links" / f"{bench.config}.json").read_text())
    assert pairs["pairs"] == [
        {"source_id": f"RUN-000{n + 1}", "target_id": cases[n]} for n in (1, 2, 3, 4)
    ]
    assert fresh.get_requirement(bench.pid, bench.tests, cases[1]).attributes[bench.config] == "< RUN-0002"
    assert bench.config not in fresh.get_requirement(bench.pid, bench.tests, cases[5]).attributes
    assert fresh.get_project(bench.pid).id_counters["RUN"] == 5

    for path in (tmp_path / bench.pid / "modules" / bench.runs / "requirements").glob("*.json"):
        text = path.read_text()
        assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"
    link_text = (tmp_path / bench.pid / "links" / f"{bench.config}.json").read_text()
    assert link_text == json.dumps(json.loads(link_text), indent=2, ensure_ascii=False) + "\n"


def test_second_import_appends_to_link_mirror_of_test_case(service):
    bench = Bench(service)
    case = bench.test_case("Case", "SWT-0001")
    text = "<testsuite><testcase name='SWT-0001'/></testsuite>"
    service.import_test_results(bench.pid, bench.runs, text)
    service.import_test_results(bench.pid, bench.runs, text.replace("/>", "><failure/></testcase>"))
    assert service.get_requirement(bench.pid, bench.tests, case).attributes[bench.config] == "< RUN-0001; < RUN-0002"
    statuses = {tc["id"]: tc["status"] for tc in service.execution_report(bench.pid, bench.runs)["test_cases"]}
    assert statuses == {case: "failed"}  # same date: the later record wins


def test_execution_date_option_overrides_file_and_today_is_the_fallback(service):
    bench = Bench(service)
    bench.test_case("Case", "SWT-0001")
    service.import_test_results(bench.pid, bench.runs, JUNIT, {"execution_date": "2026-07-07"})
    assert {r.attributes["execution_date"] for r in bench.records()} == {"2026-07-07"}
    undated = "<testsuites><testsuite><testcase name='SWT-0001'/></testsuite></testsuites>"
    preview = service.preview_test_results(bench.pid, bench.runs, undated)
    assert preview["results"][0]["execution_date"] == date.today().isoformat()


@pytest.mark.parametrize("value", ["2026-02-30", "07/07/2026", "2026-7-7"])
def test_invalid_execution_date_option_is_refused(service, value):
    bench = Bench(service)
    with pytest.raises(ValueError) as excinfo:
        service.preview_test_results(bench.pid, bench.runs, JUNIT, {"execution_date": value})
    assert str(excinfo.value) == f"Execution date '{value}' is not a date (expected YYYY-MM-DD)"


@pytest.mark.parametrize("options, message", [
    ("x", "Import options must be an object"),
    ({"executed_by": 5}, "Option 'executed_by' must be a string"),
])
def test_invalid_options_are_refused(service, options, message):
    bench = Bench(service)
    with pytest.raises(ValueError) as excinfo:
        service.preview_test_results(bench.pid, bench.runs, JUNIT, options)
    assert str(excinfo.value) == message


def test_enum_result_without_choice_for_category_refuses_the_file(service, tmp_path):
    bench = Bench(service, result_choices=("Passed", "Failed"))
    bench.test_case("A", "SWT-0001")
    bench.test_case("B", "SWT-0004")
    preview = service.preview_test_results(bench.pid, bench.runs, JUNIT)
    message = "Result attribute 'Result' has no value for not run results (choices: Passed, Failed)"
    assert preview["errors"] == [message]
    before = _snapshot(tmp_path / bench.pid)
    with pytest.raises(ValueError) as excinfo:
        service.import_test_results(bench.pid, bench.runs, JUNIT)
    assert str(excinfo.value) == message
    assert _snapshot(tmp_path / bench.pid) == before


def test_result_choices_compare_like_result_words(service):
    bench = Bench(service, result_choices=("OK", "NOK", "Skipped"))
    bench.test_case("A", "SWT-0001")
    bench.test_case("B", "SWT-0003")
    bench.test_case("C", "SWT-0004")
    service.import_test_results(bench.pid, bench.runs, JUNIT)
    assert [r.attributes["result"] for r in bench.records()] == ["OK", "NOK", "Skipped"]


@pytest.mark.parametrize("option, kwargs, value", [
    ("executed_by", {"executed_by_choices": ("Ann", "Bob")}, "Zed"),
    ("version", {"version_choices": ("1.0", "1.1")}, "2.0"),
])
def test_executed_by_and_version_values_are_validated(service, tmp_path, option, kwargs, value):
    bench = Bench(service, **kwargs)
    bench.test_case("A", "SWT-0001")
    bench.test_case("B", "SWT-0002")
    preview = service.preview_test_results(bench.pid, bench.runs, JUNIT, {option: value})
    name = "Executed By" if option == "executed_by" else "Build/SW Version Under Test"
    expected = f"{name}: '{value}' is not one of {list(kwargs[next(iter(kwargs))])}"
    assert preview["errors"] == [expected]  # listed once, not per record
    before = _snapshot(tmp_path / bench.pid)
    with pytest.raises(ValueError, match="is not one of"):
        service.import_test_results(bench.pid, bench.runs, JUNIT, {option: value})
    assert _snapshot(tmp_path / bench.pid) == before
    good = "Ann" if option == "executed_by" else "1.1"
    assert service.import_test_results(bench.pid, bench.runs, JUNIT, {option: good})["imported"] == 2


def test_missing_role_attributes_are_warnings_but_result_is_required(service):
    project = service.create_project("P")
    tests = service.create_module(project.id, "Tests", "TC", type="test_cases").id
    runs = service.create_module(project.id, "Runs", "RUN", type="test_execution").id
    service.create_link_configuration(project.id, "Executes", runs, tests)
    case = service.add_requirement(project.id, tests, {"object_text": "x"}).id
    text = f"<testsuite><testcase name='{case}'/></testsuite>"
    preview = service.preview_test_results(project.id, runs, text, {"executed_by": "Ann", "version": "1"})
    assert preview["errors"] == ["Module 'Runs' has no result attribute (e.g. 'Result') to store the test results in"]
    assert preview["warnings"] == [
        "Executed by is not stored: module 'Runs' has no such attribute",
        "Build/version is not stored: module 'Runs' has no such attribute",
        "Execution dates are not stored: module 'Runs' has no date attribute",
        "Evidence is not stored: module 'Runs' has no evidence/log attribute",
    ]
    with pytest.raises(ValueError, match="has no result attribute"):
        service.import_test_results(project.id, runs, text)


def test_error_list_is_capped(service, monkeypatch):
    import reqman.service as service_module

    monkeypatch.setattr(service_module, "MAX_RESULT_IMPORT_ERRORS", 1)
    bench = Bench(service, result_choices=("Passed",), executed_by_choices=("Ann",))
    bench.test_case("A", "SWT-0001")
    bench.test_case("B", "SWT-0002")
    preview = service.preview_test_results(bench.pid, bench.runs, JUNIT, {"executed_by": "Zed"})
    assert len(preview["errors"]) == 2
    assert preview["errors"][-1] == "... and 1 more problem(s)"


# --- target configuration -----------------------------------------------------------------------


def test_reverse_stored_link_configuration_orientation(service, tmp_path):
    bench = Bench(service)
    case = bench.test_case("Case", "SWT-0001")
    path = tmp_path / bench.pid / "project.json"
    data = json.loads(path.read_text())
    data["link_configurations"][0]["source_module_id"] = bench.tests
    data["link_configurations"][0]["target_module_id"] = bench.runs
    path.write_text(json.dumps(data, indent=2) + "\n")

    service.import_test_results(bench.pid, bench.runs, "<testsuite><testcase name='SWT-0001'/></testsuite>")
    record = bench.records()[0]
    assert record.attributes[bench.config] == f"< {case}"
    assert service.get_requirement(bench.pid, bench.tests, case).attributes[bench.config] == f"> {record.id}"
    pairs = json.loads((tmp_path / bench.pid / "links" / f"{bench.config}.json").read_text())["pairs"]
    assert pairs == [{"source_id": case, "target_id": record.id}]
    report = service.execution_report(bench.pid, bench.runs)
    assert report["test_cases"][0]["status"] == "passed"


def test_config_id_choice_unknown_and_not_joining(service):
    bench = Bench(service)
    bench.test_case("Case", "SWT-0001")
    other_tests = service.create_module(bench.pid, "More tests", "MT", type="test_cases").id
    second = service.create_link_configuration(bench.pid, "Executes more", bench.runs, other_tests).id
    reqs = service.create_module(bench.pid, "Reqs", "REQ").id
    unrelated = service.create_link_configuration(bench.pid, "Verifies", bench.tests, reqs).id

    preview = service.preview_test_results(bench.pid, bench.runs, JUNIT, {"config_id": second})
    assert preview["test_module"]["id"] == other_tests
    assert [c["id"] for c in preview["link_configurations"]] == [bench.config, second]
    with pytest.raises(FileNotFoundError, match="Link configuration 'nope' not found"):
        service.preview_test_results(bench.pid, bench.runs, JUNIT, {"config_id": "nope"})
    with pytest.raises(ValueError) as excinfo:
        service.preview_test_results(bench.pid, bench.runs, JUNIT, {"config_id": unrelated})
    assert str(excinfo.value) == (
        "Link configuration 'verifies' does not join module 'Runs' to a test cases module"
    )


def test_module_without_test_cases_link_and_wrong_module_types(service):
    project = service.create_project("P")
    runs = service.create_module(project.id, "Runs", "RUN", type="test_execution").id
    tests = service.create_module(project.id, "Tests", "TC", type="test_cases").id
    reqs = service.create_module(project.id, "Reqs", "REQ").id
    with pytest.raises(ValueError) as excinfo:
        service.preview_test_results(project.id, runs, JUNIT)
    assert str(excinfo.value) == (
        "Module 'Runs' is not linked to a test cases module: add a link configuration from it to a "
        "test_cases module first"
    )
    for module_id, kind in ((tests, "test_cases"), (reqs, "requirements")):
        with pytest.raises(ValueError) as excinfo:
            service.import_test_results(project.id, module_id, JUNIT)
        assert str(excinfo.value).endswith(f"is a {kind} module; test results are imported into a test_execution module")
    with pytest.raises(FileNotFoundError):
        service.preview_test_results(project.id, "missing", JUNIT)


def test_content_type_and_size_checks_in_service(service, monkeypatch):
    bench = Bench(service)
    with pytest.raises(ValueError, match="The result file content must be a string"):
        service.preview_test_results(bench.pid, bench.runs, 5)
    monkeypatch.setattr(results_module, "MAX_TEST_RESULTS_IMPORT_BYTES", 10)
    with pytest.raises(ValueError, match="The result file is too large"):
        service.preview_test_results(bench.pid, bench.runs, JUNIT)


def test_count_limit_refuses_import_and_writes_nothing(service, tmp_path, monkeypatch):
    bench, _ = _bench_with_cases(service)
    monkeypatch.setattr(results_module, "MAX_TEST_RESULTS", 3)
    before = _snapshot(tmp_path / bench.pid)
    with pytest.raises(ValueError, match="The file holds more than 3 tests"):
        service.import_test_results(bench.pid, bench.runs, JUNIT)
    assert _snapshot(tmp_path / bench.pid) == before


def test_preview_never_writes(service, tmp_path):
    bench, _ = _bench_with_cases(service)
    before = _snapshot(tmp_path)
    service.preview_test_results(bench.pid, bench.runs, JUNIT)
    service.preview_test_results(bench.pid, bench.runs, ROBOT_RF7)
    assert _snapshot(tmp_path) == before


def test_robot_import_matches_tags_and_writes_dates(service):
    bench = Bench(service)
    case1 = bench.test_case("One", "SWT-0001")
    case2 = bench.test_case("Two", "Invalid login")
    service.import_test_results(bench.pid, bench.runs, ROBOT_RF7, {"file_name": "output.xml"})
    records = bench.records()
    assert [(r.attributes["result"], r.attributes["execution_date"], r.attributes[bench.config]) for r in records] == [
        ("Passed", "2026-05-03", f"> {case1}"),
        ("Failed", "2026-05-04", f"> {case2}"),
    ]
    assert records[0].attributes["evidence"] == "output.xml: Top.Login.Valid login"


def test_locked_project_refuses_import_but_allows_preview(service, tmp_path):
    bench, _ = _bench_with_cases(service)
    service.lock_project(bench.pid, "Release")
    before = _snapshot(tmp_path / bench.pid)
    assert service.preview_test_results(bench.pid, bench.runs, JUNIT)["summary"]["matched"] == 4
    from reqman.service import ProjectLockedError

    with pytest.raises(ProjectLockedError):
        service.import_test_results(bench.pid, bench.runs, JUNIT)
    assert _snapshot(tmp_path / bench.pid) == before


# --- history ------------------------------------------------------------------------------------


def _git(path: Path, *args: str) -> str:
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env, check=True)
    return result.stdout


def test_import_is_one_commit_with_history(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    service = ProjectService(ProjectStore(root), history=GitHistory(GitSettings(enabled=True, squash_window_seconds=0)))
    bench, _ = _bench_with_cases(service)
    folder = root / bench.pid
    before = _git(folder, "log", "--format=%H").splitlines()
    service.preview_test_results(bench.pid, bench.runs, JUNIT)
    assert _git(folder, "log", "--format=%H").splitlines() == before
    service.import_test_results(bench.pid, bench.runs, JUNIT)
    after = _git(folder, "log", "--format=%H").splitlines()
    assert len(after) == len(before) + 1
    assert _git(folder, "status", "--porcelain") == ""
    changed = _git(folder, "show", "--name-only", "--format=", "HEAD").split()
    assert f"links/{bench.config}.json" in changed
    assert any(p.startswith(f"modules/{bench.runs}/requirements/") for p in changed)
    assert any(p.startswith(f"modules/{bench.tests}/requirements/") for p in changed)
    assert "project.json" in changed


# --- REST / WebSocket -----------------------------------------------------------------------------


def _api_bench(tmp_path):
    service = ProjectService(ProjectStore(tmp_path))
    return _bench_with_cases(service)


XML = {"Content-Type": "application/xml"}


def test_rest_preview_and_import_parity_with_ws_and_single_broadcast(client, tmp_path):
    bench, cases = _api_bench(tmp_path)
    base = f"/projects/{bench.pid}/modules/{bench.runs}/import/test-results"
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as watcher:
        preview = client.post(f"{base}/preview?executed_by=Ann&file_name=junit.xml", content=JUNIT, headers=XML)
        assert preview.status_code == 200, preview.text
        ws_preview = _rpc(ws, "requirement.preview_test_results", {
            "project_id": bench.pid, "module_id": bench.runs, "xml": JUNIT,
            "executed_by": "Ann", "file_name": "junit.xml",
        })
        assert ws_preview["result"] == preview.json()

        resp = client.post(f"{base}?executed_by=Ann&file_name=junit.xml", content=JUNIT, headers={"Content-Type": "text/xml"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["requirement_ids"] == ["RUN-0001", "RUN-0002", "RUN-0003", "RUN-0004"]
        assert json.loads(watcher.receive_text()) == {"event": "changed", "project_id": bench.pid}
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": bench.pid}

        ws_import = _rpc(ws, "requirement.import_test_results", {
            "project_id": bench.pid, "module_id": bench.runs, "xml": JUNIT,
            "executed_by": "Ann", "file_name": "junit.xml",
        }, 2)
        assert ws_import["result"]["requirement_ids"] == ["RUN-0005", "RUN-0006", "RUN-0007", "RUN-0008"]
        assert {k: v for k, v in ws_import["result"].items() if k != "requirement_ids"} == {
            k: v for k, v in resp.json().items() if k != "requirement_ids"
        }
        assert json.loads(watcher.receive_text()) == {"event": "changed", "project_id": bench.pid}
        # Exactly one broadcast per import; the sender got none of its own.
        client.post("/projects", json={"name": "Other"})
        assert json.loads(watcher.receive_text()) == {"event": "changed", "project_id": "other"}
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": "other"}
    records = client.get(f"/projects/{bench.pid}/modules/{bench.runs}/requirements").json()
    assert len(records) == 8
    assert records[0]["attributes"]["executed_by"] == "Ann"


def test_rest_preview_and_failed_import_do_not_broadcast(client, tmp_path):
    bench, _ = _api_bench(tmp_path)
    base = f"/projects/{bench.pid}/modules/{bench.runs}/import/test-results"
    before = _snapshot(tmp_path / bench.pid)
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as watcher:
        assert client.post(f"{base}/preview", content=JUNIT, headers=XML).status_code == 200
        resp = client.post(f"{base}?execution_date=2026-02-30", content=JUNIT, headers=XML)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Execution date '2026-02-30' is not a date (expected YYYY-MM-DD)"
        bad = _rpc(ws, "requirement.import_test_results", {
            "project_id": bench.pid, "module_id": bench.runs, "xml": "<!DOCTYPE x><testsuites/>",
        })
        assert bad["error"]["code"] == -32602
        assert "DOCTYPE" in bad["error"]["message"]
        assert _rpc(ws, "requirement.preview_test_results", {
            "project_id": bench.pid, "module_id": bench.runs, "xml": JUNIT,
        }, 2)["result"]["summary"]["matched"] == 4
        client.post("/projects", json={"name": "Other"})
        assert json.loads(watcher.receive_text()) == {"event": "changed", "project_id": "other"}
    assert _snapshot(tmp_path / bench.pid) == before


@pytest.mark.parametrize(
    "path, body, status",
    [
        ("/modules/runs/import/test-results", "<!DOCTYPE t><testsuites/>", 400),
        ("/modules/runs/import/test-results", "<html/>", 400),
        ("/modules/tests/import/test-results", JUNIT, 400),
        ("/modules/missing/import/test-results", JUNIT, 404),
        ("/modules/runs/import/test-results?config_id=nope", JUNIT, 404),
        ("/modules/runs/import/test-results?match_attribute=nope", JUNIT, 400),
        ("/modules/runs/import/test-results/preview?config_id=nope", JUNIT, 404),
    ],
)
def test_rest_import_errors(client, tmp_path, path, body, status):
    bench, _ = _api_bench(tmp_path)
    resp = client.post(f"/projects/{bench.pid}{path}", content=body, headers=XML)
    assert resp.status_code == status, resp.text
    assert client.get(f"/projects/{bench.pid}/modules/runs/requirements").json() == []


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/json", "multipart/form-data"])
def test_rest_import_requires_xml_content_type(client, tmp_path, content_type):
    bench, _ = _api_bench(tmp_path)
    headers = {"Content-Type": content_type} if content_type else {}
    for suffix in ("", "/preview"):
        resp = client.post(
            f"/projects/{bench.pid}/modules/{bench.runs}/import/test-results{suffix}", content=JUNIT, headers=headers
        )
        assert resp.status_code == 415
        assert resp.json()["detail"] == "The result file must be sent with Content-Type application/xml or text/xml"


def test_rest_body_size_limit(client, tmp_path, monkeypatch):
    import reqman.app as app_module

    bench, _ = _api_bench(tmp_path)
    monkeypatch.setattr(app_module, "MAX_TEST_RESULTS_IMPORT_BYTES", 100)
    resp = client.post(f"/projects/{bench.pid}/modules/{bench.runs}/import/test-results", content=JUNIT, headers=XML)
    assert resp.status_code == 413
    assert resp.json()["detail"].startswith("The result file is too large")


def test_locked_project_rest_and_ws_refuse_import_allow_preview(client, tmp_path):
    bench, _ = _api_bench(tmp_path)
    assert client.put(f"/projects/{bench.pid}/lock", json={"reason": "Release"}).status_code == 200
    base = f"/projects/{bench.pid}/modules/{bench.runs}/import/test-results"
    assert client.post(f"{base}/preview", content=JUNIT, headers=XML).status_code == 200
    resp = client.post(base, content=JUNIT, headers=XML)
    assert resp.status_code == 423
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "requirement.import_test_results", {
            "project_id": bench.pid, "module_id": bench.runs, "xml": JUNIT,
        })
        assert response["error"]["code"] == -32004
        preview = _rpc(ws, "requirement.preview_test_results", {
            "project_id": bench.pid, "module_id": bench.runs, "xml": JUNIT,
        }, 2)
        assert preview["result"]["summary"]["matched"] == 4
    assert client.get(f"/projects/{bench.pid}/modules/{bench.runs}/requirements").json() == []


def test_verification_status_follows_imported_results(client, tmp_path):
    service = ProjectService(ProjectStore(tmp_path))
    bench, cases = _bench_with_cases(service)
    reqs = service.create_module(bench.pid, "Reqs", "REQ").id
    verifies = service.create_link_configuration(bench.pid, "Verifies", bench.tests, reqs).id
    r1 = service.add_requirement(bench.pid, reqs, {"object_text": "R1"}).id
    r2 = service.add_requirement(bench.pid, reqs, {"object_text": "R2"}).id
    r3 = service.add_requirement(bench.pid, reqs, {"object_text": "R3"}).id
    service.link_requirements(bench.pid, verifies, cases[1], r1)
    service.link_requirements(bench.pid, verifies, cases[2], r2)
    url = f"/projects/{bench.pid}/modules/{reqs}/verification-status"
    assert client.get(url).json()["statuses"] == {r1: "not_run", r2: "not_run", r3: "not_covered"}
    client.post(f"/projects/{bench.pid}/modules/{bench.runs}/import/test-results", content=JUNIT, headers=XML)
    assert client.get(url).json()["statuses"] == {r1: "verified", r2: "failed", r3: "not_covered"}
    # Never stored: no requirement file or project.json mentions it.
    assert "verification" not in (tmp_path / bench.pid / "project.json").read_text().lower()
    assert client.get(f"/projects/{bench.pid}/modules/{bench.runs}/verification-status").status_code == 400


# --- review regressions -------------------------------------------------------------------


def test_partial_match_of_a_very_long_name_stays_linear(service):
    """Partial matching only tries runs of words as long as a key (not every run: O(n^3))."""
    import time

    bench, cases = _bench_with_cases(service)
    filler = " ".join(f"w{i}" for i in range(5000))
    text = (
        "<testsuite>"
        f"<testcase name='{filler} SWT-0001 {filler}'/>"
        f"<testcase name='{filler}'/>"
        f"<testcase name='{filler} SWT-0002 {filler}'/>"
        "</testsuite>"
    )
    started = time.monotonic()
    preview = service.preview_test_results(bench.pid, bench.runs, text)
    assert time.monotonic() - started < 2
    assert [(r["state"], r["match"], r["test_case_id"]) for r in preview["results"]] == [
        ("matched", "partial", cases[1]), ("unmatched", None, None), ("matched", "partial", cases[2]),
    ]


def test_partial_match_semantics_with_multi_word_keys(service):
    bench = Bench(service)
    login = bench.test_case("Login", "Login Works")
    both = bench.test_case("Login twice", "login works twice")
    text = (
        "<testsuite>"
        "<testcase name='suite login works now'/>"
        "<testcase name='x login works twice y'/>"
        "<testcase name='Login Works'/>"
        "</testsuite>"
    )
    preview = service.preview_test_results(bench.pid, bench.runs, text)
    assert [(r["state"], r["match"], r["test_case_id"], r["candidates"]) for r in preview["results"]] == [
        ("matched", "partial", login, []),
        ("ambiguous", None, None, sorted([login, both])),
        ("matched", "exact", login, []),
    ]


DEPTH = 5000


@pytest.mark.parametrize(
    "text, count",
    [
        ("<testsuites>" + "<testsuite name='s'>" * DEPTH + "<testcase name='deep'/>"
         + "</testsuite>" * DEPTH + "</testsuites>", 1),
        ("<robot><suite name='top'>" + "<suite name='s'>" * DEPTH + "<test name='deep'><status status='PASS'/></test>"
         + "</suite>" * DEPTH + "</suite></robot>", 1),
    ],
    ids=["junit", "robot"],
)
def test_deeply_nested_suites_do_not_hit_the_recursion_limit(text, count):
    _, results = parse_test_results(text)
    assert len(results) == count
    assert results[0].name == "deep"
    assert results[0].full_name.endswith(".deep") or results[0].full_name == "deep"


def test_deeply_nested_robot_suites_keep_qualified_names_and_order():
    text = (
        "<robot><suite name='A'><suite name='B'><test name='t1'><status status='PASS'/></test></suite>"
        "<test name='t2'><status status='FAIL'/></test><suite name='C'><test name='t3'/></suite></suite></robot>"
    )
    _, results = parse_test_results(text)
    assert [r.full_name for r in results] == ["A.B.t1", "A.t2", "A.C.t3"]


def test_rest_deeply_nested_file_imports_without_server_error(client, tmp_path):
    bench, _ = _api_bench(tmp_path)
    text = "<testsuites>" + "<testsuite>" * DEPTH + "<testcase name='SWT-0001'/>" + "</testsuite>" * DEPTH + "</testsuites>"
    resp = client.post(f"/projects/{bench.pid}/modules/{bench.runs}/import/test-results/preview", content=text, headers=XML)
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["matched"] == 1


class _ReviewGuard:
    """Permission guard (server edition) refusing the listed actions; allows everything else."""

    def __init__(self, *denied):
        self.denied = set(denied)

    def check(self, identity, action, project_id=None):
        from reqman.extensions import AccessDeniedError

        if action in self.denied:
            raise AccessDeniedError(f"{action} denied")


@pytest.mark.parametrize(
    "default, denied, error",
    [
        ("Reviewed", (), "A new requirement cannot start as Reviewed"),
        ("Draft", ("project.review",), "project.review denied"),
    ],
)
def test_import_applies_the_status_guard_to_new_records(service, tmp_path, monkeypatch, default, denied, error):
    from reqman import extensions
    from reqman.extensions import AccessDeniedError

    bench, _ = _bench_with_cases(service)
    service.add_attribute_definition(
        bench.pid, "status", "Status", type="enum", choices=["Draft", "Reviewed", "Approved"], default=default,
        module_types=["test_execution"],
    )
    before = _snapshot(tmp_path / bench.pid)
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _ReviewGuard(*denied))
    with pytest.raises(AccessDeniedError, match=error):
        service.import_test_results(bench.pid, bench.runs, JUNIT)
    assert _snapshot(tmp_path / bench.pid) == before


def test_import_with_status_guard_allowed_writes_records(service, monkeypatch):
    from reqman import extensions

    bench, _ = _bench_with_cases(service)
    service.add_attribute_definition(
        bench.pid, "status", "Status", type="enum", choices=["Draft", "Reviewed"], default="Draft",
        module_types=["test_execution"],
    )
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _ReviewGuard())
    assert service.import_test_results(bench.pid, bench.runs, JUNIT)["imported"] == 4
    assert {r.attributes["status"] for r in bench.records()} == {"Draft"}
