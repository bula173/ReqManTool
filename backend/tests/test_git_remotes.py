"""Smoke tests for git remotes: configure, push/pull, clone, sync-conflict blocking.

Every test works in pytest's ``tmp_path`` (outside any repository) with its
own repositories; remotes are local bare repositories (``git init --bare``),
never a network URL. ``conftest.py`` isolates git from the user's
configuration. Thorough coverage belongs to the dedicated test slices.
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


@pytest.fixture
def root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=data, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    return data


@pytest.fixture
def bare(tmp_path):
    """A local bare repository standing in for a GitLab/GitHub remote."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True)
    return remote


def make_service(root: Path) -> ProjectService:
    history = GitHistory(GitSettings(enabled=True), clock=Clock())
    return ProjectService(ProjectStore(root), history=history)


@pytest.fixture
def service(root):
    return make_service(root)


def _project_with_requirement(service: ProjectService):
    project = service.create_project("Alpha")
    module = service.create_module(project.id, "System", "SYRS")
    req = service.add_requirement(project.id, module.id, {"object_text": "Hello"})
    return project, module, req


# --- configure ---------------------------------------------------------------------


def test_set_remote_validates_stores_and_removes(service, root, bare):
    project, _module, _req = _project_with_requirement(service)
    with pytest.raises(ValueError, match="cannot be reached"):
        service.set_git_remote(project.id, str(root.parent / "missing.git"))
    with pytest.raises(ValueError, match="must not start with"):
        service.set_git_remote(project.id, "--upload-pack=evil")
    status = service.set_git_remote(project.id, str(bare))
    assert status["remote"]["url"] == str(bare)
    assert status["remote"]["auto_push"] is False
    assert status["remote"]["sync_conflict"] is False
    assert git(root / project.id, "remote", "get-url", "origin").strip() == str(bare)
    # Removing the remote: URL null/empty.
    status = service.set_git_remote(project.id, None)
    assert status["remote"]["url"] is None
    assert "origin" not in git(root / project.id, "remote")


def test_set_remote_rejects_command_transports_without_executing(service, tmp_path):
    """``ext::``/``fd::`` URLs run commands; they must be refused, not run."""
    project, _module, _req = _project_with_requirement(service)
    sentinel = tmp_path / "pwned"
    for url in (f"ext::sh -c 'touch {sentinel}'", "fd::7"):
        with pytest.raises(ValueError, match="remote helpers are not allowed"):
            service.set_git_remote(project.id, url)
    assert not sentinel.exists(), "the ext:: transport executed a command"
    assert service.git_status(project.id)["remote"]["url"] is None


def test_remote_needs_a_repository(service, root, bare, monkeypatch):
    monkeypatch.setenv("REQMAN_GIT", "on")  # GitSettings already enabled; belt and braces
    plain = ProjectService(ProjectStore(root))  # history off: plain files
    project = plain.create_project("NoRepo")
    with pytest.raises(ValueError):
        plain.set_git_remote(project.id, str(bare))
    with pytest.raises(ConflictError):
        service.git_push(project.id)  # created without a repository


# --- push --------------------------------------------------------------------------


def test_push_publishes_current_baseline_and_tags(service, root, bare):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.create_baseline(project.id, "v1", "First")
    result = service.git_push(project.id)
    assert result["url"] == str(bare)
    assert "refs/heads/current" in result["pushed"]
    assert "refs/heads/baseline" in result["pushed"]
    assert "refs/tags/baseline/v1" in result["pushed"]
    folder = root / project.id
    head = git(folder, "rev-parse", "HEAD").strip()
    refs = git(folder, "ls-remote", str(bare))
    assert f"{head}\trefs/heads/current" in refs
    assert f"{head}\trefs/heads/baseline" in refs
    assert "refs/tags/baseline/v1" in refs
    status = service.git_status(project.id)
    assert status["remote"] == {
        "url": str(bare),
        "auto_push": False,
        "ahead": 0,
        "behind": 0,
        "sync_conflict": False,
        "conflict_message": None,
    }


def test_push_without_remote_is_rejected(service):
    project, _module, _req = _project_with_requirement(service)
    with pytest.raises(ValueError, match="No remote is configured"):
        service.git_push(project.id)
    with pytest.raises(ValueError, match="No remote is configured"):
        service.git_pull(project.id)


def test_auto_push_is_opt_in(service, root, bare):
    project, module, req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    folder = root / project.id
    # Off (the default): a mutation commits locally but pushes nothing.
    service.update_requirement(project.id, module.id, req.id, {"object_text": "one"})
    assert "refs/heads/current" not in git(folder, "ls-remote", str(bare))
    # On: the next mutation pushes automatically.
    status = service.set_git_auto_push(project.id, True)
    assert status["remote"]["auto_push"] is True
    service.update_requirement(project.id, module.id, req.id, {"object_text": "two"})
    head = git(folder, "rev-parse", "HEAD").strip()
    assert f"{head}\trefs/heads/current" in git(folder, "ls-remote", str(bare))
    assert service.git_status(project.id)["last_error"] is None
    # Removing the remote clears the opt-in: a later remote must not inherit it.
    assert service.set_git_remote(project.id, None)["remote"]["auto_push"] is False
    status = service.set_git_remote(project.id, str(bare))
    assert status["remote"]["auto_push"] is False
    assert git(folder, "config", "--get", "reqman.autopush", check=False).strip() != "true"


def test_auto_push_needs_a_remote(service):
    project, _module, _req = _project_with_requirement(service)
    with pytest.raises(ValueError, match="needs a configured remote"):
        service.set_git_auto_push(project.id, True)


# --- clone and pull ----------------------------------------------------------------


def test_clone_lists_project_and_pull_fast_forwards(service, root, bare, tmp_path):
    project, module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)

    other_root = tmp_path / "data2"
    other_root.mkdir()
    other = make_service(other_root)
    cloned = other.add_project_from_remote(str(bare))
    assert cloned.id == project.id and cloned.name == "Alpha"
    assert [p.id for p in other.list_projects()] == [project.id]
    status = other.git_status(project.id)
    assert status["state"] == "ready" and status["remote"]["url"] == str(bare)
    assert not (other_root / ".reqman-staging").exists()

    # The first instance adds a requirement and pushes; the second pulls (fast-forward only).
    new_req = service.add_requirement(project.id, module.id, {"object_text": "Pushed later"})
    service.git_push(project.id)
    result = other.git_pull(project.id)
    assert result["updated"] is True and result["notes"] == []
    pulled = other.get_requirement(project.id, module.id, new_req.id)
    assert pulled.attributes["object_text"] == "Pushed later"
    # Pulling again changes nothing.
    assert other.git_pull(project.id)["updated"] is False


def test_add_project_from_remote_refuses_taken_id_and_bad_url(service, root, bare, tmp_path):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    with pytest.raises(ConflictError, match="already exists"):
        service.add_project_from_remote(str(bare))  # same data directory: id taken
    other = make_service(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        other.add_project_from_remote(str(tmp_path / "nothing.git"))
    with pytest.raises(ValueError):
        other.add_project_from_remote("   ")
    assert other.list_projects() == []
    assert not (tmp_path / "elsewhere" / ".reqman-staging").exists()


# --- concurrency: diverged histories -----------------------------------------------


def _two_instances(service, root, bare, tmp_path):
    """Two instances of the same project, diverged by one commit each (different files)."""
    project = service.create_project("Alpha")
    module = service.create_module(project.id, "System", "SYRS")
    req_a = service.add_requirement(project.id, module.id, {"object_text": "A"})
    req_b = service.add_requirement(project.id, module.id, {"object_text": "B"})
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    other_root = tmp_path / "data2"
    other_root.mkdir()
    other = make_service(other_root)
    other.add_project_from_remote(str(bare))
    # Diverge on different files so a git merge resolves cleanly.
    service.update_requirement(project.id, module.id, req_a.id, {"object_text": "A changed here"})
    other.update_requirement(project.id, module.id, req_b.id, {"object_text": "B changed there"})
    return project, module, req_a, req_b, other, other_root


def test_push_race_rejects_then_pull_flags_sync_conflict(service, root, bare, tmp_path):
    project, module, req_a, _req_b, other, other_root = _two_instances(service, root, bare, tmp_path)
    service.git_push(project.id)  # the first instance wins the race
    with pytest.raises(ConflictError, match="Pull first"):
        other.git_push(project.id)
    with pytest.raises(ConflictError, match="Sync conflict") as excinfo:
        other.git_pull(project.id)
    assert getattr(excinfo.value, "project_changed", False) is True  # broadcast despite the failure
    status = other.git_status(project.id)
    assert status["remote"]["sync_conflict"] is True
    assert status["remote"]["ahead"] == 1 and status["remote"]["behind"] == 1
    # Mutations are blocked until the user reconciles the histories in git.
    with pytest.raises(ConflictError, match="Sync conflict"):
        other.update_requirement(project.id, module.id, req_a.id, {"object_text": "nope"})
    with pytest.raises(ConflictError, match="Sync conflict"):
        other.create_baseline(project.id, "v1")
    # The user resolves in git (a merge; the files touch different requirements).
    git(other_root / project.id, "merge", "--no-edit", "refs/remotes/origin/current")
    assert other.git_status(project.id)["remote"]["sync_conflict"] is False
    other.update_requirement(project.id, module.id, req_a.id, {"object_text": "allowed again"})
    other.git_push(project.id)
    assert service.git_pull(project.id)["updated"] is True


def test_sync_conflict_does_not_block_other_projects(service, root, bare, tmp_path):
    project, _module, _req_a, _req_b, other, _other_root = _two_instances(service, root, bare, tmp_path)
    service.git_push(project.id)
    with pytest.raises(ConflictError):
        other.git_pull(project.id)
    unrelated = other.create_project("Beta")  # no remote, no conflict: not blocked
    assert unrelated.name == "Beta"


# --- REST / WebSocket --------------------------------------------------------------


@pytest.fixture
def client(root, monkeypatch):
    monkeypatch.setenv("REQMAN_GIT", "on")
    monkeypatch.setenv("REQMAN_DATA_DIR", str(root))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(root.parent / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_api_remote_sync_and_ws_parity(client, root, bare):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "M"}).json()["id"]
    client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "x"}}
    )
    assert client.put(f"/projects/{project_id}/git/remote", json={"url": "/nope/missing.git"}).status_code == 400
    assert client.post(f"/projects/{project_id}/git/push").status_code == 400  # no remote yet
    with client.websocket_connect("/ws") as other:
        status = client.put(f"/projects/{project_id}/git/remote", json={"url": str(bare)})
        assert status.status_code == 200 and status.json()["remote"]["url"] == str(bare)
        assert other.receive_json() == {"event": "changed", "project_id": project_id}
        pushed = client.post(f"/projects/{project_id}/git/push")
        assert pushed.status_code == 200 and "refs/heads/current" in pushed.json()["pushed"]
        assert other.receive_json() == {"event": "changed", "project_id": project_id}
    auto = client.put(f"/projects/{project_id}/git/auto-push", json={"enabled": True})
    assert auto.status_code == 200 and auto.json()["remote"]["auto_push"] is True
    pulled = client.post(f"/projects/{project_id}/git/pull")
    assert pulled.status_code == 200 and pulled.json()["updated"] is False
    rest_status = client.get(f"/projects/{project_id}/git").json()
    assert rest_status["remote"]["ahead"] == 0 and rest_status["remote"]["behind"] == 0
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "project.git_status", "params": {"id": project_id}})
        assert ws.receive_json()["result"] == rest_status
        ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "project.git_pull", "params": {"id": project_id}})
        assert ws.receive_json()["result"]["updated"] is False
        ws.send_json(
            {"jsonrpc": "2.0", "id": 3, "method": "project.git_set_remote", "params": {"id": project_id, "url": None}}
        )
        assert ws.receive_json()["result"]["remote"]["url"] is None


def test_api_add_project_from_remote(client, root, bare, tmp_path):
    # Publish a project from a second data directory, then clone it over the API.
    other = make_service(tmp_path / "data2")
    project = other.create_project("Shared")
    other.create_module(project.id, "Mod", "M")
    other.set_git_remote(project.id, str(bare))
    other.git_push(project.id)
    with client.websocket_connect("/ws") as watcher:
        created = client.post("/projects/from-remote", json={"url": str(bare)})
        assert created.status_code == 200 and created.json()["id"] == project.id
        assert watcher.receive_json() == {"event": "changed", "project_id": project.id}
    assert created.json()["name"] == "Shared"
    assert client.post("/projects/from-remote", json={"url": str(bare)}).status_code == 409
    assert client.post("/projects/from-remote", json={"url": "/nope/missing.git"}).status_code == 400
