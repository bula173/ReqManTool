"""Smoke tests for project templates and the subproject tree."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore

RBC = "builtin:rbc_template"


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path / "data"))


def _write_template(directory: Path, stem: str, data) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
    (directory / f"{stem}.json").write_text(text)


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


# --- templates: service ------------------------------------------------------------


def test_builtin_rbc_template_is_listed_and_valid(service):
    templates = service.list_templates()
    rbc = next(t for t in templates if t["id"] == RBC)
    assert rbc["source"] == "builtin"
    assert rbc["error"] is None
    assert [m["name"] for m in rbc["modules"]] == [
        "StRS", "SyRS", "SwRS", "SysTest", "SwTest", "SysTestExec", "SwTestExec"
    ]


def test_create_project_from_rbc_template_builds_structure(service):
    project = service.create_project("RBC GP", template_id=RBC)
    store = service.store
    assert {m.name for m in service.list_modules(project.id)} == {
        "StRS", "SyRS", "SwRS", "SysTest", "SwTest", "SysTestExec", "SwTestExec"
    }
    reloaded = store.load_project(project.id)
    configs = {c.name: c for c in reloaded.link_configurations}
    assert configs["Satisfies StRS"].source_module_id == "syrs"
    assert configs["Satisfies StRS"].target_module_id == "strs"
    mirror_ids = {a.id for a in reloaded.attributes if a.system}
    assert mirror_ids == {c.id for c in reloaded.link_configurations}
    for config in reloaded.link_configurations:
        assert store.load_link_pairs(project.id, config.id) == []
        assert (store.root / project.id / "links" / f"{config.id}.json").exists()
    requirements_view = next(v for v in reloaded.views if v.name == "Requirements")
    assert configs["Satisfies StRS"].id in requirements_view.attribute_ids
    assert not any(a.startswith("link:") for v in reloaded.views for a in v.attribute_ids)
    # Requirement ids still come from the module prefix, after the heading skeleton's.
    skeleton = service.list_requirements(project.id, "syrs")
    assert skeleton and [r.id for r in skeleton][0] == "SYRS-0001"
    req = service.add_requirement(project.id, "syrs", {"object_text": "x"})
    assert req.id == f"SYRS-{len(skeleton) + 1:04d}"
    assert req.attributes["status"] == "Draft"
    assert not (store.root / ".reqman-staging").exists()


def test_create_from_invalid_template_leaves_nothing_on_disk(tmp_path):
    builtin_dir = tmp_path / "builtin"
    bad = _minimal_template(views=[{"name": "Main", "attribute_ids": ["link:nope"]}])
    _write_template(builtin_dir, "bad", bad)
    service = ProjectService(ProjectStore(tmp_path / "data"), builtin_templates_dir=builtin_dir)
    with pytest.raises(ValueError, match="link:nope"):
        service.create_project("X", template_id="builtin:bad")
    assert list((tmp_path / "data").iterdir()) == []
    listed = service.list_templates()
    assert listed[0]["id"] == "builtin:bad" and "link:nope" in listed[0]["error"]


def test_create_from_template_rolls_back_when_a_write_fails(service, monkeypatch):
    original = ProjectStore.save_project

    def failing_save(self, project):
        if ".reqman-staging" in str(self.root):
            raise OSError("disk full")
        return original(self, project)

    monkeypatch.setattr(ProjectStore, "save_project", failing_save)
    with pytest.raises(OSError):
        service.create_project("RBC", template_id=RBC)
    assert list(service.store.root.iterdir()) == []
    assert service.list_projects() == []


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"format_version": 3}, "newer"),  # version 2 is current
        ({"format_version": None}, "format_version"),
        ({"modules": [{"key": "a", "name": "A", "prefix": "A"}, {"key": "a", "name": "B", "prefix": "B"}]}, "duplicate module key"),
        ({"link_configurations": [{"key": "x", "name": "L", "source_module": "a", "target_module": "zzz"}]}, "zzz"),
        ({"attributes": [{"id": "s", "name": "Id"}]}, "reserved"),
        ({"attributes": [{"id": "s", "name": "S", "type": "enum"}]}, "choices"),
        ({"attributes": [{"id": "s", "name": "S"}, {"id": "t", "name": "s"}]}, "already used"),
        ({"modules": [{"key": "a", "name": "A", "prefix": "1bad"}], "link_configurations": [], "views": []}, "Invalid prefix"),
        ({"views": [{"name": "V", "attribute_ids": ["missing"]}]}, "unknown attribute 'missing'"),
    ],
)
def test_invalid_user_template_is_listed_with_error_and_rejected(service, overrides, message):
    _write_template(service.store.root / "_templates", "broken", _minimal_template(**overrides))
    _write_template(service.store.root / "_templates", "good", _minimal_template(name="Good"))
    by_id = {t["id"]: t for t in service.list_templates()}
    assert message in by_id["user:broken"]["error"]
    assert by_id["user:good"]["error"] is None
    assert by_id[RBC]["error"] is None
    with pytest.raises(ValueError, match="is invalid"):
        service.create_project("X", template_id="user:broken")
    assert service.list_projects() == []


def test_unknown_template_keys_are_ignored(service):
    data = _minimal_template(future_key=1)
    data["modules"][0]["type"] = "requirements"
    _write_template(service.store.root / "_templates", "mini", data)
    project = service.create_project("P", template_id="user:mini")
    assert {m.name for m in service.list_modules(project.id)} == {"Alpha", "Beta"}


def test_unparseable_user_template_does_not_hide_others(service):
    _write_template(service.store.root / "_templates", "junk", "{not json")
    templates = service.list_templates()
    junk = next(t for t in templates if t["id"] == "user:junk")
    assert junk["name"] == "junk" and "JSON" in junk["error"]
    assert any(t["id"] == RBC and t["error"] is None for t in templates)


@pytest.mark.parametrize("template_id", ["rbc_template", "user:", "other:x", 5])
def test_malformed_template_id_is_rejected(service, template_id):
    with pytest.raises(ValueError):
        service.create_project("X", template_id=template_id)


def test_missing_template_is_not_found(service):
    with pytest.raises(FileNotFoundError):
        service.create_project("X", template_id="user:nope")
    with pytest.raises(FileNotFoundError):
        service.create_project("X", template_id="builtin:nope")


def test_save_as_template_round_trips(service):
    source = service.create_project("Source", template_id=RBC)
    service.add_requirement(source.id, "strs", {"object_text": "not saved"})
    summary = service.save_project_as_template(source.id, "  My RBC ", "desc")
    assert summary["id"] == "user:my-rbc"
    path = service.store.root / "_templates" / "my-rbc.json"
    text = path.read_text()
    assert text.endswith("}\n") and text.startswith('{\n  "format_version": 2,')
    data = json.loads(text)
    assert list(data) == [
        "format_version", "id", "name", "description", "attributes", "modules",
        "module_tree",  # the RBC folders are kept
        "link_configurations", "views",
        "row_styles",  # the RBC template's default row styles are kept
    ]
    assert "object_text" not in {a["id"] for a in data["attributes"]}
    assert "not saved" not in text
    requirements_view = next(v for v in data["views"] if v["name"] == "Requirements")
    assert "link:satisfies-strs" in requirements_view["attribute_ids"]
    # Never listed as a project.
    assert [p.id for p in service.list_projects()] == ["source"]

    copy = service.create_project("Copy", template_id="user:my-rbc")
    original = service.get_project(source.id)
    assert [a.to_dict() for a in copy.attributes] == [a.to_dict() for a in original.attributes]
    assert [v.to_dict() for v in copy.views] == [v.to_dict() for v in original.views]
    # Only the heading skeleton is carried over, never other requirements.
    def objects(project_id):
        return [
            (r.attributes.get("object_type"), r.attributes.get("object_text"))
            for r in service.list_requirements(project_id, "strs")
        ]

    copied = objects(copy.id)
    assert copied and all(kind in ("Heading", "Information") for kind, _ in copied)
    assert copied == [o for o in objects(source.id) if o[1] != "not saved"]


def test_save_as_template_rejects_name_collisions(service):
    project = service.create_project("P")
    service.save_project_as_template(project.id, "Mine")
    with pytest.raises(ConflictError):
        service.save_project_as_template(project.id, "mine")
    with pytest.raises(ConflictError):
        service.save_project_as_template(project.id, "rbc_template")
    with pytest.raises(ValueError, match="must not be empty"):
        service.save_project_as_template(project.id, "  ")
    assert service.store.list_user_template_ids() == ["mine"]


def test_templates_dir_is_never_a_project(service):
    (service.store.root / "_templates").mkdir()
    (service.store.root / "_templates" / "project.json").write_text('{"id": "_templates", "name": "x"}\n')
    assert service.list_projects() == []
    with pytest.raises(FileNotFoundError):
        service.get_project("_templates")
    with pytest.raises(FileNotFoundError):
        service.create_project("Child", parent_project_id="_templates")
    assert service.create_project("_templates").id == "templates"


# --- subprojects: service ------------------------------------------------------------


def test_create_subprojects_and_reparent(service):
    rte = service.create_project("RTE")
    gp = service.create_project("RBC_GP", template_id=RBC, parent_project_id=rte.id)
    ga = service.create_project("RBC_GA_NCBIR", parent_project_id=gp.id)
    assert gp.parent_project_id == "rte"
    assert service.get_project(ga.id).parent_project_id == gp.id

    with pytest.raises(ValueError, match="own parent"):
        service.set_project_parent(gp.id, gp.id)
    with pytest.raises(ValueError, match="subproject"):
        service.set_project_parent(rte.id, ga.id)
    with pytest.raises(FileNotFoundError):
        service.set_project_parent(ga.id, "missing")
    with pytest.raises(FileNotFoundError):
        service.create_project("Orphan", parent_project_id="missing")
    assert [p.id for p in service.list_projects()] == ["rbc-ga-ncbir", "rbc-gp", "rte"]

    moved = service.set_project_parent(ga.id, None)
    assert moved.parent_project_id is None
    assert "parent_project_id" not in json.loads((service.store.root / ga.id / "project.json").read_text())


def test_project_json_without_parent_is_unchanged_on_save(service):
    project = service.create_project("Old")
    path = service.store.root / project.id / "project.json"
    before = path.read_text()
    assert "parent_project_id" not in before
    service.store.save_project(service.get_project(project.id))
    assert path.read_text() == before


def test_parent_key_follows_description(service):
    parent = service.create_project("Parent")
    child = service.create_project("Child", parent_project_id=parent.id)
    data = json.loads((service.store.root / child.id / "project.json").read_text())
    assert list(data)[:5] == ["format_version", "id", "name", "description", "parent_project_id"]


def test_dangling_and_cyclic_parents_on_disk_list_as_top_level(service):
    for name in ("A", "B", "C", "D"):
        service.create_project(name)

    def set_raw(project_id, parent):
        path = service.store.root / project_id / "project.json"
        data = json.loads(path.read_text())
        data["parent_project_id"] = parent
        path.write_text(json.dumps(data, indent=2) + "\n")

    set_raw("a", "b")
    set_raw("b", "a")
    set_raw("c", "a")
    set_raw("d", "ghost")
    parents = {p.id: p.parent_project_id for p in service.list_projects()}
    assert parents == {"a": None, "b": None, "c": "a", "d": None}
    # Stored data is not rewritten, and repairing a cycle is allowed.
    assert service.get_project("d").parent_project_id == "ghost"
    service.set_project_parent("a", None)
    with pytest.raises(ValueError, match="subproject"):
        service.set_project_parent("a", "b")


# --- REST / WebSocket --------------------------------------------------------------


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


def test_rest_templates_and_subprojects(client):
    resp = client.get("/templates")
    assert resp.status_code == 200
    assert any(t["id"] == RBC for t in resp.json())

    parent = client.post("/projects", json={"name": "RTE"}).json()
    resp = client.post("/projects", json={"name": "GP", "template_id": RBC, "parent_project_id": parent["id"]})
    assert resp.status_code == 200, resp.text
    gp = resp.json()
    assert gp["parent_project_id"] == "rte"
    assert len(client.get(f"/projects/{gp['id']}/modules").json()) == 7

    assert client.post("/projects", json={"name": "X", "template_id": "user:none"}).status_code == 404
    assert client.post("/projects", json={"name": "X", "template_id": "bogus"}).status_code == 400
    assert client.post("/projects", json={"name": "X", "parent_project_id": "none"}).status_code == 404

    resp = client.put(f"/projects/{parent['id']}/parent", json={"parent_project_id": gp["id"]})
    assert resp.status_code == 400
    resp = client.put(f"/projects/{gp['id']}/parent", json={"parent_project_id": None})
    assert resp.status_code == 200 and "parent_project_id" not in resp.json()

    resp = client.post(f"/projects/{gp['id']}/save-as-template", json={"name": "Mine"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "user:mine"
    resp = client.post(f"/projects/{gp['id']}/save-as-template", json={"name": "MINE"})
    assert resp.status_code == 409
    assert client.post("/projects/missing/save-as-template", json={"name": "Z"}).status_code == 404
    assert [p["id"] for p in client.get("/projects").json()] == ["gp", "rte"]


def test_websocket_templates_parity_and_broadcasts(client):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        listed = _rpc(ws1, "template.list", {})
        assert any(t["id"] == RBC for t in listed["result"])

        created = _rpc(ws1, "project.create", {"name": "GP", "template_id": RBC}, 2)["result"]
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": created["id"]}
        child = _rpc(ws1, "project.create", {"name": "GA", "parent_project_id": created["id"]}, 3)["result"]
        assert child["parent_project_id"] == created["id"]
        json.loads(ws2.receive_text())

        response = _rpc(ws1, "project.set_parent", {"id": created["id"], "parent_project_id": child["id"]}, 4)
        assert response["error"]["code"] == -32602
        response = _rpc(ws1, "project.set_parent", {"id": child["id"], "parent_project_id": None}, 5)
        assert "parent_project_id" not in response["result"]
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": child["id"]}

        response = _rpc(ws1, "project.save_as_template", {"id": created["id"], "name": "Mine"}, 6)
        assert response["result"]["id"] == "user:mine"
        assert json.loads(ws2.receive_text()) == {"event": "templates_changed"}
        response = _rpc(ws1, "project.save_as_template", {"id": created["id"], "name": "mine"}, 7)
        assert response["error"]["code"] == -32602

        client.post(f"/projects/{created['id']}/save-as-template", json={"name": "Other"})
        assert json.loads(ws1.receive_text()) == {"event": "templates_changed"}
        assert json.loads(ws2.receive_text()) == {"event": "templates_changed"}
