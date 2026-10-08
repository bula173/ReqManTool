"""Module types (project format 5), type-scoped attributes, link type pairs and ``date`` attributes.

See docs/ARCHITECTURE.md "Module types and date attributes". Every test works
in pytest's ``tmp_path``; git history is off (``conftest.py``).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import io
import json

import openpyxl
import pytest
from fastapi.testclient import TestClient

from reqman.formats import PROJECT_FORMAT
from reqman.models import MODULE_TYPES
from reqman.service import ATTRIBUTE_TYPES, LINK_TYPE_PAIRS, ProjectService
from reqman.store import ProjectStore

RBC = "builtin:rbc_template"


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
    return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


def _assert_json_file_style(path: Path) -> dict:
    text = path.read_text()
    data = json.loads(text)
    assert text == json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return data


def _requirement_file(root: Path, project_id: str, module_id: str, requirement_id: str) -> Path:
    return root / project_id / "modules" / module_id / "requirements" / f"{requirement_id}.json"


def _three_types(service):
    project = service.create_project("P")
    reqs = service.create_module(project.id, "Reqs", "REQ")
    tests = service.create_module(project.id, "Tests", "TC", type="test_cases")
    execs = service.create_module(project.id, "Exec", "EX", type="test_execution")
    return project, reqs, tests, execs


DATE_ERROR = "Run On: '{}' is not a date (expected YYYY-MM-DD)"


# --- attribute types ---------------------------------------------------------------


def test_attribute_types_include_date():
    assert ATTRIBUTE_TYPES == ("string", "text", "integer", "boolean", "enum", "date")
    assert MODULE_TYPES == ("requirements", "test_cases", "test_execution")
    assert LINK_TYPE_PAIRS == (
        ("requirements", "requirements"),
        ("test_cases", "requirements"),
        ("test_execution", "test_cases"),
    )


# --- date values -----------------------------------------------------------------


@pytest.mark.parametrize("value", ["2026-01-31", "2024-02-29", "0001-01-01", "9999-12-31"])
def test_date_attribute_accepts_existing_calendar_dates_and_persists(service, tmp_path, value):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    module = service.create_module(project.id, "Reqs", "REQ")
    requirement = service.add_requirement(project.id, module.id, {"object_text": "x", "run_on": value})

    fresh = ProjectService(ProjectStore(tmp_path))
    assert fresh.get_requirement(project.id, module.id, requirement.id).attributes["run_on"] == value
    _assert_json_file_style(_requirement_file(tmp_path, project.id, module.id, requirement.id))


@pytest.mark.parametrize(
    "value",
    ["2026-02-30", "2025-02-29", "2026-13-01", "2026-00-10", "2026-1-1", "02/01/2026", " 2026-01-01",
     "2026-01-01 ", "2026-01-01T00:00:00", "2026-01-01\n", "20260101", "", "today", "２０２６-01-01"],
)
def test_date_attribute_rejects_invalid_strings_on_add_and_update(service, value):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    module = service.create_module(project.id, "Reqs", "REQ")
    with pytest.raises(ValueError) as excinfo:
        service.add_requirement(project.id, module.id, {"object_text": "x", "run_on": value})
    assert str(excinfo.value) == DATE_ERROR.format(value)
    assert service.list_requirements(project.id, module.id) == []

    requirement = service.add_requirement(project.id, module.id, {"object_text": "x", "run_on": "2026-01-01"})
    with pytest.raises(ValueError) as excinfo:
        service.update_requirement(project.id, module.id, requirement.id, {"run_on": value})
    assert str(excinfo.value) == DATE_ERROR.format(value)
    assert service.get_requirement(project.id, module.id, requirement.id).attributes["run_on"] == "2026-01-01"


@pytest.mark.parametrize("value", [20260101, 2026.0, True, ["2026-01-01"], {"d": "2026-01-01"}])
def test_date_attribute_rejects_non_string_values(service, value):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    module = service.create_module(project.id, "Reqs", "REQ")
    with pytest.raises(ValueError, match="is not a date"):
        service.add_requirement(project.id, module.id, {"object_text": "x", "run_on": value})


def test_date_attribute_null_clears_the_value(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    module = service.create_module(project.id, "Reqs", "REQ")
    requirement = service.add_requirement(project.id, module.id, {"object_text": "x", "run_on": "2026-01-01"})
    updated = service.update_requirement(project.id, module.id, requirement.id, {"run_on": None})
    assert updated.attributes.get("run_on") is None


@pytest.mark.parametrize("default", ["2026-02-30", "2026-1-1", "01.01.2026", 5, True])
def test_date_attribute_default_is_validated(service, default):
    project = service.create_project("P")
    with pytest.raises(ValueError) as excinfo:
        service.add_attribute_definition(project.id, "run_on", "Run On", type="date", default=default)
    assert str(excinfo.value) == f"Invalid default value: Run On: '{default}' is not a date (expected YYYY-MM-DD)"
    assert [a.id for a in service.get_project(project.id).attributes] == ["object_text"]


def test_date_attribute_valid_default_is_merged_into_new_requirements(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date", default="2026-06-30")
    module = service.create_module(project.id, "Reqs", "REQ")
    requirement = service.add_requirement(project.id, module.id, {"object_text": "x"})
    assert requirement.attributes["run_on"] == "2026-06-30"


def test_date_attribute_cannot_have_choices_and_required_date_needs_non_blank_default(service):
    project = service.create_project("P")
    with pytest.raises(ValueError, match="Attribute 'Run On' of type 'date' cannot have choices"):
        service.add_attribute_definition(project.id, "run_on", "Run On", type="date", choices=["2026-01-01"])
    with pytest.raises(ValueError):
        service.add_attribute_definition(project.id, "run_on", "Run On", type="date", required=True, default="  ")
    assert [a.id for a in service.get_project(project.id).attributes] == ["object_text"]


def test_stored_invalid_date_value_reads_back_unchanged(service, tmp_path):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    module = service.create_module(project.id, "Reqs", "REQ")
    requirement = service.add_requirement(project.id, module.id, {"object_text": "x", "run_on": "2026-01-01"})
    path = _requirement_file(tmp_path, project.id, module.id, requirement.id)
    data = json.loads(path.read_text())
    data["attributes"]["run_on"] = "31/01/2026"
    path.write_text(json.dumps(data, indent=2) + "\n")
    before = path.read_bytes()

    assert service.get_requirement(project.id, module.id, requirement.id).attributes["run_on"] == "31/01/2026"
    # Updating another attribute does not re-validate (or rewrite) the stored legacy value.
    service.update_requirement(project.id, module.id, requirement.id, {"object_text": "y"})
    assert json.loads(path.read_text())["attributes"]["run_on"] == "31/01/2026"
    assert path.read_bytes() != before  # object_text changed


# --- date over REST / WebSocket ----------------------------------------------------


def test_date_attribute_rest_and_ws_parity(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Reqs", "prefix": "REQ"}).json()["id"]
    resp = client.post(f"/projects/{project_id}/attributes", json={"id": "run_on", "name": "Run On", "type": "date"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["attributes"][-1]["type"] == "date"
    url = f"/projects/{project_id}/modules/{module_id}/requirements"

    resp = client.post(url, json={"attributes": {"object_text": "x", "run_on": "2026-02-30"}})
    assert resp.status_code == 400
    assert resp.json()["detail"] == DATE_ERROR.format("2026-02-30")
    resp = client.post(url, json={"attributes": {"object_text": "x", "run_on": "2026-02-28"}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["attributes"]["run_on"] == "2026-02-28"

    with client.websocket_connect("/ws") as ws:
        bad = _rpc(ws, "requirement.add", {
            "project_id": project_id, "module_id": module_id,
            "attributes": {"object_text": "y", "run_on": "2026-2-1"},
        })
        assert bad["error"] == {"code": -32602, "message": DATE_ERROR.format("2026-2-1")}
        good = _rpc(ws, "requirement.add", {
            "project_id": project_id, "module_id": module_id,
            "attributes": {"object_text": "y", "run_on": "2028-02-29"},
        }, request_id=2)
        assert good["result"]["attributes"]["run_on"] == "2028-02-29"
        bad_default = _rpc(ws, "attribute.add", {
            "project_id": project_id, "id": "due", "name": "Due", "type": "date", "default": "2026-04-31",
        }, request_id=3)
        assert bad_default["error"] == {
            "code": -32602, "message": "Invalid default value: Due: '2026-04-31' is not a date (expected YYYY-MM-DD)",
        }
    resp = client.post(f"/projects/{project_id}/attributes", json={
        "id": "due", "name": "Due", "type": "date", "default": "2026-04-31",
    })
    assert resp.status_code == 400
    assert [r["id"] for r in client.get(url).json()] == ["REQ-0001", "REQ-0002"]


# --- date in CSV import / exports ---------------------------------------------------


def test_csv_import_strips_blanks_around_dates_and_treats_empty_as_unset(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    module = service.create_module(project.id, "Reqs", "REQ")
    result = service.import_csv(project.id, module.id, "ObjectText,Run On\nA,  2026-01-31 \nB,\nC,\t2024-02-29\t\n")
    assert result["imported"] == 3
    values = {r.attributes["object_text"]: r.attributes.get("run_on") for r in service.list_requirements(project.id, module.id)}
    assert values == {"A": "2026-01-31", "B": None, "C": "2024-02-29"}


@pytest.mark.parametrize("cell", ["2026-02-30", "31.01.2026", "2026-1-31", "tomorrow"])
def test_csv_import_invalid_date_row_refuses_the_whole_file(service, tmp_path, cell):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    module = service.create_module(project.id, "Reqs", "REQ")
    before = _snapshot(tmp_path / project.id)
    with pytest.raises(ValueError) as excinfo:
        service.import_csv(project.id, module.id, f"ObjectText,Run On\nA,2026-01-31\nB, {cell} \n")
    assert f"Row 3: Run On: '{cell}' is not a date (expected YYYY-MM-DD)" in str(excinfo.value)
    assert service.list_requirements(project.id, module.id) == []
    assert _snapshot(tmp_path / project.id) == before


def test_csv_export_writes_date_string_and_reimports(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    module = service.create_module(project.id, "Reqs", "REQ")
    service.add_requirement(project.id, module.id, {"object_text": "x", "run_on": "2026-03-01"})
    csv_text = service.export_csv(project.id)
    assert "2026-03-01" in csv_text
    other = service.create_module(project.id, "Copy", "CPY")
    rows = [line for line in csv_text.splitlines()]
    reimport = "ObjectText,Run On\n" + "\n".join(
        ",".join(line.split(",")[3:]) for line in rows[1:]
    ) + "\n"
    service.import_csv(project.id, other.id, reimport)
    assert service.list_requirements(project.id, other.id)[0].attributes["run_on"] == "2026-03-01"


def test_xlsx_export_writes_real_date_cells_and_invalid_stored_values_as_text(service, tmp_path):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    module = service.create_module(project.id, "Reqs", "REQ")
    service.add_requirement(project.id, module.id, {"object_text": "good", "run_on": "2026-01-31"})
    bad = service.add_requirement(project.id, module.id, {"object_text": "bad", "run_on": "2026-01-01"})
    service.add_requirement(project.id, module.id, {"object_text": "none"})
    path = _requirement_file(tmp_path, project.id, module.id, bad.id)
    data = json.loads(path.read_text())
    data["attributes"]["run_on"] = "2026-02-30"
    path.write_text(json.dumps(data, indent=2) + "\n")

    workbook = openpyxl.load_workbook(io.BytesIO(service.export_xlsx(project.id)))
    sheet = workbook.worksheets[0]
    header = [c.value for c in sheet[1]]
    column = header.index("Run On")
    by_text = {row[header.index("ObjectText")].value: row[column] for row in sheet.iter_rows(min_row=2)}
    good = by_text["good"]
    assert good.is_date
    assert good.value.date().isoformat() == "2026-01-31"
    assert good.number_format == "yyyy-mm-dd"
    assert by_text["bad"].value == "2026-02-30"
    assert not by_text["bad"].is_date
    assert by_text["none"].value is None


def test_reqif_declares_date_datatype_only_when_a_date_attribute_exists(service, tmp_path):
    project = service.create_project("P")
    module = service.create_module(project.id, "Reqs", "REQ")
    service.add_requirement(project.id, module.id, {"object_text": "x"})
    assert "DATATYPE-DEFINITION-DATE" not in service.export_reqif(project.id)
    assert "ATTRIBUTE-DEFINITION-DATE" not in service.export_reqif(project.id)

    service.add_attribute_definition(project.id, "run_on", "Run On", type="date")
    good = service.add_requirement(project.id, module.id, {"object_text": "good", "run_on": "2026-01-31"})
    bad = service.add_requirement(project.id, module.id, {"object_text": "bad", "run_on": "2026-01-01"})
    path = _requirement_file(tmp_path, project.id, module.id, bad.id)
    data = json.loads(path.read_text())
    data["attributes"]["run_on"] = "not-a-date"
    path.write_text(json.dumps(data, indent=2) + "\n")

    reqif = service.export_reqif(project.id)
    assert reqif.count("<DATATYPE-DEFINITION-DATE ") == 1
    assert '<ATTRIBUTE-DEFINITION-DATE IDENTIFIER="run_on" LONG-NAME="Run On">' in reqif
    assert reqif.count("<ATTRIBUTE-VALUE-DATE ") == 1
    assert 'THE-VALUE="2026-01-31T00:00:00"' in reqif
    assert "not-a-date" not in reqif
    assert good.id in reqif and bad.id in reqif


# --- module types: service ----------------------------------------------------------


@pytest.mark.parametrize("module_type", ["test_cases", "test_execution"])
def test_create_module_with_type_writes_type_last_in_module_json(service, tmp_path, module_type):
    project = service.create_project("P")
    module = service.create_module(project.id, "Mod", "M", type=module_type)
    assert module.type == module_type
    data = _assert_json_file_style(tmp_path / project.id / "modules" / module.id / "module.json")
    assert list(data) == ["id", "name", "prefix", "description", "type"]
    assert data["type"] == module_type
    assert ProjectService(ProjectStore(tmp_path)).get_module(project.id, module.id).type == module_type


@pytest.mark.parametrize("module_type", [None, "requirements"])
def test_requirements_module_json_has_no_type_key(service, tmp_path, module_type):
    project = service.create_project("P")
    module = service.create_module(project.id, "Mod", "M", type=module_type)
    assert module.type == "requirements"
    data = _assert_json_file_style(tmp_path / project.id / "modules" / module.id / "module.json")
    assert list(data) == ["id", "name", "prefix", "description"]
    assert "type" not in module.to_dict()


@pytest.mark.parametrize("module_type", ["Requirements", "test-cases", "", "tests", 5, ["test_cases"]])
def test_create_module_rejects_unknown_type_and_writes_nothing(service, tmp_path, module_type):
    project = service.create_project("P")
    before = _snapshot(tmp_path / project.id)
    with pytest.raises(ValueError) as excinfo:
        service.create_module(project.id, "Mod", "M", type=module_type)
    assert str(excinfo.value) == (
        f"Unknown module type {module_type!r}; expected one of requirements, test_cases, test_execution"
    )
    assert service.list_modules(project.id) == []
    assert _snapshot(tmp_path / project.id) == before


@pytest.mark.parametrize("stored", ["bogus", "Test_Cases", 7, None])
def test_unknown_stored_module_type_reads_as_requirements_and_is_not_rewritten(service, tmp_path, stored):
    project = service.create_project("P")
    module = service.create_module(project.id, "Mod", "M", type="test_cases")
    path = tmp_path / project.id / "modules" / module.id / "module.json"
    data = json.loads(path.read_text())
    data["type"] = stored
    path.write_text(json.dumps(data, indent=2) + "\n")
    before = path.read_bytes()

    assert service.get_module(project.id, module.id).type == "requirements"
    assert [m.type for m in service.list_modules(project.id)] == ["requirements"]
    assert path.read_bytes() == before


# --- module types: REST / WebSocket --------------------------------------------------


def test_create_module_type_rest_and_ws(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    resp = client.post(f"/projects/{project_id}/modules", json={"name": "Tests", "prefix": "TC", "type": "test_cases"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "test_cases"
    resp = client.post(f"/projects/{project_id}/modules", json={"name": "Reqs", "prefix": "REQ"})
    assert "type" not in resp.json()

    with client.websocket_connect("/ws") as ws:
        created = _rpc(ws, "module.create", {
            "project_id": project_id, "name": "Exec", "prefix": "EX", "type": "test_execution",
        })
        assert created["result"]["type"] == "test_execution"
        plain = _rpc(ws, "module.create", {"project_id": project_id, "name": "More", "prefix": "MR"}, 2)
        assert "type" not in plain["result"]

    listed = {m["id"]: m.get("type") for m in client.get(f"/projects/{project_id}/modules").json()}
    assert listed == {"tests": "test_cases", "reqs": None, "exec": "test_execution", "more": None}
    assert client.get(f"/projects/{project_id}/modules/exec").json()["type"] == "test_execution"


def test_create_module_invalid_type_rest_400_ws_32602_and_no_broadcast(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    expected = "Unknown module type 'Tests'; expected one of requirements, test_cases, test_execution"
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as watcher:
        resp = client.post(f"/projects/{project_id}/modules", json={"name": "T", "prefix": "T", "type": "Tests"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == expected
        response = _rpc(ws, "module.create", {"project_id": project_id, "name": "T", "prefix": "T", "type": "Tests"})
        assert response["error"] == {"code": -32602, "message": expected}
        # The next event the watcher sees comes from a later, distinguishable mutation.
        client.post("/projects", json={"name": "Other"})
        assert json.loads(watcher.receive_text()) == {"event": "changed", "project_id": "other"}
    assert client.get(f"/projects/{project_id}/modules").json() == []


# --- attribute scope by module type ------------------------------------------------


def test_add_attribute_with_module_types_stores_canonical_order_and_applies_by_type(service, tmp_path):
    project, reqs, tests, execs = _three_types(service)
    service.add_attribute_definition(
        project.id, "result", "Result", type="string", module_types=["test_execution", "test_cases", "test_execution"]
    )
    data = _assert_json_file_style(tmp_path / project.id / "project.json")
    stored = next(a for a in data["attributes"] if a["id"] == "result")
    assert list(stored) == ["id", "name", "type", "required", "default", "choices", "system", "module_types"]
    assert stored["module_types"] == ["test_cases", "test_execution"]

    service.add_requirement(project.id, tests.id, {"object_text": "t", "result": "ok"})
    service.add_requirement(project.id, execs.id, {"object_text": "e", "result": "ok"})
    with pytest.raises(ValueError, match=r"Attribute\(s\) do not apply to module 'reqs': \['Result'\]"):
        service.add_requirement(project.id, reqs.id, {"object_text": "r", "result": "ok"})

    fresh = ProjectService(ProjectStore(tmp_path))
    assert next(a for a in fresh.get_project(project.id).attributes if a.id == "result").module_types == [
        "test_cases", "test_execution",
    ]


def test_attribute_applies_to_listed_module_or_listed_type(service):
    project, reqs, tests, execs = _three_types(service)
    other = service.create_module(project.id, "Other reqs", "OR")
    service.add_attribute_definition(
        project.id, "note", "Note", modules=[reqs.id], module_types=["test_execution"]
    )
    service.add_requirement(project.id, reqs.id, {"object_text": "r", "note": "n"})
    service.add_requirement(project.id, execs.id, {"object_text": "e", "note": "n"})
    for module in (tests, other):
        with pytest.raises(ValueError, match="do not apply"):
            service.add_requirement(project.id, module.id, {"object_text": "x", "note": "n"})


def test_empty_module_types_are_not_written(service, tmp_path):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "owner", "Owner", module_types=[])
    stored = json.loads((tmp_path / project.id / "project.json").read_text())["attributes"][-1]
    assert "module_types" not in stored and "modules" not in stored


@pytest.mark.parametrize(
    "module_types, message",
    [
        (["bogus"], "Unknown module type(s) ['bogus']; expected some of requirements, test_cases, test_execution"),
        (["test_cases", "Tests"], "Unknown module type(s) ['Tests']; expected some of requirements, test_cases, test_execution"),
        ("test_cases", "Attribute module_types must be a list of module types"),
        ([1], "Attribute module_types must be a list of module types"),
    ],
)
def test_add_attribute_refuses_invalid_module_types(service, module_types, message):
    project = service.create_project("P")
    with pytest.raises(ValueError) as excinfo:
        service.add_attribute_definition(project.id, "owner", "Owner", module_types=module_types)
    assert str(excinfo.value) == message
    assert [a.id for a in service.get_project(project.id).attributes] == ["object_text"]


def test_required_attribute_cannot_be_scoped_by_type(service):
    project, *_ = _three_types(service)
    message = (
        "Required attribute 'Owner' applies to every module; only optional attributes can be limited to modules"
    )
    with pytest.raises(ValueError) as excinfo:
        service.add_attribute_definition(
            project.id, "owner", "Owner", required=True, default="me", module_types=["test_cases"]
        )
    assert str(excinfo.value) == message
    service.add_attribute_definition(project.id, "owner", "Owner", required=True, default="me")
    with pytest.raises(ValueError) as excinfo:
        service.set_attribute_modules(project.id, "owner", [], False, ["test_cases"])
    assert str(excinfo.value) == message
    assert service.set_attribute_modules(project.id, "owner", None, False, [])["dropped_values"] == []


def test_set_attribute_modules_type_scope_refuses_values_outside_then_drops(service, tmp_path):
    project, reqs, tests, execs = _three_types(service)
    service.add_attribute_definition(project.id, "note", "Note")
    in_tests = service.add_requirement(project.id, tests.id, {"object_text": "t", "note": "keep"})
    in_exec = service.add_requirement(project.id, execs.id, {"object_text": "e", "note": "gone"})
    blank = service.add_requirement(project.id, reqs.id, {"object_text": "r", "note": "  "})
    before = _snapshot(tmp_path / project.id)

    with pytest.raises(ValueError) as excinfo:
        service.set_attribute_modules(project.id, "note", [], False, ["test_cases"])
    assert str(excinfo.value) == (
        f"Attribute 'Note' has values in 1 requirement(s) of modules it would no longer apply to "
        f"({in_exec.id}); clear them first or drop them"
    )
    assert _snapshot(tmp_path / project.id) == before

    result = service.set_attribute_modules(project.id, "note", [], True, ["test_cases"])
    assert result["dropped_values"] == [{"module_id": execs.id, "requirement_id": in_exec.id}]
    fresh = ProjectService(ProjectStore(tmp_path))
    assert "note" not in fresh.get_requirement(project.id, execs.id, in_exec.id).attributes
    assert "note" not in fresh.get_requirement(project.id, reqs.id, blank.id).attributes  # blank: removed silently
    assert fresh.get_requirement(project.id, tests.id, in_tests.id).attributes["note"] == "keep"
    attr = next(a for a in fresh.get_project(project.id).attributes if a.id == "note")
    assert (attr.modules, attr.module_types) == ([], ["test_cases"])


def test_set_attribute_modules_omitted_module_types_clears_the_type_scope(service, tmp_path):
    project, reqs, tests, execs = _three_types(service)
    service.add_attribute_definition(project.id, "note", "Note", module_types=["test_cases"])
    service.set_attribute_modules(project.id, "note", [reqs.id])
    stored = json.loads((tmp_path / project.id / "project.json").read_text())["attributes"][-1]
    assert stored["modules"] == [reqs.id]
    assert "module_types" not in stored
    service.set_attribute_modules(project.id, "note", None)
    stored = json.loads((tmp_path / project.id / "project.json").read_text())["attributes"][-1]
    assert "modules" not in stored and "module_types" not in stored


def test_set_attribute_modules_refuses_unknown_type(service):
    project, *_ = _three_types(service)
    service.add_attribute_definition(project.id, "note", "Note")
    with pytest.raises(ValueError, match=r"Unknown module type\(s\) \['x'\]"):
        service.set_attribute_modules(project.id, "note", [], False, ["x"])


def test_module_types_scope_rest_and_ws_parity(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    for name, prefix, module_type in (("Reqs", "REQ", None), ("Tests", "TC", "test_cases")):
        client.post(f"/projects/{project_id}/modules", json={"name": name, "prefix": prefix, "type": module_type})
    resp = client.post(f"/projects/{project_id}/attributes", json={
        "id": "note", "name": "Note", "module_types": ["test_cases", "test_cases"],
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["attributes"][-1]["module_types"] == ["test_cases"]
    resp = client.post(f"/projects/{project_id}/attributes", json={"id": "x", "name": "X", "module_types": ["nope"]})
    assert resp.status_code == 400
    message = resp.json()["detail"]
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "attribute.add", {"project_id": project_id, "id": "x", "name": "X", "module_types": ["nope"]})
        assert response["error"] == {"code": -32602, "message": message}
        added = _rpc(ws, "attribute.add", {
            "project_id": project_id, "id": "y", "name": "Y", "module_types": ["requirements"],
        }, 2)
        assert added["result"]["attributes"][-1]["module_types"] == ["requirements"]
        changed = _rpc(ws, "attribute.set_modules", {
            "project_id": project_id, "id": "y", "modules": [], "module_types": ["test_cases"],
        }, 3)
        assert changed["result"]["project"]["attributes"][-1]["module_types"] == ["test_cases"]

    client.post(f"/projects/{project_id}/modules/tests/requirements", json={"attributes": {"object_text": "t", "note": "v"}})
    resp = client.put(f"/projects/{project_id}/attributes/note/modules", json={"modules": [], "module_types": ["requirements"]})
    assert resp.status_code == 400
    assert "clear them first or drop them" in resp.json()["detail"]
    resp = client.put(f"/projects/{project_id}/attributes/note/modules", json={
        "modules": [], "module_types": ["requirements"], "drop_values": True,
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["dropped_values"] == [{"module_id": "tests", "requirement_id": "TC-0001"}]
    assert client.put(f"/projects/{project_id}/attributes/missing/modules", json={"modules": []}).status_code == 404


# --- link configuration type pairs ---------------------------------------------------


@pytest.mark.parametrize("source_type, target_type", LINK_TYPE_PAIRS)
def test_link_configuration_allowed_type_pairs(service, source_type, target_type):
    project = service.create_project("P")
    source = service.create_module(project.id, "Source", "SRC", type=source_type)
    target = service.create_module(project.id, "Target", "TGT", type=target_type)
    config = service.create_link_configuration(project.id, "Link", source.id, target.id)
    assert (config.source_module_id, config.target_module_id) == (source.id, target.id)


@pytest.mark.parametrize(
    "source_type, target_type",
    [
        (s, t) for s in MODULE_TYPES for t in MODULE_TYPES if (s, t) not in LINK_TYPE_PAIRS
    ],
)
def test_link_configuration_refuses_other_type_pairs_and_writes_nothing(service, tmp_path, source_type, target_type):
    project = service.create_project("P")
    source = service.create_module(project.id, "Source", "SRC", type=source_type)
    target = service.create_module(project.id, "Target", "TGT", type=target_type)
    before = _snapshot(tmp_path / project.id)
    with pytest.raises(ValueError) as excinfo:
        service.create_link_configuration(project.id, "Link", source.id, target.id)
    assert str(excinfo.value) == (
        f"A link from module 'Source' ({source_type}) to module 'Target' ({target_type}) is not allowed; "
        "link configurations join requirements → requirements, test_cases → requirements, "
        "test_execution → test_cases"
    )
    assert _snapshot(tmp_path / project.id) == before
    assert service.list_link_configurations(project.id) == []


def test_link_type_pair_rejection_rest_and_ws(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    client.post(f"/projects/{project_id}/modules", json={"name": "Reqs", "prefix": "REQ"})
    client.post(f"/projects/{project_id}/modules", json={"name": "Exec", "prefix": "EX", "type": "test_execution"})
    body = {"name": "Bad", "source_module_id": "exec", "target_module_id": "reqs"}
    resp = client.post(f"/projects/{project_id}/link-configurations", json=body)
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("A link from module 'Exec' (test_execution) to module 'Reqs'")
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "link_configuration.create", {"project_id": project_id, **body})
    assert response["error"] == {"code": -32602, "message": resp.json()["detail"]}
    assert client.get(f"/projects/{project_id}").json()["link_configurations"] == []


def test_stored_link_configuration_with_disallowed_types_keeps_loading_and_linking(service, tmp_path):
    project = service.create_project("P")
    reqs = service.create_module(project.id, "Reqs", "REQ")
    tests = service.create_module(project.id, "Tests", "TC", type="test_cases")
    config = service.create_link_configuration(project.id, "Verifies", tests.id, reqs.id)
    # Hand-edit: swap the direction to requirements -> test_cases (not allowed for new configurations).
    path = tmp_path / project.id / "project.json"
    data = json.loads(path.read_text())
    data["link_configurations"][0]["source_module_id"] = reqs.id
    data["link_configurations"][0]["target_module_id"] = tests.id
    path.write_text(json.dumps(data, indent=2) + "\n")

    requirement = service.add_requirement(project.id, reqs.id, {"object_text": "r"})
    test_case = service.add_requirement(project.id, tests.id, {"object_text": "t"})
    assert [c.source_module_id for c in service.list_link_configurations(project.id)] == [reqs.id]
    service.link_requirements(project.id, config.id, requirement.id, test_case.id)
    assert service.get_requirement(project.id, tests.id, test_case.id).attributes[config.id] == f"< {requirement.id}"


# --- project format ---------------------------------------------------------------------


def test_project_format_is_current_and_v4_project_loads_without_rewrite(service, tmp_path):
    assert PROJECT_FORMAT.current == 8
    project = service.create_project("P")
    module = service.create_module(project.id, "Reqs", "REQ")
    service.add_attribute_definition(project.id, "note", "Note")
    service.add_requirement(project.id, module.id, {"object_text": "x", "note": "n"})
    path = tmp_path / project.id / "project.json"
    data = json.loads(path.read_text())
    data["format_version"] = 4
    data["module_order"] = [module.id]  # v4..6 stored plain ids
    data.pop("locked", None)
    data["attributes"][-1]["module_types"] = ["test_cases"]  # stray key, ignored by version 4
    path.write_text(json.dumps(data, indent=2) + "\n")
    before = _snapshot(tmp_path / project.id)

    loaded = service.get_project(project.id)
    note = next(a for a in loaded.attributes if a.id == "note")
    assert note.module_types == []
    assert service.get_module(project.id, module.id).type == "requirements"
    service.list_requirements(project.id, module.id)
    assert _snapshot(tmp_path / project.id) == before
    # The stray scope means nothing: the attribute still applies to the requirements module.
    service.add_requirement(project.id, module.id, {"object_text": "y", "note": "n"})
    saved = json.loads(path.read_text())
    assert saved["format_version"] == PROJECT_FORMAT.current
    assert "module_types" not in next(a for a in saved["attributes"] if a["id"] == "note")
    assert json.loads((tmp_path / project.id / "modules" / module.id / "module.json").read_text()) == {
        "id": module.id, "name": "Reqs", "prefix": "REQ", "description": "",
    }


# --- templates ----------------------------------------------------------------------------


def test_rbc_template_lists_and_creates_module_types(service):
    rbc = next(t for t in service.list_templates() if t["id"] == RBC)
    types = {m["key"]: m["type"] for m in rbc["modules"]}
    assert types == {
        "strs": "requirements", "syrs": "requirements", "swrs": "requirements",
        "systest": "test_cases", "swtest": "test_cases",
        "systestexec": "test_execution", "swtestexec": "test_execution",
    }
    project = service.create_project("R", template_id=RBC)
    assert {m.id: m.type for m in service.list_modules(project.id)} == types
    attrs = {a.id: a for a in project.attributes}
    for attr_id in ("result", "executed_by", "execution_date", "version_under_test", "evidence_ref"):
        assert attrs[attr_id].module_types == ["test_execution"]
    assert attrs["execution_date"].type == "date"
    assert attrs["result"].choices == ["Passed", "Failed", "Blocked", "Not Run"]
    assert attrs["result"].default == "Not Run"
    configs = {c.id: (c.source_module_id, c.target_module_id) for c in service.list_link_configurations(project.id)}
    assert configs["executes-systest"] == ("systestexec", "systest")
    assert configs["executes-swtest"] == ("swtestexec", "swtest")


def test_save_as_template_round_trips_module_types_and_type_scopes(service, tmp_path):
    project, reqs, tests, execs = _three_types(service)
    service.add_attribute_definition(project.id, "result", "Result", module_types=["test_execution"])
    service.add_attribute_definition(project.id, "run_on", "Run On", type="date", default="2026-01-01")
    service.create_link_configuration(project.id, "Verifies", tests.id, reqs.id)
    service.create_link_configuration(project.id, "Executes", execs.id, tests.id)
    summary = service.save_project_as_template(project.id, "Typed")
    assert {m["key"]: m["type"] for m in summary["modules"]} == {
        "reqs": "requirements", "tests": "test_cases", "exec": "test_execution",
    }
    data = _assert_json_file_style(tmp_path / "_templates" / "typed.json")
    modules = {m["key"]: m for m in data["modules"]}
    assert "type" not in modules["reqs"]
    assert modules["tests"]["type"] == "test_cases"
    assert list(modules["exec"])[-1] == "type"
    attributes = {a["id"]: a for a in data["attributes"]}
    assert attributes["result"]["module_types"] == ["test_execution"]
    assert "module_types" not in attributes["run_on"]
    assert attributes["run_on"]["type"] == "date"

    copy = service.create_project("Copy", template_id=summary["id"])
    assert {m.id: m.type for m in service.list_modules(copy.id)} == {
        "reqs": "requirements", "tests": "test_cases", "exec": "test_execution",
    }
    copied = {a.id: a for a in copy.attributes}
    assert copied["result"].module_types == ["test_execution"]
    assert copied["run_on"].default == "2026-01-01"


def test_template_with_disallowed_link_type_pair_is_refused(service, tmp_path):
    project, reqs, tests, execs = _three_types(service)
    service.create_link_configuration(project.id, "Verifies", tests.id, reqs.id)
    service.save_project_as_template(project.id, "Typed")
    data = json.loads((tmp_path / "_templates" / "typed.json").read_text())
    data["id"], data["name"] = "broken", "Broken"
    link = data["link_configurations"][0]
    assert (link["source_module"], link["target_module"]) == ("tests", "reqs")
    link["source_module"], link["target_module"] = "reqs", "tests"
    with pytest.raises(ValueError, match="is not allowed; link configurations join"):
        service.import_template(json.dumps(data, indent=2) + "\n")
        service.create_project("From broken", template_id="user:broken")
    assert [p.id for p in service.list_projects()] == [project.id]


@pytest.mark.parametrize("bad", [{"type": "tests"}, {"type": 3}])
def test_template_with_unknown_module_type_is_refused(service, tmp_path, bad):
    project, *_ = _three_types(service)
    service.save_project_as_template(project.id, "Typed")
    data = json.loads((tmp_path / "_templates" / "typed.json").read_text())
    data["id"], data["name"] = "broken", "Broken"
    data["modules"][1].update(bad)
    with pytest.raises(ValueError):
        service.import_template(json.dumps(data, indent=2) + "\n")
        service.create_project("From broken", template_id="user:broken")
    assert [p.id for p in service.list_projects()] == [project.id]
