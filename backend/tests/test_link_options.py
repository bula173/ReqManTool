"""Drag-and-drop linking: ``ProjectService.link_options``, ``GET /projects/{id}/links/options``
and the WebSocket ``requirement.link_options`` method, within a project and across projects.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from reqman import extensions
from reqman.extensions import ACTION_PROJECT_READ, AccessDeniedError
from reqman.service import ProjectService
from reqman.store import ProjectStore


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path))


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as module

    importlib.reload(module)
    return module


@pytest.fixture
def client(app_module):
    return TestClient(app_module.app)


def add(service, project_id, module_id, text="Text"):
    return service.add_requirement(project_id, module_id, {"object_text": text}).id


def local_project(service):
    """Project ``p``: modules ``sys`` (SYS) and ``srs`` (SRS), one requirement each, no configuration."""
    project = service.create_project("P")
    service.create_module(project.id, "Sys", "SYS")
    service.create_module(project.id, "Srs", "SRS")
    return project.id, add(service, project.id, "sys"), add(service, project.id, "srs")


def cross_projects(service):
    """``a`` references ``b``; configuration ``a``.srs -> ``b``.sys; one requirement in each module."""
    service.create_project("B")
    service.create_module("b", "Sys", "SYS")
    b_req = add(service, "b", "sys")
    service.create_project("A")
    service.create_module("a", "Srs", "SRS")
    a_req = add(service, "a", "srs")
    service.set_project_references("a", ["b"])
    config = service.create_link_configuration("a", "Traces", "srs", "sys", "b")
    return config.id, a_req, b_req


def rpc(ws, method, params, request_id=1):
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    return json.loads(ws.receive_text())


# --- within one project ---------------------------------------------------------


def test_no_configuration_between_the_modules_gives_no_options(service):
    project_id, sys_req, srs_req = local_project(service)
    assert service.link_options(project_id, srs_req, sys_req) == []
    assert service.link_options(project_id, sys_req, srs_req) == []


def test_configuration_between_other_modules_is_not_offered(service):
    project_id, sys_req, srs_req = local_project(service)
    service.create_module(project_id, "Other", "OTH")
    service.create_link_configuration(project_id, "Refines", "other", "sys")
    assert service.link_options(project_id, srs_req, sys_req) == []


def test_option_in_drag_direction_when_configured_from_dragged_module(service):
    project_id, sys_req, srs_req = local_project(service)
    config = service.create_link_configuration(project_id, "Satisfies", "srs", "sys")

    options = service.link_options(project_id, srs_req, sys_req)

    assert len(options) == 1
    option = options[0]
    assert option["config"].id == config.id
    assert option["project_id"] == project_id
    assert (option["source_id"], option["target_id"]) == (srs_req, sys_req)
    assert (option["source_project_id"], option["target_project_id"]) == (project_id, project_id)
    assert (option["source_module_id"], option["target_module_id"]) == ("srs", "sys")
    assert option["exists"] is False


def test_option_reversed_when_configured_towards_dragged_module(service):
    project_id, sys_req, srs_req = local_project(service)
    service.create_link_configuration(project_id, "Satisfies", "srs", "sys")

    # Dragging the SYS requirement onto the SRS one still links SRS -> SYS.
    [option] = service.link_options(project_id, sys_req, srs_req)
    assert (option["source_id"], option["target_id"]) == (srs_req, sys_req)
    assert (option["source_module_id"], option["target_module_id"]) == ("srs", "sys")


def test_both_directions_configured_gives_one_option_each_in_configuration_order(service):
    project_id, sys_req, srs_req = local_project(service)
    forward = service.create_link_configuration(project_id, "Satisfies", "srs", "sys")
    backward = service.create_link_configuration(project_id, "Derived From", "sys", "srs")

    options = service.link_options(project_id, srs_req, sys_req)

    assert [o["config"].id for o in options] == [forward.id, backward.id]
    assert (options[0]["source_id"], options[0]["target_id"]) == (srs_req, sys_req)
    assert (options[1]["source_id"], options[1]["target_id"]) == (sys_req, srs_req)


def test_configuration_within_one_module_is_offered_once_in_drag_direction(service):
    project_id, sys_req, _ = local_project(service)
    other = add(service, project_id, "sys", "Second")
    config = service.create_link_configuration(project_id, "Refines", "sys", "sys")

    [option] = service.link_options(project_id, sys_req, other)
    assert option["config"].id == config.id
    assert (option["source_id"], option["target_id"]) == (sys_req, other)

    [option] = service.link_options(project_id, other, sys_req)
    assert (option["source_id"], option["target_id"]) == (other, sys_req)


def test_exists_marks_a_link_that_is_already_there(service):
    project_id, sys_req, srs_req = local_project(service)
    forward = service.create_link_configuration(project_id, "Satisfies", "srs", "sys")
    backward = service.create_link_configuration(project_id, "Derived From", "sys", "srs")
    service.link_requirements(project_id, forward.id, srs_req, sys_req)

    options = {o["config"].id: o["exists"] for o in service.link_options(project_id, sys_req, srs_req)}
    assert options == {forward.id: True, backward.id: False}


def test_link_options_is_read_only(service, tmp_path):
    project_id, sys_req, srs_req = local_project(service)
    service.create_link_configuration(project_id, "Satisfies", "srs", "sys")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    service.take_touched_projects()

    service.link_options(project_id, srs_req, sys_req)

    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before
    assert service.take_touched_projects() == set()


def test_same_requirement_on_both_ends_is_refused(service):
    project_id, sys_req, _ = local_project(service)
    with pytest.raises(ValueError, match="cannot be linked to itself"):
        service.link_options(project_id, sys_req, sys_req)
    with pytest.raises(ValueError, match="cannot be linked to itself"):
        service.link_options(project_id, sys_req, sys_req, project_id)


@pytest.mark.parametrize("which", ["from", "to"])
def test_unknown_requirement_is_not_found(service, which):
    project_id, sys_req, _ = local_project(service)
    from_id, to_id = ("SYS-9999", sys_req) if which == "from" else (sys_req, "SYS-9999")
    with pytest.raises(FileNotFoundError, match="SYS-9999"):
        service.link_options(project_id, from_id, to_id)


def test_unknown_project_is_not_found(service):
    with pytest.raises(FileNotFoundError):
        service.link_options("ghost", "A-1", "B-1")


# --- across projects ------------------------------------------------------------


def test_cross_project_option_when_dragging_from_the_referencing_project(service):
    config_id, a_req, b_req = cross_projects(service)

    # Dragged from a (the configuration holder) onto b's requirement.
    [option] = service.link_options("b", a_req, b_req, "a")

    assert option["config"].id == config_id
    assert option["project_id"] == "a"
    assert (option["source_project_id"], option["source_id"]) == ("a", a_req)
    assert (option["target_project_id"], option["target_id"]) == ("b", b_req)
    assert (option["source_module_id"], option["target_module_id"]) == ("srs", "sys")
    assert option["exists"] is False


def test_cross_project_option_when_dragging_from_the_referenced_project(service):
    config_id, a_req, b_req = cross_projects(service)

    # Dragged from b onto a's requirement: still a's configuration, a -> b.
    [option] = service.link_options("a", b_req, a_req, "b")

    assert option["config"].id == config_id
    assert option["project_id"] == "a"
    assert (option["source_project_id"], option["source_id"]) == ("a", a_req)
    assert (option["target_project_id"], option["target_id"]) == ("b", b_req)


def test_cross_project_exists_after_linking_with_qualified_target(service):
    config_id, a_req, b_req = cross_projects(service)
    service.link_requirements("a", config_id, a_req, f"b:{b_req}")

    assert [o["exists"] for o in service.link_options("b", a_req, b_req, "a")] == [True]
    assert [o["exists"] for o in service.link_options("a", b_req, a_req, "b")] == [True]


def test_cross_project_ignores_local_configurations_and_unrelated_projects(service):
    _, a_req, b_req = cross_projects(service)
    # A local configuration of a whose module ids match b's must not be offered across projects.
    service.create_module("a", "Sys", "ASY")
    service.create_link_configuration("a", "Local", "srs", "sys")
    service.create_project("C")
    service.create_module("c", "Sys", "CSY")
    c_req = add(service, "c", "sys")

    assert len(service.link_options("b", a_req, b_req, "a")) == 1
    assert service.link_options("c", a_req, c_req, "a") == []
    assert service.link_options("a", c_req, a_req, "c") == []


def test_cross_project_same_requirement_id_is_not_self_link(service):
    service.create_project("B")
    service.create_module("b", "Sys", "REQ")
    b_req = add(service, "b", "sys")
    service.create_project("A")
    service.create_module("a", "Srs", "REQ")
    a_req = add(service, "a", "srs")
    assert a_req == b_req == "REQ-0001"
    service.set_project_references("a", ["b"])
    config = service.create_link_configuration("a", "Traces", "srs", "sys", "b")

    [option] = service.link_options("b", a_req, b_req, "a")
    assert option["config"].id == config.id
    assert (option["source_project_id"], option["target_project_id"]) == ("a", "b")


def test_cross_project_unknown_dragged_project_or_requirement_is_not_found(service):
    _, a_req, b_req = cross_projects(service)
    with pytest.raises(FileNotFoundError):
        service.link_options("b", a_req, b_req, "ghost")
    with pytest.raises(FileNotFoundError, match="SRS-9999"):
        service.link_options("b", "SRS-9999", b_req, "a")
    with pytest.raises(FileNotFoundError, match="SYS-9999"):
        service.link_options("b", a_req, "SYS-9999", "a")


class _DenyRead:
    """Permission guard refusing ``project.read`` on one project."""

    def __init__(self, project_id):
        self.project_id = project_id

    def check(self, identity, action, project_id=None):
        if action == ACTION_PROJECT_READ and project_id == self.project_id:
            raise AccessDeniedError(f"read denied on {project_id}")


def test_cross_project_link_options_into_an_unreadable_project_is_denied(service, monkeypatch):
    _, a_req, b_req = cross_projects(service)
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _DenyRead("a"))

    # Dragged from the unreadable project a onto b's requirement.
    with pytest.raises(AccessDeniedError, match="read denied on a"):
        service.link_options("b", a_req, b_req, "a")
    # Readable both ways still works.
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _DenyRead("c"))
    assert len(service.link_options("b", a_req, b_req, "a")) == 1


# --- REST and WebSocket -----------------------------------------------------------


def rest_local_setup(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    for name, prefix in (("Sys", "SYS"), ("Srs", "SRS")):
        assert client.post(f"/projects/{project_id}/modules", json={"name": name, "prefix": prefix}).status_code == 200
    sys_req = client.post(f"/projects/{project_id}/modules/sys/requirements", json={"attributes": {"object_text": "S"}}).json()["id"]
    srs_req = client.post(f"/projects/{project_id}/modules/srs/requirements", json={"attributes": {"object_text": "R"}}).json()["id"]
    config_id = client.post(
        f"/projects/{project_id}/link-configurations",
        json={"name": "Satisfies", "source_module_id": "srs", "target_module_id": "sys"},
    ).json()["id"]
    return project_id, config_id, sys_req, srs_req


def test_rest_link_options_returns_serialized_options(client):
    project_id, config_id, sys_req, srs_req = rest_local_setup(client)

    resp = client.get(f"/projects/{project_id}/links/options", params={"from_id": sys_req, "to_id": srs_req})

    assert resp.status_code == 200, resp.text
    [option] = resp.json()
    assert option["config"]["id"] == config_id
    assert option["config"]["name"] == "Satisfies"
    assert "target_project_id" not in option["config"]
    assert option["source_id"] == srs_req
    assert option["target_id"] == sys_req
    assert option["project_id"] == project_id
    assert option["exists"] is False


def test_rest_link_options_errors(client):
    project_id, _, sys_req, srs_req = rest_local_setup(client)
    url = f"/projects/{project_id}/links/options"

    assert client.get(url, params={"from_id": sys_req, "to_id": sys_req}).status_code == 400
    assert client.get(url, params={"from_id": "SYS-9999", "to_id": srs_req}).status_code == 404
    assert client.get(url, params={"from_id": sys_req, "to_id": "SRS-9999"}).status_code == 404
    assert client.get("/projects/ghost/links/options", params={"from_id": "A", "to_id": "B"}).status_code == 404
    assert client.get(url, params={"from_id": sys_req, "to_id": srs_req, "from_project_id": "ghost"}).status_code == 404
    # Both requirement ids are required query parameters.
    assert client.get(url, params={"from_id": sys_req}).status_code == 422


def test_rest_and_websocket_link_options_agree(client):
    project_id, _, sys_req, srs_req = rest_local_setup(client)
    rest = client.get(
        f"/projects/{project_id}/links/options", params={"from_id": srs_req, "to_id": sys_req}
    ).json()

    with client.websocket_connect("/ws") as ws:
        response = rpc(ws, "requirement.link_options", {"project_id": project_id, "from_id": srs_req, "to_id": sys_req})
        assert response["result"] == rest

        error = rpc(ws, "requirement.link_options", {"project_id": project_id, "from_id": srs_req, "to_id": srs_req}, 2)
        assert error["error"]["code"] == -32602
        missing = rpc(ws, "requirement.link_options", {"project_id": project_id, "from_id": "X-1", "to_id": sys_req}, 3)
        assert missing["error"]["code"] == -32001


def test_rest_and_websocket_cross_project_link_options_agree(client):
    for name, module, prefix in (("B", "Sys", "SYS"), ("A", "Srs", "SRS")):
        project_id = client.post("/projects", json={"name": name}).json()["id"]
        client.post(f"/projects/{project_id}/modules", json={"name": module, "prefix": prefix})
    b_req = client.post("/projects/b/modules/sys/requirements", json={"attributes": {"object_text": "S"}}).json()["id"]
    a_req = client.post("/projects/a/modules/srs/requirements", json={"attributes": {"object_text": "R"}}).json()["id"]
    assert client.put("/projects/a/references", json={"references": ["b"]}).status_code == 200
    config = client.post(
        "/projects/a/link-configurations",
        json={"name": "Traces", "source_module_id": "srs", "target_module_id": "sys", "target_project_id": "b"},
    ).json()
    assert config["target_project_id"] == "b"

    rest = client.get("/projects/b/links/options", params={"from_id": a_req, "to_id": b_req, "from_project_id": "a"})
    assert rest.status_code == 200, rest.text
    [option] = rest.json()
    assert option["project_id"] == "a"
    assert option["config"]["target_project_id"] == "b"
    assert (option["source_project_id"], option["target_project_id"]) == ("a", "b")

    with client.websocket_connect("/ws") as ws:
        response = rpc(
            ws, "requirement.link_options",
            {"project_id": "b", "from_id": a_req, "to_id": b_req, "from_project_id": "a"},
        )
        assert response["result"] == rest.json()


def test_link_options_is_not_a_mutating_method_and_does_not_broadcast(client, app_module):
    assert "requirement.link_options" in app_module.RPC_METHODS
    assert "requirement.link_options" not in app_module.MUTATING_METHODS
    project_id, _, sys_req, srs_req = rest_local_setup(client)

    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        assert client.get(
            f"/projects/{project_id}/links/options", params={"from_id": srs_req, "to_id": sys_req}
        ).status_code == 200
        assert "result" in rpc(ws1, "requirement.link_options", {"project_id": project_id, "from_id": srs_req, "to_id": sys_req})
        # The next event either client sees is from this marker mutation, not from the reads.
        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws1.receive_text()) == {"event": "changed", "project_id": marker}
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": marker}
