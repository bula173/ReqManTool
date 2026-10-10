"""Rich text size limits apply to the canonical result, not only the input.

``sanitize`` must never store a value it would reject on the next write
(copy/update) or that ``to_plain`` would truncate on read; a stored text
default over the limits must not block adding requirements.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from reqman.richtext import MAX_LENGTH, MAX_NODES, RICH_TEXT_PREFIX as P, sanitize, to_plain
from reqman.service import ProjectService
from reqman.store import ProjectStore

END = "</div>"
TOO_LARGE = "Rich text is too large"


def rich(body: str) -> str:
    return P + body + END


# Inputs within both limits whose canonical result is not: escaping
# (``&`` -> ``&amp;``) and list items gaining a ``p``.
AMP_OVERFLOW = rich("<p><b>" + "&" * (MAX_LENGTH - 100) + "</b></p>")
LI_OVERFLOW = P + "<ul>" + "<li>x" * 149_000
OVERFLOWS = [AMP_OVERFLOW, LI_OVERFLOW]


def _exact_length_value(extra: int) -> str:
    """A rich value whose canonical form is ``MAX_LENGTH + extra`` characters long."""
    shell = len(rich("<p><strong></strong></p>"))
    amps = (MAX_LENGTH - shell) // 5 - 10
    xs = MAX_LENGTH - shell - amps * 5 + extra
    return rich("<p><strong>" + "&" * amps + "x" * xs + "</strong></p>")


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path / "data"))


@pytest.fixture
def module(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "note", "Note", "text")
    return project.id, service.create_module(project.id, "M", "M").id


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def _rpc(ws, request_id: int, method: str, params: dict) -> dict:
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    reply = json.loads(ws.receive_text())
    while "id" not in reply:
        reply = json.loads(ws.receive_text())
    return reply


# --- R1: limits on the result ------------------------------------------------------------


def test_inputs_within_limits_but_results_over_them_are_rejected():
    assert len(AMP_OVERFLOW) <= MAX_LENGTH and len(LI_OVERFLOW) <= MAX_LENGTH
    for value in OVERFLOWS:
        with pytest.raises(ValueError, match=TOO_LARGE):
            sanitize(value)


def test_result_length_limit_is_exact():
    accepted = sanitize(_exact_length_value(0))
    assert len(accepted) == MAX_LENGTH
    assert sanitize(accepted) == accepted
    with pytest.raises(ValueError, match=TOO_LARGE):
        sanitize(_exact_length_value(1))


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(rich("<p><b>" + "&" * (MAX_LENGTH // 5 - 100) + "</b></p>"), id="amp"),
        pytest.param(P + "<ul>" + "<li>x" * ((MAX_NODES - 1) // 3), id="list-items"),
        pytest.param(_exact_length_value(0), id="exact-length"),
        pytest.param(rich("<p><em>a</em>" + "<br/>b" * (MAX_NODES // 2 - 10) + "</p>"), id="breaks"),
    ],
)
def test_accepted_near_limit_values_are_idempotent_and_read_whole(value):
    once = sanitize(value)
    assert sanitize(once) == once
    plain = to_plain(once)
    assert to_plain(value) == plain
    if "&" in value:
        assert plain.count("&") == value.count("&")
    if "<li>" in value:
        assert plain.count("- x") == value.count("<li>")
    if "<br/>b" in value:
        assert plain.count("b") == value.count("<br/>b")


def test_overflowing_results_are_rejected_by_service(service, module):
    project_id, module_id = module
    ok = service.add_requirement(project_id, module_id, {"object_text": "ok"})
    for value in OVERFLOWS:
        with pytest.raises(ValueError, match=TOO_LARGE):
            service.add_requirement(project_id, module_id, {"note": value})
        with pytest.raises(ValueError, match=TOO_LARGE):
            service.update_requirement(project_id, module_id, ok.id, {"note": value})
        with pytest.raises(ValueError, match=TOO_LARGE):
            service.add_attribute_definition(project_id, f"d{len(value)}", f"D{len(value)}", "text", default=value)
    assert service.get_requirement(project_id, module_id, ok.id).attributes.get("note") is None
    assert len(service.list_requirements(project_id, module_id)) == 1


def test_overflowing_results_are_rejected_by_rest_and_websocket(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "M", "prefix": "M"}).json()["id"]
    base = f"/projects/{project_id}/modules/{module_id}/requirements"
    ok = client.post(base, json={"attributes": {"object_text": "ok"}}).json()["id"]
    for value in OVERFLOWS:
        resp = client.post(base, json={"attributes": {"object_text": value}})
        assert resp.status_code == 400 and TOO_LARGE in resp.text, resp.text[:300]
        resp = client.put(f"{base}/{ok}", json={"attributes": {"object_text": value}})
        assert resp.status_code == 400 and TOO_LARGE in resp.text, resp.text[:300]
    with client.websocket_connect("/ws") as ws:
        request_id = 0
        for value in OVERFLOWS:
            for method, extra in (("requirement.add", {}), ("requirement.update", {"id": ok})):
                request_id += 1
                reply = _rpc(ws, request_id, method, {
                    "project_id": project_id, "module_id": module_id, **extra, "attributes": {"object_text": value},
                })
                assert reply["error"]["code"] == -32602
                assert TOO_LARGE in reply["error"]["message"]
    assert client.get(f"{base}/{ok}").json()["attributes"]["object_text"] == "ok"
    assert len(client.get(base).json()) == 1


# --- R2: an over-limit stored default -------------------------------------------------


def _hand_write_default(service, project_id: str, attr_id: str, default: str) -> None:
    path = service.store.root / project_id / "project.json"
    data = json.loads(path.read_text())
    for attr in data["attributes"]:
        if attr["id"] == attr_id:
            attr["default"] = default
    path.write_text(json.dumps(data, indent=2) + "\n")


def test_over_limit_stored_default_does_not_block_adding(service, module):
    project_id, module_id = module
    _hand_write_default(service, project_id, "note", LI_OVERFLOW)
    without = service.add_requirement(project_id, module_id, {"object_text": "a"})
    assert "note" not in without.attributes  # skipped like an invalid legacy default
    given = service.add_requirement(project_id, module_id, {"object_text": "b", "note": "mine"})
    assert given.attributes["note"] == "mine"


def test_given_value_means_the_default_is_never_sanitized(service, module, monkeypatch):
    import reqman.richtext as richtext

    project_id, module_id = module
    _hand_write_default(service, project_id, "note", rich("<p><b>d</b></p>"))
    seen: list[str] = []
    real = richtext.sanitize
    monkeypatch.setattr(richtext, "sanitize", lambda v: seen.append(v) or real(v))
    service.add_requirement(project_id, module_id, {"object_text": "o", "note": "mine"})
    assert sorted(seen) == ["mine", "o"]


def test_over_limit_stored_default_over_rest_and_websocket(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "M", "prefix": "M"}).json()["id"]
    resp = client.post(f"/projects/{project_id}/attributes", json={"id": "note", "name": "Note", "type": "text"})
    assert resp.status_code == 200, resp.text
    path = tmp_path / project_id / "project.json"
    data = json.loads(path.read_text())
    next(a for a in data["attributes"] if a["id"] == "note")["default"] = AMP_OVERFLOW
    path.write_text(json.dumps(data, indent=2) + "\n")
    base = f"/projects/{project_id}/modules/{module_id}/requirements"
    resp = client.post(base, json={"attributes": {"object_text": "a"}})
    assert resp.status_code == 200 and "note" not in resp.json()["attributes"], resp.text[:300]
    resp = client.post(base, json={"attributes": {"object_text": "b", "note": "n"}})
    assert resp.status_code == 200 and resp.json()["attributes"]["note"] == "n", resp.text[:300]
    with client.websocket_connect("/ws") as ws:
        reply = _rpc(ws, 1, "requirement.add", {"project_id": project_id, "module_id": module_id, "attributes": {"object_text": "c"}})
        assert "result" in reply and "note" not in reply["result"]["attributes"], str(reply)[:300]


def test_saving_a_project_with_an_over_limit_default_as_template_is_a_clear_error(service, module):
    project_id, _ = module
    _hand_write_default(service, project_id, "note", LI_OVERFLOW)
    with pytest.raises(ValueError, match=f"cannot be saved as a template.*{TOO_LARGE}"):
        service.save_project_as_template(project_id, "T")
    assert not any(t["source"] == "user" for t in service.list_templates())


def test_template_with_near_limit_default_creates_working_projects(service, module):
    project_id, _ = module
    default = rich("<p><b>" + "&" * (MAX_LENGTH // 5 - 100) + "</b></p>")
    service.add_attribute_definition(project_id, "big", "Big", "text", default=default)
    stored = next(a.default for a in service.get_project(project_id).attributes if a.id == "big")
    template = service.save_project_as_template(project_id, "T")
    created = service.create_project("Q", template_id=template["id"])
    copied = next(a.default for a in created.attributes if a.id == "big")
    assert copied == stored
    new_module = service.list_modules(created.id)[0].id
    req = service.add_requirement(created.id, new_module, {"object_text": "x"})
    assert req.attributes["big"] == stored


def test_copy_of_an_over_limit_stored_value_is_a_clear_400(service, module, client, tmp_path):
    project_id, module_id = module
    req = service.add_requirement(project_id, module_id, {"object_text": "ok"})
    path = service.store.root / project_id / "modules" / module_id / "requirements" / f"{req.id}.json"
    data = json.loads(path.read_text())
    data["attributes"]["note"] = LI_OVERFLOW
    path.write_text(json.dumps(data, indent=2) + "\n")
    with pytest.raises(ValueError, match=TOO_LARGE):
        service.copy_requirement(project_id, module_id, req.id, None)
    assert len(service.list_requirements(project_id, module_id)) == 1

    # The same over REST and WebSocket (the client's data directory is tmp_path).
    api_project = client.post("/projects", json={"name": "R"}).json()["id"]
    client.post(f"/projects/{api_project}/attributes", json={"id": "note", "name": "Note", "type": "text"})
    api_module = client.post(f"/projects/{api_project}/modules", json={"name": "M", "prefix": "M"}).json()["id"]
    base = f"/projects/{api_project}/modules/{api_module}/requirements"
    api_req = client.post(base, json={"attributes": {"object_text": "ok"}}).json()["id"]
    api_path = tmp_path / api_project / "modules" / api_module / "requirements" / f"{api_req}.json"
    data = json.loads(api_path.read_text())
    data["attributes"]["note"] = LI_OVERFLOW
    api_path.write_text(json.dumps(data, indent=2) + "\n")
    resp = client.post(f"{base}/{api_req}/copy", json={})
    assert resp.status_code == 400 and TOO_LARGE in resp.text, resp.text[:300]
    with client.websocket_connect("/ws") as ws:
        reply = _rpc(ws, 1, "requirement.copy", {"project_id": api_project, "module_id": api_module, "id": api_req})
        assert reply["error"]["code"] == -32602 and TOO_LARGE in reply["error"]["message"]
    assert len(client.get(base).json()) == 1


# --- R3: no quadratic stack scans -------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(P + "<span>" * 200 + "<li>" * 499_000, id="li-under-deep-spans"),
        pytest.param(P + "<p><button>" + "<span>" * 198 + "<ul>" * 499_000, id="p-out-of-scope"),
        pytest.param(P + "<li>" + "<span>" * 198 + "</ul>" * 399_000, id="end-tag-not-open"),
    ],
)
def test_deep_stack_with_many_start_tags_is_linear(value):
    assert len(value) <= MAX_LENGTH
    start = time.perf_counter()
    try:
        sanitize(value)
    except ValueError:
        pass
    to_plain(value)
    assert time.perf_counter() - start < 5  # was ~7 s for sanitize alone; now well under 2 s
