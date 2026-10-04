"""Edge cases for data format versions, persisted module order, project view
names and UTF-8 BOM templates (complements test_formats_and_module_order.py).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from reqman import config as config_module
from reqman.formats import CONFIG_FORMAT, PROJECT_FORMAT, NewerFormatError
from reqman.service import ProjectService
from reqman.store import ProjectStore
from reqman.templates import parse_template_text, read_builtin_template_text

NEWER = PROJECT_FORMAT.current + 1
NEWER_MESSAGE = "written by a newer ReqManTool"
INVALID_VERSIONS = ["1", True, False, 0, -3, 1.5, None]


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
    """The next message on ``ws`` is the reply to a harmless request, not a queued event."""
    response = _rpc(ws, "template.list", {}, request_id)
    assert response.get("id") == request_id, response


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


def _set_raw(path: Path, **changes) -> None:
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _delete_raw(path: Path, *keys) -> None:
    data = json.loads(path.read_text())
    for key in keys:
        data.pop(key, None)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _external_edit(path: Path, how: str, **changes) -> None:
    """Edit a JSON file like another program would: in place, or via a temp file + rename."""
    before = path.stat()
    data = json.loads(path.read_text())
    data.update(changes)
    text = json.dumps(data, indent=2) + "\n"
    if how == "in_place":
        path.write_text(text)
        # A later save always has a later mtime; make that explicit so the
        # test is deterministic on file systems with coarse timestamps.
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    else:
        tmp = path.with_name("project.json.tmp-editor")
        tmp.write_text(text)
        os.replace(tmp, path)


def _project_with_data(service):
    project = service.create_project("P")
    module = service.create_module(project.id, "Reqs", "R")
    other = service.create_module(project.id, "Tests", "T")
    req = service.add_requirement(project.id, module.id, {"object_text": "x"})
    target = service.add_requirement(project.id, other.id, {"object_text": "y"})
    config = service.create_link_configuration(project.id, "Verifies", module.id, other.id)
    service.link_requirements(project.id, config.id, req.id, target.id)
    return project, module, other, req, target, config


def _rest_project(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Reqs", "prefix": "R"}).json()["id"]
    other_id = client.post(f"/projects/{project_id}/modules", json={"name": "Tests", "prefix": "T"}).json()["id"]
    req_id = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "x"}}
    ).json()["id"]
    target_id = client.post(
        f"/projects/{project_id}/modules/{other_id}/requirements", json={"attributes": {"object_text": "y"}}
    ).json()["id"]
    config_id = client.post(
        f"/projects/{project_id}/link-configurations",
        json={"name": "Verifies", "source_module_id": module_id, "target_module_id": other_id},
    ).json()["id"]
    resp = client.post(
        f"/projects/{project_id}/links", json={"config_id": config_id, "source_id": req_id, "target_id": target_id}
    )
    assert resp.status_code == 200, resp.text
    return project_id, module_id, other_id, req_id, target_id, config_id


# --- format check after an external edit (cache invalidation) -----------------------


@pytest.mark.parametrize("how", ["in_place", "replace"])
def test_external_bump_to_newer_version_after_a_read_is_detected(service, how):
    project, module, other, req, target, config = _project_with_data(service)
    store = service.store
    # Warm the per-project format check with reads that do not load project.json.
    service.get_module(project.id, module.id)
    service.get_requirement(project.id, module.id, req.id)
    store.load_link_pairs(project.id, config.id)

    _external_edit(store.root / project.id / "project.json", how, format_version=NEWER)
    before = _snapshot(store.root)

    for call in (
        lambda: service.get_module(project.id, module.id),
        lambda: service.get_requirement(project.id, module.id, req.id),
        lambda: store.load_link_pairs(project.id, config.id),
        lambda: store.save_requirement(project.id, module.id, store.load_requirement(project.id, module.id, req.id)),
        lambda: store.save_module(project.id, module),
        lambda: store.save_link_pairs(project.id, config.id, []),
        lambda: store.delete_requirement(project.id, other.id, target.id),
        lambda: service.update_requirement(project.id, module.id, req.id, {"object_text": "changed"}),
    ):
        with pytest.raises(NewerFormatError, match=NEWER_MESSAGE):
            call()
    assert _snapshot(store.root) == before


def test_same_size_in_place_bump_with_restored_mtime_is_detected(service):
    """Only the ctime changes: the format cache must not keep trusting the old check."""
    project, module, _, _, _, _ = _project_with_data(service)
    path = service.store.root / project.id / "project.json"
    service.get_module(project.id, module.id)  # warm the format cache
    before = path.stat()
    text = path.read_text()
    assert f'"format_version": {PROJECT_FORMAT.current},' in text
    edited = text.replace(f'"format_version": {PROJECT_FORMAT.current},', f'"format_version": {NEWER},', 1)
    assert len(edited) == len(text)
    with open(path, "r+", encoding="utf-8") as handle:  # in place: same inode
        handle.write(edited)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_mtime_ns, after.st_size, after.st_ino) == (before.st_mtime_ns, before.st_size, before.st_ino)
    if after.st_ctime_ns == before.st_ctime_ns:
        pytest.skip("filesystem ctime granularity too coarse")
    with pytest.raises(NewerFormatError, match=NEWER_MESSAGE):
        service.get_module(project.id, module.id)


def test_newer_version_is_rejected_by_a_fresh_store_too(service):
    project, module, _, req, _, _ = _project_with_data(service)
    _set_raw(service.store.root / project.id / "project.json", format_version=NEWER)
    fresh = ProjectService(ProjectStore(service.store.root))
    with pytest.raises(NewerFormatError):
        fresh.get_requirement(project.id, module.id, req.id)


def test_external_downgrade_back_to_a_supported_version_is_readable_again(service):
    project, module, _, req, _, _ = _project_with_data(service)
    path = service.store.root / project.id / "project.json"
    _external_edit(path, "replace", format_version=NEWER)
    with pytest.raises(NewerFormatError):
        service.get_module(project.id, module.id)

    _external_edit(path, "replace", format_version=PROJECT_FORMAT.current)
    assert service.get_module(project.id, module.id).id == module.id
    updated = service.update_requirement(project.id, module.id, req.id, {"object_text": "again"})
    assert updated.attributes["object_text"] == "again"
    reloaded = ProjectService(ProjectStore(service.store.root))
    assert reloaded.get_requirement(project.id, module.id, req.id).attributes["object_text"] == "again"


# --- invalid format_version in project.json -------------------------------------------


@pytest.mark.parametrize("value", INVALID_VERSIONS)
def test_invalid_project_format_version_is_an_error_and_never_overwritten(service, value):
    project, module, _, req, _, _ = _project_with_data(service)
    _set_raw(service.store.root / project.id / "project.json", format_version=value)
    before = _snapshot(service.store.root)
    for call in (
        lambda: service.get_project(project.id),
        lambda: service.get_module(project.id, module.id),
        lambda: service.get_requirement(project.id, module.id, req.id),
        lambda: service.create_module(project.id, "More", "M"),
        lambda: service.add_requirement(project.id, module.id, {"object_text": "z"}),
        lambda: service.update_requirement(project.id, module.id, req.id, {"object_text": "z"}),
        lambda: service.create_view(project.id, "V", []),
    ):
        with pytest.raises(ValueError, match="invalid format_version") as excinfo:
            call()
        assert not isinstance(excinfo.value, NewerFormatError)
    assert _snapshot(service.store.root) == before


@pytest.mark.parametrize("value", ["1", True, 0])
def test_invalid_project_format_version_is_400_on_rest_and_32602_on_ws(client, tmp_path, value):
    project_id, module_id, _, req_id, _, _ = _rest_project(client)
    _set_raw(tmp_path / project_id / "project.json", format_version=value)
    before = _snapshot(tmp_path / project_id)

    for resp in (
        client.get(f"/projects/{project_id}"),
        client.get(f"/projects/{project_id}/modules"),
        client.get(f"/projects/{project_id}/modules/{module_id}"),
        client.get(f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}"),
        client.post(f"/projects/{project_id}/modules", json={"name": "N", "prefix": "N"}),
        client.post(f"/projects/{project_id}/modules/{module_id}/reorder", json={"direction": "down"}),
        client.post(f"/projects/{project_id}/views", json={"name": "V"}),
    ):
        assert resp.status_code == 400, resp.text
        assert "invalid format_version" in resp.json()["detail"]
    # The list shows the project as a read-only stub carrying the same error.
    [stub] = client.get("/projects").json()
    assert stub["id"] == project_id and "invalid format_version" in stub["error"]

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        for index, (method, params) in enumerate(
            (
                ("project.get", {"id": project_id}),
                ("module.get", {"project_id": project_id, "id": module_id}),
                ("module.create", {"project_id": project_id, "name": "N", "prefix": "N"}),
                ("module.reorder", {"project_id": project_id, "id": module_id, "direction": "down"}),
                ("requirement.add", {"project_id": project_id, "module_id": module_id, "attributes": {}}),
            ),
            start=1,
        ):
            response = _rpc(ws, method, params, index)
            assert response["error"]["code"] == -32602, response
            assert "invalid format_version" in response["error"]["message"]
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path / project_id) == before


# --- config format version -----------------------------------------------------------


@pytest.mark.parametrize("value", ["1", True, 0, None])
def test_config_with_invalid_format_version_raises_and_is_not_overwritten(tmp_path, monkeypatch, value):
    config_file = tmp_path / "config.json"
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_file))
    config_file.write_text(json.dumps({"format_version": value, "data_dir": "/x"}) + "\n")
    before = config_file.read_bytes()
    with pytest.raises(ValueError, match="invalid format_version"):
        config_module.load_saved_data_dir()
    with pytest.raises(ValueError, match="invalid format_version"):
        config_module.check_config_writable()
    with pytest.raises(ValueError, match="invalid format_version"):
        config_module.save_data_dir("/y")
    assert config_file.read_bytes() == before


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '"text"', "", "\x00\x01"])
def test_malformed_config_is_ignored_and_replaced_on_save(tmp_path, monkeypatch, content):
    config_file = tmp_path / "config.json"
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_file))
    config_file.write_text(content)
    assert config_module.load_saved_data_dir() is None
    config_module.check_config_writable()  # nothing to protect
    assert config_file.read_text() == content  # reading did not rewrite it

    config_module.save_data_dir("/new/dir")
    text = config_file.read_text()
    assert text == json.dumps({"format_version": CONFIG_FORMAT.current, "data_dir": "/new/dir"}, indent=2) + "\n"
    assert config_module.load_saved_data_dir() == "/new/dir"


def test_config_with_bom_and_current_version_loads(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_file))
    config_file.write_bytes(b"\xef\xbb\xbf" + json.dumps({"format_version": 1, "data_dir": "/bom/dir"}).encode())
    assert config_module.load_saved_data_dir() == "/bom/dir"


def test_missing_config_file_is_created_with_format_version_first(tmp_path, monkeypatch):
    config_file = tmp_path / "nested" / "config.json"
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_file))
    assert config_module.load_saved_data_dir() is None
    config_module.check_config_writable()
    config_module.save_data_dir("/a")
    data = json.loads(config_file.read_text())
    assert list(data) == ["format_version", "data_dir"]
    assert config_file.read_text().endswith("}\n")


def test_set_config_with_newer_config_switches_nothing_and_does_not_broadcast(client, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"format_version": 99, "data_dir": "/elsewhere"}, indent=4))
    before = config_file.read_bytes()
    new_dir = tmp_path / "not-created"
    with client.websocket_connect("/ws") as ws:
        resp = client.post("/config", json={"data_dir": str(new_dir)})
        assert resp.status_code == 409
        assert NEWER_MESSAGE in resp.json()["detail"]
        _assert_no_broadcast(ws)
    assert config_file.read_bytes() == before
    assert not new_dir.exists()  # refused before the new directory was created
    assert client.get("/config").json()["data_dir"] == str(tmp_path.resolve())


@pytest.mark.parametrize("value", ["2", False, 0])
def test_set_config_with_invalid_config_version_is_400_and_unchanged(client, tmp_path, value):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"format_version": value, "data_dir": "/elsewhere"}) + "\n")
    before = config_file.read_bytes()
    resp = client.post("/config", json={"data_dir": str(tmp_path / "other")})
    assert resp.status_code == 400
    assert "invalid format_version" in resp.json()["detail"]
    assert config_file.read_bytes() == before
    assert client.get("/config").json()["data_dir"] == str(tmp_path.resolve())


def test_set_config_replaces_malformed_config_and_broadcasts_root_changed(client, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("{broken")
    new_dir = tmp_path / "other"
    with client.websocket_connect("/ws") as ws:
        resp = client.post("/config", json={"data_dir": str(new_dir)})
        assert resp.status_code == 200, resp.text
        assert json.loads(ws.receive_text()) == {"event": "root_changed"}
    expected = {"format_version": CONFIG_FORMAT.current, "data_dir": str(new_dir.resolve())}
    assert config_file.read_text() == json.dumps(expected, indent=2) + "\n"
    assert client.get("/config").json()["data_dir"] == str(new_dir.resolve())


def test_app_refuses_to_start_with_a_newer_saved_config(tmp_path, monkeypatch):
    import reqman.app as app_module

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"format_version": 99, "data_dir": str(tmp_path / "x")}) + "\n")
    before = config_file.read_bytes()
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_file))
    monkeypatch.delenv("REQMAN_DATA_DIR", raising=False)
    try:
        with pytest.raises(NewerFormatError, match=NEWER_MESSAGE):
            importlib.reload(app_module)
        assert config_file.read_bytes() == before
        assert not (tmp_path / "x").exists()

        # An explicit data directory overrides (and does not read) the saved config.
        monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path / "explicit"))
        importlib.reload(app_module)
        assert app_module.DATA_DIR == (tmp_path / "explicit").resolve()
        assert config_file.read_bytes() == before
    finally:
        monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path / "explicit"))
        importlib.reload(app_module)


# --- writes next to / into a newer project ------------------------------------------


def _make_newer(root: Path, project_id: str) -> None:
    _set_raw(root / project_id / "project.json", format_version=NEWER)


def test_newer_project_rest_reads_and_writes_are_409_without_broadcast(client, tmp_path):
    project_id, module_id, other_id, req_id, target_id, config_id = _rest_project(client)
    client.post(f"/projects/{project_id}/views", json={"name": "Main"})
    _make_newer(tmp_path, project_id)
    before = _snapshot(tmp_path)
    base = f"/projects/{project_id}"
    req_url = f"{base}/modules/{module_id}/requirements/{req_id}"
    link_body = {"config_id": config_id, "source_id": req_id, "target_id": target_id}

    with client.websocket_connect("/ws") as ws:
        responses = {
            "add attribute": client.post(f"{base}/attributes", json={"id": "owner", "name": "Owner"}),
            "create link config": client.post(
                f"{base}/link-configurations",
                json={"name": "Other", "source_module_id": other_id, "target_module_id": module_id},
            ),
            "list link configs": client.get(f"{base}/link-configurations"),
            "remove link config": client.delete(f"{base}/link-configurations/{config_id}"),
            "create view": client.post(f"{base}/views", json={"name": "V"}),
            "list views": client.get(f"{base}/views"),
            "remove view": client.delete(f"{base}/views/main"),
            "find requirement": client.get(f"{base}/requirements/{req_id}"),
            "get module": client.get(f"{base}/modules/{module_id}"),
            "create module": client.post(f"{base}/modules", json={"name": "N", "prefix": "N"}),
            "reorder module": client.post(f"{base}/modules/{other_id}/reorder", json={"direction": "up"}),
            "list requirements": client.get(f"{base}/modules/{module_id}/requirements"),
            "get requirement": client.get(req_url),
            "update requirement": client.put(req_url, json={"attributes": {"object_text": "z"}}),
            "remove requirement": client.delete(req_url),
            "move requirement": client.put(f"{req_url}/parent", json={"parent_id": None}),
            "reorder requirement": client.post(f"{req_url}/reorder", json={"direction": "down"}),
            "copy requirement": client.post(f"{req_url}/copy", json={}),
            "link": client.post(f"{base}/links", json=link_body),
            "unlink": client.request("DELETE", f"{base}/links", json=link_body),
            "import csv": client.post(
                f"{base}/modules/{module_id}/import/csv",
                content=b"ObjectText\nimported\n",
                headers={"Content-Type": "text/csv"},
            ),
            "purge module": client.post(f"{base}/modules/{module_id}/purge"),
            "purge project": client.post(f"{base}/purge"),
            "set parent": client.put(f"{base}/parent", json={"parent_project_id": None}),
            "save as template": client.post(f"{base}/save-as-template", json={"name": "From newer"}),
            "export reqif": client.get(f"{base}/export/reqif"),
            "export csv": client.get(f"{base}/export/csv"),
            "export xlsx": client.get(f"{base}/export/xlsx"),
            "export docx": client.get(f"{base}/export/docx"),
        }
        for label, resp in responses.items():
            assert resp.status_code == 409, f"{label}: {resp.status_code} {resp.text}"
            assert NEWER_MESSAGE in resp.json()["detail"], label
        _assert_no_broadcast(ws)
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "_templates").exists()


def test_newer_project_ws_writes_are_rejected_without_broadcast(client, tmp_path):
    project_id, module_id, other_id, req_id, target_id, config_id = _rest_project(client)
    _make_newer(tmp_path, project_id)
    before = _snapshot(tmp_path)
    calls = [
        ("module.reorder", {"project_id": project_id, "id": other_id, "direction": "up"}),
        ("module.reorder", {"project_id": project_id, "id": module_id, "direction": "up"}),  # boundary
        ("module.create", {"project_id": project_id, "name": "N", "prefix": "N"}),
        ("requirement.add", {"project_id": project_id, "module_id": module_id, "attributes": {}}),
        ("requirement.link", {"project_id": project_id, "config_id": config_id,
                              "source_id": req_id, "target_id": target_id}),
        ("link_configuration.create", {"project_id": project_id, "name": "L",
                                       "source_module_id": other_id, "target_module_id": module_id}),
        ("view.create", {"project_id": project_id, "name": "V"}),
        ("project.save_as_template", {"id": project_id, "name": "From newer"}),
    ]
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        for index, (method, params) in enumerate(calls, start=1):
            response = _rpc(ws, method, params, index)
            assert response["error"]["code"] == -32602, (method, response)
            assert NEWER_MESSAGE in response["error"]["message"], method
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path) == before


def test_other_projects_stay_usable_next_to_a_newer_project(service):
    newer, *_ = _project_with_data(service)
    root = service.store.root
    _make_newer(root, newer.id)
    newer_before = _snapshot(root / newer.id)

    blank = service.create_project("Blank")
    module = service.create_module(blank.id, "Reqs", "R")
    req = service.add_requirement(blank.id, module.id, {"object_text": "fine"})
    from_template = service.create_project("RBC", template_id="builtin:rbc_template")

    reloaded = ProjectService(ProjectStore(root))
    assert reloaded.get_requirement(blank.id, module.id, req.id).attributes["object_text"] == "fine"
    assert [m.name for m in reloaded.list_modules(from_template.id)][0] == "StRS"
    summary = reloaded.save_project_as_template(blank.id, "From blank")
    assert reloaded.create_project("Copy", template_id=summary["id"]).id
    # The listing shows the newer project as a read-only stub instead of failing.
    listed = {p.id: p for p in reloaded.list_projects()}
    assert NEWER_MESSAGE in listed[newer.id].error
    assert not hasattr(listed[blank.id], "error")
    assert _snapshot(root / newer.id) == newer_before


def test_newer_project_is_never_written_through_parent_references(service):
    newer = service.create_project("Newer")
    other = service.create_project("Other")
    root = service.store.root
    _make_newer(root, newer.id)
    before = _snapshot(root / newer.id)

    with pytest.raises(NewerFormatError):
        service.set_project_parent(newer.id, other.id)
    # Referencing it as a parent only writes the child (whether or not the
    # reference is accepted, the newer project's files stay untouched).
    for attempt in (
        lambda: service.create_project("Child", parent_project_id=newer.id),
        lambda: service.create_project("RBC child", template_id="builtin:rbc_template", parent_project_id=newer.id),
        lambda: service.set_project_parent(other.id, newer.id),
    ):
        try:
            attempt()
        except NewerFormatError:
            pass
    assert _snapshot(root / newer.id) == before


# --- module order ------------------------------------------------------------------


def _project_json(service, project_id) -> dict:
    return json.loads((service.store.root / project_id / "project.json").read_text())


def test_module_removed_by_hand_is_dropped_from_order_on_next_create(service):
    project = service.create_project("P")
    for name in ("A", "B", "C"):
        service.create_module(project.id, name, "R")
    service.reorder_module(project.id, "c", "up")
    service.reorder_module(project.id, "c", "up")  # c, a, b
    shutil.rmtree(service.store.root / project.id / "modules" / "a")

    assert [m.id for m in service.list_modules(project.id)] == ["c", "b"]
    assert _project_json(service, project.id)["module_order"] == ["c", "a", "b"]  # not rewritten by reading

    service.create_module(project.id, "D", "R")
    assert _project_json(service, project.id)["module_order"] == ["c", "b", "d"]
    # A module recreated under the removed id is new: it goes last.
    service.create_module(project.id, "A", "R")
    fresh = ProjectService(ProjectStore(service.store.root))
    assert [m.id for m in fresh.list_modules(project.id)] == ["c", "b", "d", "a"]


def test_module_added_by_hand_is_appended_and_reorderable(service):
    project = service.create_project("P")
    for name in ("B", "C"):
        service.create_module(project.id, name, "R")
    service.reorder_module(project.id, "c", "up")  # c, b
    hand = service.store.root / project.id / "modules" / "a"
    (hand / "requirements").mkdir(parents=True)
    (hand / "module.json").write_text(json.dumps({"id": "a", "name": "A", "prefix": "R", "description": ""}))

    assert [m.id for m in service.list_modules(project.id)] == ["c", "b", "a"]
    service.reorder_module(project.id, "a", "up")
    assert _project_json(service, project.id)["module_order"] == ["c", "a", "b"]


def test_project_without_modules_has_no_module_order_key(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "owner", "Owner")
    service.create_view(project.id, "Main", [])
    text = (service.store.root / project.id / "project.json").read_text()
    data = json.loads(text)
    assert "module_order" not in data
    assert list(data)[0] == "format_version"
    assert text == json.dumps(data, indent=2) + "\n"


def test_module_order_stays_last_and_formatted_across_unrelated_saves(service):
    project = service.create_project("P")
    for name in ("A", "B"):
        service.create_module(project.id, name, "R")
    service.reorder_module(project.id, "b", "up")
    service.add_attribute_definition(project.id, "owner", "Owner")
    service.create_view(project.id, "Main", ["owner"])
    path = service.store.root / project.id / "project.json"
    text = path.read_text()
    data = json.loads(text)
    assert list(data)[0] == "format_version"
    assert list(data)[-1] == "module_order"
    assert data["module_order"] == ["b", "a"]
    assert text == json.dumps(data, indent=2) + "\n"


def test_exports_follow_module_order(service):
    project = service.create_project("P")
    for name in ("A", "B"):
        module = service.create_module(project.id, name, name)
        service.add_requirement(project.id, module.id, {"object_text": f"in {name}"})
    service.reorder_module(project.id, "b", "up")
    rows = service.export_csv(project.id).splitlines()[1:]
    assert [row.split(",")[1] for row in rows] == ["b", "a"]


def test_module_order_via_rest_create_and_template(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    for name in ("Zeta", "Alpha"):
        assert client.post(f"/projects/{project_id}/modules", json={"name": name, "prefix": "R"}).status_code == 200
    assert [m["id"] for m in client.get(f"/projects/{project_id}/modules").json()] == ["zeta", "alpha"]

    rbc = client.post("/projects", json={"name": "RBC", "template_id": "builtin:rbc_template"}).json()["id"]
    names = [m["name"] for m in client.get(f"/projects/{rbc}/modules").json()]
    assert names == ["StRS", "SyRS", "SwRS", "SysTest", "SwTest"]


# --- module reorder: REST / WebSocket parity ----------------------------------------


def test_module_reorder_errors_have_rest_ws_parity_and_never_broadcast(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    for name in ("A", "B"):
        client.post(f"/projects/{project_id}/modules", json={"name": name, "prefix": "R"})
    before = _snapshot(tmp_path / project_id)

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        assert client.post("/projects/ghost/modules/a/reorder", json={"direction": "up"}).status_code == 404
        assert client.post(f"/projects/{project_id}/modules/ghost/reorder", json={"direction": "up"}).status_code == 404
        assert client.post(f"/projects/{project_id}/modules/a/reorder", json={}).status_code == 422
        assert client.post(f"/projects/{project_id}/modules/a/reorder", json={"direction": "UP"}).status_code == 422

        ghost_project = _rpc(ws, "module.reorder", {"project_id": "ghost", "id": "a", "direction": "up"}, 1)
        assert ghost_project["error"]["code"] == -32001
        assert "ghost" in ghost_project["error"]["message"]
        ghost_module = _rpc(ws, "module.reorder", {"project_id": project_id, "id": "ghost", "direction": "up"}, 2)
        assert ghost_module["error"]["code"] == -32001
        missing = _rpc(ws, "module.reorder", {"project_id": project_id, "id": "a"}, 3)
        assert missing["error"]["code"] == -32602
        upper = _rpc(ws, "module.reorder", {"project_id": project_id, "id": "a", "direction": "UP"}, 4)
        assert upper["error"] == {"code": -32602, "message": "Invalid direction 'UP': must be 'up' or 'down'"}
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path / project_id) == before


def test_boundary_module_reorder_via_rest_and_ws_writes_nothing(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    for name in ("A", "B"):
        client.post(f"/projects/{project_id}/modules", json={"name": name, "prefix": "R"})
    path = tmp_path / project_id / "project.json"
    before = path.read_bytes()

    resp = client.post(f"/projects/{project_id}/modules/a/reorder", json={"direction": "up"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "a"
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "module.reorder", {"project_id": project_id, "id": "b", "direction": "down"})
        assert response["result"]["id"] == "b"
    assert path.read_bytes() == before


def test_module_reorder_via_ws_persists_and_sender_gets_no_broadcast(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    for name in ("A", "B"):
        client.post(f"/projects/{project_id}/modules", json={"name": name, "prefix": "R"})
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        response = _rpc(ws, "module.reorder", {"project_id": project_id, "id": "b", "direction": "up"})
        assert response["result"] == client.get(f"/projects/{project_id}/modules/b").json()
        assert json.loads(observer.receive_text()) == {"event": "changed", "project_id": project_id}
        _assert_no_broadcast(ws)
    fresh = ProjectService(ProjectStore(tmp_path))
    assert [m.id for m in fresh.list_modules(project_id)] == ["b", "a"]


# --- view names: REST / WebSocket parity -----------------------------------------------


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_blank_view_name_is_400_on_rest_and_32602_on_ws_without_broadcast(client, tmp_path, name):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    before = _snapshot(tmp_path / project_id)
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.post(f"/projects/{project_id}/views", json={"name": name})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "View name must not be empty"
        response = _rpc(ws, "view.create", {"project_id": project_id, "name": name})
        assert response["error"] == {"code": -32602, "message": "View name must not be empty"}
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path / project_id) == before


@pytest.mark.parametrize("name", [None, 5, ["Main"]])
def test_non_string_view_name_on_ws_is_32602(client, name):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "view.create", {"project_id": project_id, "name": name})
        assert response["error"] == {"code": -32602, "message": "View name must not be empty"}
    assert client.get(f"/projects/{project_id}/views").json() == []


@pytest.mark.parametrize("duplicate", ["main", "  Main  ", "MAIN\t", "mAiN"])
def test_duplicate_view_name_rest_ws_parity_and_no_broadcast(client, tmp_path, duplicate):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    assert client.post(f"/projects/{project_id}/views", json={"name": "Main"}).status_code == 200
    before = _snapshot(tmp_path / project_id)
    expected = f"View name '{duplicate.strip()}' is already used by view 'main'"
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.post(f"/projects/{project_id}/views", json={"name": duplicate})
        assert resp.status_code == 400
        assert resp.json()["detail"] == expected
        response = _rpc(ws, "view.create", {"project_id": project_id, "name": duplicate})
        assert response["error"] == {"code": -32602, "message": expected}
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path / project_id) == before


def test_view_names_are_stored_trimmed_via_rest_and_ws(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.post(f"/projects/{project_id}/views", json={"name": "  Compact  "})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Compact"
        assert json.loads(observer.receive_text()) == {"event": "changed", "project_id": project_id}
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": project_id}
        response = _rpc(ws, "view.create", {"project_id": project_id, "name": "\tWide "})
        assert response["result"]["name"] == "Wide"
        assert json.loads(observer.receive_text()) == {"event": "changed", "project_id": project_id}
    fresh = ProjectService(ProjectStore(tmp_path))
    assert [v.name for v in fresh.list_views(project_id)] == ["Compact", "Wide"]
    stored = json.loads((tmp_path / project_id / "project.json").read_text())["views"]
    assert [v["name"] for v in stored] == ["Compact", "Wide"]


def test_hand_edited_view_names_are_compared_trimmed_and_existing_duplicates_kept(service):
    project = service.create_project("P")
    path = service.store.root / project.id / "project.json"
    _set_raw(
        path,
        views=[
            {"id": "main", "name": " Main ", "attribute_ids": []},
            {"id": "main-2", "name": "MAIN", "attribute_ids": []},
        ],
    )
    with pytest.raises(ValueError, match="already used by view 'main'"):
        service.create_view(project.id, "main", [])
    view = service.create_view(project.id, "Other", [])
    assert view.name == "Other"
    reloaded = ProjectService(ProjectStore(service.store.root))
    assert [v.name for v in reloaded.list_views(project.id)] == [" Main ", "MAIN", "Other"]


# --- templates with a UTF-8 BOM ------------------------------------------------------


BOM_TEMPLATE = {
    "format_version": 1,
    "id": "bom",
    "name": "Bom Ü “quoted”",
    "modules": [
        {"key": "b", "name": "Beta", "prefix": "B"},
        {"key": "a", "name": "Alpha", "prefix": "A"},
    ],
}


def _bom_bytes(data: dict) -> bytes:
    return b"\xef\xbb\xbf" + json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def test_user_template_with_bom_via_rest(client, tmp_path):
    templates_dir = tmp_path / "_templates"
    templates_dir.mkdir()
    (templates_dir / "bom.json").write_bytes(_bom_bytes(BOM_TEMPLATE))
    before = (templates_dir / "bom.json").read_bytes()

    entry = next(t for t in client.get("/templates").json() if t["id"] == "user:bom")
    assert entry["error"] is None
    assert entry["name"] == BOM_TEMPLATE["name"]

    resp = client.post("/projects", json={"name": "From bom", "template_id": "user:bom"})
    assert resp.status_code == 200, resp.text
    modules = client.get(f"/projects/{resp.json()['id']}/modules").json()
    assert [m["name"] for m in modules] == ["Beta", "Alpha"]  # the template's declared order
    assert (templates_dir / "bom.json").read_bytes() == before


def test_builtin_template_with_bom_is_accepted(tmp_path):
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    (builtin / "bom.json").write_bytes(_bom_bytes(BOM_TEMPLATE))
    assert not read_builtin_template_text("bom", builtin).startswith("﻿")

    service = ProjectService(ProjectStore(tmp_path / "data"), builtin_templates_dir=builtin)
    entry = next(t for t in service.list_templates() if t["id"] == "builtin:bom")
    assert entry["error"] is None
    project = service.create_project("P", template_id="builtin:bom")
    assert [m.name for m in service.list_modules(project.id)] == ["Beta", "Alpha"]


def test_parse_template_text_strips_only_one_leading_bom():
    text = json.dumps(BOM_TEMPLATE)
    assert parse_template_text("﻿" + text, "user:bom").name == BOM_TEMPLATE["name"]
    with pytest.raises(ValueError):
        parse_template_text("﻿﻿" + text, "user:bom")
    with pytest.raises(ValueError):
        parse_template_text(" ﻿" + text, "user:bom")


def test_bom_template_with_invalid_content_still_reports_the_error(client, tmp_path):
    templates_dir = tmp_path / "_templates"
    templates_dir.mkdir()
    (templates_dir / "broken.json").write_bytes(b"\xef\xbb\xbf{not json")
    entry = next(t for t in client.get("/templates").json() if t["id"] == "user:broken")
    assert entry["error"]
    resp = client.post("/projects", json={"name": "X", "template_id": "user:broken"})
    assert resp.status_code == 400
    assert client.get("/projects").json() == []


# --- unversioned (v1) projects: byte-for-byte reads, first save -------------------------


def test_unversioned_project_reads_via_rest_and_ws_leave_files_unchanged(client, tmp_path):
    project_id, module_id, other_id, req_id, _, _ = _rest_project(client)
    assert client.post(f"/projects/{project_id}/views", json={"name": "Main"}).status_code == 200
    path = tmp_path / project_id / "project.json"
    # Older data: no format_version and no module_order.
    _delete_raw(path, "format_version", "module_order")
    legacy_keys = list(json.loads(path.read_text()))
    before = _snapshot(tmp_path)

    base = f"/projects/{project_id}"
    for url in (
        "/projects",
        base,
        f"{base}/modules",
        f"{base}/modules/{module_id}",
        f"{base}/modules/{module_id}/requirements",
        f"{base}/modules/{module_id}/requirements/{req_id}",
        f"{base}/requirements/{req_id}",
        f"{base}/link-configurations",
        f"{base}/views",
        f"{base}/export/reqif",
        f"{base}/export/csv",
        f"{base}/export/xlsx",
        f"{base}/export/docx",
        "/templates",
    ):
        assert client.get(url).status_code == 200, url
    # A boundary reorder is a no-op and must not upgrade the file either.
    assert client.post(f"{base}/modules/{module_id}/reorder", json={"direction": "up"}).status_code == 200
    with client.websocket_connect("/ws") as ws:
        for index, (method, params) in enumerate(
            (
                ("project.list", {}),
                ("project.get", {"id": project_id}),
                ("module.list", {"project_id": project_id}),
                ("module.get", {"project_id": project_id, "id": other_id}),
                ("requirement.list", {"project_id": project_id, "module_id": module_id}),
                ("requirement.get", {"project_id": project_id, "module_id": module_id, "id": req_id}),
                ("link_configuration.list", {"project_id": project_id}),
                ("view.list", {"project_id": project_id}),
            ),
            start=1,
        ):
            assert "result" in _rpc(ws, method, params, index), method
    assert _snapshot(tmp_path) == before

    # The first real save writes format_version as the first key, keeps the
    # other keys in their order and still adds no module_order.
    resp = client.post(f"{base}/attributes", json={"id": "owner", "name": "Owner"})
    assert resp.status_code == 200, resp.text
    text = path.read_text()
    data = json.loads(text)
    assert list(data) == ["format_version", *legacy_keys]
    assert data["format_version"] == PROJECT_FORMAT.current
    assert text == json.dumps(data, indent=2) + "\n"
    # Module, requirement and link-pair files carry no version of their own.
    for file in (tmp_path / project_id).rglob("*.json"):
        if file != path:
            assert "format_version" not in json.loads(file.read_text()), file


def test_reorder_of_unversioned_project_writes_full_order_and_version(service):
    project = service.create_project("P")
    for name in ("C", "A", "B"):
        service.create_module(project.id, name, "R")
    path = service.store.root / project.id / "project.json"
    _delete_raw(path, "format_version", "module_order")
    assert [m.id for m in service.list_modules(project.id)] == ["a", "b", "c"]

    service.reorder_module(project.id, "c", "up")
    text = path.read_text()
    data = json.loads(text)
    assert list(data)[0] == "format_version"
    assert list(data)[-1] == "module_order"
    assert data["module_order"] == ["a", "c", "b"]
    assert text == json.dumps(data, indent=2) + "\n"
