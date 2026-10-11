"""Rich text edge cases: write paths (move, templates, baselines, commit messages),
required-attribute blankness, sanitiser corner cases and the plain round-trip.

Git tests work in pytest's ``tmp_path`` with their own repositories;
``conftest.py`` isolates git from the user's configuration.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reqman.git_repo import GitHistory, GitSettings
from reqman.formats import PROJECT_FORMAT
from reqman.history import format_value
from reqman.richtext import RICH_TEXT_PREFIX as P, from_plain, is_rich, sanitize, to_plain
from reqman.service import ProjectService
from reqman.store import ProjectStore

END = "</div>"
CHECKOUT = Path(__file__).resolve().parents[2]

DIRTY = P + '<p><b onclick="x">Bold</b> <span style="color:#F00;position:fixed">red</span><script>alert(1)</script></p>' + END
CLEAN = P + '<p><strong>Bold</strong> <span style="color: #ff0000">red</span></p>' + END


def rich(body: str) -> str:
    return P + body + END


def _no_markup(text: str) -> None:
    for bad in ("<p>", "<strong>", "<span", "xmlns", "</", "&lt;", "&amp;", "script", "onclick"):
        assert bad not in text, (bad, text)


# --- fixtures ------------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path):
    return ProjectService(ProjectStore(tmp_path / "data"))


@pytest.fixture
def module(service):
    project = service.create_project("P")
    service.add_attribute_definition(project.id, "note", "Note", "text")
    return project.id, service.create_module(project.id, "M", "M").id


def _requirement_path(service, project_id, module_id, req_id) -> Path:
    return service.store.root / project_id / "modules" / module_id / "requirements" / f"{req_id}.json"


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
def git_root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=data, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    return data


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def git_service(git_root, clock):
    return ProjectService(ProjectStore(git_root), history=GitHistory(GitSettings(enabled=True), clock=clock))


# --- 1. write paths ----------------------------------------------------------------------------


def test_move_keeps_rich_value_and_file_format(service, module):
    project_id, module_id = module
    parent = service.add_requirement(project_id, module_id, {"object_text": "Parent"})
    child = service.add_requirement(project_id, module_id, {"object_text": DIRTY, "note": rich("<ol><li>a</li></ol>")})
    moved = service.move_requirement(project_id, module_id, child.id, parent.id, 0)
    assert moved.attributes["object_text"] == CLEAN
    assert moved.parent_id == parent.id

    fresh = ProjectService(ProjectStore(service.store.root))
    reloaded = fresh.get_requirement(project_id, module_id, child.id)
    assert reloaded.attributes["object_text"] == CLEAN
    assert reloaded.attributes["note"] == rich("<ol><li><p>a</p></li></ol>")
    text = _requirement_path(service, project_id, module_id, child.id).read_text(encoding="utf-8")
    assert text.endswith("}\n") and text.startswith('{\n  "')
    assert json.loads(text)["attributes"]["object_text"] == CLEAN


def test_template_creation_sanitises_rich_defaults(service):
    template = {
        "format_version": 1,
        "id": "rich",
        "name": "Rich",
        "description": "",
        "attributes": [{"id": "note", "name": "Note", "type": "text", "default": DIRTY}],
        "modules": [{"key": "a", "name": "Alpha", "prefix": "A"}],
        "link_configurations": [],
        "views": [],
    }
    summary = service.import_template(json.dumps(template, indent=2) + "\n")
    assert summary["error"] is None
    project = service.create_project("From rich", template_id=summary["id"])
    note = next(a for a in project.attributes if a.id == "note")
    assert note.default == CLEAN

    # The default is what a new requirement gets, and it persists.
    module_id = service.list_modules(project.id)[0].id
    req = service.add_requirement(project.id, module_id, {"object_text": "x"})
    assert req.attributes["note"] == CLEAN
    fresh = ProjectService(ProjectStore(service.store.root))
    assert next(a for a in fresh.store.load_project(project.id).attributes if a.id == "note").default == CLEAN
    assert fresh.get_requirement(project.id, module_id, req.id).attributes["note"] == CLEAN


def test_save_as_template_round_trips_rich_default(service):
    project = service.create_project("Src")
    service.add_attribute_definition(project.id, "note", "Note", "text", default=DIRTY)
    service.create_module(project.id, "M", "M")
    summary = service.save_project_as_template(project.id, "Rich copy")
    path = service.store.root / "_templates" / f"{summary['id'].split(':', 1)[1]}.json"
    text = path.read_text(encoding="utf-8")
    assert text.endswith("}\n") and text.startswith('{\n  "format_version"')
    saved = next(a for a in json.loads(text)["attributes"] if a["id"] == "note")
    assert saved["default"] == CLEAN

    copy = service.create_project("Dst", template_id=summary["id"])
    assert next(a for a in copy.attributes if a.id == "note").default == CLEAN


def test_baseline_and_compare_carry_sanitised_rich_values(git_service, git_root, clock):
    service = git_service
    project = service.create_project("Alpha")
    module = service.create_module(project.id, "System", "SYRS")
    v1_value = rich("<p><em>First</em> 😀 &amp; &lt;x&gt;</p>")
    req = service.add_requirement(project.id, module.id, {"object_text": v1_value})
    service.create_baseline(project.id, "v1")

    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"object_text": DIRTY})
    service.create_baseline(project.id, "v2")

    at_v1 = service.baseline_requirements(project.id, "v1", module.id)
    assert at_v1[0].attributes["object_text"] == sanitize(v1_value) == v1_value
    assert to_plain(at_v1[0].attributes["object_text"]) == "First 😀 & <x>"

    for to_name in ("v2", "current"):
        result = service.compare_baselines(project.id, "v1", to_name)
        entry = next(m for m in result["modules"] if m["module_id"] == module.id)
        diffs = {d["attribute_id"]: d for m in entry["modified"] if m["id"] == req.id for d in m["diffs"]}
        # Diffs carry the stored (sanitised) values; the client renders or projects them.
        assert diffs["object_text"]["old"] == v1_value
        assert diffs["object_text"]["new"] == CLEAN
        assert "script" not in json.dumps(result)


# --- 2. required text attribute ------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", ["<p><strong> </strong></p>", "<p><br/></p>", "<p><strong>&nbsp;</strong></p>", "<p></p><p><em>\t</em></p>"]
)
def test_whitespace_only_formatting_is_blank_for_required_text(service, module, body):
    project_id, module_id = module
    with pytest.raises(ValueError, match="required attribute 'ObjectText' is empty"):
        service.add_requirement(project_id, module_id, {"object_text": rich(body)})
    req = service.add_requirement(project_id, module_id, {"object_text": "ok"})
    with pytest.raises(ValueError, match="required attribute 'ObjectText' is empty"):
        service.update_requirement(project_id, module_id, req.id, {"object_text": rich(body)})
    assert service.get_requirement(project_id, module_id, req.id).attributes["object_text"] == "ok"


def test_whitespace_only_formatting_in_optional_text_is_stored_empty(service, module):
    project_id, module_id = module
    req = service.add_requirement(project_id, module_id, {"object_text": "x", "note": rich("<p><b> </b><br/></p>")})
    assert req.attributes["note"] == ""


# --- 3. sanitiser edge cases ---------------------------------------------------------------------


def test_unicode_and_entities_round_trip():
    value = rich("<p><strong>&amp; &lt;tag&gt; &quot;q&quot; a&nbsp;b &#x1F600; 😀 zażółć 漢字</strong></p>")
    once = sanitize(value)
    assert once == rich("<p><strong>&amp; &lt;tag&gt; \"q\" a b 😀 😀 zażółć 漢字</strong></p>")
    assert sanitize(once) == once
    assert to_plain(once) == '& <tag> "q" a b 😀 😀 zażółć 漢字'


def test_unicode_rich_value_persists_through_fresh_store(service, module):
    project_id, module_id = module
    value = rich("<p><u>😀 &amp; 漢字 &lt;/div&gt;</u></p>")
    req = service.add_requirement(project_id, module_id, {"object_text": value})
    fresh = ProjectService(ProjectStore(service.store.root))
    assert fresh.get_requirement(project_id, module_id, req.id).attributes["object_text"] == value


@pytest.mark.parametrize(
    "style, expected",
    [
        ("color:#ff0000;background:url(x)", "color: #ff0000"),
        ("font-size:20px;position:fixed", "font-size: 20px"),
        ("COLOR:#FF0000", "color: #ff0000"),
        ("Font-Size:24PX", "font-size: 24px"),
        ("color:#ff0000 !important", None),
        ("color:#ff0000;font-size:20px !important", "color: #ff0000"),
        ("color:#ff0000&quot;;onmouseover=&quot;alert(1)", None),
        ("color: rgb(256, 0, 0)", None),
        ("color:#ff0000;color:#00ff00", "color: #00ff00"),
        ("color:red", None),
        ("font-size:calc(20px)", None),
        ("color:#ff0000; behavior:url(x.htc); -moz-binding:url(y)", "color: #ff0000"),
    ],
)
def test_style_values_are_allow_listed(style, expected):
    value = rich(f'<p><span style="{style}">a</span><strong>b</strong></p>')
    result = sanitize(value)
    if expected is None:
        assert result == rich("<p>a<strong>b</strong></p>")
    else:
        assert result == rich(f'<p><span style="{expected}">a</span><strong>b</strong></p>')
    for bad in ("url", "position", "important", "onmouseover", "behavior", "binding", "calc"):
        assert bad not in result


def test_quote_breakout_in_style_attribute_is_stripped():
    result = sanitize(rich("<p><span style='color:#ff0000\" onclick=\"x'>a</span><em>e</em></p>"))
    assert result == rich("<p>a<em>e</em></p>")


def test_mixed_case_tags_are_normalised():
    assert sanitize(rich("<P><STRONG>a</STRONG><Em>b</eM><BR><SpAn STYLE='color:#000'>c</sPaN></P>")) == rich(
        '<p><strong>a</strong><em>b</em><br/><span style="color: #000000">c</span></p>'
    )
    assert sanitize(rich("<UL><LI>x</LI></UL>")) == rich("<ul><li><p>x</p></li></ul>")


def test_unclosed_and_stray_closing_tags():
    assert sanitize(rich("<p><strong>unclosed")) == rich("<p><strong>unclosed</strong></p>")
    stray = sanitize(rich("</strong></p></ul></li>text<p>x</p></em></span>"))
    # As in HTML, a stray </p> is an empty paragraph; the other stray end tags are ignored.
    assert stray == "\ntext\nx"
    # A premature </div> does not end the value: later markup is still sanitised.
    assert sanitize(rich("<p><em>a</em></p></div><script>x</script><p>b</p>")) == rich("<p><em>a</em></p><p>b</p>")


def test_unclosed_list_items_stay_items():
    # HTML (and the frontend's DOMParser mirror in richText.ts) closes an open
    # <li> at the next <li>: two items, not one item "ab".
    result = sanitize(rich("<ul><li>a<li>b"))
    assert sanitize(result) == result
    assert result == rich("<ul><li><p>a</p></li><li><p>b</p></li></ul>")
    assert to_plain(result) == "- a\n- b"


def test_cdata_and_processing_instructions_are_removed():
    assert sanitize(rich("<p>a<![CDATA[<script>x</script>]]>b<?php echo 1 ?>c</p>")) == "abc"
    assert sanitize(rich('<?xml version="1.0"?><!DOCTYPE html><p><strong>a</strong>b</p>')) == rich(
        "<p><strong>a</strong>b</p>"
    )


def test_one_megabyte_input_sanitises_quickly():
    big = rich("<p><strong>" + "x&amp;y <i>z</i> " * 70000 + "</strong></p>")
    assert len(big) > 1_000_000
    start = time.perf_counter()
    once = sanitize(big)
    assert sanitize(once) == once
    plain = to_plain(once)
    assert time.perf_counter() - start < 20  # loose bound: well under a second locally
    assert plain.count("x&y z") == 70000


def test_many_paragraphs_and_deep_empty_marks_are_fast():
    start = time.perf_counter()
    assert sanitize(rich("<b>" * 100_000)) == ""
    wide = sanitize(rich("<p><strong>a</strong><em>b</em></p>" * 30_000))
    assert wide.count("<p>") == 30_000
    assert time.perf_counter() - start < 20


def test_reopening_many_implicitly_closed_marks_stays_linear():
    # Hundreds of formatting elements, differing only in dropped attributes,
    # closed implicitly by every following <p>: reopening them all in each
    # paragraph would grow the output (and the time) quadratically.
    opened = "".join(f"<b a={i}>" for i in range(200))
    big = rich("<p>" + opened + "<p>x" * 50_000)
    assert len(big) > 200_000
    start = time.perf_counter()
    once = sanitize(big)
    elapsed = time.perf_counter() - start
    # CI runners vary; 5 s is still far below quadratic (which would be 20+ s)
    # but gives slow runners headroom without masking real regressions.
    assert elapsed < 5
    assert len(once) <= 5 * len(big)
    # The first paragraph holds only the (empty) opened marks.
    assert to_plain(once).split("\n") == [""] + ["x"] * 50_000
    assert sanitize(once) == once
    # Distinct mark kinds are capped too.
    mixed = rich("<p>" + "<b><i><u><s><a><font><code><tt>" * 30 + "<p>x" * 50_000)
    start = time.perf_counter()
    once = sanitize(mixed)
    assert time.perf_counter() - start < 5
    assert len(once) <= 5 * len(mixed)
    assert sanitize(once) == once


def test_li_outside_a_list_is_unwrapped():
    assert sanitize(rich("<li><strong>x</strong></li><li>y</li>")) == rich("<p><strong>x</strong>y</p>")
    assert sanitize(rich("<p>a</p><li>b</li>")) == "a\nb"


# --- 4. from_plain / to_plain round-trip --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "one\ntwo\n\nthree",
        "a < b && c > d\n<p>x</p>",
        "",
        "😀 & 漢字",
        P,
        P + "\n",
        P + "<p>x</p>\nsecond\n\n" + END,
        P + "a &amp; b\n&lt;",
    ],
)
def test_from_plain_round_trips_through_to_plain_and_sanitize(text):
    stored = from_plain(text)
    assert sanitize(stored) == stored
    assert to_plain(stored) == text
    assert is_rich(stored) == text.startswith(P)


def test_from_plain_normalises_line_endings_only_for_prefixed_text():
    assert from_plain("a\r\nb\tc") == "a\r\nb\tc"  # plain text is untouched
    stored = from_plain(P + "a\r\nb\tc\rd")
    assert to_plain(stored) == P + "a\nb\tc\nd"  # tabs are kept, like spaces


def test_multiline_plain_is_unchanged_by_sanitize_and_to_plain():
    text = "line 1\nline 2\n\n  indented\n"
    assert sanitize(text) == text and to_plain(text) == text


# --- 5. commit messages --------------------------------------------------------------------------


def test_commit_messages_contain_no_markup(git_service, git_root, clock):
    service = git_service
    project = service.create_project("Alpha")
    service.add_attribute_definition(project.id, "note", "Note", "text")
    module = service.create_module(project.id, "System", "SYRS")
    req = service.add_requirement(project.id, module.id, {"object_text": DIRTY})
    clock.offset = 3600
    service.update_requirement(
        project.id, module.id, req.id,
        {"object_text": rich("<ul><li><em>Item</em> &amp; more</li></ul>"), "note": rich("<p><u>n</u></p>")},
    )
    clock.offset = 7200
    service.update_requirement(project.id, module.id, req.id, {"object_text": "plain again"})

    log = git(git_root / project.id, "log", "--format=%B")
    _no_markup(log)
    assert "Item & more" in log
    assert "plain again" in log


def test_format_value_projects_rich_values():
    assert format_value(rich("<p><strong>a</strong></p><p>b</p>")) == "a b"
    assert format_value(rich("<p></p>")) == "(formatted text)"
    long = format_value(rich("<p><em>" + "word " * 30 + "</em></p>"))
    assert len(long) <= 40 and long.endswith("…")
    _no_markup(long)


# --- 6. project format follows stored rich text --------------------------------------------------


def _as_v3(data: dict) -> dict:
    """``data`` (a current-format project.json) as a genuine version 3 file would hold it.

    Version 7 turned ``module_order`` id strings into ``{"module": id}``
    entries; keys added after version 3 (``locked``, ``references``...) are
    absent from a version 3 file.
    """
    v3 = {k: v for k, v in data.items() if k not in ("locked", "references")}
    v3["format_version"] = 3
    if "module_order" in v3:
        v3["module_order"] = [e["module"] for e in v3["module_order"] if "module" in e]
    return v3


def _downgrade_to_v3(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(_as_v3(data), indent=2) + "\n", encoding="utf-8")


def _set_project_version(service, project_id, version):
    assert version == 3, "only a version 3 downgrade is modelled"
    path = service.store.root / project_id / "project.json"
    _downgrade_to_v3(path)
    return path


def _project_version(path: Path) -> int:
    return json.loads(path.read_text(encoding="utf-8"))["format_version"]


def test_rich_update_rewrites_a_v3_project_as_current_format(service, module):
    project_id, module_id = module
    req = service.add_requirement(project_id, module_id, {"object_text": "plain"})
    path = service.store.root / project_id / "project.json"
    current = json.loads(path.read_text(encoding="utf-8"))
    _set_project_version(service, project_id, 3)

    # A plain update needs no newer format: project.json is left untouched.
    before = path.read_text(encoding="utf-8")
    service.update_requirement(project_id, module_id, req.id, {"object_text": "still  plain\tvalue"})
    assert path.read_text(encoding="utf-8") == before

    service.update_requirement(project_id, module_id, req.id, {"note": rich("<p><b>x</b></p>")})
    text = path.read_text(encoding="utf-8")
    version = PROJECT_FORMAT.current
    assert _project_version(path) == version
    assert text.endswith("}\n") and text.startswith('{\n  "format_version": %d,\n  "id": ' % version)
    # Rewritten through the migrations: the same project, at the current format, two-space indented.
    assert json.loads(text) == current
    assert text == json.dumps(current, indent=2, ensure_ascii=False) + "\n"


def test_rich_update_rewrites_the_project_in_the_same_commit(git_service, clock):
    service = git_service
    project = service.create_project("Alpha")
    module = service.create_module(project.id, "System", "SYRS")
    req = service.add_requirement(project.id, module.id, {"object_text": "plain"})
    path = _set_project_version(service, project.id, 3)
    git(service.store.root / project.id, "add", "project.json")
    git(service.store.root / project.id, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "downgrade")
    clock.offset = 3600

    service.update_requirement(project.id, module.id, req.id, {"object_text": rich("<p><em>x</em></p>")})
    assert _project_version(path) == PROJECT_FORMAT.current
    changed = git(service.store.root / project.id, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(changed) == sorted(["project.json", f"modules/{module.id}/requirements/{req.id}.json"])
    assert git(service.store.root / project.id, "status", "--porcelain") == ""


@pytest.fixture
def client(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_rich_update_over_rest_and_websocket_rewrites_a_v3_project(client, tmp_path):
    project_id = client.post("/projects", json={"name": "P"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "M", "prefix": "M"}).json()["id"]
    base = f"/projects/{project_id}/modules/{module_id}/requirements"
    rest_id = client.post(base, json={"attributes": {"object_text": "a"}}).json()["id"]
    ws_id = client.post(base, json={"attributes": {"object_text": "b"}}).json()["id"]
    path = tmp_path / "data" / project_id / "project.json"

    def downgrade():
        _downgrade_to_v3(path)
        assert _project_version(path) == 3

    downgrade()
    resp = client.put(f"{base}/{rest_id}", json={"attributes": {"object_text": rich("<p><u>r</u></p>")}})
    assert resp.status_code == 200, resp.text
    assert _project_version(path) == PROJECT_FORMAT.current

    downgrade()
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "requirement.update",
            "params": {"project_id": project_id, "module_id": module_id, "id": ws_id,
                       "attributes": {"object_text": rich("<p><s>w</s></p>")}},
        }))
        reply = json.loads(ws.receive_text())
        while "id" not in reply:
            reply = json.loads(ws.receive_text())
        assert reply["result"]["attributes"]["object_text"] == rich("<p><s>w</s></p>")
    assert _project_version(path) == PROJECT_FORMAT.current


# --- 7. plain values keep their exact whitespace -------------------------------------------------


@pytest.mark.parametrize("text", ["  indented", "two  spaces", "tab\there", "\tlead\n\n  trail  \n", " "])
def test_plain_values_are_stored_with_their_exact_whitespace(service, module, text):
    project_id, module_id = module
    req = service.add_requirement(project_id, module_id, {"object_text": "x", "note": text})
    assert service.get_requirement(project_id, module_id, req.id).attributes["note"] == text
    service.update_requirement(project_id, module_id, req.id, {"note": text, "object_text": "y" + text})
    stored = service.get_requirement(project_id, module_id, req.id).attributes
    assert stored["note"] == text and stored["object_text"] == "y" + text


@pytest.mark.parametrize("text", ["  indented", "two  spaces", "tab\there", "\tlead\n\n  trail  ", "a\n \nb"])
def test_unformatted_editor_html_demotes_to_the_exact_plain_text(text):
    # The editor (RichTextField, preserveWhitespace 'full') sends a plain
    # value back as one <p> per line with its spaces and tabs.
    html = "".join(f"<p>{line}</p>" for line in text.split("\n"))
    assert sanitize(rich(html)) == text


# --- 8. defaults -----------------------------------------------------------------------------------


def test_a_hand_edited_dirty_text_default_is_sanitised_on_create_and_import(service, module):
    project_id, module_id = module
    path = service.store.root / project_id / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    note = next(a for a in data["attributes"] if a["id"] == "note")
    note["default"] = DIRTY
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    req = service.add_requirement(project_id, module_id, {"object_text": "x"})
    assert req.attributes["note"] == CLEAN
    assert service.get_requirement(project_id, module_id, req.id).attributes["note"] == CLEAN

    result = service.import_csv(project_id, module_id, "ObjectText\nimported\n")
    imported = service.get_requirement(project_id, module_id, result["requirement_ids"][0])
    assert imported.attributes["note"] == CLEAN
