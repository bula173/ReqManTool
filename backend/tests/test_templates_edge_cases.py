"""Edge cases for project templates and the subproject tree.

Complements ``test_templates.py``: REST/WebSocket parity for error cases,
all-or-nothing template creation, template validation rules, user template
files, save-as-template round trips and JSON format, hand-edited parent
data, and live-sync broadcasts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json
import re

import pytest
from fastapi.testclient import TestClient

from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore
from reqman.templates import BUILTIN_TEMPLATES_DIR

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


def _write_template(directory: Path, stem: str, data) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.json"
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
        path.write_text(text, encoding="utf-8")
    return path


def _minimal_template(**overrides):
    data = {
        "format_version": 1,
        "id": "mini",
        "name": "Mini",
        "attributes": [{"id": "status", "name": "Status", "type": "enum", "choices": ["A", "B"], "default": "A"}],
        "modules": [{"key": "a", "name": "Alpha", "prefix": "A"}, {"key": "b", "name": "Beta", "prefix": "B"}],
        "link_configurations": [{"key": "ab", "name": "Refines", "source_module": "a", "target_module": "b"}],
        "views": [{"name": "Main", "attribute_ids": ["object_text", "status", "link:ab"]}],
    }
    data.update(overrides)
    return data


def _snapshot(directory: Path) -> dict[str, bytes]:
    """Every file below ``directory`` (relative path -> bytes)."""
    return {
        str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()
    }


def _structure(service: ProjectService, project_id: str) -> dict:
    """A project's structure, independent of its id/name."""
    project = service.get_project(project_id)
    return {
        "attributes": [a.to_dict() for a in project.attributes],
        "link_configurations": [c.to_dict() for c in project.link_configurations],
        "views": [v.to_dict() for v in project.views],
        "modules": [m.to_dict() for m in service.list_modules(project_id)],
    }


def _set_raw_parent(service: ProjectService, project_id: str, parent) -> None:
    path = service.store.root / project_id / "project.json"
    data = json.loads(path.read_text())
    data["parent_project_id"] = parent
    path.write_text(json.dumps(data, indent=2) + "\n")


# --- REST / WebSocket parity for error cases ---------------------------------------


def test_set_parent_errors_match_between_rest_and_ws_and_never_broadcast(client):
    a = client.post("/projects", json={"name": "A"}).json()["id"]
    b = client.post("/projects", json={"name": "B", "parent_project_id": a}).json()["id"]
    c = client.post("/projects", json={"name": "C", "parent_project_id": b}).json()["id"]
    before = client.get("/projects").json()
    cases = [
        ("missing", None, 404, -32001),  # project does not exist
        (a, "missing", 404, -32001),  # parent does not exist
        (a, "_templates", 404, -32001),  # reserved folder is never a project
        (a, a, 400, -32602),  # self
        (a, b, 400, -32602),  # direct child
        (a, c, 400, -32602),  # grandchild: longer cycle
        (b, "", 400, -32602),  # empty string is not "top level"
    ]
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        for index, (project_id, parent, status, code) in enumerate(cases):
            resp = client.put(f"/projects/{project_id}/parent", json={"parent_project_id": parent})
            assert resp.status_code == status, (project_id, parent, resp.text)
            response = _rpc(ws1, "project.set_parent", {"id": project_id, "parent_project_id": parent}, index)
            assert response["error"]["code"] == code, (project_id, parent, response)
            assert response["error"]["message"] == resp.json()["detail"]

        # No failure broadcast anything: the first event either client sees is this one.
        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": marker}
        assert json.loads(ws1.receive_text()) == {"event": "changed", "project_id": marker}

    after = [p for p in client.get("/projects").json() if p["id"] != marker]
    assert after == before


@pytest.mark.parametrize("bad", [5, True, ["a"], {"id": "a"}])
def test_non_string_template_and_parent_ids_are_rejected(client, bad):
    a = client.post("/projects", json={"name": "A"}).json()["id"]
    with client.websocket_connect("/ws") as ws:
        assert _rpc(ws, "project.set_parent", {"id": a, "parent_project_id": bad})["error"]["code"] == -32602
        assert _rpc(ws, "project.create", {"name": "X", "parent_project_id": bad}, 2)["error"]["code"] == -32602
        assert _rpc(ws, "project.create", {"name": "X", "template_id": bad}, 3)["error"]["code"] == -32602
    # REST rejects the wrong JSON type at request validation.
    assert client.put(f"/projects/{a}/parent", json={"parent_project_id": bad}).status_code == 422
    assert client.post("/projects", json={"name": "X", "parent_project_id": bad}).status_code == 422
    assert client.post("/projects", json={"name": "X", "template_id": bad}).status_code == 422
    projects = client.get("/projects").json()
    assert [p["id"] for p in projects] == [a]
    assert "parent_project_id" not in projects[0]


@pytest.mark.parametrize(
    "template_id, parent, status, code",
    [
        ("rbc_template", None, 400, -32602),  # not namespaced
        ("", None, 400, -32602),
        ("builtin:", None, 400, -32602),
        ("other:rbc_template", None, 400, -32602),
        ("user:nope", None, 404, -32001),
        ("builtin:nope", None, 404, -32001),
        ("builtin:../templates/rbc_template", None, 404, -32001),  # never joined into a path
        ("user:../../config", None, 404, -32001),
        (RBC, "missing", 404, -32001),
        (RBC, "", 400, -32602),
        (None, "", 400, -32602),
    ],
)
def test_create_project_errors_match_between_rest_and_ws(client, template_id, parent, status, code):
    body = {"name": "X", "template_id": template_id, "parent_project_id": parent}
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        resp = client.post("/projects", json=body)
        assert resp.status_code == status, resp.text
        response = _rpc(ws1, "project.create", body)
        assert response["error"]["code"] == code
        assert response["error"]["message"] == resp.json()["detail"]
        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": marker}
    assert [p["id"] for p in client.get("/projects").json()] == ["marker"]


# --- all-or-nothing creation from a template ---------------------------------------


def _publish_after_target_appears(monkeypatch, make_target):
    original = ProjectStore.publish_staged_project

    def racing_publish(self, staging, project_id):
        make_target(self.root / project_id)
        return original(self, staging, project_id)

    monkeypatch.setattr(ProjectStore, "publish_staged_project", racing_publish)


def test_publish_conflict_keeps_the_path_that_appeared_and_leaves_no_staging(service, monkeypatch):
    def make_target(path: Path):
        path.mkdir()
        (path / "notes.txt").write_text("someone else's file\n")

    _publish_after_target_appears(monkeypatch, make_target)
    with pytest.raises(ValueError, match="already exists; try again"):
        service.create_project("RBC", template_id=RBC)
    root = service.store.root
    assert sorted(p.name for p in root.iterdir()) == ["rbc"]
    assert _snapshot(root / "rbc") == {"notes.txt": b"someone else's file\n"}
    assert service.list_projects() == []

    monkeypatch.undo()
    # The taken path is skipped next time.
    assert service.create_project("RBC", template_id=RBC).id == "rbc-2"
    assert not (root / ".reqman-staging").exists()


def test_publish_conflict_with_a_concurrently_created_project_does_not_touch_it(service, monkeypatch):
    other = ProjectService(ProjectStore(service.store.root))

    _publish_after_target_appears(monkeypatch, lambda path: other.create_project("RBC"))
    with pytest.raises(ValueError, match="try again"):
        service.create_project("RBC", template_id=RBC)
    root = service.store.root
    project = service.get_project("rbc")
    assert project.attributes[0].id == "object_text" and len(project.attributes) == 1
    assert service.list_modules("rbc") == []
    assert sorted(p.name for p in root.iterdir()) == ["rbc"]


def test_rest_publish_conflict_returns_400_and_does_not_broadcast(client, monkeypatch, tmp_path):
    _publish_after_target_appears(monkeypatch, lambda path: path.mkdir())
    with client.websocket_connect("/ws") as ws:
        resp = client.post("/projects", json={"name": "RBC", "template_id": RBC})
        assert resp.status_code == 400
        assert "try again" in resp.json()["detail"]
        monkeypatch.undo()
        created = client.post("/projects", json={"name": "Next"}).json()["id"]
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": created}
    assert not (tmp_path / ".reqman-staging").exists()
    assert [p["id"] for p in client.get("/projects").json()] == ["next"]


@pytest.mark.parametrize("method", ["save_module", "save_link_pairs"])
def test_failed_staged_write_of_modules_or_links_leaves_nothing(service, monkeypatch, method):
    original = getattr(ProjectStore, method)
    calls = []

    def failing(self, *args, **kwargs):
        if ".reqman-staging" in str(self.root):
            calls.append(args)
            if len(calls) == 3:  # after some files were already written
                raise OSError("disk full")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ProjectStore, method, failing)
    with pytest.raises(OSError, match="disk full"):
        service.create_project("RBC", template_id=RBC)
    assert len(calls) == 3
    assert list(service.store.root.iterdir()) == []
    assert service.list_projects() == []

    monkeypatch.undo()
    project = service.create_project("RBC", template_id=RBC)
    assert project.id == "rbc"
    assert sorted(p.name for p in service.store.root.iterdir()) == ["rbc"]


def test_taken_non_project_path_is_skipped_for_template_projects(service):
    plain = service.store.root / "rbc"
    plain.mkdir()
    (plain / "keep.txt").write_text("x\n")
    project = service.create_project("RBC", template_id=RBC)
    assert project.id == "rbc-2"
    assert _snapshot(plain) == {"keep.txt": b"x\n"}
    assert [p.id for p in service.list_projects()] == ["rbc-2"]


def test_stale_staging_leftover_is_never_a_project_and_is_not_removed(service):
    stale = service.store.root / ".reqman-staging" / "crashed" / "ghost"
    stale.mkdir(parents=True)
    (stale / "project.json").write_text('{"id": "ghost", "name": "Ghost"}\n')
    (service.store.root / ".reqman-staging" / "project.json").write_text('{"id": "x", "name": "X"}\n')

    service.create_project("RBC", template_id=RBC)
    assert [p.id for p in service.list_projects()] == ["rbc"]
    with pytest.raises(FileNotFoundError):
        service.get_project(".reqman-staging")
    # Only this creation's own staging folder was removed.
    assert sorted(p.name for p in (service.store.root / ".reqman-staging").iterdir()) == ["crashed", "project.json"]


@pytest.mark.parametrize("name, expected_id", [("_templates", "templates"), (".reqman-staging", "reqman-staging")])
def test_reserved_names_get_a_different_id_when_created_from_a_template(service, name, expected_id):
    project = service.create_project(name, template_id=RBC)
    assert project.id == expected_id
    assert project.name == name
    assert [p.id for p in service.list_projects()] == [expected_id]


# --- template validation rules ------------------------------------------------------

_ENUM = {"id": "status", "name": "Status", "type": "enum", "choices": ["A", "B"], "default": "A"}


@pytest.mark.parametrize(
    "overrides, message",
    [
        (
            {"link_configurations": [{"key": "x", "name": "L", "source_module": "zzz", "target_module": "b"}]},
            "'source_module' 'zzz' is not a module key of this template",
        ),
        (
            {"link_configurations": [{"key": "x", "name": "L", "source_module": 5, "target_module": "b"}]},
            "'source_module' 5 is not a module key",
        ),
        (
            {"modules": [{"key": "a", "name": "Alpha", "prefix": "A"}, {"key": "b", "name": " alpha ", "prefix": "B"}]},
            "duplicate module name 'alpha'",
        ),
        (
            {
                "link_configurations": [
                    {"key": "ab", "name": "L1", "source_module": "a", "target_module": "b"},
                    {"key": "ab", "name": "L2", "source_module": "b", "target_module": "a"},
                ],
                "views": [],
            },
            "duplicate link configuration key 'ab'",
        ),
        (
            {"views": [{"name": "Main", "attribute_ids": []}, {"name": "MAIN ", "attribute_ids": []}]},
            "duplicate view name 'MAIN'",
        ),
        ({"attributes": [_ENUM, {"id": "x", "name": "X", "type": "float"}]}, "Unknown attribute type 'float'"),
        (
            {"attributes": [_ENUM, {"id": "x", "name": "X", "type": "integer", "default": "5"}]},
            "Invalid default value: X: '5' is not an integer",
        ),
        (
            {"attributes": [{"id": "status", "name": "Status", "type": "enum", "choices": ["A"], "default": "C"}]},
            "Invalid default value: Status: 'C' is not one of ['A']",
        ),
        (
            {"attributes": [_ENUM, {"id": "x", "name": "X", "type": "string", "choices": ["a"]}]},
            "Attribute 'X' of type 'string' cannot have choices",
        ),
        (
            {"attributes": [{"id": "status", "name": "Status", "type": "enum", "choices": "AB"}]},
            "Enum attribute 'Status' needs a non-empty list of choices",
        ),
        (
            {"attributes": [_ENUM, {"id": "x", "name": "X", "required": True, "default": "  "}]},
            "is required, so its default must not be empty",
        ),
        ({"attributes": [_ENUM, {"id": "x", "name": "X", "required": "yes"}]}, "'required' must be true or false"),
        ({"attributes": [_ENUM, {"id": "link:ab", "name": "X"}]}, "must not start with 'link:'"),
        ({"attributes": [_ENUM, {"id": "x", "name": "X", "system": True}]}, "cannot be system attributes"),
        ({"attributes": [_ENUM, {"id": "object_text", "name": "Text"}]}, "attributes[1]: 'object_text' is a built-in attribute"),
        ({"attributes": [_ENUM, {"id": "status", "name": "Other"}]}, "attributes[1]: duplicate attribute id 'status'"),
        ({"attributes": [_ENUM, {"id": "", "name": "X"}]}, "'id' must be a non-empty string"),
        (
            {"link_configurations": [{"key": "ab", "name": "status", "source_module": "a", "target_module": "b"}]},
            "Link name 'status' is already used by attribute 'status'",
        ),
        (
            {"link_configurations": [{"key": "ab", "name": "Parent", "source_module": "a", "target_module": "b"}]},
            "Link name 'Parent' is reserved",
        ),
        ({"views": [{"name": "V", "attribute_ids": ["link:zz"]}]}, "'link:zz' does not name a link configuration key"),
        ({"views": [{"name": "V", "attribute_ids": ["link:"]}]}, "'link:' does not name a link configuration key"),
        ({"views": [{"name": "V", "attribute_ids": "object_text"}]}, "'attribute_ids' must be a list of strings"),
        ({"views": [{"name": "V", "attribute_ids": [1]}]}, "'attribute_ids' must be a list of strings"),
        ({"views": [{"attribute_ids": []}]}, "views[0]: 'name' must be a non-empty string"),
        ({"format_version": "1"}, "'format_version' must be the integer 1, got '1'"),
        ({"format_version": True}, "'format_version' must be the integer 1, got True"),
        ({"format_version": 1.0}, "'format_version' must be the integer 1, got 1.0"),
        ({"format_version": 0}, "Unsupported format_version 0"),
        ({"name": "  "}, "'name' must be a non-empty string"),
        ({"description": 5}, "'description' must be a string"),
        ({"modules": {"a": {}}}, "'modules' must be a list"),
        ({"modules": ["a"]}, "modules[0] must be an object"),
        ({"modules": [{"key": "a", "name": "Alpha"}], "link_configurations": [], "views": []}, "'prefix' must be a string"),
        ({"modules": [{"name": "Alpha", "prefix": "A"}]}, "modules[0]: 'key' must be a non-empty string"),
        ({"modules": [{"key": "a", "prefix": "A"}]}, "modules[0]: 'name' must be a non-empty string"),
        ({"link_configurations": [{"name": "L", "source_module": "a", "target_module": "b"}]}, "'key' must be a non-empty string"),
    ],
)
def test_each_template_rule_is_reported_in_the_listing_and_on_create(service, overrides, message):
    templates_dir = service.store.root / "_templates"
    _write_template(templates_dir, "broken", _minimal_template(**overrides))
    _write_template(templates_dir, "good", _minimal_template(name="Good"))

    by_id = {t["id"]: t for t in service.list_templates()}
    broken = by_id["user:broken"]
    assert broken["error"] is not None and message in broken["error"], broken["error"]
    assert broken["modules"] == []
    assert by_id["user:good"]["error"] is None
    assert by_id[RBC]["error"] is None

    with pytest.raises(ValueError, match=re.escape(message)) as excinfo:
        service.create_project("X", template_id="user:broken")
    assert broken["error"] in str(excinfo.value)
    assert sorted(p.name for p in service.store.root.iterdir()) == ["_templates"]


def test_listed_template_error_does_not_name_an_internal_project(service):
    attributes = [_ENUM, {"id": "status", "name": "Other"}]
    _write_template(service.store.root / "_templates", "broken", _minimal_template(attributes=attributes))
    listed = next(t for t in service.list_templates() if t["id"] == "user:broken")["error"]
    assert "template-check" not in listed and "on project" not in listed
    assert listed == "attributes[1]: duplicate attribute id 'status'"
    with pytest.raises(ValueError) as excinfo:
        service.create_project("X", template_id="user:broken")
    assert listed in str(excinfo.value) and "on project" not in str(excinfo.value)


def test_listing_an_invalid_template_does_not_log_warnings(service, caplog):
    _write_template(service.store.root / "_templates", "broken", _minimal_template(name=" "))
    caplog.set_level("DEBUG", logger="reqman.service")
    for _ in range(3):
        assert next(t for t in service.list_templates() if t["id"] == "user:broken")["error"]
    assert [r for r in caplog.records if r.levelno >= 30] == []
    assert any(r.levelname == "DEBUG" and "user:broken" in r.getMessage() for r in caplog.records)


def test_missing_format_version_and_non_object_template_are_invalid(service):
    templates_dir = service.store.root / "_templates"
    data = _minimal_template()
    del data["format_version"]
    _write_template(templates_dir, "noversion", data)
    _write_template(templates_dir, "array", "[1, 2]\n")
    _write_template(templates_dir, "string", '"Mini"\n')
    by_id = {t["id"]: t for t in service.list_templates()}
    assert "'format_version' must be the integer 1, got None" in by_id["user:noversion"]["error"]
    assert by_id["user:noversion"]["name"] == "Mini"  # readable name is still shown
    assert "must be a JSON object" in by_id["user:array"]["error"]
    assert by_id["user:array"]["name"] == "array"
    assert by_id["user:string"]["name"] == "string"
    for template_id in ("user:noversion", "user:array", "user:string"):
        with pytest.raises(ValueError, match="is invalid"):
            service.create_project("X", template_id=template_id)
    assert service.list_projects() == []


def test_unknown_keys_at_every_level_are_ignored(service):
    data = _minimal_template(future_top_level={"nested": [1, 2]})
    data["attributes"][0].update({"description": "ignored", "system": False, "ui": {"width": 3}})
    data["modules"][0]["type"] = "requirements"
    data["modules"][1]["type"] = "tests"
    data["link_configurations"][0]["type"] = "verifies"
    data["views"][0]["sort"] = "id"
    _write_template(service.store.root / "_templates", "future", data)

    entry = next(t for t in service.list_templates() if t["id"] == "user:future")
    assert entry["error"] is None
    assert entry["modules"] == [{"key": "a", "name": "Alpha", "prefix": "A"}, {"key": "b", "name": "Beta", "prefix": "B"}]
    project = service.create_project("P", template_id="user:future")
    assert [(m.id, m.name, m.prefix) for m in service.list_modules(project.id)] == [
        ("alpha", "Alpha", "A"),
        ("beta", "Beta", "B"),
    ]
    config = project.link_configurations[0]
    assert (config.name, config.source_module_id, config.target_module_id) == ("Refines", "alpha", "beta")
    # The view's "link:ab" is mapped to the real mirror attribute id.
    assert project.views[0].attribute_ids == ["object_text", "status", config.id]
    stored = (service.store.root / project.id / "project.json").read_text()
    assert "future_top_level" not in stored and '"type": "requirements"' not in stored


# --- user template files ----------------------------------------------------------


def test_non_utf8_user_template_is_listed_with_error_and_rejected(client, tmp_path):
    templates_dir = tmp_path / "_templates"
    text = json.dumps(_minimal_template(name="Zażółć"), ensure_ascii=False, indent=2) + "\n"
    _write_template(templates_dir, "cp1250", text.encode("cp1250"))
    _write_template(templates_dir, "good", _minimal_template(name="Good"))

    by_id = {t["id"]: t for t in client.get("/templates").json()}
    entry = by_id["user:cp1250"]
    assert entry["error"] and entry["name"] == "cp1250" and entry["modules"] == []
    assert by_id["user:good"]["error"] is None
    assert by_id[RBC]["error"] is None

    resp = client.post("/projects", json={"name": "X", "template_id": "user:cp1250"})
    assert resp.status_code == 400
    assert entry["error"] in resp.json()["detail"]
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "project.create", {"name": "X", "template_id": "user:cp1250"})
        assert response["error"] == {"code": -32602, "message": resp.json()["detail"]}
        listed = _rpc(ws, "template.list", {}, 2)["result"]
    assert listed == client.get("/templates").json()
    assert client.get("/projects").json() == []


def test_hidden_temporary_and_non_json_files_in_templates_dir_are_ignored(service):
    templates_dir = service.store.root / "_templates"
    valid = _minimal_template(name="Visible")
    _write_template(templates_dir, "visible", valid)
    _write_template(templates_dir, ".hidden", _minimal_template(name="Hidden"))
    (templates_dir / ".visible.json.123.abcdef.tmp").write_text(json.dumps(_minimal_template(name="Tmp")))
    (templates_dir / "notes.txt").write_text(json.dumps(_minimal_template(name="Notes")))
    (templates_dir / "backup.json.bak").write_text(json.dumps(_minimal_template(name="Bak")))
    (templates_dir / "folder.json").mkdir()
    _write_template(templates_dir / "nested", "inner", _minimal_template(name="Inner"))

    user = [t for t in service.list_templates() if t["source"] == "user"]
    assert [(t["id"], t["name"], t["error"]) for t in user] == [("user:visible", "Visible", None)]
    for template_id in ("user:.hidden", "user:notes", "user:folder", "user:nested/inner", "user:../_templates/visible"):
        with pytest.raises(FileNotFoundError):
            service.create_project("X", template_id=template_id)
    assert service.list_projects() == []


def test_templates_are_listed_builtin_first_then_user_each_sorted_by_name(service):
    templates_dir = service.store.root / "_templates"
    _write_template(templates_dir, "z", _minimal_template(name="alpha"))
    _write_template(templates_dir, "a", _minimal_template(name="Beta"))
    _write_template(templates_dir, "m", "{broken")
    ids = [t["id"] for t in service.list_templates()]
    assert ids == [RBC, "user:z", "user:a", "user:m"]


def test_listing_templates_writes_nothing(service):
    _write_template(service.store.root / "_templates", "junk", "{not json")
    before = _snapshot(service.store.root)
    service.list_templates()
    assert _snapshot(service.store.root) == before
    assert service.list_projects() == []


# --- save as template ----------------------------------------------------------------


def test_save_as_template_round_trips_a_hand_written_template_with_remapped_link_refs(service):
    _write_template(service.store.root / "_templates", "mini", _minimal_template())
    source = service.create_project("Source", "described", template_id="user:mini")
    service.add_requirement(source.id, "alpha", {"object_text": "requirement text not saved"})

    summary = service.save_project_as_template(source.id, "Mini Copy")
    assert summary == {
        "id": "user:mini-copy",
        "source": "user",
        "name": "Mini Copy",
        "description": "",
        "modules": [{"key": "alpha", "name": "Alpha", "prefix": "A"}, {"key": "beta", "name": "Beta", "prefix": "B"}],
        "error": None,
    }
    data = json.loads((service.store.root / "_templates" / "mini-copy.json").read_text())
    assert data["link_configurations"] == [
        {"key": "refines", "name": "Refines", "source_module": "alpha", "target_module": "beta"}
    ]
    assert data["views"] == [{"name": "Main", "attribute_ids": ["object_text", "status", "link:refines"]}]
    assert data["attributes"] == [
        {"id": "status", "name": "Status", "type": "enum", "required": False, "default": "A", "choices": ["A", "B"]}
    ]

    copy = service.create_project("Copy", template_id="user:mini-copy")
    assert _structure(service, copy.id) == _structure(service, source.id)
    assert service.list_requirements(copy.id, "alpha") == []
    assert service.get_project(copy.id).id_counters == {}
    assert service.get_project(copy.id).description == ""


def test_save_as_template_round_trips_special_characters_and_empty_values(service):
    project = service.create_project("Special")
    service.add_attribute_definition(
        project.id, "zone", 'Strefa "ż/ł"', "enum", False, None, ["Północ", 'Quote "x"', "Tab\tNew\nline"]
    )
    service.add_attribute_definition(project.id, "note", "Note", "text", False, "", None)
    service.add_attribute_definition(project.id, "count", "Count", "integer", True, 0, None)
    service.add_attribute_definition(project.id, "flag", "Flag", "boolean", False, False, None)
    service.create_module(project.id, "Wymagania ąę", "REQ", 'Opis "quoted"\nsecond line')
    service.create_module(project.id, "Empty", "E", "")
    service.create_view(project.id, "Widok ż", ["zone", "object_text", "note", "count", "flag"])
    service.create_view(project.id, "Empty view", [])

    name = 'Szablon "RBC" / v2 – ż'
    description = 'Line 1\nLine "2" \\ ü'
    summary = service.save_project_as_template(project.id, name, description)
    entry = next(t for t in service.list_templates() if t["id"] == summary["id"])
    assert entry["name"] == name and entry["description"] == description and entry["error"] is None

    copy = service.create_project("Copy", template_id=summary["id"])
    assert _structure(service, copy.id) == _structure(service, project.id)


def test_save_as_template_drops_dangling_view_references(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "status", "Status")
    source = service.create_module(project.id, "Source", "S")
    target = service.create_module(project.id, "Target", "T")
    removed = service.create_link_configuration(project.id, "Removed", source.id, target.id)
    kept = service.create_link_configuration(project.id, "Kept", target.id, source.id)
    service.create_view(project.id, "Main", ["object_text", removed.id, "status", kept.id])
    service.remove_link_configuration(project.id, removed.id)
    # A hand-edited reference to an attribute that never existed.
    path = service.store.root / project.id / "project.json"
    data = json.loads(path.read_text())
    data["views"][0]["attribute_ids"].append("ghost")
    path.write_text(json.dumps(data, indent=2) + "\n")
    project_bytes = path.read_bytes()

    summary = service.save_project_as_template(project.id, "T")
    saved = json.loads((service.store.root / "_templates" / "t.json").read_text())
    assert saved["views"] == [{"name": "Main", "attribute_ids": ["object_text", "status", f"link:{kept.id}"]}]
    assert path.read_bytes() == project_bytes  # the project itself is not repaired
    copy = service.create_project("Copy", template_id=summary["id"])
    copy_project = service.get_project(copy.id)
    assert copy_project.views[0].attribute_ids == ["object_text", "status", copy_project.link_configurations[0].id]


def test_save_as_template_round_trips_duplicate_prefixes(service):
    project = service.create_project("P")
    service.create_module(project.id, "Alpha", "REQ")
    service.create_module(project.id, "Beta", "REQ")
    summary = service.save_project_as_template(project.id, "Shared prefix")
    assert [m["prefix"] for m in summary["modules"]] == ["REQ", "REQ"]
    copy = service.create_project("Copy", template_id=summary["id"])
    assert [(m.id, m.prefix) for m in service.list_modules(copy.id)] == [("alpha", "REQ"), ("beta", "REQ")]
    first = service.add_requirement(copy.id, "alpha", {"object_text": "x"})
    second = service.add_requirement(copy.id, "beta", {"object_text": "y"})
    assert (first.id, second.id) == ("REQ-0001", "REQ-0002")


@pytest.mark.parametrize("kind", ["modules", "views"])
def test_save_as_template_rejects_case_insensitive_duplicate_names_on_rest_and_ws(client, tmp_path, kind):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    if kind == "modules":
        client.post(f"/projects/{project_id}/modules", json={"name": "Alpha", "prefix": "A"})
        client.post(f"/projects/{project_id}/modules", json={"name": "alpha", "prefix": "B"})
        expected = "duplicate module name 'alpha'"
    else:
        client.post(f"/projects/{project_id}/views", json={"name": "Main", "attribute_ids": []})
        # Projects reject the duplicate now; older (hand-edited) data can still hold one.
        resp = client.post(f"/projects/{project_id}/views", json={"name": "MAIN", "attribute_ids": []})
        assert resp.status_code == 400, resp.text
        path = tmp_path / project_id / "project.json"
        data = json.loads(path.read_text())
        data["views"].append({"id": "main-2", "name": "MAIN", "attribute_ids": []})
        path.write_text(json.dumps(data, indent=2) + "\n")
        expected = "duplicate view name 'MAIN'"

    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        resp = client.post(f"/projects/{project_id}/save-as-template", json={"name": "T"})
        assert resp.status_code == 400, resp.text
        assert expected in resp.json()["detail"]
        assert "cannot be saved as a template" in resp.json()["detail"]
        response = _rpc(ws1, "project.save_as_template", {"id": project_id, "name": "T"})
        assert response["error"] == {"code": -32602, "message": resp.json()["detail"]}
        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": marker}
    assert not (tmp_path / "_templates").exists()
    assert [t["source"] for t in client.get("/templates").json()] == ["builtin"]


@pytest.mark.parametrize("name", ["RBC_TEMPLATE", "rbc_template", "  Rbc_Template  "])
def test_save_as_template_name_clash_with_builtin_is_409_and_ws_error(client, tmp_path, name):
    builtin_before = _snapshot(BUILTIN_TEMPLATES_DIR)
    project_id = client.post("/projects", json={"name": "P", "template_id": RBC}).json()["id"]
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        resp = client.post(f"/projects/{project_id}/save-as-template", json={"name": name})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "A template named 'RBC_Template' already exists (builtin:rbc_template)"
        response = _rpc(ws1, "project.save_as_template", {"id": project_id, "name": name})
        assert response["error"] == {"code": -32602, "message": resp.json()["detail"]}
        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": marker}
    assert not (tmp_path / "_templates").exists()
    assert _snapshot(BUILTIN_TEMPLATES_DIR) == builtin_before


def test_save_as_template_clashes_with_the_readable_name_of_an_invalid_user_template(service):
    data = _minimal_template(name="Broken One", format_version=99)
    _write_template(service.store.root / "_templates", "broken", data)
    project = service.create_project("P")
    with pytest.raises(ConflictError, match="Broken One"):
        service.save_project_as_template(project.id, "broken one")
    assert service.store.list_user_template_ids() == ["broken"]


def test_save_as_template_suffixes_the_file_name_and_never_overwrites(service):
    templates_dir = service.store.root / "_templates"
    hand_written = _write_template(templates_dir, "mine", _minimal_template(name="Something else"))
    original = hand_written.read_bytes()
    project = service.create_project("P")

    assert service.save_project_as_template(project.id, "Mine")["id"] == "user:mine-2"
    assert service.save_project_as_template(project.id, "Mine!")["id"] == "user:mine-3"
    assert service.save_project_as_template(project.id, "***")["id"] == "user:template"
    assert hand_written.read_bytes() == original
    assert service.store.list_user_template_ids() == ["mine", "mine-2", "mine-3", "template"]
    names = {t["id"]: t["name"] for t in service.list_templates()}
    assert (names["user:mine"], names["user:mine-2"], names["user:mine-3"]) == ("Something else", "Mine", "Mine!")


def test_saved_template_json_format_and_contents(service):
    parent = service.create_project("Parent")
    project = service.create_project("Child", "child description", template_id=RBC, parent_project_id=parent.id)
    service.add_requirement(project.id, "strs", {"object_text": "secret"})
    builtin_before = _snapshot(BUILTIN_TEMPLATES_DIR)

    service.save_project_as_template(project.id, "Saved", "Saved description")
    templates_dir = service.store.root / "_templates"
    assert sorted(p.name for p in templates_dir.iterdir()) == ["saved.json"]  # no temp files left
    text = (templates_dir / "saved.json").read_text()
    data = json.loads(text)
    assert text == json.dumps(data, indent=2) + "\n"
    assert list(data) == [
        "format_version", "id", "name", "description", "attributes", "modules", "link_configurations", "views",
    ]
    assert (data["format_version"], data["id"], data["name"], data["description"]) == (
        1, "saved", "Saved", "Saved description",
    )
    assert all(list(a) == ["id", "name", "type", "required", "default", "choices"] for a in data["attributes"])
    assert all(list(m) == ["key", "name", "prefix", "description"] for m in data["modules"])
    assert all(list(c) == ["key", "name", "source_module", "target_module"] for c in data["link_configurations"])
    assert all(list(v) == ["name", "attribute_ids"] for v in data["views"])
    # Mirror attributes are not saved as attributes; requirements, counters and parent are not saved.
    assert not any(a.get("system") for a in data["attributes"])
    assert {a["id"] for a in data["attributes"]}.isdisjoint({c["key"] for c in data["link_configurations"]})
    for absent in ("secret", "id_counters", "parent_project_id", "child description", "STRS-0001"):
        assert absent not in text
    assert _snapshot(BUILTIN_TEMPLATES_DIR) == builtin_before
    assert [p.id for p in service.list_projects()] == ["child", "parent"]


def test_ws_save_as_template_accepts_null_description_and_reports_missing_project(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "project.save_as_template", {"id": project_id, "name": "T", "description": None})
        assert response["result"]["description"] == ""
        response = _rpc(ws, "project.save_as_template", {"id": "missing", "name": "U"}, 2)
        assert response["error"]["code"] == -32001
        response = _rpc(ws, "project.save_as_template", {"id": project_id, "name": "  "}, 3)
        assert response["error"] == {"code": -32602, "message": "Template name must not be empty"}
        response = _rpc(ws, "project.save_as_template", {"id": project_id, "name": "V", "description": 5}, 4)
        assert response["error"] == {"code": -32602, "message": "Template description must be a string"}
    assert json.loads((tmp_path / "_templates" / "t.json").read_text())["description"] == ""
    assert client.post(f"/projects/{project_id}/save-as-template", json={"name": "  "}).status_code == 400
    assert sorted(p.name for p in (tmp_path / "_templates").iterdir()) == ["t.json"]
    assert [p["id"] for p in client.get("/projects").json()] == ["p"]


# --- subprojects: legacy data and hand-edited parents -------------------------------


def _legacy_project_text() -> str:
    # What ReqManTool wrote before subprojects existed (no parent_project_id key).
    data = {
        "id": "legacy",
        "name": "Legacy",
        "description": "Old project",
        "attributes": [
            {"id": "object_text", "name": "ObjectText", "type": "text", "required": True, "default": None,
             "choices": None, "system": False},
        ],
        "id_counters": {},
        "link_configurations": [],
        "views": [],
    }
    return json.dumps(data, indent=2) + "\n"


def test_legacy_project_without_parent_loads_and_is_only_rewritten_by_real_changes(service):
    project_dir = service.store.root / "legacy"
    project_dir.mkdir(parents=True)
    path = project_dir / "project.json"
    legacy = _legacy_project_text()
    path.write_text(legacy)
    # The legacy file matches the current serializer for a top-level project.
    assert service.get_project("legacy").to_dict() == json.loads(legacy)

    assert service.get_project("legacy").parent_project_id is None
    assert service.list_projects()[0].parent_project_id is None
    service.set_project_parent("legacy", None)  # already top level: no write
    service.list_projects()
    service.list_modules("legacy")
    service.list_templates()
    assert path.read_text() == legacy

    # Creating a module is a real change: it records the module order, and the
    # file gains its format_version as first key.
    module = service.create_module("legacy", "Reqs", "R")
    service.add_requirement("legacy", module.id, {"object_text": "x"})  # bumps id_counters
    expected = {"format_version": 1, **json.loads(legacy)}
    expected["id_counters"] = {"R": 1}
    expected["module_order"] = [module.id]
    assert path.read_text() == json.dumps(expected, indent=2) + "\n"
    assert "parent_project_id" not in path.read_text()


def test_legacy_explicit_null_parent_loads_as_top_level(service):
    project_dir = service.store.root / "legacy"
    project_dir.mkdir(parents=True)
    data = json.loads(_legacy_project_text())
    data = {**{k: data[k] for k in ("id", "name", "description")}, "parent_project_id": None,
            **{k: data[k] for k in ("attributes", "id_counters", "link_configurations", "views")}}
    (project_dir / "project.json").write_text(json.dumps(data, indent=2) + "\n")
    assert service.get_project("legacy").parent_project_id is None
    child = service.create_project("Child", parent_project_id="legacy")
    assert service.get_project(child.id).parent_project_id == "legacy"


def test_list_projects_survives_long_cycles_self_parents_and_bad_values(service):
    ids = ["a0", "a1", "c1", "c2", "c3", "d0", "d1", "self", "num", "lst", "obj", "empty", "boolean", "z0"]
    for project_id in ids:
        service.create_project(project_id)
    raw = {
        "c1": "c2", "c2": "c3", "c3": "c1",  # three-cycle
        "a0": "a1", "a1": "c2",  # chain into the cycle, visited before it
        "z0": "c3",  # chain into the cycle, visited after it
        "d1": "d0",  # valid
        "self": "self",
        "num": 5,
        "lst": ["a0"],
        "obj": {"id": "a0"},
        "empty": "",
        "boolean": True,
    }
    for project_id, parent in raw.items():
        _set_raw_parent(service, project_id, parent)
    before = _snapshot(service.store.root)

    parents = {p.id: p.parent_project_id for p in service.list_projects()}
    assert parents == {
        "a0": "a1", "a1": "c2", "c1": None, "c2": None, "c3": None, "d0": None, "d1": "d0",
        "self": None, "num": None, "lst": None, "obj": None, "empty": None, "boolean": None, "z0": "c3",
    }
    # Stored data is untouched and get_project reports the stored value.
    assert _snapshot(service.store.root) == before
    assert service.get_project("self").parent_project_id == "self"
    assert service.get_project("num").parent_project_id == 5


def test_rest_project_list_shows_broken_parents_as_top_level(client, tmp_path):
    for name in ("A", "B", "C"):
        client.post("/projects", json={"name": name})
    for project_id, parent in (("a", "b"), ("b", "a"), ("c", "a")):
        path = tmp_path / project_id / "project.json"
        data = json.loads(path.read_text())
        data["parent_project_id"] = parent
        path.write_text(json.dumps(data, indent=2) + "\n")
    listed = {p["id"]: p.get("parent_project_id") for p in client.get("/projects").json()}
    assert listed == {"a": None, "b": None, "c": "a"}
    with client.websocket_connect("/ws") as ws:
        result = _rpc(ws, "project.list", {})["result"]
    assert {p["id"]: p.get("parent_project_id") for p in result} == listed
    assert client.get("/projects/a").json()["parent_project_id"] == "b"


def test_set_parent_with_hand_edited_cycles_and_bad_values_never_hangs(service):
    for project_id in ("c1", "c2", "c3", "outsider", "weird"):
        service.create_project(project_id)
    _set_raw_parent(service, "c1", "c2")
    _set_raw_parent(service, "c2", "c3")
    _set_raw_parent(service, "c3", "c1")
    _set_raw_parent(service, "weird", 42)

    # Attaching to a cycle member or to a project with a non-string parent terminates.
    assert service.set_project_parent("outsider", "c1").parent_project_id == "c1"
    assert service.set_project_parent("outsider", "weird").parent_project_id == "weird"
    child = service.create_project("Child", parent_project_id="c2")
    assert service.get_project(child.id).parent_project_id == "c2"
    # Closing the cycle differently is still rejected.
    with pytest.raises(ValueError, match="subproject"):
        service.set_project_parent("c1", "c3")

    # Setting one member to top level repairs the cycle.
    service.set_project_parent("c1", None)
    parents = {p.id: p.parent_project_id for p in service.list_projects()}
    assert (parents["c1"], parents["c2"], parents["c3"]) == (None, "c3", "c1")
    assert parents["child"] == "c2"
    assert "parent_project_id" not in json.loads((service.store.root / "c1" / "project.json").read_text())


def test_set_parent_to_the_same_parent_does_not_rewrite_the_file(service):
    parent = service.create_project("Parent")
    child = service.create_project("Child", parent_project_id=parent.id)
    path = service.store.root / child.id / "project.json"
    before = path.read_bytes()
    stat_before = path.stat().st_mtime_ns
    assert service.set_project_parent(child.id, parent.id).parent_project_id == parent.id
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stat_before


_PATH_LIKE_PARENTS = ["a/../b", "b/", "./b", "b/.", "..", ".", "b\\", "../data/b"]


@pytest.mark.parametrize("parent", _PATH_LIKE_PARENTS)
def test_parent_id_must_be_an_existing_project_id_not_a_path(service, parent):
    service.create_project("A")
    service.create_project("B")
    service.create_project("C")
    before = _snapshot(service.store.root)
    with pytest.raises(FileNotFoundError, match=re.escape(f"Parent project '{parent}' not found")):
        service.set_project_parent("c", parent)
    with pytest.raises(FileNotFoundError, match=re.escape(f"Parent project '{parent}' not found")):
        service.create_project("D", parent_project_id=parent)
    with pytest.raises(FileNotFoundError, match=re.escape(f"Parent project '{parent}' not found")):
        service.create_project("D", template_id=RBC, parent_project_id=parent)
    assert _snapshot(service.store.root) == before


def test_absolute_parent_path_is_not_a_project_id(service, tmp_path):
    service.create_project("C")
    service.create_project("Outside")  # a real project, but named by its absolute path
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "project.json").write_text(json.dumps({"id": "elsewhere", "name": "E"}) + "\n")
    for parent in (str(service.store.root / "outside"), str(outside)):
        with pytest.raises(FileNotFoundError, match="not found"):
            service.set_project_parent("c", parent)
        with pytest.raises(FileNotFoundError, match="not found"):
            service.create_project("D", parent_project_id=parent)
    assert "parent_project_id" not in json.loads((service.store.root / "c" / "project.json").read_text())


@pytest.mark.parametrize("project_id", ["./c", "c/", "x/../c", "..", ".", "_templates", ""])
def test_path_like_project_ids_are_not_found(service, project_id):
    service.create_project("C")
    service.create_project("P")
    assert not service.store.project_exists(project_id)
    with pytest.raises(FileNotFoundError):
        service.get_project(project_id)
    with pytest.raises(FileNotFoundError):
        service.set_project_parent(project_id, "p")
    with pytest.raises(FileNotFoundError):
        service.save_project_as_template(project_id, "Mine")
    assert [t for t in service.list_templates() if t["source"] == "user"] == []
    assert "parent_project_id" not in json.loads((service.store.root / "c" / "project.json").read_text())


def test_ancestor_walk_never_leaves_the_data_directory_or_trips_on_bad_files(service, tmp_path):
    # Hand-edited data: b's parent is a path to a malformed project.json outside the data
    # directory, d's parent is a malformed project inside it. Neither is read or fatal.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "project.json").write_text("{}\n")
    (service.store.root / "broken").mkdir(parents=True)
    (service.store.root / "broken" / "project.json").write_text('{"name": "no id"}\n')
    for name in ("B", "C", "D"):
        service.create_project(name)
    _set_raw_parent(service, "b", "../elsewhere")
    _set_raw_parent(service, "d", "broken")

    assert service.set_project_parent("c", "b").parent_project_id == "b"
    assert service.set_project_parent("c", "d").parent_project_id == "d"
    assert service.set_project_parent("c", "broken").parent_project_id == "broken"
    assert service.create_project("E", parent_project_id="broken").parent_project_id == "broken"


@pytest.fixture
def outside_client(tmp_path, monkeypatch):
    """A client whose data directory has a sibling ``elsewhere/project.json`` without an ``id``."""
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "project.json").write_text("{}\n")
    monkeypatch.setenv("REQMAN_DATA_DIR", str(data))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app), data


@pytest.mark.parametrize("parent", [*_PATH_LIKE_PARENTS, "../elsewhere", "elsewhere/..", "ABSOLUTE"])
def test_path_like_parent_is_404_over_rest_and_ws_and_never_broadcasts(outside_client, tmp_path, parent):
    client, data = outside_client
    if parent == "ABSOLUTE":
        parent = str(tmp_path / "elsewhere")
    for name in ("A", "B", "C"):
        client.post("/projects", json={"name": name})
    before = _snapshot(data)
    detail = f"Parent project '{parent}' not found"
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        resp = client.put("/projects/c/parent", json={"parent_project_id": parent})
        assert (resp.status_code, resp.json()["detail"]) == (404, detail)
        response = _rpc(ws1, "project.set_parent", {"id": "c", "parent_project_id": parent})
        assert response["error"] == {"code": -32001, "message": detail}

        resp = client.post("/projects", json={"name": "D", "parent_project_id": parent})
        assert (resp.status_code, resp.json()["detail"]) == (404, detail)
        response = _rpc(ws1, "project.create", {"name": "D", "parent_project_id": parent}, 2)
        assert response["error"] == {"code": -32001, "message": detail}

        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": marker}
    assert {k: v for k, v in _snapshot(data).items() if not k.startswith("marker")} == before


def test_path_like_project_id_over_ws_is_not_found(outside_client):
    client, _ = outside_client
    client.post("/projects", json={"name": "A"})
    with client.websocket_connect("/ws") as ws:
        for index, project_id in enumerate(["../elsewhere", "./a", "a/"]):
            response = _rpc(ws, "project.set_parent", {"id": project_id, "parent_project_id": None}, index)
            assert response["error"]["code"] == -32001, response
            response = _rpc(ws, "project.get", {"id": project_id}, index)
            assert response["error"]["code"] == -32001, response
            response = _rpc(ws, "project.save_as_template", {"id": project_id, "name": "T"}, index)
            assert response["error"]["code"] == -32001, response
    assert client.get("/projects/..%2Felsewhere").status_code == 404
    assert [t for t in client.get("/templates").json() if t["source"] == "user"] == []


@pytest.mark.skipif(sys.platform == "win32", reason="'\\' is a path separator on Windows")
def test_backslash_named_project_folder_is_not_listed_and_cannot_be_a_parent(client, tmp_path):
    """The listing agrees with ``load_project``: a folder it would refuse is never listed."""
    client.post("/projects", json={"name": "Alpha"})
    hand = client.post("/projects", json={"name": "Hand"}).json()["id"]
    odd = "hand\\made"
    (tmp_path / hand).rename(tmp_path / odd)
    raw = json.loads((tmp_path / odd / "project.json").read_text())
    raw["id"] = odd
    (tmp_path / odd / "project.json").write_text(json.dumps(raw, indent=2) + "\n")

    assert ProjectService(ProjectStore(tmp_path)).store.list_project_ids() == ["alpha"]
    assert [p.id for p in ProjectService(ProjectStore(tmp_path)).list_projects()] == ["alpha"]
    resp = client.get("/projects")
    assert resp.status_code == 200 and [p["id"] for p in resp.json()] == ["alpha"]
    detail = f"Parent project '{odd}' not found"
    with client.websocket_connect("/ws") as ws:
        assert [p["id"] for p in _rpc(ws, "project.list", {})["result"]] == ["alpha"]
        response = _rpc(ws, "project.set_parent", {"id": "alpha", "parent_project_id": odd}, 2)
        assert response["error"] == {"code": -32001, "message": detail}
    resp = client.put("/projects/alpha/parent", json={"parent_project_id": odd})
    assert (resp.status_code, resp.json()["detail"]) == (404, detail)
    assert client.get("/projects/alpha").json().get("parent_project_id") is None


# --- broadcasts ----------------------------------------------------------------------


def test_rest_set_parent_broadcasts_changed_to_every_ws_client(client):
    parent = client.post("/projects", json={"name": "Parent"}).json()["id"]
    child = client.post("/projects", json={"name": "Child"}).json()["id"]
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        resp = client.put(f"/projects/{child}/parent", json={"parent_project_id": parent})
        assert resp.status_code == 200 and resp.json()["parent_project_id"] == parent
        assert json.loads(ws1.receive_text()) == {"event": "changed", "project_id": child}
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": child}
    assert client.get(f"/projects/{child}").json()["parent_project_id"] == parent


def test_ws_set_parent_and_save_as_template_notify_other_clients_only(client):
    parent = client.post("/projects", json={"name": "Parent"}).json()["id"]
    child = client.post("/projects", json={"name": "Child"}).json()["id"]
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        response = _rpc(ws1, "project.set_parent", {"id": child, "parent_project_id": parent})
        assert response["result"]["parent_project_id"] == parent
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": child}

        response = _rpc(ws1, "project.save_as_template", {"id": child, "name": "Mine"}, 2)
        assert response["result"]["id"] == "user:mine"
        assert json.loads(ws2.receive_text()) == {"event": "templates_changed"}

        # The sender got no event of its own: its next message is the next response.
        response = _rpc(ws1, "project.get", {"id": child}, 3)
        assert response["id"] == 3 and response["result"]["parent_project_id"] == parent


def test_listing_templates_never_broadcasts(client):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        assert any(t["id"] == RBC for t in _rpc(ws1, "template.list", {})["result"])
        assert client.get("/templates").status_code == 200
        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws1.receive_text()) == {"event": "changed", "project_id": marker}
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": marker}


def test_failed_rest_save_as_template_does_not_broadcast(client):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    client.post(f"/projects/{project_id}/save-as-template", json={"name": "Taken"})
    with client.websocket_connect("/ws") as ws:
        assert client.post(f"/projects/{project_id}/save-as-template", json={"name": "taken"}).status_code == 409
        assert client.post(f"/projects/{project_id}/save-as-template", json={"name": " "}).status_code == 400
        assert client.post("/projects/missing/save-as-template", json={"name": "New"}).status_code == 404
        assert client.post(f"/projects/{project_id}/save-as-template", json={"name": "New"}).status_code == 200
        assert json.loads(ws.receive_text()) == {"event": "templates_changed"}
    assert [t["id"] for t in client.get("/templates").json() if t["source"] == "user"] == ["user:new", "user:taken"]
