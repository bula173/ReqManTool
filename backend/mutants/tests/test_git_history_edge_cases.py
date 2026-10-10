"""Edge cases of per-project git history: enable variants, squash blockers,
author identity, failures, concurrency, history reading and the REST /
WebSocket error contracts.

Same rules as ``test_git_history.py`` (whose helpers and fixtures are reused):
every repository lives in pytest's ``tmp_path``, ``conftest.py`` isolates git
from the user's configuration, and ``git()`` refuses to run in the checkout.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from reqman.formats import PROJECT_FORMAT
from reqman.git_repo import GitSettings
from reqman.history import session_of
from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore
from test_git_history import (  # noqa: F401 - fixtures are used by name
    CHECKOUT,
    Clock,
    _project_with_requirement,
    client,
    clock,
    commits,
    git,
    make_service,
    root,
    service,
)


def git_input(path: Path, data: bytes, *args: str) -> str:
    """``git`` with stdin, under the same never-in-the-checkout guard as ``git()``."""
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    result = subprocess.run(["git", *args], cwd=path, input=data, capture_output=True, env=env, check=False)
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout.decode()


def req_path(module_id: str, req_id: str) -> str:
    return f"modules/{module_id}/requirements/{req_id}.json"


def outer_repo(tmp_path: Path) -> Path:
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q", "-b", "main")
    (outer / "README").write_text("outer\n")
    git(outer, "add", "README")
    git(outer, "commit", "-q", "-m", "outer initial")
    return outer


def rpc(ws, method: str, params: dict, request_id: int = 1) -> dict:
    ws.send_json({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    return ws.receive_json()


# --- enable variants ---------------------------------------------------------------------


def test_enable_on_unborn_repo_on_another_branch(root, clock):
    project = ProjectService(ProjectStore(root)).create_project("Unborn")
    folder = root / project.id
    git(folder, "init", "-q", "-b", "main")
    service = make_service(root, clock)
    status = service.git_status(project.id)
    assert status["state"] == "paused" and status["branch"] == "main"
    assert status["head"] is None and status["can_enable"] is True

    status = service.enable_history(project.id)
    assert status["state"] == "ready" and status["branch"] == "current"
    assert status["pending_changes"] == 0 and status["last_error"] is None
    assert git(folder, "symbolic-ref", "HEAD").strip() == "refs/heads/current"
    assert commits(folder) == ["Enable history (git) for project 'Unborn'"]
    assert git(folder, "for-each-ref", "--format=%(refname)").split() == ["refs/heads/current"]
    assert "project.json" in git(folder, "ls-files")


def test_enable_on_ready_repo_without_commits(root, clock):
    project = ProjectService(ProjectStore(root)).create_project("Empty")
    folder = root / project.id
    git(folder, "init", "-q", "-b", "current")
    service = make_service(root, clock)
    status = service.git_status(project.id)
    assert status["state"] == "ready" and status["head"] is None
    assert status["can_enable"] is True and status["pending_changes"] > 0

    status = service.enable_history(project.id)
    assert status["can_enable"] is False and status["pending_changes"] == 0
    assert commits(folder) == ["Enable history (git) for project 'Empty'"]
    assert session_of(git(folder, "log", "-1", "--format=%B")) is None  # never squashed into
    assert "*.tmp" in (folder / ".git" / "info" / "exclude").read_text()


def test_mutation_on_ready_repo_without_commits_commits_all_files(root, clock):
    plain = ProjectService(ProjectStore(root))
    project = plain.create_project("Empty")
    folder = root / project.id
    git(folder, "init", "-q", "-b", "current")
    service = make_service(root, clock)
    module = service.create_module(project.id, "Mod", "M")
    assert len(commits(folder)) == 1
    assert git(folder, "status", "--porcelain") == ""
    assert {"project.json", f"modules/{module.id}/module.json"} <= set(git(folder, "ls-files").splitlines())
    assert service.git_status(project.id)["last_error"] is None


# --- squash blockers -------------------------------------------------------------------


def _session_then_update(service, root, clock, prepare) -> Path:
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    assert len(commits(folder)) == 2
    prepare(folder)
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert git(folder, "status", "--porcelain", "--", "project.json", "modules", "links") == ""
    return folder


def test_remote_tracking_ref_blocks_squash(service, root, clock):
    def prepare(folder):
        git(folder, "update-ref", "refs/remotes/origin/current", "HEAD")

    folder = _session_then_update(service, root, clock, prepare)
    assert len(commits(folder)) == 3
    assert git(folder, "rev-parse", "refs/remotes/origin/current") == git(folder, "rev-parse", "HEAD~1")


def test_stash_blocks_squash(service, root, clock):
    def prepare(folder):
        (folder / "notes.txt").write_text("scratch\n")
        git(folder, "stash", "push", "-q", "-u")
        assert not (folder / "notes.txt").exists()

    folder = _session_then_update(service, root, clock, prepare)
    assert len(commits(folder)) == 3
    assert len(git(folder, "stash", "list").splitlines()) == 1
    git(folder, "stash", "pop", "-q")
    assert (folder / "notes.txt").read_text() == "scratch\n"


@pytest.mark.parametrize("kind", ["branch", "lightweight tag"])
def test_other_branch_or_lightweight_tag_blocks_squash(service, root, clock, kind):
    def prepare(folder):
        if kind == "branch":
            git(folder, "branch", "feature")
        else:
            git(folder, "tag", "light")

    folder = _session_then_update(service, root, clock, prepare)
    assert len(commits(folder)) == 3
    ref = "feature" if kind == "branch" else "light"
    assert git(folder, "rev-parse", ref) == git(folder, "rev-parse", "HEAD~1")


def test_ref_on_an_older_commit_does_not_block_squash(service, root, clock):
    def prepare(folder):
        git(folder, "tag", "start", "HEAD~1")
        git(folder, "branch", "old", "HEAD~1")

    folder = _session_then_update(service, root, clock, prepare)
    assert len(commits(folder)) == 2
    assert "Status Draft → Reviewed" not in git(folder, "log", "-1", "--format=%s")  # session described as a whole
    assert "SYRS-0001 added" in git(folder, "log", "-1", "--format=%B")


def test_squash_window_zero_never_squashes(root, clock):
    service = make_service(root, clock, squash_window_seconds=0)
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    assert len(commits(folder)) == 4  # create, attribute, module, requirement
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    service.update_requirement(project.id, module.id, req.id, {"status": "Draft"})
    assert commits(folder)[:2] == ["SYRS-0001: Status Reviewed → Draft", "SYRS-0001: Status Draft → Reviewed"]
    assert service.git_status(project.id)["squash_window_minutes"] == 0


@pytest.mark.parametrize(
    ("raw", "minutes"), [("0", 0.0), ("0.5", 0.5), ("abc", 10.0), ("inf", 10.0), ("nan", 10.0), ("", 10.0)]
)
def test_squash_minutes_setting(raw, minutes):
    assert GitSettings.from_env({"REQMAN_GIT_SQUASH_MINUTES": raw}).squash_window_seconds == minutes * 60


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no", "disabled", " off "])
def test_git_off_values(value):
    assert GitSettings.from_env({"REQMAN_GIT": value}).enabled is False


def test_same_name_other_email_starts_a_new_commit(root, clock):
    first = make_service(root, clock)
    project, module, req = _project_with_requirement(first)
    second = make_service(root, clock, author_name="Test Author", author_email="else@example.com")
    second.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    folder = root / project.id
    assert len(commits(folder)) == 3
    assert git(folder, "log", "-1", "--format=%an <%ae>").strip() == "Test Author <else@example.com>"


def test_merge_commit_with_session_trailer_is_never_amended(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    session = session_of(git(folder, "log", "-1", "--format=%B"))
    git(folder, "checkout", "-q", "-b", "side")
    (folder / "notes.txt").write_text("side\n")
    git(folder, "add", "notes.txt")
    git(folder, "commit", "-q", "-m", "side work")
    git(folder, "checkout", "-q", "current")
    git(folder, "merge", "-q", "--no-ff", "side", "-m", f"Merge side\n\nReqMan-Session: {session}")
    git(folder, "branch", "-q", "-D", "side")  # only the two parents can block the squash now
    merge = git(folder, "rev-parse", "HEAD").strip()

    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert git(folder, "rev-parse", "HEAD~1").strip() == merge
    assert len(git(folder, "rev-list", "--parents", "-1", merge).split()) == 3
    assert git(folder, "log", "-1", "--format=%s").strip() == "SYRS-0001: Status Draft → Reviewed"


# --- author identity -----------------------------------------------------------------


@pytest.fixture
def no_env_identity(monkeypatch):
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(name, raising=False)


def _plain_project_with_repo(root: Path, *config: tuple[str, str]):
    project = ProjectService(ProjectStore(root)).create_project("Who")
    folder = root / project.id
    git(folder, "init", "-q", "-b", "current")
    for key, value in config:
        git(folder, "config", key, value)
    return project, folder


def test_identity_partial_setting_is_completed_by_git(root, clock):
    service = make_service(root, clock, author_name="Custom")
    project = service.create_project("Alpha")
    folder = root / project.id
    assert git(folder, "log", "-1", "--format=%an <%ae>|%cn <%ce>").strip() == (
        "Custom <author@example.com>|Custom <author@example.com>"
    )
    assert service.git_status(project.id)["author"] == {"name": "Custom", "email": "author@example.com"}


def test_identity_from_repository_config(root, clock, no_env_identity):
    project, folder = _plain_project_with_repo(root, ("user.name", "Repo User"), ("user.email", "repo@example.com"))
    service = make_service(root, clock)
    service.enable_history(project.id)
    assert git(folder, "log", "-1", "--format=%an <%ae>").strip() == "Repo User <repo@example.com>"
    assert service.git_status(project.id)["author"] == {"name": "Repo User", "email": "repo@example.com"}


def test_identity_falls_back_to_login_when_git_var_fails(root, clock, no_env_identity, monkeypatch):
    project, folder = _plain_project_with_repo(root, ("user.useConfigOnly", "true"))
    probe = subprocess.run(["git", "var", "GIT_AUTHOR_IDENT"], cwd=folder, capture_output=True, check=False)
    assert probe.returncode != 0  # git itself has no identity here
    monkeypatch.setattr("reqman.git_repo.getpass.getuser", lambda: "tester")
    service = make_service(root, clock)
    service.enable_history(project.id)
    assert git(folder, "log", "-1", "--format=%an <%ae>|%cn <%ce>").strip() == (
        "tester <tester@localhost>|tester <tester@localhost>"
    )
    assert service.git_status(project.id)["last_error"] is None


def test_identity_configured_email_with_login_name_when_git_var_fails(root, clock, no_env_identity, monkeypatch):
    project, folder = _plain_project_with_repo(root, ("user.useConfigOnly", "true"))
    monkeypatch.setattr("reqman.git_repo.getpass.getuser", lambda: "tester")
    service = make_service(root, clock, author_email="me@example.com")
    service.enable_history(project.id)
    assert git(folder, "log", "-1", "--format=%an <%ae>").strip() == "tester <me@example.com>"


def test_identity_falls_back_to_reqman_without_login_name(root, clock, no_env_identity, monkeypatch):
    project, folder = _plain_project_with_repo(root, ("user.useConfigOnly", "true"))

    def no_login():
        raise OSError("no login name")

    monkeypatch.setattr("reqman.git_repo.getpass.getuser", no_login)
    make_service(root, clock).enable_history(project.id)
    assert git(folder, "log", "-1", "--format=%an <%ae>").strip() == "reqman <reqman@localhost>"


# --- concurrency -------------------------------------------------------------------------


def test_threaded_mutations_on_one_project_are_all_committed(service, root, clock):
    project, module, first = _project_with_requirement(service)
    reqs = [first] + [service.add_requirement(project.id, module.id, {"object_text": f"R{i}"}) for i in range(3)]
    folder = root / project.id
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(reqs))

    def work(req_id: str) -> None:
        try:
            barrier.wait()
            for round_ in range(2):
                service.update_requirement(project.id, module.id, req_id, {"object_text": f"{req_id} v{round_}"})
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(r.id,)) for r in reqs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors
    assert git(folder, "status", "--porcelain") == ""
    assert not (folder / ".git" / "index.lock").exists()
    assert service.git_status(project.id)["last_error"] is None
    git(folder, "fsck", "--no-progress", "--no-dangling")
    for r in reqs:
        committed = json.loads(git(folder, "show", f"HEAD:{req_path(module.id, r.id)}"))
        assert committed["attributes"]["object_text"] == f"{r.id} v1"
    assert len(commits(folder)) == 2  # one session, squashed


# --- failures ---------------------------------------------------------------------------


def test_merge_in_progress_pauses_auto_commit_then_recovers(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    git(folder, "checkout", "-q", "-b", "side")
    (folder / "notes.txt").write_text("side\n")
    git(folder, "add", "notes.txt")
    git(folder, "commit", "-q", "-m", "side work")
    git(folder, "checkout", "-q", "current")
    git(folder, "merge", "-q", "--no-ff", "--no-commit", "side")
    head_before = git(folder, "rev-parse", "HEAD")

    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert service.get_requirement(project.id, module.id, req.id).attributes["status"] == "Reviewed"
    assert git(folder, "rev-parse", "HEAD") == head_before
    assert git(folder, "diff", "--cached", "--name-only").strip() == "notes.txt"  # nothing staged by us
    status = service.git_status(project.id)
    assert status["state"] == "paused" and "A merge is in progress" in status["message"]
    assert status["last_error"] is None
    assert status["pending_changes"] >= 1

    git(folder, "merge", "--quit")
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Again"})
    assert service.git_status(project.id)["last_error"] is None
    body = git(folder, "log", "-1", "--format=%B")
    assert "Status Draft → Reviewed" in body and "ObjectText Hello → Again" in body
    assert git(folder, "status", "--porcelain", "--", "project.json", "modules", "links") == ""
    # The user's merge leftovers stay staged and are not part of the auto-commit.
    assert "notes.txt" not in git(folder, "show", "--name-only", "--format=", "HEAD")
    assert git(folder, "diff", "--cached", "--name-only").strip() == "notes.txt"


def _dubious_git(tmp_path: Path) -> Path:
    script = tmp_path / "bin" / "git-dubious"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "--show-toplevel" ]; then\n'
        "    echo \"fatal: detected dubious ownership in repository at '$PWD'\" >&2\n"
        "    exit 128\n"
        "  fi\n"
        "done\n"
        'exec git "$@"\n'
    )
    script.chmod(0o755)
    return script


def test_dubious_ownership_is_an_error_state_that_never_blocks_saving(root, clock, tmp_path):
    service = make_service(root, clock)
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    head_before = git(folder, "rev-parse", "HEAD")
    blocked = make_service(root, clock, executable=str(_dubious_git(tmp_path)))

    status = blocked.git_status(project.id)
    assert status["state"] == "error" and "dubious ownership" in status["message"]
    assert status["can_enable"] is False
    blocked.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert blocked.get_requirement(project.id, module.id, req.id).attributes["status"] == "Reviewed"
    assert git(folder, "rev-parse", "HEAD") == head_before
    history = blocked.requirement_history(project.id, module.id, req.id)
    assert history["available"] is False and "dubious ownership" in history["reason"]
    with pytest.raises(ValueError) as excinfo:
        blocked.enable_history(project.id)
    assert not isinstance(excinfo.value, ConflictError)
    new = blocked.create_project("Fresh")
    assert not (root / new.id / ".git").exists()

    # A working git commits what was saved meanwhile.
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Later"})
    assert "Status Draft → Reviewed" in git(folder, "log", "-1", "--format=%B")
    assert git(folder, "status", "--porcelain") == ""


# --- nested ------------------------------------------------------------------------------


def test_many_mutations_inside_an_enclosing_repo_leave_it_untouched(tmp_path, clock):
    outer = outer_repo(tmp_path)
    (outer / "staged.txt").write_text("staged by the user\n")
    git(outer, "add", "staged.txt")
    git_dir = outer / ".git"
    snapshot = {
        "head": git(outer, "rev-parse", "HEAD"),
        "refs": git(outer, "for-each-ref"),
        "index": (git_dir / "index").read_bytes(),
        "config": (git_dir / "config").read_bytes(),
        "objects": git(outer, "count-objects", "-v"),
    }
    service = make_service(outer / "data", clock)
    project, module, req = _project_with_requirement(service)
    other = service.create_module(project.id, "Software", "SRS")
    target = service.add_requirement(project.id, other.id, {"object_text": "Target"})
    config = service.create_link_configuration(project.id, "Satisfies", module.id, other.id)
    for step in (
        lambda: service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"}),
        lambda: service.link_requirements(project.id, config.id, req.id, target.id),
        lambda: service.create_view(project.id, "Main", ["object_text"]),
        lambda: service.reorder_module(project.id, other.id, "up"),
        lambda: service.import_csv(project.id, module.id, "ObjectText\nImported\n"),
        lambda: service.remove_requirement(project.id, other.id, target.id),
        lambda: service.purge_module(project.id, other.id),
    ):
        clock.offset += 3600
        step()
    with pytest.raises(ConflictError):
        service.enable_history(project.id)
    sub = service.create_project("Sub", parent_project_id=project.id)

    assert git(outer, "rev-parse", "HEAD") == snapshot["head"]
    assert git(outer, "for-each-ref") == snapshot["refs"]
    assert (git_dir / "index").read_bytes() == snapshot["index"]
    assert (git_dir / "config").read_bytes() == snapshot["config"]
    assert git(outer, "count-objects", "-v") == snapshot["objects"]
    assert git(outer, "diff", "--cached", "--name-only").strip() == "staged.txt"
    assert not list((outer / "data").rglob(".git"))
    assert service.git_status(sub.id)["state"] == "nested"


# --- history -------------------------------------------------------------------------


def test_history_limit_is_200_and_read_at_call_time():
    import reqman.git_repo as git_repo

    assert git_repo.HISTORY_LIMIT == 200
    # This test checks implementation detail (default parameter tuple).
    # Mutmut may mutate the default value; this test verifies the expected default.
    defaults = git_repo.GitHistory.requirement_history.__defaults__
    assert defaults is not None, "requirement_history should have a default parameter"
    assert defaults[0] is None, "default limit should be None"


def test_history_is_capped_at_history_limit_entries(service, root, monkeypatch):
    limit = 5
    monkeypatch.setattr("reqman.git_repo.HISTORY_LIMIT", limit)
    project, module, a = _project_with_requirement(service)
    b = service.add_requirement(project.id, module.id, {"object_text": "Other"})
    folder = root / project.id
    path_a, path_b = req_path(module.id, a.id), req_path(module.id, b.id)
    base_a, base_b = json.loads((folder / path_a).read_text()), json.loads((folder / path_b).read_text())
    stream = bytearray()

    def blob(path: str, base: dict, index: int) -> None:
        content = json.dumps({**base, "attributes": {**base["attributes"], "object_text": f"v{index}"}}, indent=2)
        data = (content + "\n").encode()
        stream.extend(f"M 100644 inline {path}\ndata {len(data)}\n".encode() + data + b"\n")

    head = git(folder, "rev-parse", "HEAD").strip()
    for i in range(limit):
        message = f"edit {i}".encode()
        stream.extend(b"commit refs/heads/current\n")
        stream.extend(f"committer Test Author <author@example.com> {1_700_000_000 + i} +0000\n".encode())
        stream.extend(f"data {len(message)}\n".encode() + message + b"\n")
        if i == 0:
            stream.extend(f"from {head}\n".encode())
        blob(path_a, base_a, i)
        if i < limit - 1:
            blob(path_b, base_b, i)
        stream.extend(b"\n")
    git_input(folder, bytes(stream), "fast-import", "--quiet")
    git(folder, "reset", "-q", "--hard")

    # a: limit + 1 revisions -> the `limit` newest are listed, the oldest is only the baseline.
    history = service.requirement_history(project.id, module.id, a.id)
    assert history["truncated"] is True and len(history["entries"]) == limit
    assert history["entries"][0]["subject"] == f"edit {limit - 1}"
    assert history["entries"][-1]["subject"] == "edit 0"
    assert history["entries"][-1]["change"] == "modified"
    assert history["blame"]["object_text"]["subject"] == f"edit {limit - 1}"
    # b: exactly `limit` revisions -> complete, the oldest is its creation.
    history = service.requirement_history(project.id, module.id, b.id)
    assert history["truncated"] is False and len(history["entries"]) == limit
    assert history["entries"][-1]["change"] == "added"
    assert history["entries"][0]["subject"] == f"edit {limit - 2}"


def test_history_skips_an_unreadable_revision_committed_by_hand(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    path = folder / req_path(module.id, req.id)
    good = path.read_text()
    path.write_text("{ broken by hand")
    git(folder, "commit", "-q", "-am", "break it")
    path.write_text(good.replace('"Draft"', '"Reviewed"'))
    git(folder, "commit", "-q", "-am", "fix it")

    history = service.requirement_history(project.id, module.id, req.id)
    assert [(e["subject"], e["change"], e["unreadable"]) for e in history["entries"]] == [
        ("fix it", "modified", False),
        ("break it", "modified", True),
        (history["entries"][2]["subject"], "added", False),
    ]
    assert history["entries"][0]["diffs"] == [
        {"kind": "attribute", "attribute_id": "status", "old": "Draft", "new": "Reviewed"}
    ]
    assert history["blame"]["status"]["subject"] == "fix it"


def test_history_through_a_merged_branch(service, root, clock):
    project, module, a = _project_with_requirement(service)
    b = service.add_requirement(project.id, module.id, {"object_text": "B"})
    folder = root / project.id
    git(folder, "checkout", "-q", "-b", "side")
    path = folder / req_path(module.id, a.id)
    path.write_text(path.read_text().replace('"Draft"', '"Reviewed"'))
    git(folder, "commit", "-q", "-am", "side edit")
    git(folder, "checkout", "-q", "current")
    clock.offset = 3600
    service.update_requirement(project.id, module.id, b.id, {"object_text": "B2"})
    git(folder, "merge", "-q", "--no-ff", "side", "-m", "Merge side")

    # First-parent history: the merge is listed with what it brought into `current`.
    history = service.requirement_history(project.id, module.id, a.id)
    assert [e["change"] for e in history["entries"]] == ["modified", "added"]
    assert history["entries"][0]["subject"] == "Merge side"
    assert history["entries"][0]["diffs"] == [
        {"kind": "attribute", "attribute_id": "status", "old": "Draft", "new": "Reviewed"}
    ]
    assert history["blame"]["status"]["subject"] == "Merge side"
    assert history["uncommitted"] is False

    clock.offset = 0  # within the window, but HEAD is a merge without a trailer
    service.update_requirement(project.id, module.id, a.id, {"status": "Draft"})
    assert git(folder, "log", "-2", "--format=%s").splitlines() == [
        "SYRS-0001: Status Reviewed → Draft",
        "Merge side",
    ]
    history = service.requirement_history(project.id, module.id, a.id)
    assert history["entries"][0]["diffs"][0]["new"] == "Draft"


def test_history_across_a_merge_of_diverged_branches_diffs_each_commit_against_its_first_parent(
    service, root, clock, monkeypatch
):
    project, module, a = _project_with_requirement(service)
    folder = root / project.id
    path = folder / req_path(module.id, a.id)
    base = path.read_text()
    git(folder, "checkout", "-q", "-b", "side")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2030-01-01T00:00:02")
    path.write_text(base.replace('"Hello"', '"Side text"'))
    git(folder, "commit", "-q", "-am", "side edit")
    git(folder, "checkout", "-q", "current")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2030-01-01T00:00:01")
    path.write_text(base.replace('"Draft"', '"Reviewed"'))
    git(folder, "commit", "-q", "-am", "main edit")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2030-01-01T00:00:03")
    git(folder, "merge", "-q", "--no-ff", "side", check=False)
    path.write_text(base.replace('"Draft"', '"Reviewed"').replace('"Hello"', '"Side text"'))
    git(folder, "commit", "-q", "-am", "Merge side")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    history = service.requirement_history(project.id, module.id, a.id)
    # Commits of `current` only, each against its first parent: the side branch's
    # commit is part of the merge's entry, and no entry has a phantom diff.
    assert [(e["subject"], e["change"]) for e in history["entries"]][:2] == [
        ("Merge side", "modified"),
        ("main edit", "modified"),
    ]
    assert history["entries"][2]["change"] == "added" and len(history["entries"]) == 3
    assert history["entries"][0]["diffs"] == [
        {"kind": "attribute", "attribute_id": "object_text", "old": "Hello", "new": "Side text"}
    ]
    assert history["entries"][1]["diffs"] == [
        {"kind": "attribute", "attribute_id": "status", "old": "Draft", "new": "Reviewed"}
    ]
    assert history["blame"]["status"]["subject"] == "main edit"
    assert history["blame"]["object_text"]["subject"] == "Merge side"


def test_history_of_a_removed_requirement(service, root, clock):
    project, module, req = _project_with_requirement(service)
    clock.offset = 3600
    service.remove_requirement(project.id, module.id, req.id)
    with pytest.raises(FileNotFoundError):
        service.requirement_history(project.id, module.id, req.id)
    # The file history itself still ends with the removal.
    history = service.history.requirement_history(root / project.id, req_path(module.id, req.id), req.id)
    assert history["available"] is True
    assert [e["change"] for e in history["entries"]] == ["removed", "added"]
    assert history["blame"] == {}


def test_history_does_not_follow_into_another_requirement(service, root, clock):
    project, module, old = _project_with_requirement(service)
    folder = root / project.id
    clock.offset = 3600
    service.remove_requirement(project.id, module.id, old.id)
    clock.offset = 0  # right after the removal: same session commit
    new = service.add_requirement(project.id, module.id, {"object_text": "Hello", "status": "Draft"})
    assert new.id != old.id
    # git sees the removal + addition in the session commit as a rename.
    assert git(folder, "log", "-1", "--format=%s").strip() == "SYRS-0001 removed; SYRS-0002 added"
    assert "R" in git(folder, "show", "--name-status", "--format=", "-M", "HEAD", "--", "modules")[:1]
    first_commit_of_old = git(folder, "log", "--format=%H", "--", req_path(module.id, old.id)).split()[-1]

    history = service.requirement_history(project.id, module.id, new.id)
    assert all(e["commit"] != first_commit_of_old for e in history["entries"])
    assert all(diff.get("old") != old.id for e in history["entries"] for diff in e["diffs"])


def test_history_of_a_requirement_that_git_sees_as_a_rename_lists_its_creation(service, root, clock):
    project, module, old = _project_with_requirement(service)
    folder = root / project.id
    clock.offset = 3600
    service.remove_requirement(project.id, module.id, old.id)
    clock.offset = 0  # right after the removal: same session commit
    new = service.add_requirement(project.id, module.id, {"object_text": "Hello", "status": "Draft"})
    history = service.requirement_history(project.id, module.id, new.id)
    head = git(folder, "rev-parse", "HEAD").strip()
    assert [(e["commit"], e["change"]) for e in history["entries"]] == [(head, "added")]
    assert history["blame"]["object_text"]["commit"] == head


def test_history_while_paused_reports_uncommitted_changes(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    git(folder, "checkout", "-q", "-b", "elsewhere")
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert service.git_status(project.id)["state"] == "paused"
    history = service.requirement_history(project.id, module.id, req.id)
    assert history["available"] is True and history["uncommitted"] is True
    assert [e["change"] for e in history["entries"]] == ["added"]


def test_history_on_unborn_repo_is_available_and_uncommitted(root, clock):
    plain = ProjectService(ProjectStore(root))
    project = plain.create_project("Unborn")
    module = plain.create_module(project.id, "Mod", "M")
    req = plain.add_requirement(project.id, module.id, {"object_text": "x"})
    git(root / project.id, "init", "-q", "-b", "current")
    history = make_service(root, clock).requirement_history(project.id, module.id, req.id)
    assert history == {
        "available": True,
        "reason": None,
        "entries": [],
        "blame": {},
        "truncated": False,
        "created": None,
        "modified": None,
        "uncommitted": True,
    }


# --- REST / WebSocket contracts ---------------------------------------------------------


def _client_for(data_dir: Path, config_dir: Path, monkeypatch, **env: str) -> TestClient:
    monkeypatch.setenv("REQMAN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(config_dir / "config.json"))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def _create_with_requirement(client: TestClient) -> tuple[str, str, str]:
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "M"}).json()["id"]
    req_id = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "x"}}
    ).json()["id"]
    return project_id, module_id, req_id


def test_api_enable_nested_is_409_and_ws_minus_32602_without_broadcast(tmp_path, monkeypatch):
    outer = outer_repo(tmp_path)
    client = _client_for(outer / "data", tmp_path, monkeypatch, REQMAN_GIT="on")
    project_id, module_id, req_id = _create_with_requirement(client)
    assert client.get(f"/projects/{project_id}/git").json()["state"] == "nested"

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as other:
        response = client.post(f"/projects/{project_id}/git/enable")
        assert response.status_code == 409 and "inside the git repository" in response.json()["detail"]
        reply = rpc(ws, "project.git_enable", {"id": project_id})
        assert reply["error"]["code"] == -32602 and "inside the git repository" in reply["error"]["message"]
        # Neither failure broadcast "changed": the next message is the reply to this request.
        assert rpc(other, "project.git_status", {"id": project_id}, 7)["id"] == 7
    history = client.get(f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}/history").json()
    assert history["available"] is False
    assert git(outer, "rev-list", "--count", "--all").strip() == "1"


def test_api_enable_with_git_off_is_400(root, monkeypatch):
    client = _client_for(root, root.parent, monkeypatch)  # conftest: REQMAN_GIT=off
    project_id, module_id, req_id = _create_with_requirement(client)
    assert not (root / project_id / ".git").exists()
    status = client.get(f"/projects/{project_id}/git").json()
    assert status["state"] == "off" and status["git_version"] is None
    response = client.post(f"/projects/{project_id}/git/enable")
    assert response.status_code == 400 and "REQMAN_GIT=off" in response.json()["detail"]
    with client.websocket_connect("/ws") as ws:
        assert rpc(ws, "project.git_enable", {"id": project_id})["error"]["code"] == -32602
    history = client.get(f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}/history").json()
    assert history["available"] is False and "REQMAN_GIT=off" in history["reason"]
    assert not (root / project_id / ".git").exists()


def test_api_enable_with_git_missing_is_400(root, monkeypatch):
    client = _client_for(
        root, root.parent, monkeypatch, REQMAN_GIT="on", REQMAN_GIT_EXECUTABLE=str(root.parent / "no-such-git")
    )
    project_id, _module_id, _req_id = _create_with_requirement(client)
    assert client.get(f"/projects/{project_id}/git").json()["state"] == "git_missing"
    response = client.post(f"/projects/{project_id}/git/enable")
    assert response.status_code == 400 and "not found" in response.json()["detail"]
    with client.websocket_connect("/ws") as ws:
        assert rpc(ws, "project.git_enable", {"id": project_id})["error"]["code"] == -32602
    assert not (root / project_id / ".git").exists()


def test_ws_history_and_status_of_missing_resources(client):
    project_id, module_id, _req_id = _create_with_requirement(client)
    with client.websocket_connect("/ws") as ws:
        missing_req = rpc(ws, "requirement.history", {"project_id": project_id, "module_id": module_id, "id": "M-9999"})
        assert missing_req["error"]["code"] == -32001
        missing_module = rpc(ws, "requirement.history", {"project_id": project_id, "module_id": "nope", "id": "M-0001"})
        assert missing_module["error"]["code"] == -32001
        assert rpc(ws, "project.git_status", {"id": "missing"})["error"]["code"] == -32001
        assert rpc(ws, "project.git_enable", {"id": "missing"})["error"]["code"] == -32001
        assert rpc(ws, "requirement.history", {"project_id": project_id})["error"]["code"] == -32602
    assert client.post("/projects/missing/git/enable").status_code == 404
    assert client.get(f"/projects/missing/modules/{module_id}/requirements/M-0001/history").status_code == 404


@pytest.mark.parametrize(
    ("damage", "rest_status"),
    [("newer", 409), ("malformed", 400)],
)
def test_stub_project_is_refused_by_status_enable_and_history(client, root, damage, rest_status):
    project_id, module_id, req_id = _create_with_requirement(client)
    plain = ProjectService(ProjectStore(root)).create_project("Plain")  # no repository
    folder = root / project_id
    head_before = git(folder, "rev-parse", "HEAD")
    for pid in (project_id, plain.id):
        path = root / pid / "project.json"
        if damage == "newer":
            data = json.loads(path.read_text())
            data["format_version"] = PROJECT_FORMAT.current + 1
            path.write_text(json.dumps(data, indent=2) + "\n")
        else:
            path.write_text("{ not json")
    snapshot = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}

    history_url = f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}/history"
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as other:
        for pid in (project_id, plain.id):
            assert client.get(f"/projects/{pid}/git").status_code == rest_status
            assert client.post(f"/projects/{pid}/git/enable").status_code == rest_status
            assert rpc(ws, "project.git_status", {"id": pid})["error"]["code"] == -32602
            assert rpc(ws, "project.git_enable", {"id": pid})["error"]["code"] == -32602
        assert client.get(history_url).status_code == rest_status
        reply = rpc(ws, "requirement.history", {"project_id": project_id, "module_id": module_id, "id": req_id})
        assert reply["error"]["code"] == -32602
        assert rpc(other, "project.list", {}, 9)["id"] == 9  # no "changed" broadcast came first

    assert git(folder, "rev-parse", "HEAD") == head_before
    assert git(folder, "status", "--porcelain", "--", "modules", "links") == ""
    assert not (root / plain.id / ".git").exists()
    assert {p: p.read_bytes() for p in snapshot} == snapshot


def test_rest_enable_broadcasts_changed_to_websocket_clients(client, root):
    plain = ProjectService(ProjectStore(root)).create_project("Plain")
    with client.websocket_connect("/ws") as ws:
        response = client.post(f"/projects/{plain.id}/git/enable")
        assert response.status_code == 200 and response.json()["state"] == "ready"
        assert ws.receive_json() == {"event": "changed", "project_id": plain.id}
        status = rpc(ws, "project.git_status", {"id": plain.id})["result"]
    assert status == client.get(f"/projects/{plain.id}/git").json()
    assert commits(root / plain.id) == ["Enable history (git) for project 'Plain'"]


def test_data_dir_switch_keeps_git_history_on(client, root, tmp_path):
    other = tmp_path / "other"
    response = client.post("/config", json={"data_dir": str(other)})
    assert response.status_code == 200
    project_id, module_id, req_id = _create_with_requirement(client)
    folder = other / project_id
    assert (folder / ".git").is_dir()
    assert client.get(f"/projects/{project_id}/git").json()["state"] == "ready"
    assert commits(folder)[-1] == "Create project 'Api'"
    assert git(folder, "status", "--porcelain") == ""
    history = client.get(f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}/history").json()
    assert history["available"] is True and history["entries"][0]["change"] == "added"
    assert not any(root.iterdir())
