"""Optional shared column widths of a named view (``View.column_widths``)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from reqman.models import View
from reqman.service import MAX_COLUMN_WIDTH, MIN_COLUMN_WIDTH, VIEW_ID_COLUMN, ProjectService
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
    view = service.create_view(project.id, "Main", ["object_text", "status"])
    return project.id, view.id


# --- model ------------------------------------------------------------------------


def test_view_without_widths_serializes_exactly_as_before():
    view = View(id="main", name="Main", attribute_ids=["object_text"])
    assert view.to_dict() == {"id": "main", "name": "Main", "attribute_ids": ["object_text"]}
    assert View.from_dict(view.to_dict()) == view


def test_view_widths_round_trip_and_bad_hand_edited_entries_are_dropped():
    view = View.from_dict(
        {"id": "m", "name": "M", "attribute_ids": ["a"], "column_widths": {"a": 120, "b": 0, "c": True, "d": "9"}}
    )
    assert view.column_widths == {"a": 120}
    assert View.from_dict(view.to_dict()) == view
    assert View.from_dict({"id": "m", "name": "M", "column_widths": [1, 2]}).column_widths is None


# --- service ----------------------------------------------------------------------


def test_project_json_unchanged_by_a_view_without_widths(service):
    project_id, view_id = _project_with_view(service)
    path = service.store.root / project_id / "project.json"
    before = path.read_bytes()
    assert b"column_widths" not in before
    service.set_view_column_widths(project_id, view_id, None)
    assert path.read_bytes() == before


def test_set_and_clear_view_column_widths_persists_in_view_column_order(service):
    project_id, view_id = _project_with_view(service)
    view = service.set_view_column_widths(project_id, view_id, {"status": 90, VIEW_ID_COLUMN: 110})
    assert list(view.column_widths.items()) == [(VIEW_ID_COLUMN, 110), ("status", 90)]

    path = service.store.root / project_id / "project.json"
    text = path.read_text()
    assert text.endswith("\n")
    stored = json.loads(text)["views"][0]
    assert list(stored) == ["id", "name", "attribute_ids", "column_widths"]
    assert stored["column_widths"] == {VIEW_ID_COLUMN: 110, "status": 90}
    assert '\n      "column_widths": {\n        "@id": 110,' in text  # two-space indentation

    reloaded = ProjectService(ProjectStore(service.store.root))
    assert reloaded.list_views(project_id)[0].column_widths == {VIEW_ID_COLUMN: 110, "status": 90}

    service.set_view_column_widths(project_id, view_id, {})
    assert "column_widths" not in json.loads(path.read_text())["views"][0]


def test_create_view_accepts_column_widths(service):
    project = service.create_project("P")
    view = service.create_view(project.id, "Main", ["object_text"], {"object_text": 300})
    assert view.column_widths == {"object_text": 300}


@pytest.mark.parametrize(
    "widths, message",
    [
        ([100], "must be an object"),
        ("wide", "must be an object"),
        ({"other": 100}, "Unknown column 'other'"),
        ({"status": 100}, "Unknown column 'status'"),  # a project attribute outside the view
        ({"object_text": MIN_COLUMN_WIDTH - 1}, "must be an integer"),
        ({"object_text": MAX_COLUMN_WIDTH + 1}, "must be an integer"),
        ({"object_text": 120.5}, "must be an integer"),
        ({"object_text": True}, "must be an integer"),
        ({"object_text": "120"}, "must be an integer"),
        ({"object_text": None}, "must be an integer"),
    ],
)
def test_invalid_column_widths_are_rejected_and_nothing_is_written(service, widths, message):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "status", "Status")
    view = service.create_view(project.id, "Main", ["object_text"])
    path = service.store.root / project.id / "project.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        service.set_view_column_widths(project.id, view.id, widths)
    with pytest.raises(ValueError, match=message):
        service.create_view(project.id, "Other", ["object_text"], widths)
    assert path.read_bytes() == before


def test_set_column_widths_of_missing_view_raises(service):
    project = service.create_project("P")
    with pytest.raises(FileNotFoundError):
        service.set_view_column_widths(project.id, "nope", {VIEW_ID_COLUMN: 100})


# --- REST / WebSocket -----------------------------------------------------------------


def test_column_widths_rest_and_ws_parity_with_broadcast(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    view_id = client.post(
        f"/projects/{project_id}/views", json={"name": "Main", "attribute_ids": ["object_text"]}
    ).json()["id"]
    url = f"/projects/{project_id}/views/{view_id}/column-widths"
    changed = {"event": "changed", "project_id": project_id}

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.put(url, json={"column_widths": {"object_text": 250}})
        assert resp.status_code == 200
        assert resp.json()["column_widths"] == {"object_text": 250}
        assert json.loads(observer.receive_text()) == changed
        assert json.loads(ws.receive_text()) == changed

        response = _rpc(
            ws, "view.set_column_widths",
            {"project_id": project_id, "id": view_id, "column_widths": {VIEW_ID_COLUMN: 80}},
        )
        assert response["result"]["column_widths"] == {VIEW_ID_COLUMN: 80}
        assert json.loads(observer.receive_text()) == changed

        response = _rpc(ws, "view.set_column_widths", {"project_id": project_id, "id": view_id, "column_widths": None})
        assert "column_widths" not in response["result"]
        assert json.loads(observer.receive_text()) == changed

        created = _rpc(
            ws, "view.create",
            {"project_id": project_id, "name": "Wide", "attribute_ids": ["object_text"],
             "column_widths": {"object_text": 500}},
        )
        assert created["result"]["column_widths"] == {"object_text": 500}
        assert json.loads(observer.receive_text()) == changed

    views = client.get(f"/projects/{project_id}/views").json()
    assert [v.get("column_widths") for v in views] == [None, {"object_text": 500}]


def test_invalid_column_widths_are_400_on_rest_and_32602_on_ws_without_broadcast(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    view_id = client.post(
        f"/projects/{project_id}/views", json={"name": "Main", "attribute_ids": ["object_text"]}
    ).json()["id"]
    path = tmp_path / project_id / "project.json"
    before = path.read_bytes()
    expected = f"Width of column 'object_text' must be an integer from {MIN_COLUMN_WIDTH} to {MAX_COLUMN_WIDTH}"

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.put(
            f"/projects/{project_id}/views/{view_id}/column-widths", json={"column_widths": {"object_text": 0}}
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == expected
        response = _rpc(
            ws, "view.set_column_widths",
            {"project_id": project_id, "id": view_id, "column_widths": {"object_text": 0}},
        )
        assert response["error"] == {"code": -32602, "message": expected}

        resp = client.post(
            f"/projects/{project_id}/views",
            json={"name": "Bad", "attribute_ids": ["object_text"], "column_widths": {"x": 100}},
        )
        assert resp.status_code == 400
        _assert_no_broadcast(observer)
    assert path.read_bytes() == before


def test_column_widths_of_missing_view_is_404_and_32001(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    resp = client.put(f"/projects/{project_id}/views/nope/column-widths", json={"column_widths": None})
    assert resp.status_code == 404
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "view.set_column_widths", {"project_id": project_id, "id": "nope", "column_widths": None})
        assert response["error"]["code"] == -32001
