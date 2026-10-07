"""Row styling by enum attribute value (``Project.row_styles``, project format 3)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from reqman.formats import PROJECT_FORMAT, upgrade
from reqman.models import Project, RowStyle
from reqman.service import MAX_ROW_STYLES, ProjectService
from reqman.store import ProjectStore

RBC = "builtin:rbc_template"
RBC_DEFAULTS = [
    {"attribute_id": "object_type", "value": "Heading", "font_color": "#2f9e44", "background_color": None, "bold": False},
    {"attribute_id": "object_type", "value": "Information", "font_color": "#1971c2", "background_color": None, "bold": False},
]


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


def _project(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "kind", "Kind", "enum", choices=["Heading", "Info", "Req"])
    service.add_attribute_definition(project.id, "note", "Note")
    return project.id


def _project_file(service, project_id):
    return service.store.root / project_id / "project.json"


# --- service ----------------------------------------------------------------------


def test_set_row_styles_round_trips_and_is_written_only_when_present(service):
    project_id = _project(service)
    path = _project_file(service, project_id)
    assert "row_styles" not in json.loads(path.read_text())

    rules = [
        {"attribute_id": "kind", "value": "Heading", "font_color": "#2F9E44", "bold": True},
        {"attribute_id": "kind", "value": "Info", "font_color": None, "background_color": "#e7f5ff", "bold": False},
    ]
    project = service.set_row_styles(project_id, rules)
    assert project.row_styles[0] == RowStyle("kind", "Heading", "#2f9e44", None, True)

    text = path.read_text()
    data = json.loads(text)
    assert text.endswith("}\n") and text == json.dumps(data, indent=2) + "\n"
    assert data["format_version"] == PROJECT_FORMAT.current == 3
    assert list(data)[-1] == "row_styles"
    assert data["row_styles"][0] == {
        "attribute_id": "kind", "value": "Heading", "font_color": "#2f9e44", "background_color": None, "bold": True,
    }
    reloaded = ProjectService(ProjectStore(service.store.root)).get_project(project_id)
    assert reloaded.row_styles == project.row_styles

    service.set_row_styles(project_id, [])
    assert "row_styles" not in json.loads(path.read_text())


@pytest.mark.parametrize(
    "rules, message",
    [
        ("x", "must be a list"),
        (["x"], r"row_styles\[0\]: must be an object"),
        ([{"attribute_id": "nope", "value": "Heading", "bold": True}], "unknown attribute"),
        ([{"attribute_id": "note", "value": "Heading", "bold": True}], "not an enum attribute"),
        ([{"attribute_id": "kind", "value": "Other", "bold": True}], "not a choice"),
        ([{"attribute_id": "kind", "value": "Heading", "font_color": "green"}], "#rrggbb"),
        ([{"attribute_id": "kind", "value": "Heading", "background_color": "#12345"}], "#rrggbb"),
        ([{"attribute_id": "kind", "value": "Heading", "bold": "yes"}], "'bold' must be true or false"),
        ([{"attribute_id": "kind", "value": "Heading"}], "sets no font color"),
        ([{"attribute_id": "kind", "value": "Heading", "bold": True, "italic": True}], "unknown key"),
        (
            [{"attribute_id": "kind", "value": "Heading", "bold": True}] * 2,
            r"row_styles\[1\]: .*already has a row style",
        ),
        ([{"attribute_id": "kind", "value": "Heading", "bold": True}] * (MAX_ROW_STYLES + 1), "at most"),
    ],
)
def test_invalid_row_styles_are_rejected_and_nothing_is_written(service, rules, message):
    project_id = _project(service)
    path = _project_file(service, project_id)
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        service.set_row_styles(project_id, rules)
    assert path.read_bytes() == before


def test_stale_hand_edited_rules_are_ignored_but_kept(service):
    project = Project.from_dict(
        {
            "id": "p", "name": "P",
            "attributes": [{"id": "kind", "name": "Kind", "type": "enum", "choices": ["A"]}],
            "row_styles": [
                {"attribute_id": "kind", "value": "Gone", "bold": True},
                {"attribute_id": "missing", "value": "A", "bold": True},
                {"attribute_id": "kind", "value": "A", "font_color": "not-a-color", "bold": True},
                "junk",
            ],
        }
    )
    assert len(project.row_styles) == 3
    assert project.row_styles[2].font_color is None
    assert ProjectService._effective_row_styles(project) == [RowStyle("kind", "A", None, None, True)]


# --- migration --------------------------------------------------------------------


def test_v2_project_migrates_without_row_styles_and_saves_as_v3(service):
    project_id = _project(service)
    path = _project_file(service, project_id)
    data = json.loads(path.read_text())
    data["format_version"] = 2
    data["row_styles"] = [{"attribute_id": "kind", "value": "Heading", "bold": True}]
    path.write_text(json.dumps(data, indent=2) + "\n")

    upgraded = upgrade(data, PROJECT_FORMAT, "Project 'p'")
    assert "row_styles" not in upgraded
    assert "row_styles" in data  # input not modified

    assert service.get_project(project_id).row_styles == []
    assert json.loads(path.read_text())["format_version"] == 2  # loading never rewrites

    service.create_module(project_id, "M", "M")
    saved = json.loads(path.read_text())
    assert saved["format_version"] == 3
    assert "row_styles" not in saved


# --- defaults and templates -------------------------------------------------------


def test_blank_project_has_no_rules_and_rbc_template_has_defaults(service):
    blank = service.create_project("Blank")
    assert blank.row_styles == []
    assert "row_styles" not in json.loads(_project_file(service, blank.id).read_text())

    rbc = service.create_project("Rbc", template_id=RBC)
    assert [s.to_dict() for s in rbc.row_styles] == RBC_DEFAULTS
    stored = json.loads(_project_file(service, rbc.id).read_text())
    assert stored["row_styles"] == RBC_DEFAULTS


def test_save_as_template_keeps_rules(service):
    source = service.create_project("Src", template_id=RBC)
    service.set_row_styles(
        source.id, RBC_DEFAULTS + [{"attribute_id": "status", "value": "Approved", "background_color": "#ebfbee"}]
    )
    saved = service.save_project_as_template(source.id, "Copy")
    copy = service.create_project("Copy", template_id=saved["id"])
    assert [s.to_dict() for s in copy.row_styles] == [s.to_dict() for s in service.get_project(source.id).row_styles]


@pytest.mark.parametrize(
    "rule, message",
    [
        ({"attribute_id": "kind", "value": "Nope", "bold": True}, "not a choice"),
        ({"attribute_id": "kind", "value": "A", "font_color": "red"}, "#rrggbb"),
        ({"attribute_id": "kind", "value": "A", "bold": 1}, "'bold' must be true or false"),
        ({"attribute_id": "", "value": "A", "bold": True}, "attribute_id"),
    ],
)
def test_template_with_bad_row_style_is_rejected(service, rule, message):
    template = {
        "format_version": 1,
        "name": "Styled",
        "attributes": [{"id": "kind", "name": "Kind", "type": "enum", "choices": ["A"]}],
        "row_styles": [rule],
    }
    with pytest.raises(ValueError, match=message):
        service.import_template(json.dumps(template))


# --- DOCX -------------------------------------------------------------------------


def test_docx_applies_row_styles(service):
    project_id = _project(service)
    module = service.create_module(project_id, "M", "M")
    service.add_requirement(project_id, module.id, {"object_text": "Styled heading", "kind": "Heading"})
    service.add_requirement(project_id, module.id, {"object_text": "Plain", "kind": "Req"})
    service.set_row_styles(
        project_id, [{"attribute_id": "kind", "value": "Heading", "font_color": "#2f9e44", "background_color": "#fff3bf", "bold": True}]
    )
    xml = zipfile.ZipFile(io.BytesIO(service.export_docx(project_id))).read("word/document.xml").decode()
    assert xml.count('w:color w:val="2F9E44"') >= 2  # heading and attribute paragraphs
    assert 'w:fill="FFF3BF"' in xml
    paragraphs = xml.split("<w:p>")
    styled = [p for p in paragraphs if "Styled heading" in p or ">Heading<" in p]
    plain = [p for p in paragraphs if ">Plain<" in p or ">Req<" in p]
    assert styled and all("<w:b/>" in p and "FFF3BF" in p for p in styled)
    assert plain and not any("2F9E44" in p or "FFF3BF" in p for p in plain)


# --- REST / WebSocket -------------------------------------------------------------


def test_rest_and_ws_parity_with_broadcast(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    client.post(
        f"/projects/{project_id}/attributes",
        json={"id": "kind", "name": "Kind", "type": "enum", "choices": ["Heading", "Info"]},
    )
    url = f"/projects/{project_id}/row-styles"
    changed = {"event": "changed", "project_id": project_id}
    rule = {"attribute_id": "kind", "value": "Heading", "font_color": "#2f9e44"}
    path = tmp_path / project_id / "project.json"

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.put(url, json={"row_styles": [rule]})
        assert resp.status_code == 200
        assert resp.json()["row_styles"] == [{**rule, "background_color": None, "bold": False}]
        assert json.loads(observer.receive_text()) == changed
        assert json.loads(ws.receive_text()) == changed

        response = _rpc(ws, "project.set_row_styles", {"id": project_id, "row_styles": []})
        assert "row_styles" not in response["result"]
        assert json.loads(observer.receive_text()) == changed

        before = path.read_bytes()
        bad = client.put(url, json={"row_styles": [{**rule, "value": "Nope"}]})
        assert bad.status_code == 400 and "not a choice" in bad.json()["detail"]
        ws_bad = _rpc(ws, "project.set_row_styles", {"id": project_id, "row_styles": [{**rule, "value": "Nope"}]}, 2)
        assert ws_bad["error"]["code"] == -32602 and "not a choice" in ws_bad["error"]["message"]
        assert path.read_bytes() == before
        # No broadcast for the failures: the next message is this reply.
        assert _rpc(observer, "template.list", {}, 999)["id"] == 999

    assert client.put("/projects/nope/row-styles", json={"row_styles": []}).status_code == 404


# --- baseline compare -------------------------------------------------------------


def test_compare_styles_each_listed_requirement_by_its_own_side():
    attributes = [{"id": "kind", "name": "Kind", "type": "enum", "choices": ["Heading", "Req"]}]

    def snapshot(rules, requirements):
        project = {"id": "p", "name": "P", "attributes": attributes}
        if rules is not None:
            project["row_styles"] = rules
        return {"project": project, "requirements": {"m": requirements}}

    heading = {"attribute_id": "kind", "value": "Heading", "font_color": "#2f9e44", "background_color": None, "bold": False}
    old = snapshot(None, {"M-1": {"attributes": {"kind": "Heading"}}, "M-2": {"attributes": {"kind": "Heading"}}})
    new = snapshot([heading], {"M-2": {"attributes": {"kind": "Heading"}}, "M-3": {"attributes": {"kind": "Req"}}})
    result = {"modules": [{"module_id": "m", "added": ["M-3"], "removed": ["M-1"], "modified": [{"id": "M-2"}]}]}

    ProjectService._add_compare_row_styles(result, old, new)
    # M-1 is removed (old side: no rules), M-3 is added but unstyled.
    assert result["modules"][0]["row_styles"] == {"M-2": heading}

    plain = {"modules": [{"module_id": "m", "added": [], "removed": ["M-1"], "modified": []}]}
    ProjectService._add_compare_row_styles(plain, old, snapshot(None, {}))
    assert "row_styles" not in plain["modules"][0]
