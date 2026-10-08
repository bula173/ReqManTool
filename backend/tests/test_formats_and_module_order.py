"""Data format versions, persisted module order, project view-name uniqueness
and UTF-8 BOM user templates (backlog item requested 2026-10-04).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from reqman import config as config_module
from reqman.formats import (
    PROJECT_FORMAT,
    FileFormat,
    NewerFormatError,
    upgrade,
    with_format_version,
)
from reqman.service import ProjectService
from reqman.store import ProjectStore

RBC = "builtin:rbc_template"
NEWER = PROJECT_FORMAT.current + 1


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
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n")


# --- migration mechanism ------------------------------------------------------


def test_missing_format_version_is_version_one():
    fmt = FileFormat(name="test", current=1)
    assert upgrade({"a": 1}, fmt, "File x") == {"a": 1}
    assert upgrade({"format_version": 1, "a": 1}, fmt, "File x") == {"a": 1}


def test_migration_steps_run_in_order_without_mutating_input():
    calls = []

    def v1_to_v2(data):
        calls.append(1)
        return {**data, "b": data["a"] * 2}

    def v2_to_v3(data):
        calls.append(2)
        data["c"] = data.pop("b") + 1
        return data

    fmt = FileFormat(name="test", current=3, migrations={1: v1_to_v2, 2: v2_to_v3})
    original = {"a": 1, "nested": {"x": 1}}
    assert upgrade(original, fmt, "File x") == {"a": 1, "nested": {"x": 1}, "c": 3}
    assert calls == [1, 2]
    assert original == {"a": 1, "nested": {"x": 1}}
    # A version-2 file only runs the second step.
    calls.clear()
    assert upgrade({"format_version": 2, "a": 0, "b": 5}, fmt, "File x") == {"a": 0, "c": 6}
    assert calls == [2]


def test_missing_migration_step_is_a_clear_error():
    fmt = FileFormat(name="test", current=2)
    with pytest.raises(ValueError, match="no migration from test format_version 1 to 2"):
        upgrade({}, fmt, "File x")


def test_newer_version_is_rejected():
    fmt = FileFormat(name="test", current=1)
    with pytest.raises(NewerFormatError, match="File x was written by a newer ReqManTool"):
        upgrade({"format_version": 2}, fmt, "File x")


@pytest.mark.parametrize("value", [0, -1, "1", 1.0, True, None, [1]])
def test_invalid_format_version_is_rejected(value):
    with pytest.raises(ValueError, match="invalid format_version"):
        upgrade({"format_version": value}, FileFormat(name="test", current=1), "File x")


def test_with_format_version_puts_the_key_first():
    assert list(with_format_version({"id": "p", "format_version": 0}, PROJECT_FORMAT)) == ["format_version", "id"]


# --- project format version -----------------------------------------------------


def test_new_project_json_starts_with_format_version(service):
    project = service.create_project("P")
    text = (service.store.root / project.id / "project.json").read_text()
    data = json.loads(text)
    assert list(data)[0] == "format_version"
    assert data["format_version"] == PROJECT_FORMAT.current
    assert text == json.dumps(data, indent=2) + "\n"
    # The API representation is unchanged (the version is a file property).
    assert "format_version" not in service.get_project(project.id).to_dict()


def test_unversioned_project_loads_and_is_not_rewritten_by_reads(service):
    project = service.create_project("P")
    module = service.create_module(project.id, "Reqs", "R")
    service.add_requirement(project.id, module.id, {"object_text": "x"})
    path = service.store.root / project.id / "project.json"
    data = json.loads(path.read_text())
    del data["format_version"]
    path.write_text(json.dumps(data, indent=2) + "\n")
    before = _snapshot(service.store.root)

    assert service.get_project(project.id).name == "P"
    service.list_projects()
    service.list_modules(project.id)
    service.list_requirements(project.id, module.id)
    service.export_csv(project.id)
    assert _snapshot(service.store.root) == before

    service.add_attribute_definition(project.id, "owner", "Owner")  # a real save
    assert list(json.loads(path.read_text()))[0] == "format_version"


def _newer_project(service):
    project = service.create_project("P")
    module = service.create_module(project.id, "Reqs", "R")
    other = service.create_module(project.id, "Tests", "T")
    req = service.add_requirement(project.id, module.id, {"object_text": "x"})
    target = service.add_requirement(project.id, other.id, {"object_text": "y"})
    config = service.create_link_configuration(project.id, "Verifies", module.id, other.id)
    _set_raw(service.store.root / project.id / "project.json", format_version=NEWER)
    return project, module, other, req, target, config


def test_newer_project_is_rejected_on_read(service):
    project, module, _, req, _, _ = _newer_project(service)
    for call in (
        lambda: service.get_project(project.id),
        lambda: service.list_modules(project.id),
        lambda: service.get_module(project.id, module.id),
        lambda: service.list_requirements(project.id, module.id),
        lambda: service.get_requirement(project.id, module.id, req.id),
        lambda: service.export_reqif(project.id),
    ):
        with pytest.raises(NewerFormatError, match="written by a newer ReqManTool"):
            call()
    # The list does not fail as a whole: the project is a read-only stub carrying the error.
    [stub] = service.list_projects()
    assert stub.id == project.id and "written by a newer ReqManTool" in stub.error


def test_newer_project_files_are_never_written(service):
    project, module, other, req, target, config = _newer_project(service)
    before = _snapshot(service.store.root)
    mutations = (
        lambda: service.create_module(project.id, "More", "M"),
        lambda: service.reorder_module(project.id, other.id, "up"),
        lambda: service.add_requirement(project.id, module.id, {"object_text": "z"}),
        lambda: service.update_requirement(project.id, module.id, req.id, {"object_text": "z"}),
        lambda: service.move_requirement(project.id, module.id, req.id, None),
        lambda: service.reorder_requirement(project.id, module.id, req.id, "down"),
        lambda: service.remove_requirement(project.id, module.id, req.id),
        lambda: service.link_requirements(project.id, config.id, req.id, target.id),
        lambda: service.purge_module(project.id, module.id),
        lambda: service.purge_project(project.id),
        lambda: service.create_view(project.id, "V", []),
        lambda: service.set_project_parent(project.id, None),
        lambda: service.import_csv(project.id, module.id, "ObjectText\na\n"),
        lambda: service.store.save_link_pairs(project.id, config.id, []),
        lambda: service.store.delete_link_pairs(project.id, config.id),
        lambda: service.store.delete_requirement(project.id, module.id, req.id),
    )
    for mutate in mutations:
        with pytest.raises(NewerFormatError):
            mutate()
    assert _snapshot(service.store.root) == before


def test_newer_project_is_409_on_rest_and_error_on_ws(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "M", "prefix": "M"}).json()["id"]
    _set_raw(tmp_path / project_id / "project.json", format_version=NEWER)
    before = _snapshot(tmp_path / project_id)

    for resp in (
        client.get(f"/projects/{project_id}"),
        client.get(f"/projects/{project_id}/modules"),
        client.post(f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "x"}}),
    ):
        assert resp.status_code == 409, resp.text
        assert "written by a newer ReqManTool" in resp.json()["detail"]

    resp = client.get("/projects")
    assert resp.status_code == 200, resp.text
    [stub] = resp.json()
    assert stub["id"] == project_id and stub["name"] == "P"
    assert "written by a newer ReqManTool" in stub["error"]

    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "module.create", {"project_id": project_id, "name": "N", "prefix": "N"})
        assert response["error"]["code"] == -32602
        assert "written by a newer ReqManTool" in response["error"]["message"]
        assert _rpc(ws, "project.list", {}, 2)["result"] == [stub]
    assert _snapshot(tmp_path / project_id) == before


def _newer_child_of(service):
    """Project ``x`` with subproject ``n``; ``n`` is then bumped to a newer format."""
    parent = service.create_project("X")
    child = service.create_project("N", parent_project_id=parent.id)
    _set_raw(service.store.root / child.id / "project.json", format_version=NEWER)
    return parent, child


def test_move_under_newer_project_is_rejected_not_a_cycle(service):
    parent, child = _newer_child_of(service)
    before = _snapshot(service.store.root)
    # The ancestor walk cannot read the newer project's parent, so it cannot
    # rule out a cycle (here there would be one: x -> n -> x).
    with pytest.raises(NewerFormatError, match="written by a newer ReqManTool"):
        service.set_project_parent(parent.id, child.id)
    assert _snapshot(service.store.root) == before
    assert service.get_project(parent.id).parent_project_id is None


def test_create_under_newer_project_is_rejected(service):
    _, child = _newer_child_of(service)
    before = _snapshot(service.store.root)
    with pytest.raises(NewerFormatError, match="written by a newer ReqManTool"):
        service.create_project("Sub", parent_project_id=child.id)
    with pytest.raises(NewerFormatError, match="written by a newer ReqManTool"):
        service.create_project("Sub", template_id=RBC, parent_project_id=child.id)
    assert _snapshot(service.store.root) == before


def test_parent_under_newer_project_is_409_on_rest_and_32602_on_ws(client, tmp_path):
    parent_id = client.post("/projects", json={"name": "X"}).json()["id"]
    child_id = client.post("/projects", json={"name": "N", "parent_project_id": parent_id}).json()["id"]
    _set_raw(tmp_path / child_id / "project.json", format_version=NEWER)
    before = _snapshot(tmp_path)

    for resp in (
        client.put(f"/projects/{parent_id}/parent", json={"parent_project_id": child_id}),
        client.post("/projects", json={"name": "Sub", "parent_project_id": child_id}),
    ):
        assert resp.status_code == 409, resp.text
        assert "written by a newer ReqManTool" in resp.json()["detail"]

    with client.websocket_connect("/ws") as ws:
        for request_id, (method, params) in enumerate(
            (
                ("project.set_parent", {"id": parent_id, "parent_project_id": child_id}),
                ("project.create", {"name": "Sub", "parent_project_id": child_id}),
            ),
            start=1,
        ):
            response = _rpc(ws, method, params, request_id)
            assert response["error"]["code"] == -32602, response
            assert "written by a newer ReqManTool" in response["error"]["message"]
    assert _snapshot(tmp_path) == before


# --- config format version ------------------------------------------------------


def test_config_file_is_versioned_and_legacy_config_loads(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_file))
    config_file.write_text(json.dumps({"data_dir": "/some/where"}, indent=2) + "\n")
    before = config_file.read_text()
    assert config_module.load_saved_data_dir() == "/some/where"
    assert config_file.read_text() == before  # reading does not rewrite

    config_module.save_data_dir("/else/where")
    text = config_file.read_text()
    assert text == json.dumps({"format_version": 1, "data_dir": "/else/where"}, indent=2) + "\n"


def test_newer_config_file_is_rejected_and_never_overwritten(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_file))
    config_file.write_text(json.dumps({"format_version": 99, "data_dir": "/x"}) + "\n")
    before = config_file.read_bytes()
    with pytest.raises(NewerFormatError, match="written by a newer ReqManTool"):
        config_module.load_saved_data_dir()
    with pytest.raises(NewerFormatError):
        config_module.save_data_dir("/y")
    assert config_file.read_bytes() == before


def test_set_config_refuses_to_overwrite_newer_config(client, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"format_version": 99, "data_dir": str(tmp_path)}) + "\n")
    before = config_file.read_bytes()
    resp = client.post("/config", json={"data_dir": str(tmp_path / "other")})
    assert resp.status_code == 409
    assert "written by a newer ReqManTool" in resp.json()["detail"]
    assert config_file.read_bytes() == before
    assert client.get("/config").json()["data_dir"] == str(tmp_path.resolve())


# --- module order ------------------------------------------------------------------


def test_new_modules_are_appended(service):
    project = service.create_project("P")
    for name in ("Zeta", "Alpha", "Mid"):
        service.create_module(project.id, name, "R")
    assert [m.id for m in service.list_modules(project.id)] == ["zeta", "alpha", "mid"]
    data = json.loads((service.store.root / project.id / "project.json").read_text())
    # Project format 7: entries are {"module": id} objects.
    assert data["module_order"] == [{"module": "zeta"}, {"module": "alpha"}, {"module": "mid"}]
    assert list(data)[-1] == "module_order"


def test_project_without_module_order_lists_in_id_order_and_appends(service):
    project = service.create_project("P")
    for name in ("Zeta", "Alpha"):
        service.create_module(project.id, name, "R")
    path = service.store.root / project.id / "project.json"
    data = json.loads(path.read_text())
    del data["module_order"]
    path.write_text(json.dumps(data, indent=2) + "\n")

    assert [m.id for m in service.list_modules(project.id)] == ["alpha", "zeta"]
    service.create_module(project.id, "Aardvark", "R")
    assert [m.id for m in service.list_modules(project.id)] == ["alpha", "zeta", "aardvark"]


def test_module_order_is_tolerant_of_hand_edits(service):
    project = service.create_project("P")
    for name in ("A", "B", "C", "D"):
        service.create_module(project.id, name, "R")
    path = service.store.root / project.id / "project.json"
    _set_raw(path, module_order=["c", "ghost", "a", "c", 7])
    before = path.read_bytes()
    # Unknown/duplicate/non-string entries are dropped, unlisted modules follow in id order.
    assert [m.id for m in service.list_modules(project.id)] == ["c", "a", "b", "d"]
    assert path.read_bytes() == before  # not rewritten by reading

    service.reorder_module(project.id, "d", "up")
    assert json.loads(path.read_text())["module_order"] == [{"module": m} for m in ("c", "a", "d", "b")]


@pytest.mark.parametrize("bad", ["abc", {"a": 1}, None, 5])
def test_non_list_module_order_reads_as_unset(service, bad):
    project = service.create_project("P")
    for name in ("B", "A"):
        service.create_module(project.id, name, "R")
    _set_raw(service.store.root / project.id / "project.json", module_order=bad)
    assert [m.id for m in service.list_modules(project.id)] == ["a", "b"]


def test_reorder_module_up_down_and_boundaries(service):
    project = service.create_project("P")
    for name in ("A", "B", "C"):
        service.create_module(project.id, name, "R")
    path = service.store.root / project.id / "project.json"

    moved = service.reorder_module(project.id, "c", "up")
    assert moved.id == "c"
    assert [m.id for m in service.list_modules(project.id)] == ["a", "c", "b"]
    service.reorder_module(project.id, "a", "down")
    assert [m.id for m in service.list_modules(project.id)] == ["c", "a", "b"]

    before = path.read_bytes()
    service.reorder_module(project.id, "c", "up")
    service.reorder_module(project.id, "b", "down")
    assert path.read_bytes() == before  # boundary moves write nothing

    with pytest.raises(ValueError, match="Invalid direction"):
        service.reorder_module(project.id, "a", "left")
    with pytest.raises(FileNotFoundError):
        service.reorder_module(project.id, "ghost", "up")
    with pytest.raises(FileNotFoundError):
        service.reorder_module("no-such-project", "a", "up")


def test_template_keeps_declared_module_order(service):
    project = service.create_project("RBC", template_id=RBC)
    # The RBC template's module tree, depth-first (folders Requirements, Tests, Test Execution).
    assert [m.name for m in service.list_modules(project.id)] == [
        "StRS", "SyRS", "SwRS", "SysTest", "SwTest", "SysTestExec", "SwTestExec"
    ]


def test_save_as_template_keeps_module_order(service):
    project = service.create_project("P")
    for name in ("A", "B", "C"):
        service.create_module(project.id, name, "R")
    service.reorder_module(project.id, "c", "up")
    service.reorder_module(project.id, "c", "up")
    summary = service.save_project_as_template(project.id, "Ordered")
    assert [m["key"] for m in summary["modules"]] == ["c", "a", "b"]
    copy = service.create_project("Copy", template_id=summary["id"])
    assert [m.id for m in service.list_modules(copy.id)] == ["c", "a", "b"]


def test_purge_keeps_module_in_order(service):
    project = service.create_project("P")
    for name in ("A", "B"):
        service.create_module(project.id, name, "R")
    service.reorder_module(project.id, "b", "up")
    service.purge_project(project.id)
    assert [m.id for m in service.list_modules(project.id)] == ["b", "a"]


def test_module_reorder_rest_and_ws_parity_with_broadcast(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    for name in ("A", "B", "C"):
        client.post(f"/projects/{project_id}/modules", json={"name": name, "prefix": "R"})

    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        resp = client.post(f"/projects/{project_id}/modules/c/reorder", json={"direction": "up"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == "c"
        assert json.loads(ws1.receive_text()) == {"event": "changed", "project_id": project_id}
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": project_id}
        assert [m["id"] for m in client.get(f"/projects/{project_id}/modules").json()] == ["a", "c", "b"]

        response = _rpc(ws1, "module.reorder", {"project_id": project_id, "id": "c", "direction": "up"})
        assert response["result"]["id"] == "c"
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": project_id}
        assert [m["id"] for m in _rpc(ws1, "module.list", {"project_id": project_id}, 2)["result"]] == ["c", "a", "b"]

        response = _rpc(ws1, "module.reorder", {"project_id": project_id, "id": "c", "direction": "sideways"}, 3)
        assert response["error"]["code"] == -32602
        response = _rpc(ws1, "module.reorder", {"project_id": project_id, "id": "ghost", "direction": "up"}, 4)
        assert response["error"]["code"] == -32001

    assert client.post(f"/projects/{project_id}/modules/c/reorder", json={"direction": "sideways"}).status_code == 422
    assert client.post(f"/projects/{project_id}/modules/ghost/reorder", json={"direction": "up"}).status_code == 404


# --- view names ---------------------------------------------------------------------


@pytest.mark.parametrize("duplicate", ["Main", "main", "  MAIN  ", "mAiN\t"])
def test_project_rejects_duplicate_view_names(service, duplicate):
    project = service.create_project("P")
    service.create_view(project.id, "Main", [])
    with pytest.raises(ValueError, match="View name '.*' is already used by view 'main'"):
        service.create_view(project.id, duplicate, [])
    assert [v.name for v in service.list_views(project.id)] == ["Main"]


def test_view_name_is_stripped_and_must_not_be_blank(service):
    project = service.create_project("P")
    assert service.create_view(project.id, "  Compact  ", []).name == "Compact"
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="View name must not be empty"):
            service.create_view(project.id, blank, [])


def test_duplicate_view_name_is_400_on_rest_and_32602_on_ws(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    assert client.post(f"/projects/{project_id}/views", json={"name": "Main"}).status_code == 200
    resp = client.post(f"/projects/{project_id}/views", json={"name": " main "})
    assert resp.status_code == 400
    assert "already used" in resp.json()["detail"]
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "view.create", {"project_id": project_id, "name": "MAIN"})
        assert response["error"] == {"code": -32602, "message": "View name 'MAIN' is already used by view 'main'"}


# --- templates with a UTF-8 BOM ------------------------------------------------------


def test_user_template_with_utf8_bom_is_accepted(service):
    data = {
        "format_version": 1,
        "id": "bom",
        "name": "Bom Ü",
        "modules": [{"key": "a", "name": "Alpha", "prefix": "A"}],
    }
    templates_dir = service.store.root / "_templates"
    templates_dir.mkdir(parents=True)
    (templates_dir / "bom.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(data, ensure_ascii=False).encode("utf-8"))
    entry = next(t for t in service.list_templates() if t["id"] == "user:bom")
    assert entry["error"] is None
    assert entry["name"] == "Bom Ü"
    project = service.create_project("From bom", template_id="user:bom")
    assert [m.name for m in service.list_modules(project.id)] == ["Alpha"]
