"""The ``operational_scenarios`` module type, the RBC OpScen module and chapter skeletons.

Smoke tests: creating an operational scenarios module, the link type pairs
it allows, project format 9, the RBC template's OpScen module (scoped
attributes, link configuration, skeleton) and every RBC module's chapter
skeleton, scenarios in a traceability matrix, and "Apply chapter template"
(``apply_module_skeleton``) on an existing module with REST/WebSocket parity.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reqman import richtext
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


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


def _texts(service, project_id, module_id, parent_id=None) -> list[str]:
    siblings = [r for r in service.list_requirements(project_id, module_id) if r.parent_id == parent_id]
    siblings.sort(key=lambda r: (r.order, r.id))
    return [richtext.to_plain(r.attributes.get("object_text") or "") for r in siblings]


# --- module type and link pairs ----------------------------------------------------------


def test_create_operational_scenarios_module_round_trips(service):
    project = service.create_project("P")
    module = service.create_module(project.id, "Scenarios", "OPS", "", "operational_scenarios")
    assert module.type == "operational_scenarios"
    assert service.get_module(project.id, module.id).type == "operational_scenarios"
    path = service.store.root / project.id / "modules" / module.id / "module.json"
    assert json.loads(path.read_text())["type"] == "operational_scenarios"
    assert json.loads((service.store.root / project.id / "project.json").read_text())["format_version"] == PROJECT_FORMAT.current


@pytest.mark.parametrize(
    "source_type, target_type",
    [
        ("requirements", "operational_scenarios"),
        ("test_cases", "operational_scenarios"),
        ("operational_scenarios", "operational_scenarios"),
    ],
)
def test_link_pairs_allowed_with_scenarios(service, source_type, target_type):
    project = service.create_project("P")
    source = service.create_module(project.id, "Source", "SRC", "", source_type)
    target = service.create_module(project.id, "Target", "TGT", "", target_type)
    config = service.create_link_configuration(project.id, "Traces", source.id, target.id)
    assert config.source_module_id == source.id and config.target_module_id == target.id


@pytest.mark.parametrize(
    "source_type, target_type",
    [
        ("operational_scenarios", "requirements"),
        ("operational_scenarios", "test_cases"),
        ("test_execution", "operational_scenarios"),
        ("operational_scenarios", "test_execution"),
    ],
)
def test_link_pairs_refused_with_scenarios(service, source_type, target_type):
    project = service.create_project("P")
    source = service.create_module(project.id, "Source", "SRC", "", source_type)
    target = service.create_module(project.id, "Target", "TGT", "", target_type)
    with pytest.raises(ValueError, match="link"):
        service.create_link_configuration(project.id, "Traces", source.id, target.id)
    assert service.list_link_configurations(project.id) == []


def test_project_format_8_drops_scenario_type_scopes():
    v8 = {
        "format_version": 8,
        "id": "p",
        "name": "P",
        "attributes": [
            {"id": "a", "name": "A", "type": "string", "module_types": ["test_cases", "operational_scenarios"]},
            {"id": "b", "name": "B", "type": "string", "module_types": ["operational_scenarios"]},
        ],
    }
    upgraded = upgrade(v8, PROJECT_FORMAT, "Project 'p'")
    assert upgraded["attributes"][0]["module_types"] == ["test_cases"]
    assert "module_types" not in upgraded["attributes"][1]


# --- RBC template --------------------------------------------------------------------------


def test_rbc_template_creates_opscen_with_scoped_attributes_and_link(service):
    project = service.create_project("Rbc", template_id=RBC)
    modules = service.list_modules(project.id)
    assert modules[0].name == "OpScen" and modules[0].prefix == "OPS"
    assert modules[0].type == "operational_scenarios"
    by_name = {m.name: m.id for m in modules}
    opscen, strs = by_name["OpScen"], by_name["StRS"]
    attrs = {a.id: a for a in service.get_project(project.id).attributes}
    for attr_id in ("actors", "trigger", "scenario_steps", "postconditions", "operational_mode"):
        assert attrs[attr_id].modules == [opscen], attr_id
    assert attrs["preconditions"].modules == [opscen, by_name["SysTest"], by_name["SwTest"]]
    assert attrs["operational_mode"].type == "enum"
    assert "Level 2" in attrs["operational_mode"].choices and "SR" in attrs["operational_mode"].choices
    config = next(c for c in service.list_link_configurations(project.id) if c.name == "Derived from OpScen")
    assert (config.source_module_id, config.target_module_id) == (strs, opscen)
    assert _texts(service, project.id, opscen) == [
        "Introduction", "Actors and Roles", "Normal Operation", "Degraded Operation", "Emergency", "Maintenance",
    ]
    views = {v.name: v for v in service.list_views(project.id)}
    assert views["Operational Scenarios"].modules == [opscen]


def test_every_rbc_module_gets_chapters(service):
    project = service.create_project("Rbc", template_id=RBC)
    for module in service.list_modules(project.id):
        roots = _texts(service, project.id, module.id)
        assert roots, module.name
    by_name = {m.name: m.id for m in service.list_modules(project.id)}
    assert "Operational Needs" in _texts(service, project.id, by_name["StRS"])
    assert "Test Strategy and Coverage" in _texts(service, project.id, by_name["SysTest"])
    assert _texts(service, project.id, by_name["SwTestExec"]) == [
        "Campaign", "Results", "Deviations and Anomalies", "Summary and Verdict",
    ]


def test_traceability_reports_scenarios_not_reached_by_tests(service):
    project = service.create_project("P")
    ops = service.create_module(project.id, "Scenarios", "OPS", "", "operational_scenarios")
    tests = service.create_module(project.id, "Tests", "TC", "", "test_cases")
    config = service.create_link_configuration(project.id, "Validates", tests.id, ops.id)
    covered = service.add_requirement(project.id, ops.id, {"object_text": "Start of Mission"})
    uncovered = service.add_requirement(project.id, ops.id, {"object_text": "Handover"})
    test = service.add_requirement(project.id, tests.id, {"object_text": "SoM test"})
    service.link_requirements(project.id, config.id, test.id, covered.id)

    report = service.traceability_matrix(project.id, [config.id])
    assert [m["id"] for m in report["modules"]] == [ops.id, tests.id]
    assert report["steps"][0]["covered"] == 1 and report["steps"][0]["uncovered"] == [uncovered.id]
    assert report["unverified"] == [
        {"module_id": ops.id, "test_module_id": tests.id, "total": 2, "ids": [uncovered.id]}
    ]


# --- apply chapter template ------------------------------------------------------------------


def _module_with_object_type(service):
    project = service.create_project("P")
    module = service.create_module(project.id, "Scenarios", "OPS", "", "operational_scenarios")
    service.add_attribute_definition(
        project.id, "object_type", "Object Type", "enum", choices=["Heading", "Information", "Requirement"]
    )
    return project, module


def test_apply_skeleton_adds_missing_headings_only_and_is_idempotent(service):
    project, module = _module_with_object_type(service)
    intro = service.add_requirement(
        project.id, module.id, {"object_text": "  introduction ", "object_type": "Heading"}
    )
    body = service.add_requirement(project.id, module.id, {"object_text": "A scenario", "object_type": "Requirement"})
    before = {r.id: r.to_dict() for r in service.list_requirements(project.id, module.id)}

    preview = service.preview_module_skeleton(project.id, module.id)
    assert preview["template_id"] == "builtin:operational_scenarios"
    assert [h["heading"] for h in preview["headings"]] == [
        "Actors and Roles", "Normal Operation", "Degraded Operation", "Emergency", "Maintenance",
    ]
    assert service.list_requirements(project.id, module.id) and len(before) == 2  # preview writes nothing

    result = service.apply_module_skeleton(project.id, module.id)
    assert len(result["created"]) == 5
    after = {r.id: r for r in service.list_requirements(project.id, module.id)}
    for requirement_id, data in before.items():
        assert after[requirement_id].to_dict() == data  # nothing existing changed
    assert _texts(service, project.id, module.id) == [
        "  introduction ", "A scenario", "Actors and Roles", "Normal Operation", "Degraded Operation", "Emergency",
        "Maintenance",
    ]
    assert intro.id in after and body.id in after

    snapshot = _snapshot(service.store.root / project.id)
    again = service.apply_module_skeleton(project.id, module.id)
    assert again["created"] == [] and again["headings"] == []
    assert _snapshot(service.store.root / project.id) == snapshot


def test_apply_skeleton_descends_into_existing_headings(service, tmp_path):
    project, module = _module_with_object_type(service)
    template = {
        "format_version": 1,
        "kind": "module",
        "id": "nested",
        "name": "Nested",
        "description": "",
        "module": {"name": "N", "prefix": "N", "description": "", "type": "operational_scenarios"},
        "attributes": [],
        "skeleton": [{"heading": "Normal Operation", "children": [{"heading": "Start of Mission"}]}],
    }
    path = service.store.root / "_templates" / "modules" / "nested.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(template, indent=2) + "\n")
    normal = service.add_requirement(project.id, module.id, {"object_text": "Normal Operation", "object_type": "Heading"})
    service.add_requirement(project.id, module.id, {"object_text": "Existing", "object_type": "Heading"}, normal.id)

    result = service.apply_module_skeleton(project.id, module.id, "user:nested")
    assert [(h["heading"], h["parent_id"], h["level"], h["under_existing"]) for h in result["headings"]] == [
        ("Start of Mission", normal.id, 2, True)
    ]
    assert _texts(service, project.id, module.id, normal.id) == ["Existing", "Start of Mission"]


def test_apply_skeleton_rest_and_websocket_parity(client, tmp_path):
    project = client.post("/projects", json={"name": "P"}).json()
    module = client.post(
        f"/projects/{project['id']}/modules",
        json={"name": "Tests", "prefix": "TC", "type": "test_cases"},
    ).json()
    client.post(
        f"/projects/{project['id']}/attributes",
        json={"id": "object_type", "name": "Object Type", "type": "enum", "choices": ["Heading", "Information"]},
    )
    base = f"/projects/{project['id']}/modules/{module['id']}/apply-skeleton"
    preview = client.post(f"{base}/preview", json={}).json()
    assert preview["template_id"] == "builtin:test_cases" and preview["headings"]

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        response = _rpc(ws, "module.preview_skeleton", {"project_id": project["id"], "id": module["id"]})
        assert response["result"]["headings"] == preview["headings"]

        applied = client.post(base, json={"template_id": None})
        assert applied.status_code == 200
        assert len(applied.json()["created"]) == preview["requirements"]
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": project["id"]}
        assert json.loads(observer.receive_text()) == {"event": "changed", "project_id": project["id"]}

        # The WebSocket call answers its sender and broadcasts to the other clients.
        response = _rpc(ws, "module.apply_skeleton", {"project_id": project["id"], "id": module["id"]}, 2)
        assert response["result"]["created"] == []
        assert json.loads(observer.receive_text()) == {"event": "changed", "project_id": project["id"]}

    missing = client.post(base, json={"template_id": "builtin:nope"})
    assert missing.status_code == 404
