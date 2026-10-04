import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_create_and_get_project(client):
    resp = client.post("/projects", json={"name": "Project One"})
    assert resp.status_code == 200, resp.text
    project_id = resp.json()["id"]
    assert project_id == "project-one"

    resp = client.get(f"/projects/{project_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Project One"


def test_full_crud_flow(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    mod1_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module One", "prefix": "REQ"}).json()["id"]
    mod2_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module Two", "prefix": "REQ"}).json()["id"]

    resp = client.post(
        f"/projects/{project_id}/modules/{mod1_id}/requirements",
        json={"attributes": {"object_text": "First"}},
    )
    assert resp.status_code == 200, resp.text
    req1_id = resp.json()["id"]
    assert req1_id == "REQ-0001"

    resp = client.post(
        f"/projects/{project_id}/modules/{mod2_id}/requirements",
        json={"attributes": {"object_text": "Second"}},
    )
    assert resp.status_code == 200, resp.text
    req2_id = resp.json()["id"]
    assert req2_id == "REQ-0002"

    config_id = client.post(
        f"/projects/{project_id}/link-configurations",
        json={"name": "Parent", "source_module_id": mod2_id, "target_module_id": mod1_id},
    ).json()["id"]

    resp = client.post(
        f"/projects/{project_id}/links",
        json={"config_id": config_id, "source_id": req2_id, "target_id": req1_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"]["attributes"][config_id] == f"> {req1_id}"
    assert resp.json()["target"]["attributes"][config_id] == f"< {req2_id}"

    resp = client.put(
        f"/projects/{project_id}/modules/{mod1_id}/requirements/{req1_id}",
        json={"attributes": {"object_text": "First updated"}},
    )
    assert resp.status_code == 200
    assert resp.json()["attributes"]["object_text"] == "First updated"

    resp = client.delete(f"/projects/{project_id}/modules/{mod1_id}/requirements/{req1_id}")
    assert resp.status_code == 200

    resp = client.get(f"/projects/{project_id}/modules/{mod2_id}/requirements/{req2_id}")
    assert config_id not in resp.json()["attributes"]


def test_missing_project_returns_404(client):
    resp = client.get("/projects/does-not-exist")
    assert resp.status_code == 404


def test_export_reqif(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module One", "prefix": "REQ"}).json()["id"]
    client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements",
        json={"attributes": {"object_text": "First"}},
    )
    resp = client.get(f"/projects/{project_id}/export/reqif")
    assert resp.status_code == 200, resp.text
    assert b"<?xml" in resp.content or b"REQ-IF" in resp.content or b"REQ-0001" in resp.content


def test_export_csv(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module One", "prefix": "REQ"}).json()["id"]
    client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements",
        json={"attributes": {"object_text": "First"}},
    )
    resp = client.get(f"/projects/{project_id}/export/csv")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.content.decode()
    assert "Id,Module,Parent,ObjectText" in body
    assert "REQ-0001" in body


def test_export_xlsx(client):
    import io

    from openpyxl import load_workbook

    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module One", "prefix": "REQ"}).json()["id"]
    client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements",
        json={"attributes": {"object_text": "First"}},
    )
    resp = client.get(f"/projects/{project_id}/export/xlsx")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    workbook = load_workbook(io.BytesIO(resp.content))
    rows = list(workbook.active.iter_rows(values_only=True))
    assert rows[0] == ("Id", "Module", "Parent", "ObjectText")
    assert rows[1][0] == "REQ-0001"


def test_export_docx(client):
    import io

    from docx import Document as DocxDocument

    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module One", "prefix": "REQ"}).json()["id"]
    client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements",
        json={"attributes": {"object_text": "First"}},
    )
    resp = client.get(f"/projects/{project_id}/export/docx")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    document = DocxDocument(io.BytesIO(resp.content))
    body_texts = [p.text for p in document.paragraphs]
    assert "Project One" in body_texts
    assert "REQ-0001" in body_texts


def test_websocket_rpc_roundtrip(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "project.create", "params": {"name": "Project One"}}))
        response = json.loads(ws.receive_text())
        project_id = response["result"]["id"]
        assert project_id == "project-one"

        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "module.create",
            "params": {"project_id": project_id, "name": "Module One", "prefix": "REQ"},
        }))
        response = json.loads(ws.receive_text())
        module_id = response["result"]["id"]
        assert module_id == "module-one"

        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "requirement.add",
            "params": {"project_id": project_id, "module_id": module_id, "attributes": {"object_text": "First"}},
        }))
        response = json.loads(ws.receive_text())
        assert response["result"]["id"] == "REQ-0001"
        assert response["result"]["attributes"]["object_text"] == "First"


def test_broadcast_notifies_other_ws_clients_on_rest_mutation(client):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        resp = client.post("/projects", json={"name": "Project One"})
        project_id = resp.json()["id"]
        event1 = json.loads(ws1.receive_text())
        event2 = json.loads(ws2.receive_text())
        assert event1 == {"event": "changed", "project_id": project_id}
        assert event2 == {"event": "changed", "project_id": project_id}


def test_broadcast_excludes_sender_on_ws_mutation(client):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        ws1.send_text(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "project.create", "params": {"name": "Project One"}}))
        response = json.loads(ws1.receive_text())
        project_id = response["result"]["id"]

        event2 = json.loads(ws2.receive_text())
        assert event2 == {"event": "changed", "project_id": project_id}


def test_nested_requirement_and_move(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module One", "prefix": "REQ"}).json()["id"]

    chapter_id = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements",
        json={"attributes": {"object_text": "Chapter 1"}},
    ).json()["id"]

    resp = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements",
        json={"attributes": {"object_text": "Child"}, "parent_id": chapter_id},
    )
    assert resp.status_code == 200, resp.text
    child_id = resp.json()["id"]
    assert resp.json()["parent_id"] == chapter_id

    chapter2_id = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements",
        json={"attributes": {"object_text": "Chapter 2"}},
    ).json()["id"]

    resp = client.put(
        f"/projects/{project_id}/modules/{module_id}/requirements/{child_id}/parent",
        json={"parent_id": chapter2_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_id"] == chapter2_id

    resp = client.put(
        f"/projects/{project_id}/modules/{module_id}/requirements/{child_id}/parent",
        json={"parent_id": child_id},
    )
    assert resp.status_code == 400

    resp = client.delete(f"/projects/{project_id}/modules/{module_id}/requirements/{chapter2_id}")
    assert resp.status_code == 200
    resp = client.get(f"/projects/{project_id}/modules/{module_id}/requirements/{child_id}")
    assert resp.json()["parent_id"] is None


def test_get_config_returns_current_data_dir(client, tmp_path):
    resp = client.get("/config")
    assert resp.status_code == 200
    assert resp.json()["data_dir"] == str(tmp_path)


def test_set_config_switches_data_dir_and_persists(client, tmp_path):
    new_dir = tmp_path / "other-root"
    resp = client.post("/config", json={"data_dir": str(new_dir)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data_dir"] == str(new_dir)

    # New projects are now created under the new root.
    resp = client.post("/projects", json={"name": "Project One"})
    assert resp.status_code == 200, resp.text
    assert (new_dir / "project-one" / "project.json").exists()
    assert not (tmp_path / "project-one" / "project.json").exists()

    # The choice survives a process restart (module reload).
    config_file = tmp_path / "config.json"
    assert json.loads(config_file.read_text())["data_dir"] == str(new_dir)


def test_set_config_broadcasts_root_changed_to_ws_clients(client, tmp_path):
    with client.websocket_connect("/ws") as ws:
        resp = client.post("/config", json={"data_dir": str(tmp_path / "other-root")})
        assert resp.status_code == 200
        event = json.loads(ws.receive_text())
        assert event == {"event": "root_changed"}


def test_link_configuration_crud_and_direction_enforcement(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    src_id = client.post(f"/projects/{project_id}/modules", json={"name": "Source", "prefix": "SRC"}).json()["id"]
    tgt_id = client.post(f"/projects/{project_id}/modules", json={"name": "Target", "prefix": "TGT"}).json()["id"]

    resp = client.post(
        f"/projects/{project_id}/link-configurations",
        json={"name": "Verifies", "source_module_id": src_id, "target_module_id": tgt_id},
    )
    assert resp.status_code == 200, resp.text
    config_id = resp.json()["id"]

    resp = client.get(f"/projects/{project_id}/link-configurations")
    assert resp.status_code == 200
    assert [c["id"] for c in resp.json()] == [config_id]

    source_id = client.post(
        f"/projects/{project_id}/modules/{src_id}/requirements", json={"attributes": {"object_text": "S"}}
    ).json()["id"]
    target_id = client.post(
        f"/projects/{project_id}/modules/{tgt_id}/requirements", json={"attributes": {"object_text": "T"}}
    ).json()["id"]

    # Wrong direction is rejected.
    resp = client.post(
        f"/projects/{project_id}/links",
        json={"config_id": config_id, "source_id": target_id, "target_id": source_id},
    )
    assert resp.status_code == 400

    # Correct direction succeeds and both sides' mirrors are set.
    resp = client.post(
        f"/projects/{project_id}/links",
        json={"config_id": config_id, "source_id": source_id, "target_id": target_id},
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/projects/{project_id}/modules/{src_id}/requirements/{source_id}")
    assert resp.json()["attributes"][config_id] == f"> {target_id}"
    resp = client.get(f"/projects/{project_id}/modules/{tgt_id}/requirements/{target_id}")
    assert resp.json()["attributes"][config_id] == f"< {source_id}"

    # Removing the configuration cleans up the attribute definition and mirrored values.
    resp = client.delete(f"/projects/{project_id}/link-configurations/{config_id}")
    assert resp.status_code == 200

    resp = client.get(f"/projects/{project_id}/modules/{src_id}/requirements/{source_id}")
    assert config_id not in resp.json()["attributes"]
    resp = client.get(f"/projects/{project_id}")
    assert config_id not in {a["id"] for a in resp.json()["attributes"]}


def test_link_requirements_without_configuration_returns_404(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "REQ"}).json()["id"]
    a_id = client.post(f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "A"}}).json()["id"]
    b_id = client.post(f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "B"}}).json()["id"]

    resp = client.post(
        f"/projects/{project_id}/links",
        json={"config_id": "does-not-exist", "source_id": a_id, "target_id": b_id},
    )
    assert resp.status_code == 404


def test_reorder_requirement(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "REQ"}).json()["id"]
    r1_id = client.post(f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "First"}}).json()["id"]
    r2_id = client.post(f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "Second"}}).json()["id"]

    resp = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements/{r2_id}/reorder",
        json={"direction": "up"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["order"] == 0

    resp = client.get(f"/projects/{project_id}/modules/{module_id}/requirements/{r1_id}")
    assert resp.json()["order"] == 1


def test_copy_requirement(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "REQ"}).json()["id"]
    parent_id = client.post(f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "Parent"}}).json()["id"]
    child_id = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements",
        json={"attributes": {"object_text": "Child"}, "parent_id": parent_id},
    ).json()["id"]

    resp = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements/{parent_id}/copy",
        json={"target_parent_id": None},
    )
    assert resp.status_code == 200, resp.text
    copy_id = resp.json()["id"]
    assert copy_id != parent_id

    resp = client.get(f"/projects/{project_id}/modules/{module_id}/requirements")
    all_reqs = resp.json()
    copied_children = [r for r in all_reqs if r["parent_id"] == copy_id]
    assert len(copied_children) == 1
    assert copied_children[0]["id"] != child_id


def test_browse_folders_lists_subdirectories(client, tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "not_a_dir.txt").write_text("x")

    resp = client.get("/fs/browse", params={"path": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["path"] == str(tmp_path)
    assert [d["name"] for d in body["directories"]] == ["alpha", "beta"]


def test_browse_folders_missing_path_returns_404(client, tmp_path):
    resp = client.get("/fs/browse", params={"path": str(tmp_path / "does-not-exist")})
    assert resp.status_code == 404


def test_browse_folders_defaults_without_path_param(client):
    resp = client.get("/fs/browse")
    assert resp.status_code == 200
    assert "directories" in resp.json()


def test_view_crud(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]

    resp = client.post(f"/projects/{project_id}/views", json={"name": "Compact", "attribute_ids": ["object_text"]})
    assert resp.status_code == 200, resp.text
    view_id = resp.json()["id"]

    resp = client.get(f"/projects/{project_id}/views")
    assert resp.status_code == 200
    assert [v["id"] for v in resp.json()] == [view_id]

    resp = client.delete(f"/projects/{project_id}/views/{view_id}")
    assert resp.status_code == 200

    resp = client.get(f"/projects/{project_id}/views")
    assert resp.json() == []


def test_create_view_unknown_attribute_returns_400(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    resp = client.post(f"/projects/{project_id}/views", json={"name": "Compact", "attribute_ids": ["nope"]})
    assert resp.status_code == 400


def test_find_requirement_across_modules(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    mod1_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module One", "prefix": "REQ"}).json()["id"]
    mod2_id = client.post(f"/projects/{project_id}/modules", json={"name": "Module Two", "prefix": "TEST"}).json()["id"]
    req_id = client.post(
        f"/projects/{project_id}/modules/{mod2_id}/requirements",
        json={"attributes": {"object_text": "Somewhere"}},
    ).json()["id"]

    resp = client.get(f"/projects/{project_id}/requirements/{req_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["module_id"] == mod2_id
    assert body["requirement"]["id"] == req_id


def test_find_requirement_missing_returns_404(client):
    project_id = client.post("/projects", json={"name": "Project One"}).json()["id"]
    resp = client.get(f"/projects/{project_id}/requirements/REQ-9999")
    assert resp.status_code == 404
