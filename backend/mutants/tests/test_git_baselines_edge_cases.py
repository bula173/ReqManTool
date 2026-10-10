"""Edge cases of baselines (slice 2b): CAS rollback, failure broadcasts, paused
and stub projects, name limits, list ordering ties, broken committed files,
compare corners, log limit clamping and the squash window after a baseline.

Same rules as ``test_git_baselines.py`` (whose helpers and fixtures are
reused): every repository lives in pytest's ``tmp_path``, ``conftest.py``
isolates git from the user's configuration, and ``git()`` refuses to run in
the checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reqman.git_repo import BASELINE_REF, BaselineFailed, GitHistory, validate_baseline_name
from reqman.service import ConflictError, ProjectService
from reqman.store import ProjectStore
from test_git_baselines import (  # noqa: F401 - fixtures are used by name
    CHECKOUT,
    Clock,
    _project_with_requirement,
    client,
    clock,
    git,
    make_service,
    root,
    service,
)


def commit_count(folder: Path) -> int:
    return len(git(folder, "log", "--format=%H").splitlines())


def sabotage_branch_cas(monkeypatch, folder: Path):
    """Move ``refs/heads/baseline`` to HEAD just before every CAS, so the CAS fails."""
    original = GitHistory._repo
    hits: list[list[str]] = []

    def patched(self, info, args, **kwargs):
        if args and args[0] == "update-ref" and len(args) >= 3 and args[1] == BASELINE_REF:
            hits.append(list(args))
            git(folder, "update-ref", BASELINE_REF, git(folder, "rev-parse", "HEAD").strip())
        return original(self, info, args, **kwargs)

    monkeypatch.setattr(GitHistory, "_repo", patched)
    return hits


# --- create: rollback and refusals --------------------------------------------------


def test_branch_cas_failure_deletes_tag_and_records_error(service, root, monkeypatch):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    service.create_baseline(project.id, "v1")
    service.update_requirement(project.id, module.id, req.id, {"status": "Reviewed"})
    head = git(folder, "rev-parse", "HEAD").strip()

    hits = sabotage_branch_cas(monkeypatch, folder)
    with pytest.raises(ValueError) as err:
        service.create_baseline(project.id, "v2")
    assert isinstance(err.value, BaselineFailed)
    assert not isinstance(err.value, ConflictError)
    assert err.value.project_changed is True
    assert "v2" in str(err.value)
    assert hits, "the compare-and-swap on refs/heads/baseline never ran"
    # The tag was deleted again; the hand-moved branch is left alone.
    assert "baseline/v2" not in git(folder, "tag", "-l")
    assert git(folder, "rev-parse", BASELINE_REF).strip() == head
    assert "v2" in (service.git_status(project.id)["last_error"] or "")

    # Recovery: with the branch now on `current`'s history the retry succeeds
    # (the sabotage became a no-op) and clears the recorded error.
    baseline = service.create_baseline(project.id, "v2")
    assert baseline["commit"] == head
    assert git(folder, "rev-parse", "refs/tags/baseline/v2^{commit}").strip() == head
    assert service.git_status(project.id)["last_error"] is None


def test_create_baseline_on_paused_repo_conflicts_browse_still_works(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    service.create_baseline(project.id, "v1")
    git(folder, "checkout", "-q", "-b", "other")  # paused: another branch checked out
    with pytest.raises(ConflictError):
        service.create_baseline(project.id, "v2")
    assert "baseline/v2" not in git(folder, "tag", "-l")
    # Reading baselines keeps working while paused.
    listed = service.list_baselines(project.id)
    assert listed["available"] is True and [b["name"] for b in listed["baselines"]] == ["v1"]
    assert service.get_baseline(project.id, "v1")["project"]["name"] == "Alpha"
    assert [r.id for r in service.baseline_requirements(project.id, "v1", module.id)] == [req.id]
    assert service.compare_baselines(project.id, "v1", "v1")["modules"] == []


def test_create_baseline_with_missing_git_executable_is_plain_400(service, root, tmp_path):
    project, _module, _req = _project_with_requirement(service)
    broken = make_service(root, executable=str(tmp_path / "no-such-git"))
    with pytest.raises(ValueError) as err:
        broken.create_baseline(project.id, "v1")
    assert not isinstance(err.value, ConflictError)
    listed = broken.list_baselines(project.id)
    assert listed["available"] is False and listed["baselines"] == []


def test_stub_project_refuses_baseline_operations(service, root):
    project, module, _req = _project_with_requirement(service)
    service.create_baseline(project.id, "v1")
    (root / project.id / "project.json").write_text("not json\n")
    for call in (
        lambda: service.create_baseline(project.id, "v2"),
        lambda: service.list_baselines(project.id),
        lambda: service.get_baseline(project.id, "v1"),
        lambda: service.baseline_requirements(project.id, "v1", module.id),
        lambda: service.compare_baselines(project.id, "v1", "current"),
        lambda: service.git_log(project.id),
    ):
        with pytest.raises(ValueError) as err:
            call()
        assert not isinstance(err.value, (ConflictError, FileNotFoundError))


# --- names ---------------------------------------------------------------------------


def test_baseline_name_length_trim_and_unicode_limits(service, root):
    project, _module, _req = _project_with_requirement(service)
    longest = "x" * 100
    assert validate_baseline_name(f"  {longest}  ") == longest  # trimmed, 100 chars OK
    baseline = service.create_baseline(project.id, f"  {longest} ")
    assert baseline["name"] == longest
    assert f"baseline/{longest}" in git(root / project.id, "tag", "-l")
    for bad in ("x" * 101, "wersja-ś", "名前", "v@{1}", "v~1", "v^2", "v:1", "v\\1", "v*"):
        with pytest.raises(ValueError):
            validate_baseline_name(bad)
        with pytest.raises(ValueError):
            service.create_baseline(project.id, bad)
    assert git(root / project.id, "tag", "-l").strip() == f"baseline/{longest}"


# --- list ----------------------------------------------------------------------------


def test_list_baselines_order_is_newest_first_and_deterministic_on_ties(service, root):
    project, _module, _req = _project_with_requirement(service)
    folder = root / project.id
    service.create_baseline(project.id, "v1")

    def hand_tag(name: str, date: str) -> None:
        env = dict(os.environ, GIT_COMMITTER_DATE=date)
        subprocess.run(
            ["git", "tag", "-a", f"baseline/{name}", "-m", name],
            cwd=folder, env=env, capture_output=True, check=True,
        )

    hand_tag("tie-b", "2030-06-01T12:00:00 +0000")
    hand_tag("tie-a", "2030-06-01T12:00:00 +0000")  # same creation time
    hand_tag("old", "2001-01-01T00:00:00 +0000")
    names = [b["name"] for b in service.list_baselines(project.id)["baselines"]]
    assert set(names[:2]) == {"tie-a", "tie-b"}  # the tied pair is newest
    assert names[2:] == ["v1", "old"]
    # Ties do not make the order flap between calls.
    again = [b["name"] for b in service.list_baselines(project.id)["baselines"]]
    assert again == names


# --- browse: hand-committed broken files ---------------------------------------------


def test_browse_baseline_with_broken_committed_files_is_a_clear_error_not_a_crash(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    broken_module = service.create_module(project.id, "Broken", "BRK")
    (folder / "modules" / broken_module.id / "module.json").write_text("{ not json\n")
    (folder / "modules" / module.id / "requirements" / "zzz.json").write_text("[broken\n")
    service.create_baseline(project.id, "b1")  # commits the broken files too

    snapshot = service.get_baseline(project.id, "b1")
    assert [m["id"] for m in snapshot["modules"]] == [module.id]  # unreadable module skipped
    assert [r.id for r in service.baseline_requirements(project.id, "b1", module.id)] == [req.id]
    assert service.baseline_requirements(project.id, "b1", broken_module.id) == []

    # An unreadable committed project.json is a ValueError naming the file, never a 500.
    original = (folder / "project.json").read_text()
    (folder / "project.json").write_text("not json\n")
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "break project.json")
    git(folder, "tag", "-a", "baseline/b2", "-m", "b2")
    (folder / "project.json").write_text(original)
    git(folder, "add", "project.json")
    git(folder, "commit", "-q", "-m", "restore")
    with pytest.raises(ValueError, match="project.json") as err:
        service.get_baseline(project.id, "b2")
    assert not isinstance(err.value, FileNotFoundError)
    with pytest.raises(ValueError, match="project.json"):
        service.baseline_requirements(project.id, "b2", module.id)
    # Compare tolerates it (the unreadable side simply has no project data).
    result = service.compare_baselines(project.id, "b2", "current")
    assert result["from"]["name"] == "b2" and "summary" in result


# --- compare corners -----------------------------------------------------------------


def test_compare_module_removed_current_current_and_reserved_names(service, root):
    project, module, req = _project_with_requirement(service)
    service.create_baseline(project.id, "v1")
    other = service.create_module(project.id, "Victim", "VIC")
    gone = service.add_requirement(project.id, other.id, {"object_text": "Doomed"})

    # A module missing on one side is reported as added/removed per direction.
    result = service.compare_baselines(project.id, "current", "v1")
    by_module = {m["module_id"]: m for m in result["modules"]}
    assert by_module[other.id]["change"] == "removed"
    assert by_module[other.id]["removed"] == [gone.id]
    assert result["summary"]["modules_removed"] == 1
    assert result["summary"]["requirements_removed"] == 1

    # Purging empties the module but never removes it (server-owned rule).
    service.purge_module(project.id, other.id)
    purged = service.compare_baselines(project.id, "v1", "current")
    by_module = {m["module_id"]: m for m in purged["modules"]}
    assert by_module[other.id]["change"] == "added" and by_module[other.id]["added"] == []
    assert purged["summary"]["modules_added"] == 1 and purged["summary"]["requirements_added"] == 0

    same = service.compare_baselines(project.id, "current", "current")
    assert same["from"] == {"name": "current", "commit": None}
    assert same["to"] == {"name": "current", "commit": None}
    assert same["modules"] == [] and not any(same["summary"].values())

    # The reserved names are not browsable baselines, and an unknown name on
    # either compare side is 404.
    with pytest.raises(FileNotFoundError):
        service.get_baseline(project.id, "current")
    with pytest.raises(FileNotFoundError):
        service.get_baseline(project.id, "compare")
    with pytest.raises(FileNotFoundError):
        service.compare_baselines(project.id, "v1", "compare")
    with pytest.raises(FileNotFoundError):
        service.compare_baselines(project.id, "v1", "nope")
    del module, req


# --- git log limits ------------------------------------------------------------------


def test_git_log_limit_defaults_to_20_and_is_capped_at_200(service, root, monkeypatch):
    project, _module, _req = _project_with_requirement(service)
    captured: list[int] = []
    real_log = service.history.log
    monkeypatch.setattr(
        service.history, "log", lambda path, limit: captured.append(limit) or real_log(path, limit)
    )
    assert service.git_log(project.id)["available"] is True
    service.git_log(project.id, 1000)
    service.git_log(project.id, 200)
    assert captured == [20, 200, 200]
    for bad in (0, -1, True, "5", 2.0):
        with pytest.raises(ValueError):
            service.git_log(project.id, bad)


# --- squash after a baseline ---------------------------------------------------------


def test_mutations_after_baseline_squash_into_the_post_baseline_commit_only(service, root):
    project, module, req = _project_with_requirement(service)
    folder = root / project.id
    service.create_baseline(project.id, "v1")
    base = git(folder, "rev-parse", "HEAD").strip()

    service.update_requirement(project.id, module.id, req.id, {"object_text": "First"})
    first = git(folder, "rev-parse", "HEAD").strip()
    count = commit_count(folder)
    assert first != base  # never amends the baselined commit

    # Still inside the squash window: the second change amends the new
    # commit, not the baselined one.
    service.update_requirement(project.id, module.id, req.id, {"object_text": "Second"})
    second = git(folder, "rev-parse", "HEAD").strip()
    assert commit_count(folder) == count
    assert second != first and second != base
    assert git(folder, "rev-parse", "HEAD^").strip() == base
    assert git(folder, "rev-parse", "refs/tags/baseline/v1^{commit}").strip() == base
    assert git(folder, "rev-parse", BASELINE_REF).strip() == base
    shown = git(folder, "show", f"HEAD:modules/{module.id}/requirements/{req.id}.json")
    assert "Second" in shown and "First" not in shown


# --- REST / WebSocket ----------------------------------------------------------------


def test_api_baseline_failed_broadcasts_and_log_limit_contract(client, root, monkeypatch):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "M"}).json()["id"]
    req_id = client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "x"}}
    ).json()["id"]
    folder = root / project_id

    assert client.post(f"/projects/{project_id}/baselines", json={"name": "v1"}).status_code == 200
    client.put(
        f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}",
        json={"attributes": {"object_text": "after v1"}},
    )

    sabotage_branch_cas(monkeypatch, folder)

    # REST: the part-way failure is a 400 that still broadcasts "changed".
    with client.websocket_connect("/ws") as other:
        failed = client.post(f"/projects/{project_id}/baselines", json={"name": "v2"})
        assert failed.status_code == 400
        assert "v2" in failed.json()["detail"]
        assert other.receive_json() == {"event": "changed", "project_id": project_id}
        assert "baseline/v2" not in git(folder, "tag", "-l")
        assert client.get(f"/projects/{project_id}/git").json()["last_error"]

        # Advance HEAD past the sabotaged branch so the next CAS fails too.
        client.put(
            f"/projects/{project_id}/modules/{module_id}/requirements/{req_id}",
            json={"attributes": {"object_text": "after failure"}},
        )
        other.receive_json()  # the mutation's own broadcast

    # WebSocket: same failure is -32602 and still broadcasts to the others.
    with client.websocket_connect("/ws") as ws, client.websocket_connect("/ws") as other:
        ws.send_json(
            {"jsonrpc": "2.0", "id": 1, "method": "baseline.create",
             "params": {"project_id": project_id, "name": "v3"}}
        )
        reply = ws.receive_json()
        assert reply["error"]["code"] == -32602 and "v3" in reply["error"]["message"]
        assert other.receive_json() == {"event": "changed", "project_id": project_id}
        assert "baseline/v3" not in git(folder, "tag", "-l")

        # git log limits: WS rejects a non-integer limit, REST rejects < 1.
        ws.send_json(
            {"jsonrpc": "2.0", "id": 2, "method": "project.git_log",
             "params": {"id": project_id, "limit": "x"}}
        )
        assert ws.receive_json()["error"]["code"] == -32602
        ws.send_json({"jsonrpc": "2.0", "id": 3, "method": "project.git_log", "params": {"id": project_id}})
        assert ws.receive_json()["result"]["available"] is True

        # Compare parity, including the unknown-name error.
        rest = client.get(f"/projects/{project_id}/baselines/compare?from=current&to=current").json()
        ws.send_json(
            {"jsonrpc": "2.0", "id": 4, "method": "baseline.compare",
             "params": {"project_id": project_id, "from": "current", "to": "current"}}
        )
        assert ws.receive_json()["result"] == rest
        assert rest["modules"] == [] and not any(rest["summary"].values())
        assert client.get(f"/projects/{project_id}/baselines/compare?from=nope&to=current").status_code == 404
        ws.send_json(
            {"jsonrpc": "2.0", "id": 5, "method": "baseline.compare",
             "params": {"project_id": project_id, "from": "nope", "to": "current"}}
        )
        assert ws.receive_json()["error"]["code"] == -32001

    assert client.get(f"/projects/{project_id}/git/log?limit=0").status_code == 422
    assert client.get(f"/projects/{project_id}/git/log?limit=1000").status_code == 200
    assert [b["name"] for b in client.get(f"/projects/{project_id}/baselines").json()["baselines"]] == ["v1"]
