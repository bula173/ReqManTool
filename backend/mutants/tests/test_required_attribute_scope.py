"""Only optional attributes can be scoped to modules; required ones apply to every module."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from reqman.service import ProjectService
from reqman.store import ProjectStore
from reqman.templates import BUILTIN_TEMPLATES_DIR

RBC = "builtin:rbc_template"
MESSAGE = "Required attribute 'Steps' applies to every module; only optional attributes can be limited to modules"


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
def project(service):
    project = service.create_project("P")
    sys_module = service.create_module(project.id, "Sys", "SYS")
    test_module = service.create_module(project.id, "Test", "TST")
    return project.id, sys_module.id, test_module.id


# --- service ---------------------------------------------------------------------


def test_add_required_scoped_attribute_is_rejected(service, project):
    project_id, _, test_id = project
    before = service.store._project_file(project_id).read_bytes()
    with pytest.raises(ValueError) as excinfo:
        service.add_attribute_definition(project_id, "steps", "Steps", required=True, default="x", modules=[test_id])
    assert str(excinfo.value) == MESSAGE
    assert service.store._project_file(project_id).read_bytes() == before


@pytest.mark.parametrize("modules", [None, []])
def test_add_required_attribute_for_every_module_is_allowed(service, project, modules):
    project_id, _, _ = project
    result = service.add_attribute_definition(project_id, "steps", "Steps", required=True, default="x", modules=modules)
    assert next(a for a in result.attributes if a.id == "steps").modules == []


def test_set_modules_on_required_attribute(service, project):
    project_id, sys_id, _ = project
    service.add_attribute_definition(project_id, "steps", "Steps", required=True, default="x")
    before = service.store._project_file(project_id).read_bytes()
    with pytest.raises(ValueError) as excinfo:
        service.set_attribute_modules(project_id, "steps", [sys_id])
    assert str(excinfo.value) == MESSAGE
    assert service.store._project_file(project_id).read_bytes() == before
    # "Every module" stays allowed.
    assert service.set_attribute_modules(project_id, "steps", [])["dropped_values"] == []
    assert service.set_attribute_modules(project_id, "steps", None)["dropped_values"] == []


def test_optional_attribute_can_still_be_scoped(service, project):
    project_id, sys_id, test_id = project
    service.add_attribute_definition(project_id, "note", "Note", modules=[test_id])
    result = service.set_attribute_modules(project_id, "note", [sys_id])
    assert next(a for a in result["project"].attributes if a.id == "note").modules == [sys_id]


def test_hand_edited_required_scoped_attribute_applies_to_every_module(service, project):
    project_id, sys_id, test_id = project
    service.add_attribute_definition(project_id, "steps", "Steps", required=True, default="tbd")
    path = service.store._project_file(project_id)
    data = json.loads(path.read_text())
    next(a for a in data["attributes"] if a["id"] == "steps")["modules"] = [test_id]
    path.write_text(json.dumps(data, indent=2) + "\n")

    loaded = service.get_project(project_id)  # loads; the stored list is kept
    assert next(a for a in loaded.attributes if a.id == "steps").modules == [test_id]
    in_sys = service.add_requirement(project_id, sys_id, {"object_text": "a"})
    assert in_sys.attributes["steps"] == "tbd"
    with pytest.raises(ValueError, match="required attribute 'Steps' is empty"):
        service.add_requirement(project_id, sys_id, {"object_text": "b", "steps": " "})

    saved = service.save_project_as_template(project_id, "Copy")
    copy = service.create_project("Copy", template_id=saved["id"])
    assert next(a for a in copy.attributes if a.id == "steps").modules == []

    # Resetting the scope to every module clears the stray list.
    service.set_attribute_modules(project_id, "steps", [])
    assert "modules" not in next(a for a in json.loads(path.read_text())["attributes"] if a["id"] == "steps")


# --- templates -------------------------------------------------------------------


def _template(required):
    return {
        "format_version": 1,
        "name": "Scoped",
        "attributes": [{"id": "steps", "name": "Steps", "required": required, "default": "x", "modules": ["t"]}],
        "modules": [{"key": "t", "name": "Test", "prefix": "TST"}],
    }


def test_template_with_required_scoped_attribute_is_rejected(service):
    with pytest.raises(ValueError, match=r"attributes\[0\] \('steps'\): " + MESSAGE):
        service.import_template(json.dumps(_template(True)))
    assert service.store.list_user_template_ids() == []

    # A file placed by hand is listed with the reason and cannot be used.
    service.import_template(json.dumps(_template(False)))
    local_id = service.store.list_user_template_ids()[0]
    service.store.save_user_template(local_id, _template(True))
    entry = next(t for t in service.list_templates() if t["source"] == "user")
    assert MESSAGE in entry["error"]
    with pytest.raises(ValueError, match="only optional attributes"):
        service.create_project("X", template_id=entry["id"])


def test_rbc_template_scopes_only_optional_attributes(service):
    data = json.loads((BUILTIN_TEMPLATES_DIR / "rbc_template.json").read_text(encoding="utf-8"))
    scoped = [a for a in data["attributes"] if a.get("modules")]
    assert scoped and all(a.get("required", False) is False for a in scoped)
    assert next(t for t in service.list_templates() if t["id"] == RBC)["error"] is None
    project = service.create_project("Rbc", template_id=RBC)
    assert any(a.modules for a in project.attributes)


# --- REST / WebSocket parity -----------------------------------------------------


def test_rest_and_websocket_reject_required_scope_alike(client, tmp_path):
    pid = client.post("/projects", json={"name": "P"}).json()["id"]
    mid = client.post(f"/projects/{pid}/modules", json={"name": "Test", "prefix": "TST"}).json()["id"]
    project_file = next(tmp_path.rglob(f"{pid}/project.json"))
    before = project_file.read_bytes()

    body = {"id": "steps", "name": "Steps", "required": True, "default": "x", "modules": [mid]}
    rest = client.post(f"/projects/{pid}/attributes", json=body)
    assert rest.status_code == 400 and rest.json()["detail"] == MESSAGE
    with client.websocket_connect("/ws") as ws:
        ws_add = _rpc(ws, "attribute.add", {"project_id": pid, **body})
        assert ws_add["error"]["code"] == -32602 and ws_add["error"]["message"] == MESSAGE
    assert project_file.read_bytes() == before

    assert client.post(f"/projects/{pid}/attributes", json={**body, "modules": []}).status_code == 200
    before = project_file.read_bytes()
    rest_set = client.put(f"/projects/{pid}/attributes/steps/modules", json={"modules": [mid]})
    assert rest_set.status_code == 400 and rest_set.json()["detail"] == MESSAGE
    with client.websocket_connect("/ws") as ws:
        ws_set = _rpc(ws, "attribute.set_modules", {"project_id": pid, "id": "steps", "modules": [mid]})
        assert ws_set["error"]["code"] == -32602 and ws_set["error"]["message"] == MESSAGE
    assert project_file.read_bytes() == before
