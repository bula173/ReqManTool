"""Per-project git history: operations in progress and unresolved conflicts pause
auto-commit; a symlinked ``.git`` is foreign; fsmonitor never runs; AppleDouble
(``._*``) files are no data files; git error messages; the hooks directory.

Same rules as ``test_git_history.py`` (whose helpers and fixtures are reused):
every repository lives in pytest's ``tmp_path``, ``conftest.py`` isolates git
from the user's configuration, and every git call refuses to run in the
checkout. Every git command of these tests has a timeout, and so has every git
command ReqManTool runs (``_TIMEOUT_SECONDS`` is lowered).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reqman import git_repo
from reqman.git_repo import GitError, GitHistory, GitSettings, data_pathspec
from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore
from test_git_history import (  # noqa: F401 - fixtures are used by name
    CHECKOUT,
    _project_with_requirement,
    clock,
    commits,
    git,
    make_service,
    root,
    service,
)
from test_git_history_edge_cases import req_path
from test_git_history_safety import _git_dir_snapshot, _mutate, _repo_with_commit

APPLEDOUBLE = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        \x00\x02\xff\xfe"


@pytest.fixture(autouse=True)
def _git_timeout(monkeypatch):
    # Guard: a git command of ReqManTool that blocks is killed after 20 s.
    monkeypatch.setattr("reqman.git_repo._TIMEOUT_SECONDS", 20)


def run_git(path: Path, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
    """git in a test repository, never in the checkout, with a timeout and no editor."""
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    env.update({"GIT_EDITOR": ":", "GIT_TERMINAL_PROMPT": "0"})
    result = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, env=env, check=False, timeout=30
    )
    if ok:
        assert result.returncode == 0, result.stderr
    return result


def _unmerged(folder: Path) -> str:
    return run_git(folder, "ls-files", "-u").stdout


def _assert_paused_and_untouched(service, project_id: str, folder: Path, word: str) -> None:
    """A mutation saves its files but stages and commits nothing while the repository is busy."""
    head = run_git(folder, "rev-parse", "HEAD").stdout
    unmerged = _unmerged(folder)
    staged = run_git(folder, "diff", "--cached", "--name-status").stdout
    module = service.create_module(project_id, "Other", "OTH")  # writes module.json and project.json
    assert (folder / "modules" / module.id / "module.json").is_file()  # saved
    assert run_git(folder, "rev-parse", "HEAD").stdout == head
    assert _unmerged(folder) == unmerged
    assert run_git(folder, "diff", "--cached", "--name-status").stdout == staged
    status = service.git_status(project_id)
    assert status["state"] == "paused", status
    assert word in status["message"] and "resolve" in status["message"]
    assert status["last_error"] is None
    assert status["can_enable"] is False
    assert status["pending_changes"] >= 1
    with pytest.raises(ConflictError, match="auto-commit is paused"):
        service.enable_history(project_id)
    assert run_git(folder, "rev-parse", "HEAD").stdout == head
    assert _unmerged(folder) == unmerged


def _diverge(service, project, module, req, clock, folder: Path) -> None:
    """``side`` and ``current`` both change the requirement's object text from 'Hello'."""
    run_git(folder, "checkout", "-q", "-b", "side")
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Side"})  # paused: saved only
    run_git(folder, "commit", "-q", "-am", "side work")
    run_git(folder, "checkout", "-q", "current")
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Main"})
    assert "Main" in (folder / req_path(module.id, req.id)).read_text()


# --- F1: a merge, cherry-pick, revert or rebase in progress, or unresolved conflicts ------


def test_merge_conflict_pauses_auto_commit_and_resumes_after_resolution(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    _diverge(service, project, module, req, clock, folder)
    assert run_git(folder, "merge", "side", ok=False).returncode != 0  # conflict
    assert req_path(module.id, req.id) in _unmerged(folder)

    _assert_paused_and_untouched(service, project.id, folder, "merge")
    # The conflict markers are still the user's to resolve: never marked resolved.
    assert "<<<<<<<" in (folder / req_path(module.id, req.id)).read_text()

    # Resolved and concluded by the user: committing resumes with what was saved meanwhile.
    run_git(folder, "checkout", "--theirs", "--", req_path(module.id, req.id))
    run_git(folder, "add", "--", req_path(module.id, req.id))
    run_git(folder, "commit", "-q", "--no-edit")
    assert service.git_status(project.id)["state"] == "ready"
    clock.offset = 7200
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    body = run_git(folder, "log", "-1", "--format=%B").stdout
    assert "Status Draft → Reviewed" in body
    assert run_git(folder, "status", "--porcelain", "--", "project.json", "modules", "links").stdout == ""
    assert "<<<<<<<" not in run_git(folder, "show", f"HEAD:{req_path(module.id, req.id)}").stdout


def test_stash_pop_conflict_pauses_auto_commit_and_never_commits_markers(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    path = folder / req_path(module.id, req.id)
    path.write_text(path.read_text().replace('"Hello"', '"Stashed"'))
    run_git(folder, "stash", "-q")
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Committed"})
    assert run_git(folder, "stash", "pop", ok=False).returncode != 0  # conflict; the stash is kept
    assert not (folder / ".git" / "MERGE_HEAD").exists()
    assert req_path(module.id, req.id) in _unmerged(folder)

    _assert_paused_and_untouched(service, project.id, folder, "conflicts")
    assert "<<<<<<<" not in run_git(folder, "show", f"HEAD:{req_path(module.id, req.id)}").stdout

    # Resolved (and unstaged, as after `git stash pop` without conflicts): resumes.
    path.write_text(run_git(folder, "show", f"HEAD:{req_path(module.id, req.id)}").stdout)
    run_git(folder, "reset", "-q")
    assert _unmerged(folder) == ""
    clock.offset = 7200
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert service.git_status(project.id)["state"] == "ready"
    assert "Status Draft → Reviewed" in run_git(folder, "log", "-1", "--format=%B").stdout
    assert run_git(folder, "status", "--porcelain", "--", "project.json", "modules", "links").stdout == ""


def test_cherry_pick_in_progress_pauses_until_it_is_concluded(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    _diverge(service, project, module, req, clock, folder)
    assert run_git(folder, "cherry-pick", "side", ok=False).returncode != 0  # conflict
    _assert_paused_and_untouched(service, project.id, folder, "cherry-pick")

    # Conflict resolved, but the cherry-pick is not concluded yet: still paused.
    run_git(folder, "checkout", "--theirs", "--", req_path(module.id, req.id))
    run_git(folder, "add", "--", req_path(module.id, req.id))
    assert _unmerged(folder) == "" and (folder / ".git" / "CHERRY_PICK_HEAD").exists()
    head = run_git(folder, "rev-parse", "HEAD").stdout
    service.create_view(project.id, "Main", ["object_text"])
    assert run_git(folder, "rev-parse", "HEAD").stdout == head
    assert service.git_status(project.id)["state"] == "paused"

    run_git(folder, "cherry-pick", "--continue")
    clock.offset = 7200
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert service.git_status(project.id)["state"] == "ready"
    assert commits(folder)[1] == "side work"  # the user's cherry-pick, then ours on top
    assert run_git(folder, "status", "--porcelain", "--", "project.json", "modules", "links").stdout == ""


@pytest.mark.parametrize(
    "marker, word",
    [
        ("MERGE_HEAD", "merge"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("rebase-merge/", "rebase"),
        ("rebase-apply/", "rebase"),
        ("sequencer/", "cherry-pick"),
    ],
)
def test_every_operation_in_progress_pauses_auto_commit(service, root, clock, marker, word):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    head = run_git(folder, "rev-parse", "HEAD").stdout
    target = folder / ".git" / marker.rstrip("/")
    if marker.endswith("/"):
        target.mkdir()
    else:
        target.write_text(head)
    _assert_paused_and_untouched(service, project.id, folder, word)

    target.rmdir() if marker.endswith("/") else target.unlink()
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert service.git_status(project.id)["state"] == "ready"
    assert run_git(folder, "status", "--porcelain", "--", "project.json", "modules", "links").stdout == ""


def test_operation_started_between_detect_and_staging_skips_the_commit(service, root, clock, monkeypatch):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    head = run_git(folder, "rev-parse", "HEAD").stdout
    history = service.history
    real = history._operation_in_progress
    calls: list[str | None] = []

    def started_after_detect(info):
        # ``detect`` sees nothing; the re-check right before ``git add`` sees a merge.
        result = real(info) if not calls else "A merge is in progress in the project repository"
        calls.append(result)
        return result

    monkeypatch.setattr(history, "_operation_in_progress", started_after_detect)
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert calls == [None, "A merge is in progress in the project repository"]
    assert run_git(folder, "rev-parse", "HEAD").stdout == head  # no commit
    assert run_git(folder, "diff", "--cached", "--name-only").stdout == ""  # nothing staged
    assert history._last_errors == {}  # skipped, not failed

    monkeypatch.setattr(history, "_operation_in_progress", real)
    clock.offset = 7200
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Later"})
    assert "Status Draft → Reviewed" in run_git(folder, "log", "-1", "--format=%B").stdout


# --- F2: a .git symlink to another repository's git dir ------------------------------------


def test_git_symlink_to_another_repository_is_foreign_and_never_touched(root, clock, tmp_path):
    other = _repo_with_commit(tmp_path / "other")
    project, module, req = _project_with_requirement(ProjectService(ProjectStore(root)))
    folder = root / project.id
    (folder / ".git").symlink_to(other / ".git", target_is_directory=True)
    before = _git_dir_snapshot(other)

    service = make_service(root, clock)
    status = service.git_status(project.id)
    assert status["state"] == "foreign" and status["can_enable"] is False
    _mutate(service, project.id, module.id, req.id, clock)
    with pytest.raises(ConflictError, match="points to another repository"):
        service.enable_history(project.id)
    assert service.requirement_history(project.id, module.id, req.id)["available"] is False
    assert service.get_requirement(project.id, module.id, req.id).attributes["status"] == "Reviewed"

    assert _git_dir_snapshot(other) == before
    assert (folder / ".git").is_symlink()


# --- F3: fsmonitor and automatic maintenance never run -----------------------------------


def test_a_configured_fsmonitor_never_runs(service, root, clock, tmp_path):
    marker = tmp_path / "fsmonitor-ran"
    script = tmp_path / "fsmonitor"
    script.write_text(f'#!/bin/sh\necho ran >> "{marker}"\nexit 1\n')
    script.chmod(0o755)
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    run_git(folder, "config", "core.fsmonitor", str(script))
    run_git(folder, "status", "--porcelain")  # the test's own git does run it
    assert marker.exists()
    marker.unlink()

    _mutate(service, project.id, module.id, req.id, clock)
    service.git_status(project.id)
    service.requirement_history(project.id, module.id, req.id)
    assert not marker.exists()
    assert len(commits(folder)) == 2


# --- F4: macOS AppleDouble files (._X.json) -------------------------------------------------


def test_appledouble_files_do_not_break_listing_or_loading(tmp_path):
    service = ProjectService(ProjectStore(tmp_path / "data"))
    project, module, req = _project_with_requirement(service)
    folder = service.store.project_path(project.id)
    (folder / "modules" / module.id / "requirements" / f"._{req.id}.json").write_bytes(APPLEDOUBLE)
    (folder / "modules" / module.id / "._module.json").write_bytes(APPLEDOUBLE)
    (folder / "modules" / f"._{module.id}").write_bytes(APPLEDOUBLE)

    assert service.store.list_requirement_ids(project.id, module.id) == [req.id]
    assert [r.id for r in service.list_requirements(project.id, module.id)] == [req.id]
    assert [m.id for m in service.list_modules(project.id)] == [module.id]
    service.add_requirement(project.id, module.id, {"object_text": "Second"})
    assert len(service.list_requirements(project.id, module.id)) == 2


def test_appledouble_files_are_never_committed_or_counted(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    junk = [
        req_path(module.id, f"._{req.id}"),
        f"links/._{module.id}.json",
        f"modules/{module.id}/._module.json",
        "._project.json",
    ]
    for path in junk:
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(APPLEDOUBLE)
        assert data_pathspec(path) is None
    assert service.git_status(project.id)["pending_changes"] == 0

    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    tracked = run_git(folder, "ls-files").stdout.splitlines()
    assert not [p for p in tracked if Path(p).name.startswith("._")]
    assert service.git_status(project.id)["pending_changes"] == 0
    assert run_git(folder, "diff", "--cached", "--name-only").stdout == ""


# --- F5: git error messages name the subcommand -------------------------------------------


def test_git_error_names_the_subcommand_not_the_options(service, root):
    project = service.create_project("Alpha")
    history = service.history
    info = history.detect(root / project.id)
    with pytest.raises(GitError) as excinfo:
        history._repo(info, ["-c", "a.b=c", "--no-pager", "no-such-subcommand"])
    assert str(excinfo.value).startswith("git no-such-subcommand failed: ")
    with pytest.raises(GitError) as excinfo:
        history._repo(info, ["-c", "commit.gpgsign=false", "commit", "--bogus-option"])
    assert str(excinfo.value).startswith("git commit failed: ")


# --- F6: the hooks directory -------------------------------------------------------------------


@pytest.fixture
def windows_hooks(monkeypatch, tmp_path):
    """Simulated Windows: no ``os.getuid``, every directory reports mode 0o777.

    Returns the lists of directories ``mkdtemp`` made and of ``atexit``
    registrations; directories are made in ``tmp_path``.
    """
    monkeypatch.setattr(git_repo, "_WINDOWS", True)
    monkeypatch.setattr(git_repo, "_hooks_path", None)
    monkeypatch.delattr(os, "getuid", raising=False)
    real_stat, real_lstat = os.stat, os.lstat

    def wide_open(real):
        def fake(path, *args, **kwargs):
            result = real(path, *args, **kwargs)
            mode = stat.S_IFMT(result.st_mode) | 0o777
            return os.stat_result((mode, *tuple(result)[1:]))
        return fake

    monkeypatch.setattr(os, "stat", wide_open(real_stat))
    monkeypatch.setattr(os, "lstat", wide_open(real_lstat))
    made: list[str] = []
    registered: list[tuple] = []
    real_mkdtemp = git_repo.tempfile.mkdtemp

    def mkdtemp(*args, **kwargs):
        made.append(real_mkdtemp(*args, **{**kwargs, "dir": tmp_path}))
        return made[-1]

    monkeypatch.setattr(git_repo.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(git_repo.atexit, "register", lambda *args: registered.append(args))
    return made, registered


def test_hooks_path_is_dev_null_on_posix(monkeypatch):
    monkeypatch.setattr(git_repo, "_WINDOWS", False)

    def fail(*args, **kwargs):
        raise AssertionError("no hooks directory is made on POSIX")

    monkeypatch.setattr(git_repo.tempfile, "mkdtemp", fail)
    assert git_repo._no_hooks_dir() == "/dev/null"


def test_hooks_directory_on_windows_is_made_once_despite_wide_modes(windows_hooks):
    made, registered = windows_hooks
    paths = {git_repo._no_hooks_dir() for _ in range(50)}
    assert len(made) == 1 and paths == {made[0]}
    assert registered == [(git_repo._remove_dir, made[0])]
    first = Path(made[0])
    assert first.is_dir() and list(first.iterdir()) == []

    # Removed (e.g. by a temp cleaner): made again under the same name, nothing registered again.
    first.rmdir()
    assert git_repo._no_hooks_dir() == made[0] and first.is_dir()
    assert len(made) == 1 and len(registered) == 1


def test_hooks_directory_on_windows_failing_its_check_falls_back_once(windows_hooks, monkeypatch):
    made, registered = windows_hooks
    monkeypatch.setattr(git_repo, "_is_real_dir", lambda path: False)  # e.g. replaced by a junction
    assert {git_repo._no_hooks_dir() for _ in range(50)} == {"/dev/null"}
    assert len(made) == 1 and len(registered) == 1  # still removed at exit


def test_hooks_directory_on_windows_replaced_by_a_symlink_falls_back(windows_hooks, tmp_path):
    made, _registered = windows_hooks
    first = Path(git_repo._no_hooks_dir())
    first.rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    first.symlink_to(elsewhere, target_is_directory=True)
    assert {git_repo._no_hooks_dir() for _ in range(5)} == {"/dev/null"}
    assert len(made) == 1


def test_hooks_directory_falls_back_to_dev_null_when_no_directory_can_be_made(windows_hooks, monkeypatch):
    calls: list[int] = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise OSError("read-only file system")

    monkeypatch.setattr(git_repo.tempfile, "mkdtemp", fail)
    assert {git_repo._no_hooks_dir() for _ in range(50)} == {"/dev/null"}
    assert len(calls) == 1 and windows_hooks[1] == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX hooks path")
def test_git_commands_use_the_hooks_directory(monkeypatch, root, clock):
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(command, *args, **kwargs):
        seen.append(list(command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(git_repo.subprocess, "run", spy)
    history = GitHistory(GitSettings(enabled=True), clock=clock)
    service = ProjectService(ProjectStore(root), history=history)
    service.create_project("Alpha")
    assert seen
    for command in seen:
        assert command[1:3] == ["-c", "core.hooksPath=/dev/null"]
        assert "core.fsmonitor=false" in command and "maintenance.auto=false" in command
        assert "gc.auto=0" in command
