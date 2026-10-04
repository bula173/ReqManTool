"""Smoke tests for per-project git history (git_repo.py, history.py, ProjectService hooks).

Every test works in pytest's ``tmp_path`` (outside any repository) with its
own repositories; ``conftest.py`` isolates git from the user's configuration.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from reqman.git_repo import GitHistory, GitSettings
from reqman.history import FileChange, commit_message, describe_changes, session_of
from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore

CHECKOUT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


def git(path: Path, *args: str, check: bool = True) -> str:
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env, check=False)
    if check:
        assert result.returncode == 0, result.stderr
    return result.stdout


def commits(path: Path) -> list[str]:
    return git(path, "log", "--format=%s").splitlines()


@pytest.fixture
def root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    # Safety net: the scratch data directory must not be inside any repository.
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=data, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    return data


@pytest.fixture
def clock():
    return Clock()


def make_service(root: Path, clock: Clock | None = None, **settings) -> ProjectService:
    history = GitHistory(GitSettings(enabled=True, **settings), clock=clock or Clock())
    return ProjectService(ProjectStore(root), history=history)


@pytest.fixture
def service(root, clock):
    return make_service(root, clock)


def _project_with_requirement(service: ProjectService):
    project = service.create_project("Alpha")
    service.add_attribute_definition(project.id, "status", "Status", "enum", choices=["Draft", "Reviewed"])
    module = service.create_module(project.id, "System", "SYRS")
    req = service.add_requirement(project.id, module.id, {"object_text": "Hello", "status": "Draft"})
    return project, module, req


# --- repositories ----------------------------------------------------------------


def test_new_project_gets_repo_on_current_with_initial_commit(service, root):
    project = service.create_project("Alpha")
    folder = root / project.id
    assert (folder / ".git").is_dir()
    assert git(folder, "symbolic-ref", "HEAD").strip() == "refs/heads/current"
    assert commits(folder) == ["Create project 'Alpha'"]
    assert git(folder, "status", "--porcelain") == ""
    assert "*.tmp" in (folder / ".git" / "info" / "exclude").read_text()
    status = service.git_status(project.id)
    assert status["state"] == "ready"
    assert status["head"]["subject"] == "Create project 'Alpha'"
    assert status["pending_changes"] == 0


def test_template_project_repo_survives_staging_rename(service, root):
    template_id = service.list_templates()[0]["id"]
    project = service.create_project("From Template", template_id=template_id)
    folder = root / project.id
    assert git(folder, "rev-parse", "--show-toplevel").strip() == str(folder.resolve())
    assert "modules/" in git(folder, "ls-files")
    assert git(folder, "status", "--porcelain") == ""
    assert not (root / ".reqman-staging").exists()


def test_subproject_is_a_sibling_folder_with_its_own_repo(service, root):
    parent = service.create_project("Parent")
    child = service.create_project("Child", parent_project_id=parent.id)
    assert (root / child.id).parent == root
    parent_files = git(root / parent.id, "ls-files").splitlines()
    assert all(not f.startswith(child.id) for f in parent_files)
    assert commits(root / child.id) == ["Create project 'Child'"]


# --- auto-commit and squash --------------------------------------------------------


def test_mutations_squash_within_window_with_generated_message(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    log = commits(folder)
    assert len(log) == 2  # initial commit + one squashed session commit
    body = git(folder, "log", "-1", "--format=%B")
    # The amended message describes the whole session, including project.json changes.
    assert "Attribute 'Status' added" in body and "Module 'System' added" in body
    assert "SYRS-0001 added" in body
    assert session_of(body) is not None
    assert git(folder, "status", "--porcelain") == ""


def test_window_expiry_starts_a_new_commit(service, root, clock):
    project, module, req = _project_with_requirement(service)
    clock.offset = 11 * 60
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    folder = root / project.id
    assert len(commits(folder)) == 3
    assert commits(folder)[0] == "SYRS-0001: Status Draft → Reviewed"


def test_tagged_or_baseline_commit_is_never_amended(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    git(folder, "tag", "-a", "baseline/v1", "-m", "v1")
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert len(commits(folder)) == 3
    git(folder, "branch", "baseline")
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Changed"})
    assert len(commits(folder)) == 4
    assert git(folder, "tag", "--points-at", "baseline/v1^{commit}")  # tag still where it was


def test_other_author_starts_a_new_commit(root, clock):
    first = make_service(root, clock)
    project, module, req = _project_with_requirement(first)
    second = make_service(root, clock, author_name="Other", author_email="other@example.com")
    second.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    folder = root / project.id
    assert len(commits(folder)) == 3
    assert git(folder, "log", "-1", "--format=%an <%ae>").strip() == "Other <other@example.com>"


def test_changes_that_cancel_out_drop_the_session_commit(service, root, clock):
    project, module, req = _project_with_requirement(service)
    clock.offset = 11 * 60
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert len(commits(root / project.id)) == 3
    clock.offset = 0  # the next change follows right after that commit
    service.update_requirement(project.id, module.id, req.id, {"status": "Draft"})
    assert len(commits(root / project.id)) == 2
    assert git(root / project.id, "status", "--porcelain") == ""


def test_cancelled_change_keeps_earlier_session_changes(service, root, clock):
    project, module, req = _project_with_requirement(service)
    clock.offset = 11 * 60
    service.add_attribute_definition(project.id, "prio", "Priority")
    clock.offset = 0
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    service.update_requirement(project.id, module.id, req.id, {"status": "Draft"})
    folder = root / project.id
    assert len(commits(folder)) == 3
    assert commits(folder)[0] == "Attribute 'Priority' added"
    assert git(folder, "status", "--porcelain") == ""


def test_every_mutation_leaves_a_clean_tree(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    other = service.create_module(project.id, "Software", "SRS")
    target = service.add_requirement(project.id, other.id, {"object_text": "Target"})
    child = service.add_requirement(project.id, module.id, {"object_text": "Child"}, parent_id=req.id)
    config = service.create_link_configuration(project.id, "Satisfies", module.id, other.id)
    steps = [
        lambda: service.set_project_parent(project.id, None),
        lambda: service.reorder_module(project.id, other.id, "up"),
        lambda: service.update_requirement(project.id, module.id, req.id, {"object_text": "Edited"}),
        lambda: service.move_requirement(project.id, module.id, child.id, None),
        lambda: service.reorder_requirement(project.id, module.id, child.id, "up"),
        lambda: service.copy_requirement(project.id, module.id, req.id, None),
        lambda: service.link_requirements(project.id, config.id, req.id, target.id),
        lambda: service.unlink_requirements(project.id, config.id, req.id, target.id),
        lambda: service.create_view(project.id, "Main", ["object_text"]),
        lambda: service.remove_view(project.id, "main"),
        lambda: service.import_csv(project.id, module.id, "ObjectText\nImported\n"),
        lambda: service.remove_requirement(project.id, module.id, child.id),
        lambda: service.remove_link_configuration(project.id, config.id),
        lambda: service.purge_module(project.id, other.id),
        lambda: service.purge_project(project.id),
    ]
    for index, step in enumerate(steps):
        clock.offset = (index + 1) * 3600  # one commit per step
        step()
        assert git(folder, "status", "--porcelain") == "", f"step {index} left changes"
    assert service.git_status(project.id)["last_error"] is None


def test_user_staged_files_are_never_committed(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    (folder / "notes.txt").write_text("mine\n")
    git(folder, "add", "notes.txt")
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert "notes.txt" not in git(folder, "show", "--name-only", "--format=", "HEAD")
    assert git(folder, "diff", "--cached", "--name-only").strip() == "notes.txt"


def test_git_failure_keeps_files_and_retries(service, root, clock):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    lock = folder / ".git" / "index.lock"
    lock.write_text("")
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert service.get_requirement(project.id, module.id, req.id).attributes["status"] == "Reviewed"
    status = service.git_status(project.id)
    assert "Auto-commit failed" in status["last_error"]
    assert status["pending_changes"] == 1
    lock.unlink()
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Again"})
    assert git(folder, "status", "--porcelain") == ""
    assert "Status Draft → Reviewed" in git(folder, "log", "-1", "--format=%B")
    assert service.git_status(project.id)["last_error"] is None


# --- nested and foreign repositories -----------------------------------------------


def test_data_dir_inside_outer_repo_never_commits_there(tmp_path, clock):
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    (outer / "README").write_text("outer\n")
    git(outer, "add", "README")
    git(outer, "commit", "-q", "-m", "outer initial")
    head_before = git(outer, "rev-parse", "HEAD")
    data = outer / "data"
    service = make_service(data, clock)

    project, module, req = _project_with_requirement(service)
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})

    assert not (data / project.id / ".git").exists()
    assert git(outer, "rev-parse", "HEAD") == head_before
    assert git(outer, "ls-files").splitlines() == ["README"]
    status = service.git_status(project.id)
    assert status["state"] == "nested"
    assert status["enclosing_repo"] == str(outer.resolve())
    assert status["can_enable"] is False
    with pytest.raises(ConflictError):
        service.enable_history(project.id)
    assert git(outer, "ls-files").splitlines() == ["README"]
    history = service.requirement_history(project.id, module.id, req.id)
    assert history["available"] is False and "inside the git repository" in history["reason"]


def test_project_with_own_repo_inside_outer_repo_is_nested(tmp_path, clock):
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    service = make_service(outer / "data", clock)
    project = service.create_project("Inner")
    folder = outer / "data" / project.id
    git(folder, "init", "-q")
    service.create_module(project.id, "Mod", "M")
    assert git(folder, "rev-list", "--all", check=False) == ""
    assert git(outer, "rev-list", "--all", check=False) == ""
    assert service.git_status(project.id)["state"] == "nested"


def test_other_branch_pauses_and_enable_creates_current(root, clock):
    plain = ProjectService(ProjectStore(root))
    project = plain.create_project("Legacy")
    folder = root / project.id
    git(folder, "init", "-q", "-b", "main")
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "user commit")
    service = make_service(root, clock)
    service.create_module(project.id, "Mod", "M")
    assert commits(folder) == ["user commit"]  # paused: nothing committed
    status = service.git_status(project.id)
    assert status["state"] == "paused" and status["can_enable"] is True
    assert status["pending_changes"] > 0
    status = service.enable_history(project.id)
    assert status["state"] == "ready" and status["pending_changes"] == 0
    assert git(folder, "symbolic-ref", "HEAD").strip() == "refs/heads/current"
    assert commits(folder)[0] == "Module 'Mod' added"


def test_detached_head_or_existing_current_is_refused(root, clock):
    service = make_service(root, clock)
    project = service.create_project("Alpha")
    folder = root / project.id
    git(folder, "checkout", "-q", "--detach")
    with pytest.raises(ConflictError):
        service.enable_history(project.id)
    git(folder, "checkout", "-q", "-b", "other")
    with pytest.raises(ConflictError):
        service.enable_history(project.id)


def test_enable_history_on_plain_project(root, clock):
    project = ProjectService(ProjectStore(root)).create_project("Plain")
    service = make_service(root, clock)
    assert service.git_status(project.id)["state"] == "no_repo"
    status = service.enable_history(project.id)
    assert status["state"] == "ready"
    assert commits(root / project.id) == ["Enable history (git) for project 'Plain'"]
    assert service.enable_history(project.id)["state"] == "ready"  # idempotent
    assert len(commits(root / project.id)) == 1


def test_git_missing_degrades_to_plain_files(root, clock):
    service = make_service(root, clock, executable=str(root / "no-such-git"))
    project, module, req = _project_with_requirement(service)
    assert not (root / project.id / ".git").exists()
    status = service.git_status(project.id)
    assert status["state"] == "git_missing" and "not found" in status["message"]
    assert service.requirement_history(project.id, module.id, req.id)["available"] is False
    with pytest.raises(ValueError):
        service.enable_history(project.id)


# --- history ------------------------------------------------------------------


def test_requirement_history_and_blame(service, root, clock):
    project, module, req = _project_with_requirement(service)
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    history = service.requirement_history(project.id, module.id, req.id)
    assert history["available"] is True and history["uncommitted"] is False
    newest, oldest = history["entries"]
    assert oldest["change"] == "added"
    assert newest["change"] == "modified"
    assert newest["diffs"] == [{"kind": "attribute", "attribute_id": "status", "old": "Draft", "new": "Reviewed"}]
    assert history["blame"]["status"]["commit"] == newest["commit"]
    assert history["blame"]["object_text"]["commit"] == oldest["commit"]
    assert newest["author_name"] == "Test Author"


def test_history_unavailable_without_repo(root):
    service = ProjectService(ProjectStore(root))  # git off
    project = service.create_project("Plain")
    module = service.create_module(project.id, "Mod", "M")
    req = service.add_requirement(project.id, module.id, {"object_text": "x"})
    history = service.requirement_history(project.id, module.id, req.id)
    assert history["available"] is False and history["entries"] == []
    with pytest.raises(FileNotFoundError):
        service.requirement_history(project.id, module.id, "M-9999")


# --- messages ---------------------------------------------------------------------


def test_describe_changes_and_commit_message():
    project = {"attributes": [{"id": "status", "name": "Status"}, {"id": "sat", "name": "Sat", "system": True}]}
    changes = [
        FileChange(
            "modules/sys/requirements/SYRS-0012.json",
            b'{"id": "SYRS-0012", "attributes": {"status": "Draft", "sat": ""}, "parent_id": null, "order": 0}',
            b'{"id": "SYRS-0012", "attributes": {"status": "Reviewed", "sat": "> X"}, "parent_id": null, "order": 0}',
        ),
        FileChange("modules/sys/requirements/SYRS-0013.json", None, b'{"id": "SYRS-0013", "attributes": {}}'),
    ]
    entries = describe_changes(changes, project)
    assert entries == ["SYRS-0012: Status Draft → Reviewed", "SYRS-0013 added"]
    message = commit_message(entries, "abc123")
    assert message.splitlines()[0] == "SYRS-0012: Status Draft → Reviewed; SYRS-0013 added"
    assert session_of(message) == "abc123"
    long = commit_message([f"REQ-{i:04d} added" for i in range(300)])
    assert "+" in long.splitlines()[0] and "… and 100 more" in long
    assert session_of(long) is None


def test_settings_from_env():
    settings = GitSettings.from_env(
        {"REQMAN_GIT_SQUASH_MINUTES": "2.5", "REQMAN_GIT_AUTHOR_NAME": "A", "REQMAN_GIT_AUTHOR_EMAIL": "a@b"}
    )
    assert settings.enabled and settings.squash_window_seconds == 150
    assert (settings.author_name, settings.author_email) == ("A", "a@b")
    assert GitSettings.from_env({"REQMAN_GIT": "off"}).enabled is False
    assert GitSettings.from_env({"REQMAN_GIT_SQUASH_MINUTES": "-1"}).squash_window_seconds == 600


# --- REST / WebSocket ------------------------------------------------------------------


@pytest.fixture
def client(root, monkeypatch):
    monkeypatch.setenv("REQMAN_GIT", "on")
    monkeypatch.setenv("REQMAN_DATA_DIR", str(root))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(root.parent / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_api_status_history_and_ws_parity(client):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "M"}).json()["id"]
    req_id = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "x"}}
    ).json()["id"]

    rest_status = client.get(f"/projects/{project_id}/git").json()
    assert rest_status["state"] == "ready"
    rest_history = client.get(f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}/history").json()
    assert rest_history["available"] is True and rest_history["entries"][0]["change"] == "added"
    assert client.get(f"/projects/{project_id}/modules/{module_id}/requirements/NOPE/history").status_code == 404
    assert client.get("/projects/missing/git").status_code == 404

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as other:
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "project.git_status", "params": {"id": project_id}})
        assert ws.receive_json()["result"]["state"] == rest_status["state"]
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "requirement.history",
                "params": {"project_id": project_id, "module_id": module_id, "id": req_id},
            }
        )
        assert ws.receive_json()["result"]["entries"] == rest_history["entries"]
        ws.send_json({"jsonrpc": "2.0", "id": 3, "method": "project.git_enable", "params": {"id": project_id}})
        assert ws.receive_json()["result"]["state"] == "ready"
        assert other.receive_json() == {"event": "changed", "project_id": project_id}

    response = client.post(f"/projects/{project_id}/git/enable")
    assert response.status_code == 200 and response.json()["state"] == "ready"
