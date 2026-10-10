"""AI prompt library: built-in and user prompts, CRUD, export/import, rendering.

Prompts are user data outside every project (``<data>/_templates/prompts/``):
mutations broadcast ``prompts_changed`` (REST and WebSocket; the WebSocket
sender is excluded), rendering is read-only and never broadcast. Rendering
fills the placeholders server-side (project/module context, the exact CSV
import header, the attribute guide, link columns, import rules, the selected
requirements as CSV and the free text).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import csv
import importlib
import io
import json

import pytest
from fastapi.testclient import TestClient

from reqman import extensions
from reqman.extensions import ACTION_LIBRARY_WRITE, AccessDeniedError
from reqman.prompts import BUILTIN_PROMPTS_DIR, PLACEHOLDERS, PROMPT_FORMAT_VERSION, PROMPT_KEYS
from reqman.service import ConflictError, ProjectService
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


def prompt_data(name="My prompt", body="Module {{module_name}}", **extra):
    return {"name": name, "body": body, **extra}


def prompts_dir(root):
    return root / "_templates" / "prompts"


def tree_bytes(path):
    return {p: p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def rpc(ws, method, params, request_id=1):
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
    return json.loads(ws.receive_text())


_counter = {"n": 0}


def render_one(service, project_id, module_id, placeholder, **kwargs):
    """Render a fresh user prompt whose body is just ``{{placeholder}}``."""
    _counter["n"] += 1
    prompt = service.create_prompt(prompt_data(f"Only {placeholder} {_counter['n']}", "{{" + placeholder + "}}"))
    return service.render_prompt(project_id, prompt["id"], module_id, **kwargs)["text"]


def csv_rows(text):
    return list(csv.reader(io.StringIO(text)))


def rich_paragraph(text):
    return json.dumps({"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]})


# --- built-in prompts --------------------------------------------------------------


def test_builtin_prompts_are_all_valid_and_listed_with_builtin_ids(service):
    stems = sorted(p.stem for p in BUILTIN_PROMPTS_DIR.glob("*.json"))
    assert stems, "the backend ships built-in prompts"

    listed = service.list_prompts()

    builtin = [p for p in listed if p["source"] == "builtin"]
    assert sorted(p["id"] for p in builtin) == [f"builtin:{s}" for s in stems]
    assert all(p["error"] is None for p in builtin), [p for p in builtin if p["error"]]
    assert all(p["body"] and p["name"] for p in builtin)
    # Built-ins first, sorted by name (case-insensitive).
    names = [p["name"].casefold() for p in builtin]
    assert names == sorted(names)
    assert listed[: len(builtin)] == builtin


def test_builtin_prompt_files_use_the_canonical_key_order(service):
    for path in BUILTIN_PROMPTS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert list(data) == list(PROMPT_KEYS), path.name
        assert data["kind"] == "prompt" and data["format_version"] == PROMPT_FORMAT_VERSION
        assert all(p in PLACEHOLDERS for p in service.get_prompt(f"builtin:{path.stem}")["placeholders"])


def test_every_builtin_prompt_renders_without_leftover_placeholders(service):
    service.create_project("Alpha", "About alpha")
    service.create_module("alpha", "Sys", "SYS")
    req = service.add_requirement("alpha", "sys", {"object_text": "The system shall start."}).id

    for entry in service.list_prompts():
        result = service.render_prompt(
            "alpha", entry["id"], "sys", requirement_ids=[req], free_text="Some description"
        )
        assert "{{" not in result["text"], entry["id"]
        assert result["selected_ids"] == [req]
        assert result["import_mode"] == entry["import_mode"]


# --- storage location ------------------------------------------------------------------


def test_user_prompts_are_never_listed_as_projects_or_templates(service, root):
    service.create_project("Alpha")
    templates_before = service.list_templates()
    module_templates_before = service.list_module_templates()

    created = service.create_prompt(prompt_data())

    assert created["id"] == "user:my-prompt"
    assert (prompts_dir(root) / "my-prompt.json").is_file()
    assert [p.id for p in service.list_projects()] == ["alpha"]
    assert service.list_templates() == templates_before
    assert service.list_module_templates() == module_templates_before


def test_a_prompt_and_a_project_template_may_share_a_stem(service, root):
    service.create_project("Alpha")
    template = service.save_project_as_template("alpha", "Shared")
    prompt = service.create_prompt(prompt_data("Shared"))

    assert template["id"] == "user:shared" and prompt["id"] == "user:shared"
    assert [t["id"] for t in service.list_templates() if t["source"] == "user"] == ["user:shared"]
    assert [p["id"] for p in service.list_prompts() if p["source"] == "user"] == ["user:shared"]


# --- CRUD ------------------------------------------------------------------------------


def test_create_prompt_writes_canonical_json_and_reloads(service, root):
    created = service.create_prompt({
        "name": "  Zażółć \"quoted\"  ",
        "description": "Line 1\nLine 2",
        "selection": "optional",
        "free_text": "required",
        "free_text_label": "Describe it",
        "import_mode": "update",
        "body": "{{project_name}} / {{ free_text }} ✓",
        "unknown_key": "ignored",
    })

    assert created["id"].startswith("user:") and created["id"] != "user:"
    assert {k: v for k, v in created.items() if k != "id"} == {
        "source": "user",
        "name": 'Zażółć "quoted"',
        "description": "Line 1\nLine 2",
        "selection": "optional",
        "free_text": "required",
        "free_text_label": "Describe it",
        "import_mode": "update",
        "body": "{{project_name}} / {{ free_text }} ✓",
        "placeholders": ["project_name", "free_text"],
        "error": None,
    }
    stem = created["id"].split(":", 1)[1]
    text = (prompts_dir(root) / f"{stem}.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert text == json.dumps(data, indent=2) + "\n"
    assert list(data) == list(PROMPT_KEYS)
    assert data["kind"] == "prompt" and data["format_version"] == PROMPT_FORMAT_VERSION
    assert "unknown_key" not in data

    reloaded = ProjectService(ProjectStore(root))
    assert reloaded.get_prompt(created["id"]) == created


def test_create_prompt_defaults(service):
    created = service.create_prompt({"name": "Bare", "body": "Hello"})
    assert {k: created[k] for k in ("description", "selection", "free_text", "free_text_label", "import_mode")} == {
        "description": "", "selection": "none", "free_text": "none", "free_text_label": "", "import_mode": "create",
    }
    assert created["placeholders"] == []


@pytest.mark.parametrize(
    "data, message",
    [
        (prompt_data(body="Hi {{nope}} {{module_name}}"), "Unknown placeholder(s) {{nope}}"),
        (prompt_data(name="   "), "'name' must not be empty"),
        (prompt_data(name=None), "'name' must not be empty"),
        (prompt_data(name=12), "'name' must be a string"),
        (prompt_data(body=""), "'body' must not be empty"),
        (prompt_data(selection="all"), "'selection' must be one of"),
        (prompt_data(free_text=True), "'free_text' must be one of"),
        (prompt_data(import_mode="replace"), "'import_mode' must be one of"),
        (prompt_data(description=["x"]), "'description' must be a string"),
        (prompt_data(name="x" * 201), "too long"),
        ("not an object", "must be an object"),
        (None, "must be an object"),
    ],
)
def test_create_prompt_rejects_invalid_shapes_and_writes_nothing(service, root, data, message):
    with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)").replace("{", r"\{").replace("}", r"\}")):
        service.create_prompt(data)
    assert not prompts_dir(root).exists() or list(prompts_dir(root).iterdir()) == []


def test_prompt_names_are_unique_case_insensitively_across_builtin_and_user(service, root):
    builtin_name = service.list_prompts()[0]["name"]
    with pytest.raises(ConflictError, match="already exists"):
        service.create_prompt(prompt_data(builtin_name.upper()))

    first = service.create_prompt(prompt_data("Mine"))
    with pytest.raises(ConflictError):
        service.create_prompt(prompt_data("  MINE "))
    other = service.create_prompt(prompt_data("Other"))
    with pytest.raises(ConflictError):
        service.update_prompt(other["id"], prompt_data("mine"))

    # Keeping (or re-casing) its own name is fine.
    assert service.update_prompt(first["id"], prompt_data("MINE"))["name"] == "MINE"
    assert sorted(p.name for p in prompts_dir(root).iterdir()) == ["mine.json", "other.json"]


def test_names_with_the_same_slug_get_distinct_files(service, root):
    a = service.create_prompt(prompt_data("A b"))
    b = service.create_prompt(prompt_data("A-b"))
    assert (a["id"], b["id"]) == ("user:a-b", "user:a-b-2")
    assert service.get_prompt("user:a-b")["name"] == "A b"


def test_update_prompt_keeps_the_file_stem_and_replaces_content(service, root):
    created = service.create_prompt(prompt_data("Original", description="old"))
    updated = service.update_prompt(created["id"], prompt_data("Renamed", body="{{csv_header}}"))

    assert updated["id"] == created["id"] == "user:original"
    assert updated["description"] == ""
    data = json.loads((prompts_dir(root) / "original.json").read_text(encoding="utf-8"))
    assert data["name"] == "Renamed" and data["body"] == "{{csv_header}}"
    assert not (prompts_dir(root) / "renamed.json").exists()


def test_update_prompt_validates_before_writing(service, root):
    created = service.create_prompt(prompt_data("Original"))
    before = tree_bytes(root)
    with pytest.raises(ValueError, match="Unknown placeholder"):
        service.update_prompt(created["id"], prompt_data(body="{{bogus}}"))
    assert tree_bytes(root) == before


def test_builtin_prompts_are_read_only(service):
    builtin_id = service.list_prompts()[0]["id"]
    before = tree_bytes(BUILTIN_PROMPTS_DIR)

    with pytest.raises(ValueError, match="read-only"):
        service.update_prompt(builtin_id, prompt_data("Changed"))
    with pytest.raises(ValueError, match="cannot be deleted"):
        service.delete_prompt(builtin_id)

    assert tree_bytes(BUILTIN_PROMPTS_DIR) == before


def test_missing_and_malformed_prompt_ids(service):
    for call in (
        lambda: service.get_prompt("user:missing"),
        lambda: service.update_prompt("user:missing", prompt_data()),
        lambda: service.delete_prompt("user:missing"),
        lambda: service.export_prompt("builtin:missing"),
    ):
        with pytest.raises(FileNotFoundError):
            call()
    for bad in ("nope", "other:x", "user:", 5, None):
        with pytest.raises(ValueError, match="Invalid prompt id"):
            service.get_prompt(bad)


def test_path_like_prompt_ids_never_escape_the_prompt_folder(service, root):
    service.create_project("Alpha")
    (root / "_templates").mkdir(exist_ok=True)
    (root / "_templates" / "outside.json").write_text(json.dumps(prompt_data("Outside")), encoding="utf-8")
    for bad in ("user:../outside", "user:../../alpha/project", "builtin:../prompts/x"):
        with pytest.raises(FileNotFoundError):
            service.get_prompt(bad)
        with pytest.raises((FileNotFoundError, ValueError)):
            service.delete_prompt(bad)
    assert (root / "_templates" / "outside.json").exists()
    assert (root / "alpha" / "project.json").exists()


def test_delete_prompt_removes_only_its_file(service, root):
    keep = service.create_prompt(prompt_data("Keep"))
    gone = service.create_prompt(prompt_data("Gone"))

    service.delete_prompt(gone["id"])

    assert [p.name for p in prompts_dir(root).iterdir()] == ["keep.json"]
    assert [p["id"] for p in service.list_prompts() if p["source"] == "user"] == [keep["id"]]
    with pytest.raises(FileNotFoundError):
        service.delete_prompt(gone["id"])


def test_invalid_user_prompt_file_is_listed_with_error_and_can_be_repaired(service, root):
    prompts_dir(root).mkdir(parents=True)
    (prompts_dir(root) / "broken.json").write_text(
        json.dumps({"name": "Broken one", "body": "{{nope}}"}), encoding="utf-8"
    )
    (prompts_dir(root) / "garbage.json").write_text("{", encoding="utf-8")
    service.create_prompt(prompt_data("Good"))

    by_id = {p["id"]: p for p in service.list_prompts()}

    assert by_id["user:broken"]["name"] == "Broken one"
    assert "Unknown placeholder" in by_id["user:broken"]["error"]
    assert by_id["user:broken"]["body"] == ""
    assert by_id["user:garbage"]["name"] == "garbage" and "not valid JSON" in by_id["user:garbage"]["error"]
    assert by_id["user:good"]["error"] is None
    with pytest.raises(ValueError, match="is invalid"):
        service.get_prompt("user:broken")

    repaired = service.update_prompt("user:broken", prompt_data("Broken one"))
    assert repaired["error"] is None and service.get_prompt("user:broken")["name"] == "Broken one"


def test_hidden_and_non_json_files_are_not_prompts(service, root):
    prompts_dir(root).mkdir(parents=True)
    (prompts_dir(root) / ".hidden.json").write_text(json.dumps(prompt_data("Hidden")), encoding="utf-8")
    (prompts_dir(root) / "notes.txt").write_text("x", encoding="utf-8")
    assert [p for p in service.list_prompts() if p["source"] == "user"] == []


# --- export / import ---------------------------------------------------------------------


def test_export_is_the_canonical_file_and_import_round_trips(service, root):
    data = {
        "name": "Ünïcødé, \"quotes\" & <tags>",
        "description": "",
        "selection": "required",
        "free_text": "optional",
        "free_text_label": "",
        "import_mode": "none",
        "body": "Line\twith tab\n{{selected_csv}}\n{{ free_text }}\n",
    }
    created = service.create_prompt(data)
    stem = created["id"].split(":", 1)[1]

    exported = service.export_prompt(created["id"])

    assert exported == (prompts_dir(root) / f"{stem}.json").read_text(encoding="utf-8")
    assert exported == json.dumps(json.loads(exported), indent=2) + "\n"

    service.delete_prompt(created["id"])
    imported = service.import_prompt(exported)
    assert {k: v for k, v in imported.items() if k != "id"} == {k: v for k, v in created.items() if k != "id"}
    assert service.export_prompt(imported["id"]) == exported


def test_export_of_a_builtin_prompt_imports_after_renaming(service):
    builtin = service.list_prompts()[0]
    exported = json.loads(service.export_prompt(builtin["id"]))

    with pytest.raises(ConflictError):
        service.import_prompt(json.dumps(exported))

    exported["name"] = "My copy"
    imported = service.import_prompt(json.dumps(exported))
    assert imported["source"] == "user" and imported["body"] == builtin["body"]


def test_import_never_overwrites_and_accepts_a_bom(service, root):
    first = service.import_prompt("﻿" + json.dumps({"kind": "prompt", "format_version": 1, **prompt_data("Same")}))
    assert first["id"] == "user:same"
    before = tree_bytes(root)

    with pytest.raises(ConflictError):
        service.import_prompt(json.dumps(prompt_data("SAME", body="other")))
    assert tree_bytes(root) == before

    second = service.import_prompt(json.dumps(prompt_data("same!", body="other")))
    assert second["id"] == "user:same-2"
    assert service.get_prompt("user:same")["body"] == first["body"]


@pytest.mark.parametrize(
    "text, message",
    [
        ("{", "not valid JSON"),
        ("[]", "must be a JSON object"),
        (json.dumps({"kind": "template", **prompt_data()}), "'kind' must be 'prompt'"),
        (json.dumps({"format_version": 2, **prompt_data()}), "newer than this ReqManTool"),
        (json.dumps({"format_version": 0, **prompt_data()}), "Unsupported format_version"),
        (json.dumps({"format_version": "1", **prompt_data()}), "must be the integer"),
        (json.dumps({"format_version": True, **prompt_data()}), "must be the integer"),
        (json.dumps(prompt_data(body="{{unknown_thing}}")), "Unknown placeholder"),
    ],
)
def test_import_rejects_invalid_files(service, root, text, message):
    with pytest.raises(ValueError, match="The prompt file is invalid") as excinfo:
        service.import_prompt(text)
    assert message in str(excinfo.value)
    assert not prompts_dir(root).exists()


def test_import_rejects_non_string_and_oversized_files(service):
    with pytest.raises(ValueError, match="must be a string"):
        service.import_prompt({"name": "x"})
    with pytest.raises(ValueError, match="too large"):
        service.import_prompt(json.dumps(prompt_data(description="x" * (300 * 1024))))


# --- prompts are not project data ---------------------------------------------------------


def test_prompt_mutations_never_touch_projects_even_when_locked(service, root):
    service.create_project("Alpha")
    service.create_module("alpha", "Sys", "SYS")
    service.lock_project("alpha", "Release")
    before = tree_bytes(root / "alpha")
    service.take_touched_projects()

    created = service.create_prompt(prompt_data())
    service.update_prompt(created["id"], prompt_data(body="changed"))
    service.import_prompt(json.dumps(prompt_data("Imported")))
    service.delete_prompt(created["id"])

    assert tree_bytes(root / "alpha") == before
    assert service.take_touched_projects() == set()


class _DenyLibrary:
    def check(self, identity, action, project_id=None):
        if action == ACTION_LIBRARY_WRITE:
            raise AccessDeniedError("library write denied")


def test_prompt_library_writes_need_the_library_permission(service, root, monkeypatch):
    created = service.create_prompt(prompt_data())
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _DenyLibrary())
    before = tree_bytes(root)

    for call in (
        lambda: service.create_prompt(prompt_data("Other")),
        lambda: service.update_prompt(created["id"], prompt_data(body="x")),
        lambda: service.delete_prompt(created["id"]),
        lambda: service.import_prompt(json.dumps(prompt_data("Imported"))),
    ):
        with pytest.raises(AccessDeniedError):
            call()

    assert tree_bytes(root) == before
    assert service.get_prompt(created["id"])["name"] == "My prompt"
    assert len(service.list_prompts()) > 1


# --- rendering ------------------------------------------------------------------------------


@pytest.fixture
def rich_project(service):
    """``alpha`` with modules ``sys`` (requirements), ``stk``, ``tests`` (test_cases) and scoped attributes."""
    service.create_project("Alpha", "Alpha description")
    service.create_module("alpha", "Sys", "SYS", "System requirements")
    service.create_module("alpha", "Stk", "STK")
    service.create_module("alpha", "Tests", "TC", type="test_cases")
    service.add_attribute_definition("alpha", "priority", "Priority", type="integer", required=True, default=3)
    service.add_attribute_definition("alpha", "status", "Status", type="enum", choices=["Draft", "Approved"])
    service.add_attribute_definition("alpha", "due", "Due", type="date")
    service.add_attribute_definition("alpha", "safety", "Safety", type="boolean", default=False)
    service.add_attribute_definition("alpha", "stk_only", "Stakeholder", modules=["stk"])
    service.add_attribute_definition("alpha", "steps", "Steps", type="text", module_types=["test_cases"])
    service.create_link_configuration("alpha", "Satisfies", "sys", "stk")
    service.create_link_configuration("alpha", "Verifies", "tests", "sys")
    return "alpha"


def test_render_fills_project_and_module_context(service, rich_project):
    created = service.create_prompt(prompt_data(
        "Context",
        "{{project_name}}|{{project_description}}|{{module_name}}|{{module_description}}|"
        "{{module_type}}|{{module_prefix}}",
    ))

    result = service.render_prompt("alpha", created["id"], "sys")
    assert result["text"] == "Alpha|Alpha description|Sys|System requirements|requirements|SYS"
    assert (result["module_id"], result["module_name"], result["import_mode"]) == ("sys", "Sys", "create")
    assert result["prompt"]["id"] == created["id"] and result["selected_ids"] == []

    result = service.render_prompt("alpha", created["id"], "tests")
    assert result["text"] == "Alpha|Alpha description|Tests||test_cases|TC"


def test_render_csv_header_is_the_exact_import_header_of_the_target_module(service, rich_project):
    assert render_one(service, "alpha", "sys", "csv_header") == (
        "Id,Parent,ObjectText,Priority,Status,Due,Safety,Link: Satisfies"
    )
    # Module-scoped and module-type-scoped attributes, and the module's own outgoing links only.
    assert render_one(service, "alpha", "stk", "csv_header") == (
        "Id,Parent,ObjectText,Priority,Status,Due,Safety,Stakeholder"
    )
    assert render_one(service, "alpha", "tests", "csv_header") == (
        "Id,Parent,ObjectText,Priority,Status,Due,Safety,Steps,Link: Verifies"
    )


def test_render_csv_header_is_accepted_by_the_import(service, rich_project):
    stk = service.add_requirement("alpha", "stk", {"object_text": "Need", "priority": 1}).id
    header = render_one(service, "alpha", "sys", "csv_header")

    result = service.import_csv(
        "alpha", "sys", header + f"\nN1,,\"Text, with comma\",2,Draft,2026-01-31,true,{stk}\nN2,N1,Child,,,,,\n"
    )

    assert result["imported"] == 2 and result["linked"] == 1
    child = service.get_requirement("alpha", "sys", result["requirement_ids"][1])
    assert child.attributes["priority"] == 3  # the default of an empty cell


def test_render_csv_header_uses_ids_for_reserved_or_ambiguous_names(service, root):
    service.create_project("Old")
    service.create_module("old", "Sys", "SYS")
    path = root / "old" / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    base = {"type": "string", "required": False, "default": None, "choices": None, "system": False}
    data["attributes"] += [
        {**base, "id": "parent_attr", "name": "Parent"},
        {**base, "id": "twin_a", "name": "Twin"},
        {**base, "id": "twin_b", "name": "Twin"},
    ]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    header = render_one(service, "old", "sys", "csv_header")

    assert header == "Id,Parent,ObjectText,parent_attr,twin_a,twin_b"
    result = service.import_csv("old", "sys", header + "\n,,Text,p,a,b\n")
    req = service.get_requirement("old", "sys", result["requirement_ids"][0])
    assert req.attributes == {"object_text": "Text", "parent_attr": "p", "twin_a": "a", "twin_b": "b"}


def test_render_csv_header_quotes_names_with_commas(service):
    service.create_project("P")
    service.create_module("p", "Sys", "SYS")
    service.add_attribute_definition("p", "odd", 'Odd, "name"')
    header = render_one(service, "p", "sys", "csv_header")
    assert header == 'Id,Parent,ObjectText,"Odd, ""name"""'
    assert csv_rows(header)[0] == ["Id", "Parent", "ObjectText", 'Odd, "name"']


def test_render_attribute_guide_lists_types_required_choices_dates_and_defaults(service, rich_project):
    guide = render_one(service, "alpha", "sys", "attribute_guide").splitlines()

    assert guide[0].startswith("- Id: a short key of the row")
    assert guide[1] == "- Parent: empty (top level) or the Id key of another row"
    assert guide[2:] == [
        "- ObjectText: text (several lines allowed; quote the cell); required",
        "- Priority: integer; required; default when empty: 3",
        "- Status: exactly one of the listed values; one of: Draft | Approved",
        "- Due: date written YYYY-MM-DD",
        "- Safety: true or false; default when empty: false",
    ]


def test_render_update_mode_guide_and_rules(service, rich_project):
    created = service.create_prompt(prompt_data("Upd", "{{attribute_guide}}\n---\n{{import_rules}}", import_mode="update"))
    text = service.render_prompt("alpha", created["id"], "sys")["text"]
    guide, rules = text.split("\n---\n")

    assert "the id of an existing requirement to change it" in guide
    assert "or an existing requirement id of this module" in guide
    assert "keep its Id exactly as given" in rules
    assert "an empty cell leaves the value unchanged" in rules
    assert "Do not change the Parent of an existing requirement" in rules
    assert "Every row becomes a new requirement" not in rules


def test_render_import_rules_for_create_and_review_prompts(service, rich_project):
    rules = render_one(service, "alpha", "sys", "import_rules")
    assert rules.startswith("- Answer with exactly one CSV code block")
    assert 'Every row becomes a new requirement of module "Sys"' in rules
    assert "Link columns list ids of existing requirements" in rules
    assert rules.endswith("so check every row against these rules.")
    # No link columns: no link rule.
    assert "Link columns" not in render_one(service, "alpha", "stk", "import_rules")

    review = service.create_prompt(prompt_data("Review", "{{import_rules}}", import_mode="none"))
    assert service.render_prompt("alpha", review["id"], "sys")["text"] == "Answer as a review; do not answer with CSV."


def test_render_link_columns(service, rich_project):
    text = render_one(service, "alpha", "sys", "link_columns")
    assert text.startswith("Link columns (optional, include them in the header only as given):\n")
    assert '"Link: Satisfies" (optional)' in text and 'module "Stk"' in text
    assert render_one(service, "alpha", "stk", "link_columns") == "This module has no link columns."


def test_render_link_columns_of_a_cross_project_configuration(service):
    service.create_project("Base")
    service.create_module("base", "Stake", "STK")
    service.create_project("Alpha")
    service.create_module("alpha", "Sys", "SYS")
    service.set_project_references("alpha", ["base"])
    service.create_link_configuration("alpha", "Traces", "sys", "stake", "base")

    assert render_one(service, "alpha", "sys", "csv_header") == "Id,Parent,ObjectText,Link: Traces"
    text = render_one(service, "alpha", "sys", "link_columns")
    assert 'module "Stake" of project "base"' in text and "base:<id>" in text


@pytest.fixture
def tree(service, rich_project):
    """In ``sys``: SYS-0001 (rich) > SYS-0002 > SYS-0003; SYS-0004 at the root."""
    service.add_requirement("alpha", "sys", {"object_text": rich_paragraph("Top, \"quoted\""), "priority": 1})
    service.add_requirement("alpha", "sys", {"object_text": "Child", "priority": 2, "safety": True}, parent_id="SYS-0001")
    service.add_requirement(
        "alpha", "sys", {"object_text": "Line 1\nLine 2", "priority": 3, "due": "2026-05-01"}, parent_id="SYS-0002"
    )
    service.add_requirement("alpha", "sys", {"object_text": "Other", "priority": 4, "status": "Draft"})
    return "alpha"


def test_render_selected_csv_without_and_with_children(service, tree):
    header = ["Id", "Parent", "ObjectText", "Priority", "Status", "Due", "Safety"]

    text = render_one(service, "alpha", "sys", "selected_csv", requirement_ids=["SYS-0004", "SYS-0001"])
    assert text.endswith("\n")
    assert csv_rows(text) == [
        header,
        ["SYS-0001", "", 'Top, "quoted"', "1", "", "", "false"],
        ["SYS-0004", "", "Other", "4", "Draft", "", "false"],
    ]

    text = render_one(
        service, "alpha", "sys", "selected_csv", requirement_ids=["SYS-0001"], include_children=True
    )
    assert csv_rows(text) == [
        header,
        ["SYS-0001", "", 'Top, "quoted"', "1", "", "", "false"],
        ["SYS-0002", "SYS-0001", "Child", "2", "", "", "true"],
        ["SYS-0003", "SYS-0002", "Line 1\nLine 2", "3", "", "2026-05-01", "false"],
    ]


def test_render_selection_ids_and_module_name(service, tree):
    created = service.create_prompt(prompt_data("Sel", "{{selected_ids}}|{{selection_module_name}}", selection="required"))

    result = service.render_prompt(
        "alpha", created["id"], "stk", requirement_ids=["SYS-0002"], selection_module_id="sys", include_children=True
    )

    assert result["text"] == "SYS-0002, SYS-0003|Sys"
    assert result["selected_ids"] == ["SYS-0002", "SYS-0003"]
    assert result["module_id"] == "stk"


def test_render_without_selection_uses_placeholders_text(service, tree):
    created = service.create_prompt(prompt_data("None sel", "{{selected_ids}}|{{selection_module_name}}|{{selected_csv}}"))
    assert service.render_prompt("alpha", created["id"], "sys")["text"] == "(none)|(none)|(no requirements selected)\n"


def test_render_selection_from_another_project_is_qualified(service, tree):
    service.create_project("Beta")
    service.create_module("beta", "Sys", "SYS")
    service.add_requirement("beta", "sys", {"object_text": "Beta parent"})
    service.add_requirement("beta", "sys", {"object_text": "Beta child"}, parent_id="SYS-0001")

    created = service.create_prompt(prompt_data("Cross", "{{selected_ids}}\n{{selected_csv}}"))
    result = service.render_prompt(
        "alpha", created["id"], "sys",
        requirement_ids=["SYS-0001"], selection_project_id="beta", selection_module_id="sys", include_children=True,
    )

    ids_line, csv_text = result["text"].split("\n", 1)
    assert ids_line == "beta:SYS-0001, beta:SYS-0002"
    assert csv_rows(csv_text) == [
        ["Id", "Parent", "ObjectText"],
        ["beta:SYS-0001", "", "Beta parent"],
        ["beta:SYS-0002", "beta:SYS-0001", "Beta child"],
    ]
    assert result["selected_ids"] == ["beta:SYS-0001", "beta:SYS-0002"]


def test_render_free_text_is_stripped_and_never_rescanned(service, rich_project):
    assert render_one(service, "alpha", "sys", "free_text", free_text="  {{project_name}} & more \n") == (
        "{{project_name}} & more"
    )
    assert render_one(service, "alpha", "sys", "free_text") == "(none)"


def test_render_fills_repeated_and_spaced_placeholders(service, rich_project):
    created = service.create_prompt(prompt_data("Rep", "{{module_prefix}}-{{ module_prefix }}-{{module_prefix}}"))
    assert service.render_prompt("alpha", created["id"], "sys")["text"] == "SYS-SYS-SYS"


def test_render_requires_free_text_and_selection_when_the_prompt_does(service, tree):
    needs_text = service.create_prompt(prompt_data("Needs text", free_text="required", free_text_label="Feature"))
    needs_sel = service.create_prompt(prompt_data("Needs sel", selection="required"))

    with pytest.raises(ValueError, match="needs the Feature"):
        service.render_prompt("alpha", needs_text["id"], "sys", free_text="   ")
    with pytest.raises(ValueError, match="needs selected requirements"):
        service.render_prompt("alpha", needs_sel["id"], "sys", requirement_ids=[])

    assert service.render_prompt("alpha", needs_text["id"], "sys", free_text="x")["text"] == "Module Sys"
    assert service.render_prompt("alpha", needs_sel["id"], "sys", requirement_ids=["SYS-0004"])["text"] == "Module Sys"


@pytest.mark.parametrize(
    "kwargs, error, message",
    [
        ({"requirement_ids": ["SYS-0099"]}, FileNotFoundError, "not found"),
        ({"requirement_ids": "SYS-0001"}, ValueError, "must be a list"),
        ({"requirement_ids": [1]}, ValueError, "must be a list"),
        ({"include_children": "yes"}, ValueError, "include_children"),
        ({"free_text": 5}, ValueError, "free_text must be a string"),
        ({"free_text": "x" * (64 * 1024 + 1)}, ValueError, "too long"),
        ({"selection_module_id": "missing"}, FileNotFoundError, ""),
        ({"selection_project_id": "missing"}, FileNotFoundError, ""),
        ({"selection_project_id": 5}, ValueError, "selection_project_id"),
    ],
)
def test_render_rejects_invalid_arguments(service, tree, kwargs, error, message):
    created = service.create_prompt(prompt_data("Args"))
    with pytest.raises(error, match=message):
        service.render_prompt("alpha", created["id"], "sys", **kwargs)


def test_render_missing_project_module_or_prompt(service, rich_project):
    created = service.create_prompt(prompt_data())
    with pytest.raises(FileNotFoundError):
        service.render_prompt("missing", created["id"], "sys")
    with pytest.raises(FileNotFoundError):
        service.render_prompt("alpha", created["id"], "missing")
    with pytest.raises(FileNotFoundError):
        service.render_prompt("alpha", "user:missing", "sys")
    with pytest.raises(ValueError):
        service.render_prompt("alpha", "missing", "sys")


def test_render_never_writes(service, root, tree):
    created = service.create_prompt(prompt_data("All", "\n".join("{{" + p + "}}" for p in PLACEHOLDERS)))
    service.lock_project("alpha", "Frozen")
    before = tree_bytes(root)
    service.take_touched_projects()

    result = service.render_prompt(
        "alpha", created["id"], "sys", requirement_ids=["SYS-0001"], include_children=True, free_text="text"
    )

    assert "{{" not in result["text"]
    assert tree_bytes(root) == before
    assert service.take_touched_projects() == set()


# --- REST / WebSocket ---------------------------------------------------------------------


def _setup_project(client):
    client.post("/projects", json={"name": "Alpha", "description": "Desc"})
    client.post("/projects/alpha/modules", json={"name": "Sys", "prefix": "SYS"})
    client.post("/projects/alpha/modules/sys/requirements", json={"attributes": {"object_text": "First"}})
    client.post(
        "/projects/alpha/modules/sys/requirements",
        json={"attributes": {"object_text": "Second"}, "parent_id": "SYS-0001"},
    )


def test_rest_prompt_crud_status_codes_and_payloads(client, tmp_path):
    listed = client.get("/prompts")
    assert listed.status_code == 200
    assert all(p["id"].startswith("builtin:") for p in listed.json())

    resp = client.post("/prompts", json=prompt_data("Rest prompt", description="d"))
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert created["id"] == "user:rest-prompt" and created["error"] is None
    assert (tmp_path / "_templates" / "prompts" / "rest-prompt.json").is_file()

    assert client.get("/prompts/user:rest-prompt").json() == created
    assert client.post("/prompts", json=prompt_data("REST PROMPT")).status_code == 409
    assert client.post("/prompts", json=prompt_data("Bad", body="{{x}}")).status_code == 400
    assert client.post("/prompts", json={"name": "No body"}).status_code == 400

    resp = client.put("/prompts/user:rest-prompt", json=prompt_data("Renamed", body="{{csv_header}}"))
    assert resp.status_code == 200 and resp.json()["id"] == "user:rest-prompt"
    assert client.put("/prompts/user:missing", json=prompt_data("X")).status_code == 404

    builtin_id = listed.json()[0]["id"]
    assert client.put(f"/prompts/{builtin_id}", json=prompt_data("X")).status_code == 400
    assert client.delete(f"/prompts/{builtin_id}").status_code == 400
    assert client.get("/prompts/user:missing").status_code == 404
    assert client.get("/prompts/nonsense").status_code == 400

    resp = client.get("/prompts/user:rest-prompt/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert 'filename="rest-prompt.prompt.json"' in resp.headers["content-disposition"]
    assert resp.text == (tmp_path / "_templates" / "prompts" / "rest-prompt.json").read_text(encoding="utf-8")
    exported = resp.text

    assert client.delete("/prompts/user:rest-prompt").status_code == 204
    assert client.delete("/prompts/user:rest-prompt").status_code == 404

    resp = client.post("/prompts/import", content=exported.encode("utf-8"), headers={"Content-Type": "application/json"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed"
    assert client.post(
        "/prompts/import", content=exported.encode("utf-8"), headers={"Content-Type": "application/json"}
    ).status_code == 409
    assert client.post("/prompts/import", content=b"{", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post(
        "/prompts/import", content=exported.encode("utf-8"), headers={"Content-Type": "text/plain"}
    ).status_code == 415
    assert client.post(
        "/prompts/import", content='{"name": "Zażółć", "body": "x"}'.encode("cp1250"),
        headers={"Content-Type": "application/json"},
    ).status_code == 400


def test_rest_prompt_mutations_broadcast_prompts_changed_and_failures_do_not(client):
    exported = json.dumps(prompt_data("Imported"))
    with client.websocket_connect("/ws") as ws:
        assert client.post("/prompts", json=prompt_data("One")).status_code == 200
        assert json.loads(ws.receive_text()) == {"event": "prompts_changed"}
        assert client.put("/prompts/user:one", json=prompt_data("One", body="b")).status_code == 200
        assert json.loads(ws.receive_text()) == {"event": "prompts_changed"}
        assert client.post("/prompts/import", content=exported, headers={"Content-Type": "application/json"}).status_code == 200
        assert json.loads(ws.receive_text()) == {"event": "prompts_changed"}
        assert client.delete("/prompts/user:imported").status_code == 204
        assert json.loads(ws.receive_text()) == {"event": "prompts_changed"}

        # Failures, reads and exports broadcast nothing.
        assert client.post("/prompts", json=prompt_data("ONE")).status_code == 409
        assert client.post("/prompts", json=prompt_data(body="{{bad}}")).status_code == 400
        assert client.delete("/prompts/user:missing").status_code == 404
        assert client.get("/prompts").status_code == 200
        assert client.get("/prompts/user:one/export").status_code == 200

        other = client.post("/projects", json={"name": "Sentinel"}).json()["id"]
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": other}


def test_rest_render_returns_text_and_does_not_broadcast(client):
    _setup_project(client)
    prompt_id = client.post("/prompts", json=prompt_data("R", "{{project_name}}|{{selected_ids}}|{{csv_header}}")).json()["id"]

    with client.websocket_connect("/ws") as ws:
        resp = client.post(
            "/projects/alpha/prompts/render",
            json={"prompt_id": prompt_id, "module_id": "sys", "requirement_ids": ["SYS-0001"], "include_children": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["text"] == "Alpha|SYS-0001, SYS-0002|Id,Parent,ObjectText"
        assert resp.json()["selected_ids"] == ["SYS-0001", "SYS-0002"]

        client.post("/projects", json={"name": "Sentinel"})
        assert json.loads(ws.receive_text()) == {"event": "changed", "project_id": "sentinel"}


@pytest.mark.parametrize(
    "body, status",
    [
        ({"prompt_id": "user:missing", "module_id": "sys"}, 404),
        ({"prompt_id": "builtin:draft-requirements", "module_id": "sys"}, 400),  # free text required
        ({"prompt_id": "nonsense", "module_id": "sys"}, 400),
        ({"prompt_id": "builtin:review-ambiguities", "module_id": "missing"}, 404),
        ({"prompt_id": "builtin:review-ambiguities", "module_id": "sys", "requirement_ids": ["SYS-9"]}, 404),
        ({"prompt_id": "builtin:review-ambiguities", "module_id": "sys", "include_children": "x",
          "requirement_ids": ["SYS-0001"]}, 400),
        ({"module_id": "sys"}, 422),
    ],
)
def test_rest_render_error_codes(client, body, status):
    _setup_project(client)
    assert client.post("/projects/alpha/prompts/render", json=body).status_code == status
    assert client.post(
        "/projects/missing/prompts/render", json={"prompt_id": "builtin:review-ambiguities", "module_id": "sys"}
    ).status_code == 404


def test_ws_prompt_crud_excludes_the_sender_and_matches_rest(client, tmp_path):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        response = rpc(ws1, "prompt.create", {"prompt": prompt_data("Ws prompt")}, 1)
        assert response["result"]["id"] == "user:ws-prompt"
        assert json.loads(ws2.receive_text()) == {"event": "prompts_changed"}
        assert client.get("/prompts/user:ws-prompt").json() == response["result"]

        response = rpc(ws1, "prompt.update", {"prompt_id": "user:ws-prompt", "prompt": prompt_data("Ws prompt", body="b")}, 2)
        assert response["result"]["body"] == "b"
        assert json.loads(ws2.receive_text()) == {"event": "prompts_changed"}

        exported = rpc(ws1, "prompt.export", {"prompt_id": "user:ws-prompt"}, 3)["result"]
        assert exported == client.get("/prompts/user:ws-prompt/export").text

        assert rpc(ws1, "prompt.delete", {"prompt_id": "user:ws-prompt"}, 4)["result"] is None
        assert json.loads(ws2.receive_text()) == {"event": "prompts_changed"}

        response = rpc(ws1, "prompt.import", {"text": exported}, 5)
        assert response["result"]["id"] == "user:ws-prompt"
        assert json.loads(ws2.receive_text()) == {"event": "prompts_changed"}

        assert rpc(ws1, "prompt.list", {}, 6)["result"] == client.get("/prompts").json()
        assert rpc(ws1, "prompt.get", {"prompt_id": "user:ws-prompt"}, 7)["result"]["name"] == "Ws prompt"

        # Errors: conflict and invalid are -32602, missing is -32001; no broadcast.
        assert rpc(ws1, "prompt.create", {"prompt": prompt_data("WS PROMPT")}, 8)["error"]["code"] == -32602
        assert rpc(ws1, "prompt.create", {"prompt": prompt_data(body="{{x}}")}, 9)["error"]["code"] == -32602
        assert rpc(ws1, "prompt.create", {}, 10)["error"]["code"] == -32602
        assert rpc(ws1, "prompt.delete", {"prompt_id": "user:missing"}, 11)["error"]["code"] == -32001
        assert rpc(ws1, "prompt.get", {"prompt_id": "user:missing"}, 12)["error"]["code"] == -32001
        builtin_id = client.get("/prompts").json()[0]["id"]
        assert rpc(ws1, "prompt.delete", {"prompt_id": builtin_id}, 13)["error"]["code"] == -32602
        assert rpc(ws1, "prompt.update", {"prompt_id": builtin_id, "prompt": prompt_data("Z")}, 14)["error"]["code"] == -32602

        # The sender saw none of its own events: its next message is this REST broadcast.
        client.post("/prompts", json=prompt_data("From rest"))
        assert json.loads(ws1.receive_text()) == {"event": "prompts_changed"}
        assert json.loads(ws2.receive_text()) == {"event": "prompts_changed"}


def test_ws_render_matches_rest_and_does_not_broadcast(client):
    _setup_project(client)
    client.post("/projects", json={"name": "Beta"})
    client.post("/projects/beta/modules", json={"name": "Other", "prefix": "OTH"})
    client.post("/projects/beta/modules/other/requirements", json={"attributes": {"object_text": "B1"}})
    body = {
        "prompt_id": "builtin:review-ambiguities",
        "module_id": "sys",
        "requirement_ids": ["SYS-0001"],
        "selection_project_id": "beta",
        "selection_module_id": "other",
        "include_children": False,
        "free_text": "focus",
    }
    body["requirement_ids"] = ["OTH-0001"]
    rest = client.post("/projects/alpha/prompts/render", json=body)
    assert rest.status_code == 200, rest.text
    assert rest.json()["selected_ids"] == ["beta:OTH-0001"]

    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        ws_result = rpc(ws1, "prompt.render", {"project_id": "alpha", **body}, 1)["result"]
        assert ws_result == rest.json()

        error = rpc(ws1, "prompt.render", {"project_id": "alpha", **body, "requirement_ids": ["OTH-9"]}, 2)["error"]
        assert error["code"] == -32001
        error = rpc(ws1, "prompt.render", {"project_id": "alpha", **body, "free_text": 3}, 3)["error"]
        assert error["code"] == -32602

        client.post("/projects", json={"name": "Sentinel"})
        assert json.loads(ws2.receive_text()) == {"event": "changed", "project_id": "sentinel"}
