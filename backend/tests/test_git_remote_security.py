"""Remote URL safety and auto-push opt-in rules for git remotes.

User-supplied remote URLs must never execute commands (``ext::``/``fd::``
remote helpers) and must use a network transport; local paths and
``file://`` URLs are a test-only opt-in (``REQMAN_GIT_ALLOW_FILE_REMOTES``,
set by ``conftest.py``). The validator is unit-tested only: no network
remote is ever contacted. Remotes that are used are local bare repositories.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from reqman import git_repo
from reqman.git_repo import GitHistory, GitSettings, _clean_env
from reqman.service import ProjectService
from reqman.store import ProjectStore

from test_git_remotes import Clock, git

ALLOW = "REQMAN_GIT_ALLOW_FILE_REMOTES"


@pytest.fixture
def root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=data, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    return data


@pytest.fixture
def bare(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True)
    return remote


@pytest.fixture
def service(root):
    return ProjectService(ProjectStore(root), history=GitHistory(GitSettings(enabled=True), clock=Clock()))


def _project(service: ProjectService):
    project = service.create_project("Alpha")
    module = service.create_module(project.id, "System", "SYRS")
    req = service.add_requirement(project.id, module.id, {"object_text": "Hello"})
    return project, module, req


def _ext_url(sentinel: Path) -> str:
    # ``%20`` would be the ext:: escape for a space; plain spaces split arguments.
    return f"ext::sh -c touch% {sentinel}"


# --- the validator (unit; never contacts a remote) ---------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo.git",
        "ssh://git@gitlab.example.com:2222/org/repo.git",
        "git://example.com/repo.git",
        "git+ssh://git@example.com/repo.git",
        "git@github.com:org/repo.git",
        "gitlab-alias:org/repo.git",
    ],
)
def test_check_remote_url_accepts_network_urls(url, monkeypatch):
    monkeypatch.delenv(ALLOW, raising=False)
    assert GitHistory._check_remote_url(f"  {url}  ") == url


@pytest.mark.parametrize(
    "url, message",
    [
        ("ext::sh -c 'touch /tmp/pwned'", "remote helpers"),
        ("fd::7", "remote helpers"),
        ("EXT::sh", "remote helpers"),
        ("custom+helper.x::anything", "remote helpers"),
        ("-uhack", "must not start with '-'"),
        ("http://example.com/repo.git", "use an ssh://"),
        ("ftp://example.com/repo.git", "use an ssh://"),
        ("file:///srv/repo.git", "use an ssh://"),
        ("/srv/repo.git", "local paths"),
        ("../repo.git", "local paths"),
        ("repo.git", "local paths"),
        ("C:\\repos\\repo.git", "local paths"),
        ("", "must not be empty"),
    ],
)
def test_check_remote_url_rejects_unsafe_urls(url, message, monkeypatch):
    monkeypatch.delenv(ALLOW, raising=False)
    with pytest.raises(ValueError, match=message):
        GitHistory._check_remote_url(url)


def test_check_remote_url_file_opt_in(monkeypatch):
    monkeypatch.setenv(ALLOW, "1")
    assert GitHistory._check_remote_url("file:///srv/repo.git") == "file:///srv/repo.git"
    assert GitHistory._check_remote_url("/srv/repo.git") == "/srv/repo.git"
    for url in ("ext::sh -c id", "fd::7", "-uhack", "http://example.com/repo.git"):
        with pytest.raises(ValueError):
            GitHistory._check_remote_url(url)


def test_git_allow_protocol_is_our_allowlist(monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "ext:fd:file")  # inherited: never trusted
    monkeypatch.delenv(ALLOW, raising=False)
    assert _clean_env()["GIT_ALLOW_PROTOCOL"] == "ssh:git:https"
    monkeypatch.setenv(ALLOW, "1")
    assert _clean_env()["GIT_ALLOW_PROTOCOL"] == "ssh:git:https:file"


# --- service ------------------------------------------------------------------------


def test_set_remote_rejects_helpers_before_running_git(service, tmp_path):
    project, _module, _req = _project(service)
    sentinel = tmp_path / "pwned"
    for url in (_ext_url(sentinel), "fd::7"):
        with pytest.raises(ValueError, match="remote helpers are not allowed"):
            service.set_git_remote(project.id, url)
    assert not sentinel.exists()
    assert service.git_status(project.id)["remote"]["url"] is None


def test_local_remotes_need_the_opt_in(service, bare, monkeypatch):
    project, _module, _req = _project(service)
    monkeypatch.delenv(ALLOW, raising=False)
    for url in (str(bare), bare.as_uri()):
        with pytest.raises(ValueError, match="Invalid remote URL"):
            service.set_git_remote(project.id, url)
    assert service.git_status(project.id)["remote"]["url"] is None
    monkeypatch.setenv(ALLOW, "1")
    status = service.set_git_remote(project.id, bare.as_uri())
    assert status["remote"]["url"] == bare.as_uri()
    assert service.git_push(project.id)["pushed"]


def test_git_refuses_file_transport_without_opt_in(service, root, bare, monkeypatch):
    """A local remote configured outside the app is refused by git itself."""
    project, _module, _req = _project(service)
    git(root / project.id, "remote", "add", "origin", str(bare))
    monkeypatch.delenv(ALLOW, raising=False)
    with pytest.raises(Exception, match="transport 'file' not allowed"):
        service.git_push(project.id)


# --- REST / WebSocket ---------------------------------------------------------------


@pytest.fixture
def client(root, monkeypatch):
    monkeypatch.setenv("REQMAN_GIT", "on")
    monkeypatch.setenv("REQMAN_DATA_DIR", str(root))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(root.parent / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_api_and_ws_reject_command_transports(client, root, bare, tmp_path, monkeypatch):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    sentinel = tmp_path / "pwned"
    bad_urls = (_ext_url(sentinel), "fd::7")
    for url in bad_urls:
        resp = client.put(f"/projects/{project_id}/git/remote", json={"url": url})
        assert resp.status_code == 400 and "remote helpers" in resp.json()["detail"]
        resp = client.post("/projects/from-remote", json={"url": url})
        assert resp.status_code == 400 and "remote helpers" in resp.json()["detail"]
    with client.websocket_connect("/ws") as ws:
        for index, url in enumerate(bad_urls):
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2 * index,
                    "method": "project.git_set_remote",
                    "params": {"id": project_id, "url": url},
                }
            )
            reply = ws.receive_json()
            assert reply["error"]["code"] == -32602 and "remote helpers" in reply["error"]["message"]
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2 * index + 1,
                    "method": "project.create_from_remote",
                    "params": {"url": url},
                }
            )
            reply = ws.receive_json()
            assert reply["error"]["code"] == -32602 and "remote helpers" in reply["error"]["message"]
    assert not sentinel.exists()
    assert client.get(f"/projects/{project_id}/git").json()["remote"]["url"] is None
    assert [p["id"] for p in client.get("/projects").json()] == [project_id]
    # Local paths are refused without the opt-in, over REST and WebSocket alike.
    monkeypatch.delenv(ALLOW, raising=False)
    assert client.put(f"/projects/{project_id}/git/remote", json={"url": str(bare)}).status_code == 400
    assert client.post("/projects/from-remote", json={"url": bare.as_uri()}).status_code == 400
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"jsonrpc": "2.0", "id": 9, "method": "project.create_from_remote", "params": {"url": str(bare)}}
        )
        assert ws.receive_json()["error"]["code"] == -32602


# --- auto-push opt-in and timeout ---------------------------------------------------


def test_new_remote_does_not_inherit_auto_push(service, root, bare, tmp_path):
    project, _module, _req = _project(service)
    second = tmp_path / "second.git"
    subprocess.run(["git", "init", "--bare", "-q", str(second)], check=True, capture_output=True)
    service.set_git_remote(project.id, str(bare))
    assert service.set_git_auto_push(project.id, True)["remote"]["auto_push"] is True
    service.set_git_remote(project.id, "")
    assert service.set_git_remote(project.id, str(second))["remote"]["auto_push"] is False
    assert git(root / project.id, "config", "--get", "reqman.autopush", check=False) == ""
    # A stale opt-in left behind after a removal outside the app is dropped too.
    git(root / project.id, "remote", "remove", "origin")
    git(root / project.id, "config", "reqman.autopush", "true")
    assert service.set_git_remote(project.id, str(bare))["remote"]["auto_push"] is False


def test_auto_push_uses_short_timeout_and_records_timeouts(service, bare, monkeypatch):
    project, module, req = _project(service)
    service.set_git_remote(project.id, str(bare))
    service.set_git_auto_push(project.id, True)
    real_run = subprocess.run
    push_timeouts: list[float] = []

    def fake_run(command, *args, **kwargs):
        if "push" in command:
            push_timeouts.append(kwargs["timeout"])
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(git_repo.subprocess, "run", fake_run)
    service.update_requirement(project.id, module.id, req.id, {"object_text": "changed"})
    assert push_timeouts == [git_repo._AUTO_PUSH_TIMEOUT_SECONDS] and push_timeouts[0] <= 10
    error = service.git_status(project.id)["last_error"]
    assert error is not None and "Auto-push failed" in error and "timed out" in error
