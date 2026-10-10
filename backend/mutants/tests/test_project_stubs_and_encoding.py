"""Project list stubs for unloadable projects (written by a newer ReqManTool,
malformed project.json) and ``utf-8-sig`` reads of every data file
(backlog items, 2026-10-04).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json
import os
import threading

import pytest
from fastapi.testclient import TestClient

from reqman.formats import PROJECT_FORMAT, NewerFormatError
from reqman.service import ProjectService, ProjectStub, _unique_slug
from reqman.store import ProjectStore

NEWER = PROJECT_FORMAT.current + 1
NEWER_MESSAGE = "written by a newer ReqManTool"
BOM = b"\xef\xbb\xbf"


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


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


def _set_raw(path: Path, **changes) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# --- project list stubs ----------------------------------------------------------


def test_newer_project_is_a_top_level_stub_and_its_child_stays_under_it(service):
    grand = service.create_project("Grand")
    newer = service.create_project("Newer", parent_project_id=grand.id)
    child = service.create_project("Child", parent_project_id=newer.id)
    _set_raw(service.store.root / newer.id / "project.json", format_version=NEWER)
    before = _snapshot(service.store.root)

    listed = {p.id: p for p in service.list_projects()}
    stub = listed[newer.id]
    assert isinstance(stub, ProjectStub)
    assert stub.name == "Newer"
    assert NEWER_MESSAGE in stub.error
    # Its stored parent comes from a file this tool cannot read: listed top level.
    assert stub.parent_project_id is None
    assert listed[child.id].parent_project_id == newer.id
    assert stub.to_dict() == {"id": newer.id, "name": "Newer", "error": stub.error}

    with pytest.raises(NewerFormatError, match=NEWER_MESSAGE):
        service.get_project(newer.id)
    with pytest.raises(NewerFormatError, match=NEWER_MESSAGE):
        service.set_project_parent(child.id, newer.id)
    assert _snapshot(service.store.root) == before


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"{not json", "not valid JSON"),
        (b"[1, 2]\n", "expected a JSON object, got list"),
        (b'{"id": "x", "name": "\xff"}\n', "not valid UTF-8"),
        (b'{"format_version": "1", "id": "x", "name": "X"}\n', "invalid format_version"),
        # Version 1 objects of the wrong shape. These used to fail only when
        # the project itself was loaded (earlier implementer decision 2), so a
        # stub's module/requirement files could still be read and written.
        # Every stub is now read-only: they block all of the project's files too.
        (b'{"name": "No id"}\n', "missing key 'id'"),
        (b'{"id": "x", "name": "X", "attributes": 5}\n', "invalid project.json"),
    ],
)
def test_unloadable_project_json_is_a_stub_and_never_treated_as_version_one(service, raw, expected):
    good = service.create_project("Good")
    bad = service.create_project("Bad")
    module = service.create_module(bad.id, "Reqs", "R")
    req = service.add_requirement(bad.id, module.id, {"object_text": "x"})
    (service.store.root / bad.id / "project.json").write_bytes(raw)
    before = _snapshot(service.store.root)

    listed = service.list_projects()
    assert [p.id for p in listed] == [bad.id, good.id]
    assert listed[1] == service.get_project(good.id)
    assert isinstance(listed[0], ProjectStub) and expected in listed[0].error
    assert f"Project '{bad.id}'" in listed[0].error

    # The project cannot be loaded, so its other files are neither read nor written.
    calls = [
        lambda: service.get_project(bad.id),
        lambda: service.update_requirement(bad.id, module.id, req.id, {"object_text": "y"}),
        lambda: service.get_module(bad.id, module.id),
        lambda: service.get_requirement(bad.id, module.id, req.id),
        lambda: service.store.save_module(bad.id, module),
        lambda: service.move_requirement(bad.id, module.id, req.id, None),
        lambda: service.reorder_requirement(bad.id, module.id, req.id, "down"),
        lambda: service.remove_requirement(bad.id, module.id, req.id),
    ]
    for call in calls:
        with pytest.raises(ValueError, match=expected) as excinfo:
            call()
        assert not isinstance(excinfo.value, NewerFormatError)
    assert _snapshot(service.store.root) == before


def test_stub_name_falls_back_to_the_id(service):
    project = service.create_project("Named")
    (service.store.root / project.id / "project.json").write_text('{"name": 5}\n', encoding="utf-8")
    [stub] = service.list_projects()
    assert stub.name == project.id


def test_project_list_with_stub_rest_ws_parity(client, tmp_path):
    good_id = client.post("/projects", json={"name": "Good"}).json()["id"]
    newer_id = client.post("/projects", json={"name": "Newer"}).json()["id"]
    broken_id = client.post("/projects", json={"name": "Broken"}).json()["id"]
    _set_raw(tmp_path / newer_id / "project.json", format_version=NEWER)
    (tmp_path / broken_id / "project.json").write_text("{oops", encoding="utf-8")
    before = _snapshot(tmp_path)

    resp = client.get("/projects")
    assert resp.status_code == 200, resp.text
    rest_list = resp.json()
    by_id = {p["id"]: p for p in rest_list}
    assert "error" not in by_id[good_id]
    assert NEWER_MESSAGE in by_id[newer_id]["error"]
    assert "not valid JSON" in by_id[broken_id]["error"]

    assert client.get(f"/projects/{newer_id}").status_code == 409
    resp = client.get(f"/projects/{broken_id}")
    assert resp.status_code == 400 and "not valid JSON" in resp.json()["detail"]
    with client.websocket_connect("/ws") as ws:
        assert _rpc(ws, "project.list", {})["result"] == rest_list
        response = _rpc(ws, "project.get", {"id": newer_id}, 2)
        assert response["error"]["code"] == -32602 and NEWER_MESSAGE in response["error"]["message"]
        response = _rpc(ws, "project.get", {"id": broken_id}, 3)
        assert response["error"]["code"] == -32602 and "not valid JSON" in response["error"]["message"]
    assert _snapshot(tmp_path) == before


# --- utf-8-sig reads -------------------------------------------------------------


def _add_bom(path: Path) -> None:
    path.write_bytes(BOM + path.read_bytes())


def test_data_files_with_a_utf8_bom_are_read_and_rewritten_without_one(service):
    project = service.create_project("Zażółć")
    module = service.create_module(project.id, "Reqs", "R")
    other = service.create_module(project.id, "Tests", "T")
    req = service.add_requirement(project.id, module.id, {"object_text": "ünïcode"})
    target = service.add_requirement(project.id, other.id, {"object_text": "t"})
    config = service.create_link_configuration(project.id, "Verifies", module.id, other.id)
    service.link_requirements(project.id, config.id, req.id, target.id)
    root = service.store.root / project.id
    files = [
        root / "project.json",
        root / "modules" / module.id / "module.json",
        root / "modules" / module.id / "requirements" / f"{req.id}.json",
        root / "links" / f"{config.id}.json",
    ]
    for path in files:
        _add_bom(path)

    fresh = ProjectService(ProjectStore(service.store.root))
    assert fresh.get_project(project.id).name == "Zażółć"
    assert [p.id for p in fresh.list_projects()] == [project.id]
    assert fresh.get_module(project.id, module.id).name == "Reqs"
    assert fresh.get_requirement(project.id, module.id, req.id).attributes["object_text"] == "ünïcode"
    assert fresh.store.load_link_pairs(project.id, config.id)[0].target_id == target.id

    # Saving writes plain UTF-8 again: no BOM, two-space indent, trailing newline.
    fresh.update_requirement(project.id, module.id, req.id, {"object_text": "again"})
    fresh.add_attribute_definition(project.id, "owner", "Owner")
    fresh.unlink_requirements(project.id, config.id, req.id, target.id)
    for path in (files[0], files[2], files[3]):
        raw = path.read_bytes()
        assert not raw.startswith(BOM), path
        text = raw.decode("utf-8")
        assert text == json.dumps(json.loads(text), indent=2) + "\n"


# --- more stub sources: unreadable files -----------------------------------------


def _assert_no_broadcast(ws, request_id=999):
    """The next message on ``ws`` is the reply to a harmless request, not a queued event."""
    response = _rpc(ws, "template.list", {}, request_id)
    assert response.get("id") == request_id, response


def _make_unreadable(path: Path) -> None:
    """Make ``path`` unreadable to this process, or skip the test where that is impossible."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root: chmod does not stop reads")
    path.chmod(0)
    try:
        path.read_bytes()
    except PermissionError:
        return
    path.chmod(0o644)
    pytest.skip("chmod has no effect on this platform")


def test_project_json_the_process_cannot_read_is_a_stub_named_by_its_id(service):
    good = service.create_project("Good")
    locked = service.create_project("Locked name")
    path = service.store.root / locked.id / "project.json"
    _make_unreadable(path)
    try:
        listed = service.list_projects()
        assert [p.id for p in listed] == [good.id, locked.id]
        assert listed[0] == service.get_project(good.id)
        stub = listed[1]
        assert isinstance(stub, ProjectStub)
        # The stored name cannot be read either: the id stands in for it.
        assert stub.name == locked.id
        assert stub.error and stub.parent_project_id is None
        _assert_unreadable_error(stub.error, locked.id, service.store.root)
        with pytest.raises(ValueError, match="unreadable project.json"):
            service.get_project(locked.id)
    finally:
        path.chmod(0o644)
    # Nothing was rewritten: once readable again it loads as before.
    assert service.get_project(locked.id).name == "Locked name"


def test_project_json_that_is_a_directory_is_a_stub(service):
    good = service.create_project("Good")
    odd = service.create_project("Odd")
    path = service.store.root / odd.id / "project.json"
    path.unlink()
    path.mkdir()

    listed = {p.id: p for p in service.list_projects()}
    assert listed[good.id] == service.get_project(good.id)
    stub = listed[odd.id]
    assert isinstance(stub, ProjectStub) and stub.name == odd.id and stub.error
    assert path.is_dir()


def _assert_unreadable_error(message: str, project_id: str, root: Path) -> None:
    """An unreadable project.json is reported by project id, never by its absolute path."""
    assert f"Project '{project_id}' has an unreadable project.json" in message, message
    assert str(root) not in message, message


@pytest.mark.parametrize("kind", ["directory", "unreadable_file"])
def test_opening_a_project_whose_project_json_cannot_be_read_is_400_and_32602(client, tmp_path, kind):
    lenient = TestClient(client.app, raise_server_exceptions=False)
    project_id = lenient.post("/projects", json={"name": "Odd"}).json()["id"]
    module_id = lenient.post(f"/projects/{project_id}/modules", json={"name": "Reqs", "prefix": "R"}).json()["id"]
    path = tmp_path / project_id / "project.json"
    if kind == "directory":
        path.unlink()
        path.mkdir()
    else:
        _make_unreadable(path)
    try:
        before = _snapshot(tmp_path / project_id) if kind == "directory" else None
        [stub] = lenient.get("/projects").json()
        assert stub["id"] == project_id and stub["name"] == project_id
        _assert_unreadable_error(stub["error"], project_id, tmp_path)
        for resp in (
            lenient.get(f"/projects/{project_id}"),
            lenient.get(f"/projects/{project_id}/modules/{module_id}"),
            lenient.post(f"/projects/{project_id}/modules", json={"name": "N", "prefix": "N"}),
        ):
            assert resp.status_code == 400, resp.text
            _assert_unreadable_error(resp.json()["detail"], project_id, tmp_path)
        with lenient.websocket_connect("/ws") as ws, lenient.websocket_connect("/ws") as observer:
            for index, (method, params) in enumerate(
                [
                    ("project.get", {"id": project_id}),
                    ("module.get", {"project_id": project_id, "id": module_id}),
                    ("module.create", {"project_id": project_id, "name": "N", "prefix": "N"}),
                ],
                start=1,
            ):
                response = _rpc(ws, method, params, index)
                assert response["error"]["code"] == -32602, response
                _assert_unreadable_error(response["error"]["message"], project_id, tmp_path)
            _assert_no_broadcast(observer)
        if before is not None:
            assert _snapshot(tmp_path / project_id) == before
    finally:
        if path.is_file():
            path.chmod(0o644)


def test_project_folder_the_process_cannot_search_is_listed_as_a_stub(client, tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root: chmod does not stop reads")
    good_id = client.post("/projects", json={"name": "Good"}).json()["id"]
    locked_id = client.post("/projects", json={"name": "Locked"}).json()["id"]
    module_id = client.post(f"/projects/{locked_id}/modules", json={"name": "Reqs", "prefix": "R"}).json()["id"]
    folder = tmp_path / locked_id
    folder.chmod(0)
    try:
        try:
            (folder / "project.json").stat()
        except PermissionError:
            pass
        else:
            pytest.skip("chmod has no effect on this platform")

        listed = {p["id"]: p for p in client.get("/projects").json()}
        assert set(listed) == {good_id, locked_id}
        assert "error" not in listed[good_id]
        assert listed[locked_id]["name"] == locked_id
        _assert_unreadable_error(listed[locked_id]["error"], locked_id, tmp_path)
        for resp in (
            client.get(f"/projects/{locked_id}"),
            client.get(f"/projects/{locked_id}/modules"),
            client.post(f"/projects/{locked_id}/modules", json={"name": "N", "prefix": "N"}),
            # Its modules report the stub error too, not "not found".
            client.get(f"/projects/{locked_id}/modules/{module_id}"),
        ):
            assert resp.status_code == 400, resp.text
            _assert_unreadable_error(resp.json()["detail"], locked_id, tmp_path)
        with client.websocket_connect("/ws") as ws:
            assert {p["id"]: p for p in _rpc(ws, "project.list", {})["result"]} == listed
            response = _rpc(ws, "project.get", {"id": locked_id}, 2)
            assert response["error"]["code"] == -32602, response
    finally:
        folder.chmod(0o755)
    assert client.get(f"/projects/{locked_id}").json()["name"] == "Locked"


def test_project_folder_removed_while_listing_is_skipped(service, monkeypatch):
    kept = service.create_project("Kept")
    listed_ids = service.store.list_project_ids()
    # A folder that was listed but is gone by the time it is loaded.
    monkeypatch.setattr(service.store, "list_project_ids", lambda: ["gone", *listed_ids])
    assert [p.id for p in service.list_projects()] == [kept.id]


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({"name": "  Padded  "}, "Padded"),
        ({"name": "   "}, None),
        ({"name": ""}, None),
        ({"name": None}, None),
        ({"name": ["x"]}, None),
        ({"name": "\ud800"}, None),  # a lone surrogate escape cannot be listed
        ({"name": "Zażółć \"q\" <&>"}, "Zażółć \"q\" <&>"),
    ],
)
def test_stub_name_is_the_stored_name_if_usable(service, stored, expected):
    project = service.create_project("Original")
    # A newer project.json: the name is read best effort from the raw file.
    raw = {"format_version": NEWER, "id": project.id, **stored}
    (service.store.root / project.id / "project.json").write_text(json.dumps(raw) + "\n", encoding="utf-8")
    [stub] = service.list_projects()
    assert isinstance(stub, ProjectStub)
    assert stub.name == (expected if expected is not None else project.id)


def test_stub_name_of_a_non_utf8_file_is_the_id(service):
    project = service.create_project("Original")
    (service.store.root / project.id / "project.json").write_bytes(b'{"name": "Caf\xe9"}\n')
    [stub] = service.list_projects()
    assert stub.name == project.id and "not valid UTF-8" in stub.error


# --- stubs and the subproject tree ---------------------------------------------------


@pytest.mark.parametrize("kind", ["newer", "missing_id"])
def test_stub_breaks_a_cycle_through_its_own_parent_and_keeps_its_subprojects(service, kind):
    stub = service.create_project("Stub")
    child = service.create_project("Child", parent_project_id=stub.id)
    grandchild = service.create_project("Grandchild", parent_project_id=child.id)
    loop_a = service.create_project("Loop A", parent_project_id=grandchild.id)
    loop_b = service.create_project("Loop B", parent_project_id=loop_a.id)
    # Hand-edited: the stub's stored parent is its own grandchild, and loop A/B form a cycle.
    path = service.store.root / stub.id / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["parent_project_id"] = grandchild.id
    if kind == "newer":
        data["format_version"] = NEWER
    else:
        del data["id"]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _set_raw(service.store.root / loop_a.id / "project.json", parent_project_id=loop_b.id)
    before = _snapshot(service.store.root)

    parents = {p.id: p.parent_project_id for p in service.list_projects()}
    assert parents == {
        stub.id: None,
        child.id: stub.id,
        grandchild.id: child.id,
        loop_a.id: None,
        loop_b.id: None,
    }
    assert _snapshot(service.store.root) == before


def test_stub_parent_is_omitted_over_rest_and_ws_and_its_children_point_to_it(client, tmp_path):
    top = client.post("/projects", json={"name": "Top"}).json()["id"]
    stub_id = client.post("/projects", json={"name": "Stub", "parent_project_id": top}).json()["id"]
    child = client.post("/projects", json={"name": "Child", "parent_project_id": stub_id}).json()["id"]
    (tmp_path / stub_id / "project.json").write_text(
        json.dumps({"id": stub_id, "name": "Stub", "parent_project_id": top, "attributes": 5}) + "\n",
        encoding="utf-8",
    )

    listed = {p["id"]: p for p in client.get("/projects").json()}
    assert set(listed[stub_id]) == {"id", "name", "error"}
    assert listed[stub_id]["name"] == "Stub"
    assert listed[child]["parent_project_id"] == stub_id
    assert "parent_project_id" not in listed[top]
    with client.websocket_connect("/ws") as ws:
        assert {p["id"]: p for p in _rpc(ws, "project.list", {})["result"]} == listed


# --- create / move around stubs (pinned behavior) --------------------------------------


def test_create_and_move_under_an_invalid_json_stub_are_allowed(client, tmp_path):
    stub_id = client.post("/projects", json={"name": "Broken"}).json()["id"]
    mover = client.post("/projects", json={"name": "Mover"}).json()["id"]
    stub_file = tmp_path / stub_id / "project.json"
    stub_file.write_bytes(b"{oops")

    resp = client.post("/projects", json={"name": "Created", "parent_project_id": stub_id})
    assert resp.status_code == 200, resp.text
    created = resp.json()["id"]
    resp = client.put(f"/projects/{mover}/parent", json={"parent_project_id": stub_id})
    assert resp.status_code == 200, resp.text
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "project.create", {"name": "Via WS", "parent_project_id": stub_id})
        assert response["result"]["parent_project_id"] == stub_id
        via_ws = response["result"]["id"]

    assert stub_file.read_bytes() == b"{oops"
    listed = {p["id"]: p for p in client.get("/projects").json()}
    assert "not valid JSON" in listed[stub_id]["error"]
    for project_id in (created, mover, via_ws):
        assert listed[project_id]["parent_project_id"] == stub_id


def test_create_and_move_under_a_newer_stub_are_409_and_32602_without_writes(client, tmp_path):
    stub_id = client.post("/projects", json={"name": "Newer"}).json()["id"]
    mover = client.post("/projects", json={"name": "Mover"}).json()["id"]
    _set_raw(tmp_path / stub_id / "project.json", format_version=NEWER)
    before = _snapshot(tmp_path)

    resp = client.post("/projects", json={"name": "Created", "parent_project_id": stub_id})
    assert resp.status_code == 409 and NEWER_MESSAGE in resp.json()["detail"]
    resp = client.put(f"/projects/{mover}/parent", json={"parent_project_id": stub_id})
    assert resp.status_code == 409 and NEWER_MESSAGE in resp.json()["detail"]
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        for index, (method, params) in enumerate(
            [
                ("project.create", {"name": "Via WS", "parent_project_id": stub_id}),
                ("project.set_parent", {"id": mover, "parent_project_id": stub_id}),
            ],
            start=1,
        ):
            response = _rpc(ws, method, params, index)
            assert response["error"]["code"] == -32602 and NEWER_MESSAGE in response["error"]["message"]
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path) == before


def test_moving_an_invalid_json_stub_itself_is_400_and_32602_without_writes(client, tmp_path):
    stub_id = client.post("/projects", json={"name": "Broken"}).json()["id"]
    other = client.post("/projects", json={"name": "Other"}).json()["id"]
    (tmp_path / stub_id / "project.json").write_bytes(b"[]\n")
    before = _snapshot(tmp_path)

    resp = client.put(f"/projects/{stub_id}/parent", json={"parent_project_id": other})
    assert resp.status_code == 400 and "expected a JSON object" in resp.json()["detail"]
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        response = _rpc(ws, "project.set_parent", {"id": stub_id, "parent_project_id": None})
        assert response["error"]["code"] == -32602 and "expected a JSON object" in response["error"]["message"]
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path) == before


# --- module / requirement calls on an invalid-JSON project -----------------------------


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


def test_ws_module_and_requirement_calls_on_an_invalid_json_project_are_32602_without_broadcast(client, tmp_path):
    project_id, module_id, other_id, req_id, target_id, config_id = _rest_project(client)
    (tmp_path / project_id / "project.json").write_text("{oops", encoding="utf-8")
    before = _snapshot(tmp_path)
    p, m = project_id, module_id
    calls = [
        ("module.list", {"project_id": p}),
        ("module.get", {"project_id": p, "id": m}),
        ("module.create", {"project_id": p, "name": "N", "prefix": "N"}),
        ("module.reorder", {"project_id": p, "id": m, "direction": "down"}),
        ("module.purge", {"project_id": p, "id": m}),
        ("project.purge", {"id": p}),
        ("requirement.list", {"project_id": p, "module_id": m}),
        ("requirement.get", {"project_id": p, "module_id": m, "id": req_id}),
        ("requirement.add", {"project_id": p, "module_id": m, "attributes": {"object_text": "n"}}),
        ("requirement.update", {"project_id": p, "module_id": m, "id": req_id, "attributes": {"object_text": "u"}}),
        ("requirement.remove", {"project_id": p, "module_id": m, "id": req_id}),
        ("requirement.move", {"project_id": p, "module_id": m, "id": req_id, "parent_id": None}),
        ("requirement.reorder", {"project_id": p, "module_id": m, "id": req_id, "direction": "down"}),
        ("requirement.copy", {"project_id": p, "module_id": m, "id": req_id}),
        ("requirement.import_csv", {"project_id": p, "module_id": m, "csv": "object_text\nz\n"}),
        ("requirement.link", {"project_id": p, "config_id": config_id, "source_id": req_id, "target_id": target_id}),
        ("requirement.unlink", {"project_id": p, "config_id": config_id, "source_id": req_id, "target_id": target_id}),
        ("link_configuration.list", {"project_id": p}),
        ("view.list", {"project_id": p}),
        ("attribute.add", {"project_id": p, "id": "owner", "name": "Owner"}),
    ]
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        for index, (method, params) in enumerate(calls, start=1):
            response = _rpc(ws, method, params, index)
            assert "error" in response, (method, response)
            assert response["error"]["code"] == -32602, (method, response)
            assert "not valid JSON" in response["error"]["message"], (method, response)
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path) == before


def test_rest_module_and_requirement_calls_on_an_invalid_json_project_are_400(client, tmp_path):
    project_id, module_id, other_id, req_id, target_id, config_id = _rest_project(client)
    (tmp_path / project_id / "project.json").write_bytes(b'{"id": "x", "name": "\xff"}')
    before = _snapshot(tmp_path)
    base = f"/projects/{project_id}"
    for resp in (
        client.get(f"{base}/modules"),
        client.get(f"{base}/modules/{module_id}"),
        client.get(f"{base}/modules/{module_id}/requirements"),
        client.get(f"{base}/modules/{module_id}/requirements/{req_id}"),
        client.get(f"{base}/requirements/{req_id}"),
        client.post(f"{base}/modules", json={"name": "N", "prefix": "N"}),
        client.post(f"{base}/modules/{module_id}/requirements", json={"attributes": {"object_text": "n"}}),
        client.post(f"{base}/links", json={"config_id": config_id, "source_id": req_id, "target_id": target_id}),
    ):
        assert resp.status_code == 400, (resp.request.url, resp.text)
        assert "not valid UTF-8" in resp.json()["detail"]
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda data: data.pop("id"), "missing key 'id'"),
        (lambda data: data.update(attributes=5), "invalid project.json"),
    ],
    ids=["missing_id", "attributes_not_a_list"],
)
def test_requirement_move_reorder_remove_on_a_wrong_shape_stub_are_rejected_without_writes(
    client, tmp_path, change, expected
):
    project_id, module_id, other_id, req_id, target_id, config_id = _rest_project(client)
    base = f"/projects/{project_id}/modules/{module_id}/requirements"
    child_id = client.post(base, json={"attributes": {"object_text": "child"}}).json()["id"]
    sibling_id = client.post(base, json={"attributes": {"object_text": "sibling"}}).json()["id"]
    assert client.put(f"{base}/{child_id}/parent", json={"parent_id": req_id}).status_code == 200
    path = tmp_path / project_id / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    [stub] = [p for p in client.get("/projects").json() if p["id"] == project_id]
    assert expected in stub["error"]

    p, m = project_id, module_id
    with client.websocket_connect("/ws") as observer:
        for resp in (
            client.put(f"{base}/{req_id}/parent", json={"parent_id": sibling_id}),
            client.put(f"{base}/{child_id}/parent", json={"parent_id": None}),
            client.post(f"{base}/{req_id}/reorder", json={"direction": "down"}),
            client.delete(f"{base}/{req_id}"),
        ):
            assert resp.status_code == 400, (resp.request.url, resp.text)
            assert expected in resp.json()["detail"]
        _assert_no_broadcast(observer)
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        for index, (method, params) in enumerate(
            [
                ("requirement.move", {"project_id": p, "module_id": m, "id": req_id, "parent_id": sibling_id}),
                ("requirement.move", {"project_id": p, "module_id": m, "id": child_id, "parent_id": None}),
                ("requirement.reorder", {"project_id": p, "module_id": m, "id": req_id, "direction": "down"}),
                ("requirement.remove", {"project_id": p, "module_id": m, "id": req_id}),
            ],
            start=1,
        ):
            response = _rpc(ws, method, params, index)
            assert response["error"]["code"] == -32602, (method, response)
            assert expected in response["error"]["message"], (method, response)
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path) == before


# --- purge on a stub or a missing project --------------------------------------------


@pytest.mark.parametrize(
    ("raw", "status", "expected"),
    [
        (None, 409, NEWER_MESSAGE),
        (b"{oops", 400, "not valid JSON"),
        (b'{"name": "No id"}\n', 400, "missing key 'id'"),
    ],
    ids=["newer", "invalid_json", "wrong_shape"],
)
def test_purging_a_stub_project_is_rejected_without_writes_or_broadcast(client, tmp_path, raw, status, expected):
    # A project whose modules hold no requirements: nothing would be deleted,
    # but the purge must still refuse a project it cannot load.
    project_id = client.post("/projects", json={"name": "Empty"}).json()["id"]
    client.post(f"/projects/{project_id}/modules", json={"name": "Reqs", "prefix": "R"})
    path = tmp_path / project_id / "project.json"
    if raw is None:
        _set_raw(path, format_version=NEWER)
    else:
        path.write_bytes(raw)
    before = _snapshot(tmp_path)

    with client.websocket_connect("/ws") as observer:
        resp = client.post(f"/projects/{project_id}/purge")
        assert resp.status_code == status and expected in resp.json()["detail"], resp.text
        _assert_no_broadcast(observer)
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        response = _rpc(ws, "project.purge", {"id": project_id})
        assert response["error"]["code"] == -32602 and expected in response["error"]["message"], response
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path) == before


def test_purging_a_missing_project_is_404_and_32001_without_broadcast(client, tmp_path):
    with client.websocket_connect("/ws") as observer:
        resp = client.post("/projects/no-such-project/purge")
        assert resp.status_code == 404 and "not found" in resp.json()["detail"], resp.text
        _assert_no_broadcast(observer)
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        response = _rpc(ws, "project.purge", {"id": "no-such-project"})
        assert response["error"]["code"] == -32001, response
        _assert_no_broadcast(observer)
    assert not (tmp_path / "no-such-project").exists()


# --- utf-8-sig reads through the list paths --------------------------------------------


def _bom_every_json_file(directory: Path) -> int:
    count = 0
    for path in directory.rglob("*.json"):
        _add_bom(path)
        count += 1
    return count


def _api_view(client, project_id, module_id, other_id, req_id):
    base = f"/projects/{project_id}"
    paths = [
        "/projects",
        base,
        f"{base}/modules",
        f"{base}/modules/{module_id}",
        f"{base}/modules/{module_id}/requirements",
        f"{base}/modules/{other_id}/requirements",
        f"{base}/modules/{module_id}/requirements/{req_id}",
        f"{base}/requirements/{req_id}",
        f"{base}/link-configurations",
        f"{base}/views",
    ]
    view = {}
    for path in paths:
        resp = client.get(path)
        assert resp.status_code == 200, (path, resp.text)
        view[path] = resp.json()
    return view


def test_every_list_path_reads_bom_prefixed_files_like_plain_ones(client, tmp_path):
    project_id, module_id, other_id, req_id, target_id, config_id = _rest_project(client)
    sub_id = client.post("/projects", json={"name": "Sub ünï", "parent_project_id": project_id}).json()["id"]
    client.post(f"/projects/{project_id}/views", json={"name": "V"})
    expected = _api_view(client, project_id, module_id, other_id, req_id)
    reqif_before = client.get(f"/projects/{project_id}/export/reqif")
    assert reqif_before.status_code == 200
    assert _bom_every_json_file(tmp_path / project_id) >= 6
    _bom_every_json_file(tmp_path / sub_id)

    import reqman.app as app_module

    importlib.reload(app_module)  # a fresh store: no cached format checks
    fresh = TestClient(app_module.app)
    assert _api_view(fresh, project_id, module_id, other_id, req_id) == expected
    # The ReqIF export reads the link-pair file: the link survives the BOM.
    reqif = fresh.get(f"/projects/{project_id}/export/reqif")
    assert reqif.status_code == 200
    assert reqif.text.count(target_id) == reqif_before.text.count(target_id) >= 1
    with fresh.websocket_connect("/ws") as ws:
        assert _rpc(ws, "project.list", {})["result"] == expected["/projects"]
        assert _rpc(ws, "module.list", {"project_id": project_id}, 2)["result"] == expected[
            f"/projects/{project_id}/modules"
        ]
        assert _rpc(ws, "requirement.list", {"project_id": project_id, "module_id": module_id}, 3)["result"] == (
            expected[f"/projects/{project_id}/modules/{module_id}/requirements"]
        )
    # Unlinking reads and rewrites the BOM-prefixed link-pair file.
    resp = fresh.request(
        "DELETE",
        f"/projects/{project_id}/links",
        json={"config_id": config_id, "source_id": req_id, "target_id": target_id},
    )
    assert resp.status_code == 200, resp.text
    link_file = tmp_path / project_id / "links" / f"{config_id}.json"
    raw = link_file.read_bytes()
    assert not raw.startswith(BOM)
    assert json.loads(raw)["pairs"] == []


def test_bom_prefixed_newer_project_is_still_newer(client, tmp_path):
    project_id, module_id, *_ = _rest_project(client)
    path = tmp_path / project_id / "project.json"
    _set_raw(path, format_version=NEWER, name="Neuer Größe")
    _add_bom(path)
    before = _snapshot(tmp_path)

    [stub] = client.get("/projects").json()
    assert stub["name"] == "Neuer Größe"  # the name is read past the BOM
    assert NEWER_MESSAGE in stub["error"]
    for resp in (
        client.get(f"/projects/{project_id}"),
        client.get(f"/projects/{project_id}/modules/{module_id}"),
        client.post(f"/projects/{project_id}/modules", json={"name": "N", "prefix": "N"}),
    ):
        assert resp.status_code == 409, resp.text
        assert NEWER_MESSAGE in resp.json()["detail"]
    service = ProjectService(ProjectStore(tmp_path))
    with pytest.raises(NewerFormatError):
        service.get_project(project_id)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"{oops", "not valid JSON"),
        (b"[]", "expected a JSON object"),
        (b'{"format_version": "1", "id": "p", "name": "P"}', "invalid format_version"),
    ],
)
def test_bom_does_not_hide_an_invalid_project_json(service, raw, expected):
    project = service.create_project("P")
    (service.store.root / project.id / "project.json").write_bytes(BOM + raw)
    [stub] = service.list_projects()
    assert isinstance(stub, ProjectStub) and expected in stub.error


# --- config file BOM ---------------------------------------------------------------------


def test_bom_config_selects_the_data_dir_and_is_rewritten_without_a_bom(tmp_path, monkeypatch):
    saved_dir = (tmp_path / "Daten ä").resolve()
    saved_dir.mkdir()
    config_file = tmp_path / "config.json"
    config_file.write_bytes(BOM + json.dumps({"format_version": 1, "data_dir": str(saved_dir)}, indent=2).encode())
    monkeypatch.delenv("REQMAN_DATA_DIR", raising=False)
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_file))
    import reqman.app as app_module

    importlib.reload(app_module)
    client = TestClient(app_module.app)
    assert client.get("/config").json() == {"data_dir": str(saved_dir)}

    new_dir = (tmp_path / "Neu ö").resolve()
    resp = client.post("/config", json={"data_dir": str(new_dir)})
    assert resp.status_code == 200, resp.text
    raw = config_file.read_bytes()
    assert not raw.startswith(BOM)
    text = raw.decode("utf-8")
    assert json.loads(text) == {"format_version": 1, "data_dir": str(new_dir)}
    assert list(json.loads(text)) == ["format_version", "data_dir"]
    assert text == json.dumps(json.loads(text), indent=2) + "\n"


def test_bom_config_from_a_newer_tool_is_refused_and_kept(tmp_path, monkeypatch):
    from reqman import config as config_module

    config_file = tmp_path / "config.json"
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_file))
    config_file.write_bytes(BOM + json.dumps({"format_version": 99, "data_dir": "/x"}).encode())
    before = config_file.read_bytes()
    with pytest.raises(NewerFormatError):
        config_module.load_saved_data_dir()
    with pytest.raises(NewerFormatError):
        config_module.save_data_dir("/y")
    assert config_file.read_bytes() == before


# --- format-check cache ----------------------------------------------------------------


def test_format_check_notices_project_json_made_invalid_after_a_cached_valid_read(service):
    project = service.create_project("P")
    module = service.create_module(project.id, "Reqs", "R")
    path = service.store.root / project.id / "project.json"
    original = path.read_bytes()
    assert service.get_module(project.id, module.id).name == "Reqs"  # caches the check

    path.write_bytes(b"{oops")
    with pytest.raises(ValueError, match="not valid JSON"):
        service.get_module(project.id, module.id)
    with pytest.raises(ValueError, match="not valid JSON"):
        service.store.save_module(project.id, module)

    path.write_bytes(original)
    assert service.get_module(project.id, module.id).name == "Reqs"


def test_format_check_notices_a_same_size_edit_that_restores_the_mtime(service):
    project = service.create_project("P")
    module = service.create_module(project.id, "Reqs", "R")
    path = service.store.root / project.id / "project.json"
    original = path.read_bytes()
    stat = path.stat()
    assert service.get_module(project.id, module.id).name == "Reqs"

    garbage = b"[" + b" " * (len(original) - 2) + b"]"
    assert len(garbage) == len(original)
    path.write_bytes(garbage)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="expected a JSON object"):
        service.get_module(project.id, module.id)


@pytest.mark.parametrize(
    ("bad", "error"),
    [(b"{oops", ValueError), (None, NewerFormatError)],
)
def test_a_failed_format_check_is_not_cached(service, bad, error):
    project = service.create_project("P")
    module = service.create_module(project.id, "Reqs", "R")
    path = service.store.root / project.id / "project.json"
    original = path.read_bytes()
    if bad is None:
        _set_raw(path, format_version=NEWER)
    else:
        path.write_bytes(bad)
    for _ in range(2):
        with pytest.raises(error):
            service.get_module(project.id, module.id)

    path.write_bytes(original)
    assert service.get_module(project.id, module.id).name == "Reqs"
    assert [p.id for p in service.list_projects()] == [project.id]
    assert not isinstance(service.list_projects()[0], ProjectStub)


# --- names whose id is too long for a folder name -------------------------------------

LONG_NAME = "x" * 300


def _tree(directory: Path) -> dict[str, bytes | None]:
    """Every path under ``directory``: file contents, ``None`` for a directory."""
    return {
        str(p.relative_to(directory)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(directory.rglob("*"))
    }


def _finishes(fn, timeout=10.0):
    """``fn()``'s result; fails (instead of hanging the suite) if it does not return in ``timeout`` s."""
    outcome: dict = {}

    def run():
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "the call did not finish: the server would hang"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


@pytest.mark.parametrize("template_id", [None, "builtin:rbc_template"], ids=["blank", "template"])
def test_creating_a_project_whose_id_is_too_long_is_400_and_32602_without_writes(client, tmp_path, template_id):
    lenient = TestClient(client.app, raise_server_exceptions=False)
    body = {"name": LONG_NAME, "template_id": template_id}
    before = _tree(tmp_path)
    resp = _finishes(lambda: lenient.post("/projects", json=body))
    assert resp.status_code == 400 and "too long" in resp.json()["detail"], resp.text

    def over_ws():
        with lenient.websocket_connect("/ws") as ws, lenient.websocket_connect("/ws") as observer:
            response = _rpc(ws, "project.create", body)
            assert response["error"]["code"] == -32602 and "too long" in response["error"]["message"], response
            _assert_no_broadcast(observer)

    # The whole session runs under the guard: closing it waits for the server.
    _finishes(over_ws)
    assert _tree(tmp_path) == before
    assert lenient.get("/projects").json() == []


def test_creating_a_module_whose_id_is_too_long_is_400_and_32602_without_writes(client, tmp_path):
    lenient = TestClient(client.app, raise_server_exceptions=False)
    project_id = lenient.post("/projects", json={"name": "P"}).json()["id"]
    body = {"name": LONG_NAME, "prefix": "L"}
    before = _tree(tmp_path)
    resp = _finishes(lambda: lenient.post(f"/projects/{project_id}/modules", json=body))
    assert resp.status_code == 400 and "too long" in resp.json()["detail"], resp.text

    def over_ws():
        with lenient.websocket_connect("/ws") as ws, lenient.websocket_connect("/ws") as observer:
            response = _rpc(ws, "module.create", {"project_id": project_id, **body})
            assert response["error"]["code"] == -32602 and "too long" in response["error"]["message"], response
            _assert_no_broadcast(observer)

    _finishes(over_ws)
    assert _tree(tmp_path) == before
    assert lenient.get(f"/projects/{project_id}/modules").json() == []


def test_an_id_that_only_becomes_too_long_with_a_suffix_is_rejected(service):
    limit = service.store.max_name_length()
    if limit > 1024:
        pytest.skip(f"file name limit {limit} is too large to test")
    first = service.create_project("y" * limit)
    assert first.id == "y" * limit
    before = _tree(service.store.root)
    with pytest.raises(ValueError, match="too long"):
        _finishes(lambda: service.create_project("y" * limit))
    module = service.create_module(first.id, "m" * limit, "M")
    with pytest.raises(ValueError, match="too long"):
        _finishes(lambda: service.create_module(first.id, "m" * limit, "N"))
    assert [m.id for m in service.list_modules(first.id)] == [module.id]
    assert [p.id for p in service.list_projects()] == [first.id]
    assert set(_tree(service.store.root)) >= set(before)


def test_slug_collision_loop_is_bounded():
    with pytest.raises(ValueError, match="free id"):
        _finishes(lambda: _unique_slug("Name", lambda candidate: True, fallback="project"))


def test_getting_a_project_whose_id_is_too_long_is_404_and_32001(client):
    long_id = "x" * 300
    assert client.get(f"/projects/{long_id}").status_code == 404
    assert client.get(f"/projects/{long_id}/modules").json() == []
    with client.websocket_connect("/ws") as ws:
        assert _rpc(ws, "project.get", {"id": long_id})["error"]["code"] == -32001


# --- unsearchable folders in the data directory ------------------------------------------


def test_unsearchable_folders_are_listed_only_if_named_like_a_project(service):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root: chmod does not stop reads")
    root = service.store.root
    good = service.create_project("Good")
    folders = [root / name for name in (".hidden", "lost+found", ".Spotlight-V100", "alpha")]
    for folder in folders:
        folder.mkdir()
    try:
        for folder in folders:
            folder.chmod(0)
        try:
            (root / "alpha" / "project.json").stat()
        except PermissionError:
            pass
        else:
            pytest.skip("chmod has no effect on this platform")
        assert service.store.list_project_ids() == ["alpha", good.id]
        listed = service.list_projects()
        assert [p.id for p in listed] == ["alpha", good.id]
        assert isinstance(listed[0], ProjectStub) and "unreadable project.json" in listed[0].error
    finally:
        for folder in folders:
            folder.chmod(0o755)
    # Once searchable, the empty folders are not projects at all.
    assert service.store.list_project_ids() == [good.id]


# --- module purge on a stub or a missing project --------------------------------------------


@pytest.mark.parametrize(
    ("raw", "status", "expected"),
    [
        (None, 409, NEWER_MESSAGE),
        (b"{oops", 400, "not valid JSON"),
        (b'{"name": "No id"}\n', 400, "missing key 'id'"),
    ],
    ids=["newer", "invalid_json", "wrong_shape"],
)
def test_purging_a_module_of_a_stub_project_is_rejected_without_writes_or_broadcast(
    client, tmp_path, raw, status, expected
):
    # An empty module: nothing would be deleted, but the purge must still
    # refuse a project it cannot load.
    project_id = client.post("/projects", json={"name": "Empty"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Reqs", "prefix": "R"}).json()["id"]
    path = tmp_path / project_id / "project.json"
    if raw is None:
        _set_raw(path, format_version=NEWER)
    else:
        path.write_bytes(raw)
    before = _tree(tmp_path)

    for target in (module_id, "no-such-module"):
        with client.websocket_connect("/ws") as observer:
            resp = client.post(f"/projects/{project_id}/modules/{target}/purge")
            assert resp.status_code == status and expected in resp.json()["detail"], resp.text
            _assert_no_broadcast(observer)
        with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
            response = _rpc(ws, "module.purge", {"project_id": project_id, "id": target})
            assert response["error"]["code"] == -32602 and expected in response["error"]["message"], response
            _assert_no_broadcast(observer)
    assert _tree(tmp_path) == before


def test_purging_a_module_of_a_missing_project_is_404_and_32001_without_broadcast(client, tmp_path):
    with client.websocket_connect("/ws") as observer:
        resp = client.post("/projects/no-such-project/modules/reqs/purge")
        assert resp.status_code == 404 and "not found" in resp.json()["detail"], resp.text
        _assert_no_broadcast(observer)
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        response = _rpc(ws, "module.purge", {"project_id": "no-such-project", "id": "reqs"})
        assert response["error"]["code"] == -32001, response
        _assert_no_broadcast(observer)
    assert not (tmp_path / "no-such-project").exists()


def test_purging_a_missing_module_of_a_valid_project_purges_nothing(client, tmp_path):
    # Unchanged behaviour: a module that does not exist has nothing to purge.
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    before = _tree(tmp_path)
    resp = client.post(f"/projects/{project_id}/modules/no-such-module/purge")
    assert resp.status_code == 200 and resp.json() == {"purged": 0}, resp.text
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "module.purge", {"project_id": project_id, "id": "no-such-module"})
        assert response["result"] == {"purged": 0}, response
    assert _tree(tmp_path) == before


# --- missing modules and requirements of a stub project -----------------------------------------


@pytest.mark.parametrize(
    ("raw", "status", "expected"),
    [
        (None, 409, NEWER_MESSAGE),
        (b"{oops", 400, "not valid JSON"),
    ],
    ids=["newer", "invalid_json"],
)
def test_missing_modules_and_requirements_of_a_stub_report_the_stub_error(client, tmp_path, raw, status, expected):
    # Paths that would be "not found" (or an empty list) in a loadable project
    # report why the project cannot be loaded instead.
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Reqs", "prefix": "R"}).json()["id"]
    path = tmp_path / project_id / "project.json"
    if raw is None:
        _set_raw(path, format_version=NEWER)
    else:
        path.write_bytes(raw)
    before = _tree(tmp_path)
    base = f"/projects/{project_id}"
    with client.websocket_connect("/ws") as observer:
        for resp in (
            client.get(f"{base}/modules/no-such-module"),
            client.get(f"{base}/modules/{module_id}/requirements"),
            client.get(f"{base}/modules/no-such-module/requirements"),
            client.get(f"{base}/modules/{module_id}/requirements/R-404"),
            client.delete(f"{base}/modules/{module_id}/requirements/R-404"),
            client.get(f"{base}/requirements/R-404"),
        ):
            assert resp.status_code == status, (resp.request.method, resp.request.url, resp.text)
            assert expected in resp.json()["detail"]
        _assert_no_broadcast(observer)
    p, m = project_id, module_id
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        for index, (method, params) in enumerate(
            [
                ("module.get", {"project_id": p, "id": "no-such-module"}),
                ("requirement.list", {"project_id": p, "module_id": m}),
                ("requirement.list", {"project_id": p, "module_id": "no-such-module"}),
                ("requirement.get", {"project_id": p, "module_id": m, "id": "R-404"}),
                ("requirement.remove", {"project_id": p, "module_id": m, "id": "R-404"}),
            ],
            start=1,
        ):
            response = _rpc(ws, method, params, index)
            assert response["error"]["code"] == -32602, (method, response)
            assert expected in response["error"]["message"], (method, response)
        _assert_no_broadcast(observer)
    assert _tree(tmp_path) == before


def test_missing_modules_and_requirements_of_a_loadable_project_are_still_not_found(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Reqs", "prefix": "R"}).json()["id"]
    base = f"/projects/{project_id}"
    assert client.get(f"{base}/modules/{module_id}/requirements").json() == []
    assert client.get(f"{base}/modules/no-such-module/requirements").json() == []
    for resp in (
        client.get(f"{base}/modules/no-such-module"),
        client.get(f"{base}/modules/{module_id}/requirements/R-404"),
        client.delete(f"{base}/modules/{module_id}/requirements/R-404"),
        client.get(f"{base}/requirements/R-404"),
    ):
        assert resp.status_code == 404, (resp.request.method, resp.request.url, resp.text)
