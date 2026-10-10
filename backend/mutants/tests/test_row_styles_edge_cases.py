"""Edge cases of row styling (``Project.row_styles``): real git baselines,
history messages, null/missing request bodies, template rules, save-as-template
and DOCX rule precedence.

Git tests work in pytest's ``tmp_path`` (outside any repository);
``conftest.py`` isolates git from the user's configuration.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from reqman.git_repo import GitHistory, GitSettings
from reqman.history import FileChange, describe_changes
from reqman.service import ProjectService
from reqman.store import TEMPLATES_DIR_NAME, ProjectStore
from reqman.templates import parse_template

CHECKOUT = Path(__file__).resolve().parents[2]
CHANGED = "changed"

HEADING = {"attribute_id": "kind", "value": "Heading", "font_color": "#2f9e44", "background_color": None, "bold": False}
REQ_BOLD = {"attribute_id": "kind", "value": "Req", "font_color": None, "background_color": None, "bold": True}


class Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


def git(path: Path, *args: str) -> str:
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=data, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    return data


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def git_service(root, clock):
    return ProjectService(ProjectStore(root), history=GitHistory(GitSettings(enabled=True), clock=clock))


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path / "data"))


def _client(monkeypatch, root: Path, git_on: bool) -> TestClient:
    monkeypatch.setenv("REQMAN_GIT", "on" if git_on else "off")
    monkeypatch.setenv("REQMAN_DATA_DIR", str(root))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(root.parent / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


@pytest.fixture
def client(root, monkeypatch):
    return _client(monkeypatch, root, git_on=False)


@pytest.fixture
def git_client(root, monkeypatch):
    return _client(monkeypatch, root, git_on=True)


def _rpc(ws, method, params, request_id=1):
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    return json.loads(ws.receive_text())


def _downgrade_to_v2(path: Path, stray_rules: list | None = None) -> None:
    """Rewrite project.json as format 2 (no ``row_styles``; optionally a stray one v2 ignored)."""
    data = json.loads(path.read_text())
    data["format_version"] = 2
    data.pop("row_styles", None)
    if stray_rules is not None:
        data["row_styles"] = stray_rules
    path.write_text(json.dumps(data, indent=2) + "\n")


def _module_entry(result: dict, module_id: str) -> dict:
    return next(m for m in result["modules"] if m["module_id"] == module_id)


# --- baseline compare through real git baselines ------------------------------------


def test_compare_real_baselines_styles_each_side_by_its_own_snapshot(git_service, root, clock):
    service = git_service
    project = service.create_project("Alpha")
    service.add_attribute_definition(project.id, "kind", "Kind", "enum", choices=["Heading", "Req"])
    module = service.create_module(project.id, "System", "SYRS")
    removed = service.add_requirement(project.id, module.id, {"object_text": "Gone", "kind": "Heading"})
    modified = service.add_requirement(project.id, module.id, {"object_text": "Old text", "kind": "Heading"})
    untouched = service.add_requirement(project.id, module.id, {"object_text": "Same", "kind": "Req"})

    # The "old" baseline predates format 3: project.json is format 2, and a
    # stray hand-edited row_styles key in it meant (and means) nothing.
    project_file = root / project.id / "project.json"
    _downgrade_to_v2(project_file, stray_rules=[HEADING])
    service.create_baseline(project.id, "old")
    old_shown = json.loads(git(root / project.id, "show", "refs/tags/baseline/old^{commit}:project.json"))
    assert old_shown["format_version"] == 2

    clock.offset = 3600
    service.set_row_styles(project.id, [HEADING, REQ_BOLD])
    service.remove_requirement(project.id, module.id, removed.id)
    service.update_requirement(project.id, module.id, modified.id, {"object_text": "New text"})
    added = service.add_requirement(project.id, module.id, {"object_text": "Added", "kind": "Heading"})
    service.create_baseline(project.id, "new")

    forward = _module_entry(service.compare_baselines(project.id, "old", "new"), module.id)
    assert forward["removed"] == [removed.id] and forward["added"] == [added.id]
    # Removed: the old side has no rules, so it is unstyled; untouched rows are not listed.
    assert forward["row_styles"] == {modified.id: HEADING, added.id: HEADING}
    assert untouched.id not in forward["row_styles"]

    backward = _module_entry(service.compare_baselines(project.id, "new", "old"), module.id)
    # Removed (from the "new" side) is styled; added/modified take the old side's (no) rules.
    assert backward["row_styles"] == {added.id: HEADING}

    # The baseline view reads each snapshot's own rules, not the live project's.
    service.set_row_styles(project.id, [REQ_BOLD])
    assert "row_styles" not in service.get_baseline(project.id, "old")["project"]
    assert service.get_baseline(project.id, "new")["project"]["row_styles"] == [HEADING, REQ_BOLD]

    # Current side: the live rules (Req bold only) style the modified requirement.
    service.update_requirement(project.id, module.id, untouched.id, {"object_text": "Changed now"})
    to_current = _module_entry(service.compare_baselines(project.id, "new", "current"), module.id)
    assert to_current["row_styles"] == {untouched.id: REQ_BOLD}

    # Neither side styled: no row_styles key at all.
    service.set_row_styles(project.id, [])
    old_to_current = service.compare_baselines(project.id, "old", "current")
    assert all("row_styles" not in m for m in old_to_current["modules"])


def test_compare_and_baseline_view_over_rest_and_ws_match(git_client, root):
    client = git_client
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    assert client.post(
        f"/projects/{project_id}/attributes",
        json={"id": "kind", "name": "Kind", "type": "enum", "choices": ["Heading", "Req"]},
    ).status_code == 200
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "M"}).json()["id"]
    reqs_url = f"/projects/{project_id}/modules/{module_id}/requirements"
    first = client.post(reqs_url, json={"attributes": {"object_text": "a", "kind": "Heading"}}).json()["id"]

    _downgrade_to_v2(root / project_id / "project.json")
    assert client.post(f"/projects/{project_id}/baselines", json={"name": "old"}).status_code == 200

    assert client.put(f"/projects/{project_id}/row-styles", json={"row_styles": [HEADING]}).status_code == 200
    client.put(f"{reqs_url}/{first}", json={"attributes": {"object_text": "b"}})
    second = client.post(reqs_url, json={"attributes": {"object_text": "c", "kind": "Req"}}).json()["id"]
    assert client.post(f"/projects/{project_id}/baselines", json={"name": "new"}).status_code == 200

    compare = client.get(f"/projects/{project_id}/baselines/compare?from=old&to=new")
    assert compare.status_code == 200
    entry = _module_entry(compare.json(), module_id)
    assert entry["row_styles"] == {first: HEADING}
    assert second in entry["added"] and second not in entry["row_styles"]

    old_view = client.get(f"/projects/{project_id}/baselines/old").json()
    new_view = client.get(f"/projects/{project_id}/baselines/new").json()
    assert "row_styles" not in old_view["project"]
    assert new_view["project"]["row_styles"] == [HEADING]

    with client.websocket_connect("/ws") as ws:
        ws_compare = _rpc(ws, "baseline.compare", {"project_id": project_id, "from": "old", "to": "new"})
        assert ws_compare["result"] == compare.json()
        ws_view = _rpc(ws, "baseline.get", {"project_id": project_id, "name": "new"}, 2)
        assert ws_view["result"] == new_view


# --- history ------------------------------------------------------------------------


def _project_json(rules=None) -> bytes:
    data = {"id": "p", "name": "P", "attributes": []}
    if rules is not None:
        data["row_styles"] = rules
    return (json.dumps(data, indent=2) + "\n").encode()


@pytest.mark.parametrize(
    "old, new",
    [
        (None, [HEADING]),  # added
        ([HEADING], [HEADING, REQ_BOLD]),  # extended
        ([HEADING, REQ_BOLD], [REQ_BOLD, HEADING]),  # reordered (order is precedence)
        ([HEADING], [{**HEADING, "bold": True}]),  # one field
        ([HEADING], None),  # cleared
    ],
)
def test_history_describes_row_style_changes(old, new):
    change = FileChange("project.json", _project_json(old), _project_json(new))
    assert describe_changes([change]) == ["Row styles changed"]


def test_history_has_no_row_style_entry_when_rules_are_unchanged():
    change = FileChange(
        "project.json",
        _project_json([HEADING]),
        json.dumps({"id": "p", "name": "Renamed", "attributes": [], "row_styles": [HEADING]}).encode(),
    )
    assert "Row styles changed" not in describe_changes([change])


def test_set_row_styles_commit_subject(git_service, root, clock):
    project = git_service.create_project("Alpha")
    git_service.add_attribute_definition(project.id, "kind", "Kind", "enum", choices=["Heading", "Req"])
    clock.offset = 3600
    git_service.set_row_styles(project.id, [HEADING])
    assert git_service.git_log(project.id)["entries"][0]["subject"] == "Row styles changed"


# --- null / missing body ------------------------------------------------------------


def _styled_project(client) -> str:
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    client.post(
        f"/projects/{project_id}/attributes",
        json={"id": "kind", "name": "Kind", "type": "enum", "choices": ["Heading", "Req"]},
    )
    assert client.put(f"/projects/{project_id}/row-styles", json={"row_styles": [HEADING]}).status_code == 200
    return project_id


@pytest.mark.parametrize("body", [{"row_styles": None}, {}], ids=["null", "missing"])
def test_rest_null_or_missing_row_styles_clears_and_broadcasts(client, root, body):
    project_id = _styled_project(client)
    path = root / project_id / "project.json"
    with client.websocket_connect("/ws") as observer:
        resp = client.put(f"/projects/{project_id}/row-styles", json=body)
        assert resp.status_code == 200
        assert "row_styles" not in resp.json()
        assert json.loads(observer.receive_text()) == {"event": CHANGED, "project_id": project_id}
    text = path.read_text()
    assert "row_styles" not in json.loads(text) and text.endswith("}\n")


@pytest.mark.parametrize(
    "params", [{"row_styles": None}, {}], ids=["null", "missing"]
)
def test_ws_null_or_missing_row_styles_clears_and_broadcasts(client, root, params):
    project_id = _styled_project(client)
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        response = _rpc(ws, "project.set_row_styles", {"id": project_id, **params})
        assert "error" not in response and "row_styles" not in response["result"]
        assert json.loads(observer.receive_text()) == {"event": CHANGED, "project_id": project_id}
    assert "row_styles" not in json.loads((root / project_id / "project.json").read_text())


def test_rest_literal_null_body_is_rejected_and_writes_nothing(client, root):
    project_id = _styled_project(client)
    path = root / project_id / "project.json"
    before = path.read_bytes()
    with client.websocket_connect("/ws") as observer:
        resp = client.put(
            f"/projects/{project_id}/row-styles", content=b"null", headers={"content-type": "application/json"}
        )
        assert 400 <= resp.status_code < 500
        assert _rpc(observer, "template.list", {}, 999)["id"] == 999  # no broadcast
    assert path.read_bytes() == before


def test_ws_missing_id_is_an_error_without_broadcast(client):
    project_id = _styled_project(client)
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        assert "error" in _rpc(ws, "project.set_row_styles", {"row_styles": []})
        assert "error" in _rpc(ws, "project.set_row_styles", {"id": "nope", "row_styles": []}, 2)
        assert _rpc(observer, "template.list", {}, 999)["id"] == 999
    assert client.get(f"/projects/{project_id}").json()["row_styles"] == [HEADING]


# --- system attributes --------------------------------------------------------------


def test_system_enum_attribute_is_rejected_and_ignored(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "kind", "Kind", "enum", choices=["Heading"])
    service.set_row_styles(project.id, [HEADING])
    path = service.store.root / project.id / "project.json"
    data = json.loads(path.read_text())
    next(a for a in data["attributes"] if a["id"] == "kind")["system"] = True
    path.write_text(json.dumps(data, indent=2) + "\n")

    before = path.read_bytes()
    with pytest.raises(ValueError, match="not an enum attribute"):
        service.set_row_styles(project.id, [HEADING])
    assert path.read_bytes() == before
    assert ProjectService._effective_row_styles(service.get_project(project.id)) == []


def test_link_mirror_and_object_text_attributes_are_rejected(service):
    project = service.create_project("P")
    a = service.create_module(project.id, "A", "A")
    b = service.create_module(project.id, "B", "B")
    config = service.create_link_configuration(project.id, "Satisfies", a.id, b.id)
    for attr_id in (config.id, "object_text"):
        with pytest.raises(ValueError, match="not an enum attribute"):
            service.set_row_styles(project.id, [{"attribute_id": attr_id, "value": "x", "bold": True}])


# --- template rules -----------------------------------------------------------------


def _template(rules, **overrides):
    data = {
        "format_version": 1,
        "name": "Styled",
        "attributes": [
            {"id": "kind", "name": "Kind", "type": "enum", "choices": ["A", "B"]},
            {"id": "note", "name": "Note", "type": "text"},
        ],
        "row_styles": rules,
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "rules, message",
    [
        ([{"attribute_id": "note", "value": "A", "bold": True}], "not an enum attribute"),
        ([{"attribute_id": "object_text", "value": "A", "bold": True}], "not an enum attribute"),
        ([{"attribute_id": "missing", "value": "A", "bold": True}], "unknown attribute"),
        (
            [{"attribute_id": "kind", "value": "A", "bold": True}, {"attribute_id": "kind", "value": "A", "font_color": "#000000"}],
            r"row_styles\[1\]: .*already has a row style",
        ),
        ([{"attribute_id": "kind", "value": "A"}], "sets no font color"),
    ],
)
def test_template_row_style_rules_are_enforced_on_import(service, rules, message):
    with pytest.raises(ValueError, match=message):
        service.import_template(json.dumps(_template(rules)))
    assert not (service.store.root / TEMPLATES_DIR_NAME).exists()


@pytest.mark.parametrize(
    "rule, message",
    [
        ({"attribute_id": "kind", "value": "A", "font_color": 123}, r"row_styles\[0\]: 'font_color' must be a color string"),
        ({"attribute_id": "kind", "value": "A", "background_color": ["#ffffff"]}, r"'background_color' must be a color string"),
        ({"attribute_id": "kind", "value": 1, "bold": True}, r"row_styles\[0\]: 'value' must be a string"),
        ({"attribute_id": "kind", "value": None, "bold": True}, r"'value' must be a string"),
        ({"attribute_id": 5, "value": "A", "bold": True}, "'attribute_id' must be a non-empty string"),
        ({"attribute_id": "kind", "value": "A", "bold": "true"}, "'bold' must be true or false"),
    ],
)
def test_parse_template_rejects_bad_row_style_shapes(rule, message):
    with pytest.raises(ValueError, match=message):
        parse_template(_template([rule]), "user:styled")


@pytest.mark.parametrize("value", ["oops", {"a": 1}, ["x"]])
def test_parse_template_rejects_non_list_or_non_object_row_styles(value):
    with pytest.raises(ValueError, match="row_styles"):
        parse_template(_template(value), "user:styled")


def test_parse_template_accepts_missing_or_null_row_styles():
    data = _template([])
    del data["row_styles"]
    assert parse_template(data, "user:styled").row_styles == []
    assert parse_template(_template(None), "user:styled").row_styles == []


def test_imported_template_round_trips_rules_and_creates_styled_project(service):
    rules = [
        {"attribute_id": "kind", "value": "B", "font_color": "#ABCDEF", "background_color": None, "bold": False},
        {"attribute_id": "kind", "value": "A", "font_color": None, "background_color": "#000000", "bold": True},
    ]
    summary = service.import_template(json.dumps(_template(rules)))
    project = service.create_project("From template", template_id=summary["id"])
    # Colors are normalized to lower case; order is kept.
    assert [s.to_dict() for s in project.row_styles] == [
        {**rules[0], "font_color": "#abcdef"},
        rules[1],
    ]


# --- save as template ---------------------------------------------------------------


def _template_file(service, summary) -> Path:
    return service.store.root / TEMPLATES_DIR_NAME / f"{summary['id'].split(':', 1)[1]}.json"


def test_save_as_template_drops_stale_rules(service):
    project = service.create_project("Src")
    service.add_attribute_definition(project.id, "kind", "Kind", "enum", choices=["Heading", "Req"])
    service.add_attribute_definition(project.id, "note", "Note")
    path = service.store.root / project.id / "project.json"
    data = json.loads(path.read_text())
    data["row_styles"] = [
        {"attribute_id": "kind", "value": "Gone", "bold": True},  # missing choice
        {"attribute_id": "missing", "value": "Heading", "bold": True},  # missing attribute
        {"attribute_id": "note", "value": "Heading", "bold": True},  # not an enum
        HEADING,
        {**HEADING, "bold": True},  # duplicate of an earlier pair
        {"attribute_id": "kind", "value": "Req", "font_color": "bad"},  # nothing left to set
        REQ_BOLD,
    ]
    path.write_text(json.dumps(data, indent=2) + "\n")

    summary = service.save_project_as_template(project.id, "Copy")
    text = _template_file(service, summary).read_text()
    saved = json.loads(text)
    assert text == json.dumps(saved, indent=2) + "\n"
    assert list(saved)[-1] == "row_styles"
    assert saved["row_styles"] == [HEADING, REQ_BOLD]
    copy = service.create_project("Copy", template_id=summary["id"])
    assert [s.to_dict() for s in copy.row_styles] == [HEADING, REQ_BOLD]


@pytest.mark.parametrize("stale_only", [False, True], ids=["no-rules", "stale-only"])
def test_save_as_template_without_usable_rules_writes_no_key(service, stale_only):
    project = service.create_project("Src")
    service.add_attribute_definition(project.id, "kind", "Kind", "enum", choices=["Heading"])
    if stale_only:
        path = service.store.root / project.id / "project.json"
        data = json.loads(path.read_text())
        data["row_styles"] = [{"attribute_id": "kind", "value": "Gone", "bold": True}]
        path.write_text(json.dumps(data, indent=2) + "\n")
    summary = service.save_project_as_template(project.id, "Copy")
    saved = json.loads(_template_file(service, summary).read_text())
    assert "row_styles" not in saved
    assert service.create_project("Copy", template_id=summary["id"]).row_styles == []


# --- DOCX ---------------------------------------------------------------------------


def _docx_paragraphs(service, project_id):
    document = Document(io.BytesIO(service.export_docx(project_id)))
    return document.paragraphs


def _rows(paragraphs, requirement_id):
    """The heading paragraph of ``requirement_id`` and its attribute paragraphs."""
    start = next(i for i, p in enumerate(paragraphs) if p.text == requirement_id)
    rows = [paragraphs[start]]
    for p in paragraphs[start + 1:]:
        if p.style.name.startswith("Heading"):
            break
        rows.append(p)
    return rows


def _fill(paragraph):
    ppr = paragraph._p.pPr
    shd = ppr.find(qn("w:shd")) if ppr is not None else None
    return shd.get(qn("w:fill")) if shd is not None else None


def _colors(paragraph):
    return {str(r.font.color.rgb) if r.font.color and r.font.color.rgb else None for r in paragraph.runs}


def _docx_project(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "kind", "Kind", "enum", choices=["Heading", "Req"])
    service.add_attribute_definition(project.id, "phase", "Phase", "enum", choices=["Draft", "Final"])
    module = service.create_module(project.id, "M", "M")
    return project.id, module.id


def test_docx_background_only_rule_shades_without_color_or_bold(service):
    project_id, module_id = _docx_project(service)
    styled = service.add_requirement(project_id, module_id, {"object_text": "Shaded", "kind": "Heading"})
    plain = service.add_requirement(project_id, module_id, {"object_text": "Plain", "kind": "Req"})
    service.set_row_styles(project_id, [{"attribute_id": "kind", "value": "Heading", "background_color": "#fff3bf"}])
    paragraphs = _docx_paragraphs(service, project_id)

    rows = _rows(paragraphs, styled.id)
    assert len(rows) >= 2 and all(_fill(p) == "FFF3BF" for p in rows)
    assert all(_colors(p) == {None} for p in rows)
    # Only the "<Attribute>: " label runs are bold, as in an unstyled export.
    for p in rows[1:]:
        assert [bool(r.bold) for r in p.runs] == [True, False]
    assert all(_fill(p) is None for p in _rows(paragraphs, plain.id))


def test_docx_bold_only_rule_bolds_every_run_without_color_or_shading(service):
    project_id, module_id = _docx_project(service)
    styled = service.add_requirement(project_id, module_id, {"object_text": "Bold", "kind": "Req"})
    plain = service.add_requirement(project_id, module_id, {"object_text": "Plain", "kind": "Heading"})
    service.set_row_styles(project_id, [REQ_BOLD])
    paragraphs = _docx_paragraphs(service, project_id)

    rows = _rows(paragraphs, styled.id)
    assert all(r.bold for p in rows for r in p.runs)
    assert all(_fill(p) is None and _colors(p) == {None} for p in rows)
    for p in _rows(paragraphs, plain.id)[1:]:
        assert [bool(r.bold) for r in p.runs] == [True, False]


def test_docx_first_matching_rule_wins_across_attributes(service):
    project_id, module_id = _docx_project(service)
    both = service.add_requirement(project_id, module_id, {"object_text": "Both", "kind": "Heading", "phase": "Draft"})
    kind_only = service.add_requirement(
        project_id, module_id, {"object_text": "Kind only", "kind": "Heading", "phase": "Final"}
    )
    red_draft = {"attribute_id": "phase", "value": "Draft", "font_color": "#e03131", "background_color": None, "bold": False}
    service.set_row_styles(project_id, [red_draft, HEADING])
    paragraphs = _docx_paragraphs(service, project_id)
    assert all(_colors(p) == {"E03131"} for p in _rows(paragraphs, both.id))
    assert all(_colors(p) == {"2F9E44"} for p in _rows(paragraphs, kind_only.id))

    # Reversing the rules reverses the winner.
    service.set_row_styles(project_id, [HEADING, red_draft])
    paragraphs = _docx_paragraphs(service, project_id)
    assert all(_colors(p) == {"2F9E44"} for p in _rows(paragraphs, both.id))
