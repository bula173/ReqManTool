"""Module templates (``kind: module``), heading skeletons and project template format 2.

Parsing and ``migrate_v1``, the attribute merge rules, skeleton
requirements, "Add module from template", "Save module as template",
project templates referencing module templates, the RBC skeletons and the
skeletons written by "Save as template". REST/WebSocket parity and
broadcasts are checked for both module template operations.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from reqman import richtext
from reqman.formats import PROJECT_FORMAT
from reqman.git_repo import GitHistory, GitSettings
from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore
from reqman.templates import (
    FORMAT_VERSION,
    MAX_SKELETON_NODES,
    MAX_SKELETON_TEXT_LENGTH,
    migrate_v1,
    parse_module_template,
    parse_module_template_text,
    parse_skeleton,
    parse_template,
    skeleton_size,
)

RBC = "builtin:rbc_template"
CHECKOUT = Path(__file__).resolve().parents[2]
OBJECT_TYPE = {
    "id": "object_type",
    "name": "Object Type",
    "type": "enum",
    "choices": ["Heading", "Information", "Requirement"],
    "default": "Requirement",
}


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
    if not directory.exists():
        return {}
    return {str(p.relative_to(directory)): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


def _module_template(**overrides) -> dict:
    data = {
        "format_version": 1,
        "kind": "module",
        "id": "spec",
        "name": "Spec",
        "description": "A spec module",
        "module": {"name": "Spec", "prefix": "SP", "description": "Module description"},
        "attributes": [
            copy.deepcopy(OBJECT_TYPE),
            {"id": "priority", "name": "Priority", "type": "enum", "choices": ["High", "Low"], "default": "Low"},
        ],
        "view": {"name": "Spec view", "attribute_ids": ["object_text", "object_type", "priority"]},
        "skeleton": [
            {"heading": "Intro", "text": "Purpose of the document", "children": [{"heading": "Scope"}]},
            {"heading": "Body"},
        ],
    }
    data.update(overrides)
    return data


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=2) + "\n")


def _write_module_template(service, stem: str, data) -> str:
    _write(service.store.root / "_templates" / "modules" / f"{stem}.json", data)
    return f"user:{stem}"


def _write_project_template(service, stem: str, data) -> str:
    _write(service.store.root / "_templates" / f"{stem}.json", data)
    return f"user:{stem}"


def _objects(service, project_id, module_id) -> list[tuple]:
    """(id, plain text, object type, parent, order) of a module's requirements, in id order."""
    return [
        (
            r.id,
            richtext.to_plain(r.attributes.get("object_text") or ""),
            next((v for k, v in r.attributes.items() if k in ("object_type", "otype")), None),
            r.parent_id,
            r.order,
        )
        for r in sorted(service.list_requirements(project_id, module_id), key=lambda r: r.id)
    ]


def _attr(service, project_id, attr_id):
    return next(a for a in service.get_project(project_id).attributes if a.id == attr_id)


# --- parsing -------------------------------------------------------------------------


def test_parse_module_template_and_unknown_keys():
    data = _module_template(future={"x": 1})
    data["module"]["type"] = "test_cases"
    data["attributes"][1]["ui"] = "ignored"
    template = parse_module_template(data, "user:spec")
    assert (template.id, template.source, template.name, template.format_version) == ("user:spec", "user", "Spec", 1)
    assert (template.module.name, template.module.prefix, template.module.type) == ("Spec", "SP", "test_cases")
    assert [a.id for a in template.attributes] == ["object_type", "priority"]
    assert template.view.attribute_ids == ["object_text", "object_type", "priority"]
    assert skeleton_size(template.skeleton) == 3
    assert template.skeleton[0].text == "Purpose of the document"
    assert template.skeleton[0].children[0].heading == "Scope"
    # Written back in the canonical shape (unknown keys dropped).
    assert list(template.to_dict()) == [
        "format_version", "kind", "id", "name", "description", "module", "attributes", "view", "skeleton",
    ]
    assert "future" not in template.to_dict()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": None}, "'kind' must be 'module' (this is not a module template)"),
        ({"kind": "project"}, "'kind' must be 'module'"),
        ({"format_version": 2}, "format_version 2 is newer than this ReqManTool supports (1)"),
        ({"format_version": 0}, "Unsupported format_version 0"),
        ({"format_version": "1"}, "'format_version' must be the integer 1, got '1'"),
        ({"format_version": None}, "'format_version' must be the integer 1, got None"),
        ({"name": " "}, "'name' must be a non-empty string"),
        ({"module": None}, "'module' must be an object"),
        ({"module": {"name": "", "prefix": "P"}}, "module: 'name' must be a non-empty string"),
        ({"module": {"name": "M", "prefix": None}}, "module: 'prefix' must be a string"),
        ({"module": {"name": "M", "prefix": "P", "type": 3}}, "module: 'type' must be a module type string"),
        ({"attributes": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]}, "attributes[1]: duplicate attribute id 'a'"),
        ({"attributes": [{"id": "a", "name": "Same"}, {"id": "b", "name": " same "}]},
         "attributes[1]: duplicate attribute name 'same'"),
        ({"attributes": [{"id": "a", "name": "A", "system": True}]}, "cannot be system attributes"),
        ({"attributes": [{"id": "a"}]}, "attributes[0]: 'name' must be a non-empty string"),
        ({"view": {"name": "V", "attribute_ids": ["missing"]}},
         "view: unknown attribute 'missing' (expected 'object_text' or a template attribute id)"),
        ({"view": {"name": "", "attribute_ids": []}}, "view: 'name' must be a non-empty string"),
        ({"view": "V"}, "'view' must be an object or null"),
        ({"view": {"name": "V", "attribute_ids": ["object_text"], "column_widths": {"object_text": "wide"}}},
         "'column_widths' must be an object mapping column keys to integer widths"),
        ({"skeleton": {"heading": "x"}}, "'skeleton' must be a list"),
        ({"skeleton": [{"heading": " "}]}, "skeleton[0]: 'heading' must be a non-empty string"),
        ({"skeleton": [{"heading": "H", "text": 5}]}, "skeleton[0]: 'text' must be a string"),
        ({"skeleton": [{"heading": "H", "children": [{"text": "t"}]}]},
         "skeleton[0].children[0]: 'heading' must be a non-empty string"),
        ({"skeleton": ["H"]}, "skeleton[0] must be an object"),
    ],
)
def test_module_template_shape_errors(overrides, message):
    with pytest.raises(ValueError) as info:
        parse_module_template(_module_template(**overrides), "user:x")
    assert message in str(info.value)


def test_project_and_module_templates_reject_each_other():
    with pytest.raises(ValueError, match="this is a module template, not a project template"):
        parse_template(_module_template(), "user:x")
    with pytest.raises(ValueError, match="'kind' must be 'module'"):
        parse_module_template({"format_version": 2, "name": "P", "modules": []}, "user:x")


def test_skeleton_limits():
    flat = [{"heading": f"H{i}"} for i in range(MAX_SKELETON_NODES)]
    assert skeleton_size(parse_skeleton(flat)) == MAX_SKELETON_NODES
    # Nested nodes count too.
    nested = [{"heading": "Root", "children": flat[1:]}]
    assert skeleton_size(parse_skeleton(nested)) == MAX_SKELETON_NODES
    with pytest.raises(ValueError, match="'skeleton' has more than 2000 headings"):
        parse_skeleton(flat + [{"heading": "One more"}])
    with pytest.raises(ValueError, match="'skeleton' has more than 2000 headings"):
        parse_skeleton([{"heading": "Root", "children": flat}])
    long = "x" * MAX_SKELETON_TEXT_LENGTH
    assert parse_skeleton([{"heading": long, "text": long}])[0].heading == long
    with pytest.raises(ValueError, match="'heading' is longer than 10000 characters"):
        parse_skeleton([{"heading": long + "x"}])
    with pytest.raises(ValueError, match="'text' is longer than 10000 characters"):
        parse_skeleton([{"heading": "H", "text": long + "x"}])
    # Blank text means none; headings are stripped; null children mean none.
    node = parse_skeleton([{"heading": "  H  ", "text": "   ", "children": None}])[0]
    assert (node.heading, node.text, node.children) == ("H", None, [])
    assert parse_skeleton(None) == []


def test_migrate_v1_drops_v2_only_module_keys_without_mutating_input():
    v1 = {
        "format_version": 1,
        "name": "Old",
        "modules": [
            {"key": "a", "name": "A", "prefix": "A", "module_template": "builtin:requirements", "skeleton": [{"heading": "H"}]},
            {"key": "b", "name": "B", "prefix": "B", "extra": 1},
        ],
    }
    original = copy.deepcopy(v1)
    migrated = migrate_v1(v1)
    assert v1 == original
    assert migrated["format_version"] == FORMAT_VERSION == 2
    assert migrated["modules"] == [{"key": "a", "name": "A", "prefix": "A"}, {"key": "b", "name": "B", "prefix": "B", "extra": 1}]
    template = parse_template(v1, "user:old")
    assert template.format_version == 2
    assert template.modules[0].module_template is None and template.modules[0].skeleton is None


def test_v1_template_may_not_omit_name_even_with_a_module_template():
    v1 = {"format_version": 1, "name": "Old", "modules": [{"key": "a", "module_template": "builtin:requirements"}]}
    with pytest.raises(ValueError, match="modules\\[0\\]: 'name' must be a non-empty string"):
        parse_template(v1, "user:old")
    v2 = dict(v1, format_version=2)
    module = parse_template(v2, "user:new").modules[0]
    assert (module.name, module.prefix, module.module_template) == (None, None, "builtin:requirements")


@pytest.mark.parametrize("reference", ["requirements", "other:x", "user:", 5])
def test_project_template_module_template_reference_must_be_namespaced(reference):
    data = {"format_version": 2, "name": "T", "modules": [{"key": "a", "name": "A", "prefix": "A", "module_template": reference}]}
    with pytest.raises(ValueError, match="modules\\[0\\]: 'module_template': Invalid template id"):
        parse_template(data, "user:t")


def test_project_template_module_skeleton_limit_names_the_module():
    data = {
        "format_version": 2,
        "name": "T",
        "modules": [{"key": "a", "name": "A", "prefix": "A", "skeleton": [{"heading": "h"}] * (MAX_SKELETON_NODES + 1)}],
    }
    with pytest.raises(ValueError, match="'modules\\[0\\].skeleton' has more than 2000 headings"):
        parse_template(data, "user:t")


# --- listing -------------------------------------------------------------------------


def test_builtin_module_templates_are_listed_and_valid(service):
    listed = service.list_module_templates()
    assert [(t["id"], t["error"]) for t in listed] == [
        ("builtin:operational_scenarios", None),
        ("builtin:requirements", None),
        ("builtin:test_execution", None),
        ("builtin:test_cases", None),
    ]
    by_id = {t["id"]: t for t in listed}
    assert by_id["builtin:requirements"]["headings"] == 16
    assert by_id["builtin:test_cases"]["headings"] == 8
    assert by_id["builtin:test_execution"]["headings"] == 6
    assert by_id["builtin:test_cases"]["module"]["type"] == "test_cases"
    assert by_id["builtin:requirements"]["module"] == {
        "name": "Requirements", "prefix": "REQ", "description": "Requirements specification.", "type": "requirements",
    }


def test_invalid_user_module_templates_are_listed_with_error_and_never_project_templates(service):
    _write_module_template(service, "good", _module_template(name="Good"))
    _write_module_template(service, "junk", "{not json")
    _write_module_template(service, "noobj", _module_template(name="No object type", attributes=[], view=None))
    _write_module_template(service, "wrongkind", {"format_version": 2, "name": "Project", "modules": []})
    before = _snapshot(service.store.root)
    by_id = {t["id"]: t for t in service.list_module_templates()}
    assert _snapshot(service.store.root) == before  # listing writes nothing
    assert by_id["user:good"]["error"] is None and by_id["user:good"]["headings"] == 3
    assert by_id["user:good"]["attributes"] == ["Object Type", "Priority"]
    assert by_id["user:good"]["view"] == "Spec view"
    assert "JSON" in by_id["user:junk"]["error"] and by_id["user:junk"]["name"] == "junk"
    assert "needs an 'Object Type' attribute" in by_id["user:noobj"]["error"]
    assert "'kind' must be 'module'" in by_id["user:wrongkind"]["error"]
    assert by_id["user:junk"]["module"] is None and by_id["user:junk"]["headings"] == 0
    # A module template is never listed as a project template, and vice versa.
    assert not any(t["id"].startswith("user:") for t in service.list_templates())


# --- create module from template: service ---------------------------------------------


def test_create_module_from_template_builds_module_attributes_view_and_skeleton(service):
    project = service.create_project("P")
    template_id = _write_module_template(service, "spec", _module_template())
    result = service.create_module_from_template(project.id, template_id)
    module = result["module"]
    assert (module.id, module.name, module.prefix, module.description, module.type) == (
        "spec", "Spec", "SP", "Module description", "requirements",
    )
    assert result["added_attributes"] == ["object_type", "priority"]
    assert result["extended_attributes"] == []
    assert result["view_id"] is not None
    assert result["headings"] == 4  # three headings and one Information text

    assert _objects(service, project.id, "spec") == [
        ("SP-0001", "Intro", "Heading", None, 0),
        ("SP-0002", "Purpose of the document", "Information", "SP-0001", 0),
        ("SP-0003", "Scope", "Heading", "SP-0001", 1),  # child headings follow the text
        ("SP-0004", "Body", "Heading", None, 1),
    ]
    # Defaults apply to the other attributes.
    assert all(r.attributes["priority"] == "Low" for r in service.list_requirements(project.id, "spec"))
    # Added attributes are optional and scoped to the new module only.
    for attr_id in ("object_type", "priority"):
        attr = _attr(service, project.id, attr_id)
        assert attr.required is False and attr.modules == ["spec"]
    view = next(v for v in service.get_project(project.id).views if v.id == result["view_id"])
    assert (view.name, view.attribute_ids) == ("Spec view", ["object_text", "object_type", "priority"])
    # The next id continues after the skeleton.
    assert service.add_requirement(project.id, "spec", {"object_text": "new"}).id == "SP-0005"

    fresh = ProjectService(ProjectStore(service.store.root))
    assert [m.id for m in fresh.list_modules(project.id)] == ["spec"]
    text = (service.store.root / project.id / "project.json").read_text()
    data = json.loads(text)
    assert text == json.dumps(data, indent=2) + "\n" and data["format_version"] == PROJECT_FORMAT.current
    assert data["id_counters"] == {"SP": 5}


def test_create_module_from_template_overrides_and_folder(service):
    project = service.create_project("P")
    service.create_module(project.id, "Existing", "EX")
    folder = service.create_module_folder(project.id, "Specs")["id"]
    result = service.create_module_from_template(
        project.id, "builtin:test_cases", name="  System tests ", prefix="ST", description="Mine", folder_id=folder
    )
    module = result["module"]
    assert (module.id, module.name, module.prefix, module.description, module.type) == (
        "system-tests", "System tests", "ST", "Mine", "test_cases",
    )
    assert service.get_module_tree(project.id) == [
        {"module": "existing"}, {"folder": "specs", "name": "Specs", "children": [{"module": "system-tests"}]},
    ]
    assert result["headings"] == 9
    assert {r.id for r in service.list_requirements(project.id, "system-tests")} == {f"ST-{i:04d}" for i in range(1, 10)}


@pytest.mark.parametrize(
    ("kwargs", "error", "message"),
    [
        ({"name": " "}, ValueError, "Module name must not be empty"),
        ({"prefix": "1bad"}, ValueError, "Invalid prefix"),
        ({"prefix": 5}, ValueError, "Module prefix must be a string"),
        ({"description": 5}, ValueError, "Module description must be a string"),
        ({"folder_id": "ghost"}, FileNotFoundError, "Module folder 'ghost' not found"),
        ({"template_id": "user:nope"}, FileNotFoundError, "Module template 'user:nope' not found"),
        ({"template_id": "builtin:rbc_template"}, FileNotFoundError, "Module template 'builtin:rbc_template' not found"),
        ({"template_id": "bogus"}, ValueError, "Invalid template id"),
    ],
)
def test_create_module_from_template_errors_write_nothing(service, kwargs, error, message):
    project = service.create_project("P")
    kwargs = {"template_id": "builtin:requirements", **kwargs}
    before = _snapshot(service.store.root)
    with pytest.raises(error, match=message):
        service.create_module_from_template(project.id, **kwargs)
    assert _snapshot(service.store.root) == before


def test_create_module_from_template_in_unknown_project_is_not_found(service):
    with pytest.raises(FileNotFoundError):
        service.create_module_from_template("ghost", "builtin:requirements")


def test_same_template_twice_gets_suffixed_module_id_and_shares_attributes(service):
    project = service.create_project("P")
    first = service.create_module_from_template(project.id, "builtin:requirements")
    second = service.create_module_from_template(project.id, "builtin:requirements", prefix="REQB")
    assert (first["module"].id, second["module"].id) == ("requirements", "requirements-2")
    assert second["added_attributes"] == []
    assert second["extended_attributes"] == ["object_type", "status"]
    assert second["view_id"] is None  # the view name is taken
    assert _attr(service, project.id, "object_type").modules == ["requirements", "requirements-2"]
    assert [v.name for v in service.get_project(project.id).views] == ["Requirements"]


# --- attribute merge rules ------------------------------------------------------------


def test_existing_attribute_is_matched_by_name_and_only_its_scope_is_extended(service):
    project = service.create_project("P")
    service.create_module(project.id, "Other", "OT")
    service.add_attribute_definition(
        project.id, "otype", "object TYPE", "enum", default="Heading",
        choices=["Heading", "Information", "Note"], modules=["other"],
    )
    service.add_attribute_definition(project.id, "priority", "Priority", "enum", default="High", choices=["High", "Low", "Mid"])
    req = service.add_requirement(project.id, "other", {"object_text": "kept", "otype": "Note"})
    other_dir = service.store.root / project.id / "modules" / "other"
    other_before = _snapshot(other_dir)
    template_id = _write_module_template(service, "spec", _module_template())

    result = service.create_module_from_template(project.id, template_id)
    assert result["added_attributes"] == []
    assert result["extended_attributes"] == ["otype"]  # "priority" applies to every module already
    otype = _attr(service, project.id, "otype")
    assert (otype.name, otype.default, otype.choices, otype.modules) == (
        "object TYPE", "Heading", ["Heading", "Information", "Note"], ["other", "spec"],
    )
    priority = _attr(service, project.id, "priority")
    assert (priority.default, priority.choices, priority.modules) == ("High", ["High", "Low", "Mid"], [])
    # The view maps the template ids to the project's attributes.
    view = next(v for v in service.get_project(project.id).views if v.id == result["view_id"])
    assert view.attribute_ids == ["object_text", "otype", "priority"]
    # The project's defaults apply, not the template's.
    assert {r.attributes["priority"] for r in service.list_requirements(project.id, "spec")} == {"High"}
    assert [o[2] for o in _objects(service, project.id, "spec")] == ["Heading", "Information", "Heading", "Heading"]
    # Other modules and their requirements are untouched.
    assert _snapshot(other_dir) == other_before
    assert service.get_requirement(project.id, "other", req.id).attributes["otype"] == "Note"


def test_attribute_scoped_by_module_type_is_not_extended(service):
    project = service.create_project("P")
    service.add_attribute_definition(
        project.id, "object_type", "Object Type", "enum", choices=["Heading", "Information"], module_types=["test_cases"]
    )
    result = service.create_module_from_template(project.id, "builtin:test_cases")
    assert "object_type" not in result["extended_attributes"]
    assert _attr(service, project.id, "object_type").modules == []


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda s, p: s.add_attribute_definition(p, "prio", "PRIORITY", "string"),
         "attributes[1] ('Priority'): the project's attribute 'PRIORITY' has type 'string', the template's has type 'enum'"),
        (lambda s, p: (s.create_module(p, "A", "A"), s.create_module(p, "B", "B"),
                       s.create_link_configuration(p, "Priority", "a", "b")),
         "attributes[1] ('Priority'): 'Priority' is a link attribute of the project"),
    ],
    ids=["type-mismatch", "link-mirror"],
)
def test_merge_refusals_write_nothing(service, setup, message):
    project = service.create_project("P")
    setup(service, project.id)
    template_id = _write_module_template(service, "spec", _module_template())
    before = _snapshot(service.store.root)
    with pytest.raises(ValueError) as info:
        service.create_module_from_template(project.id, template_id)
    assert str(info.value) == f"Module template '{template_id}' cannot be added: {message}"
    assert _snapshot(service.store.root) == before


def test_template_attribute_id_taken_by_another_name_gets_a_free_id(service):
    project = service.create_project("P")
    service.create_module(project.id, "Other", "OT")
    service.add_attribute_definition(project.id, "priority", "Urgency", "string", modules=["other"])
    template_id = _write_module_template(service, "spec", _module_template())
    result = service.create_module_from_template(project.id, template_id)
    assert result["added_attributes"] == ["object_type", "priority-2"]
    added = _attr(service, project.id, "priority-2")
    assert (added.name, added.type, added.modules) == ("Priority", "enum", ["spec"])
    assert _attr(service, project.id, "priority").modules == ["other"]  # unchanged
    view = next(v for v in service.get_project(project.id).views if v.id == result["view_id"])
    assert view.attribute_ids == ["object_text", "object_type", "priority-2"]


def test_existing_view_name_is_kept(service):
    project = service.create_project("P")
    service.create_view(project.id, "SPEC VIEW", [])
    template_id = _write_module_template(service, "spec", _module_template())
    result = service.create_module_from_template(project.id, template_id)
    assert result["view_id"] is None
    assert [(v.name, v.attribute_ids) for v in service.get_project(project.id).views] == [("SPEC VIEW", [])]


# --- skeleton object type rules --------------------------------------------------------


@pytest.mark.parametrize(
    ("choices", "skeleton", "message"),
    [
        (["Information", "Requirement"], [{"heading": "H"}],
         "attribute 'Object Type' has no 'Heading' choice, needed by the heading skeleton"),
        (["Heading", "Requirement"], [{"heading": "H", "text": "T"}],
         "skeleton heading 'H': attribute 'Object Type' has no 'Information' choice, needed by the heading skeleton"),
    ],
)
def test_object_type_choices_are_never_edited_and_missing_ones_refuse(service, choices, skeleton, message):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "object_type", "Object Type", "enum", choices=choices)
    template_id = _write_module_template(service, "spec", _module_template(skeleton=skeleton))
    before = _snapshot(service.store.root)
    with pytest.raises(ValueError, match=message):
        service.create_module_from_template(project.id, template_id)
    assert _snapshot(service.store.root) == before
    assert _attr(service, project.id, "object_type").choices == choices


def test_skeleton_without_texts_needs_no_information_choice(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "object_type", "Object Type", "enum", choices=["heading"])
    template_id = _write_module_template(service, "spec", _module_template(skeleton=[{"heading": "H"}]))
    service.create_module_from_template(project.id, template_id)
    # The choice is matched loosely and stored as the project spells it.
    assert [o[2] for o in _objects(service, project.id, "spec")] == ["heading"]


def test_string_object_type_and_missing_object_type(service):
    project = service.create_project("P")
    attrs = [{"id": "priority", "name": "Priority", "type": "string", "default": None, "choices": None}]
    no_type = _write_module_template(service, "plain", _module_template(attributes=attrs, view=None))
    before = _snapshot(service.store.root)
    with pytest.raises(ValueError, match="a heading skeleton needs an 'Object Type' attribute"):
        service.create_module_from_template(project.id, no_type)
    assert _snapshot(service.store.root) == before
    # Without a skeleton no Object Type is needed.
    empty = _write_module_template(service, "empty", _module_template(name="Empty", attributes=attrs, view=None, skeleton=[]))
    assert service.create_module_from_template(project.id, empty)["headings"] == 0
    # A string attribute with id "object-type" serves as Object Type.
    service.add_attribute_definition(project.id, "object-type", "Kind", "string")
    service.create_module_from_template(project.id, no_type, name="Typed", prefix="TY")
    assert [r.attributes["object-type"] for r in service.list_requirements(project.id, "typed")] == [
        "Heading", "Information", "Heading", "Heading",
    ]


def test_required_attribute_without_default_makes_the_skeleton_fail(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "owner", "Owner", "string", required=True)
    template_id = _write_module_template(service, "spec", _module_template())
    before = _snapshot(service.store.root)
    with pytest.raises(ValueError, match="skeleton heading 'Intro'"):
        service.create_module_from_template(project.id, template_id)
    assert _snapshot(service.store.root) == before


def test_required_attribute_with_default_is_applied_to_skeleton_objects(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "owner", "Owner", "string", required=True, default="QA")
    template_id = _write_module_template(service, "spec", _module_template())
    service.create_module_from_template(project.id, template_id)
    assert {r.attributes["owner"] for r in service.list_requirements(project.id, "spec")} == {"QA"}


def test_maximum_skeleton_is_created(service):
    project = service.create_project("P")
    skeleton = [{"heading": f"H{i}"} for i in range(MAX_SKELETON_NODES)]
    template_id = _write_module_template(service, "big", _module_template(skeleton=skeleton, view=None))
    assert service.create_module_from_template(project.id, template_id)["headings"] == MAX_SKELETON_NODES
    assert len(service.list_requirements(project.id, "spec")) == MAX_SKELETON_NODES


# --- git history ----------------------------------------------------------------------


def _git(path: Path, *args: str) -> str:
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_create_module_from_template_is_one_commit(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    service = ProjectService(ProjectStore(root), history=GitHistory(GitSettings(enabled=True, squash_window_seconds=0)))
    project = service.create_project("P")
    service.create_module_folder(project.id, "Specs")
    before = _git(root / project.id, "log", "--format=%H").splitlines()
    service.create_module_from_template(project.id, "builtin:requirements", folder_id="specs")
    after = _git(root / project.id, "log", "--format=%H").splitlines()
    assert len(after) == len(before) + 1
    files = _git(root / project.id, "show", "--name-only", "--format=", "HEAD").split()
    assert "project.json" in files and "modules/requirements/module.json" in files
    assert sum(f.startswith("modules/requirements/requirements/") for f in files) == 18
    assert _git(root / project.id, "status", "--porcelain") == ""
    # A refused template writes and commits nothing.
    with pytest.raises(ValueError):
        service.create_module_from_template(project.id, "builtin:test_cases", prefix="1bad")
    assert _git(root / project.id, "log", "--format=%H").splitlines() == after


# --- create module from template: REST / WebSocket --------------------------------------


def test_rest_and_ws_create_module_from_template_match_and_broadcast(client, tmp_path):
    rest_project = client.post("/projects", json={"name": "Rest"}).json()["id"]
    ws_project = client.post("/projects", json={"name": "Ws"}).json()["id"]
    for project_id in (rest_project, ws_project):
        client.post(f"/projects/{project_id}/module-folders", json={"name": "Specs"})
    body = {"template_id": "builtin:test_execution", "name": "Runs", "folder_id": "specs"}
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.post(f"/projects/{rest_project}/modules/from-template", json=body)
        assert resp.status_code == 200, resp.text
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": rest_project}
        assert json.loads(observer.receive_text()) == {"event": "changed", "project_id": rest_project}
        response = _rpc(ws, "module.create_from_template", {"project_id": ws_project, **body})
        assert json.loads(observer.receive_text()) == {"event": "changed", "project_id": ws_project}
        _assert_no_broadcast(ws)
    rest_result = resp.json()
    assert response["result"] == rest_result
    assert rest_result["module"] == {
        "id": "runs", "name": "Runs", "prefix": "TX",
        "description": "Test execution: one entry per executed test case.", "type": "test_execution",
    }
    assert rest_result["headings"] == 7 and rest_result["extended_attributes"] == []
    assert rest_result["added_attributes"][0] == "object_type"
    for project_id in (rest_project, ws_project):
        tree = client.get(f"/projects/{project_id}/module-tree").json()
        assert tree == [{"folder": "specs", "name": "Specs", "children": [{"module": "runs"}]}]
    rest_reqs = client.get(f"/projects/{rest_project}/modules/runs/requirements").json()
    ws_reqs = client.get(f"/projects/{ws_project}/modules/runs/requirements").json()
    assert rest_reqs == ws_reqs and len(rest_reqs) == 7


@pytest.mark.parametrize(
    ("project", "body", "status", "code"),
    [
        ("p", {"template_id": "user:nope"}, 404, -32001),
        ("ghost", {"template_id": "builtin:requirements"}, 404, -32001),
        ("p", {"template_id": "builtin:requirements", "folder_id": "ghost"}, 404, -32001),
        ("p", {"template_id": "bogus"}, 400, -32602),
        ("p", {"template_id": "builtin:requirements", "prefix": "1bad"}, 400, -32602),
        ("p", {"template_id": "user:mismatch"}, 400, -32602),
    ],
    ids=["unknown-template", "unknown-project", "unknown-folder", "malformed-id", "bad-prefix", "type-mismatch"],
)
def test_create_module_from_template_errors_have_rest_ws_parity(client, tmp_path, project, body, status, code):
    client.post("/projects", json={"name": "P"})
    client.post("/projects/p/attributes", json={"id": "prio", "name": "Priority", "type": "string"})
    _write(tmp_path / "_templates" / "modules" / "mismatch.json", _module_template())
    before = _snapshot(tmp_path)
    with client.websocket_connect("/ws") as sender, client.websocket_connect("/ws") as observer:
        resp = client.post(f"/projects/{project}/modules/from-template", json=body)
        assert resp.status_code == status, resp.text
        response = _rpc(sender, "module.create_from_template", {"project_id": project, **body})
        assert response["error"] == {"code": code, "message": resp.json()["detail"]}
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path) == before


def test_create_module_from_template_in_newer_project_is_409(client, tmp_path):
    client.post("/projects", json={"name": "P"})
    path = tmp_path / "p" / "project.json"
    data = json.loads(path.read_text())
    data["format_version"] = PROJECT_FORMAT.current + 1
    path.write_text(json.dumps(data, indent=2) + "\n")
    before = _snapshot(tmp_path)
    resp = client.post("/projects/p/modules/from-template", json={"template_id": "builtin:requirements"})
    assert resp.status_code == 409 and "newer ReqManTool" in resp.json()["detail"]
    assert _snapshot(tmp_path) == before


def test_rest_and_ws_list_module_templates(client):
    rest = client.get("/module-templates")
    assert rest.status_code == 200
    with client.websocket_connect("/ws") as ws:
        assert _rpc(ws, "module_template.list", {})["result"] == rest.json()
    assert [t["id"] for t in rest.json()] == [
        "builtin:operational_scenarios", "builtin:requirements", "builtin:test_execution", "builtin:test_cases",
    ]


# --- save module as template ---------------------------------------------------------------


def _project_with_headings(service):
    project = service.create_project("P")
    service.add_attribute_definition(
        project.id, "object_type", "Object Type", "enum", default="Requirement",
        choices=["Heading", "Information", "Requirement"],
    )
    service.create_module(project.id, "Spec", "SP", "Spec module")
    service.create_module(project.id, "Other", "OT")
    service.add_attribute_definition(project.id, "risk", "Risk", "integer", modules=["other"])
    service.add_attribute_definition(project.id, "owner", "Owner", "string", required=True, default="QA")
    service.create_link_configuration(project.id, "Refines", "spec", "other")

    def add(text, kind, parent=None):
        return service.add_requirement(project.id, "spec", {"object_text": text, "object_type": kind}, parent).id

    intro = add("Introduction", "Heading")
    add('<div xmlns="http://www.w3.org/1999/xhtml"><p><strong>Why</strong> this exists</p></div>', "Information", intro)
    add("Second info is not the text", "Information", intro)
    scope = add("Scope", "Heading", intro)
    add("Details", "Heading", scope)
    req = add("A requirement", "Requirement")
    add("Below a requirement", "Heading", req)  # left out with its subtree
    add("Annex", "Heading")
    return project


def test_save_module_as_template_writes_canonical_file_with_nested_headings(service):
    project = _project_with_headings(service)
    view = service.create_view(project.id, "Spec view", ["object_text", "object_type", "risk", "refines", "owner"])
    summary = service.save_module_as_template(project.id, "spec", "  My Spec ", "Saved", view.id)
    assert summary["id"] == "user:my-spec"
    assert (summary["name"], summary["headings"], summary["view"], summary["error"]) == ("My Spec", 4, "Spec view", None)

    path = service.store.root / "_templates" / "modules" / "my-spec.json"
    text = path.read_text()
    data = json.loads(text)
    assert text == json.dumps(data, indent=2) + "\n"
    assert data == {
        "format_version": 1,
        "kind": "module",
        "id": "my-spec",
        "name": "My Spec",
        "description": "Saved",
        "module": {"name": "Spec", "prefix": "SP", "description": "Spec module"},
        # Only attributes applying to the module, never object_text or link mirrors; never required.
        "attributes": [
            {"id": "object_type", "name": "Object Type", "type": "enum", "default": "Requirement",
             "choices": ["Heading", "Information", "Requirement"]},
            {"id": "owner", "name": "Owner", "type": "string", "default": "QA", "choices": None},
        ],
        # References to attributes not saved (scoped elsewhere, link mirror) are dropped.
        "view": {"name": "Spec view", "attribute_ids": ["object_text", "object_type", "owner"]},
        "skeleton": [
            {"heading": "Introduction", "text": "Why this exists", "children": [
                {"heading": "Scope", "children": [{"heading": "Details"}]},
            ]},
            {"heading": "Annex"},
        ],
    }
    # Not a project template, and the project is unchanged by saving.
    assert not any(t["id"] == "user:my-spec" for t in service.list_templates())
    listed = next(t for t in service.list_module_templates() if t["id"] == "user:my-spec")
    assert listed == summary


def test_saved_module_template_round_trips_into_another_project(service):
    source = _project_with_headings(service)
    service.save_module_as_template(source.id, "spec", "Round trip")
    target = service.create_project("Target")
    result = service.create_module_from_template(target.id, "user:round-trip")
    assert result["module"].name == "Spec"
    assert [(o[1], o[2], o[3], o[4]) for o in _objects(service, target.id, "spec")] == [
        ("Introduction", "Heading", None, 0),
        ("Why this exists", "Information", "SP-0001", 0),
        ("Scope", "Heading", "SP-0001", 1),
        ("Details", "Heading", "SP-0003", 0),
        ("Annex", "Heading", None, 1),
    ]
    # Saved attributes are optional when merged into a project that lacks them.
    assert _attr(service, target.id, "owner").required is False


def test_save_module_without_object_type_has_an_empty_skeleton(service):
    project = service.create_project("P")
    service.create_module(project.id, "Plain", "PL", type="test_cases")
    service.add_requirement(project.id, "plain", {"object_text": "x"})
    summary = service.save_module_as_template(project.id, "plain", "Plain")
    data = json.loads((service.store.root / "_templates" / "modules" / "plain.json").read_text())
    assert data["skeleton"] == [] and summary["headings"] == 0
    assert data["module"] == {"name": "Plain", "prefix": "PL", "description": "", "type": "test_cases"}
    assert "view" not in data


def test_save_module_as_template_errors(service):
    project = _project_with_headings(service)
    service.save_module_as_template(project.id, "spec", "Taken")
    before = _snapshot(service.store.root / "_templates")
    with pytest.raises(ConflictError, match="A module template named 'Taken' already exists \\(user:taken\\)"):
        service.save_module_as_template(project.id, "spec", " TAKEN ")
    with pytest.raises(ConflictError, match="builtin:requirements"):
        service.save_module_as_template(project.id, "spec", "requirements SPECIFICATION")
    with pytest.raises(FileNotFoundError, match="View 'ghost' not found"):
        service.save_module_as_template(project.id, "spec", "New", view_id="ghost")
    with pytest.raises(FileNotFoundError):
        service.save_module_as_template(project.id, "ghost", "New")
    with pytest.raises(FileNotFoundError):
        service.save_module_as_template("ghost", "spec", "New")
    with pytest.raises(ValueError, match="Template name must not be empty"):
        service.save_module_as_template(project.id, "spec", "  ")
    with pytest.raises(ValueError, match="Template description must be a string"):
        service.save_module_as_template(project.id, "spec", "New", 5)
    assert _snapshot(service.store.root / "_templates") == before
    # A project template of the same name does not conflict (separate namespace).
    service.save_project_as_template(project.id, "Shared name")
    assert service.save_module_as_template(project.id, "spec", "Shared name")["id"] == "user:shared-name"


def test_save_module_with_more_than_2000_headings_is_refused(service):
    project = service.create_project("P")
    skeleton = [{"heading": f"H{i}"} for i in range(MAX_SKELETON_NODES)]
    template_id = _write_module_template(service, "big", _module_template(skeleton=skeleton, view=None))
    service.create_module_from_template(project.id, template_id)
    assert service.save_module_as_template(project.id, "spec", "At limit")["headings"] == MAX_SKELETON_NODES
    service.add_requirement(project.id, "spec", {"object_text": "One more", "object_type": "Heading"})
    before = _snapshot(service.store.root / "_templates")
    with pytest.raises(ValueError, match="Module 'spec' has more than 2000 headings; too many for a template"):
        service.save_module_as_template(project.id, "spec", "Too big")
    assert _snapshot(service.store.root / "_templates") == before


def test_rest_and_ws_save_module_as_template_broadcast_templates_changed(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    client.post(f"/projects/{project_id}/modules/from-template", json={"template_id": "builtin:requirements"})
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as observer:
        resp = client.post(f"/projects/{project_id}/modules/requirements/save-as-template",
                           json={"name": "Rest copy", "view_id": "requirements"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == "user:rest-copy" and resp.json()["headings"] == 16
        assert json.loads(ws.receive_text()) == {"event": "templates_changed"}
        assert json.loads(observer.receive_text()) == {"event": "templates_changed"}
        response = _rpc(ws, "module.save_as_template",
                        {"project_id": project_id, "id": "requirements", "name": "Ws copy", "view_id": "requirements"})
        assert json.loads(observer.receive_text()) == {"event": "templates_changed"}
        _assert_no_broadcast(ws)
    assert {k: v for k, v in response["result"].items() if k not in ("id", "name")} == {
        k: v for k, v in resp.json().items() if k not in ("id", "name")
    }
    rest_file = json.loads((tmp_path / "_templates" / "modules" / "rest-copy.json").read_text())
    ws_file = json.loads((tmp_path / "_templates" / "modules" / "ws-copy.json").read_text())
    assert {k: v for k, v in rest_file.items() if k not in ("id", "name")} == {
        k: v for k, v in ws_file.items() if k not in ("id", "name")
    }


@pytest.mark.parametrize(
    ("module", "params", "status", "code"),
    [
        ("requirements", {"name": "rest copy"}, 409, None),
        ("requirements", {"name": "X", "view_id": "ghost"}, 404, -32001),
        ("ghost", {"name": "X"}, 404, -32001),
        ("requirements", {"name": " "}, 400, -32602),
    ],
    ids=["duplicate-name", "unknown-view", "unknown-module", "blank-name"],
)
def test_save_module_as_template_errors_have_parity_and_no_broadcast(client, tmp_path, module, params, status, code):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    client.post(f"/projects/{project_id}/modules/from-template", json={"template_id": "builtin:requirements"})
    client.post(f"/projects/{project_id}/modules/requirements/save-as-template", json={"name": "Rest copy"})
    before = _snapshot(tmp_path)
    with client.websocket_connect("/ws") as sender, client.websocket_connect("/ws") as observer:
        resp = client.post(f"/projects/{project_id}/modules/{module}/save-as-template", json=params)
        assert resp.status_code == status, resp.text
        response = _rpc(sender, "module.save_as_template", {"project_id": project_id, "id": module, **params})
        assert response["error"]["message"] == resp.json()["detail"]
        if code is not None:
            assert response["error"]["code"] == code
        _assert_no_broadcast(observer)
    assert _snapshot(tmp_path) == before


# --- project templates referencing module templates -------------------------------------------


def _project_template(modules, **overrides) -> dict:
    data = {"format_version": 2, "id": "t", "name": "T", "attributes": [], "modules": modules,
            "link_configurations": [], "views": []}
    data.update(overrides)
    return data


def test_project_template_module_defaults_come_from_the_module_template(service):
    template_id = _write_project_template(service, "t", _project_template([
        {"key": "r", "module_template": "builtin:requirements"},
        {"key": "t", "name": "Tests", "module_template": "builtin:test_cases"},
        {"key": "x", "name": "Extra", "prefix": "EX", "module_template": "builtin:requirements"},
    ]))
    entry = next(t for t in service.list_templates() if t["id"] == template_id)
    assert entry["error"] is None
    assert entry["modules"] == [
        {"key": "r", "name": "Requirements", "prefix": "REQ", "type": "requirements"},
        {"key": "t", "name": "Tests", "prefix": "TC", "type": "test_cases"},
        {"key": "x", "name": "Extra", "prefix": "EX", "type": "requirements"},
    ]
    project = service.create_project("P", template_id=template_id)
    modules = {m.id: m for m in service.list_modules(project.id)}
    assert modules["requirements"].description == "Requirements specification."
    assert modules["tests"].type == "test_cases"
    # A shared attribute is scoped to every module that uses it, in module order.
    assert _attr(service, project.id, "object_type").modules == ["requirements", "tests", "extra"]
    assert _attr(service, project.id, "preconditions").modules == ["tests"]
    assert [v.name for v in service.get_project(project.id).views] == ["Requirements", "Test Cases"]
    assert [len(service.list_requirements(project.id, m)) for m in ("requirements", "tests", "extra")] == [18, 9, 18]
    # Skeleton ids come from the new project's counters.
    assert service.get_project(project.id).id_counters == {"REQ": 18, "TC": 9, "EX": 18}
    assert not (service.store.root / ".reqman-staging").exists()


@pytest.mark.parametrize(
    ("modules", "message"),
    [
        ([{"key": "r", "module_template": "builtin:test_cases", "type": "requirements"}],
         "modules[0] ('Test Specification'): type 'requirements' differs from module template 'builtin:test_cases' ('test_cases')"),
        ([{"key": "r", "module_template": "user:nope"}], "modules[0] ('r'): Module template 'user:nope' not found"),
        ([{"key": "r", "module_template": "user:broken"}], "modules[0] ('r'): Module template 'user:broken' is invalid"),
        ([{"key": "a", "name": "A", "prefix": "A", "skeleton": [{"heading": "H"}]}],
         "modules[0] ('A'): a heading skeleton needs an 'Object Type' attribute"),
        ([{"key": "r", "module_template": "builtin:requirements"}, {"key": "s", "module_template": "builtin:requirements"}],
         "modules[1] ('Requirements'): duplicate module name 'Requirements'"),
    ],
    ids=["type-mismatch", "missing-module-template", "invalid-module-template", "skeleton-without-object-type",
         "duplicate-default-name"],
)
def test_invalid_module_template_references_are_listed_and_refused(service, modules, message):
    _write_module_template(service, "broken", "{nope")
    template_id = _write_project_template(service, "t", _project_template(modules))
    entry = next(t for t in service.list_templates() if t["id"] == template_id)
    assert entry["error"] is not None and message in entry["error"], entry["error"]
    with pytest.raises(ValueError, match="is invalid"):
        service.create_project("P", template_id=template_id)
    assert service.list_projects() == []
    assert not (service.store.root / ".reqman-staging").exists()


def test_inline_skeleton_replaces_the_module_templates_and_empty_list_means_none(service):
    template_id = _write_project_template(service, "t", _project_template([
        {"key": "a", "name": "Inline", "module_template": "builtin:requirements",
         "skeleton": [{"heading": "Only", "text": "Info", "children": [{"heading": "Child"}]}]},
        {"key": "b", "name": "None", "prefix": "NO", "module_template": "builtin:requirements", "skeleton": []},
    ]))
    project = service.create_project("P", template_id=template_id)
    assert [(o[1], o[2]) for o in _objects(service, project.id, "inline")] == [
        ("Only", "Heading"), ("Info", "Information"), ("Child", "Heading"),
    ]
    assert service.list_requirements(project.id, "none") == []


def test_inline_skeleton_with_template_attributes_and_no_module_template(service):
    template_id = _write_project_template(service, "t", _project_template(
        [{"key": "a", "name": "A", "prefix": "A", "skeleton": [{"heading": "H"}]}],
        attributes=[OBJECT_TYPE],
    ))
    project = service.create_project("P", template_id=template_id)
    assert [(o[1], o[2]) for o in _objects(service, project.id, "a")] == [("H", "Heading")]


def test_v1_template_skeleton_and_module_template_keys_are_ignored(service):
    template_id = _write_project_template(service, "t", _project_template(
        [{"key": "a", "name": "A", "prefix": "A", "module_template": "builtin:test_cases", "skeleton": [{"heading": "H"}]}],
        format_version=1, attributes=[OBJECT_TYPE],
    ))
    project = service.create_project("P", template_id=template_id)
    module = service.get_module(project.id, "a")
    assert module.type == "requirements"
    assert service.list_requirements(project.id, "a") == []
    assert [a.id for a in service.get_project(project.id).attributes if not a.system][-1] == "object_type"
    # The file on disk is never rewritten.
    assert json.loads((service.store.root / "_templates" / "t.json").read_text())["format_version"] == 1


def test_module_template_view_follows_template_views_and_yields_on_name_clash(service):
    template_id = _write_project_template(service, "t", _project_template(
        [{"key": "r", "module_template": "builtin:requirements"}, {"key": "t", "module_template": "builtin:test_cases"}],
        views=[{"name": "requirements", "attribute_ids": ["object_text"]}, {"name": "Mine", "attribute_ids": []}],
    ))
    project = service.create_project("P", template_id=template_id)
    views = service.get_project(project.id).views
    assert [(v.name, v.attribute_ids) for v in views][:2] == [("requirements", ["object_text"]), ("Mine", [])]
    assert [v.name for v in views] == ["requirements", "Mine", "Test Cases"]


# --- RBC skeletons ----------------------------------------------------------------------------


def test_rbc_template_creates_the_heading_skeletons(service):
    project = service.create_project("RBC", template_id=RBC)
    headings, texts = {}, {}
    for module in service.list_modules(project.id):
        kinds = [r.attributes.get("object_type") for r in service.list_requirements(project.id, module.id)]
        headings[module.id] = kinds.count("Heading")
        texts[module.id] = kinds.count("Information")
        assert len(kinds) == headings[module.id] + texts[module.id], module.id
    assert headings == {
        "opscen": 13, "strs": 27, "syrs": 28, "swrs": 37, "systest": 21, "swtest": 21, "systestexec": 13,
        "swtestexec": 13,
    }
    assert texts == {
        "opscen": 1, "strs": 3, "syrs": 2, "swrs": 1, "systest": 2, "swtest": 2, "systestexec": 2, "swtestexec": 2,
    }
    counters = service.get_project(project.id).id_counters
    assert counters["SYRS"] == 30 and counters["SWRS"] == 38 and counters["STRS"] == 30 and counters["OPS"] == 14


# --- save project as template: skeletons ---------------------------------------------------------


def test_save_project_as_template_writes_module_skeletons_and_round_trips(service):
    project = _project_with_headings(service)
    service.save_project_as_template(project.id, "With headings")
    data = json.loads((service.store.root / "_templates" / "with-headings.json").read_text())
    modules = {m["key"]: m for m in data["modules"]}
    assert list(modules["spec"]) == ["key", "name", "prefix", "description", "skeleton"]
    assert modules["spec"]["skeleton"] == [
        {"heading": "Introduction", "text": "Why this exists", "children": [
            {"heading": "Scope", "children": [{"heading": "Details"}]},
        ]},
        {"heading": "Annex"},
    ]
    assert "skeleton" not in modules["other"]  # written only when non-empty
    copy_project = service.create_project("Copy", template_id="user:with-headings")
    assert [(o[1], o[2], o[3]) for o in _objects(service, copy_project.id, "spec")] == [
        ("Introduction", "Heading", None),
        ("Why this exists", "Information", "SP-0001"),
        ("Scope", "Heading", "SP-0001"),
        ("Details", "Heading", "SP-0003"),
        ("Annex", "Heading", None),
    ]
    assert service.list_requirements(copy_project.id, "other") == []


def test_project_template_attribute_scope_is_extended_by_module_template_reference(service):
    template_id = _write_project_template(service, "t", _project_template(
        [{"key": "a", "name": "A", "prefix": "A"}, {"key": "r", "module_template": "builtin:requirements"}],
        attributes=[
            dict(OBJECT_TYPE, modules=["a"]),
            {"id": "status", "name": "Status", "type": "enum", "choices": ["Draft", "Done"], "required": True,
             "default": "Draft"},
        ],
    ))
    project = service.create_project("P", template_id=template_id)
    object_type = _attr(service, project.id, "object_type")
    status = _attr(service, project.id, "status")
    # The template's own attributes win; the module template only extends the scope.
    assert (object_type.modules, object_type.choices) == (["a", "requirements"], OBJECT_TYPE["choices"])
    assert (status.required, status.modules, status.choices) == (True, [], ["Draft", "Done"])
    assert {r.attributes["status"] for r in service.list_requirements(project.id, "requirements")} == {"Draft"}


def test_create_module_from_template_leaves_unrelated_attribute_scopes_alone(service):
    # Documented rule: an existing attribute is never changed except to add the new
    # module to its scope. Attributes the template does not touch keep
    # their stored ``modules`` lists byte for byte.
    project = service.create_project("P")
    for name in ("A", "B"):
        service.create_module(project.id, name, name)
    service.add_attribute_definition(project.id, "x", "X", modules=["a", "b"])
    service.reorder_module(project.id, "b", "up")  # display order is now b, a
    before = {a.id: list(a.modules) for a in service.get_project(project.id).attributes}

    service.create_module_from_template(project.id, "builtin:requirements")
    after = {a.id: list(a.modules) for a in service.get_project(project.id).attributes}
    assert after["x"] == before["x"] == ["a", "b"]


def test_create_module_from_template_inserts_new_module_at_its_display_position_in_extended_scopes(service):
    # R1: an extended scope gets the new module at its display position; the
    # other entries keep their stored order (no re-sort).
    project = service.create_project("P")
    for name in ("A", "B"):
        service.create_module(project.id, name, name)
    service.add_attribute_definition(
        project.id, "priority", "Priority", "enum", choices=["High", "Low"], default="Low", modules=["a", "b"]
    )
    folder = service.create_module_folder(project.id, "F")["id"]
    service.move_module_folder(project.id, folder, None, 0)  # F (holding the new module) is shown first
    template_id = _write_module_template(service, "spec", _module_template())
    result = service.create_module_from_template(project.id, template_id, folder_id=folder)
    assert "priority" in result["extended_attributes"]
    assert _attr(service, project.id, "priority").modules == ["spec", "a", "b"]
    # Shown last: appended.
    service.reorder_module(project.id, "b", "up")  # display order F/spec, b, a; stored scope stays
    assert _attr(service, project.id, "priority").modules == ["spec", "a", "b"]
    service.create_module_from_template(project.id, template_id, name="Last")
    assert _attr(service, project.id, "priority").modules == ["spec", "a", "b", "last"]


def test_create_module_from_template_leaves_nothing_behind_when_saving_the_project_fails(service, monkeypatch):
    # R3: a failure after the module file is written removes the new module's files.
    project = service.create_project("P")
    service.create_module(project.id, "A", "A")
    template_id = _write_module_template(service, "spec", _module_template())
    root = service.store.root / project.id
    before = _snapshot(root)

    def fail(_project):
        raise OSError("disk full")

    monkeypatch.setattr(service.store, "save_project", fail)
    with pytest.raises(OSError, match="disk full"):
        service.create_module_from_template(project.id, template_id)
    monkeypatch.undo()
    assert _snapshot(root) == before
    assert not (root / "modules" / "spec").exists()
    assert [m.id for m in service.list_modules(project.id)] == ["a"]
    # A retry works and gets the same module id.
    assert service.create_module_from_template(project.id, template_id)["module"].id == "spec"


def test_create_module_from_template_reports_object_type_scope_extended_for_the_skeleton(service):
    # R6: the skeleton's Object Type attribute, not listed by the template but
    # scoped elsewhere in the project, is extended to the new module and reported.
    project = service.create_project("P")
    service.create_module(project.id, "A", "A")
    service.add_attribute_definition(
        project.id, "object_type", "Object Type", "enum",
        choices=["Heading", "Information", "Requirement"], default="Requirement", modules=["a"],
    )
    template = _module_template(
        attributes=[{"id": "priority", "name": "Priority", "type": "enum", "choices": ["High", "Low"]}],
        view=None,
    )
    template_id = _write_module_template(service, "spec", template)
    result = service.create_module_from_template(project.id, template_id)
    assert result["extended_attributes"] == ["object_type"]
    assert result["added_attributes"] == ["priority"]
    assert _attr(service, project.id, "object_type").modules == ["a", "spec"]


def test_save_project_as_template_with_too_many_headings_has_the_project_prefix(service):
    # R7: the >2000-heading error of a module is reported like every other one.
    project = service.create_project("P")
    skeleton = [{"heading": f"H{i}"} for i in range(MAX_SKELETON_NODES)]
    template_id = _write_module_template(service, "big", _module_template(skeleton=skeleton, view=None))
    service.create_module_from_template(project.id, template_id)
    service.add_requirement(project.id, "spec", {"object_text": "One more", "object_type": "Heading"})
    before = _snapshot(service.store.root / "_templates")
    with pytest.raises(
        ValueError,
        match=f"^Project '{project.id}' cannot be saved as a template: Module 'spec' has more than 2000 headings",
    ):
        service.save_project_as_template(project.id, "Too big")
    assert _snapshot(service.store.root / "_templates") == before
