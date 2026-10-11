"""Pure-function tests for ``history.py``: commit message entries, commit
messages, the session trailer and a requirement's change list and blame.

No git and no file system: every input is raw JSON bytes, as ``git_repo.py``
hands them over.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reqman.history import (
    MAX_VALUE_LENGTH,
    FileChange,
    Revision,
    commit_message,
    describe_changes,
    format_value,
    is_checkout_subject,
    requirement_history,
    session_of,
)

REQ_PATH = "modules/sys/requirements/{}.json"


def raw(value) -> bytes:
    return json.dumps(value, indent=2).encode("utf-8")


def req(req_id: str, attributes: dict | None = None, parent_id=None, order=0) -> dict:
    return {"id": req_id, "attributes": attributes or {}, "parent_id": parent_id, "order": order}


def req_change(req_id: str, old: dict | bytes | None, new: dict | bytes | None) -> FileChange:
    def encode(value):
        return value if value is None or isinstance(value, bytes) else raw(value)

    return FileChange(REQ_PATH.format(req_id), encode(old), encode(new))


PROJECT = {
    "id": "alpha",
    "name": "Alpha",
    "attributes": [
        {"id": "object_text", "name": "Text"},
        {"id": "status", "name": "Status"},
        {"id": "satisfies", "name": "Satisfies", "system": True},
    ],
    "link_configurations": [{"id": "satisfies", "name": "Satisfies"}],
}


# --- format_value ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "(empty)"),
        ("", "(empty)"),
        (True, "true"),
        (False, "false"),
        (3, "3"),
        (["a", "ü"], '["a", "ü"]'),
        ("line one\n   line  two\t", "line one line two"),
        ("a" * MAX_VALUE_LENGTH, "a" * MAX_VALUE_LENGTH),
    ],
)
def test_format_value_renders_short_single_line_values(value, expected):
    assert format_value(value) == expected


def test_format_value_cuts_long_values_to_40_characters_with_an_ellipsis():
    cut = format_value("x" * 100)
    assert len(cut) == MAX_VALUE_LENGTH == 40
    assert cut == "x" * 39 + "…"
    # Trailing whitespace before the cut is dropped, not kept before the ellipsis.
    assert format_value("y" * 38 + "   " + "z" * 20) == "y" * 38 + "…"


def test_long_attribute_values_are_cut_in_requirement_entries():
    old, new = "o" * 60, "n" * 60
    entries = describe_changes(
        [req_change("SYRS-0001", req("SYRS-0001", {"object_text": old}), req("SYRS-0001", {"object_text": new}))],
        PROJECT,
    )
    assert entries == [f"SYRS-0001: Text {'o' * 39}… → {'n' * 39}…"]


# --- requirements -----------------------------------------------------------------


def test_requirement_parent_change_and_reorder():
    before = req("SYRS-0002", {"status": "Draft"}, parent_id=None, order=1)
    moved = req("SYRS-0002", {"status": "Draft"}, parent_id="SYRS-0001", order=0)
    reordered = req("SYRS-0002", {"status": "Draft"}, parent_id=None, order=0)
    assert describe_changes([req_change("SYRS-0002", before, moved)], PROJECT) == ["SYRS-0002: moved under SYRS-0001"]
    assert describe_changes([req_change("SYRS-0002", moved, before)], PROJECT) == ["SYRS-0002: moved to top level"]
    # Only the order changed: "reordered"; a parent change already implies the new position.
    assert describe_changes([req_change("SYRS-0002", before, reordered)], PROJECT) == ["SYRS-0002: reordered"]


def test_requirement_attribute_and_move_in_project_attribute_order_then_unknown_sorted():
    old = req("SYRS-0001", {"status": "Draft", "object_text": "A", "zeta": 1, "alpha": None})
    new = req("SYRS-0001", {"status": "Reviewed", "object_text": "B", "zeta": 2, "alpha": "x"}, parent_id="SYRS-0009")
    entries = describe_changes([req_change("SYRS-0001", old, new)], PROJECT)
    assert entries == [
        "SYRS-0001: Text A → B, Status Draft → Reviewed, alpha (empty) → x, zeta 1 → 2, moved under SYRS-0009"
    ]


def test_link_mirror_attribute_changes_are_left_out():
    old = req("SYRS-0001", {"satisfies": ""})
    new = req("SYRS-0001", {"satisfies": "> SRS-0001"})
    assert describe_changes([req_change("SYRS-0001", old, new)], PROJECT) == []


def test_removed_attribute_is_still_named_from_the_old_project_file():
    old_project = {**PROJECT, "attributes": [*PROJECT["attributes"], {"id": "prio", "name": "Priority"}]}
    changes = [
        FileChange("project.json", raw(old_project), raw(PROJECT)),
        req_change("SYRS-0001", req("SYRS-0001", {"prio": "High"}), req("SYRS-0001", {})),
    ]
    entries = describe_changes(changes, None)
    assert "SYRS-0001: Priority High → (empty)" in entries
    assert "Attribute 'Priority' removed" in entries


def test_requirement_added_removed_and_unchanged():
    assert describe_changes([req_change("SYRS-0003", None, req("SYRS-0003"))], PROJECT) == ["SYRS-0003 added"]
    assert describe_changes([req_change("SYRS-0003", req("SYRS-0003"), None)], PROJECT) == ["SYRS-0003 removed"]
    same = req("SYRS-0003", {"status": "Draft"})
    assert describe_changes([req_change("SYRS-0003", same, same)], PROJECT) == []


def test_unreadable_requirement_files_are_described_as_changed():
    good = req("SYRS-0001", {"status": "Draft"})
    assert describe_changes([req_change("SYRS-0001", good, b"{not json")], PROJECT) == ["SYRS-0001 changed"]
    assert describe_changes([req_change("SYRS-0001", b"\xff\xfe", good)], PROJECT) == ["SYRS-0001 changed"]
    # A JSON value that is not an object.
    assert describe_changes([req_change("SYRS-0001", good, b"[1, 2]")], PROJECT) == ["SYRS-0001 changed"]


def test_byte_order_mark_is_not_an_unreadable_file():
    old = b"\xef\xbb\xbf" + raw(req("SYRS-0001", {"status": "Draft"}))
    new = raw(req("SYRS-0001", {"status": "Reviewed"}))
    assert describe_changes([req_change("SYRS-0001", old, new)], PROJECT) == ["SYRS-0001: Status Draft → Reviewed"]


# --- modules ----------------------------------------------------------------------


def test_module_added_removed_renamed_and_changed():
    path = "modules/sys/module.json"
    system = {"id": "sys", "name": "System", "prefix": "SYRS", "description": ""}
    assert describe_changes([FileChange(path, None, raw(system))]) == ["Module 'System' added"]
    assert describe_changes([FileChange(path, raw(system), None)]) == ["Module 'System' removed"]
    renamed = {**system, "name": "Platform"}
    assert describe_changes([FileChange(path, raw(system), raw(renamed))]) == ["Module 'System' renamed to 'Platform'"]
    described = {**system, "description": "Top level"}
    assert describe_changes([FileChange(path, raw(system), raw(described))]) == ["Module 'System' changed"]
    # Unreadable after the change: named from the readable old version.
    assert describe_changes([FileChange(path, raw(system), b"{oops")]) == ["Module 'System' changed"]
    # Nothing readable at all: named by its id.
    assert describe_changes([FileChange(path, b"{oops", b"{still")]) == ["Module 'sys' changed"]


# --- links ------------------------------------------------------------------------


def pairs(*items: tuple[str, str]) -> bytes:
    return raw({"pairs": [{"source_id": s, "target_id": t} for s, t in items]})


def test_link_pairs_added_and_removed_with_configuration_name():
    old = pairs(("SYRS-0001", "SRS-0001"), ("SYRS-0002", "SRS-0002"))
    new = pairs(("SYRS-0001", "SRS-0001"), ("SYRS-0003", "SRS-0003"), ("SYRS-0001", "SRS-0004"))
    entries = describe_changes([FileChange("links/satisfies.json", old, new)], PROJECT)
    assert entries == [
        "Link SYRS-0001 → SRS-0004 added (Satisfies)",
        "Link SYRS-0003 → SRS-0003 added (Satisfies)",
        "Link SYRS-0002 → SRS-0002 removed (Satisfies)",
    ]


def test_link_file_of_unknown_configuration_uses_its_id_and_new_file_counts_as_all_added():
    entries = describe_changes([FileChange("links/traces.json", None, pairs(("A-1", "B-1")))], PROJECT)
    assert entries == ["Link A-1 → B-1 added (traces)"]


def test_removed_link_file_adds_no_entry_the_project_entry_covers_it():
    old_project = PROJECT
    new_project = {**PROJECT, "link_configurations": [], "attributes": PROJECT["attributes"][:2]}
    changes = [
        FileChange("links/satisfies.json", pairs(("A-1", "B-1")), None),
        FileChange("project.json", raw(old_project), raw(new_project)),
    ]
    assert describe_changes(changes, new_project) == ["Link configuration 'Satisfies' removed"]


# --- project.json -----------------------------------------------------------------


def test_project_created_removed_and_unreadable():
    assert describe_changes([FileChange("project.json", None, raw(PROJECT))]) == ["Project 'Alpha' created"]
    assert describe_changes([FileChange("project.json", raw(PROJECT), None)]) == ["Project file removed"]
    assert describe_changes([FileChange("project.json", raw(PROJECT), b"{")]) == ["Project file changed"]
    assert describe_changes([FileChange("project.json", b"{", raw(PROJECT))]) == ["Project settings changed"]


def test_project_rename_description_and_parent():
    new = {**PROJECT, "name": "Beta", "description": "New", "parent_project_id": "root"}
    entries = describe_changes([FileChange("project.json", raw(PROJECT), raw(new))])
    assert entries == [
        "Project renamed 'Alpha' → 'Beta'",
        "Project description changed",
        "Parent project (empty) → root",
    ]


def test_attribute_and_link_configuration_entries_skip_link_mirrors():
    new = {
        **PROJECT,
        "attributes": [
            {"id": "object_text", "name": "Text"},
            {"id": "status", "name": "Status", "choices": ["Draft"]},
            {"id": "verifies", "name": "Verifies", "system": True},
            {"id": "prio", "name": "Priority"},
        ],
        "link_configurations": [{"id": "verifies", "name": "Verifies"}],
    }
    entries = describe_changes([FileChange("project.json", raw(PROJECT), raw(new))])
    assert entries == [
        "Attribute 'Status' changed",
        "Attribute 'Priority' added",
        "Link configuration 'Verifies' added",
        "Link configuration 'Satisfies' removed",
    ]


def test_views_added_changed_and_removed():
    old = {**PROJECT, "views": [{"id": "main", "name": "Main", "attribute_ids": ["object_text"]},
                                 {"id": "old", "name": "Old", "attribute_ids": []}]}
    new = {**PROJECT, "views": [{"id": "main", "name": "Main", "attribute_ids": ["object_text", "status"]},
                                 {"id": "fresh", "name": "Fresh", "attribute_ids": []}]}
    entries = describe_changes([FileChange("project.json", raw(old), raw(new))])
    assert entries == ["View 'Main' changed", "View 'Fresh' added", "View 'Old' removed"]


def test_module_order_permutation_is_described_but_an_appended_module_is_not():
    old = {**PROJECT, "module_order": ["sys", "sw"]}
    swapped = {**PROJECT, "module_order": ["sw", "sys"]}
    assert describe_changes([FileChange("project.json", raw(old), raw(swapped))]) == ["Module order changed"]
    appended = {**PROJECT, "module_order": ["sys", "sw", "hw"]}
    changes = [
        FileChange("project.json", raw(old), raw(appended)),
        FileChange("modules/hw/module.json", None, raw({"id": "hw", "name": "Hardware"})),
    ]
    assert describe_changes(changes) == ["Module 'Hardware' added"]


def test_bookkeeping_only_project_changes_produce_no_entry_but_other_keys_do():
    bumped = {**PROJECT, "id_counters": {"SYRS": 7}, "format_version": 2}
    assert describe_changes([FileChange("project.json", raw(PROJECT), raw(bumped))]) == []
    other = {**PROJECT, "something_new": True}
    assert describe_changes([FileChange("project.json", raw(PROJECT), raw(other))]) == ["Project settings changed"]


def test_entries_are_grouped_project_modules_requirements_links_other():
    changes = [
        FileChange("modules/sys/notes.txt", None, b"x"),
        FileChange("links/satisfies.json", None, pairs(("SYRS-0002", "SRS-0001"))),
        req_change("SYRS-0002", None, req("SYRS-0002")),
        req_change("SYRS-0001", None, req("SYRS-0001")),
        FileChange("modules/sys/module.json", None, raw({"id": "sys", "name": "System"})),
        FileChange("project.json", None, raw(PROJECT)),
    ]
    assert describe_changes(changes, PROJECT) == [
        "Project 'Alpha' created",
        "Module 'System' added",
        "SYRS-0001 added",
        "SYRS-0002 added",
        "Link SYRS-0002 → SRS-0001 added (Satisfies)",
        "modules/sys/notes.txt added",
    ]


# --- commit_message / session_of --------------------------------------------------


def test_commit_message_exact_shape_with_trailer():
    assert commit_message(["SYRS-0001 added"], "s1") == "SYRS-0001 added\n\nReqMan-Session: s1\n"
    assert commit_message([], None) == "Update project data\n"
    assert commit_message(["", ""], "s2") == "Update project data\n\nReqMan-Session: s2\n"


def test_commit_message_packs_subject_to_72_characters_and_lists_all_in_body():
    a, b, c = "a" * 30, "b" * 30, "c" * 30
    message = commit_message([a, b, c], "s")
    assert message == f"{a}; {b}; +1 more\n\n{a}\n{b}\n{c}\n\nReqMan-Session: s\n"
    exactly = commit_message(["x" * 35, "y" * 35])  # 35 + 2 + 35 == 72: still fits
    assert exactly == "x" * 35 + "; " + "y" * 35 + "\n"


def test_commit_message_with_a_checkout_subject_keeps_the_entries_in_the_body():
    message = commit_message(["SYRS-0001 added", "SYRS-0002 added"], "s1", "u1", "[syrs][alice][Reworking]")
    assert message == (
        "[syrs][alice][Reworking]\n\nSYRS-0001 added\nSYRS-0002 added\n\n"
        "ReqMan-Session: s1\nReqMan-User: u1\n"
    )
    # Without entries the body still says what happened.
    assert commit_message([], "s2", None, "[syrs][alice][T]") == (
        "[syrs][alice][T]\n\nUpdate project data\n\nReqMan-Session: s2\n"
    )


def test_commit_message_shortens_a_given_subject_to_the_subject_budget():
    subject = "[system-module][alice][A checkout name that goes on and on and on for a while]"
    message = commit_message(["SYRS-0001 added"], None, None, subject)
    head = message.split("\n\n")[0]
    assert len(head) <= 72 and head.endswith("…") and head.startswith("[system-module][alice][")
    assert message.endswith("SYRS-0001 added\n")


def test_is_checkout_subject_recognises_a_checkout_subject_only():
    assert is_checkout_subject("[syrs][alice][Reworking the braking chapter]")
    assert is_checkout_subject("[syrs][alice][" + "x" * 200 + "]")  # name shortened to the budget
    assert is_checkout_subject("[syrs][alice][" + "x" * 200 + "…")  # cut off mid-name
    assert not is_checkout_subject("SYRS-0001 added")
    assert not is_checkout_subject("Update project data")
    assert not is_checkout_subject("")


def test_session_of_reads_only_the_last_paragraph_trailer():
    assert session_of("Subject\n\nBody\n\nReqMan-Session: abc\n") == "abc"
    assert session_of("Subject\n\nSigned-off-by: X <x@y>\nReqMan-Session:  def  \n") == "def"
    assert session_of("Subject\n\nReqMan-Session: abc\n\nLater paragraph\n") is None
    assert session_of("Subject\n\nReqMan-Session:\n") is None
    assert session_of("Subject\n\nOther-Session: abc\n") is None
    assert session_of("") is None
    # The subject alone is the last paragraph of a one-line message.
    assert session_of("ReqMan-Session: only") == "only"


# --- requirement_history ----------------------------------------------------------


def rev(commit: str, content) -> Revision:
    data = content if content is None or isinstance(content, bytes) else raw(content)
    return Revision(
        commit=commit,
        author_name=f"Author {commit}",
        author_email=f"{commit}@example.com",
        authored_date=f"2026-01-0{commit[-1]}T00:00:00+00:00",
        date=f"2026-01-0{commit[-1]}T00:00:00+00:00",
        subject=f"subject {commit}",
        content=data,
    )


def summary(result) -> list[tuple[str, str, bool]]:
    return [(e["commit"], e["change"], e["unreadable"]) for e in result["entries"]]


def test_history_entry_carries_commit_metadata():
    result = requirement_history("R-1", [rev("c1", req("R-1", {"t": "x"}))])
    (entry,) = result["entries"]
    assert entry == {
        "commit": "c1",
        "author_name": "Author c1",
        "author_email": "c1@example.com",
        "authored_date": "2026-01-01T00:00:00+00:00",
        "date": "2026-01-01T00:00:00+00:00",
        "subject": "subject c1",
        "change": "added",
        "diffs": [{"kind": "attribute", "attribute_id": "t", "old": None, "new": "x"}],
        "unreadable": False,
    }
    assert result["blame"]["t"]["commit"] == "c1"
    assert "change" not in result["blame"]["t"]


def test_history_of_a_removed_requirement_ends_with_removed_and_empty_blame():
    revisions = [
        rev("c3", None),
        rev("c2", req("R-1", {"status": "Reviewed"})),
        rev("c1", req("R-1", {"status": "Draft"})),
    ]
    result = requirement_history("R-1", revisions)
    assert summary(result) == [("c3", "removed", False), ("c2", "modified", False), ("c1", "added", False)]
    # The removal lists the values the requirement had.
    assert result["entries"][0]["diffs"][0] == {
        "kind": "attribute", "attribute_id": "status", "old": "Reviewed", "new": None
    }
    assert result["blame"] == {}
    assert result["truncated"] is False


def test_history_of_a_requirement_added_again_after_removal():
    revisions = [
        rev("c4", req("R-1", {"status": "New"})),
        rev("c3", None),
        rev("c2", req("R-1", {"status": "Reviewed"})),
        rev("c1", req("R-1", {"status": "Draft"})),
    ]
    result = requirement_history("R-1", revisions)
    assert [e["change"] for e in result["entries"]] == ["added", "removed", "modified", "added"]
    assert result["entries"][0]["diffs"] == [{"kind": "attribute", "attribute_id": "status", "old": None, "new": "New"}]
    assert result["blame"]["status"]["commit"] == "c4"


def test_unreadable_revision_is_listed_and_skipped_as_comparison_base():
    revisions = [
        rev("c3", req("R-1", {"status": "Reviewed", "t": "x"})),
        rev("c2", b"{ broken json"),
        rev("c1", req("R-1", {"status": "Draft", "t": "x"})),
    ]
    result = requirement_history("R-1", revisions)
    assert summary(result) == [("c3", "modified", False), ("c2", "modified", True), ("c1", "added", False)]
    assert result["entries"][1]["diffs"] == []
    # c3 is compared with c1, the last readable version.
    assert result["entries"][0]["diffs"] == [
        {"kind": "attribute", "attribute_id": "status", "old": "Draft", "new": "Reviewed"}
    ]
    assert result["blame"]["status"]["commit"] == "c3"
    assert result["blame"]["t"]["commit"] == "c1"


def test_truncated_history_uses_the_oldest_revision_only_as_baseline():
    revisions = [
        rev("c3", req("R-1", {"status": "Approved", "t": "x"})),
        rev("c2", req("R-1", {"status": "Reviewed", "t": "x"})),
        rev("c1", req("R-1", {"status": "Draft", "t": "x"})),
    ]
    result = requirement_history("R-1", revisions, truncated=True)
    assert summary(result) == [("c3", "modified", False), ("c2", "modified", False)]
    assert result["truncated"] is True
    assert result["entries"][1]["diffs"][0]["old"] == "Draft"
    # "t" was last changed before the listed window: no blame for it.
    assert set(result["blame"]) == {"status"}


def test_revision_without_any_change_is_not_listed():
    same = req("R-1", {"status": "Draft"})
    result = requirement_history("R-1", [rev("c2", same), rev("c1", same)])
    assert summary(result) == [("c1", "added", False)]


def test_parent_and_order_diffs():
    revisions = [
        rev("c3", req("R-2", {"t": "x"}, parent_id=None, order=4)),
        rev("c2", req("R-2", {"t": "x"}, parent_id="R-1", order=0)),
        rev("c1", req("R-2", {"t": "x"}, parent_id="R-1", order=2)),
    ]
    result = requirement_history("R-2", revisions)
    newest, middle, oldest = result["entries"]
    assert newest["diffs"] == [
        {"kind": "parent", "attribute_id": None, "old": "R-1", "new": None},
        {"kind": "order", "attribute_id": None, "old": 0, "new": 4},
    ]
    assert middle["diffs"] == [{"kind": "order", "attribute_id": None, "old": 2, "new": 0}]
    # Created as a child: the parent is part of the "added" entry, the initial order is not.
    assert {"kind": "parent", "attribute_id": None, "old": None, "new": "R-1"} in oldest["diffs"]
    assert all(d["kind"] != "order" for d in oldest["diffs"])
    top_level = requirement_history("R-3", [rev("c1", req("R-3", {"t": "x"}))])
    assert [d["kind"] for d in top_level["entries"][0]["diffs"]] == ["attribute"]


def test_blame_only_lists_attributes_the_requirement_has_now():
    revisions = [
        rev("c2", req("R-1", {"status": "Draft"})),
        rev("c1", req("R-1", {"status": "Draft", "prio": "High"})),
    ]
    result = requirement_history("R-1", revisions)
    assert result["entries"][0]["diffs"] == [{"kind": "attribute", "attribute_id": "prio", "old": "High", "new": None}]
    assert set(result["blame"]) == {"status"}


def test_history_stops_at_a_revision_of_another_requirement():
    revisions = [
        rev("c3", req("R-2", {"status": "Reviewed"})),
        rev("c2", req("R-2", {"status": "Draft"})),
        rev("c1", req("R-1", {"status": "Draft"})),  # the file named another id (edited by hand)
    ]
    result = requirement_history("R-2", revisions)
    assert summary(result) == [("c3", "modified", False), ("c2", "added", False)]
    # Complete: c2 is R-2's creation, not a baseline of a cut-off history.
    assert result["truncated"] is False


def test_history_stopped_at_another_requirement_still_lists_the_creation():
    revisions = [
        rev("c2", req("R-2", {"status": "Draft"})),
        rev("c1", req("R-1", {"status": "Draft"})),
    ]
    result = requirement_history("R-2", revisions)
    assert summary(result) == [("c2", "added", False)]
    assert result["blame"]["status"]["commit"] == "c2"
    # Also when the commit limit was reached: the other requirement's revision ends it.
    assert requirement_history("R-2", revisions, truncated=True)["truncated"] is False
    assert summary(requirement_history("R-2", revisions, truncated=True)) == [("c2", "added", False)]
