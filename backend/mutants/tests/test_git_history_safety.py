"""Safety of per-project git history: foreign repositories, hooks, data-file-only
commits, unexpected errors, partial "Enable history" failures and the git
environment.

Same rules as ``test_git_history.py`` (whose helpers and fixtures are reused):
every repository lives in pytest's ``tmp_path``, ``conftest.py`` isolates git
from the user's configuration, and ``git()`` refuses to run in the checkout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reqman.git_repo import GitHistory, GitSettings
from reqman.history import FileChange, describe_changes
from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore
from test_git_history import (  # noqa: F401 - fixtures are used by name
    _project_with_requirement,
    clock,
    commits,
    git,
    make_service,
    root,
    service,
)
from test_git_history_edge_cases import _client_for, req_path, rpc


def _repo_with_commit(path: Path, branch: str = "main") -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", branch)
    (path / "README").write_text("other\n")
    git(path, "add", "README")
    git(path, "commit", "-q", "-m", "other initial")
    return path


def _git_dir_snapshot(repo: Path) -> dict:
    """Every file of the repository's git dir except objects (HEAD, index, refs, config, worktrees)."""
    git_dir = repo / ".git"
    files = {
        str(p.relative_to(git_dir)): p.read_bytes()
        for p in sorted(git_dir.rglob("*"))
        if p.is_file() and "objects" not in p.relative_to(git_dir).parts
    }
    return {"files": files, "objects": git(repo, "count-objects", "-v"), "refs": git(repo, "for-each-ref")}


def _mutate(service: ProjectService, project_id: str, module_id: str, req_id: str, clock) -> None:
    """Mutations that amend, start a new commit and drop a cancelled session (2 commits remain)."""
    clock.offset = 0
    service.update_requirement(project_id, module_id, req_id, {"object_text": "Squashed"})
    service.update_requirement(project_id, module_id, req_id, {"status": "Reviewed"})
    clock.offset = 3600
    service.update_requirement(project_id, module_id, req_id, {"object_text": "Later"})  # new commit
    clock.offset = 0
    service.update_requirement(project_id, module_id, req_id, {"object_text": "Squashed"})  # cancels out
    service.create_view(project_id, "Main", ["object_text"])  # amends again


# --- R2: a .git that points to another repository ------------------------------------


def test_gitfile_pointing_to_another_repository_is_foreign_and_never_touched(root, clock, tmp_path, monkeypatch):
    other = _repo_with_commit(tmp_path / "other")
    project, module, req = _project_with_requirement(ProjectService(ProjectStore(root)))
    folder = root / project.id
    (folder / ".git").write_text(f"gitdir: {os.path.relpath(other / '.git', folder)}\n")
    before = _git_dir_snapshot(other)

    service = make_service(root, clock)
    status = service.git_status(project.id)
    assert status["state"] == "foreign" and status["can_enable"] is False
    assert "points to another repository (separate git dir or linked worktree)" in status["message"]
    _mutate(service, project.id, module.id, req.id, clock)
    service.create_project("Sibling")  # a new project still gets its own repository
    with pytest.raises(ConflictError, match="points to another repository"):
        service.enable_history(project.id)
    history = service.requirement_history(project.id, module.id, req.id)
    assert history["available"] is False and "points to another repository" in history["reason"]
    assert service.get_requirement(project.id, module.id, req.id).attributes["status"] == "Reviewed"

    client = _client_for(root, tmp_path, monkeypatch, REQMAN_GIT="on")
    assert client.get(f"/projects/{project.id}/git").json()["state"] == "foreign"
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as listener:
        response = client.post(f"/projects/{project.id}/git/enable")
        assert response.status_code == 409 and "points to another repository" in response.json()["detail"]
        assert rpc(ws, "project.git_enable", {"id": project.id})["error"]["code"] == -32602
        assert rpc(listener, "project.git_status", {"id": project.id}, 7)["id"] == 7  # no broadcast

    assert _git_dir_snapshot(other) == before
    assert (folder / ".git").is_file()


def test_linked_worktree_is_foreign_and_its_repository_is_never_touched(root, clock, tmp_path):
    main = _repo_with_commit(tmp_path / "main")
    project, module, req = _project_with_requirement(ProjectService(ProjectStore(root)))
    folder = root / project.id
    saved = tmp_path / "saved"
    shutil.move(str(folder), str(saved))
    git(main, "worktree", "add", "-q", "-b", "wt", str(folder))
    shutil.copytree(saved, folder, dirs_exist_ok=True)
    before = _git_dir_snapshot(main)

    service = make_service(root, clock)
    assert service.git_status(project.id)["state"] == "foreign"
    _mutate(service, project.id, module.id, req.id, clock)
    with pytest.raises(ConflictError):
        service.enable_history(project.id)  # would otherwise create `current` in main's refs

    assert _git_dir_snapshot(main) == before
    assert git(main, "branch", "--list", "current") == ""


# --- R3: hooks never run ------------------------------------------------------------

HOOK_NAMES = (
    "pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "post-rewrite", "post-checkout",
    "reference-transaction", "post-index-change", "pre-auto-gc", "post-merge",
)


def _install_hooks(hooks_dir: Path, marker: Path) -> Path:
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name in HOOK_NAMES:
        hook = hooks_dir / name
        hook.write_text(f'#!/bin/sh\necho "{name}" >> "{marker}"\n')
        hook.chmod(0o755)
    return hooks_dir


def test_hooks_in_the_repository_never_run(service, root, clock, tmp_path):
    marker = tmp_path / "hooks-ran"
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    _install_hooks(folder / ".git" / "hooks", marker)
    _mutate(service, project.id, module.id, req.id, clock)
    assert not marker.exists()
    assert len(commits(folder)) == 2 and git(folder, "status", "--porcelain") == ""


def test_hooks_of_a_configured_core_hooks_path_never_run(service, root, clock, tmp_path):
    marker = tmp_path / "hooks-ran"
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    git(folder, "config", "core.hooksPath", str(_install_hooks(tmp_path / "hooks", marker)))
    _mutate(service, project.id, module.id, req.id, clock)
    assert not marker.exists()
    assert len(commits(folder)) == 2


def test_enable_on_another_branch_runs_no_checkout_or_commit_hook(root, clock, tmp_path):
    marker = tmp_path / "hooks-ran"
    project = ProjectService(ProjectStore(root)).create_project("Legacy")
    folder = root / project.id
    git(folder, "init", "-q", "-b", "main")
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "by hand")
    _install_hooks(folder / ".git" / "hooks", marker)
    service = make_service(root, clock)
    assert service.enable_history(project.id)["state"] == "ready"
    service.create_module(project.id, "Mod", "M")
    assert not marker.exists()
    assert git(folder, "symbolic-ref", "HEAD").strip() == "refs/heads/current"


@pytest.mark.parametrize("source", ["global config", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"])
def test_inherited_hooks_and_templates_are_ignored(root, clock, tmp_path, monkeypatch, source):
    marker = tmp_path / "hooks-ran"
    hooks = _install_hooks(tmp_path / "hooks", marker)
    template = tmp_path / "template"
    _install_hooks(template / "hooks", marker)
    if source == "global config":
        config = tmp_path / "gitconfig"
        config.write_text(f"[init]\n\ttemplateDir = {template}\n[core]\n\thooksPath = {hooks}\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    elif source == "GIT_CONFIG_COUNT":
        monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hooks))
        monkeypatch.setenv("GIT_CONFIG_KEY_1", "init.templateDir")
        monkeypatch.setenv("GIT_CONFIG_VALUE_1", str(template))
    else:
        monkeypatch.setenv(
            "GIT_CONFIG_PARAMETERS", f"'core.hooksPath'='{hooks}' 'init.templateDir'='{template}'"
        )
    service = make_service(root, clock)
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    assert not (folder / ".git" / "hooks").exists()  # no template copied by `git init`
    assert "*.tmp" in (folder / ".git" / "info" / "exclude").read_text()
    _mutate(service, project.id, module.id, req.id, clock)
    assert not marker.exists()
    assert len(commits(folder)) == 2


def test_a_blocking_prepare_commit_msg_hook_does_not_delay_a_mutation(service, root, clock, tmp_path, monkeypatch):
    # Guard: if the hook ran, git would be killed after 10 s instead of hanging the suite.
    monkeypatch.setattr("reqman.git_repo._TIMEOUT_SECONDS", 10)
    marker = tmp_path / "hook-ran"
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    hook = folder / ".git" / "hooks" / "prepare-commit-msg"
    hook.parent.mkdir(parents=True, exist_ok=True)
    # The sleep holds no pipe of git's, so a killed git returns at once.
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\nexec sleep 30 </dev/null >/dev/null 2>&1\n')
    hook.chmod(0o755)

    started = time.monotonic()
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert time.monotonic() - started < 5
    assert not marker.exists()
    assert service.git_status(project.id)["last_error"] is None
    assert git(folder, "status", "--porcelain") == ""


# --- R4: unexpected errors never fail a mutation --------------------------------------


def test_describe_changes_tolerates_wrong_types_in_project_files():
    weird = {
        "name": "P", "attributes": 5, "link_configurations": "x", "views": 3, "module_order": 5,
    }
    normal = {
        "name": "P",
        "attributes": [{"id": "status", "name": "Status"}],
        "link_configurations": [],
        "views": [],
        "module_order": ["a", "b"],
    }
    for old, new in ((weird, normal), (normal, weird), (weird, {**weird, "module_order": "ba"})):
        change = FileChange("project.json", json.dumps(old).encode(), json.dumps(new).encode())
        assert isinstance(describe_changes([change], new), list)
    assert describe_changes(
        [FileChange("project.json", json.dumps(weird).encode(), json.dumps(normal).encode())]
    ) == ["Attribute 'Status' added"]


def test_hand_committed_project_file_with_wrong_types_is_still_auto_committed(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    path = folder / "project.json"
    data = json.loads(path.read_text())
    data["module_order"] = 5
    path.write_text(json.dumps(data, indent=2) + "\n")
    git(folder, "commit", "-q", "-am", "hand edit")

    clock.offset = 3600
    service.create_module(project.id, "Software", "SRS")
    assert service.git_status(project.id)["last_error"] is None
    assert "Module 'Software' added" in git(folder, "log", "-1", "--format=%B")
    assert git(folder, "status", "--porcelain") == ""


def test_unexpected_error_while_committing_is_recorded_not_raised(service, root, clock, monkeypatch):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id

    def broken(changes, project=None):
        raise RuntimeError("describe broke")

    monkeypatch.setattr("reqman.service.describe_changes", broken)
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert service.get_requirement(project.id, module.id, req.id).attributes["status"] == "Reviewed"
    status = service.git_status(project.id)
    assert "Auto-commit failed" in status["last_error"] and "describe broke" in status["last_error"]
    with pytest.raises(ValueError, match="describe broke") as excinfo:
        service.enable_history(project.id)  # a 400, not a 500
    assert not isinstance(excinfo.value, ConflictError)

    monkeypatch.undo()
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Again"})
    assert service.git_status(project.id)["last_error"] is None
    assert "Status Draft → Reviewed" in git(folder, "log", "-1", "--format=%B")


def test_unexpected_error_creating_the_repository_is_recorded_not_raised(root, clock, monkeypatch):
    service = make_service(root, clock)

    def broken(*args, **kwargs):
        raise RuntimeError("initial commit broke")

    monkeypatch.setattr(GitHistory, "_commit_pending", broken)
    project = service.create_project("Fragile")
    assert (root / project.id / "project.json").exists()
    assert "initial commit broke" in service.git_status(project.id)["last_error"]


# --- R5: only data files are committed or counted ------------------------------------

JUNK = (
    "modules/.DS_Store",
    "modules/{m}/.DS_Store",
    "modules/{m}/Thumbs.db",
    "modules/{m}/module.json~",
    "modules/{m}/requirements/{r}.json~",
    "modules/{m}/requirements/.{r}.json.swp",
    "modules/{m}/requirements/Thumbs.db",
    "links/Thumbs.db",
    "links/.DS_Store",
    "links/satisfies.json~",
    "project.json~",
)


def test_only_data_files_are_committed_or_counted(service, root, clock):
    project, module, req = _project_with_requirement(service)
    other = service.add_requirement(project.id, module.id, {"object_text": "Other"})
    folder = root / project.id
    (folder / "links").mkdir(exist_ok=True)
    junk = [p.format(m=module.id, r=req.id) for p in JUNK]
    for path in junk:
        (folder / path).write_text("junk\n")

    assert service.git_status(project.id)["pending_changes"] == 0
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    clock.offset = 7200
    service.remove_requirement(project.id, module.id, other.id)  # deleted data files are still staged

    tracked = set(git(folder, "ls-files").splitlines())
    assert not tracked & set(junk)
    assert req_path(module.id, other.id) not in tracked
    assert req_path(module.id, req.id) in tracked
    assert {line[3:] for line in git(folder, "status", "--porcelain", "-uall").splitlines()} == set(junk)
    assert all(line.startswith("??") for line in git(folder, "status", "--porcelain", "-uall").splitlines())
    status = service.git_status(project.id)
    assert status["pending_changes"] == 0 and status["last_error"] is None


def test_junk_the_user_staged_or_committed_is_left_alone(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    (folder / "modules" / ".DS_Store").write_text("committed by hand\n")
    git(folder, "add", "modules/.DS_Store")
    git(folder, "commit", "-q", "-m", "oops")
    (folder / "modules" / ".DS_Store").write_text("changed since\n")  # tracked and modified
    staged = folder / "modules" / module.id / "Thumbs.db"
    staged.write_text("staged by the user\n")
    git(folder, "add", str(staged.relative_to(folder)))

    assert service.git_status(project.id)["pending_changes"] == 0
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert git(folder, "show", "--name-only", "--format=", "HEAD").split() == [req_path(module.id, req.id)]
    assert git(folder, "diff", "--cached", "--name-only").split() == [f"modules/{module.id}/Thumbs.db"]
    assert git(folder, "diff", "--name-only").split() == ["modules/.DS_Store"]


# --- R8: a partly failed "Enable history" still broadcasts ------------------------------


def _git_refusing_commits(tmp_path: Path) -> Path:
    script = tmp_path / "bin" / "git-no-commit"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "commit" ]; then echo "fatal: commit refused by the test" >&2; exit 1; fi\n'
        "done\n"
        'exec git "$@"\n'
    )
    script.chmod(0o755)
    return script


def test_enable_that_fails_after_init_broadcasts_changed_on_rest_and_ws(root, tmp_path, monkeypatch):
    plain = ProjectService(ProjectStore(root)).create_project("Plain")
    client = _client_for(
        root, tmp_path, monkeypatch, REQMAN_GIT="on", REQMAN_GIT_EXECUTABLE=str(_git_refusing_commits(tmp_path))
    )
    changed = {"event": "changed", "project_id": plain.id}
    status_request = {"jsonrpc": "2.0", "id": 5, "method": "project.git_status", "params": {"id": plain.id}}
    with client.websocket_connect("/ws") as ws:
        response = client.post(f"/projects/{plain.id}/git/enable")
        assert response.status_code == 400 and "commit refused by the test" in response.json()["detail"]
        # The broadcast arrives before the reply to a later request (never blocks if it is missing).
        ws.send_json(status_request)
        assert ws.receive_json() == changed
        status = ws.receive_json()["result"]
    assert status["state"] == "ready" and status["head"] is None  # the repository was created
    assert "commit refused by the test" in status["last_error"]

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as other:
        reply = rpc(ws, "project.git_enable", {"id": plain.id})
        assert reply["error"]["code"] == -32602 and "commit refused" in reply["error"]["message"]
        other.send_json(status_request)
        assert other.receive_json() == changed
        assert other.receive_json()["id"] == 5


# --- R10: the git environment ----------------------------------------------------------


@pytest.mark.parametrize("variable", ["GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_ICASE_PATHSPECS"])
def test_inherited_pathspec_variables_are_dropped(root, clock, monkeypatch, variable):
    monkeypatch.setenv(variable, "1")
    service = make_service(root, clock)
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    monkeypatch.delenv(variable)
    assert len(commits(folder)) == 2
    assert git(folder, "status", "--porcelain") == ""
    monkeypatch.setenv(variable, "1")
    history = service.requirement_history(project.id, module.id, req.id)
    assert [e["change"] for e in history["entries"]] == ["added"]


def test_history_reads_a_requirement_path_literally(root, clock):
    folder = root / "p"
    requirements = folder / "modules" / "m" / "requirements"
    requirements.mkdir(parents=True)
    git(folder, "init", "-q", "-b", "current")
    (requirements / "R[1].json").write_text(json.dumps({"id": "R[1]", "attributes": {"t": "a"}}))
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "bracket")
    (requirements / "R1.json").write_text(json.dumps({"id": "R1", "attributes": {"t": "b"}}))
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "plain")
    (requirements / "R1.json").write_text(json.dumps({"id": "R1", "attributes": {"t": "c"}}))  # uncommitted

    history = GitHistory(GitSettings(enabled=True), clock=clock).requirement_history(
        folder, "modules/m/requirements/R[1].json", "R[1]"
    )
    assert [e["subject"] for e in history["entries"]] == ["bracket"]
    assert history["uncommitted"] is False  # R1.json's change is not this requirement's


def test_nested_detection_crosses_file_systems(root, clock, monkeypatch):
    project = ProjectService(ProjectStore(root)).create_project("Mounted")
    calls: list[dict] = []
    real_run = subprocess.run

    def spy(command, *args, **kwargs):
        if "--show-toplevel" in command:
            calls.append(kwargs.get("env") or {})
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr("reqman.git_repo.subprocess.run", spy)
    monkeypatch.setenv("GIT_DISCOVERY_ACROSS_FILESYSTEM", "0")
    assert make_service(root, clock).git_status(project.id)["state"] == "no_repo"
    assert len(calls) == 2  # the parent, then the project folder
    assert all(env.get("GIT_DISCOVERY_ACROSS_FILESYSTEM") == "1" for env in calls)
