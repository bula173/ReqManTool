"""Module folders (project format 7): the module tree in ``module_order``.

Service rules (server ids, sibling-unique names, delete only empty, no
cycles, depth, index moves, no-op moves), the 6 → 7 migration, tolerant
reading of hand-edited trees, history messages, REST/WebSocket parity and
broadcasts, and the template ``module_tree`` round trip.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from reqman.formats import PROJECT_FORMAT, upgrade
from reqman.git_repo import GitHistory, GitSettings
from reqman.history import FileChange, describe_changes
from reqman.service import MAX_MODULE_FOLDER_DEPTH, MAX_MODULE_FOLDER_NAME_LENGTH, ProjectService
from reqman.store import ProjectStore

RBC = "builtin:rbc_template"
CHECKOUT = Path(__file__).resolve().parents[2]


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


def _assert_no_broadcast(ws, request_id=999):
    """The next message on ``ws`` is the reply to a harmless request, not a queued event."""
    response = _rpc(ws, "template.list", {}, request_id)
    assert response.get("id") == request_id, response


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


def _path(service, project_id) -> Path:
    return service.store.root / project_id / "project.json"


def _raw(service, project_id) -> dict:
    return json.loads(_path(service, project_id).read_text())


def _write_raw(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def _project(service, *modules: str):
    project = service.create_project("P")
    for name in modules:
        service.create_module(project.id, name, "R")
    return project


def _tree(service, project_id):
    return service.get_module_tree(project_id)


def M(module_id):
    return {"module": module_id}


def F(folder_id, name, *children):
    return {"folder": folder_id, "name": name, "children": list(children)}


# --- create ------------------------------------------------------------------------


def test_create_folder_allocates_slug_ids_appends_and_persists(service):
    project = _project(service, "A")
    top = service.create_module_folder(project.id, "  System Specs  ")
    assert top == {"id": "system-specs", "name": "System Specs", "parent_id": None}
    nested = service.create_module_folder(project.id, "Drafts", top["id"])
    assert nested == {"id": "drafts", "name": "Drafts", "parent_id": "system-specs"}
    # A name equal to a folder elsewhere gets a suffixed id (ids are unique in the project).
    other = service.create_module_folder(project.id, "drafts")
    assert other["id"] != "drafts" and other["id"].startswith("drafts")
    assert other["name"] == "drafts"
    # A name without slug characters falls back to "folder".
    assert service.create_module_folder(project.id, "!!!")["id"] == "folder"

    expected = [M("a"), F("system-specs", "System Specs", F("drafts", "Drafts")), F(other["id"], "drafts"), F("folder", "!!!")]
    assert _tree(service, project.id) == expected
    fresh = ProjectService(ProjectStore(service.store.root))
    assert fresh.get_module_tree(project.id) == expected

    text = _path(service, project.id).read_text()
    data = json.loads(text)
    assert text == json.dumps(data, indent=2) + "\n"
    assert data["format_version"] == PROJECT_FORMAT.current
    assert data["module_order"] == expected
    # Folder entries keep the key order folder, name, children.
    assert list(data["module_order"][1]) == ["folder", "name", "children"]


def test_empty_folder_survives_and_project_without_modules_stores_it(service):
    project = service.create_project("P")
    service.create_module_folder(project.id, "Empty")
    assert _raw(service, project.id)["module_order"] == [F("empty", "Empty")]
    assert service.list_modules(project.id) == []


def test_folder_names_are_unique_among_siblings_case_insensitively(service):
    project = _project(service)
    first = service.create_module_folder(project.id, "Specs")
    before = _snapshot(service.store.root / project.id)
    with pytest.raises(ValueError, match="A folder named 'Specs' already exists here"):
        service.create_module_folder(project.id, "  SPECS ")
    assert _snapshot(service.store.root / project.id) == before
    # The same name in another folder is fine.
    service.create_module_folder(project.id, "Specs", first["id"])
    second = service.create_module_folder(project.id, "Other")
    with pytest.raises(ValueError, match="already exists here"):
        service.rename_module_folder(project.id, second["id"], "specs")
    # Renaming a folder to a different case of its own name is allowed.
    assert service.rename_module_folder(project.id, first["id"], "SPECS")["name"] == "SPECS"


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "Folder name must not be empty"),
        ("   ", "Folder name must not be empty"),
        (None, "Folder name must not be empty"),
        (5, "Folder name must not be empty"),
        ("x" * (MAX_MODULE_FOLDER_NAME_LENGTH + 1), "at most 200 characters"),
    ],
)
def test_invalid_folder_names_are_refused_and_write_nothing(service, name, message):
    project = _project(service, "A")
    folder = service.create_module_folder(project.id, "Keep")
    before = _snapshot(service.store.root / project.id)
    with pytest.raises(ValueError, match=message):
        service.create_module_folder(project.id, name)
    with pytest.raises(ValueError, match=message):
        service.rename_module_folder(project.id, folder["id"], name)
    assert _snapshot(service.store.root / project.id) == before


def test_folder_name_of_exactly_200_characters_is_allowed(service):
    project = _project(service)
    name = "y" * MAX_MODULE_FOLDER_NAME_LENGTH
    assert service.create_module_folder(project.id, f"  {name}  ")["name"] == name


def test_unknown_or_malformed_parent_is_refused(service):
    project = _project(service)
    before = _snapshot(service.store.root / project.id)
    with pytest.raises(FileNotFoundError, match="Module folder 'ghost' not found"):
        service.create_module_folder(project.id, "X", "ghost")
    with pytest.raises(ValueError, match="Folder id must be a string or null"):
        service.create_module_folder(project.id, "X", 5)
    with pytest.raises(FileNotFoundError):
        service.create_module_folder("no-such-project", "X")
    assert _snapshot(service.store.root / project.id) == before


def test_nesting_is_limited_to_16_levels(service):
    project = _project(service)
    parent = None
    for level in range(1, MAX_MODULE_FOLDER_DEPTH + 1):
        parent = service.create_module_folder(project.id, f"L{level}", parent)["id"]
    before = _snapshot(service.store.root / project.id)
    with pytest.raises(ValueError, match="nested at most 16 levels deep"):
        service.create_module_folder(project.id, "Too deep", parent)
    assert _snapshot(service.store.root / project.id) == before


# --- rename / delete -----------------------------------------------------------------


def test_rename_keeps_id_and_rename_to_same_name_writes_nothing(service):
    project = _project(service, "A")
    folder = service.create_module_folder(project.id, "Specs")
    service.move_module(project.id, "a", folder["id"])
    assert service.rename_module_folder(project.id, "specs", " Specifications ") == {
        "id": "specs", "name": "Specifications", "parent_id": None,
    }
    assert _tree(service, project.id) == [F("specs", "Specifications", M("a"))]
    before = _path(service, project.id).read_bytes()
    service.rename_module_folder(project.id, "specs", "Specifications")
    assert _path(service, project.id).read_bytes() == before
    with pytest.raises(FileNotFoundError):
        service.rename_module_folder(project.id, "ghost", "X")


def test_delete_only_empty_folders(service):
    project = _project(service, "A")
    with_module = service.create_module_folder(project.id, "With module")
    with_folder = service.create_module_folder(project.id, "With folder")
    inner = service.create_module_folder(project.id, "Inner", with_folder["id"])
    service.move_module(project.id, "a", with_module["id"])
    before = _snapshot(service.store.root / project.id)
    for folder_id in (with_module["id"], with_folder["id"]):
        with pytest.raises(ValueError, match="is not empty: move its modules and folders out first"):
            service.delete_module_folder(project.id, folder_id)
    with pytest.raises(FileNotFoundError):
        service.delete_module_folder(project.id, "ghost")
    with pytest.raises(ValueError, match="Folder id must be a string"):
        service.delete_module_folder(project.id, None)
    assert _snapshot(service.store.root / project.id) == before

    assert service.delete_module_folder(project.id, inner["id"]) == {"id": "inner", "deleted": True}
    assert service.delete_module_folder(project.id, with_folder["id"])["deleted"] is True
    assert _tree(service, project.id) == [F("with-module", "With module", M("a"))]
    # The module and its data are untouched by folder operations.
    assert service.get_module(project.id, "a").name == "A"


# --- moves ---------------------------------------------------------------------------


def test_move_module_into_folder_at_index_and_back_to_top_level(service):
    project = _project(service, "A", "B", "C", "D")
    folder = service.create_module_folder(project.id, "F")["id"]
    service.move_module(project.id, "a", folder)
    service.move_module(project.id, "b", folder)
    moved = service.move_module(project.id, "c", folder, 0)
    assert moved.id == "c"
    assert _tree(service, project.id) == [M("d"), F("f", "F", M("c"), M("a"), M("b"))]
    # An index past the end appends.
    service.move_module(project.id, "d", folder, 99)
    assert _tree(service, project.id) == [F("f", "F", M("c"), M("a"), M("b"), M("d"))]
    # Within the same folder with an index repositions it.
    service.move_module(project.id, "d", folder, 1)
    assert _tree(service, project.id) == [F("f", "F", M("c"), M("d"), M("a"), M("b"))]
    # null = the top level; index 0 puts it before the folder.
    service.move_module(project.id, "a", None, 0)
    assert _tree(service, project.id) == [M("a"), F("f", "F", M("c"), M("d"), M("b"))]
    assert [m.id for m in service.list_modules(project.id)] == ["a", "c", "d", "b"]


def test_move_to_where_the_module_already_is_writes_nothing(service):
    project = _project(service, "A", "B")
    folder = service.create_module_folder(project.id, "F")["id"]
    service.move_module(project.id, "a", folder)
    service.move_module(project.id, "b", folder)
    before = _path(service, project.id).read_bytes()
    service.move_module(project.id, "b", folder)  # already last in F
    service.move_module(project.id, "a", folder, 0)  # already first in F
    service.move_module(project.id, "b", folder, 50)
    assert _path(service, project.id).read_bytes() == before
    # Without an index, a module already in the target folder keeps its place
    # (a drop onto its own folder), even when it is not last; nothing is written.
    service.move_module(project.id, "a", folder)
    assert _tree(service, project.id) == [F("f", "F", M("a"), M("b"))]
    assert _path(service, project.id).read_bytes() == before
    # With an index it is a real move.
    service.move_module(project.id, "a", folder, 1)
    assert _tree(service, project.id) == [F("f", "F", M("b"), M("a"))]


def test_move_into_the_current_folder_without_index_keeps_position_and_commits_nothing(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    history = GitHistory(GitSettings(enabled=True, squash_window_seconds=0))
    service = ProjectService(ProjectStore(root), history=history)
    project = _project(service, "A", "B")
    folder = service.create_module_folder(project.id, "F")["id"]
    service.move_module(project.id, "a", folder)
    service.move_module(project.id, "b", folder)
    inner = service.create_module_folder(project.id, "G", folder)["id"]
    first = service.create_module_folder(project.id, "H")["id"]
    service.move_module_folder(project.id, first, None, 0)
    head = _git(root / project.id, "rev-parse", "HEAD")
    before = _path(service, project.id).read_bytes()
    service.move_module(project.id, "a", folder)  # first of three: stays first
    service.move_module(project.id, "b", folder)  # middle: stays
    service.move_module_folder(project.id, inner, folder)  # already last
    service.move_module_folder(project.id, first, None)  # first at the top level: stays
    assert _path(service, project.id).read_bytes() == before
    assert _git(root / project.id, "rev-parse", "HEAD") == head
    assert _git(root / project.id, "status", "--porcelain") == ""
    assert _tree(service, project.id) == [F("h", "H"), F("f", "F", M("a"), M("b"), F("g", "G"))]


@pytest.mark.parametrize("index", [-1, True, "1", 1.5])
def test_move_with_invalid_index_is_refused(service, index):
    project = _project(service, "A")
    folder = service.create_module_folder(project.id, "F")["id"]
    before = _snapshot(service.store.root / project.id)
    with pytest.raises(ValueError, match="Invalid index"):
        service.move_module(project.id, "a", folder, index)
    with pytest.raises(ValueError, match="Invalid index"):
        service.move_module_folder(project.id, folder, None, index)
    assert _snapshot(service.store.root / project.id) == before


def test_move_module_errors(service):
    project = _project(service, "A")
    before = _snapshot(service.store.root / project.id)
    with pytest.raises(FileNotFoundError, match="Module folder 'ghost' not found"):
        service.move_module(project.id, "a", "ghost")
    with pytest.raises(FileNotFoundError):
        service.move_module(project.id, "ghost", None)
    with pytest.raises(ValueError, match="Folder id must be a string or null"):
        service.move_module(project.id, "a", 3)
    assert _snapshot(service.store.root / project.id) == before


def test_move_folder_with_content_and_index(service):
    project = _project(service, "A", "B")
    outer = service.create_module_folder(project.id, "Outer")["id"]
    inner = service.create_module_folder(project.id, "Inner")["id"]
    service.move_module(project.id, "a", inner)
    summary = service.move_module_folder(project.id, inner, outer)
    assert summary == {"id": "inner", "name": "Inner", "parent_id": "outer"}
    assert _tree(service, project.id) == [M("b"), F("outer", "Outer", F("inner", "Inner", M("a")))]
    service.move_module_folder(project.id, inner, None, 0)
    assert _tree(service, project.id) == [F("inner", "Inner", M("a")), M("b"), F("outer", "Outer")]
    # A move to where it already is writes nothing.
    before = _path(service, project.id).read_bytes()
    service.move_module_folder(project.id, inner, None, 0)
    service.move_module_folder(project.id, outer, None)
    assert _path(service, project.id).read_bytes() == before


def test_folder_cannot_move_into_itself_or_a_descendant(service):
    project = _project(service)
    a = service.create_module_folder(project.id, "A")["id"]
    b = service.create_module_folder(project.id, "B", a)["id"]
    c = service.create_module_folder(project.id, "C", b)["id"]
    before = _snapshot(service.store.root / project.id)
    with pytest.raises(ValueError, match="cannot be moved into itself"):
        service.move_module_folder(project.id, a, a)
    for target in (b, c):
        with pytest.raises(ValueError, match="into one of its own subfolders"):
            service.move_module_folder(project.id, a, target)
    with pytest.raises(FileNotFoundError):
        service.move_module_folder(project.id, a, "ghost")
    with pytest.raises(FileNotFoundError):
        service.move_module_folder(project.id, "ghost", None)
    assert _snapshot(service.store.root / project.id) == before
    # Moving a descendant out to an ancestor is fine.
    service.move_module_folder(project.id, c, a)
    assert _tree(service, project.id) == [F("a", "A", F("b", "B"), F("c", "C"))]


def test_folder_move_keeps_sibling_names_unique(service):
    project = _project(service)
    target = service.create_module_folder(project.id, "Target")["id"]
    service.create_module_folder(project.id, "Specs", target)
    loose = service.create_module_folder(project.id, "SPECS")["id"]
    before = _snapshot(service.store.root / project.id)
    with pytest.raises(ValueError, match="A folder named 'Specs' already exists here"):
        service.move_module_folder(project.id, loose, target)
    assert _snapshot(service.store.root / project.id) == before


def test_folder_move_respects_the_depth_limit_including_subfolders(service):
    project = _project(service)
    parent = None
    for level in range(1, MAX_MODULE_FOLDER_DEPTH):  # a chain 15 deep
        parent = service.create_module_folder(project.id, f"L{level}", parent)["id"]
    two_levels = service.create_module_folder(project.id, "Two")["id"]
    service.create_module_folder(project.id, "Child", two_levels)
    before = _snapshot(service.store.root / project.id)
    # 15 + a folder 2 levels high = 17 > 16.
    with pytest.raises(ValueError, match="nested at most 16 levels deep"):
        service.move_module_folder(project.id, two_levels, parent)
    assert _snapshot(service.store.root / project.id) == before
    single = service.create_module_folder(project.id, "Single")["id"]
    service.move_module_folder(project.id, single, parent)  # exactly 16: allowed


# --- reorder -------------------------------------------------------------------------


def test_module_reorder_moves_among_entries_of_its_own_folder(service):
    project = _project(service, "A", "B", "C")
    folder = service.create_module_folder(project.id, "F")["id"]
    service.move_module(project.id, "a", folder)
    service.move_module(project.id, "b", folder)
    service.create_module_folder(project.id, "Sub", folder)
    # F: a, b, Sub ; top: c, F
    service.reorder_module(project.id, "b", "down")
    assert _tree(service, project.id) == [M("c"), F("f", "F", M("a"), F("sub", "Sub"), M("b"))]
    before = _path(service, project.id).read_bytes()
    service.reorder_module(project.id, "b", "down")  # last in its folder: no-op
    service.reorder_module(project.id, "a", "up")  # first in its folder: no-op, never leaves F
    assert _path(service, project.id).read_bytes() == before
    service.reorder_module(project.id, "c", "down")  # top level: swaps with folder F
    assert _tree(service, project.id) == [F("f", "F", M("a"), F("sub", "Sub"), M("b")), M("c")]


def test_folder_reorder_up_down_boundaries_and_errors(service):
    project = _project(service, "A")
    service.create_module_folder(project.id, "X")
    service.create_module_folder(project.id, "Y")
    assert service.reorder_module_folder(project.id, "y", "up") == {"id": "y", "name": "Y", "parent_id": None}
    assert _tree(service, project.id) == [M("a"), F("y", "Y"), F("x", "X")]
    service.reorder_module_folder(project.id, "y", "up")
    before = _path(service, project.id).read_bytes()
    service.reorder_module_folder(project.id, "y", "up")
    service.reorder_module_folder(project.id, "x", "down")
    assert _path(service, project.id).read_bytes() == before
    with pytest.raises(ValueError, match="Invalid direction 'left'"):
        service.reorder_module_folder(project.id, "x", "left")
    with pytest.raises(FileNotFoundError):
        service.reorder_module_folder(project.id, "ghost", "up")


# --- create_module with folder_id ----------------------------------------------------


def test_create_module_in_folder_and_unknown_folder_writes_nothing(service):
    project = _project(service, "A")
    folder = service.create_module_folder(project.id, "F")["id"]
    module = service.create_module(project.id, "B", "B", folder_id=folder)
    assert module.id == "b"
    service.create_module(project.id, "C", "C", folder_id=folder)
    assert _tree(service, project.id) == [M("a"), F("f", "F", M("b"), M("c"))]

    before = _snapshot(service.store.root / project.id)
    with pytest.raises(FileNotFoundError, match="Module folder 'ghost' not found"):
        service.create_module(project.id, "D", "D", folder_id="ghost")
    with pytest.raises(ValueError, match="Folder id must be a string or null"):
        service.create_module(project.id, "D", "D", folder_id=["f"])
    assert _snapshot(service.store.root / project.id) == before
    assert not (service.store.root / project.id / "modules" / "d").exists()


def test_folders_never_change_module_ids_requirements_or_links(service):
    project = _project(service, "A", "B")
    config = service.create_link_configuration(project.id, "Refines", "a", "b")
    source = service.add_requirement(project.id, "a", {"object_text": "s"})
    target = service.add_requirement(project.id, "b", {"object_text": "t"})
    service.link_requirements(project.id, config.id, source.id, target.id)
    modules_dir = service.store.root / project.id / "modules"
    links_dir = service.store.root / project.id / "links"
    before = (_snapshot(modules_dir), _snapshot(links_dir))

    folder = service.create_module_folder(project.id, "F")["id"]
    service.move_module(project.id, "b", folder)
    service.move_module(project.id, "a", folder, 0)
    service.rename_module_folder(project.id, folder, "G")
    assert (_snapshot(modules_dir), _snapshot(links_dir)) == before
    assert service.add_requirement(project.id, "a", {"object_text": "next"}).id == "R-0003"


# --- format 6 -> 7 and tolerant reading --------------------------------------------------


def test_migration_step_is_lossless_and_drops_only_non_strings():
    v6 = {"format_version": 6, "id": "p", "name": "P", "module_order": ["b", 7, "ghost", None, {"x": 1}, "a"]}
    upgraded = upgrade(dict(v6), PROJECT_FORMAT, "Project 'p'")
    # Every id string, even one naming no module, keeps its position.
    assert upgraded["module_order"] == [M("b"), M("ghost"), M("a")]
    for bad in ("abc", {"a": 1}, 5):
        assert "module_order" not in upgrade({"format_version": 6, "id": "p", "name": "P", "module_order": bad},
                                             PROJECT_FORMAT, "Project 'p'")


def test_version_6_project_loads_without_rewrite_and_is_upgraded_on_save(service):
    project = _project(service, "A", "B", "C")
    path = _path(service, project.id)
    data = _raw(service, project.id)
    data["format_version"] = 6
    data["module_order"] = ["c", 7, "ghost", "a"]
    _write_raw(path, data)
    before = _snapshot(service.store.root / project.id)

    assert [m.id for m in service.list_modules(project.id)] == ["c", "a", "b"]
    assert _tree(service, project.id) == [M("c"), M("a"), M("b")]
    service.list_templates()
    assert _snapshot(service.store.root / project.id) == before  # reading never rewrites

    service.create_module_folder(project.id, "F")
    saved = _raw(service, project.id)
    assert saved["format_version"] == PROJECT_FORMAT.current
    # The stored tree is the effective one: the missing module is dropped, b follows.
    assert saved["module_order"] == [M("c"), M("a"), M("b"), F("f", "F")]
    # No module, requirement or link-pair file was rewritten.
    after = _snapshot(service.store.root / project.id)
    assert {k: v for k, v in after.items() if k != "project.json"} == {
        k: v for k, v in before.items() if k != "project.json"
    }


def test_hand_edited_tree_is_read_tolerantly_without_rewrite(service):
    project = _project(service, "A", "B", "C", "D", "E")
    path = _path(service, project.id)
    data = _raw(service, project.id)
    data["module_order"] = [
        M("c"),
        {"folder": "f", "name": "First", "children": [M("a"), M("ghost"), M("c"), "b"]},
        {"folder": "f", "name": "Duplicate", "children": [M("d")]},  # dissolved, d kept
        {"folder": "empty", "children": None},  # missing name reads as the id
        {"folder": "", "name": "No id", "children": [M("e")]},  # skipped entirely
        42,
        {"other": True},
    ]
    _write_raw(path, data)
    before = path.read_bytes()

    expected = [M("c"), F("f", "First", M("a"), M("b")), M("d"), F("empty", "empty"), M("e")]
    assert _tree(service, project.id) == expected
    assert [m.id for m in service.list_modules(project.id)] == ["c", "a", "b", "d", "e"]
    assert path.read_bytes() == before

    # The next real change stores the effective tree.
    service.reorder_module(project.id, "e", "up")
    assert _raw(service, project.id)["module_order"] == [
        M("c"), F("f", "First", M("a"), M("b")), M("d"), M("e"), F("empty", "empty"),
    ]


def test_folders_nested_deeper_than_64_levels_are_ignored_on_read(service):
    project = _project(service, "A", "B")
    deep: dict = {"folder": "f65", "name": "F65", "children": [M("b")]}
    for level in range(64, 0, -1):
        deep = {"folder": f"f{level}", "name": f"F{level}", "children": [deep]}
    data = _raw(service, project.id)
    data["module_order"] = [M("a"), deep]
    _write_raw(_path(service, project.id), data)
    tree = _tree(service, project.id)
    # Folder 65 is ignored; its module is listed at the top level.
    assert [m.id for m in service.list_modules(project.id)] == ["a", "b"]
    assert tree[-1] == M("b")
    node, depth = tree[1], 1
    while node["children"]:
        node, depth = node["children"][0], depth + 1
    assert depth == 64 and node["folder"] == "f64"


# --- history messages --------------------------------------------------------------------


def _describe(old_order, new_order) -> list[str]:
    old = {"id": "p", "name": "P", "module_order": old_order}
    new = {"id": "p", "name": "P", "module_order": new_order}
    change = FileChange("project.json", json.dumps(old).encode(), json.dumps(new).encode())
    return describe_changes([change], new)


def test_history_messages_for_folder_operations():
    assert _describe([M("a")], [M("a"), F("f", "Specs")]) == ["Module folder 'Specs' added"]
    assert _describe([M("a"), F("f", "Specs")], [M("a")]) == ["Module folder 'Specs' removed"]
    assert _describe([F("f", "Specs")], [F("f", "Specifications")]) == [
        "Module folder 'Specs' renamed to 'Specifications'"
    ]
    assert _describe([M("a"), F("f", "Specs")], [F("f", "Specs", M("a"))]) == ["Module 'a' moved to folder 'Specs'"]
    assert _describe([F("f", "Specs", M("a"))], [M("a"), F("f", "Specs")]) == ["Module 'a' moved to the top level"]
    assert _describe([F("f", "Specs"), F("g", "Other")], [F("g", "Other", F("f", "Specs"))]) == [
        "Module folder 'Specs' moved to folder 'Other'"
    ]
    assert _describe([M("a"), M("b")], [M("b"), M("a")]) == ["Module order changed"]
    assert _describe([M("a"), F("f", "X")], [F("f", "X"), M("a")]) == ["Module order changed"]


def test_format_6_to_7_upgrade_alone_produces_no_message():
    assert _describe(["a", "b"], [M("a"), M("b")]) == []


def _git(path: Path, *args: str) -> str:
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_folder_operations_are_committed_with_descriptive_messages(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    history = GitHistory(GitSettings(enabled=True, squash_window_seconds=0))
    service = ProjectService(ProjectStore(root), history=history)
    project = service.create_project("P")
    service.create_module(project.id, "A", "A")
    service.create_module_folder(project.id, "Specs")
    service.move_module(project.id, "a", "specs")
    service.move_module(project.id, "a", "specs")  # no-op: no commit
    service.rename_module_folder(project.id, "specs", "Specifications")
    service.move_module(project.id, "a", None)
    service.delete_module_folder(project.id, "specs")
    subjects = _git(root / project.id, "log", "--format=%s").splitlines()
    assert subjects[:5] == [
        "Module folder 'Specifications' removed",
        "Module 'a' moved to the top level",
        "Module folder 'Specs' renamed to 'Specifications'",
        "Module 'a' moved to folder 'Specs'",
        "Module folder 'Specs' added",
    ]
    assert _git(root / project.id, "status", "--porcelain") == ""


# --- REST / WebSocket ------------------------------------------------------------------


def _rest_project(client, *modules: str) -> str:
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    for name in modules:
        assert client.post(f"/projects/{project_id}/modules", json={"name": name, "prefix": "R"}).status_code == 200
    return project_id


def test_rest_folder_lifecycle_broadcasts_changed_after_each_mutation(client, tmp_path):
    project_id = _rest_project(client, "A", "B")
    base = f"/projects/{project_id}"
    changed = {"event": "changed", "project_id": project_id}
    with client.websocket_connect("/ws") as ws:
        resp = client.post(f"{base}/module-folders", json={"name": "Specs"})
        assert resp.status_code == 200 and resp.json() == {"id": "specs", "name": "Specs", "parent_id": None}
        assert json.loads(ws.receive_text()) == changed
        resp = client.post(f"{base}/module-folders", json={"name": "Inner", "parent_id": "specs"})
        assert resp.json() == {"id": "inner", "name": "Inner", "parent_id": "specs"}
        assert json.loads(ws.receive_text()) == changed
        resp = client.post(f"{base}/modules/b/move", json={"folder_id": "specs", "index": 0})
        assert resp.status_code == 200 and resp.json()["id"] == "b"
        assert json.loads(ws.receive_text()) == changed
        resp = client.post(f"{base}/modules", json={"name": "C", "prefix": "C", "folder_id": "inner"})
        assert resp.status_code == 200
        assert json.loads(ws.receive_text()) == changed
        resp = client.put(f"{base}/module-folders/inner/name", json={"name": "Nested"})
        assert resp.json() == {"id": "inner", "name": "Nested", "parent_id": "specs"}
        assert json.loads(ws.receive_text()) == changed
        resp = client.post(f"{base}/module-folders/inner/move", json={"folder_id": None, "index": 0})
        assert resp.json() == {"id": "inner", "name": "Nested", "parent_id": None}
        assert json.loads(ws.receive_text()) == changed
        resp = client.post(f"{base}/module-folders/specs/reorder", json={"direction": "up"})
        assert resp.json() == {"id": "specs", "name": "Specs", "parent_id": None}
        assert json.loads(ws.receive_text()) == changed
        resp = client.post(f"{base}/modules/a/reorder", json={"direction": "down"})
        assert resp.status_code == 200
        assert json.loads(ws.receive_text()) == changed
        _assert_no_broadcast(ws)

    expected = [F("inner", "Nested", M("c")), F("specs", "Specs", M("b")), M("a")]
    assert client.get(f"{base}/module-tree").json() == expected
    assert [m["id"] for m in client.get(f"{base}/modules").json()] == ["c", "b", "a"]

    with client.websocket_connect("/ws") as ws:
        client.post(f"{base}/modules/b/move", json={})
        assert json.loads(ws.receive_text()) == changed
        resp = client.delete(f"{base}/module-folders/specs")
        assert resp.status_code == 200 and resp.json() == {"id": "specs", "deleted": True}
        assert json.loads(ws.receive_text()) == changed
    fresh = ProjectService(ProjectStore(tmp_path))
    assert fresh.get_module_tree(project_id) == [F("inner", "Nested", M("c")), M("a"), M("b")]


def test_ws_folder_operations_match_rest_and_skip_the_sender(client, tmp_path):
    project_id = _rest_project(client, "A", "B")
    changed = {"event": "changed", "project_id": project_id}
    calls = [
        ("module_folder.create", {"project_id": project_id, "name": "Specs"}),
        ("module_folder.create", {"project_id": project_id, "name": "Inner", "parent_id": "specs"}),
        ("module.move", {"project_id": project_id, "id": "b", "folder_id": "specs", "index": 0}),
        ("module.create", {"project_id": project_id, "name": "C", "prefix": "C", "folder_id": "inner"}),
        ("module_folder.rename", {"project_id": project_id, "id": "inner", "name": "Nested"}),
        ("module_folder.move", {"project_id": project_id, "id": "inner", "folder_id": None, "index": 0}),
        ("module_folder.reorder", {"project_id": project_id, "id": "specs", "direction": "up"}),
        ("module.reorder", {"project_id": project_id, "id": "a", "direction": "down"}),
    ]
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        results = []
        for index, (method, params) in enumerate(calls, start=1):
            response = _rpc(ws, method, params, index)
            assert "result" in response, (method, response)
            results.append(response["result"])
            assert json.loads(observer.receive_text()) == changed, method
        _assert_no_broadcast(ws)
        assert results[0] == {"id": "specs", "name": "Specs", "parent_id": None}
        assert results[5] == {"id": "inner", "name": "Nested", "parent_id": None}
        tree = _rpc(ws, "module_tree.get", {"project_id": project_id}, 50)["result"]
        deleted = _rpc(ws, "module_folder.create", {"project_id": project_id, "name": "Gone"}, 51)
        assert json.loads(observer.receive_text()) == changed
        assert _rpc(ws, "module_folder.delete", {"project_id": project_id, "id": deleted["result"]["id"]}, 52)[
            "result"
        ] == {"id": "gone", "deleted": True}
        assert json.loads(observer.receive_text()) == changed
    assert tree == [F("inner", "Nested", M("c")), F("specs", "Specs", M("b")), M("a")]
    assert client.get(f"/projects/{project_id}/module-tree").json() == tree


@pytest.mark.parametrize(
    ("rest", "ws", "status", "code", "message"),
    [
        (("post", "module-folders", {"name": " "}), ("module_folder.create", {"name": " "}), 400, -32602,
         "Folder name must not be empty"),
        (("post", "module-folders", {"name": "X", "parent_id": "ghost"}),
         ("module_folder.create", {"name": "X", "parent_id": "ghost"}), 404, -32001,
         "Module folder 'ghost' not found in project 'p'"),
        (("post", "module-folders", {"name": "Specs"}), ("module_folder.create", {"name": "specs"}), 400, -32602,
         "A folder named 'Specs' already exists here"),
        (("put", "module-folders/specs/name", {"name": ""}), ("module_folder.rename", {"id": "specs", "name": ""}),
         400, -32602, "Folder name must not be empty"),
        (("put", "module-folders/ghost/name", {"name": "Y"}), ("module_folder.rename", {"id": "ghost", "name": "Y"}),
         404, -32001, "Module folder 'ghost' not found in project 'p'"),
        (("delete", "module-folders/specs", None), ("module_folder.delete", {"id": "specs"}), 400, -32602,
         "Module folder 'Specs' is not empty: move its modules and folders out first"),
        (("post", "module-folders/specs/move", {"folder_id": "specs"}),
         ("module_folder.move", {"id": "specs", "folder_id": "specs"}), 400, -32602,
         "A folder cannot be moved into itself"),
        (("post", "modules/a/move", {"folder_id": "specs", "index": -2}),
         ("module.move", {"id": "a", "folder_id": "specs", "index": -2}), 400, -32602,
         "Invalid index -2: must be a non-negative integer"),
        (("post", "modules/a/move", {"folder_id": "ghost"}), ("module.move", {"id": "a", "folder_id": "ghost"}),
         404, -32001, "Module folder 'ghost' not found in project 'p'"),
        (("post", "modules", {"name": "N", "prefix": "N", "folder_id": "ghost"}),
         ("module.create", {"name": "N", "prefix": "N", "folder_id": "ghost"}), 404, -32001,
         "Module folder 'ghost' not found in project 'p'"),
    ],
    ids=["blank-name", "unknown-parent", "duplicate-name", "rename-blank", "rename-unknown", "delete-non-empty",
         "move-into-itself", "bad-index", "move-unknown-folder", "create-module-unknown-folder"],
)
def test_folder_errors_have_rest_ws_parity_and_never_broadcast(client, tmp_path, rest, ws, status, code, message):
    project_id = _rest_project(client, "A", "B")
    client.post(f"/projects/{project_id}/module-folders", json={"name": "Specs"})
    client.post(f"/projects/{project_id}/modules/b/move", json={"folder_id": "specs"})
    before = _snapshot(tmp_path / project_id)
    verb, path, body = rest
    method, params = ws
    with client.websocket_connect("/ws") as sender, client.websocket_connect("/ws") as observer:
        kwargs = {} if body is None else {"json": body}
        resp = client.request(verb.upper(), f"/projects/{project_id}/{path}", **kwargs)
        assert resp.status_code == status, resp.text
        assert resp.json()["detail"] == message
        response = _rpc(sender, method, {"project_id": project_id, **params})
        assert response["error"] == {"code": code, "message": message}
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path / project_id) == before


def test_rest_folder_operations_on_unknown_project_are_404(client):
    assert client.get("/projects/ghost/module-tree").status_code == 404
    assert client.post("/projects/ghost/module-folders", json={"name": "X"}).status_code == 404
    assert client.post("/projects/ghost/modules/a/move", json={}).status_code == 404
    with client.websocket_connect("/ws") as ws:
        assert _rpc(ws, "module_tree.get", {"project_id": "ghost"})["error"]["code"] == -32001


def test_rest_folder_reorder_rejects_invalid_direction_with_422(client):
    project_id = _rest_project(client)
    client.post(f"/projects/{project_id}/module-folders", json={"name": "X"})
    resp = client.post(f"/projects/{project_id}/module-folders/x/reorder", json={"direction": "sideways"})
    assert resp.status_code == 422
    with client.websocket_connect("/ws") as ws:
        response = _rpc(ws, "module_folder.reorder", {"project_id": project_id, "id": "x", "direction": "sideways"})
        assert response["error"] == {"code": -32602, "message": "Invalid direction 'sideways': must be 'up' or 'down'"}


# --- templates: module_tree --------------------------------------------------------------


def _write_template(directory: Path, stem: str, data) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
    (directory / f"{stem}.json").write_text(text)


def _tree_template(module_tree, **overrides) -> dict:
    data = {
        "format_version": 2,
        "id": "tree",
        "name": "Tree",
        "attributes": [],
        "modules": [
            {"key": "a", "name": "Alpha", "prefix": "A"},
            {"key": "b", "name": "Beta", "prefix": "B"},
            {"key": "c", "name": "Gamma", "prefix": "C"},
        ],
        "module_tree": module_tree,
        "link_configurations": [],
        "views": [],
    }
    data.update(overrides)
    return data


def test_template_module_tree_builds_folders_with_server_ids(service):
    tree = [
        {"folder": "k1", "name": "Specs", "children": [{"module": "b"}, {"folder": "k2", "name": "Specs", "children": []}]},
        {"folder": "k3", "name": "specs!", "children": [{"module": "a"}]},
    ]
    _write_template(service.store.root / "_templates", "tree", _tree_template(tree))
    project = service.create_project("P", template_id="user:tree")
    # Folder ids are slugged from the names (unique in the project); "c" is not placed and follows.
    assert service.get_module_tree(project.id) == [
        F("specs", "Specs", M("beta"), F("specs-2", "Specs")),
        F("specs-3", "specs!", M("alpha")),
        M("gamma"),
    ]
    assert [m.id for m in service.list_modules(project.id)] == ["beta", "alpha", "gamma"]


@pytest.mark.parametrize(
    ("tree", "message"),
    [
        ([{"module": "zzz"}], "module_tree[0]: 'module' 'zzz' is not a module key of this template"),
        ([{"module": "a"}, {"folder": "f", "name": "F", "children": [{"module": "a"}]}],
         "module_tree[1].children[0]: module key 'a' is placed more than once"),
        ([{"folder": "f", "name": "F"}, {"folder": "f", "name": "G"}], "module_tree[1]: duplicate folder key 'f'"),
        ([{"folder": "f", "name": "  "}], "module_tree[0]: 'name' must be a non-empty string"),
        ([{"folder": "", "name": "F"}], "module_tree[0]: 'folder' must be a non-empty string"),
        ([{"name": "F"}], "module_tree[0] must have a 'module' or a 'folder' key"),
        ([{"folder": "f", "name": "F", "children": {}}], "'module_tree[0].children' must be a list"),
        ({"module": "a"}, "'module_tree' must be a list"),
        (["a"], "module_tree[0] must be an object"),
        ([{"folder": "f", "name": "Same"}, {"folder": "g", "name": "SAME"}],
         "module_tree[1] ('SAME'): A folder named 'Same' already exists here"),
        ([{"folder": "f", "name": "x" * 201}], "at most 200 characters"),
    ],
)
def test_invalid_template_module_tree_is_listed_with_error_and_refused(service, tree, message):
    _write_template(service.store.root / "_templates", "tree", _tree_template(tree))
    entry = next(t for t in service.list_templates() if t["id"] == "user:tree")
    assert entry["error"] is not None and message in entry["error"]
    with pytest.raises(ValueError, match="is invalid"):
        service.create_project("P", template_id="user:tree")
    assert service.list_projects() == []


def test_template_module_tree_depth_is_limited(service):
    def chain(levels):
        node = {"folder": f"k{levels}", "name": f"L{levels}", "children": [{"module": "a"}]}
        for level in range(levels - 1, 0, -1):
            node = {"folder": f"k{level}", "name": f"L{level}", "children": [node]}
        return [node]

    _write_template(service.store.root / "_templates", "ok", _tree_template(chain(16), name="Ok"))
    _write_template(service.store.root / "_templates", "deep", _tree_template(chain(17), name="Deep"))
    by_id = {t["id"]: t for t in service.list_templates()}
    assert by_id["user:ok"]["error"] is None
    assert "nested at most 16 levels deep" in by_id["user:deep"]["error"]


def test_save_as_template_writes_folders_and_round_trips(service):
    project = _project(service, "A", "B", "C")
    service.create_module_folder(project.id, "Specs")
    service.create_module_folder(project.id, "Inner", "specs")
    service.create_module_folder(project.id, "Empty")
    service.move_module(project.id, "b", "inner")
    service.move_module(project.id, "a", "specs")
    summary = service.save_project_as_template(project.id, "Foldered")
    # ``modules`` follow the tree depth-first.
    assert [m["key"] for m in summary["modules"]] == ["c", "b", "a"]
    data = json.loads((service.store.root / "_templates" / "foldered.json").read_text())
    assert list(data).index("module_tree") == list(data).index("modules") + 1
    assert data["module_tree"] == [
        M("c"),
        F("specs", "Specs", F("inner", "Inner", M("b")), M("a")),
        F("empty", "Empty"),
    ]
    copy = service.create_project("Copy", template_id=summary["id"])
    assert service.get_module_tree(copy.id) == service.get_module_tree(project.id)


def test_save_as_template_without_folders_writes_no_module_tree(service):
    project = _project(service, "A", "B")
    service.reorder_module(project.id, "b", "up")
    service.save_project_as_template(project.id, "Flat")
    data = json.loads((service.store.root / "_templates" / "flat.json").read_text())
    assert "module_tree" not in data
    assert [m["key"] for m in data["modules"]] == ["b", "a"]


def test_rbc_template_creates_its_folders(service):
    project = service.create_project("RBC", template_id=RBC)
    assert service.get_module_tree(project.id) == [
        F("requirements", "Requirements", M("strs"), M("syrs"), M("swrs")),
        F("tests", "Tests", M("systest"), M("swtest")),
        F("test-execution", "Test Execution", M("systestexec"), M("swtestexec")),
    ]
