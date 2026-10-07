"""Per-module attribute applicability (``AttributeDefinition.modules``, project format 2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import csv
import importlib
import io
import json

import pytest
from fastapi.testclient import TestClient

from reqman.formats import PROJECT_FORMAT, upgrade
from reqman.service import ProjectService
from reqman.store import ProjectStore

RBC = "builtin:rbc_template"


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path / "data"))


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


@pytest.fixture
def scoped(service):
    """A project with modules Sys/Test and attributes scoped to Test only."""
    project = service.create_project("P")
    sys_module = service.create_module(project.id, "Sys", "SYS")
    test_module = service.create_module(project.id, "Test", "TST")
    service.add_attribute_definition(
        project.id, "steps", "Steps", "text", required=True, default="tbd", modules=[test_module.id]
    )
    service.add_attribute_definition(project.id, "note", "Note")
    return project.id, sys_module.id, test_module.id


# --- service rules ----------------------------------------------------------------


def test_scope_is_stored_in_module_order_and_unscoped_keeps_old_keys(service, scoped):
    project_id, sys_id, test_id = scoped
    service.add_attribute_definition(project_id, "both", "Both", modules=[test_id, sys_id, test_id])
    attrs = {a.id: a for a in service.get_project(project_id).attributes}
    assert attrs["both"].modules == [sys_id, test_id]
    assert attrs["note"].modules == []
    data = json.loads(service.store._project_file(project_id).read_text())
    assert data["format_version"] == PROJECT_FORMAT.current == 2
    by_id = {a["id"]: a for a in data["attributes"]}
    assert "modules" not in by_id["note"]
    assert by_id["steps"]["modules"] == [test_id]


@pytest.mark.parametrize("modules", [["nope"], "sys", [1]])
def test_invalid_scope_is_rejected(service, scoped, modules):
    project_id, _, _ = scoped
    with pytest.raises(ValueError):
        service.add_attribute_definition(project_id, "x", "X", modules=modules)
    assert "x" not in {a.id for a in service.get_project(project_id).attributes}


def test_writing_a_non_applicable_attribute_is_rejected(service, scoped):
    project_id, sys_id, test_id = scoped
    with pytest.raises(ValueError, match="do not apply to module"):
        service.add_requirement(project_id, sys_id, {"object_text": "a", "steps": "x"})
    req = service.add_requirement(project_id, sys_id, {"object_text": "a"})
    with pytest.raises(ValueError, match="do not apply to module"):
        service.update_requirement(project_id, sys_id, req.id, {"steps": "x"})
    service.update_requirement(project_id, sys_id, req.id, {"note": "fine"})


def test_required_and_default_only_in_listed_modules(service, scoped):
    project_id, sys_id, test_id = scoped
    in_sys = service.add_requirement(project_id, sys_id, {"object_text": "a"})
    assert "steps" not in in_sys.attributes
    in_test = service.add_requirement(project_id, test_id, {"object_text": "b"})
    assert in_test.attributes["steps"] == "tbd"
    with pytest.raises(ValueError, match="required attribute 'Steps' is empty"):
        service.add_requirement(project_id, test_id, {"object_text": "c", "steps": " "})


def test_set_modules_refuses_then_drops_values_outside_scope(service, scoped):
    project_id, sys_id, test_id = scoped
    a = service.add_requirement(project_id, sys_id, {"object_text": "a", "note": "keep me?"})
    b = service.add_requirement(project_id, sys_id, {"object_text": "b", "note": ""})
    c = service.add_requirement(project_id, test_id, {"object_text": "c", "note": "stays"})
    with pytest.raises(ValueError, match=a.id):
        service.set_attribute_modules(project_id, "note", [test_id])
    assert service.get_requirement(project_id, sys_id, a.id).attributes["note"] == "keep me?"

    result = service.set_attribute_modules(project_id, "note", [test_id], drop_values=True)
    assert result["dropped_values"] == [{"module_id": sys_id, "requirement_id": a.id}]
    assert "note" not in service.get_requirement(project_id, sys_id, a.id).attributes
    assert "note" not in service.get_requirement(project_id, sys_id, b.id).attributes
    assert service.get_requirement(project_id, test_id, c.id).attributes["note"] == "stays"
    assert next(x for x in result["project"].attributes if x.id == "note").modules == [test_id]

    # Widening back to every module drops nothing.
    result = service.set_attribute_modules(project_id, "note", [])
    assert result["dropped_values"] == []
    assert next(x for x in result["project"].attributes if x.id == "note").modules == []


def test_set_modules_refuses_builtin_system_and_unknown_attributes(service, scoped):
    project_id, sys_id, test_id = scoped
    with pytest.raises(ValueError, match="built in"):
        service.set_attribute_modules(project_id, "object_text", [sys_id])
    config = service.create_link_configuration(project_id, "Verifies", test_id, sys_id)
    with pytest.raises(ValueError, match="system-managed"):
        service.set_attribute_modules(project_id, config.id, [sys_id])
    with pytest.raises(FileNotFoundError):
        service.set_attribute_modules(project_id, "missing", [sys_id])
    with pytest.raises(ValueError, match="drop_values"):
        service.set_attribute_modules(project_id, "note", [sys_id], drop_values="yes")


def test_csv_import_ignores_empty_and_rejects_filled_non_applicable_columns(service, scoped):
    project_id, sys_id, test_id = scoped
    ok = service.import_csv(project_id, sys_id, "ObjectText,Steps,Note\nfirst,,n\n")
    assert ok["ignored_columns"] == ["Steps"]
    imported = service.get_requirement(project_id, sys_id, ok["requirement_ids"][0])
    assert "steps" not in imported.attributes

    before = service.list_requirements(project_id, sys_id)
    with pytest.raises(ValueError, match=r"Row 3: attribute\(s\) \['Steps'\] do not apply to this module"):
        service.import_csv(project_id, sys_id, "ObjectText,Steps\nsecond,\nthird,do it\n")
    assert len(service.list_requirements(project_id, sys_id)) == len(before)

    into_test = service.import_csv(project_id, test_id, "ObjectText,Steps\nt,do it\n")
    assert into_test["ignored_columns"] == []


def test_exports_show_only_applicable_values(service, scoped):
    project_id, sys_id, test_id = scoped
    service.add_requirement(project_id, test_id, {"object_text": "t", "steps": "do it"})
    req = service.add_requirement(project_id, sys_id, {"object_text": "s"})
    # A value left in a non-applicable module (hand-edited data) is not exported.
    path = next(service.store.root.rglob(f"{req.id}.json"))
    data = json.loads(path.read_text())
    data["attributes"]["steps"] = "stray"
    path.write_text(json.dumps(data, indent=2) + "\n")

    rows = list(csv.DictReader(io.StringIO(service.export_csv(project_id))))
    by_module = {row["Module"]: row for row in rows}
    assert by_module[test_id]["Steps"] == "do it"
    assert by_module[sys_id]["Steps"] == ""
    assert "stray" not in service.export_reqif(project_id)


# --- template ----------------------------------------------------------------


def test_rbc_template_scopes_attributes_by_module(service):
    project = service.create_project("Rbc", template_id=RBC)
    modules = {m.name: m.id for m in service.list_modules(project.id)}
    attrs = {a.id: a for a in project.attributes}
    tests = [modules["SysTest"], modules["SwTest"]]
    specs = [modules["StRS"], modules["SyRS"], modules["SwRS"]]
    for attr_id in ("preconditions", "test_steps", "expected_result", "test_result"):
        assert attrs[attr_id].modules == tests
    for attr_id in ("sil", "allocation", "verification_method"):
        assert attrs[attr_id].modules == specs
    assert attrs["status"].modules == []

    saved = service.save_project_as_template(project.id, "Copy")
    copy = service.create_project("Copy", template_id=saved["id"])
    copy_modules = {m.name: m.id for m in service.list_modules(copy.id)}
    copy_attrs = {a.id: a for a in copy.attributes}
    assert copy_attrs["test_steps"].modules == [copy_modules["SysTest"], copy_modules["SwTest"]]


def test_template_modules_must_name_template_module_keys(service):
    template = {
        "format_version": 1,
        "name": "Bad",
        "attributes": [{"id": "a", "name": "A", "modules": ["nope"]}],
        "modules": [{"key": "m", "name": "M", "prefix": "M"}],
    }
    with pytest.raises(ValueError, match="not a module key"):
        service.import_template(json.dumps(template))


# --- migration ----------------------------------------------------------------


def test_v1_project_migrates_without_scopes_and_saves_as_v2(service):
    project = service.create_project("Old")
    path = service.store._project_file(project.id)
    data = json.loads(path.read_text())
    data["format_version"] = 1
    data["attributes"].append({"id": "x", "name": "X", "type": "string", "modules": ["ignored-in-v1"]})
    path.write_text(json.dumps(data, indent=2) + "\n")

    upgraded = upgrade(data, PROJECT_FORMAT, "Project 'old'")
    assert all("modules" not in a for a in upgraded["attributes"])
    assert data["attributes"][-1]["modules"] == ["ignored-in-v1"]  # input not modified

    loaded = service.get_project(project.id)
    assert next(a for a in loaded.attributes if a.id == "x").modules == []
    assert json.loads(path.read_text())["format_version"] == 1  # loading never rewrites

    service.create_module(project.id, "M", "M")
    saved = json.loads(path.read_text())
    assert saved["format_version"] == 2
    assert "modules" not in next(a for a in saved["attributes"] if a["id"] == "x")


# --- REST / WebSocket parity ----------------------------------------------------------


def _setup_rest(client):
    project = client.post("/projects", json={"name": "P"}).json()
    pid = project["id"]
    sys_id = client.post(f"/projects/{pid}/modules", json={"name": "Sys", "prefix": "SYS"}).json()["id"]
    test_id = client.post(f"/projects/{pid}/modules", json={"name": "Test", "prefix": "TST"}).json()["id"]
    return pid, sys_id, test_id


def test_rest_and_websocket_agree(client):
    pid, sys_id, test_id = _setup_rest(client)
    response = client.post(f"/projects/{pid}/attributes", json={"id": "steps", "name": "Steps", "modules": [test_id]})
    assert response.status_code == 200
    assert next(a for a in response.json()["attributes"] if a["id"] == "steps")["modules"] == [test_id]
    bad = client.post(f"/projects/{pid}/attributes", json={"id": "bad", "name": "Bad", "modules": ["nope"]})
    assert bad.status_code == 400

    rest = client.post(f"/projects/{pid}/modules/{sys_id}/requirements", json={"attributes": {"steps": "x"}})
    assert rest.status_code == 400 and "do not apply" in rest.json()["detail"]

    with client.websocket_connect("/ws") as ws:
        added = _rpc(ws, "attribute.add", {"project_id": pid, "id": "note", "name": "Note", "modules": [sys_id]})
        assert next(a for a in added["result"]["attributes"] if a["id"] == "note")["modules"] == [sys_id]
        ws_bad = _rpc(ws, "requirement.add", {"project_id": pid, "module_id": sys_id, "attributes": {"steps": "x"}})
        assert ws_bad["error"]["code"] == -32602 and "do not apply" in ws_bad["error"]["message"]
        req = _rpc(ws, "requirement.add", {"project_id": pid, "module_id": test_id, "attributes": {"object_text": "t", "steps": "x"}})
        req_id = req["result"]["id"]

        refused = _rpc(ws, "attribute.set_modules", {"project_id": pid, "id": "steps", "modules": [sys_id]})
        assert refused["error"]["code"] == -32602
        missing = _rpc(ws, "attribute.set_modules", {"project_id": pid, "id": "nope", "modules": []})
        assert missing["error"]["code"] == -32001

    rest_refused = client.put(f"/projects/{pid}/attributes/steps/modules", json={"modules": [sys_id]})
    assert rest_refused.status_code == 400
    assert client.put(f"/projects/{pid}/attributes/nope/modules", json={"modules": []}).status_code == 404

    rest_ok = client.put(
        f"/projects/{pid}/attributes/steps/modules", json={"modules": [sys_id], "drop_values": True}
    )
    assert rest_ok.status_code == 200
    body = rest_ok.json()
    assert body["dropped_values"] == [{"module_id": test_id, "requirement_id": req_id}]
    assert next(a for a in body["project"]["attributes"] if a["id"] == "steps")["modules"] == [sys_id]

    with client.websocket_connect("/ws") as ws:
        widened = _rpc(ws, "attribute.set_modules", {"project_id": pid, "id": "steps", "modules": []})
        assert widened["result"]["dropped_values"] == []
        assert "modules" not in next(a for a in widened["result"]["project"]["attributes"] if a["id"] == "steps")


def test_set_modules_broadcasts_change(client):
    pid, sys_id, _ = _setup_rest(client)
    client.post(f"/projects/{pid}/attributes", json={"id": "note", "name": "Note"})
    with client.websocket_connect("/ws") as listener, client.websocket_connect("/ws") as sender:
        result = _rpc(sender, "attribute.set_modules", {"project_id": pid, "id": "note", "modules": [sys_id]})
        assert "result" in result
        assert json.loads(listener.receive_text()) == {"event": "changed", "project_id": pid}
