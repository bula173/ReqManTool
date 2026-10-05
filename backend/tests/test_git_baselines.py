"""Smoke tests for baselines (slice 2b): create, list, browse, compare, git log, REST/WS parity.

Every test works in pytest's ``tmp_path`` (outside any repository) with its
own repositories; ``conftest.py`` isolates git from the user's configuration.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from reqman import __version__
from reqman.git_repo import GitHistory, GitSettings, validate_baseline_name
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


# --- create -----------------------------------------------------------------------


def test_create_baseline_tags_and_fast_forwards(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    baseline = service.create_baseline(project.id, " v1 ", "First release")
    assert baseline["name"] == "v1"
    head = git(folder, "rev-parse", "HEAD").strip()
    assert baseline["commit"] == head
    assert git(folder, "rev-parse", "refs/heads/baseline").strip() == head
    assert git(folder, "rev-parse", "refs/tags/baseline/v1^{commit}").strip() == head
    message = git(folder, "tag", "-l", "--format=%(contents)", "baseline/v1")
    assert message.startswith("Baseline v1")
    assert "First release" in message
    assert "Reqman-Baseline: v1" in message
    assert f"Reqman-Tool-Version: {__version__}" in message
    assert "Reqman-Project-Format: 1" in message
    assert baseline["tool_version"] == __version__ and baseline["format_version"] == 1
    assert git(folder, "tag", "-l", "--format=%(taggername)", "baseline/v1").strip() == "Test Author"
    # The squash window is closed: the next change starts a new commit and
    # never amends the baselined one (ref containment).
    before = len(git(folder, "log", "--format=%H").splitlines())
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    assert len(git(folder, "log", "--format=%H").splitlines()) == before + 1
    assert git(folder, "rev-parse", "refs/tags/baseline/v1^{commit}").strip() == head


def test_create_baseline_commits_pending_changes_first(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    path = folder / "modules" / module.id / "requirements" / f"{req.id}.json"
    data = json.loads(path.read_text())
    data["attributes"]["object_text"] = "Hand edited"
    path.write_text(json.dumps(data, indent=2) + "\n")
    service.create_baseline(project.id, "v1")
    assert git(folder, "status", "--porcelain") == ""
    shown = git(folder, "show", f"refs/tags/baseline/v1^{{commit}}:modules/{module.id}/requirements/{req.id}.json")
    assert "Hand edited" in shown


def test_create_baseline_name_and_state_errors(service, root):
    project, _module, _req = _project_with_requirement(service)
    for bad in ("", "  ", "a b", "-x", "a..b", "x.", "x.lock", "current", "compare", "a/b", "x" * 101):
        with pytest.raises(ValueError):
            validate_baseline_name(bad)
        if bad.strip():
            with pytest.raises(ValueError):
                service.create_baseline(project.id, bad)
    service.create_baseline(project.id, "v1")
    with pytest.raises(ConflictError):  # duplicate
        service.create_baseline(project.id, "v1")
    # Not ready (detached HEAD) is a conflict.
    git(root / project.id, "checkout", "-q", "--detach")
    with pytest.raises(ConflictError):
        service.create_baseline(project.id, "v2")
    # Git off is a plain 400.
    plain = ProjectService(ProjectStore(root))
    with pytest.raises(ValueError) as err:
        plain.create_baseline(project.id, "v2")
    assert not isinstance(err.value, ConflictError)


def test_hand_moved_baseline_branch_refuses_and_changes_nothing(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    service.create_baseline(project.id, "v1")
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    # Move `baseline` to a commit that is not on `current` (a parentless commit).
    tree = git(folder, "rev-parse", "HEAD^{tree}").strip()
    orphan = git(folder, "commit-tree", tree, "-m", "elsewhere").strip()
    git(folder, "update-ref", "refs/heads/baseline", orphan)
    with pytest.raises(ConflictError):
        service.create_baseline(project.id, "v2")
    assert git(folder, "rev-parse", "refs/heads/baseline").strip() == orphan
    assert "baseline/v2" not in git(folder, "tag", "-l")


# --- list -------------------------------------------------------------------------


def test_list_baselines_newest_first_with_lightweight_and_foreign_tags(service, root):
    project, _module, _req = _project_with_requirement(service)
    folder = root / project.id
    service.create_baseline(project.id, "v1", "one")
    git(folder, "tag", "baseline/light")  # lightweight, by hand

    def foreign_tag(name: str, date: str) -> None:
        env = dict(os.environ, GIT_COMMITTER_DATE=date)
        subprocess.run(
            ["git", "tag", "-a", f"baseline/{name}", "-m", name],
            cwd=folder, env=env, capture_output=True, check=True,
        )

    foreign_tag("old", "2001-01-01T00:00:00 +0000")  # annotated, no Reqman trailers
    foreign_tag("new", "2030-01-01T00:00:00 +0000")
    listed = service.list_baselines(project.id)
    assert listed["available"] is True
    names = [b["name"] for b in listed["baselines"]]
    assert set(names) == {"v1", "light", "old", "new"}
    assert names[0] == "new" and names[-1] == "old"  # newest first (creator date)
    v1 = next(b for b in listed["baselines"] if b["name"] == "v1")
    assert v1["annotated"] is True and v1["description"] == "one"
    assert v1["tagger"] == {"name": "Test Author", "email": "author@example.com"}
    assert v1["tool_version"] == __version__ and v1["format_version"] == 1
    assert v1["date"]  # the Reqman-Baseline-Date trailer
    light = next(b for b in listed["baselines"] if b["name"] == "light")
    assert light["annotated"] is False and light["description"] is None
    assert light["commit"] == git(folder, "rev-parse", "HEAD").strip()
    old = next(b for b in listed["baselines"] if b["name"] == "old")
    assert old["annotated"] is True and old["tool_version"] is None


def test_list_baselines_without_repo(root):
    service = ProjectService(ProjectStore(root))  # git off
    project = service.create_project("Plain")
    listed = make_service(root).list_baselines(project.id)
    assert listed["available"] is False and listed["baselines"] == []


# --- browse -----------------------------------------------------------------------


def test_browse_baseline_is_frozen_in_time(service, root, clock):
    project, module, req = _project_with_requirement(service)
    service.create_baseline(project.id, "v1")
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    other = service.create_module(project.id, "Later", "LTR")
    service.add_requirement(project.id, other.id, {"object_text": "After the baseline"})
    snapshot = service.get_baseline(project.id, "v1")
    assert snapshot["name"] == "v1"
    assert [m["id"] for m in snapshot["modules"]] == [module.id]
    assert snapshot["project"]["name"] == "Alpha"
    requirements = service.baseline_requirements(project.id, "v1", module.id)
    assert [r.id for r in requirements] == [req.id]
    assert requirements[0].attributes["status"] == "Draft"  # not the later "Reviewed"
    with pytest.raises(FileNotFoundError):
        service.baseline_requirements(project.id, "v1", other.id)
    with pytest.raises(FileNotFoundError):
        service.get_baseline(project.id, "nope")


def test_browse_newer_format_baseline_is_a_clear_error(service, root):
    project, _module, _req = _project_with_requirement(service)
    folder = root / project.id
    path = folder / "project.json"
    original = path.read_text()
    data = json.loads(original)
    data["format_version"] = 99
    path.write_text(json.dumps(data, indent=2) + "\n")
    git(folder, "add", "project.json")
    git(folder, "commit", "-q", "-m", "newer format")
    git(folder, "tag", "-a", "baseline/future", "-m", "future")
    path.write_text(original)
    git(folder, "add", "project.json")
    git(folder, "commit", "-q", "-m", "back to normal")
    from reqman.formats import NewerFormatError

    with pytest.raises(NewerFormatError):
        service.get_baseline(project.id, "future")
    with pytest.raises(NewerFormatError):
        service.compare_baselines(project.id, "future", "current")


# --- compare ----------------------------------------------------------------------


def test_compare_baseline_with_current_and_other_baseline(service, root, clock):
    project, module, req = _project_with_requirement(service)
    other = service.create_module(project.id, "Software", "SRS")
    target = service.add_requirement(project.id, other.id, {"object_text": "Target"})
    config = service.create_link_configuration(project.id, "Satisfies", module.id, other.id)
    service.create_baseline(project.id, "v1")

    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    removed = target
    service.remove_requirement(project.id, other.id, removed.id)
    added = service.add_requirement(project.id, module.id, {"object_text": "New child"}, parent_id=req.id)
    kept = service.add_requirement(project.id, other.id, {"object_text": "Kept"})
    service.link_requirements(project.id, config.id, req.id, kept.id)
    service.add_attribute_definition(project.id, "prio", "Priority")
    third = service.create_module(project.id, "Extra", "EX")
    service.create_baseline(project.id, "v2")

    # Baseline ↔ current including an uncommitted hand edit.
    path = root / project.id / "modules" / module.id / "requirements" / f"{req.id}.json"
    data = json.loads(path.read_text())
    data["attributes"]["object_text"] = "Uncommitted"
    path.write_text(json.dumps(data, indent=2) + "\n")

    result = service.compare_baselines(project.id, "v1", "current")
    assert result["from"] == {"name": "v1", "commit": result["from"]["commit"]} and result["from"]["commit"]
    assert result["to"] == {"name": "current", "commit": None}
    by_module = {m["module_id"]: m for m in result["modules"]}
    assert by_module[third.id]["change"] == "added"
    assert by_module[module.id]["added"] == [added.id]
    assert by_module[other.id]["removed"] == [removed.id]
    modified = {m["id"]: m["diffs"] for m in by_module[module.id]["modified"]}
    kinds = {(d["kind"], d.get("attribute_id")) for d in modified[req.id]}
    assert ("attribute", "status") in kinds and ("attribute", "object_text") in kinds
    # The link mirror attribute is not listed per requirement; the link itself is.
    assert ("attribute", config.id) not in kinds
    assert {(entry["source_id"], entry["target_id"]) for entry in result["links"]["added"]} == {(req.id, kept.id)}
    assert result["links"]["added"][0]["name"] == "Satisfies"
    assert [a["name"] for a in result["attributes"]["added"]] == ["Priority"]
    assert result["summary"]["requirements_modified"] >= 1

    # Parent/order changes show up as diffs.
    base_to_base = service.compare_baselines(project.id, "v1", "v2")
    v2_modified = {
        m["id"]: m["diffs"]
        for entry in base_to_base["modules"]
        if entry["module_id"] == module.id
        for m in entry["modified"]
    }
    assert ("attribute", "status") in {(d["kind"], d.get("attribute_id")) for d in v2_modified[req.id]}
    same = service.compare_baselines(project.id, "v2", "v2")
    assert same["modules"] == [] and same["summary"]["requirements_modified"] == 0

    service.move_requirement(project.id, module.id, added.id, None)
    moved = service.compare_baselines(project.id, "v2", "current")
    moved_diffs = {
        m["id"]: m["diffs"]
        for entry in moved["modules"]
        if entry["module_id"] == module.id
        for m in entry["modified"]
    }
    assert any(d["kind"] == "parent" for d in moved_diffs[added.id])

    with pytest.raises(FileNotFoundError):
        service.compare_baselines(project.id, "missing", "current")


# --- git log ----------------------------------------------------------------------


def test_git_log_lists_recent_commits(service, root, clock):
    project, module, req = _project_with_requirement(service)
    clock.offset = 3600
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    log = service.git_log(project.id)
    assert log["available"] is True and log["truncated"] is False
    assert log["entries"][0]["subject"] == "SYRS-0001: Status Draft → Reviewed"
    assert log["entries"][-1]["subject"] == "Create project 'Alpha'"
    assert service.git_log(project.id, 1)["entries"] == log["entries"][:1]
    assert service.git_log(project.id, 1)["truncated"] is True
    with pytest.raises(ValueError):
        service.git_log(project.id, 0)
    plain = ProjectService(ProjectStore(root))
    assert plain.git_log(project.id)["available"] is False


# --- REST / WebSocket ---------------------------------------------------------------


@pytest.fixture
def client(root, monkeypatch):
    monkeypatch.setenv("REQMAN_GIT", "on")
    monkeypatch.setenv("REQMAN_DATA_DIR", str(root))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(root.parent / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_api_baselines_and_ws_parity(client):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "M"}).json()["id"]
    req_id = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "x"}}
    ).json()["id"]

    with client.websocket_connect("/ws") as other:
        created = client.post(f"/projects/{project_id}/baselines", json={"name": "v1", "description": "d"})
        assert created.status_code == 200 and created.json()["name"] == "v1"
        assert other.receive_json() == {"event": "changed", "project_id": project_id}

    assert client.post(f"/projects/{project_id}/baselines", json={"name": "v1"}).status_code == 409
    assert client.post(f"/projects/{project_id}/baselines", json={"name": "a b"}).status_code == 400
    assert client.post("/projects/missing/baselines", json={"name": "v1"}).status_code == 404

    rest_list = client.get(f"/projects/{project_id}/baselines").json()
    assert [b["name"] for b in rest_list["baselines"]] == ["v1"]
    snapshot = client.get(f"/projects/{project_id}/baselines/v1").json()
    assert snapshot["project"]["id"] == project_id and [m["id"] for m in snapshot["modules"]] == [module_id]
    reqs = client.get(f"/projects/{project_id}/baselines/v1/modules/{module_id}/requirements").json()
    assert [r["id"] for r in reqs] == [req_id]
    assert client.get(f"/projects/{project_id}/baselines/nope").status_code == 404

    client.put(
        f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}",
        json={"attributes": {"object_text": "changed"}},
    )
    compare = client.get(f"/projects/{project_id}/baselines/compare?from=v1&to=current").json()
    assert compare["summary"]["requirements_modified"] == 1
    log = client.get(f"/projects/{project_id}/git/log?limit=5").json()
    assert log["available"] is True and len(log["entries"]) >= 1

    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as other:
        ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "baseline.list", "params": {"project_id": project_id}})
        assert ws.receive_json()["result"] == rest_list
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "baseline.requirements",
                "params": {"project_id": project_id, "name": "v1", "module_id": module_id},
            }
        )
        assert ws.receive_json()["result"] == reqs
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "baseline.compare",
                "params": {"project_id": project_id, "from": "v1", "to": "current"},
            }
        )
        assert ws.receive_json()["result"]["summary"] == compare["summary"]
        ws.send_json({"jsonrpc": "2.0", "id": 4, "method": "project.git_log", "params": {"id": project_id}})
        assert ws.receive_json()["result"]["entries"] == client.get(f"/projects/{project_id}/git/log").json()["entries"]
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "baseline.create",
                "params": {"project_id": project_id, "name": "v2"},
            }
        )
        assert ws.receive_json()["result"]["name"] == "v2"
        assert other.receive_json() == {"event": "changed", "project_id": project_id}
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "baseline.create",
                "params": {"project_id": project_id, "name": "v2"},
            }
        )
        assert ws.receive_json()["error"]["code"] == -32602
        ws.send_json(
            {"jsonrpc": "2.0", "id": 7, "method": "baseline.get", "params": {"project_id": project_id, "name": "gone"}}
        )
        assert ws.receive_json()["error"]["code"] == -32001
