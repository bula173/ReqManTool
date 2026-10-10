"""Apply chapter template from a project template's module, and the remembered origin template.

Follow-up of Operational Scenarios / apply-skeleton: ``apply_module_skeleton``
also takes a *project* template id plus the key of one of its modules
(``builtin:rbc_template`` + ``syrs``), so an existing project gets that
template's specific chapters instead of the generic ones of a plain module
template. A project created from a template remembers it
(``Project.template_id``, project format 10) so the dialog can pre-select it.
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
from reqman.models import Project
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


def _headings(result) -> list[str]:
    return [h["heading"] for h in result["headings"]]


def _requirements_module(service, name="Spec", prefix="REQ"):
    """A blank project with one requirements module and the Object Type the skeletons need."""
    project = service.create_project("P")
    module = service.create_module(project.id, name, prefix, "", "requirements")
    service.add_attribute_definition(
        project.id, "object_type", "Object Type", "enum", choices=["Heading", "Information", "Requirement"]
    )
    return project, module


def _write_user_template(service, template_id: str, data: dict) -> None:
    path = service.store.root / "_templates" / f"{template_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Version 2: version 1 files drop `module_template` and `skeleton` keys.
    path.write_text(json.dumps({"format_version": 2, **data}, indent=2) + "\n")


def _texts(service, project_id, module_id, parent_id=None) -> list[str]:
    siblings = [r for r in service.list_requirements(project_id, module_id) if r.parent_id == parent_id]
    siblings.sort(key=lambda r: (r.order, r.id))
    return [richtext.to_plain(r.attributes.get("object_text") or "") for r in siblings]


# --- the project template's own chapters ------------------------------------------------


def test_apply_skeleton_takes_a_project_template_module_and_its_own_chapters(service):
    project, module = _requirements_module(service)

    preview = service.preview_module_skeleton(project.id, module.id, RBC, "syrs")
    assert preview["template_id"] == RBC and preview["module_key"] == "syrs"
    # The RBC-specific SyRS chapters, not the generic ones of builtin:requirements
    # ("Overview / Context" would be the generic title of that chapter): every
    # skeleton heading, the roots first and their children under them.
    assert [h["heading"] for h in preview["headings"] if h["level"] == 1] == [
        "Introduction",
        "System Overview / Context",
        "Functional Requirements",
        "Interface Requirements",
        "Performance Requirements",
        "RAM Requirements",
        "Safety Requirements",
        "Security Requirements",
        "Constraints",
        "Configuration and Data",
    ]
    levels = {h["heading"]: h["level"] for h in preview["headings"]}
    assert levels["Purpose"] == 2 and levels["SIL Allocation"] == 2  # the RBC sub-chapters
    assert "Overview / Context" not in levels
    assert service.list_requirements(project.id, module.id) == []  # the preview writes nothing

    result = service.apply_module_skeleton(project.id, module.id, RBC, "syrs")
    assert result["template_id"] == RBC and result["module_key"] == "syrs"
    assert _headings(result) == _headings(preview)
    assert len(result["created"]) > len(result["headings"])  # the informative texts come too
    assert "Safety Requirements" in _texts(service, project.id, module.id)

    again = service.apply_module_skeleton(project.id, module.id, RBC, "syrs")
    assert again["created"] == [] and again["headings"] == []


def test_apply_skeleton_descends_into_existing_headings_of_a_project_template_source(service):
    project, module = _requirements_module(service)
    intro = service.add_requirement(
        project.id, module.id, {"object_text": "Introduction", "object_type": "Heading"}
    )
    service.add_requirement(project.id, module.id, {"object_text": "Old", "object_type": "Heading"}, intro.id)

    result = service.apply_module_skeleton(project.id, module.id, RBC, "syrs")
    # "Introduction" already exists: it is descended into, its skeleton children added under it.
    added = {h["heading"]: h for h in result["headings"]}
    assert "Introduction" not in added and "System Overview / Context" in added
    children = {
        richtext.to_plain(r.attributes["object_text"])
        for r in service.list_requirements(project.id, module.id)
        if r.parent_id == intro.id
    }
    assert {"Old", "Purpose", "Scope"} <= children


def test_module_template_source_still_works_and_reports_no_module_key(service):
    project, module = _requirements_module(service, name="Tests", prefix="TC")
    result = service.apply_module_skeleton(project.id, module.id, "builtin:test_cases")
    assert result["template_id"] == "builtin:test_cases" and result["module_key"] is None
    assert "Introduction" in _headings(result)


def test_project_template_module_without_an_inline_skeleton_uses_its_module_template(service):
    _write_user_template(
        service,
        "chapters",
        {
            "id": "chapters",
            "name": "Chapters",
            "modules": [{"key": "spec", "name": "Spec", "prefix": "REQ", "module_template": "builtin:requirements"}],
        },
    )
    project, module = _requirements_module(service)

    preview = service.preview_module_skeleton(project.id, module.id, "user:chapters", "spec")
    assert "Overview / Context" in _headings(preview)  # the generic module template's title
    assert "System Overview / Context" not in _headings(preview)


def test_project_template_module_without_any_skeleton_adds_nothing(service):
    _write_user_template(
        service,
        "bare",
        {"id": "bare", "name": "Bare", "modules": [{"key": "spec", "name": "Spec", "prefix": "REQ"}]},
    )
    project, module = _requirements_module(service)

    preview = service.preview_module_skeleton(project.id, module.id, "user:bare", "spec")
    assert preview["headings"] == [] and preview["requirements"] == 0
    assert service.apply_module_skeleton(project.id, module.id, "user:bare", "spec")["created"] == []


# --- what is refused --------------------------------------------------------------


@pytest.mark.parametrize(
    "template_id, module_key, message",
    [
        (RBC, "nope", "has no module 'nope'"),
        (RBC, "", "module key must be a non-empty string"),
        (RBC, "   ", "module key must be a non-empty string"),
        (RBC, 3, "module key must be a non-empty string"),
        (None, "syrs", "A project template id is required together with a module key"),
    ],
)
def test_a_bad_project_template_source_is_a_value_error(service, template_id, module_key, message):
    project, module = _requirements_module(service)
    with pytest.raises(ValueError) as excinfo:
        service.preview_module_skeleton(project.id, module.id, template_id, module_key)
    assert message in str(excinfo.value)


def test_an_unknown_template_is_still_a_missing_file(service):
    project, module = _requirements_module(service)
    # A missing template stays 404 (FileNotFoundError), with or without a module key.
    with pytest.raises(FileNotFoundError):
        service.preview_module_skeleton(project.id, module.id, "builtin:nope", "syrs")
    # Module and project templates are separate namespaces: a module template
    # id is not readable as a project template.
    with pytest.raises(FileNotFoundError) as excinfo:
        service.preview_module_skeleton(project.id, module.id, "builtin:requirements", "requirements")
    assert "not found" in str(excinfo.value)


def test_a_dangling_module_template_reference_names_the_module(service):
    _write_user_template(
        service,
        "broken",
        {
            "id": "broken",
            "name": "Broken",
            "modules": [{"key": "spec", "name": "Spec", "prefix": "REQ", "module_template": "user:nope"}],
        },
    )
    project, module = _requirements_module(service)
    with pytest.raises(ValueError) as excinfo:
        service.preview_module_skeleton(project.id, module.id, "user:broken", "spec")
    assert "module 'spec'" in str(excinfo.value) and "user:nope" in str(excinfo.value)


def test_an_unknown_module_is_404_and_the_module_is_untouched(service):
    project, module = _requirements_module(service)
    with pytest.raises(FileNotFoundError):
        service.apply_module_skeleton(project.id, "no-module", RBC, "syrs")
    with pytest.raises(FileNotFoundError):
        service.apply_module_skeleton("no-project", module.id, RBC, "syrs")


# --- REST / WebSocket parity -------------------------------------------------------


def test_project_template_source_rest_and_websocket_parity(client):
    project = client.post("/projects", json={"name": "P"}).json()
    module = client.post(
        f"/projects/{project['id']}/modules",
        json={"name": "Spec", "prefix": "REQ", "type": "requirements"},
    ).json()
    client.post(
        f"/projects/{project['id']}/attributes",
        json={"id": "object_type", "name": "Object Type", "type": "enum", "choices": ["Heading", "Information"]},
    )
    base = f"/projects/{project['id']}/modules/{module['id']}/apply-skeleton"
    body = {"template_id": RBC, "module_key": "syrs"}

    preview = client.post(f"{base}/preview", json=body).json()
    assert preview["template_id"] == RBC and preview["module_key"] == "syrs"
    assert "System Overview / Context" in [h["heading"] for h in preview["headings"]]

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        response = _rpc(
            ws, "module.preview_skeleton",
            {"project_id": project["id"], "id": module["id"], **body},
        )
        assert response["result"] == preview

        applied = client.post(base, json=body)
        assert applied.status_code == 200
        assert len(applied.json()["created"]) == preview["requirements"]
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": project["id"]}
        assert json.loads(observer.receive_text()) == {"event": "changed", "project_id": project["id"]}

        response = _rpc(
            ws, "module.apply_skeleton",
            {"project_id": project["id"], "id": module["id"], **body}, 2,
        )
        assert response["result"]["created"] == []
        assert json.loads(observer.receive_text()) == {"event": "changed", "project_id": project["id"]}

    bad = client.post(f"{base}/preview", json={"template_id": RBC, "module_key": "nope"})
    assert bad.status_code == 400 and "has no module 'nope'" in bad.json()["detail"]
    missing = client.post(f"{base}/preview", json={"template_id": "builtin:nope", "module_key": "syrs"})
    assert missing.status_code == 404

    with client.websocket_connect("/ws") as ws:
        response = _rpc(
            ws, "module.preview_skeleton",
            {"project_id": project["id"], "id": module["id"], "template_id": RBC, "module_key": "nope"},
        )
        assert response["error"] == {"code": -32602, "message": "Template 'builtin:rbc_template' has no module 'nope'"}
        response = _rpc(
            ws, "module.preview_skeleton",
            {"project_id": project["id"], "id": module["id"], "template_id": "builtin:nope", "module_key": "syrs"},
        )
        assert response["error"]["code"] == -32001


# --- the remembered origin template -------------------------------------------------


def test_a_project_created_from_a_template_remembers_it(service):
    project = service.create_project("From RBC", template_id=RBC)
    assert project.template_id == RBC

    stored = json.loads((service.store.root / project.id / "project.json").read_text())
    assert stored["template_id"] == RBC
    assert stored["format_version"] == PROJECT_FORMAT.current
    # Written only when set, right after the lock: a blank project gains no key.
    assert "locked" not in stored and list(stored).index("template_id") == 4
    assert service.get_project(project.id).template_id == RBC

    blank = service.create_project("Blank")
    assert blank.template_id is None
    stored_blank = json.loads((service.store.root / blank.id / "project.json").read_text())
    assert "template_id" not in stored_blank
    assert stored_blank["format_version"] == PROJECT_FORMAT.current


def test_template_id_round_trips_and_reads_only_a_non_empty_string():
    assert Project.from_dict({"id": "a", "name": "A", "template_id": "user:x"}).template_id == "user:x"
    assert Project.from_dict({"id": "a", "name": "A", "template_id": ""}).template_id is None
    assert Project.from_dict({"id": "a", "name": "A", "template_id": 3}).template_id is None
    assert Project.from_dict({"id": "a", "name": "A"}).template_id is None

    project = Project(id="a", name="A", template_id="builtin:rbc_template")
    assert "template_id" not in Project(id="a", name="A").to_dict()
    assert Project.from_dict(project.to_dict()).template_id == "builtin:rbc_template"
    assert project.to_dict()["template_id"] == "builtin:rbc_template"


def test_an_older_project_file_forgets_a_stray_template_id(service):
    project = service.create_project("From RBC", template_id=RBC)
    path = service.store.root / project.id / "project.json"
    stored = json.loads(path.read_text())
    stored["format_version"] = PROJECT_FORMAT.current - 1
    path.write_text(json.dumps(stored, indent=2) + "\n")

    assert service.get_project(project.id).template_id is None  # v9 had no such key
    service.rename_project(project.id, "Renamed")
    reloaded = json.loads(path.read_text())
    assert reloaded["format_version"] == PROJECT_FORMAT.current and "template_id" not in reloaded

    # The migration itself: a stray key in an older file means nothing.
    upgraded = upgrade({**stored, "template_id": RBC}, PROJECT_FORMAT, "Project 'x'")
    assert "template_id" not in upgraded


def test_rest_returns_the_template_the_project_was_created_from(client):
    created = client.post("/projects", json={"name": "P", "template_id": RBC}).json()
    assert created["template_id"] == RBC
    assert client.get(f"/projects/{created['id']}").json()["template_id"] == RBC

    blank = client.post("/projects", json={"name": "Blank"}).json()
    assert "template_id" not in blank
    assert "template_id" not in client.get(f"/projects/{blank['id']}").json()
