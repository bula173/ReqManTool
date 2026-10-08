"""Project references and cross-project traceability.

``Project.references``, ``LinkConfiguration.target_project_id``, qualified
mirrors (``> b:SYS-0001``), incoming links computed on read, cleanup of links
from referencing projects when requirements are removed or purged, and the
REST/WebSocket contracts with their per-project ``changed`` broadcasts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from reqman import extensions
from reqman.extensions import ACTION_PROJECT_WRITE, AccessDeniedError, ModuleLockedError
from reqman.formats import PROJECT_FORMAT, NewerFormatError
from reqman.history import FileChange, describe_changes
from reqman.service import MAX_PROJECT_REFERENCES, ConflictError, ProjectLockedError, ProjectService
from reqman.store import ProjectStore


@pytest.fixture
def root(tmp_path):
    return tmp_path


@pytest.fixture
def service(root):
    return ProjectService(ProjectStore(root))


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


def two_projects(service, reference=True):
    """``b`` with module ``sys`` (SYS-0001, SYS-0002); ``a`` with module ``srs`` (SRS-0001), ``a`` -> ``b``."""
    service.create_project("B")
    service.create_module("b", "Sys", "SYS")
    add(service, "b", "sys", "First")
    add(service, "b", "sys", "Second")
    service.create_project("A")
    service.create_module("a", "Srs", "SRS")
    add(service, "a", "srs")
    if reference:
        service.set_project_references("a", ["b"])


def cross_config(service, name="Traces", source="srs", target="sys"):
    return service.create_link_configuration("a", name, source, target, "b").id


def pairs(service, project_id, config_id):
    return [(p.source_id, p.target_id) for p in service.store.load_link_pairs(project_id, config_id)]


def project_json(root, project_id):
    return (root / project_id / "project.json").read_text(encoding="utf-8")


def tree_bytes(path):
    return {p: p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def rpc(ws, method, params, request_id=1):
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    return json.loads(ws.receive_text())


# --- references: validation ---------------------------------------------------------


def test_set_references_persists_in_order_and_reloads(service, root):
    two_projects(service, reference=False)
    service.create_project("C")

    project = service.set_project_references("a", ["c", "b"])

    assert project.references == ["c", "b"]
    reloaded = ProjectService(ProjectStore(root))
    assert reloaded.get_project("a").references == ["c", "b"]


def test_set_references_writes_stable_json_after_parent_and_before_attributes(service, root):
    two_projects(service, reference=False)
    service.set_project_references("a", ["b"])

    text = project_json(root, "a")
    data = json.loads(text)
    assert text == json.dumps(data, indent=2) + "\n"
    keys = list(data)
    assert keys[:5] == ["format_version", "id", "name", "description", "references"]
    assert keys.index("references") < keys.index("attributes")
    assert data["format_version"] == PROJECT_FORMAT.current
    assert data["references"] == ["b"]


def test_clearing_references_removes_the_key(service, root):
    two_projects(service)
    service.set_project_references("a", None)
    assert "references" not in json.loads(project_json(root, "a"))
    service.set_project_references("a", ["b"])
    service.set_project_references("a", [])
    assert "references" not in json.loads(project_json(root, "a"))


def test_unchanged_references_write_nothing(service, root):
    two_projects(service)
    before = (root / "a" / "project.json").read_bytes()
    service.take_touched_projects()
    service.set_project_references("a", ["b"])
    assert (root / "a" / "project.json").read_bytes() == before
    assert service.take_touched_projects() == set()


def test_duplicate_references_collapse(service):
    two_projects(service, reference=False)
    assert service.set_project_references("a", ["b", "b", "b"]).references == ["b"]


def test_reference_to_itself_is_refused(service):
    two_projects(service, reference=False)
    with pytest.raises(ValueError, match="cannot reference itself"):
        service.set_project_references("a", ["b", "a"])
    assert service.get_project("a").references == []


def test_dangling_reference_is_not_found(service):
    two_projects(service, reference=False)
    with pytest.raises(FileNotFoundError, match="ghost"):
        service.set_project_references("a", ["b", "ghost"])
    assert service.get_project("a").references == []


@pytest.mark.parametrize("value", ["b", [""], [1], {"b": True}, [None]])
def test_malformed_references_are_refused(service, value):
    two_projects(service, reference=False)
    with pytest.raises(ValueError, match="list of project ids"):
        service.set_project_references("a", value)


def test_reference_limit(service):
    two_projects(service, reference=False)
    with pytest.raises(ValueError, match=f"at most {MAX_PROJECT_REFERENCES}"):
        service.set_project_references("a", [f"p{i}" for i in range(MAX_PROJECT_REFERENCES + 1)])
    # Duplicates count against the raw list, but exactly the limit passes the length check.
    assert service.set_project_references("a", ["b"] * MAX_PROJECT_REFERENCES).references == ["b"]


def test_reference_to_unloadable_project_is_refused(service, root):
    two_projects(service, reference=False)
    (root / "broken").mkdir()
    (root / "broken" / "project.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        service.set_project_references("a", ["broken"])
    assert service.get_project("a").references == []


def test_reference_to_newer_format_project_is_refused(service, root):
    two_projects(service, reference=False)
    (root / "newer").mkdir()
    (root / "newer" / "project.json").write_text(
        json.dumps({"format_version": PROJECT_FORMAT.current + 1, "id": "newer", "name": "Newer"}, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(NewerFormatError):
        service.set_project_references("a", ["newer"])


def test_folder_without_project_json_is_not_found(service, root):
    two_projects(service, reference=False)
    (root / "loose").mkdir()
    with pytest.raises(FileNotFoundError):
        service.set_project_references("a", ["loose"])


def test_set_references_of_missing_project_is_not_found(service):
    with pytest.raises(FileNotFoundError):
        service.set_project_references("ghost", [])


# --- references: implicit parent and in-use refusal ---------------------------------


def test_parent_is_referenced_implicitly(service):
    service.create_project("Parent")
    service.create_module("parent", "Sys", "SYS")
    add(service, "parent", "sys")
    service.create_project("Child", parent_project_id="parent")
    service.create_module("child", "Srs", "SRS")

    config = service.create_link_configuration("child", "Traces", "srs", "sys", "parent")
    assert config.target_project_id == "parent"

    info = service.project_references("child")
    assert info["references"] == [
        {"id": "parent", "name": "Parent", "implicit": True, "link_configurations": 1}
    ]
    # Listing the parent explicitly makes it non-implicit, without duplicating it.
    service.set_project_references("child", ["parent"])
    info = service.project_references("child")
    assert [(r["id"], r["implicit"]) for r in info["references"]] == [("parent", False)]


def test_removing_a_reference_in_use_is_refused_until_its_configuration_is_removed(service):
    two_projects(service)
    config_id = cross_config(service)

    with pytest.raises(ValueError, match="cannot stop referencing project 'b'.*'Traces'"):
        service.set_project_references("a", [])
    assert service.get_project("a").references == ["b"]

    service.remove_link_configuration("a", config_id)
    assert service.set_project_references("a", []).references == []


def test_reparenting_away_from_implicit_parent_in_use_is_refused(service):
    service.create_project("Parent")
    service.create_module("parent", "Sys", "SYS")
    service.create_project("Other")
    service.create_project("Child", parent_project_id="parent")
    service.create_module("child", "Srs", "SRS")
    service.create_link_configuration("child", "Traces", "srs", "sys", "parent")

    with pytest.raises(ValueError, match="cannot stop referencing project 'parent'"):
        service.set_project_parent("child", None)
    with pytest.raises(ValueError, match="cannot stop referencing project 'parent'"):
        service.set_project_parent("child", "other")
    assert service.get_project("child").parent_project_id == "parent"

    # Allowed once the parent is listed explicitly: it stays referenced.
    service.set_project_references("child", ["parent"])
    assert service.set_project_parent("child", "other").parent_project_id == "other"
    # And dropping the explicit reference is refused now.
    with pytest.raises(ValueError, match="cannot stop referencing"):
        service.set_project_references("child", [])


def test_project_references_lists_referenced_by_with_link_counts(service):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    service.link_requirements("a", config_id, "SRS-0001", "b:SYS-0002")

    info_b = service.project_references("b")
    assert info_b["references"] == []
    assert info_b["referenced_by"] == [
        {"id": "a", "name": "A", "link_configurations": [{"id": config_id, "name": "Traces", "links": 2}]}
    ]
    info_a = service.project_references("a")
    assert info_a["references"] == [{"id": "b", "name": "B", "implicit": False, "link_configurations": 1}]
    assert info_a["referenced_by"] == []


def test_project_references_reports_a_hand_edited_dangling_reference(service, root):
    two_projects(service)
    path = root / "a" / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["references"] = ["b", "ghost", "a"]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    refs = service.project_references("a")["references"]
    assert [r["id"] for r in refs] == ["b", "ghost"]  # never the project itself
    assert "error" not in refs[0]
    assert "ghost" in refs[1]["error"]


# --- cross-project link configurations ----------------------------------------------


def test_cross_configuration_needs_a_referenced_target_project(service):
    two_projects(service, reference=False)
    with pytest.raises(ValueError, match="does not reference project 'b'"):
        service.create_link_configuration("a", "Traces", "srs", "sys", "b")
    assert service.get_project("a").link_configurations == []


def test_cross_configuration_target_module_must_exist_in_target_project(service):
    two_projects(service)
    with pytest.raises(FileNotFoundError, match="'nope' not found in project 'b'"):
        service.create_link_configuration("a", "Traces", "srs", "nope", "b")
    # A module of the source project with that id does not count.
    service.create_module("a", "Local", "LOC")
    with pytest.raises(FileNotFoundError, match="'local' not found in project 'b'"):
        service.create_link_configuration("a", "Traces", "srs", "local", "b")


def test_cross_configuration_still_checks_module_type_pairs(service):
    two_projects(service)
    service.create_module("b", "Tests", "TC", type="test_cases")
    with pytest.raises(ValueError, match="is not allowed"):
        service.create_link_configuration("a", "Verifies", "srs", "tests", "b")
    service.create_module("a", "Cases", "ATC", type="test_cases")
    assert service.create_link_configuration("a", "Verifies", "cases", "sys", "b")


@pytest.mark.parametrize("bad", [1, ["b"], True])
def test_cross_configuration_target_project_must_be_a_string(service, bad):
    two_projects(service)
    with pytest.raises(ValueError):
        service.create_link_configuration("a", "Traces", "srs", "sys", bad)


def test_target_project_equal_to_own_project_means_local(service, root):
    two_projects(service)
    service.create_module("a", "Sys", "ASY")
    config = service.create_link_configuration("a", "Local", "srs", "sys", "a")
    assert config.target_project_id is None
    stored = json.loads(project_json(root, "a"))["link_configurations"]
    assert stored == [{"id": "local", "name": "Local", "source_module_id": "srs", "target_module_id": "sys"}]


def test_cross_configuration_is_stored_only_in_the_source_project(service, root):
    two_projects(service)
    before_b = tree_bytes(root / "b")
    config_id = cross_config(service)

    data = json.loads(project_json(root, "a"))
    assert data["link_configurations"] == [
        {"id": config_id, "name": "Traces", "source_module_id": "srs", "target_module_id": "sys",
         "target_project_id": "b"}
    ]
    assert (root / "a" / "links" / f"{config_id}.json").exists()
    assert tree_bytes(root / "b") == before_b


def test_link_with_plain_and_qualified_target_writes_qualified_mirror_only_in_source(service, root):
    two_projects(service)
    config_id = cross_config(service)
    before_b = tree_bytes(root / "b")
    service.take_touched_projects()

    result = service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    assert service.take_touched_projects() == {"a"}
    assert result["source"].attributes[config_id] == "> b:SYS-0001"
    assert result["target"].id == "SYS-0001"
    assert config_id not in result["target"].attributes

    result = service.link_requirements("a", config_id, "SRS-0001", "b:SYS-0002")
    assert result["source"].attributes[config_id] == "> b:SYS-0001; > b:SYS-0002"
    assert pairs(service, "a", config_id) == [("SRS-0001", "SYS-0001"), ("SRS-0001", "SYS-0002")]
    assert tree_bytes(root / "b") == before_b

    reloaded = ProjectService(ProjectStore(root))
    assert reloaded.get_requirement("a", "srs", "SRS-0001").attributes[config_id] == "> b:SYS-0001; > b:SYS-0002"


def test_duplicate_cross_link_is_refused_whichever_way_the_target_is_written(service):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    with pytest.raises(ValueError, match="'b:SYS-0001' already exists"):
        service.link_requirements("a", config_id, "SRS-0001", "b:SYS-0001")


def test_wrong_qualifier_is_refused(service):
    two_projects(service)
    config_id = cross_config(service)
    service.create_module("a", "Sys", "ASY")
    local_target = add(service, "a", "sys")
    local_id = service.create_link_configuration("a", "Local", "srs", "sys").id

    with pytest.raises(ValueError, match="not in the target project"):
        service.link_requirements("a", config_id, "SRS-0001", "c:SYS-0001")
    with pytest.raises(ValueError, match="not in the target project"):
        service.link_requirements("a", config_id, "SRS-0001", "a:SYS-0001")
    # A configuration within one project takes plain ids only.
    with pytest.raises(ValueError, match="not in the target project"):
        service.link_requirements("a", local_id, "SRS-0001", f"a:{local_target}")
    with pytest.raises(ValueError, match="not in the target project"):
        service.unlink_requirements("a", config_id, "SRS-0001", "c:SYS-0001")
    assert pairs(service, "a", config_id) == []


def test_cross_link_target_must_be_in_the_target_project_module(service):
    two_projects(service)
    config_id = cross_config(service)
    service.create_module("b", "Other", "OTH")
    other = add(service, "b", "other")
    with pytest.raises(FileNotFoundError, match="SYS-0009"):
        service.link_requirements("a", config_id, "SRS-0001", "SYS-0009")
    with pytest.raises(ValueError, match="of project 'b'"):
        service.link_requirements("a", config_id, "SRS-0001", other)


def test_unlink_cross_link_with_qualified_target(service, root):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0002")

    result = service.unlink_requirements("a", config_id, "SRS-0001", "b:SYS-0001")

    assert result["source"].attributes[config_id] == "> b:SYS-0002"
    assert result["target"].id == "SYS-0001"
    result = service.unlink_requirements("a", config_id, "SRS-0001", "SYS-0002")
    assert config_id not in result["source"].attributes
    assert pairs(service, "a", config_id) == []


def test_unlink_when_target_is_gone_removes_the_pair_and_returns_no_target(service):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    # The target vanished without the cascade (e.g. deleted by hand or by a git pull).
    service.store.delete_requirement("b", "sys", "SYS-0001")

    result = service.unlink_requirements("a", config_id, "SRS-0001", "b:SYS-0001")

    assert result["target"] is None
    assert config_id not in result["source"].attributes
    assert pairs(service, "a", config_id) == []


def test_unlink_of_a_never_existing_cross_target_returns_no_target(service):
    two_projects(service)
    config_id = cross_config(service)
    result = service.unlink_requirements("a", config_id, "SRS-0001", "SYS-0404")
    assert result["target"] is None
    assert result["source"].id == "SRS-0001"


# --- cleanup across projects ----------------------------------------------------------


def same_id_projects(service):
    """``b``.sys REQ-0001; ``a`` local REQ-0001 (module loc) and REQ-0002 (module srs) -> b:REQ-0001.

    ``a`` also has a local configuration loc -> loc with REQ-0001 -> REQ-0003.
    """
    service.create_project("B")
    service.create_module("b", "Sys", "REQ")
    assert add(service, "b", "sys") == "REQ-0001"
    service.create_project("A")
    service.create_module("a", "Loc", "REQ")
    service.create_module("a", "Srs", "REQ")
    assert add(service, "a", "loc") == "REQ-0001"
    assert add(service, "a", "srs") == "REQ-0002"
    assert add(service, "a", "loc") == "REQ-0003"
    service.set_project_references("a", ["b"])
    cross = service.create_link_configuration("a", "Traces", "srs", "sys", "b").id
    local = service.create_link_configuration("a", "Refines", "loc", "loc").id
    service.link_requirements("a", cross, "REQ-0002", "REQ-0001")
    service.link_requirements("a", local, "REQ-0001", "REQ-0003")
    return cross, local


def test_removing_a_local_requirement_with_a_cross_target_id_keeps_the_cross_link(service):
    cross, local = same_id_projects(service)

    service.remove_requirement("a", "loc", "REQ-0001")

    assert pairs(service, "a", cross) == [("REQ-0002", "REQ-0001")]
    assert service.get_requirement("a", "srs", "REQ-0002").attributes[cross] == "> b:REQ-0001"
    assert pairs(service, "a", local) == []
    assert local not in service.get_requirement("a", "loc", "REQ-0003").attributes


def test_removing_the_cross_target_keeps_local_links_with_the_same_id(service):
    cross, local = same_id_projects(service)
    service.take_touched_projects()

    service.remove_requirement("b", "sys", "REQ-0001")

    assert service.take_touched_projects() == {"a", "b"}
    assert pairs(service, "a", cross) == []
    assert cross not in service.get_requirement("a", "srs", "REQ-0002").attributes
    assert pairs(service, "a", local) == [("REQ-0001", "REQ-0003")]
    assert service.get_requirement("a", "loc", "REQ-0001").attributes[local] == "> REQ-0003"


def test_removing_a_cross_source_cleans_only_its_pairs(service):
    two_projects(service)
    add(service, "a", "srs", "Second")
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    service.link_requirements("a", config_id, "SRS-0002", "SYS-0001")
    service.take_touched_projects()

    service.remove_requirement("a", "srs", "SRS-0001")

    assert service.take_touched_projects() == {"a"}
    assert pairs(service, "a", config_id) == [("SRS-0002", "SYS-0001")]


def test_purge_module_cleans_incoming_links_of_referencing_projects(service, root):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0002")
    service.take_touched_projects()

    assert service.purge_module("b", "sys") == 2

    assert service.take_touched_projects() == {"a", "b"}
    assert pairs(service, "a", config_id) == []
    reloaded = ProjectService(ProjectStore(root))
    assert config_id not in reloaded.get_requirement("a", "srs", "SRS-0001").attributes
    links_text = (root / "a" / "links" / f"{config_id}.json").read_text(encoding="utf-8")
    assert links_text == json.dumps(json.loads(links_text), indent=2) + "\n"


def test_purge_project_cleans_incoming_links_of_referencing_projects(service):
    two_projects(service)
    config_id = cross_config(service)
    service.create_project("C")
    service.create_module("c", "Srs", "CSR")
    add(service, "c", "srs")
    service.set_project_references("c", ["b"])
    c_config = service.create_link_configuration("c", "Traces", "srs", "sys", "b").id
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    service.link_requirements("c", c_config, "CSR-0001", "SYS-0002")
    service.take_touched_projects()

    assert service.purge_project("b") == 2

    assert service.take_touched_projects() == {"a", "b", "c"}
    assert pairs(service, "a", config_id) == []
    assert pairs(service, "c", c_config) == []
    assert c_config not in service.get_requirement("c", "srs", "CSR-0001").attributes


@pytest.mark.parametrize("operation", ["remove", "purge_module", "purge_project"])
def test_cleanup_into_a_locked_referencing_project_is_refused_and_changes_nothing(service, root, operation):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    service.lock_project("a", "Release 1")
    before = tree_bytes(root)

    with pytest.raises(ProjectLockedError, match="links to the requirement"):
        if operation == "remove":
            service.remove_requirement("b", "sys", "SYS-0001")
        elif operation == "purge_module":
            service.purge_module("b", "sys")
        else:
            service.purge_project("b")

    assert tree_bytes(root) == before


def test_locked_project_without_links_to_the_removed_requirement_does_not_block(service):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    service.lock_project("a")

    service.remove_requirement("b", "sys", "SYS-0002")

    assert pairs(service, "a", config_id) == [("SRS-0001", "SYS-0001")]


class _ConflictHistory:
    """History stand-in: git on, a sync conflict in exactly one project folder; never runs git."""

    enabled = True

    def __init__(self, conflicted):
        self.conflicted = conflicted
        self.remote_auth = None

    def sync_conflict_message(self, project_dir):
        return "Diverged from the remote" if Path(project_dir).name == self.conflicted else None

    def auto_commit(self, *args, **kwargs):
        raise AssertionError("nothing may be committed after a refused operation")


def test_cleanup_into_a_referencing_project_with_a_sync_conflict_is_refused(service, root):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    before = tree_bytes(root)
    service.history = _ConflictHistory("a")

    with pytest.raises(ConflictError, match="Diverged"):
        service.remove_requirement("b", "sys", "SYS-0001")

    assert tree_bytes(root) == before


def test_unloadable_projects_are_skipped_by_cleanup_and_incoming_links(service, root):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    (root / "broken").mkdir()
    (root / "broken" / "project.json").write_text("{", encoding="utf-8")

    assert len(service.incoming_links("b")) == 1
    service.remove_requirement("b", "sys", "SYS-0001")
    assert pairs(service, "a", config_id) == []
    assert (root / "broken" / "project.json").read_text(encoding="utf-8") == "{"


class _DenyGuard:
    """Permission guard refusing exactly the listed (action, project) pairs."""

    def __init__(self, *denied):
        self.denied = set(denied)

    def check(self, identity, action, project_id=None):
        if (action, project_id) in self.denied:
            raise AccessDeniedError(f"{action} denied on {project_id}")


class _LockService:
    """Module lock service refusing writes to exactly the listed (project, module) pairs."""

    def __init__(self, *locked):
        self.locked = set(locked)

    def check_write(self, identity, project_id, module_id):
        if (project_id, module_id) in self.locked:
            raise ModuleLockedError(f"Module '{module_id}' of '{project_id}' is checked out")


@pytest.mark.parametrize("operation", ["remove", "purge_module", "purge_project"])
def test_cleanup_into_an_unwritable_referencing_project_is_refused_and_changes_nothing(
    service, root, monkeypatch, operation
):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    before = tree_bytes(root)
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _DenyGuard((ACTION_PROJECT_WRITE, "a")))

    with pytest.raises(AccessDeniedError, match="you may not change it"):
        if operation == "remove":
            service.remove_requirement("b", "sys", "SYS-0001")
        elif operation == "purge_module":
            service.purge_module("b", "sys")
        else:
            service.purge_project("b")

    assert tree_bytes(root) == before


@pytest.mark.parametrize("operation", ["remove", "purge_module", "purge_project"])
def test_cleanup_into_a_checked_out_source_module_is_refused_and_changes_nothing(
    service, root, monkeypatch, operation
):
    two_projects(service)
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    before = tree_bytes(root)
    monkeypatch.setattr(extensions.HOOKS, "lock_service", _LockService(("a", "srs")))

    with pytest.raises(ModuleLockedError, match="'srs' of 'a'"):
        if operation == "remove":
            service.remove_requirement("b", "sys", "SYS-0001")
        elif operation == "purge_module":
            service.purge_module("b", "sys")
        else:
            service.purge_project("b")

    assert tree_bytes(root) == before


def test_checked_out_unrelated_module_of_referencing_project_does_not_block_cleanup(service, monkeypatch):
    two_projects(service)
    service.create_module("a", "Other", "OTH")
    config_id = cross_config(service)
    service.link_requirements("a", config_id, "SRS-0001", "SYS-0001")
    monkeypatch.setattr(extensions.HOOKS, "lock_service", _LockService(("a", "other")))

    service.remove_requirement("b", "sys", "SYS-0001")

    assert pairs(service, "a", config_id) == []


def test_hand_edited_configuration_targeting_its_own_project_is_local(service, root):
    service.create_project("P")
    service.create_module("p", "Sys", "SYS")
    service.create_module("p", "Srs", "SRS")
    sys_req = add(service, "p", "sys")
    srs_req = add(service, "p", "srs")
    config_id = service.create_link_configuration("p", "Satisfies", "srs", "sys").id
    service.link_requirements("p", config_id, srs_req, sys_req)
    path = root / "p" / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["link_configurations"][0]["target_project_id"] = "p"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    assert service.get_project("p").link_configurations[0].target_project_id is None
    assert [o["config"].id for o in service.link_options("p", srs_req, sys_req)] == [config_id]

    # Removing the target cleans the pair and the source's mirror, as for any local configuration.
    service.remove_requirement("p", "sys", sys_req)
    assert pairs(service, "p", config_id) == []
    assert config_id not in service.get_requirement("p", "srs", srs_req).attributes


# --- incoming links ---------------------------------------------------------------------


def test_incoming_links_are_computed_on_read_and_filtered_by_module(service):
    two_projects(service)
    service.create_module("b", "Other", "OTH")
    add(service, "b", "other")
    to_sys = cross_config(service, "Traces")
    to_other = cross_config(service, "Refines", target="other")
    service.link_requirements("a", to_sys, "SRS-0001", "SYS-0002")
    service.link_requirements("a", to_sys, "SRS-0001", "SYS-0001")
    service.link_requirements("a", to_other, "SRS-0001", "OTH-0001")

    every = service.incoming_links("b")
    assert [e["target_id"] for e in every] == ["OTH-0001", "SYS-0001", "SYS-0002"]
    assert every[1] == {
        "project_id": "a",
        "project_name": "A",
        "config_id": to_sys,
        "config_name": "Traces",
        "source_module_id": "srs",
        "source_id": "SRS-0001",
        "target_module_id": "sys",
        "target_id": "SYS-0001",
    }
    assert [e["target_id"] for e in service.incoming_links("b", "sys")] == ["SYS-0001", "SYS-0002"]
    assert [e["config_id"] for e in service.incoming_links("b", "other")] == [to_other]
    assert service.incoming_links("a") == []
    with pytest.raises(FileNotFoundError):
        service.incoming_links("b", "nope")
    with pytest.raises(FileNotFoundError):
        service.incoming_links("ghost")


# --- exports, templates, formats, history ------------------------------------------------


def export_setup(service):
    two_projects(service)
    service.create_module("a", "Sys", "ASY")
    add(service, "a", "sys")
    cross = cross_config(service, "Traces")
    local = service.create_link_configuration("a", "Refines", "srs", "sys").id
    service.link_requirements("a", cross, "SRS-0001", "SYS-0001")
    return cross, local


def test_reqif_export_has_no_spec_relation_for_cross_project_links(service):
    cross, local = export_setup(service)

    xml = service.export_reqif("a")
    assert f"SPEC-RELATION-TYPE-{cross}" not in xml
    assert f"SPEC-RELATION-TYPE-{local}" in xml
    assert "SPEC-RELATION-1" not in xml
    # The qualified mirror is exported like any attribute value.
    assert "b:SYS-0001" in xml

    service.link_requirements("a", local, "SRS-0001", "ASY-0001")
    xml = service.export_reqif("a")
    assert "SPEC-RELATION-1" in xml
    assert "SPEC-RELATION-2" not in xml


def test_csv_export_keeps_the_qualified_mirror_text(service):
    export_setup(service)
    csv_text = service.export_csv("a")
    assert "> b:SYS-0001" in csv_text


def test_save_as_template_drops_cross_project_configurations_and_their_view_columns(service):
    cross, local = export_setup(service)
    service.create_view("a", "Trace view", ["object_text", cross, local])

    summary = service.save_project_as_template("a", "From A")
    text = service.store.read_user_template_text(summary["id"].split(":", 1)[1])
    data = json.loads(text)

    assert [c["key"] for c in data["link_configurations"]] == [local]
    assert "target_project_id" not in text
    assert cross not in text
    [view] = data["views"]
    assert view["attribute_ids"] == ["object_text", f"link:{local}"]


def test_format_5_project_loads_without_rewriting_and_ignores_stray_keys(service, root):
    service.create_project("B")
    service.create_project("Old")
    path = root / "old" / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format_version"] = 5
    data["references"] = ["b"]
    data["link_configurations"] = [
        {"id": "traces", "name": "Traces", "source_module_id": "x", "target_module_id": "y", "target_project_id": "b"}
    ]
    original = json.dumps(data, indent=2) + "\n"
    path.write_text(original, encoding="utf-8")

    project = service.get_project("old")
    assert project.references == []
    assert [c.target_project_id for c in project.link_configurations] == [None]
    assert service.project_references("old")["references"] == []
    assert service.project_references("b")["referenced_by"] == []
    service.incoming_links("b")
    assert path.read_text(encoding="utf-8") == original


def test_history_entries_qualify_cross_project_targets():
    project = {
        "id": "a",
        "name": "A",
        "attributes": [{"id": "traces", "name": "Traces", "system": True}],
        "link_configurations": [{"id": "traces", "name": "Traces", "target_project_id": "b"}],
    }
    new = (json.dumps({"pairs": [{"source_id": "SRS-0001", "target_id": "SYS-0001"}]}, indent=2)).encode()
    old = (json.dumps({"pairs": [{"source_id": "SRS-0001", "target_id": "SYS-0002"}]}, indent=2)).encode()
    entries = describe_changes([FileChange("links/traces.json", old, new)], project)
    assert entries == [
        "Link SRS-0001 → b:SYS-0001 added (Traces)",
        "Link SRS-0001 → b:SYS-0002 removed (Traces)",
    ]


# --- REST and WebSocket ------------------------------------------------------------------


def rest_two_projects(client, reference=True):
    for name, module, prefix in (("B", "Sys", "SYS"), ("A", "Srs", "SRS")):
        project_id = client.post("/projects", json={"name": name}).json()["id"]
        assert client.post(f"/projects/{project_id}/modules", json={"name": module, "prefix": prefix}).status_code == 200
    for module_path in ("b/modules/sys", "b/modules/sys", "a/modules/srs"):
        assert client.post(f"/projects/{module_path}/requirements", json={"attributes": {"object_text": "T"}}).status_code == 200
    if reference:
        assert client.put("/projects/a/references", json={"references": ["b"]}).status_code == 200


def rest_cross_config(client):
    resp = client.post(
        "/projects/a/link-configurations",
        json={"name": "Traces", "source_module_id": "srs", "target_module_id": "sys", "target_project_id": "b"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_rest_references_status_codes(client, tmp_path):
    rest_two_projects(client, reference=False)
    (tmp_path / "newer").mkdir()
    (tmp_path / "newer" / "project.json").write_text(
        json.dumps({"format_version": PROJECT_FORMAT.current + 1, "id": "newer", "name": "N"}) + "\n",
        encoding="utf-8",
    )
    url = "/projects/a/references"
    assert client.put(url, json={"references": ["a"]}).status_code == 400
    assert client.put(url, json={"references": "b"}).status_code == 400
    assert client.put(url, json={"references": [f"p{i}" for i in range(MAX_PROJECT_REFERENCES + 1)]}).status_code == 400
    assert client.put(url, json={"references": ["ghost"]}).status_code == 404
    assert client.put(url, json={"references": ["newer"]}).status_code == 409
    assert client.put("/projects/ghost/references", json={"references": []}).status_code == 404
    assert client.get("/projects/ghost/references").status_code == 404

    resp = client.put(url, json={"references": ["b", "b"]})
    assert resp.status_code == 200
    assert resp.json()["references"] == ["b"]
    assert client.get(url).json()["references"][0]["id"] == "b"


def test_rest_reference_in_use_and_cross_configuration_errors(client):
    rest_two_projects(client, reference=False)
    body = {"name": "Traces", "source_module_id": "srs", "target_module_id": "sys", "target_project_id": "b"}
    assert client.post("/projects/a/link-configurations", json=body).status_code == 400
    client.put("/projects/a/references", json={"references": ["b"]})
    assert client.post("/projects/a/link-configurations", json={**body, "target_module_id": "nope"}).status_code == 404
    config_id = rest_cross_config(client)

    resp = client.put("/projects/a/references", json={"references": []})
    assert resp.status_code == 400
    assert "Traces" in resp.json()["detail"]

    link = {"config_id": config_id, "source_id": "SRS-0001", "target_id": "c:SYS-0001"}
    assert client.post("/projects/a/links", json=link).status_code == 400
    assert client.post("/projects/a/links", json={**link, "target_id": "SYS-0404"}).status_code == 404


def test_rest_cross_link_unlink_and_incoming_links(client):
    rest_two_projects(client)
    config_id = rest_cross_config(client)

    resp = client.post("/projects/a/links", json={"config_id": config_id, "source_id": "SRS-0001", "target_id": "b:SYS-0001"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"]["attributes"][config_id] == "> b:SYS-0001"
    assert resp.json()["target"]["id"] == "SYS-0001"
    assert config_id not in resp.json()["target"]["attributes"]

    incoming = client.get("/projects/b/incoming-links", params={"module_id": "sys"})
    assert incoming.status_code == 200
    assert [(e["project_id"], e["source_id"], e["target_id"]) for e in incoming.json()] == [("a", "SRS-0001", "SYS-0001")]
    assert client.get("/projects/b/incoming-links", params={"module_id": "nope"}).status_code == 404

    resp = client.request(
        "DELETE", "/projects/a/links", json={"config_id": config_id, "source_id": "SRS-0001", "target_id": "SYS-0404"}
    )
    assert resp.status_code == 200
    assert resp.json()["target"] is None
    resp = client.request(
        "DELETE", "/projects/a/links", json={"config_id": config_id, "source_id": "SRS-0001", "target_id": "b:SYS-0001"}
    )
    assert resp.status_code == 200
    assert config_id not in resp.json()["source"]["attributes"]
    assert client.get("/projects/b/incoming-links").json() == []


def test_rest_and_websocket_references_agree_and_broadcast(client):
    rest_two_projects(client, reference=False)
    client.post("/projects", json={"name": "C"})

    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        resp = client.put("/projects/a/references", json={"references": ["b"]})
        assert resp.status_code == 200
        assert json.loads(ws1.receive_text()) == {"event": "changed", "project_id": "a"}
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": "a"}

        result = rpc(ws1, "project.set_references", {"id": "a", "references": ["c", "b"]})
        assert result["result"]["references"] == ["c", "b"]
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": "a"}

        ws_info = rpc(ws1, "project.references", {"id": "a"}, 2)["result"]
        assert ws_info == client.get("/projects/a/references").json()

        # Failures do not broadcast: the next event is the marker mutation.
        assert rpc(ws1, "project.set_references", {"id": "a", "references": ["a"]}, 3)["error"]["code"] == -32602
        assert rpc(ws1, "project.set_references", {"id": "a", "references": ["ghost"]}, 4)["error"]["code"] == -32001
        assert client.put("/projects/a/references", json={"references": ["a"]}).status_code == 400
        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws1.receive_text()) == {"event": "changed", "project_id": marker}
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": marker}


def test_websocket_cross_link_and_incoming_links_match_rest(client):
    rest_two_projects(client)
    with client.websocket_connect("/ws") as ws:
        config = rpc(ws, "link_configuration.create", {
            "project_id": "a", "name": "Traces", "source_module_id": "srs", "target_module_id": "sys",
            "target_project_id": "b",
        })["result"]
        assert config["target_project_id"] == "b"
        linked = rpc(ws, "requirement.link", {
            "project_id": "a", "config_id": config["id"], "source_id": "SRS-0001", "target_id": "SYS-0002",
        }, 2)["result"]
        assert linked["source"]["attributes"][config["id"]] == "> b:SYS-0002"
        incoming = rpc(ws, "link.incoming", {"project_id": "b", "module_id": "sys"}, 3)["result"]
        assert incoming == client.get("/projects/b/incoming-links", params={"module_id": "sys"}).json()
        bad = rpc(ws, "requirement.link", {
            "project_id": "a", "config_id": config["id"], "source_id": "SRS-0001", "target_id": "x:SYS-0001",
        }, 4)
        assert bad["error"]["code"] == -32602


def test_rest_requirement_removal_broadcasts_every_touched_project(client):
    rest_two_projects(client)
    config_id = rest_cross_config(client)
    client.post("/projects/a/links", json={"config_id": config_id, "source_id": "SRS-0001", "target_id": "SYS-0001"})

    with client.websocket_connect("/ws") as ws:
        assert client.delete("/projects/b/modules/sys/requirements/SYS-0001").status_code == 200
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": "b"}
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": "a"}
        # Removing a requirement nobody links to only announces its own project.
        assert client.delete("/projects/b/modules/sys/requirements/SYS-0002").status_code == 200
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": "b"}
        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": marker}

    source = client.get("/projects/a/modules/srs/requirements/SRS-0001").json()
    assert config_id not in source["attributes"]


@pytest.mark.parametrize("operation", ["module", "project"])
def test_websocket_purge_broadcasts_every_touched_project_to_other_clients(client, operation):
    rest_two_projects(client)
    config_id = rest_cross_config(client)
    client.post("/projects/a/links", json={"config_id": config_id, "source_id": "SRS-0001", "target_id": "SYS-0001"})

    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        if operation == "module":
            result = rpc(ws1, "module.purge", {"project_id": "b", "id": "sys"})
        else:
            result = rpc(ws1, "project.purge", {"id": "b"})
        assert result["result"] == {"purged": 2}
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": "b"}
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": "a"}

    assert client.get("/projects/b/incoming-links").json() == []


def test_rest_cleanup_into_locked_project_is_423_and_does_not_broadcast(client):
    rest_two_projects(client)
    config_id = rest_cross_config(client)
    client.post("/projects/a/links", json={"config_id": config_id, "source_id": "SRS-0001", "target_id": "SYS-0001"})
    assert client.put("/projects/a/lock", json={"reason": "Release"}).status_code == 200

    with client.websocket_connect("/ws") as ws:
        assert client.delete("/projects/b/modules/sys/requirements/SYS-0001").status_code == 423
        assert client.post("/projects/b/modules/sys/purge").status_code == 423
        assert client.post("/projects/b/purge").status_code == 423
        error = rpc(ws, "requirement.remove", {"project_id": "b", "module_id": "sys", "id": "SYS-0001"})
        assert error["error"]["code"] == -32004
        marker = client.post("/projects", json={"name": "Marker"}).json()["id"]
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": marker}

    assert client.get("/projects/b/modules/sys/requirements/SYS-0001").status_code == 200
    assert len(client.get("/projects/b/incoming-links").json()) == 1
