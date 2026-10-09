"""Publish / push with the login account, and the "allow editors to push" project setting.

Every provider API call is answered by ``FakeHttp`` (GitLab is never
contacted); the remote is a local bare repository in ``tmp_path``.
Thorough coverage belongs to the dedicated test slices.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from reqman import extensions
from reqman.extensions import (
    ACTION_CONNECTIONS_USE,
    ACTION_PROJECT_ADMIN,
    AccessDeniedError,
    RequestIdentity,
    identity_scope,
)
from reqman.git_repo import GitHistory, GitSettings
from reqman.providers import (
    LOGIN_CONNECTION_ID,
    ConnectionStore,
    HttpResponse,
    MemoryTokenStore,
    ProviderError,
    ProviderService,
)
from reqman.service import ProjectService
from reqman.store import ProjectStore

CHECKOUT = Path(__file__).resolve().parents[2]
TOKEN = "glpat-login-token-never-stored"


def git(path: Path, *args: str, check: bool = True) -> str:
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env, check=False)
    if check:
        assert result.returncode == 0, result.stderr
    return result.stdout


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class FakeHttp:
    """Answers provider API requests from ``routes``: ``(method, path)`` -> data, or a ``ProviderError``."""

    def __init__(self, routes: dict[tuple[str, str], object] | None = None) -> None:
        self.routes = routes or {}
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.bodies: list[tuple[str, str, object]] = []

    def request(self, label, method, url, headers, body=None):  # noqa: ANN001 - ProviderHttp's signature
        self.requests.append((method, url, dict(headers)))
        self.bodies.append((method, urllib.parse.urlsplit(url).path, body))
        answer = self.routes.get((method, urllib.parse.urlsplit(url).path))
        if answer is None:
            raise ProviderError(f"{label} API error 404: Not Found", status=404)
        if isinstance(answer, ProviderError):
            raise answer
        if callable(answer):
            answer = answer(body)
        return HttpResponse(200, answer, {})

    def count(self, path: str) -> int:
        return sum(1 for _m, url, _h in self.requests if urllib.parse.urlsplit(url).path == path)


@pytest.fixture
def root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=data, capture_output=True, check=False)
    assert probe.returncode != 0, "tmp_path is inside a git repository"
    return data


@pytest.fixture
def bare(tmp_path):
    """A local bare repository standing in for the GitLab remote."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True, capture_output=True)
    return remote


def gitlab_repository(bare: Path, path: str = "acme/brakes") -> dict:
    return {
        "id": 4242,
        "path_with_namespace": path,
        "name": "brakes",
        "visibility": "private",
        "web_url": f"https://gitlab.example.com/{path}",
        "http_url_to_repo": str(bare),
        "last_activity_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def account(root, bare):
    """A signed-in GitLab user (login source on the provider service) with a project service."""
    http = FakeHttp(
        {
            ("GET", "/api/v4/user"): {"username": "alice", "name": "Alice"},
            ("GET", "/api/v4/namespaces"): [
                {"id": 7, "full_path": "alice", "path": "alice", "name": "Alice", "kind": "user"}
            ],
            ("POST", "/api/v4/projects"): gitlab_repository(bare),
            ("GET", "/api/v4/projects"): [],
        }
    )
    providers = ProviderService(ConnectionStore(None), MemoryTokenStore(), http)
    from reqman_server.login_account import LoginAccountSource
    from reqman_server.settings import ProviderSettings, ServerSettings
    from reqman_server.sessions import SessionStore
    from reqman_server.users import UserRegistry

    settings = ServerSettings(
        providers=(
            ProviderSettings(
                id="gitlab", type="gitlab", name="GitLab", issuer="https://gitlab.example.com", client_id="c"
            ),
        ),
        state_dir=root.parent / "server",
    )
    users = UserRegistry(settings.state_dir / "users.json", admins=(), admin_groups=())
    sessions = SessionStore(None, max_age=36_000, idle=36_000, clock=Clock())
    source = LoginAccountSource(settings, users, sessions, clock=Clock())
    http.routes[("GET", "/api/v4/user")] = {"username": "alice", "name": "Alice"}
    providers.login_source = lambda: (source.connection(settings.provider("gitlab")), TOKEN)
    identity = RequestIdentity(user_id="u1", name="Alice", email="a@example.com")
    history = GitHistory(GitSettings(enabled=True), clock=Clock())
    service = ProjectService(ProjectStore(root), history=history, providers=providers)
    return {
        "service": service,
        "providers": providers,
        "http": http,
        "identity": identity,
        "settings": settings,
    }


def _project_with_requirement(service: ProjectService):
    project = service.create_project("Alpha")
    module = service.create_module(project.id, "System", "SYRS")
    req = service.add_requirement(project.id, module.id, {"object_text": "Hello"})
    return project, module, req


# --- publish with the login account ------------------------------------------------------


def test_publish_with_the_login_account_needs_no_connections_use(account, bare, monkeypatch):
    """A project admin publishes with their login (no ``connections.use``, no stored connection)."""
    service, http, identity = account["service"], account["http"], account["identity"]
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _DenyConnectionsUse())
    project, _module, _req = _project_with_requirement(service)
    with identity_scope(identity):
        result = service.publish_project(project.id, None, None, "brakes", True, None)
    assert result["repository"]["full_path"] == "acme/brakes"
    assert result["pushed"] and "refs/heads/current" in result["pushed"]
    assert service.git_status(project.id)["remote"]["url"] == str(bare)
    # The creation went to the provider API with the login OAuth token (Bearer).
    assert ("POST", "https://gitlab.example.com/api/v4/projects") in [
        (m, u) for m, u, _h in http.requests
    ]
    assert any(h.get("Authorization") == f"Bearer {TOKEN}" for m, u, h in http.requests if m == "POST")


class _DenyConnectionsUse:
    """A server edition guard: everything but ``connections.use`` is free."""

    def check(self, identity, action, project_id=None):
        if action == ACTION_CONNECTIONS_USE:
            raise AccessDeniedError(
                "Using the GitLab / GitHub connections needs the server-wide 'connections.use' permission"
            )


def test_publish_with_the_login_connection_id_selects_the_login_account(account, bare, monkeypatch):
    """``connection_id="login"`` behaves like an omitted one."""
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _DenyConnectionsUse())
    service, identity = account["service"], account["identity"]
    project, _module, _req = _project_with_requirement(service)
    with identity_scope(identity):
        result = service.publish_project(project.id, LOGIN_CONNECTION_ID)
    assert result["pushed"] and result["repository"]["full_path"] == "acme/brakes"


def test_namespaces_and_repositories_with_the_login_account(account, monkeypatch):
    """Browsing namespaces / repositories with the login account needs no ``connections.use``."""
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _DenyConnectionsUse())
    service, identity = account["service"], account["identity"]
    with identity_scope(identity):
        namespaces = service.provider_namespaces(LOGIN_CONNECTION_ID)
        repositories = service.provider_repositories(None)
    assert [ns["path"] for ns in namespaces] == ["alice"]
    assert repositories["repositories"] == []  # the fake answers /projects with 404


def test_publish_without_a_login_account_still_needs_a_connection(account, monkeypatch):
    """Local edition: publishing without a connection id is a refusal naming the login account."""
    monkeypatch.setattr(account["providers"], "login_source", None)
    service = account["service"]
    project, _module, _req = _project_with_requirement(service)
    with pytest.raises(ValueError, match="signed-in session"):
        service.publish_project(project.id, None)


# --- allow editors to push ---------------------------------------------------------------


class _EditorGuard:
    """Everyone has ``project.write``; nobody but the admin has ``project.admin``."""

    def check(self, identity, action, project_id=None):
        if action == ACTION_PROJECT_ADMIN:
            raise AccessDeniedError("you are not a project admin")


def test_editor_push_needs_the_project_opt_in(account, bare, monkeypatch):
    """An editor (project.write without project.admin) pushes only when the project allows it."""
    service, identity = account["service"], account["identity"]
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.set_git_allow_editor_push(project.id, True)
    assert service.git_status(project.id)["remote"]["allow_editor_push"] is True
    # The project admin may turn it off again.
    service.set_git_allow_editor_push(project.id, False)
    assert service.git_status(project.id)["remote"]["allow_editor_push"] is False
    service.set_git_allow_editor_push(project.id, True)

    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _EditorGuard())
    with identity_scope(identity):
        pushed = service.git_push(project.id)
    assert "refs/heads/current" in pushed["pushed"]
    # An editor cannot change the setting (project.admin).
    with identity_scope(identity):
        with pytest.raises(AccessDeniedError):
            service.set_git_allow_editor_push(project.id, False)


def test_editor_push_is_denied_without_the_opt_in(account, bare, monkeypatch):
    service, identity = account["service"], account["identity"]
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", _EditorGuard())
    with identity_scope(identity):
        with pytest.raises(AccessDeniedError, match="may not push"):
            service.git_push(project.id)
    # Pull stays open to every editor.
    with identity_scope(identity):
        assert service.git_pull(project.id)["updated"] is False
    # The setting needs a configured remote.
    monkeypatch.setattr(extensions.HOOKS, "permission_guard", extensions.AllowAllGuard())
    service.set_git_remote(project.id, None)
    with pytest.raises(ValueError, match="needs a configured remote"):
        service.set_git_allow_editor_push(project.id, True)


def test_removing_the_remote_clears_the_editor_push_opt_in(account, bare):
    service = account["service"]
    project, _module, _req = _project_with_requirement(service)
    service.set_git_remote(project.id, str(bare))
    service.set_git_allow_editor_push(project.id, True)
    assert service.git_status(project.id)["remote"]["allow_editor_push"] is True
    assert service.set_git_remote(project.id, None)["remote"]["allow_editor_push"] is False
    status = service.set_git_remote(project.id, str(bare))
    assert status["remote"]["allow_editor_push"] is False
    folder = Path(service.store.root) / project.id
    assert git(folder, "config", "--get", "reqman.allow-editor-push", check=False).strip() != "true"


# --- REST / WebSocket ---------------------------------------------------------------------


@pytest.fixture
def client(root, monkeypatch):
    monkeypatch.setenv("REQMAN_GIT", "on")
    monkeypatch.setenv("REQMAN_DATA_DIR", str(root))
    monkeypatch.setenv("REQMAN_CONFIG_FILE", str(root.parent / "config.json"))
    import reqman.app as app_module

    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_api_allow_editor_push_rest_and_ws_parity(client, root, bare):
    project_id = client.post("/projects", json={"name": "Api"}).json()["id"]
    module_id = client.post(f"/projects/{project_id}/modules", json={"name": "Mod", "prefix": "M"}).json()["id"]
    client.post(
        f"/projects/{project_id}/modules/{module_id}/requirements", json={"attributes": {"object_text": "x"}}
    )
    client.put(f"/projects/{project_id}/git/remote", json={"url": str(bare)})
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "project.git_set_allow_editor_push",
                "params": {"id": project_id, "enabled": True},
            }
        )
        result = ws.receive_json()["result"]
        assert result["remote"]["allow_editor_push"] is True
    rest = client.put(f"/projects/{project_id}/git/allow-editor-push", json={"enabled": True})
    assert rest.status_code == 200 and rest.json()["remote"]["allow_editor_push"] is True
    assert client.get(f"/projects/{project_id}/git").json()["remote"]["allow_editor_push"] is True
    off = client.put(f"/projects/{project_id}/git/allow-editor-push", json={"enabled": False})
    assert off.json()["remote"]["allow_editor_push"] is False
    # The login account path rejects nothing extra in the local edition (no login source):
    # the "already has a remote" conflict comes first, before any provider call.
    publish = client.post(f"/projects/{project_id}/git/publish", json={})
    assert publish.status_code == 409
