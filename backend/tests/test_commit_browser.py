"""Commit browser: ``ProjectService.git_commits`` / ``git_commit`` / ``get_commit_snapshot`` /
``commit_requirements`` and their REST/WebSocket surface (docs/ARCHITECTURE.md "Commit browser").

Every test works in pytest's ``tmp_path`` (outside any repository) with its
own repositories; ``conftest.py`` isolates git from the user's configuration.
Commits made by hand (other authors, dates, control characters, odd file
names, merges) go through ``git()``, which refuses to run in the checkout.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from reqman.formats import PROJECT_FORMAT, NewerFormatError
from reqman.git_repo import GitError, GitHistory, GitSettings
from reqman.service import MAX_COMMIT_OFFSET, ProjectService
from reqman.store import ProjectStore

CHECKOUT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


def git(path: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    full_env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    full_env.update(env or {})
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, env=full_env, check=False)
    if check:
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8", "surrogateescape")


def hand_commit(folder: Path, message: str, *, env: dict[str, str] | None = None) -> str:
    """Stage everything in ``folder`` and commit it with ``message`` (any bytes allowed, via a file)."""
    message_file = folder.parent.parent / "commit-message.txt"
    message_file.write_bytes(message.encode("utf-8"))
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "--allow-empty", "--cleanup=verbatim", "-F", str(message_file), env=env)
    message_file.unlink()
    return git(folder, "rev-parse", "HEAD").strip()


def head(folder: Path) -> str:
    return git(folder, "rev-parse", "HEAD").strip()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def edit_requirement(service: ProjectService, project_id: str, module_id: str, req_id: str, text: str) -> None:
    path = service.store.project_path(project_id) / service.store.requirement_relative_path(module_id, req_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["attributes"]["object_text"] = text
    write_json(path, data)


@pytest.fixture
def root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=data, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    return data


def make_service(root: Path, **settings) -> ProjectService:
    # Squash window 0: every mutation is its own commit, so the tests can count them.
    settings.setdefault("squash_window_seconds", 0)
    history = GitHistory(GitSettings(enabled=True, **settings), clock=Clock())
    return ProjectService(ProjectStore(root), history=history)


@pytest.fixture
def service(root):
    return make_service(root)


class Proj:
    """A project with two modules: SYRS (2 requirements) and SWRS (1 requirement), one commit per step."""

    def __init__(self, service: ProjectService) -> None:
        self.service = service
        self.pid = service.create_project("Alpha").id
        service.add_attribute_definition(self.pid, "status", "Status", "enum", choices=["Draft", "Reviewed"])
        self.sys = service.create_module(self.pid, "System", "SYRS").id
        self.sw = service.create_module(self.pid, "Software", "SWRS").id
        self.r1 = service.add_requirement(self.pid, self.sys, {"object_text": "One", "status": "Draft"}).id
        self.r2 = service.add_requirement(self.pid, self.sys, {"object_text": "Two", "status": "Draft"}).id
        self.s1 = service.add_requirement(self.pid, self.sw, {"object_text": "Soft", "status": "Draft"}).id
        self.folder = service.store.project_path(self.pid)


class Small:
    """A project with one module (two commits): enough for validation tests."""

    def __init__(self, service: ProjectService) -> None:
        self.pid = service.create_project("Small").id
        self.sys = service.create_module(self.pid, "System", "SYRS").id
        self.folder = service.store.project_path(self.pid)


def subjects(page: dict) -> list[str]:
    return [e["subject"] for e in page["entries"]]




ALL_SUBJECTS = [
    "SWRS-0001 added",
    "SYRS-0002 added",
    "SYRS-0001 added",
    "Module 'Software' added",
    "Module 'System' added",
    "Attribute 'Status' added",
    "Create project 'Alpha'",
]


# --- list: scopes -------------------------------------------------------------------------


def test_project_scope_lists_first_parent_history_newest_first(service):
    p = Proj(service)
    page = service.git_commits(p.pid)
    assert page["available"] is True and page["reason"] is None
    assert page["offset"] == 0 and page["has_more"] is False
    assert [e["commit"] for e in page["entries"]] == git(p.folder, "rev-list", "--first-parent", "HEAD").split()
    assert subjects(page) == ALL_SUBJECTS
    assert [e["changed_requirements"] for e in page["entries"]] == [1, 1, 1, 0, 0, 0, 0]
    newest = page["entries"][0]
    # A list entry is the parsed commit without its full message.
    assert "message" not in newest
    assert newest["parents"] == [page["entries"][1]["commit"]]
    assert page["entries"][-1]["parents"] == []
    assert newest["author_name"] == "Test Author" and newest["author_email"] == "author@example.com"
    assert newest["committer_name"] == "Test Author" and newest["date"] and newest["authored_date"]
    assert newest["baselines"] == []


def test_module_scope_lists_only_commits_touching_the_module(service):
    p = Proj(service)
    page = service.git_commits(p.pid, module_id=p.sys)
    assert subjects(page) == ["SYRS-0002 added", "SYRS-0001 added", "Module 'System' added"]
    page = service.git_commits(p.pid, module_id=p.sw)
    assert subjects(page) == ["SWRS-0001 added", "Module 'Software' added"]


def test_module_scope_counts_only_requirements_inside_the_module(service):
    p = Proj(service)
    edit_requirement(service, p.pid, p.sys, p.r1, "One changed")
    edit_requirement(service, p.pid, p.sys, p.r2, "Two changed")
    edit_requirement(service, p.pid, p.sw, p.s1, "Soft changed")
    commit = hand_commit(p.folder, "Edit three requirements by hand")
    project_entry = service.git_commits(p.pid)["entries"][0]
    assert project_entry["commit"] == commit and project_entry["changed_requirements"] == 3
    assert service.git_commits(p.pid, module_id=p.sys)["entries"][0]["changed_requirements"] == 2
    assert service.git_commits(p.pid, module_id=p.sw)["entries"][0]["changed_requirements"] == 1
    requirement_entry = service.git_commits(p.pid, module_id=p.sys, requirement_id=p.r2)["entries"][0]
    assert requirement_entry["commit"] == commit and requirement_entry["changed_requirements"] == 1


def test_requirement_scope_lists_only_commits_of_its_file(service):
    p = Proj(service)
    service.update_requirement(p.pid, p.sys, p.r1, {"status": "Reviewed"})
    service.update_requirement(p.pid, p.sys, p.r2, {"status": "Reviewed"})
    page = service.git_commits(p.pid, module_id=p.sys, requirement_id=p.r1)
    assert len(page["entries"]) == 2
    assert subjects(page)[1] == "SYRS-0001 added"
    assert all(e["changed_requirements"] == 1 for e in page["entries"])


def test_scope_validation(service):
    p = Proj(service)
    with pytest.raises(ValueError, match="needs its module_id"):
        service.git_commits(p.pid, requirement_id=p.r1)
    with pytest.raises(FileNotFoundError):
        service.git_commits(p.pid, module_id="missing")
    with pytest.raises(FileNotFoundError):
        service.git_commits(p.pid, module_id=p.sys, requirement_id="SYRS-9999")
    # A requirement of another module is not found in this one.
    with pytest.raises(FileNotFoundError):
        service.git_commits(p.pid, module_id=p.sys, requirement_id=p.s1)
    with pytest.raises(FileNotFoundError):
        service.git_commits("missing")


# --- list: paging -------------------------------------------------------------------------


def test_paging_with_offset_limit_and_has_more(service):
    p = Proj(service)
    first = service.git_commits(p.pid, limit=3)
    assert subjects(first) == ALL_SUBJECTS[:3] and first["has_more"] is True and first["offset"] == 0
    second = service.git_commits(p.pid, offset=3, limit=3)
    assert subjects(second) == ALL_SUBJECTS[3:6] and second["has_more"] is True and second["offset"] == 3
    last = service.git_commits(p.pid, offset=6, limit=3)
    assert subjects(last) == ALL_SUBJECTS[6:] and last["has_more"] is False
    exact = service.git_commits(p.pid, offset=0, limit=7)
    assert len(exact["entries"]) == 7 and exact["has_more"] is False
    beyond = service.git_commits(p.pid, offset=50)
    assert beyond["entries"] == [] and beyond["has_more"] is False and beyond["available"] is True
    # Huge limits are capped, not refused.
    assert len(service.git_commits(p.pid, limit=10_000)["entries"]) == 7
    # offset None means 0.
    assert subjects(service.git_commits(p.pid, offset=None, limit=1)) == ALL_SUBJECTS[:1]


def test_paging_in_module_scope(service):
    p = Proj(service)
    page = service.git_commits(p.pid, module_id=p.sys, limit=2)
    assert subjects(page) == ["SYRS-0002 added", "SYRS-0001 added"] and page["has_more"] is True
    page = service.git_commits(p.pid, module_id=p.sys, offset=2, limit=2)
    assert subjects(page) == ["Module 'System' added"] and page["has_more"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": "5"},
        {"limit": True},
        {"limit": 1.5},
        {"offset": -1},
        {"offset": "1"},
        {"offset": True},
        {"offset": MAX_COMMIT_OFFSET + 1},
    ],
)
def test_paging_validation(service, kwargs):
    p = Small(service)
    with pytest.raises(ValueError, match="Invalid (limit|offset)"):
        service.git_commits(p.pid, **kwargs)


# --- list: filters ------------------------------------------------------------------------


def test_author_filter_is_a_case_insensitive_fixed_string(service):
    p = Proj(service)
    edit_requirement(service, p.pid, p.sys, p.r1, "by Zed")
    commit = hand_commit(
        p.folder,
        "Edit by another author",
        env={"GIT_AUTHOR_NAME": "Zed Q. [Reviewer]", "GIT_AUTHOR_EMAIL": "zed@example.org"},
    )
    page = service.git_commits(p.pid, author="zed q. [reviewer]")
    assert [e["commit"] for e in page["entries"]] == [commit]
    assert page["entries"][0]["author_name"] == "Zed Q. [Reviewer]"
    # The email matches too (git --author matches "name <email>").
    assert [e["commit"] for e in service.git_commits(p.pid, author="ZED@EXAMPLE")["entries"]] == [commit]
    # Regex metacharacters are literal: "Zed.*" is no pattern.
    assert service.git_commits(p.pid, author="Zed.*")["entries"] == []
    assert len(service.git_commits(p.pid, author="test author")["entries"]) == 7
    # Blank filters are no filter.
    assert len(service.git_commits(p.pid, author="   ")["entries"]) == 8
    assert len(service.git_commits(p.pid, author="")["entries"]) == 8


def test_text_filter_matches_the_message_case_insensitively_and_literally(service):
    p = Proj(service)
    page = service.git_commits(p.pid, text="syrs-000")
    assert subjects(page) == ["SYRS-0002 added", "SYRS-0001 added"]
    # The body counts too (the session trailer is in every auto-commit body).
    assert len(service.git_commits(p.pid, text="reqman-session")["entries"]) >= 1
    # An invalid regular expression is just text.
    assert service.git_commits(p.pid, text="[(")["entries"] == []
    assert service.git_commits(p.pid, text="SYRS.*")["entries"] == []
    # Combined with a scope and an author.
    page = service.git_commits(p.pid, module_id=p.sys, text="added", author="Test")
    assert subjects(page) == ["SYRS-0002 added", "SYRS-0001 added", "Module 'System' added"]


@pytest.mark.parametrize("field", ["author", "text"])
@pytest.mark.parametrize(
    "value, message",
    [
        ("a\nb", "control characters"),
        ("a\tb", "control characters"),
        ("a\x00b", "control characters"),
        ("a\x7fb", "control characters"),
        ("x" * 201, "at most 200"),
        (5, "expected a string"),
        (["a"], "expected a string"),
    ],
)
def test_text_filters_are_validated(service, field, value, message):
    p = Small(service)
    with pytest.raises(ValueError, match=message):
        service.git_commits(p.pid, **{field: value})


def test_text_filter_of_200_characters_is_accepted(service):
    p = Proj(service)
    assert service.git_commits(p.pid, text="x" * 200)["entries"] == []
    assert service.git_commits(p.pid, author="x" * 200)["entries"] == []


def test_date_filters_are_inclusive_days(root, monkeypatch):
    service = make_service(root)
    # The project's first commits are dated 2020-05-05 (committer date), the rest today.
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-05-05T12:00:00")
    p = Proj(service)
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    service.update_requirement(p.pid, p.sys, p.r1, {"status": "Reviewed"})
    service.update_requirement(p.pid, p.sys, p.r2, {"status": "Reviewed"})
    old = [e["commit"] for e in service.git_commits(p.pid)["entries"]][2:]
    assert len(old) == 7

    on_the_day = service.git_commits(p.pid, since="2020-05-05", until="2020-05-05")
    assert [e["commit"] for e in on_the_day["entries"]] == old
    assert service.git_commits(p.pid, until="2020-05-04")["entries"] == []
    assert service.git_commits(p.pid, since="2020-05-06", until="2020-05-07")["entries"] == []
    assert [e["commit"] for e in service.git_commits(p.pid, until="2020-05-05")["entries"]] == old
    today = date.today()
    recent = service.git_commits(p.pid, since=today.isoformat())
    assert len(recent["entries"]) == 2 and not set(old) & {e["commit"] for e in recent["entries"]}
    assert len(service.git_commits(p.pid, since=today.isoformat(), until=today.isoformat())["entries"]) == 2
    assert service.git_commits(p.pid, since=(today + timedelta(days=2)).isoformat())["entries"] == []
    assert len(service.git_commits(p.pid, since="2020-05-05", until=today.isoformat())["entries"]) == 9
    # Dates combine with scope and paging.
    page = service.git_commits(p.pid, module_id=p.sys, until="2020-05-05", limit=2)
    assert subjects(page) == ["SYRS-0002 added", "SYRS-0001 added"] and page["has_more"] is True
    # Empty dates are no filter.
    assert len(service.git_commits(p.pid, since="", until="")["entries"]) == 9


def test_since_filter_finds_newer_commits_behind_a_backdated_commit(service):
    """Committer dates out of order (clock skew, a commit from another clone): ``since`` must still
    list every commit of that day, not stop walking the history at the first older one."""
    p = Proj(service)
    edit_requirement(service, p.pid, p.sys, p.r1, "dated")
    dated = hand_commit(
        p.folder,
        "Backdated commit",
        env={"GIT_COMMITTER_DATE": "2020-05-05T12:00:00", "GIT_AUTHOR_DATE": "2020-05-05T12:00:00"},
    )
    assert [e["commit"] for e in service.git_commits(p.pid, since="2020-05-05", until="2020-05-05")["entries"]] == [
        dated
    ]
    recent = service.git_commits(p.pid, since=date.today().isoformat())
    assert subjects(recent) == ALL_SUBJECTS


def test_date_filters_outside_gits_years_are_clamped(service):
    p = Proj(service)
    # An open bound beyond 1970-2099 limits nothing.
    assert len(service.git_commits(p.pid, since="1900-01-01")["entries"]) == 7
    assert len(service.git_commits(p.pid, until="2200-01-01")["entries"]) == 7
    assert len(service.git_commits(p.pid, since="0001-01-01", until="9999-12-31")["entries"]) == 7
    # A bound on the far side is clamped and so excludes everything.
    assert service.git_commits(p.pid, since="2200-01-01")["entries"] == []
    assert service.git_commits(p.pid, until="1900-01-01")["entries"] == []


@pytest.mark.parametrize("field, label", [("since", "from"), ("until", "to")])
@pytest.mark.parametrize(
    "value", ["2026-13-01", "2026-02-30", "2026/01/01", "26-01-01", "2026-1-1", "today", "2026-01-01T00:00", 20260101]
)
def test_date_filters_are_validated(service, field, label, value):
    p = Small(service)
    with pytest.raises(ValueError, match=f"Invalid {label} date"):
        service.git_commits(p.pid, **{field: value})


# --- list: availability -------------------------------------------------------------------


def test_history_off_or_no_repository_is_unavailable(root):
    plain = ProjectService(ProjectStore(root))
    project = plain.create_project("Plain")
    page = plain.git_commits(project.id)
    assert page["available"] is False and page["entries"] == [] and page["has_more"] is False
    # History on, but the project folder has no repository of its own.
    page = make_service(root).git_commits(project.id)
    assert page["available"] is False and page["entries"] == []
    assert isinstance(page["reason"], str) and page["reason"]


def test_empty_repository_is_available_with_no_entries(root):
    project = ProjectService(ProjectStore(root)).create_project("Empty")
    git(root / project.id, "init", "-q", "-b", "current")
    page = make_service(root).git_commits(project.id)
    assert page == {"available": True, "reason": None, "entries": [], "offset": 0, "has_more": False}


def test_baseline_tags_are_listed_on_their_commit(service):
    p = Proj(service)
    service.create_baseline(p.pid, "v1")
    service.create_baseline(p.pid, "v1.1")
    service.update_requirement(p.pid, p.sys, p.r1, {"status": "Reviewed"})
    entries = service.git_commits(p.pid)["entries"]
    assert entries[0]["baselines"] == []
    assert entries[1]["subject"] == "SWRS-0001 added" and entries[1]["baselines"] == ["v1", "v1.1"]
    assert all(e["baselines"] == [] for e in entries[2:])
    # Module scope shows them too.
    scoped = service.git_commits(p.pid, module_id=p.sw)["entries"]
    assert scoped[0]["baselines"] == ["v1", "v1.1"]


# --- details ------------------------------------------------------------------------------


def test_root_commit_is_compared_with_nothing(root):
    plain = ProjectService(ProjectStore(root))
    pid = plain.create_project("Rooted").id
    mid = plain.create_module(pid, "System", "SYRS").id
    plain.add_requirement(pid, mid, {"object_text": "a"})
    plain.add_requirement(pid, mid, {"object_text": "b"})
    folder = root / pid
    git(folder, "init", "-q", "-b", "current")
    commit = hand_commit(folder, "Import everything")
    service = make_service(root)
    details = service.git_commit(pid, commit)
    assert details["commit"] == commit and details["parents"] == []
    assert details["subject"] == "Import everything" and details["message"] == "Import everything"
    assert details["summary"]["modules_added"] == 1
    assert details["summary"]["requirements_added"] == 2
    assert details["modules"] == [
        {
            "module_id": mid,
            "name": "System",
            "change": "added",
            "added": ["SYRS-0001", "SYRS-0002"],
            "removed": [],
            "modified": [],
        }
    ]
    assert details["changed_files"] == 4
    entry = service.git_commits(pid)["entries"][0]
    assert entry["parents"] == [] and entry["changed_requirements"] == 2


def test_details_of_an_update_carry_the_attribute_diffs_and_full_message(service):
    p = Proj(service)
    service.update_requirement(p.pid, p.sys, p.r1, {"status": "Reviewed"})
    commit = head(p.folder)
    details = service.git_commit(p.pid, commit)
    assert details["message"].startswith(details["subject"])
    assert details["summary"]["requirements_modified"] == 1
    module = details["modules"][0]
    assert module["module_id"] == p.sys and module["change"] is None
    assert module["modified"][0]["id"] == p.r1
    diffs = module["modified"][0]["diffs"]
    assert any(d.get("attribute_id") == "status" for d in diffs)
    assert details["notes"] == []
    assert details["baselines"] == []


def test_merge_commit_is_compared_with_its_first_parent(service):
    p = Proj(service)
    folder = p.folder
    fork = head(folder)
    git(folder, "checkout", "-q", "-b", "side")
    edit_requirement(service, p.pid, p.sys, p.r2, "from the side branch")
    side = hand_commit(folder, "Side change")
    git(folder, "checkout", "-q", "current")
    edit_requirement(service, p.pid, p.sw, p.s1, "on current")
    mainline = hand_commit(folder, "Mainline change")
    git(folder, "merge", "--no-ff", "-q", "-m", "Merge side", "side")
    merge = head(folder)

    page = service.git_commits(p.pid)
    listed = [e["commit"] for e in page["entries"]]
    assert listed[:3] == [merge, mainline, fork]
    assert side not in listed  # first-parent history only
    assert page["entries"][0]["parents"] == [mainline, side]
    assert page["entries"][0]["changed_requirements"] == 1

    details = service.git_commit(p.pid, merge)
    assert details["parents"] == [mainline, side]
    assert details["summary"]["requirements_modified"] == 1
    assert [(m["module_id"], [x["id"] for x in m["modified"]]) for m in details["modules"]] == [(p.sys, [p.r2])]
    # The side commit itself can still be opened.
    assert service.git_commit(p.pid, side)["modules"][0]["modified"][0]["id"] == p.r2


def test_link_changes_are_listed_with_the_configuration_name(service):
    p = Proj(service)
    config = service.create_link_configuration(p.pid, "Satisfies", p.sw, p.sys)
    service.link_requirements(p.pid, config.id, p.s1, p.r1)
    details = service.git_commit(p.pid, head(p.folder))
    assert details["links"]["added"] == [
        {"config_id": config.id, "name": "Satisfies", "source_id": p.s1, "target_id": p.r1}
    ]
    assert details["summary"]["links_added"] == 1
    service.unlink_requirements(p.pid, config.id, p.s1, p.r1)
    details = service.git_commit(p.pid, head(p.folder))
    assert details["links"]["removed"] == [
        {"config_id": config.id, "name": "Satisfies", "source_id": p.s1, "target_id": p.r1}
    ]


def test_row_styles_of_changed_requirements(service):
    p = Proj(service)
    service.set_row_styles(
        p.pid,
        [{"attribute_id": "status", "value": "Reviewed", "font_color": "#112233", "background_color": "#ffffff",
          "bold": True}],
    )
    service.update_requirement(p.pid, p.sys, p.r1, {"status": "Reviewed"})
    details = service.git_commit(p.pid, head(p.folder))
    module = details["modules"][0]
    assert module["row_styles"] == {
        p.r1: {"attribute_id": "status", "value": "Reviewed", "font_color": "#112233",
               "background_color": "#ffffff", "bold": True}
    }
    # A change of an unstyled requirement carries no row_styles.
    service.update_requirement(p.pid, p.sys, p.r2, {"object_text": "plain"})
    assert "row_styles" not in service.git_commit(p.pid, head(p.folder))["modules"][0]


def test_notes_describe_module_renames_and_reorders(service):
    p = Proj(service)
    module_file = p.folder / "modules" / p.sys / "module.json"
    data = json.loads(module_file.read_text(encoding="utf-8"))
    data["name"] = "System Renamed"
    write_json(module_file, data)
    rename = hand_commit(p.folder, "Rename by hand")
    details = service.git_commit(p.pid, rename)
    assert "Module 'System' renamed to 'System Renamed'" in details["notes"]
    assert details["summary"]["requirements_modified"] == 0

    service.reorder_module(p.pid, p.sw, "up")
    details = service.git_commit(p.pid, head(p.folder))
    assert details["notes"] == ["Module order changed"]


def test_older_format_project_json_in_a_commit_is_migrated(service):
    p = Proj(service)
    project_file = p.folder / "project.json"
    current_text = project_file.read_text(encoding="utf-8")
    data = json.loads(current_text)
    # A version 6 project.json: module_order is a flat list of module id strings.
    data["format_version"] = 6
    data["module_order"] = [p.sw, p.sys]
    write_json(project_file, data)
    old = hand_commit(p.folder, "Older format")
    # Back to the current format with the original order (a reorder in the new format).
    project_file.write_text(current_text, encoding="utf-8")
    new = hand_commit(p.folder, "Current format again")

    details = service.git_commit(p.pid, new)
    assert details["notes"] == ["Module order changed"]
    assert details["summary"]["requirements_modified"] == 0
    details = service.git_commit(p.pid, old)
    assert details["notes"] == ["Module order changed"]

    snapshot = service.get_commit_snapshot(p.pid, old)
    assert snapshot["project"]["module_order"] == [{"module": p.sw}, {"module": p.sys}]
    assert [m["id"] for m in snapshot["modules"]] == [p.sw, p.sys]
    assert [r.id for r in service.commit_requirements(p.pid, old, p.sys)] == [p.r1, p.r2]


def test_newer_format_in_a_commit_is_refused(service):
    p = Proj(service)
    project_file = p.folder / "project.json"
    current_text = project_file.read_text(encoding="utf-8")
    data = json.loads(current_text)
    data["format_version"] = PROJECT_FORMAT.current + 1
    write_json(project_file, data)
    newer = hand_commit(p.folder, "From the future")
    project_file.write_text(current_text, encoding="utf-8")
    hand_commit(p.folder, "Back")
    with pytest.raises(NewerFormatError):
        service.git_commit(p.pid, newer)
    with pytest.raises(NewerFormatError):
        service.get_commit_snapshot(p.pid, newer)
    with pytest.raises(NewerFormatError):
        service.commit_requirements(p.pid, newer, p.sys)
    # The list still shows it.
    assert service.git_commits(p.pid)["entries"][1]["commit"] == newer


@pytest.mark.parametrize(
    "make_id",
    [
        lambda folder: "HEAD",
        lambda folder: "HEAD~1",
        lambda folder: "current",
        lambda folder: git(folder, "rev-parse", "HEAD").strip()[:12],
        lambda folder: git(folder, "rev-parse", "HEAD").strip().upper(),
        lambda folder: "0" * 40,
        lambda folder: "f" * 64,
        lambda folder: git(folder, "rev-parse", "HEAD^{tree}").strip(),
        lambda folder: git(folder, "rev-parse", "HEAD:project.json").strip(),
        lambda folder: "",
        lambda folder: "../../etc/passwd",
    ],
    ids=["HEAD", "HEAD~1", "branch", "short", "upper", "zeros", "sha256-missing", "tree", "blob", "empty", "path"],
)
def test_bad_or_non_commit_ids_are_not_found(service, make_id):
    p = Small(service)
    commit = make_id(p.folder)
    with pytest.raises(FileNotFoundError):
        service.git_commit(p.pid, commit)
    with pytest.raises(FileNotFoundError):
        service.get_commit_snapshot(p.pid, commit)
    with pytest.raises(FileNotFoundError):
        service.commit_requirements(p.pid, commit, p.sys)


def test_details_without_a_repository_are_not_found(root):
    plain = ProjectService(ProjectStore(root))
    pid = plain.create_project("Plain").id
    with pytest.raises(FileNotFoundError):
        plain.git_commit(pid, "a" * 40)
    with pytest.raises(FileNotFoundError):
        make_service(root).git_commit(pid, "a" * 40)


def test_details_list_baselines_on_the_commit(service):
    p = Proj(service)
    service.create_baseline(p.pid, "rel-1")
    assert service.git_commit(p.pid, head(p.folder))["baselines"] == ["rel-1"]


# --- browse at a commit -------------------------------------------------------------------


def test_snapshot_and_requirements_at_an_older_commit(service):
    p = Proj(service)
    at = [e["commit"] for e in service.git_commits(p.pid)["entries"] if e["subject"] == "SYRS-0001 added"][0]
    service.update_requirement(p.pid, p.sys, p.r1, {"object_text": "Later"})
    service.add_requirement(p.pid, p.sys, {"object_text": "Three"})

    snapshot = service.get_commit_snapshot(p.pid, at)
    assert snapshot["commit"] == at and snapshot["subject"] == "SYRS-0001 added"
    assert snapshot["author_name"] == "Test Author" and snapshot["date"]
    assert snapshot["project"]["id"] == p.pid
    assert [m["id"] for m in snapshot["modules"]] == [p.sys, p.sw]
    requirements = service.commit_requirements(p.pid, at, p.sys)
    assert [(r.id, r.attributes["object_text"]) for r in requirements] == [(p.r1, "One")]
    # The software module exists at that commit but has no requirements yet.
    assert service.commit_requirements(p.pid, at, p.sw) == []
    with pytest.raises(FileNotFoundError):
        service.commit_requirements(p.pid, at, "missing")
    # A module that did not exist yet at the commit is unknown there.
    first = service.git_commits(p.pid)["entries"][-1]["commit"]
    with pytest.raises(FileNotFoundError):
        service.commit_requirements(p.pid, first, p.sys)
    assert service.get_commit_snapshot(p.pid, first)["modules"] == []


def test_commit_browser_writes_nothing(service):
    p = Proj(service)
    before = {str(f.relative_to(p.folder)): f.read_bytes() for f in p.folder.rglob("*") if f.is_file()}
    head_before = head(p.folder)
    commit = service.git_commits(p.pid)["entries"][2]["commit"]
    service.git_commit(p.pid, commit)
    service.get_commit_snapshot(p.pid, commit)
    service.commit_requirements(p.pid, commit, p.sys)
    after = {str(f.relative_to(p.folder)): f.read_bytes() for f in p.folder.rglob("*") if f.is_file()}
    data_files = lambda files: {k: v for k, v in files.items() if not k.startswith(".git")}  # noqa: E731
    assert data_files(after) == data_files(before)
    assert head(p.folder) == head_before
    assert git(p.folder, "status", "--porcelain") == ""


# --- hostile commit contents ----------------------------------------------------------------


def test_control_characters_in_subjects_and_author_names_do_not_split_records(service):
    p = Proj(service)
    edit_requirement(service, p.pid, p.sys, p.r1, "ctl")
    odd = hand_commit(
        p.folder,
        "Odd \x1e subject \x1f here\n\nBody \x1e\x1f line\n",
        env={"GIT_AUTHOR_NAME": "Ann\x1fB\x1eC", "GIT_AUTHOR_EMAIL": "ann\x1f@example.com"},
    )
    page = service.git_commits(p.pid)
    assert len(page["entries"]) == 8
    entry = page["entries"][0]
    assert entry["commit"] == odd
    assert entry["subject"] == "Odd \x1e subject \x1f here"
    assert entry["author_name"] == "Ann\x1fB\x1eC"
    assert entry["changed_requirements"] == 1
    assert page["entries"][1]["subject"] == ALL_SUBJECTS[0]
    details = service.git_commit(p.pid, odd)
    assert details["message"] == "Odd \x1e subject \x1f here\n\nBody \x1e\x1f line\n"
    assert details["author_name"] == "Ann\x1fB\x1eC"
    # Filtering on such an author with a plain substring still works.
    assert [e["commit"] for e in service.git_commits(p.pid, author="Ann")["entries"]] == [odd]


def test_newline_in_a_requirement_file_name(service):
    p = Proj(service)
    requirements_dir = p.folder / "modules" / p.sys / "requirements"
    original = json.loads((requirements_dir / f"{p.r1}.json").read_text(encoding="utf-8"))
    odd = dict(original, id="SYRS-0099")
    odd["attributes"] = dict(original["attributes"], object_text="odd file name")
    write_json(requirements_dir / "SYRS\n0099.json", odd)
    commit = hand_commit(p.folder, "Odd file name")

    page = service.git_commits(p.pid)
    assert page["entries"][0]["commit"] == commit
    assert page["entries"][0]["changed_requirements"] == 1
    assert len(page["entries"]) == 8
    details = service.git_commit(p.pid, commit)
    assert details["changed_files"] == 1 and details["summary"]["requirements_added"] == 1
    ids = [r.id for r in service.commit_requirements(p.pid, commit, p.sys)]
    assert ids == [p.r1, p.r2, "SYRS-0099"]
    # The module scope still works with it present.
    assert service.git_commits(p.pid, module_id=p.sys)["entries"][0]["commit"] == commit

    # baseline_files reads blobs by object id: the odd name breaks nothing.
    service.create_baseline(p.pid, "odd")
    assert [r.id for r in service.baseline_requirements(p.pid, "odd", p.sys)] == [p.r1, p.r2, "SYRS-0099"]
    assert [m["id"] for m in service.get_baseline(p.pid, "odd")["modules"]] == [p.sys, p.sw]


# --- REST / WebSocket -------------------------------------------------------------------------


@pytest.fixture
def client(root, monkeypatch):
    monkeypatch.setenv("REQMAN_GIT", "on")
    monkeypatch.setenv("REQMAN_GIT_SQUASH_MINUTES", "0")
    monkeypatch.setenv("REQMAN_DATA_DIR", str(root))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(root.parent / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def rpc(ws, method: str, params: dict, request_id: int = 1) -> dict:
    ws.send_json({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    return ws.receive_json()


def _api_project(client) -> tuple[str, str, str]:
    pid = client.post("/projects", json={"name": "Api"}).json()["id"]
    mid = client.post(f"/projects/{pid}/modules", json={"name": "Mod", "prefix": "M"}).json()["id"]
    rid = client.post(
        f"/projects/{pid}/modules/{mid}/requirements", json={"attributes": {"object_text": "x"}}
    ).json()["id"]
    client.put(f"/projects/{pid}/modules/{mid}/requirements/{rid}", json={"attributes": {"object_text": "y"}})
    return pid, mid, rid


def test_rest_ws_parity_and_no_broadcast(client):
    pid, mid, rid = _api_project(client)
    base = f"/projects/{pid}/git/commits"
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as watcher:
        rest_list = client.get(base, params={"limit": 2})
        assert rest_list.status_code == 200, rest_list.text
        assert len(rest_list.json()["entries"]) == 2 and rest_list.json()["has_more"] is True
        assert rpc(ws, "commit.list", {"project_id": pid, "limit": 2})["result"] == rest_list.json()

        params = {"module_id": mid, "requirement_id": rid, "offset": 0, "limit": 5, "author": "test",
                  "text": "M-0001", "since": "2000-01-01", "until": "2099-12-31"}
        rest_scoped = client.get(base, params=params).json()
        assert len(rest_scoped["entries"]) == 2
        assert rpc(ws, "commit.list", {"project_id": pid, **params}, 2)["result"] == rest_scoped

        commit = rest_list.json()["entries"][0]["commit"]
        rest_details = client.get(f"{base}/{commit}")
        assert rest_details.status_code == 200
        assert rest_details.json()["summary"]["requirements_modified"] == 1
        assert rpc(ws, "commit.get", {"project_id": pid, "commit": commit}, 3)["result"] == rest_details.json()

        rest_snapshot = client.get(f"{base}/{commit}/snapshot")
        assert rest_snapshot.status_code == 200
        assert rpc(ws, "commit.snapshot", {"project_id": pid, "commit": commit}, 4)["result"] == rest_snapshot.json()

        rest_reqs = client.get(f"{base}/{commit}/modules/{mid}/requirements")
        assert rest_reqs.status_code == 200 and [r["id"] for r in rest_reqs.json()] == [rid]
        ws_reqs = rpc(ws, "commit.requirements", {"project_id": pid, "commit": commit, "module_id": mid}, 5)
        assert ws_reqs["result"] == rest_reqs.json()

        # Nothing was broadcast: the next event the watcher sees is the one of this mutation.
        client.post(f"/projects/{pid}/modules/{mid}/requirements", json={"attributes": {"object_text": "z"}})
        assert watcher.receive_json() == {"event": "changed", "project_id": pid}


def test_rest_and_ws_errors(client):
    pid, mid, rid = _api_project(client)
    base = f"/projects/{pid}/git/commits"
    commit = client.get(base).json()["entries"][0]["commit"]
    assert client.get("/projects/missing/git/commits").status_code == 404
    assert client.get(base, params={"module_id": "missing"}).status_code == 404
    assert client.get(base, params={"module_id": mid, "requirement_id": "M-9999"}).status_code == 404
    assert client.get(base, params={"requirement_id": rid}).status_code == 400
    assert client.get(base, params={"author": "a\tb"}).status_code == 400
    assert client.get(base, params={"text": "x" * 201}).status_code == 400
    assert client.get(base, params={"since": "2026-02-30"}).status_code == 400
    assert client.get(base, params={"until": "nope"}).status_code == 400
    assert client.get(base, params={"limit": 0}).status_code == 422
    assert client.get(base, params={"offset": -1}).status_code == 422
    assert client.get(base, params={"offset": MAX_COMMIT_OFFSET + 1}).status_code == 400
    for bad in ("HEAD", "HEAD~1", commit[:10], "0" * 40):
        assert client.get(f"{base}/{bad}").status_code == 404
        assert client.get(f"{base}/{bad}/snapshot").status_code == 404
        assert client.get(f"{base}/{bad}/modules/{mid}/requirements").status_code == 404
    assert client.get(f"{base}/{commit}/modules/missing/requirements").status_code == 404
    assert client.get(f"/projects/missing/git/commits/{commit}").status_code == 404

    with client.websocket_connect("/ws") as ws:
        assert rpc(ws, "commit.get", {"project_id": pid, "commit": "HEAD"})["error"]["code"] == -32001
        assert rpc(ws, "commit.snapshot", {"project_id": pid, "commit": "0" * 40})["error"]["code"] == -32001
        response = rpc(ws, "commit.requirements", {"project_id": pid, "commit": commit, "module_id": "missing"})
        assert response["error"]["code"] == -32001
        assert rpc(ws, "commit.list", {"project_id": pid, "module_id": "missing"})["error"]["code"] == -32001
        assert rpc(ws, "commit.list", {"project_id": pid, "limit": 0})["error"]["code"] == -32602
        assert rpc(ws, "commit.list", {"project_id": pid, "since": "x"})["error"]["code"] == -32602
        assert rpc(ws, "commit.list", {"project_id": pid, "requirement_id": rid})["error"]["code"] == -32602


def test_rest_newer_format_commit_is_409(client, root):
    pid, mid, _rid = _api_project(client)
    folder = root / pid
    project_file = folder / "project.json"
    current_text = project_file.read_text(encoding="utf-8")
    data = json.loads(current_text)
    data["format_version"] = PROJECT_FORMAT.current + 1
    write_json(project_file, data)
    newer = hand_commit(folder, "From the future")
    project_file.write_text(current_text, encoding="utf-8")
    hand_commit(folder, "Back")
    base = f"/projects/{pid}/git/commits/{newer}"
    assert client.get(base).status_code == 409
    assert client.get(f"{base}/snapshot").status_code == 409
    assert client.get(f"{base}/modules/{mid}/requirements").status_code == 409
    with client.websocket_connect("/ws") as ws:
        # NewerFormatError is a ValueError on the WebSocket.
        assert rpc(ws, "commit.get", {"project_id": pid, "commit": newer})["error"]["code"] == -32602


def test_rest_list_without_history_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("REQMAN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(tmp_path / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    client = TestClient(app_module.app)
    pid = client.post("/projects", json={"name": "Off"}).json()["id"]
    page = client.get(f"/projects/{pid}/git/commits").json()
    assert page["available"] is False and page["entries"] == []
    assert client.get(f"/projects/{pid}/git/commits/{'a' * 40}").status_code == 404


# ``_repo_lines``: the streaming git helper behind the commit list.


def _big_project(service: ProjectService) -> tuple[Path, object]:
    """A project whose HEAD holds a file far larger than a pipe buffer (git blocks writing it)."""
    proj = Small(service)
    folder = service.store.project_path(proj.pid)
    (folder / "big.txt").write_text("".join(f"line {n:07d}\n" for n in range(200_000)), encoding="ascii")
    hand_commit(folder, "Big file")
    info = service.history.detect(folder)
    assert info.git_dir is not None and info.work_tree is not None
    return folder, info


def _recording_popen(monkeypatch) -> list[subprocess.Popen]:
    import reqman.git_repo as git_repo_module

    started: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(git_repo_module.subprocess, "Popen", popen)
    return started


def test_repo_lines_close_kills_git(service, monkeypatch):
    _folder, info = _big_project(service)
    started = _recording_popen(monkeypatch)
    lines = service.history._repo_lines(info, ["show", "HEAD:big.txt"], read_only=True)
    assert next(lines) == "line 0000000"
    assert len(started) == 1 and started[0].poll() is None, "git should still be writing"
    lines.close()
    monkeypatch.undo()
    assert started[0].returncode is not None, "git left running after close()"
    assert started[0].returncode != 0, "git was killed, not finished"


def test_repo_lines_timeout_raises(service):
    _folder, info = _big_project(service)
    with pytest.raises(GitError, match="timed out"):
        list(service.history._repo_lines(info, ["show", "HEAD:big.txt"], read_only=True, timeout=0.0001))


def test_repo_lines_failure_reports_stderr(service):
    _folder, info = _big_project(service)
    with pytest.raises(GitError) as caught:
        list(service.history._repo_lines(info, ["log", "no-such-revision-xyz"], read_only=True))
    message = str(caught.value)
    assert "failed" in message and "no-such-revision-xyz" in message


def test_repo_lines_timer_start_failure_kills_git(service, monkeypatch):
    import threading

    _folder, info = _big_project(service)
    started = _recording_popen(monkeypatch)

    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Timer, "start", refuse)
    with pytest.raises(GitError, match="can't start new thread"):
        list(service.history._repo_lines(info, ["show", "HEAD:big.txt"], read_only=True))
    monkeypatch.undo()
    assert len(started) == 1 and started[0].returncode is not None, "git left running"
