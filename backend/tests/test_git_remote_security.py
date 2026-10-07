"""Remote URL safety and auto-push opt-in rules for git remotes.

User-supplied remote URLs must never execute commands (``ext::``/``fd::``
remote helpers) and must use a supported transport: ssh, git, https, or a
local folder (an absolute path or a ``file://`` URL with an absolute path,
e.g. a network share) — in normal operation, without any opt-in. The
validator is unit-tested only for network URLs: no network remote is ever
contacted. Remotes that are used are local bare repositories in tmp_path.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from reqman import git_repo
from reqman.git_repo import GitError, GitHistory, GitSettings, _clean_env, _transport_error

from reqman.service import ProjectService
from reqman.store import ProjectStore

from test_git_remotes import Clock, git

# Rejected by the validator, with the expected message part.
UNSAFE_URLS = [
    ("ext::sh -c 'touch /tmp/pwned'", "remote helpers"),
    ("fd::7", "remote helpers"),
    ("EXT::sh", "remote helpers"),
    ("custom+helper.x::anything", "remote helpers"),
    ("1abc::x", "remote helpers"),
    ("-uhack", "must not start with '-'"),
    ("-x", "must not start with '-'"),
    ("http://example.com/repo.git", "unsupported scheme 'http'"),
    ("ftp://example.com/repo.git", "unsupported scheme 'ftp'"),
    ("1foo://x", "unsupported scheme '1foo'"),
    ("file://srv/repo.git", "absolute path"),
    # git matches schemes case-sensitively (it would look for a remote helper)
    ("FILE:///srv/repo.git", "scheme 'FILE' must be lower-case"),
    ("HTTPS://example.com/repo.git", "scheme 'HTTPS' must be lower-case"),
    ("SSH://git@example.com/repo.git", "scheme 'SSH' must be lower-case"),
    ("Git://example.com/repo.git", "scheme 'Git' must be lower-case"),
    ("file://", "absolute path"),
    ("repo.git", "relative local paths"),
    ("./repo.git", "relative local paths"),
    ("../x", "relative local paths"),
    ("", "must not be empty"),
]


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
        "/srv/repo.git",
        "/Volumes/NAS Share/team/repo.git",
        "file:///srv/repo.git",
    ],
)
def test_check_remote_url_accepts_supported_urls(url):
    assert GitHistory._check_remote_url(f"  {url}  ") == url


@pytest.mark.parametrize("url, message", UNSAFE_URLS)
def test_check_remote_url_rejects_unsafe_urls(url, message):
    with pytest.raises(ValueError, match=message):
        GitHistory._check_remote_url(url)


@pytest.mark.skipif(os.name == "nt", reason="a drive path is absolute on Windows")
def test_windows_drive_path_is_no_absolute_path_on_posix():
    with pytest.raises(ValueError, match="relative local paths"):
        GitHistory._check_remote_url("C:\\repos\\repo.git")


def test_opt_in_variable_is_gone():
    assert not hasattr(git_repo, "ALLOW_FILE_REMOTES_ENV")
    assert not hasattr(git_repo, "_file_remotes_allowed")


def test_git_allow_protocol_is_our_allowlist(monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "ext:fd:file")  # inherited: never trusted
    assert _clean_env()["GIT_ALLOW_PROTOCOL"] == "ssh:git:https"
    remote = _clean_env(git_repo._REMOTE_ENV)["GIT_ALLOW_PROTOCOL"]
    assert remote == "ssh:git:https:file"
    assert {"ext", "fd"}.isdisjoint(remote.split(":"))


def test_transport_error_message():
    assert _transport_error("fatal: transport 'http' not allowed") == (
        "Remote transport 'http' is not supported; use an ssh, git, https or local-folder remote"
    )
    assert _transport_error("fatal: repository not found") is None


def test_only_remote_operations_get_the_file_transport(service, bare, monkeypatch):
    project, module, req = _project(service)
    real_run = subprocess.run
    seen: dict[str, set[str]] = {}

    def spy(command, *args, **kwargs):
        sub = git_repo._subcommand(list(command[1:]))
        seen.setdefault(sub, set()).add(kwargs["env"]["GIT_ALLOW_PROTOCOL"])
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(git_repo.subprocess, "run", spy)
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    service.update_requirement(project.id, module.id, req.id, {"object_text": "changed"})
    service.git_pull(project.id)
    for sub in ("ls-remote", "push", "fetch"):
        assert seen.pop(sub) == {"ssh:git:https:file"}, sub
    assert seen and all(values == {"ssh:git:https"} for values in seen.values()), seen


# --- service: local-folder remotes work without any opt-in --------------------------


@pytest.mark.parametrize("as_uri", [False, True], ids=["path", "file-url"])
def test_local_folder_remote_set_push_clone_pull(service, root, bare, tmp_path, as_uri):
    assert "REQMAN_GIT_ALLOW_FILE_REMOTES" not in os.environ
    url = bare.as_uri() if as_uri else str(bare)
    project, module, _req = _project(service)
    status = service.set_git_remote(project.id, url)
    assert status["remote"]["url"] == url
    assert service.git_push(project.id)["pushed"]

    other_root = tmp_path / "data2"
    other_root.mkdir()
    other = ProjectService(ProjectStore(other_root), history=GitHistory(GitSettings(enabled=True), clock=Clock()))
    cloned = other.add_project_from_remote(url)
    assert cloned.id == project.id
    assert other.git_status(project.id)["remote"]["url"] == url

    new_req = service.add_requirement(project.id, module.id, {"object_text": "Later"})
    service.git_push(project.id)
    assert other.git_pull(project.id)["updated"] is True
    assert other.get_requirement(project.id, module.id, new_req.id).attributes["object_text"] == "Later"


_SERVER_HOOKS = ("pre-receive", "update", "post-receive", "post-update")


def _arm_server_hooks(bare_repo: Path, sentinels: Path) -> None:
    """Executable server-side hooks in ``bare_repo`` that touch ``sentinels/<hook>`` when run."""
    sentinels.mkdir(exist_ok=True)
    hooks = bare_repo / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in _SERVER_HOOKS:
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch '{sentinels / name}'\n")
        hook.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="sh hook scripts")
@pytest.mark.parametrize("as_uri", [False, True], ids=["path", "file-url"])
def test_push_to_local_remote_runs_no_server_hooks(service, root, bare, tmp_path, as_uri):
    """R1: git strips ``-c`` before spawning receive-pack in a local target repo."""
    sentinels = tmp_path / "sentinels"
    _arm_server_hooks(bare, sentinels)
    # The fixture itself: an ordinary push from outside the app fires the hooks.
    probe = tmp_path / "probe"
    git(tmp_path, "init", "-q", "-b", "probe", str(probe))
    git(probe, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    git(probe, "push", "-q", str(bare), "probe")
    assert sorted(p.name for p in sentinels.iterdir()) == sorted(_SERVER_HOOKS)
    for sentinel in sentinels.iterdir():
        sentinel.unlink()

    url = bare.as_uri() if as_uri else str(bare)
    project, module, req = _project(service)
    service.set_git_remote(project.id, url)
    assert service.git_push(project.id)["pushed"]
    head = git(root / project.id, "rev-parse", "HEAD").strip()
    assert git(bare, "rev-parse", "refs/heads/current").strip() == head
    assert list(sentinels.iterdir()) == []

    service.set_git_auto_push(project.id, True)
    service.update_requirement(project.id, module.id, req.id, {"object_text": "auto-pushed"})
    assert service.git_status(project.id)["last_error"] is None
    head = git(root / project.id, "rev-parse", "HEAD").strip()
    assert git(bare, "rev-parse", "refs/heads/current").strip() == head
    assert list(sentinels.iterdir()) == []


def _arm_alternate_refs(tmp_path: Path, name: str, sentinels: Path) -> Path:
    """R6 (a): a bare target whose alternate's refs are listed by ``core.alternateRefsCommand``."""
    target = tmp_path / f"{name}.git"
    git(tmp_path, "init", "--bare", "-q", str(target))
    other = tmp_path / f"{name}-other.git"
    git(tmp_path, "init", "--bare", "-q", str(other))
    (target / "objects" / "info" / "alternates").write_text(f"{other / 'objects'}\n")
    # Run by the shell with the alternate's path appended: ``#`` drops it.
    git(target, "config", "core.alternateRefsCommand", f"touch '{sentinels / 'alternate-refs'}' #")
    return target


def _arm_update_instead(tmp_path: Path, name: str, sentinels: Path) -> Path:
    """R6 (b): a non-bare target that checks out pushes to ``current``, with a filter and an fsmonitor."""
    target = tmp_path / name
    git(tmp_path, "init", "-q", "-b", "current", str(target))
    git(target, "config", "receive.denyCurrentBranch", "updateInstead")
    fsmonitor = tmp_path / f"{name}-fsmonitor.sh"
    fsmonitor.write_text(f"#!/bin/sh\ntouch '{sentinels / 'fsmonitor'}'\n")
    fsmonitor.chmod(0o755)
    git(target, "config", "core.fsmonitor", str(fsmonitor))
    git(target, "config", "filter.evil.smudge", f"touch '{sentinels / 'smudge'}'; cat")
    (target / ".git" / "info" / "attributes").write_text("* filter=evil\n")
    return target


_TARGET_CONFIG_VECTORS = {"alternate-refs": _arm_alternate_refs, "update-instead": _arm_update_instead}


@pytest.mark.skipif(os.name == "nt", reason="sh scripts")
@pytest.mark.parametrize("vector", sorted(_TARGET_CONFIG_VECTORS))
def test_push_to_local_remote_runs_no_target_config_commands(service, root, tmp_path, vector):
    """R6: commands named in a local target's config never run on push or auto-push."""
    arm = _TARGET_CONFIG_VECTORS[vector]
    sentinels = tmp_path / "sentinels"
    sentinels.mkdir()
    # The fixture itself: an ordinary push from outside the app runs the commands.
    probe_target = arm(tmp_path, "probe-target", sentinels)
    probe = tmp_path / "probe"
    git(tmp_path, "init", "-q", "-b", "current", str(probe))
    (probe / "file.txt").write_text("x\n")
    git(probe, "add", "file.txt")
    git(probe, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    git(probe, "push", "-q", str(probe_target), "current", check=False)
    fired = sorted(p.name for p in sentinels.iterdir())
    if not fired:
        pytest.skip(f"this git version runs no command of the {vector} fixture")
    for sentinel in sentinels.iterdir():
        sentinel.unlink()

    target = arm(tmp_path, "target", sentinels)
    project, module, req = _project(service)
    service.set_git_remote(project.id, str(target))
    head = git(root / project.id, "rev-parse", "HEAD").strip()
    if vector == "update-instead":
        # Pushing the checked-out branch is refused, without any checkout.
        with pytest.raises(ValueError, match="checked out"):
            service.git_push(project.id)
        assert git(target, "rev-parse", "--verify", "-q", "refs/heads/current", check=False) == ""
        assert sorted(p.name for p in target.iterdir()) == [".git"]
    else:
        assert service.git_push(project.id)["pushed"]
        assert git(target, "rev-parse", "refs/heads/current").strip() == head
    assert list(sentinels.iterdir()) == []

    service.set_git_auto_push(project.id, True)
    service.update_requirement(project.id, module.id, req.id, {"object_text": "auto-pushed"})
    error = service.git_status(project.id)["last_error"]
    if vector == "update-instead":
        assert error is not None and "Auto-push failed" in error and "checked out" in error
    else:
        assert error is None
        head = git(root / project.id, "rev-parse", "HEAD").strip()
        assert git(target, "rev-parse", "refs/heads/current").strip() == head
    assert list(sentinels.iterdir()) == []


def test_push_refuses_mixed_local_and_network_push_urls(client, root, bare, monkeypatch):
    """R7: one local and one network push URL are refused before anything is pushed."""
    project_id = client.post("/projects", json={"name": "Alpha"}).json()["id"]
    assert client.put(f"/projects/{project_id}/git/remote", json={"url": str(bare)}).status_code == 200
    # The first pushurl replaces the url for pushes: add the local folder too.
    for push_url in (str(bare), "https://example.invalid/x.git"):
        git(root / project_id, "remote", "set-url", "--add", "--push", "origin", push_url)
    assert git(root / project_id, "remote", "get-url", "--push", "--all", "origin").split() == [
        str(bare),
        "https://example.invalid/x.git",
    ]
    real_run = subprocess.run
    pushes: list[list[str]] = []

    def spy(command, *args, **kwargs):
        if git_repo._subcommand(list(command[1:])) in ("push", "ls-remote", "fetch"):
            pushes.append(list(command))  # never run: no network contact even on failure
            return subprocess.CompletedProcess(command, 1, b"", b"fatal: test spy refused")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(git_repo.subprocess, "run", spy)
    resp = client.post(f"/projects/{project_id}/git/push")
    assert resp.status_code == 400
    assert "mixes local-folder and network" in resp.json()["detail"]
    assert pushes == []
    assert git(bare, "for-each-ref") == ""


@pytest.mark.parametrize("url", ["C:\\repos\\repo.git", "C:/repos/repo.git", "c:repo.git"])
def test_is_local_remote_windows_drive(url, monkeypatch):
    """R8: on Windows a drive letter is never the host of an scp-like ssh URL."""
    monkeypatch.setattr(git_repo, "_WINDOWS", True)
    assert git_repo._is_local_remote(url) is True
    assert git_repo._is_local_remote("git@example.com:org/repo.git") is False
    if os.name != "nt":
        monkeypatch.setattr(git_repo, "_WINDOWS", False)
        assert git_repo._is_local_remote(url) is False  # ``host:path`` on POSIX, like git


@pytest.mark.parametrize(
    "url, local",
    [
        ("/srv/repo.git", True),
        ("/srv/a::b.git", True),
        ("file:///srv/repo.git", True),
        ("../relative/repo.git", True),  # configured outside the app: still the local transport
        ("https://example.com/repo.git", False),
        ("ssh://git@example.com/repo.git", False),
        ("git://example.com/repo.git", False),
        ("git@example.com:org/repo.git", False),
        ("c:org/repo.git" if os.name != "nt" else "host:org/repo.git", False),
        ("ext::sh", False),
    ],
)
def test_is_local_remote(url, local):
    assert git_repo._is_local_remote(url) is local


def test_set_remote_rejects_unsafe_urls_before_running_git(service, tmp_path, monkeypatch):
    project, _module, _req = _project(service)
    sentinel = tmp_path / "pwned"
    real_run = subprocess.run
    remote_calls: list[list[str]] = []

    def spy(command, *args, **kwargs):
        if "ls-remote" in command or "clone" in command:
            remote_calls.append(list(command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(git_repo.subprocess, "run", spy)
    for url, message in [(_ext_url(sentinel), "remote helpers"), *UNSAFE_URLS]:
        with pytest.raises(ValueError, match=message):
            service.set_git_remote(project.id, url) if url else GitHistory._check_remote_url(url)
        with pytest.raises(ValueError, match=message):
            service.add_project_from_remote(url)
    assert remote_calls == []
    assert not sentinel.exists()
    assert service.git_status(project.id)["remote"]["url"] is None
    assert [p.id for p in service.list_projects()] == [project.id]


@pytest.mark.parametrize("bad_url", ["http://127.0.0.1:9/repo.git", "fd::7"])
def test_unsupported_transport_configured_outside_the_app(service, root, bad_url):
    """A remote set by hand in .git/config is refused by git itself, with a clear message."""
    project, module, req = _project(service)
    git(root / project.id, "remote", "add", "origin", bad_url)
    transport = bad_url.split(":", 1)[0]
    expected = f"Remote transport '{transport}' is not supported; use an ssh, git, https or local-folder remote"
    with pytest.raises(ValueError) as push_error:
        service.git_push(project.id)
    assert str(push_error.value) == expected
    with pytest.raises(ValueError) as pull_error:
        service.git_pull(project.id)
    assert str(pull_error.value) == expected
    git(root / project.id, "config", "reqman.autopush", "true")
    service.update_requirement(project.id, module.id, req.id, {"object_text": "changed"})
    error = service.git_status(project.id)["last_error"]
    assert error is not None and "Auto-push failed" in error and expected in error


def test_remote_run_maps_only_transport_errors():
    history = GitHistory(GitSettings(enabled=True))

    def failing(message):
        def run(*args, **kwargs):
            assert kwargs["env"] == git_repo._REMOTE_ENV
            raise GitError(message)

        return run

    with pytest.raises(GitError, match="^Remote transport 'ext' is not supported"):
        history._remote_run(failing("git fetch failed: fatal: transport 'ext' not allowed"))
    with pytest.raises(GitError, match="^git fetch failed: fatal: no such remote$"):
        history._remote_run(failing("git fetch failed: fatal: no such remote"))


# --- REST / WebSocket ---------------------------------------------------------------


@pytest.fixture
def client(root, monkeypatch):
    monkeypatch.setenv("REQMAN_GIT", "on")
    monkeypatch.setenv("REQMAN_DATA_DIR", str(root))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(root.parent / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_api_and_ws_reject_unsafe_urls(client, root, tmp_path):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    sentinel = tmp_path / "pwned"
    bad = [(_ext_url(sentinel), "remote helpers"), *((u, m) for u, m in UNSAFE_URLS if u)]
    for url, message in bad:
        resp = client.put(f"/projects/{project_id}/git/remote", json={"url": url})
        assert resp.status_code == 400 and message in resp.json()["detail"], (url, resp.json())
        resp = client.post("/projects/from-remote", json={"url": url})
        assert resp.status_code == 400 and message in resp.json()["detail"], (url, resp.json())
    with client.websocket_connect("/ws") as ws:
        for index, (url, message) in enumerate(bad):
            for offset, (method, params) in enumerate(
                (
                    ("project.git_set_remote", {"id": project_id, "url": url}),
                    ("project.create_from_remote", {"url": url}),
                )
            ):
                ws.send_json({"jsonrpc": "2.0", "id": 2 * index + offset, "method": method, "params": params})
                reply = ws.receive_json()
                assert reply["error"]["code"] == -32602 and message in reply["error"]["message"], (url, reply)
    assert not sentinel.exists()
    assert client.get(f"/projects/{project_id}/git").json()["remote"]["url"] is None
    assert [p["id"] for p in client.get("/projects").json()] == [project_id]


def test_api_and_ws_accept_local_folder_remotes(client, root, bare, tmp_path):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    resp = client.put(f"/projects/{project_id}/git/remote", json={"url": bare.as_uri()})
    assert resp.status_code == 200 and resp.json()["remote"]["url"] == bare.as_uri()
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "project.git_set_remote",
                "params": {"id": project_id, "url": str(bare)},
            }
        )
        reply = ws.receive_json()
        while "id" not in reply:  # skip live-sync broadcasts
            reply = ws.receive_json()
        assert "error" not in reply, reply
    assert client.get(f"/projects/{project_id}/git").json()["remote"]["url"] == str(bare)


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


# --- transport allowlist: clone, the set-remote probe and pull -----------------------

FILE_REFUSED = "Remote transport 'file' is not supported; use an ssh, git, https or local-folder remote"


def _second_service(tmp_path: Path) -> ProjectService:
    other_root = tmp_path / "data2"
    other_root.mkdir()
    return ProjectService(ProjectStore(other_root), history=GitHistory(GitSettings(enabled=True), clock=Clock()))


@pytest.mark.parametrize("as_uri", [False, True], ids=["path", "file-url"])
def test_clone_gets_the_file_transport_and_local_commands_do_not(service, bare, tmp_path, monkeypatch, as_uri):
    project, module, req = _project(service)
    url = bare.as_uri() if as_uri else str(bare)
    service.set_git_remote(project.id, url)
    service.git_push(project.id)
    other = _second_service(tmp_path)
    real_run = subprocess.run
    seen: dict[str, set[str]] = {}

    def spy(command, *args, **kwargs):
        sub = git_repo._subcommand(list(command[1:]))
        seen.setdefault(sub, set()).add(kwargs["env"]["GIT_ALLOW_PROTOCOL"])
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(git_repo.subprocess, "run", spy)
    other.add_project_from_remote(url)
    other.update_requirement(project.id, module.id, req.id, {"object_text": "changed in clone"})
    other.git_status(project.id)
    assert seen.pop("clone") == {"ssh:git:https:file"}
    assert "commit" in seen and "status" in seen, seen
    assert {"ls-remote", "push", "fetch"}.isdisjoint(seen), seen
    assert all(values == {"ssh:git:https"} for values in seen.values()), seen


def _refuse_file_transport(monkeypatch):
    """The validator still accepts local folders, but git refuses the ``file`` transport."""
    monkeypatch.setattr(git_repo, "_REMOTE_ENV", {"GIT_ALLOW_PROTOCOL": "ssh:git:https"})


@pytest.mark.parametrize("as_uri", [False, True], ids=["path", "file-url"])
def test_clone_maps_a_refused_transport(service, bare, tmp_path, monkeypatch, as_uri):
    project, _module, _req = _project(service)
    url = bare.as_uri() if as_uri else str(bare)
    service.set_git_remote(project.id, url)
    service.git_push(project.id)
    other = _second_service(tmp_path)
    _refuse_file_transport(monkeypatch)
    with pytest.raises(ValueError) as error:
        other.add_project_from_remote(url)
    assert str(error.value) == FILE_REFUSED
    assert other.list_projects() == []
    assert sorted(p.name for p in (tmp_path / "data2").iterdir() if not p.name.startswith(".")) == []


@pytest.mark.parametrize("as_uri", [False, True], ids=["path", "file-url"])
def test_set_remote_probe_maps_a_refused_transport(service, bare, monkeypatch, as_uri):
    project, _module, _req = _project(service)
    url = bare.as_uri() if as_uri else str(bare)
    _refuse_file_transport(monkeypatch)
    with pytest.raises(ValueError) as error:
        service.set_git_remote(project.id, url)
    assert str(error.value) == FILE_REFUSED
    assert service.git_status(project.id)["remote"]["url"] is None


def test_pull_maps_a_refused_fetch(service, bare, tmp_path, monkeypatch):
    """ls-remote succeeds, then git refuses the transport on fetch: the pull reports the mapped message."""
    project, module, _req = _project(service)
    service.set_git_remote(project.id, str(bare))
    service.git_push(project.id)
    other = _second_service(tmp_path)
    other.add_project_from_remote(str(bare))
    service.add_requirement(project.id, module.id, {"object_text": "Later"})
    service.git_push(project.id)
    head_before = other.git_status(project.id)["head"]
    real_run = subprocess.run
    fetches: list[list[str]] = []

    def spy(command, *args, **kwargs):
        if git_repo._subcommand(list(command[1:])) == "fetch":
            fetches.append(list(command))
            kwargs["env"] = {**kwargs["env"], "GIT_ALLOW_PROTOCOL": "ssh:git:https"}
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(git_repo.subprocess, "run", spy)
    with pytest.raises(ValueError) as error:
        other.git_pull(project.id)
    assert str(error.value) == FILE_REFUSED
    assert fetches, "the pull never reached fetch"
    assert other.git_status(project.id)["head"] == head_before


def test_api_and_ws_map_a_refused_transport(client, root, bare, tmp_path, monkeypatch):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    resp = client.put(f"/projects/{project_id}/git/remote", json={"url": str(bare)})
    assert resp.status_code == 200
    assert client.post(f"/projects/{project_id}/git/push").status_code == 200
    resp = client.put(f"/projects/{project_id}/git/remote", json={"url": None})
    assert resp.status_code == 200 and resp.json()["remote"]["url"] is None
    _refuse_file_transport(monkeypatch)
    for url in (str(bare), bare.as_uri()):
        resp = client.put(f"/projects/{project_id}/git/remote", json={"url": url})
        assert resp.status_code == 400 and resp.json()["detail"] == FILE_REFUSED, resp.json()
        resp = client.post("/projects/from-remote", json={"url": url})
        assert resp.status_code == 400 and resp.json()["detail"] == FILE_REFUSED, resp.json()
    with client.websocket_connect("/ws") as ws:
        for index, (method, params) in enumerate(
            (
                ("project.git_set_remote", {"id": project_id, "url": str(bare)}),
                ("project.create_from_remote", {"url": bare.as_uri()}),
            )
        ):
            ws.send_json({"jsonrpc": "2.0", "id": index, "method": method, "params": params})
            reply = ws.receive_json()
            assert "method" not in reply, reply  # no live-sync broadcast for a failed mutation
            assert reply["id"] == index and reply["error"]["code"] == -32602, reply
            assert reply["error"]["message"] == FILE_REFUSED, reply
    assert client.get(f"/projects/{project_id}/git").json()["remote"]["url"] is None
    assert [p["id"] for p in client.get("/projects").json()] == [project_id]


# --- local remotes inside the data directory (R3) -----------------------------------


def _inside_data_dir_urls(root: Path, project_b: str) -> list[str]:
    b_dir = root / project_b
    return [
        str(b_dir),
        b_dir.as_uri(),
        str(root),
        root.as_uri(),
        str(b_dir / "modules"),
        f"{root}/../{root.name}/{project_b}",  # not normalised by the caller
    ]


def test_service_refuses_local_remote_inside_data_dir(service, root, bare, tmp_path):
    project_a, _module, _req = _project(service)
    project_b = service.create_project("Beta")
    link = tmp_path / "link-to-b"
    link.symlink_to(root / project_b.id)
    for url in [*_inside_data_dir_urls(root, project_b.id), str(link), link.as_uri()]:
        with pytest.raises(ValueError, match="inside the data directory"):
            service.set_git_remote(project_a.id, url)
        with pytest.raises(ValueError, match="inside the data directory"):
            service.add_project_from_remote(url)
    assert service.git_status(project_a.id)["remote"]["url"] is None
    assert sorted(p.id for p in service.list_projects()) == sorted([project_a.id, project_b.id])
    # Outside the data directory: still accepted.
    assert service.set_git_remote(project_a.id, str(bare))["remote"]["url"] == str(bare)


def test_api_and_ws_refuse_local_remote_inside_data_dir(client, root, bare):
    project_a = client.post("/projects", json={"name": "Alpha"}).json()["id"]
    project_b = client.post("/projects", json={"name": "Beta"}).json()["id"]
    urls = _inside_data_dir_urls(root, project_b)
    for url in urls:
        resp = client.put(f"/projects/{project_a}/git/remote", json={"url": url})
        assert resp.status_code == 400 and "inside the data directory" in resp.json()["detail"], (url, resp.json())
        resp = client.post("/projects/from-remote", json={"url": url})
        assert resp.status_code == 400 and "inside the data directory" in resp.json()["detail"], (url, resp.json())
    with client.websocket_connect("/ws") as ws:
        for index, url in enumerate(urls):
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": index,
                    "method": "project.git_set_remote",
                    "params": {"id": project_a, "url": url},
                }
            )
            reply = ws.receive_json()
            while "id" not in reply:  # skip live-sync broadcasts
                reply = ws.receive_json()
            assert reply["error"]["code"] == -32602, (url, reply)
            assert "inside the data directory" in reply["error"]["message"], (url, reply)
    assert client.get(f"/projects/{project_a}/git").json()["remote"]["url"] is None
    resp = client.put(f"/projects/{project_a}/git/remote", json={"url": str(bare)})
    assert resp.status_code == 200 and resp.json()["remote"]["url"] == str(bare)


@pytest.mark.parametrize(
    "url, path",
    [
        ("/srv/repo.git", "/srv/repo.git"),
        ("file:///srv/repo.git", "/srv/repo.git"),
        ("file:///Volumes/NAS%20Share/repo.git", "/Volumes/NAS Share/repo.git"),
        ("https://example.com/repo.git", None),
        ("git@example.com:org/repo.git", None),
    ],
)
def test_local_remote_path(url, path):
    result = git_repo.local_remote_path(url)
    assert (str(result) if result is not None else None) == path


@pytest.mark.parametrize("as_uri", [False, True], ids=["path", "file-url"])
def test_absolute_path_with_double_colon_is_a_local_remote(service, tmp_path, as_uri):
    """R5: ``::`` inside an absolute path is no ``<transport>::`` remote helper."""
    remote = tmp_path / "a::b.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True)
    url = remote.as_uri() if as_uri else str(remote)
    assert GitHistory._check_remote_url(url) == url
    project, _module, _req = _project(service)
    assert service.set_git_remote(project.id, url)["remote"]["url"] == url
    assert service.git_push(project.id)["pushed"]
    assert git(remote, "rev-parse", "refs/heads/current").strip() == git(
        service.store.project_path(project.id), "rev-parse", "HEAD"
    ).strip()

    other_root = tmp_path / "data2"
    other_root.mkdir()
    other = ProjectService(ProjectStore(other_root), history=GitHistory(GitSettings(enabled=True), clock=Clock()))
    assert other.add_project_from_remote(url).id == project.id
    assert other.git_status(project.id)["remote"]["url"] == url
