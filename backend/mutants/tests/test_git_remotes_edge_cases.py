"""Edge cases of git remotes: pull with pending/diverged state, baseline
divergence notes, auto-push failures, remote reconfiguration, clone
validation, the sync-conflict guard, and the REST / WebSocket contracts.

Same rules as ``test_git_remotes.py`` (whose helpers and fixtures are
reused): every repository lives in pytest's ``tmp_path``, remotes are local
bare repositories (never a network URL), ``conftest.py`` isolates git from
the user's configuration, and ``git()`` refuses to run in the checkout.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore
from test_git_remotes import (  # noqa: F401 - fixtures are used by name
    CHECKOUT,
    Clock,
    _project_with_requirement,
    bare,
    client,
    git,
    make_service,
    root,
    service,
)


def _edit_project_json(project_dir: Path, **changes) -> None:
    """A pending (uncommitted) data change: rewrite project.json fields on disk."""
    path = project_dir / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _orphan_commit(folder: Path) -> str:
    """A commit with no parent (HEAD's tree), unrelated to every branch."""
    return git(folder, "commit-tree", "HEAD^{tree}", "-m", "orphan").strip()


def _commit_count(folder: Path) -> int:
    return int(git(folder, "rev-list", "--count", "HEAD").strip())


def _clone_instance(bare, tmp_path, name="data2"):
    other_root = tmp_path / name
    other_root.mkdir()
    other = make_service(other_root)
    project = other.add_project_from_remote(str(bare))
    return other, other_root, project


def _crafted_remote(tmp_path, **project_json_changes) -> Path:
    """A bare remote whose 'current' branch carries a hand-edited project.json."""
    scratch = tmp_path / "craft-data"
    scratch.mkdir()
    crafter = make_service(scratch)
    project = crafter.create_project("Crafted")
    folder = scratch / project.id
    _edit_project_json(folder, **project_json_changes)
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "craft")
    crafted_bare = tmp_path / "crafted.git"
    git(tmp_path, "init", "--bare", "-q", str(crafted_bare))
    git(folder, "push", "-q", str(crafted_bare), "refs/heads/current")
    return crafted_bare


# --- pull --------------------------------------------------------------------------


def test_pull_commits_pending_changes_first(service, root, bare):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    folder = root / project.id
    before = _commit_count(folder)
    _edit_project_json(folder, description="edited on disk")
    assert git(folder, "status", "--porcelain").strip() != ""
    result = service.git_pull(project.id)
    # Nothing new on the remote: not updated, but the pending change is committed.
    assert result["updated"] is False and result["notes"] == []
    assert git(folder, "status", "--porcelain").strip() == ""
    assert _commit_count(folder) == before + 1
    assert service.git_status(project.id)["pending_changes"] == 0


def test_pull_with_pending_changes_and_remote_ahead_flags_conflict(service, root, bare, tmp_path):
    project, module, req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    other, other_root, _cloned = _clone_instance(bare, tmp_path)
    service.update_requirement(project.id, module.id, req.id, {"object_text": "remote ahead"})
    service.git_push(project.id)
    # The clone has an uncommitted local edit: pulling commits it, which diverges.
    folder = other_root / project.id
    _edit_project_json(folder, description="local pending work")
    with pytest.raises(ConflictError, match="Sync conflict"):
        other.git_pull(project.id)
    # The pending work was committed (never lost), and the conflict is visible.
    assert git(folder, "status", "--porcelain").strip() == ""
    remote = other.git_status(project.id)["remote"]
    assert remote["sync_conflict"] is True
    assert remote["ahead"] >= 1 and remote["behind"] == 1


def test_pull_on_detached_repo_is_conflict(service, root, bare):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    folder = root / project.id
    git(folder, "checkout", "-q", "--detach")
    with pytest.raises(ConflictError):
        service.git_pull(project.id)
    git(folder, "checkout", "-q", "current")
    assert service.git_pull(project.id)["updated"] is False


def test_pull_notes_remote_without_current_branch(service, root, bare):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))  # the bare remote is still empty
    result = service.git_pull(project.id)
    assert result["updated"] is False
    assert any("no 'current' branch" in note for note in result["notes"])


def test_pull_keeps_diverged_baseline_branch_with_note(service, root, bare, tmp_path):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.create_baseline(project.id, "v1", "First")
    service.git_push(project.id)
    other, other_root, _cloned = _clone_instance(bare, tmp_path)
    folder = other_root / project.id
    # Hand-move the clone's local baseline branch to an unrelated commit.
    orphan = _orphan_commit(folder)
    git(folder, "update-ref", "refs/heads/baseline", orphan)
    result = other.git_pull(project.id)
    assert any("diverged" in note and "not moved" in note for note in result["notes"])
    assert git(folder, "rev-parse", "refs/heads/baseline").strip() == orphan
    # 'current' itself did not diverge: mutations stay allowed.
    assert other.git_status(project.id)["remote"]["sync_conflict"] is False


def test_pull_keeps_differing_baseline_tags_with_note(service, root, bare, tmp_path):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.create_baseline(project.id, "v1", "First")
    service.git_push(project.id)
    other, other_root, _cloned = _clone_instance(bare, tmp_path)
    folder = other_root / project.id
    # The clone tags baseline/v2 locally at an unrelated commit; the first
    # instance publishes its own, different baseline/v2.
    orphan = _orphan_commit(folder)
    git(folder, "tag", "baseline/v2", orphan)
    service.create_baseline(project.id, "v2", "Second")
    service.git_push(project.id)
    result = other.git_pull(project.id)
    assert any("baseline tags differ" in note for note in result["notes"])
    assert git(folder, "rev-parse", "refs/tags/baseline/v2").strip() == orphan


# --- push and auto-push --------------------------------------------------------------


def test_push_publishes_every_baseline_tag_including_dotted_names(service, root, bare):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.create_baseline(project.id, "v1.0-rc.1", "Candidate")
    service.create_baseline(project.id, "v1.0_final", "Final")
    result = service.git_push(project.id)
    assert "refs/tags/baseline/v1.0-rc.1" in result["pushed"]
    assert "refs/tags/baseline/v1.0_final" in result["pushed"]
    refs = git(root / project.id, "ls-remote", str(bare))
    assert "refs/tags/baseline/v1.0-rc.1" in refs
    assert "refs/tags/baseline/v1.0_final" in refs


def test_auto_push_failure_is_recorded_then_cleared(service, root, bare, tmp_path, monkeypatch):
    project, module, req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.set_git_auto_push(project.id, True)
    service.git_push(project.id)
    # The remote disappears: the commit still lands, the failure is recorded.
    hidden = tmp_path / "hidden.git"
    bare.rename(hidden)
    updated = service.update_requirement(project.id, module.id, req.id, {"object_text": "offline"})
    assert updated.attributes["object_text"] == "offline"
    status = service.git_status(project.id)
    assert status["last_error"] is not None and "Auto-push failed" in status["last_error"]
    assert status["head"]["subject"]  # the commit itself exists
    # The remote comes back: the next mutation pushes and clears the error.
    hidden.rename(bare)
    service.update_requirement(project.id, module.id, req.id, {"object_text": "online"})
    assert service.git_status(project.id)["last_error"] is None
    head = git(root / project.id, "rev-parse", "HEAD").strip()
    assert f"{head}\trefs/heads/current" in git(root / project.id, "ls-remote", str(bare))


def test_auto_push_rejection_is_recorded_not_raised(service, root, bare, tmp_path):
    project, module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    other, other_root, _cloned = _clone_instance(bare, tmp_path)
    other.set_git_auto_push(project.id, True)
    service.add_requirement(project.id, module.id, {"object_text": "pushed first"})
    service.git_push(project.id)  # the first instance wins
    # The clone's mutation succeeds; its auto-push is rejected and recorded.
    added = other.add_requirement(project.id, module.id, {"object_text": "stays local"})
    assert added.attributes["object_text"] == "stays local"
    status = other.git_status(project.id)
    assert status["last_error"] is not None and "Auto-push failed" in status["last_error"]
    local_head = git(other_root / project.id, "rev-parse", "HEAD").strip()
    assert f"{local_head}\trefs/heads/current" not in git(other_root / project.id, "ls-remote", str(bare))


def test_auto_push_publishes_new_baselines(service, root, bare):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.set_git_auto_push(project.id, True)
    service.create_baseline(project.id, "v1", "First")
    refs = git(root / project.id, "ls-remote", str(bare))
    assert "refs/heads/current" in refs
    assert "refs/heads/baseline" in refs
    assert "refs/tags/baseline/v1" in refs
    assert service.git_status(project.id)["last_error"] is None


# --- configure ----------------------------------------------------------------------


def test_set_remote_replaces_existing_url(service, root, bare, tmp_path):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    second = tmp_path / "second.git"
    git(tmp_path, "init", "--bare", "-q", str(second))
    status = service.set_git_remote(project.id, str(second))
    assert status["remote"]["url"] == str(second)
    assert git(root / project.id, "remote", "get-url", "origin").strip() == str(second)
    service.git_push(project.id)
    assert "refs/heads/current" in git(root / project.id, "ls-remote", str(second))
    assert "refs/heads/current" not in git(root / project.id, "ls-remote", str(bare))


def test_removing_remote_disables_auto_push_and_mutations_still_work(service, root, bare):
    project, module, req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.set_git_auto_push(project.id, True)
    status = service.set_git_remote(project.id, "")
    assert status["remote"]["url"] is None
    assert status["remote"]["auto_push"] is False
    service.update_requirement(project.id, module.id, req.id, {"object_text": "no remote"})
    assert service.git_status(project.id)["last_error"] is None
    assert "refs/heads/current" not in git(root / project.id, "ls-remote", str(bare))


def test_remote_and_auto_push_survive_a_restart(service, root, bare):
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.set_git_auto_push(project.id, True)
    reloaded = make_service(root)  # fresh store and service: .git/config persists
    remote = reloaded.git_status(project.id)["remote"]
    assert remote["url"] == str(bare)
    assert remote["auto_push"] is True


# --- clone validation ----------------------------------------------------------------


def test_clone_rejects_remote_without_current_branch(service, root, bare, tmp_path):
    # A remote with only a 'main' branch (not a ReqManTool project remote).
    scratch = tmp_path / "plain"
    scratch.mkdir()
    git(tmp_path, "init", "-q", "-b", "main", str(scratch))
    (scratch / "README").write_text("hi\n", encoding="utf-8")
    git(scratch, "add", "-A")
    git(scratch, "commit", "-q", "-m", "init")
    git(scratch, "push", "-q", str(bare), "refs/heads/main")
    with pytest.raises(ValueError):
        service.add_project_from_remote(str(bare))
    assert service.list_projects() == []
    assert not (root / ".reqman-staging").exists()


def test_clone_rejects_non_slug_project_id(service, root, tmp_path):
    crafted = _crafted_remote(tmp_path, id="Bad Id!")
    with pytest.raises(ValueError, match="unusable project id"):
        service.add_project_from_remote(str(crafted))
    assert service.list_projects() == []
    assert not (root / ".reqman-staging").exists()
    assert not (root / "crafted").exists()


def test_clone_rejects_newer_format_project(service, root, tmp_path):
    crafted = _crafted_remote(tmp_path, format_version=999)
    with pytest.raises(ValueError, match="newer ReqManTool"):
        service.add_project_from_remote(str(crafted))
    assert service.list_projects() == []
    assert not (root / ".reqman-staging").exists()


# --- sync-conflict guard --------------------------------------------------------------


def test_mutations_allowed_after_git_dir_is_deleted(service, root, bare):
    project, module, req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    shutil.rmtree(root / project.id / ".git")
    # No repository, no conflict: the guard never blocks plain file editing.
    updated = service.update_requirement(project.id, module.id, req.id, {"object_text": "still works"})
    assert updated.attributes["object_text"] == "still works"
    reloaded = make_service(root)
    assert (
        reloaded.get_requirement(project.id, module.id, req.id).attributes["object_text"] == "still works"
    )


def test_sync_conflict_guard_is_off_with_git_off(service, root, bare, tmp_path):
    # Build a real conflict with git on.
    project = service.create_project("Alpha")
    module = service.create_module(project.id, "System", "SYRS")
    req_a = service.add_requirement(project.id, module.id, {"object_text": "A"})
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    other, _other_root, _cloned = _clone_instance(bare, tmp_path)
    service.update_requirement(project.id, module.id, req_a.id, {"object_text": "here"})
    service.git_push(project.id)
    other.update_requirement(project.id, module.id, req_a.id, {"object_text": "there"})
    with pytest.raises(ConflictError, match="Sync conflict"):
        other.git_pull(project.id)
    with pytest.raises(ConflictError, match="Sync conflict"):
        other.update_requirement(project.id, module.id, req_a.id, {"object_text": "blocked"})
    # The same data directory served with REQMAN_GIT=off: plain files, no guard.
    plain = ProjectService(ProjectStore(other.store.root))
    updated = plain.update_requirement(project.id, module.id, req_a.id, {"object_text": "git off"})
    assert updated.attributes["object_text"] == "git off"


# --- REST / WebSocket ----------------------------------------------------------------


def test_api_auto_push_validation_and_ws_missing_param(client, root, bare):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    # Auto-push without a remote: 400 with the rule in the message.
    resp = client.put(f"/projects/{project_id}/git/auto-push", json={"enabled": True})
    assert resp.status_code == 400 and "remote" in resp.json()["detail"]
    assert client.put(f"/projects/{project_id}/git/remote", json={"url": str(bare)}).status_code == 200
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as other:
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "project.git_set_auto_push", "params": {"id": project_id}})
        reply = ws.receive_json()
        assert reply["error"]["code"] == -32602  # missing 'enabled'
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "project.git_set_auto_push",
                "params": {"id": project_id, "enabled": True},
            }
        )
        assert ws.receive_json()["result"]["remote"]["auto_push"] is True
        assert other.receive_json() == {"event": "changed", "project_id": project_id}
    assert client.get(f"/projects/{project_id}/git").json()["remote"]["auto_push"] is True


def test_api_pull_conflict_broadcasts_and_blocks_rest_and_ws_mutations(client, root, bare, tmp_path):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "M"}).json()["id"]
    req_id = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "x"}}
    ).json()["id"]
    assert client.put(f"/projects/{project_id}/git/remote", json={"url": str(bare)}).status_code == 200
    assert client.post(f"/projects/{project_id}/git/push").status_code == 200
    # Another instance clones, changes the same project and pushes first.
    other, _other_root, _cloned = _clone_instance(bare, tmp_path)
    other.update_requirement(project_id, module_id, req_id, {"object_text": "theirs"})
    other.git_push(project_id)
    # This instance diverges locally (allowed: the conflict is unknown until a pull).
    resp = client.put(
        f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}",
        json={"attributes": {"object_text": "ours"}},
    )
    assert resp.status_code == 200
    with client.websocket_connect("/ws") as watcher:
        pulled = client.post(f"/projects/{project_id}/git/pull")
        assert pulled.status_code == 409
        assert "Sync conflict" in pulled.json()["detail"]
        # The failed pull already fetched: the change is broadcast anyway.
        assert watcher.receive_json() == {"event": "changed", "project_id": project_id}
    status = client.get(f"/projects/{project_id}/git").json()
    assert status["remote"]["sync_conflict"] is True
    # Every mutation is now refused, over REST and over the WebSocket alike.
    blocked = client.put(
        f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}",
        json={"attributes": {"object_text": "blocked"}},
    )
    assert blocked.status_code == 409 and "Sync conflict" in blocked.json()["detail"]
    assert client.post(f"/projects/{project_id}/baselines", json={"name": "v1"}).status_code == 409
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "requirement.update",
                "params": {
                    "project_id": project_id,
                    "module_id": module_id,
                    "id": req_id,
                    "attributes": {"object_text": "blocked"},
                },
            }
        )
        reply = ws.receive_json()
        assert reply["error"]["code"] == -32602 and "Sync conflict" in reply["error"]["message"]
    # Reading is never blocked.
    read = client.get(f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}")
    assert read.status_code == 200 and read.json()["attributes"]["object_text"] == "ours"


def test_ws_create_from_remote_parity_and_errors(client, root, bare, tmp_path):
    publisher = make_service(tmp_path / "data2")
    project = publisher.create_project("Shared")
    publisher.create_module(project.id, "Mod", "M")
    publisher.set_git_remote(project.id, str(bare))
    publisher.git_push(project.id)
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as watcher:
        ws.send_json(
            {"jsonrpc": "2.0", "id": 1, "method": "project.create_from_remote", "params": {"url": str(bare)}}
        )
        result = ws.receive_json()["result"]
        assert result["id"] == project.id and result["name"] == "Shared"
        assert watcher.receive_json() == {"event": "changed", "project_id": project.id}
        # A second clone of the same remote: the id is taken (an error, no broadcast).
        ws.send_json(
            {"jsonrpc": "2.0", "id": 2, "method": "project.create_from_remote", "params": {"url": str(bare)}}
        )
        assert "already exists" in ws.receive_json()["error"]["message"]
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "project.create_from_remote",
                "params": {"url": str(tmp_path / "nope.git")},
            }
        )
        assert ws.receive_json()["error"]["code"] == -32602
        ws.send_json({"jsonrpc": "2.0", "id": 4, "method": "project.git_status", "params": {"id": project.id}})
        assert ws.receive_json()["result"]["remote"]["url"] == str(bare)
    assert client.get(f"/projects/{project.id}").status_code == 200
    assert not (root / ".reqman-staging").exists()


def test_api_clone_rejects_bad_project_json(client, root, tmp_path):
    bad_id = _crafted_remote(tmp_path, id="Bad Id!")
    resp = client.post("/projects/from-remote", json={"url": str(bad_id)})
    assert resp.status_code == 400 and "unusable project id" in resp.json()["detail"]
    newer_dir = tmp_path / "newer"
    newer_dir.mkdir()
    newer = _crafted_remote(newer_dir, format_version=999)
    resp = client.post("/projects/from-remote", json={"url": str(newer)})
    assert resp.status_code == 409 and "newer ReqManTool" in resp.json()["detail"]
    assert client.get("/projects").json() == []
    assert not (root / ".reqman-staging").exists()
