"""Views as portable project elements: ``ProjectService.update_view`` (REST ``PUT /projects/{p}/views/{v}``,
WebSocket ``view.update``), ordered columns, ``View.single_line`` and views in templates."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from reqman.models import View
from reqman.service import VIEW_ID_COLUMN, ProjectService
from reqman.store import ProjectStore


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


def _assert_no_broadcast(ws, request_id=999):
    response = _rpc(ws, "template.list", {}, request_id)
    assert response.get("id") == request_id, response


def _project_with_view(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "status", "Status")
    service.add_attribute_definition(project.id, "owner", "Owner")
    view = service.create_view(project.id, "Main", ["object_text", "status"])
    return project.id, view.id


# --- model ------------------------------------------------------------------------


def test_view_single_line_round_trips_and_is_written_only_when_set():
    view = View(id="m", name="M", attribute_ids=["a"])
    assert "single_line" not in view.to_dict()
    view = View.from_dict({"id": "m", "name": "M", "attribute_ids": ["a"], "single_line": ["a", 3, "a", "@id"]})
    assert view.single_line == ["a", "@id"]
    assert View.from_dict(view.to_dict()) == view
    assert View.from_dict({"id": "m", "name": "M", "single_line": "a"}).single_line is None
    assert list(View(id="m", name="M", column_widths={"a": 50}, single_line=["a"]).to_dict()) == [
        "id", "name", "attribute_ids", "column_widths", "single_line",
    ]


# --- service ----------------------------------------------------------------------


def test_update_view_stores_ordered_columns_and_layout_and_reloads(service):
    project_id, view_id = _project_with_view(service)
    view = service.update_view(
        project_id,
        view_id,
        {
            "name": " Review ",
            "attribute_ids": ["owner", "object_text", "status"],
            "column_widths": {"status": 90, VIEW_ID_COLUMN: 100},
            "single_line": ["status", VIEW_ID_COLUMN],
        },
    )
    assert view.name == "Review"
    assert view.attribute_ids == ["owner", "object_text", "status"]
    # Stored in the view's column order, whatever the client sent.
    assert list(view.column_widths) == [VIEW_ID_COLUMN, "status"]
    assert view.single_line == [VIEW_ID_COLUMN, "status"]

    text = (service.store.root / project_id / "project.json").read_text()
    assert text.endswith("\n")
    reloaded = ProjectService(ProjectStore(service.store.root)).list_views(project_id)[0]
    assert reloaded == view


def test_update_view_changes_only_given_fields_and_prunes_removed_columns(service):
    project_id, view_id = _project_with_view(service)
    service.update_view(
        project_id, view_id,
        {"column_widths": {"object_text": 300, "status": 80}, "single_line": ["status", "object_text"]},
    )
    view = service.update_view(project_id, view_id, {"attribute_ids": ["status", "owner"]})
    assert view.name == "Main"
    assert view.column_widths == {"status": 80}
    assert view.single_line == ["status"]
    view = service.update_view(project_id, view_id, {"name": "Renamed"})
    assert (view.attribute_ids, view.column_widths, view.single_line) == (["status", "owner"], {"status": 80}, ["status"])
    view = service.update_view(project_id, view_id, {"column_widths": None, "single_line": []})
    assert view.column_widths is None and view.single_line is None
    stored = json.loads((service.store.root / project_id / "project.json").read_text())["views"][0]
    assert list(stored) == ["id", "name", "attribute_ids"]


def test_update_view_may_keep_its_own_name_in_another_case(service):
    project_id, view_id = _project_with_view(service)
    assert service.update_view(project_id, view_id, {"name": "MAIN"}).name == "MAIN"


@pytest.mark.parametrize(
    "changes, message",
    [
        ({}, "Nothing to update"),
        ([], "must be an object"),
        ({"colour": "red"}, "Unknown view field"),
        ({"name": "  "}, "must not be empty"),
        ({"name": "other"}, "already used"),
        ({"attribute_ids": "status"}, "must be a list"),
        ({"attribute_ids": ["status", "status"]}, "Duplicate attribute"),
        ({"attribute_ids": ["nope"]}, "Unknown attribute"),
        ({"column_widths": {"owner": 100}}, "Unknown column 'owner'"),
        ({"attribute_ids": ["owner"], "column_widths": {"status": 100}}, "Unknown column 'status'"),
        ({"single_line": "status"}, "must be a list"),
        ({"single_line": ["owner"]}, "Unknown column 'owner'"),
        ({"single_line": ["status", "status"]}, "Duplicate column"),
    ],
)
def test_invalid_view_updates_are_rejected_and_nothing_is_written(service, changes, message):
    project_id, view_id = _project_with_view(service)
    service.create_view(project_id, "Other", [])
    path = service.store.root / project_id / "project.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        service.update_view(project_id, view_id, changes)
    assert path.read_bytes() == before


def test_update_missing_view_raises(service):
    project = service.create_project("P")
    with pytest.raises(FileNotFoundError):
        service.update_view(project.id, "nope", {"name": "x"})


def test_create_view_rejects_duplicate_columns_and_accepts_single_line(service):
    project = service.create_project("P")
    with pytest.raises(ValueError, match="Duplicate attribute"):
        service.create_view(project.id, "Dup", ["object_text", "object_text"])
    view = service.create_view(project.id, "Main", ["object_text"], None, ["object_text"])
    assert view.single_line == ["object_text"]


def test_views_with_order_and_layout_travel_through_save_as_template(service):
    project_id, view_id = _project_with_view(service)
    service.update_view(
        project_id, view_id,
        {"attribute_ids": ["status", "object_text"], "column_widths": {"status": 70}, "single_line": ["object_text"]},
    )
    service.save_project_as_template(project_id, "Layout template")
    template_id = next(t["id"] for t in service.list_templates() if t["name"] == "Layout template")
    copy = service.create_project("Copy", template_id=template_id)
    view = service.list_views(copy.id)[0]
    assert view.attribute_ids == ["status", "object_text"]
    assert view.column_widths == {"status": 70}
    assert view.single_line == ["object_text"]


def test_module_template_view_carries_order_and_layout(service):
    project_id, view_id = _project_with_view(service)
    module = service.create_module(project_id, "Reqs", "REQ")
    service.update_view(
        project_id, view_id,
        {"attribute_ids": ["status", "object_text"], "column_widths": {VIEW_ID_COLUMN: 90}, "single_line": ["status"]},
    )
    saved = service.save_module_as_template(project_id, module.id, "Layout module", view_id=view_id)
    other = service.create_project("Other")
    result = service.create_module_from_template(other.id, saved["id"])
    view = next(v for v in service.list_views(other.id) if v.id == result["view_id"])
    assert view.attribute_ids == ["status", "object_text"]
    assert view.column_widths == {VIEW_ID_COLUMN: 90}
    assert view.single_line == ["status"]


# --- REST / WebSocket -----------------------------------------------------------------


def test_update_view_rest_and_ws_parity_with_broadcast(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    status_id = "status"
    assert client.post(f"/projects/{project_id}/attributes", json={"id": status_id, "name": "Status"}).status_code == 200
    view_id = client.post(
        f"/projects/{project_id}/views", json={"name": "Main", "attribute_ids": ["object_text", status_id]}
    ).json()["id"]
    url = f"/projects/{project_id}/views/{view_id}"
    changed = {"event": "changed", "project_id": project_id}

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.put(url, json={"attribute_ids": [status_id, "object_text"], "single_line": [status_id]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["attribute_ids"] == [status_id, "object_text"]
        assert resp.json()["single_line"] == [status_id]
        assert json.loads(observer.receive_text()) == changed
        assert json.loads(ws.receive_text()) == changed

        response = _rpc(
            ws, "view.update",
            {"project_id": project_id, "id": view_id, "name": "Renamed", "column_widths": {"object_text": 222}},
        )
        result = response["result"]
        assert result["name"] == "Renamed"
        assert result["attribute_ids"] == [status_id, "object_text"]
        assert result["column_widths"] == {"object_text": 222}
        assert result["single_line"] == [status_id]
        assert json.loads(observer.receive_text()) == changed

        # Same message on both transports, no write, no broadcast.
        resp = client.put(url, json={"attribute_ids": ["object_text", "object_text"]})
        assert resp.status_code == 400
        response = _rpc(
            ws, "view.update", {"project_id": project_id, "id": view_id, "attribute_ids": ["object_text", "object_text"]}, 2
        )
        assert response["error"] == {"code": -32602, "message": resp.json()["detail"]}
        resp = client.put(url, json={})
        assert resp.status_code == 400
        assert "Nothing to update" in resp.json()["detail"]
        _assert_no_broadcast(observer)

    assert client.get(f"/projects/{project_id}/views").json()[0]["name"] == "Renamed"


def test_update_missing_view_is_404_and_32001(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    assert client.put(f"/projects/{project_id}/views/nope", json={"name": "x"}).status_code == 404
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "view.update", {"project_id": project_id, "id": "nope", "name": "x"})
        assert response["error"]["code"] == -32001


# --- view module scope ------------------------------------------------------------------


def _project_with_modules(service):
    project = service.create_project("P")
    first = service.create_module(project.id, "First", "FIR")
    second = service.create_module(project.id, "Second", "SEC")
    view = service.create_view(project.id, "Main", ["object_text"])
    return project.id, view.id, first.id, second.id


def test_view_modules_are_stored_in_module_order_and_written_only_when_set(service):
    project_id, view_id, first, second = _project_with_modules(service)
    path = service.store.root / project_id / "project.json"
    assert "modules" not in json.loads(path.read_text())["views"][0]
    view = service.update_view(project_id, view_id, {"modules": [second, first, second]})
    assert view.modules == [first, second]
    reloaded = ProjectService(ProjectStore(service.store.root)).list_views(project_id)[0]
    assert reloaded.modules == [first, second]
    assert list(json.loads(path.read_text())["views"][0])[-1] == "modules"
    view = service.update_view(project_id, view_id, {"modules": None})
    assert view.modules == []
    assert "modules" not in json.loads(path.read_text())["views"][0]
    created = service.create_view(project_id, "Scoped", [], None, None, [second])
    assert created.modules == [second]


@pytest.mark.parametrize("modules, message", [("x", "must be a list"), (["nope"], "Unknown module")])
def test_invalid_view_modules_are_rejected(service, modules, message):
    project_id, view_id, _, _ = _project_with_modules(service)
    path = service.store.root / project_id / "project.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        service.update_view(project_id, view_id, {"modules": modules})
    with pytest.raises(ValueError, match=message):
        service.create_view(project_id, "Other", [], None, None, modules)
    assert path.read_bytes() == before


def test_view_scoped_to_a_module_deleted_by_hand_can_still_be_saved(service):
    # R5: ids the view already stores but naming no module any more are pruned
    # on save; a new unknown id is still refused.
    import shutil

    project_id, view_id, first, second = _project_with_modules(service)
    service.update_view(project_id, view_id, {"modules": [first, second]})
    shutil.rmtree(service.store.root / project_id / "modules" / second)
    view = service.update_view(project_id, view_id, {"name": "Renamed", "modules": [first, second]})
    assert view.name == "Renamed" and view.modules == [first]
    service.update_view(project_id, view_id, {"modules": [first]})
    path = service.store.root / project_id / "project.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unknown module"):
        service.update_view(project_id, view_id, {"modules": [first, second]})  # no longer stored
    with pytest.raises(ValueError, match="Unknown module"):
        service.update_view(project_id, view_id, {"modules": [first, "nope"]})
    assert path.read_bytes() == before


def test_view_modules_travel_through_save_as_template(service):
    project_id, view_id, first, _ = _project_with_modules(service)
    service.update_view(project_id, view_id, {"modules": [first]})
    service.save_project_as_template(project_id, "Scoped template")
    template_id = next(t["id"] for t in service.list_templates() if t["name"] == "Scoped template")
    copy = service.create_project("Copy", template_id=template_id)
    module_ids = [m.id for m in service.list_modules(copy.id)]
    view = service.list_views(copy.id)[0]
    assert view.modules == [module_ids[0]]


def test_view_modules_rest_and_ws_parity(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "M", "prefix": "MOD"}).json()["id"]
    view = client.post(
        f"/projects/{project_id}/views", json={"name": "Main", "attribute_ids": [], "modules": [module_id]}
    ).json()
    assert view["modules"] == [module_id]
    changed = {"event": "changed", "project_id": project_id}
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.put(f"/projects/{project_id}/views/{view['id']}", json={"modules": []})
        assert resp.status_code == 200 and "modules" not in resp.json()
        assert json.loads(observer.receive_text()) == changed
        assert json.loads(ws.receive_text()) == changed
        response = _rpc(ws, "view.update", {"project_id": project_id, "id": view["id"], "modules": [module_id]})
        assert response["result"]["modules"] == [module_id]
        assert json.loads(observer.receive_text()) == changed
        resp = client.put(f"/projects/{project_id}/views/{view['id']}", json={"modules": ["nope"]})
        assert resp.status_code == 400
        response = _rpc(ws, "view.update", {"project_id": project_id, "id": view["id"], "modules": ["nope"]}, 2)
        assert response["error"] == {"code": -32602, "message": resp.json()["detail"]}
        _assert_no_broadcast(observer)
