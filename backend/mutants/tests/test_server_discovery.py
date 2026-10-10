"""Smoke tests for the server edition's project discovery (``reqman_server/discovery.py``).

Every provider API call is answered by ``FakeHttp`` (GitHub / GitLab are
never contacted); the "remote" repository is a local bare repository in
``tmp_path`` (``allow_local_remotes``). Thorough coverage belongs to the
dedicated test slices.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from reqman.extensions import AccessDeniedError, RequestIdentity, identity_scope
from reqman.git_repo import GitHistory, GitSettings, remote_auth_scope
from reqman.providers import ConnectionStore, HttpResponse, MemoryTokenStore, ProviderError, ProviderService
from reqman.service import ProjectService
from reqman.store import ProjectStore
from reqman_server.discovery import DiscoveryService, build_discovery_router, build_discovery_rpc_methods
from reqman_server.memberships import MembershipRegistry
from reqman_server.permissions import Permissions
from reqman_server.repo_access import RepoAccess
from reqman_server.sessions import SessionStore
from reqman_server.settings import DEFAULT_GITHUB_SCOPES, ProviderSettings, ServerConfigError, ServerSettings, load_server_settings
from reqman_server.users import LoginClaims, UserRegistry

CHECKOUT = Path(__file__).resolve().parents[2]
TOKEN = "gho_SECRET_TOKEN_never_leaks"
RENEWED = "ghu_RENEWED_TOKEN_never_leaks"


def git(path: Path, *args: str) -> str:
    assert CHECKOUT not in [path.resolve(), *path.resolve().parents], "never run git in the checkout"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class FakeHttp:
    """Answers provider API requests from ``routes``: ``(method, path)`` -> data, or a ``ProviderError``."""

    def __init__(self, routes: dict[tuple[str, str], object]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.bodies: list[tuple[str, str, object]] = []

    def request(self, label, method, url, headers, body=None):  # noqa: ANN001 - ProviderHttp's signature
        self.requests.append((method, url, dict(headers)))
        self.bodies.append((method, urllib.parse.urlsplit(url).path, body))
        parsed = urllib.parse.urlsplit(url)
        answer = self.routes.get((method, parsed.path))
        if answer is None:
            raise ProviderError(f"{label} API error 404: Not Found", status=404)
        if isinstance(answer, ProviderError):
            raise answer
        if callable(answer):
            answer = answer(urllib.parse.parse_qs(parsed.query)) if body is None else answer(body)
        return HttpResponse(200, answer, {})

    def count(self, path: str) -> int:
        return sum(1 for _m, url, _h in self.requests if urllib.parse.urlsplit(url).path == path)


PROJECT_JSON = {"format_version": 9, "id": "brakes", "name": "Brake system", "attributes": [], "id_counters": {}}


def github_repo(full_name: str, clone_url: str, topics: list[str], **extra) -> dict:
    return {
        "id": abs(hash(full_name)) % 10_000,
        "full_name": full_name,
        "name": full_name.split("/")[1],
        "html_url": f"https://github.com/{full_name}",
        "clone_url": clone_url,
        "default_branch": "current",
        "private": True,
        "topics": topics,
        **extra,
    }


@pytest.fixture
def remote(tmp_path):
    """A bare repository holding a ReqManTool project on ``current`` (standing in for GitHub)."""
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = ProjectService(ProjectStore(source_root), history=GitHistory(GitSettings(enabled=True)))
    project = source.create_project("Brake system")
    source.create_module(project.id, "System", "SYRS")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    source.set_git_remote(project.id, str(bare))
    source.git_push(project.id)
    return {"bare": bare, "service": source, "project_id": project.id}


class World:
    def __init__(
        self,
        tmp_path: Path,
        provider_type: str,
        routes: dict,
        repo_roles: bool = True,
        github_app: bool = False,
        max_repos: int = 50,
        clients: dict | None = None,
        session_tokens: dict | None = None,
    ) -> None:
        url = "https://github.com" if provider_type == "github" else "https://gitlab.example.com"
        self.provider = ProviderSettings(
            id=provider_type, type=provider_type, name=provider_type.title(), issuer=url, client_id="client",
            github_app=github_app,
        )
        self.settings = ServerSettings(
            providers=(self.provider,), state_dir=tmp_path, repo_roles=repo_roles, discovery_max_repos=max_repos
        )
        self.clock = Clock()
        self.sessions = SessionStore(None, max_age=36_000, idle=36_000, clock=self.clock)
        self.users = UserRegistry(tmp_path / "users.json", admins=(), admin_groups=())
        self.user = self.users.login(
            LoginClaims(provider=provider_type, subject="42", name="Alice", email="alice@example.com", username="alice")
        )
        self.sessions.create(
            self.user.id, provider=provider_type, oauth_tokens=session_tokens or {"access_token": TOKEN}
        )
        self.http = FakeHttp(routes)
        data = tmp_path / "data"
        data.mkdir()
        self.service = ProjectService(ProjectStore(data), history=GitHistory(GitSettings(enabled=True)))
        remote_url = lambda pid: self.service.history.remote_url(self.service.store.project_path(pid))  # noqa: E731
        project_ids = lambda: self.service.store.list_project_ids()  # noqa: E731
        self.repo = RepoAccess(self.settings, clients or {}, self.sessions, remote_url, project_ids, http=self.http)
        permissions = Permissions(self.settings, self.users, MembershipRegistry(tmp_path / "m.json"), repo=self.repo)
        self.opened: list[dict] = []

        def open_repository(clone_url, is_same, auth):
            self.opened.append({"clone_url": clone_url, "auth": auth})
            return self.service.open_repository_project(clone_url, is_same, auth)

        self.discovery = DiscoveryService(
            self.settings, self.sessions, permissions, self.repo, project_ids, remote_url, open_repository,
            http=self.http, clock=self.clock, start=lambda fn: fn(), allow_local_remotes=True,
        )
        self.identity = RequestIdentity(user_id=self.user.id, name="Alice", email="alice@example.com")

    def __call__(self, fn, *args):
        with identity_scope(self.identity):
            return fn(*args)


def github_routes(bare: Path) -> dict:
    clone = str(bare)
    return {
        ("GET", "/user/repos"): [
            github_repo("acme/brakes", clone, ["reqmantool"]),
            github_repo("acme/scanned", clone, []),
            github_repo("acme/website", "https://github.com/acme/website.git", []),
            github_repo("acme/tagged-empty", "https://github.com/acme/tagged-empty.git", ["reqmantool"]),
        ],
        ("GET", "/repos/acme/brakes/contents/project.json"): PROJECT_JSON,
        ("GET", "/repos/acme/scanned/contents/project.json"): {**PROJECT_JSON, "id": "other", "name": "Other"},
        ("GET", "/repos/acme/website/contents/project.json"): {"name": "website", "version": "1.0.0"},
        ("GET", "/repos/acme/brakes"): github_repo("acme/brakes", clone, ["reqmantool"], permissions={"pull": True}),
    }


def github_app_routes(bare: Path) -> dict:
    """A GitHub App answers from its installations instead of ``/user/repos``."""
    clone = str(bare)
    return {
        ("GET", "/user/installations"): {
            "total_count": 2,
            "installations": [
                {"id": 41, "account": {"login": "acme"}},
                {"id": 42, "account": {"login": "acme-eu"}},
            ],
        },
        ("GET", "/user/installations/41/repositories"): {
            "total_count": 3,
            "repositories": [
                github_repo("acme/brakes", clone, ["reqmantool"]),
                github_repo("acme/scanned", clone, []),
                github_repo("acme/website", "https://github.com/acme/website.git", []),
            ],
        },
        ("GET", "/user/installations/42/repositories"): {
            "total_count": 1,
            "repositories": [
                github_repo("acme-eu/tagged-empty", "https://github.com/acme-eu/tagged-empty.git", ["reqmantool"]),
            ],
        },
        ("GET", "/repos/acme/brakes/contents/project.json"): PROJECT_JSON,
        ("GET", "/repos/acme/scanned/contents/project.json"): {**PROJECT_JSON, "id": "other", "name": "Other"},
        ("GET", "/repos/acme/website/contents/project.json"): {"name": "website", "version": "1.0.0"},
        ("GET", "/repos/acme/brakes"): github_repo("acme/brakes", clone, ["reqmantool"], permissions={"pull": True}),
    }


# --- discovery -------------------------------------------------------------------------------


def test_github_discovery_filters_topic_and_scanned_projects(tmp_path, remote):
    world = World(tmp_path, "github", github_routes(remote["bare"]))
    result = world(world.discovery.list)
    assert result["enabled"] is True and result["status"] == "idle" and result["error"] is None
    by_name = {r["full_name"]: r for r in result["repositories"]}
    assert set(by_name) == {"acme/brakes", "acme/scanned", "acme/tagged-empty"}
    assert by_name["acme/brakes"]["topic"] is True
    assert by_name["acme/brakes"]["project_id"] == "brakes"
    assert by_name["acme/brakes"]["project_name"] == "Brake system"
    assert by_name["acme/scanned"]["topic"] is False and by_name["acme/scanned"]["project_id"] == "other"
    assert "cannot be opened" in by_name["acme/tagged-empty"]["project_error"]
    assert all(r["already_opened"] is False for r in result["repositories"])
    assert "_id" not in by_name["acme/brakes"]
    # project.json is read from the branch ReqManTool clones, with the raw media type and the login token.
    reads = [(url, h) for m, url, h in world.http.requests if "/contents/project.json" in url]
    assert reads and all("ref=current" in url for url, _h in reads)
    assert all(h["Accept"] == "application/vnd.github.raw+json" for _u, h in reads)
    assert all(h["Authorization"] == f"Bearer {TOKEN}" for _m, _u, h in world.http.requests)
    assert TOKEN not in json.dumps(result)


def test_discovery_is_cached_until_refresh_or_ttl(tmp_path, remote):
    world = World(tmp_path, "github", github_routes(remote["bare"]))
    world(world.discovery.list)
    world(world.discovery.list)
    assert world.http.count("/user/repos") == 1
    world(world.discovery.refresh)
    assert world.http.count("/user/repos") == 2
    world.clock.now += world.settings.discovery_ttl_seconds + 1
    world(world.discovery.list)
    assert world.http.count("/user/repos") == 3


def test_provider_errors_are_shown_verbatim(tmp_path):
    error = ProviderError("GitHub API error 403: API rate limit exceeded for user ID 42.", status=403)
    world = World(tmp_path, "github", {("GET", "/user/repos"): error})
    result = world(world.discovery.list)
    assert result["error"] == "GitHub API error 403: API rate limit exceeded for user ID 42."
    assert result["repositories"] == []


def test_discovery_off_for_other_logins_and_without_repo_roles(tmp_path):
    world = World(tmp_path, "github", {}, repo_roles=False)
    result = world(world.discovery.list)
    assert result["enabled"] is False and "repo_roles" in result["reason"]
    with pytest.raises(AccessDeniedError):
        world.discovery.list()  # no identity


def test_gitlab_discovery_uses_topic_filter_and_raw_file(tmp_path, remote):
    clone = str(remote["bare"])

    def projects(query):
        if query.get("topic") == ["reqmantool"]:
            return [{"id": 7, "path_with_namespace": "acme/brakes", "http_url_to_repo": clone, "topics": ["reqmantool"]}]
        return [
            {"id": 7, "path_with_namespace": "acme/brakes", "http_url_to_repo": clone},
            {"id": 8, "path_with_namespace": "acme/web", "http_url_to_repo": "https://gitlab.example.com/acme/web.git"},
        ]

    world = World(tmp_path, "gitlab", {
        ("GET", "/api/v4/projects"): projects,
        ("GET", "/api/v4/projects/7/repository/files/project.json/raw"): PROJECT_JSON,
    })
    result = world(world.discovery.list)
    assert [r["full_name"] for r in result["repositories"]] == ["acme/brakes"]
    listing = [url for _m, url, _h in world.http.requests if urllib.parse.urlsplit(url).path == "/api/v4/projects"]
    assert all("membership=true" in url for url in listing)
    assert any("topic=reqmantool" in url for url in listing)


# --- GitHub App (slice 2) ------------------------------------------------------------------------


def test_github_app_discovery_lists_the_installations_repositories(tmp_path, remote):
    world = World(tmp_path, "github", github_app_routes(remote["bare"]), github_app=True)
    result = world(world.discovery.list)
    assert result["enabled"] is True and result["status"] == "idle" and result["error"] is None
    by_name = {r["full_name"]: r for r in result["repositories"]}
    # The repositories of every installation, topic and project.json candidates alike.
    assert set(by_name) == {"acme/brakes", "acme/scanned", "acme-eu/tagged-empty"}
    assert by_name["acme/brakes"]["topic"] is True and by_name["acme/brakes"]["project_id"] == "brakes"
    assert by_name["acme/scanned"]["topic"] is False and by_name["acme/scanned"]["project_id"] == "other"
    assert "cannot be opened" in by_name["acme-eu/tagged-empty"]["project_error"]
    # The installations are listed, /user/repos never is.
    assert world.http.count("/user/repos") == 0
    assert world.http.count("/user/installations") == 1
    assert world.http.count("/user/installations/41/repositories") == 1
    assert world.http.count("/user/installations/42/repositories") == 1
    assert all(h["Authorization"] == f"Bearer {TOKEN}" for _m, _u, h in world.http.requests)
    assert TOKEN not in json.dumps(result)


def test_github_app_scan_stops_at_max_repos(tmp_path, remote):
    world = World(tmp_path, "github", github_app_routes(remote["bare"]), github_app=True, max_repos=1)
    result = world(world.discovery.list)
    assert result["max_repos"] == 1 and result["truncated"] is True
    assert [r["full_name"] for r in result["repositories"]] == ["acme/brakes"]


def test_github_app_scan_renews_an_expiring_user_token(tmp_path, remote):
    class Client:
        """The login provider's OAuth client: a GitHub App's expiring user tokens can be refreshed."""

        def refresh_tokens(self, refresh_token):
            self.used = refresh_token
            return {"access_token": RENEWED, "refresh_token": refresh_token, "expires_in": 7200}

    client = Client()
    world = World(
        tmp_path,
        "github",
        github_app_routes(remote["bare"]),
        github_app=True,
        clients={"github": client},
        session_tokens={"access_token": TOKEN, "refresh_token": "refresh-me", "expires_in": 7200},
    )
    world.clock.now += 7200 - 30  # inside the refresh margin
    result = world(world.discovery.list)
    assert result["error"] is None and result["repositories"]
    assert client.used == "refresh-me"
    # Every API call of the scan carried the renewed token; the expired one never left.
    assert all(h["Authorization"] == f"Bearer {RENEWED}" for _m, _u, h in world.http.requests)


# --- opening -----------------------------------------------------------------------------------


def test_open_clones_once_then_fetches(tmp_path, remote):
    world = World(tmp_path, "github", github_routes(remote["bare"]))
    result = world(world.discovery.open, "github", "acme/brakes")
    assert result["cloned"] is True and result["project_id"] == remote["project_id"]
    assert result["role"] == "viewer" and result["permission"] == "Read"
    assert world.service.store.project_exists(remote["project_id"])
    listed = world(world.discovery.refresh)
    brakes = next(r for r in listed["repositories"] if r["full_name"] == "acme/brakes")
    assert brakes["already_opened"] is True and brakes["opened_project_id"] == remote["project_id"]
    # A new commit on the remote: opening again fetches and fast-forwards.
    remote["service"].create_module(remote["project_id"], "Software", "SWRS")
    remote["service"].git_push(remote["project_id"])
    again = world(world.discovery.open, "github", "acme/brakes")
    assert again["cloned"] is False and again["updated"] is True
    assert len(world.service.list_modules(remote["project_id"])) == 2


def test_open_needs_read_access(tmp_path, remote):
    routes = github_routes(remote["bare"])
    routes[("GET", "/repos/acme/brakes")] = github_repo("acme/brakes", str(remote["bare"]), [], permissions={})
    world = World(tmp_path, "github", routes)
    with pytest.raises(AccessDeniedError, match="needs at least Read"):
        world(world.discovery.open, "github", "acme/brakes")
    with pytest.raises(AccessDeniedError):
        world(world.discovery.open, "github", "acme/missing")  # 404: no access
    with pytest.raises(ValueError, match="logged in with"):
        world(world.discovery.open, "gitlab", "acme/brakes")
    assert world.service.store.list_project_ids() == []


def test_github_app_open_clones_with_the_user_token(tmp_path, remote):
    world = World(tmp_path, "github", github_app_routes(remote["bare"]), github_app=True)
    result = world(world.discovery.open, "github", "acme/brakes")
    assert result["cloned"] is True and result["project_id"] == remote["project_id"]
    assert result["role"] == "viewer" and result["permission"] == "Read"
    env = world.opened[0]["auth"]("https://github.com/acme/brakes.git")
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert TOKEN not in env["GIT_CONFIG_VALUE_0"]


def test_open_refuses_a_foreign_clone_url(tmp_path, remote):
    routes = github_routes(remote["bare"])
    routes[("GET", "/repos/acme/brakes")] = github_repo(
        "acme/brakes", "https://evil.example.com/acme/brakes.git", [], permissions={"pull": True}
    )
    world = World(tmp_path, "github", routes)
    with pytest.raises(ValueError, match="not a repository of https://github.com"):
        world(world.discovery.open, "github", "acme/brakes")


def test_token_never_reaches_git_config_remote_or_responses(tmp_path, remote):
    world = World(tmp_path, "github", github_routes(remote["bare"]))
    result = world(world.discovery.open, "github", "acme/brakes")
    assert TOKEN not in json.dumps(result)
    folder = world.service.store.project_path(result["project_id"])
    config = (folder / ".git" / "config").read_text()
    assert TOKEN not in config and "extraHeader" not in config
    assert git(folder, "remote", "get-url", "origin").strip() == str(remote["bare"])
    # The one-shot auth: a Basic header scoped to the provider for https URLs of it only.
    auth = world.opened[0]["auth"]
    env = auth("https://github.com/acme/brakes.git")
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert TOKEN not in env["GIT_CONFIG_VALUE_0"]  # base64 of x-access-token:<token>
    assert auth("https://evil.example.com/acme/brakes.git") is None
    assert auth(str(remote["bare"])) is None


def test_remote_auth_scope_reaches_only_the_git_processes_inside(tmp_path):
    history = GitHistory(GitSettings(enabled=True))
    history.remote_auth = lambda url: {"FROM": "connection"}
    with remote_auth_scope(lambda url: {"FROM": "login"}):
        assert history._remote_env("https://github.com/a/b.git")["FROM"] == "login"
    assert history._remote_env("https://github.com/a/b.git")["FROM"] == "connection"


# --- REST / WebSocket parity -------------------------------------------------------------------


def test_rest_and_websocket_parity(tmp_path, remote):
    world = World(tmp_path, "github", github_routes(remote["bare"]))
    events: list[dict] = []

    async def broadcast(message):
        events.append(message)

    app = FastAPI()
    app.include_router(build_discovery_router(world.discovery, broadcast))

    class Identity:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            with identity_scope(world.identity):
                await self.inner(scope, receive, send)

    app.add_middleware(Identity)
    client = TestClient(app)
    methods = build_discovery_rpc_methods(world.discovery, broadcast)

    rest_list = client.get("/discovery/projects").json()
    ws_list = world(methods["discovery.list"], {})
    assert rest_list == ws_list
    assert client.post("/discovery/refresh").json() == world(methods["discovery.refresh"], {})

    response = client.post("/discovery/open", json={"provider": "github", "repo": "acme/brakes"})
    assert response.status_code == 200, response.text
    assert response.json()["cloned"] is True
    assert events == [{"event": "changed", "project_id": remote["project_id"]}]
    with identity_scope(world.identity):
        ws_open = asyncio.run(methods["discovery.open"]({"provider": "github", "repo": "acme/brakes"}))
    assert ws_open["project_id"] == remote["project_id"] and ws_open["cloned"] is False
    assert events[-1] == {"event": "changed", "project_id": remote["project_id"]}

    denied = client.post("/discovery/open", json={"provider": "github", "repo": "acme/missing"})
    assert denied.status_code == 403


# --- "Publish project" tags the repository ---------------------------------------------------


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_publish_adds_the_reqmantool_topic(tmp_path, provider):
    bare = tmp_path / "published.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    api = "" if provider == "github" else "/api/v4"
    routes = {
        ("GET", f"{api}/user"): {"login": "alice", "username": "alice", "name": "Alice"},
        ("POST", "/user/repos"): {"full_name": "alice/brakes", "clone_url": str(bare), "html_url": "x"},
        ("GET", "/repos/alice/brakes/topics"): {"names": ["safety"]},
        ("PUT", "/repos/alice/brakes/topics"): {"names": ["safety", "reqmantool"]},
        ("POST", "/api/v4/projects"): {"path_with_namespace": "alice/brakes", "http_url_to_repo": str(bare)},
    }
    http = FakeHttp(routes)
    providers = ProviderService(ConnectionStore(None), MemoryTokenStore(), http)
    url = "https://github.com" if provider == "github" else "https://gitlab.example.com"
    connection = providers.add_connection(provider, url, None, "token")
    data = tmp_path / "data"
    data.mkdir()
    service = ProjectService(ProjectStore(data), history=GitHistory(GitSettings(enabled=True)), providers=providers)
    project = service.create_project("Brakes")
    service.create_module(project.id, "System", "SYRS")
    result = service.publish_project(project.id, connection["id"])
    assert "topic_error" not in result
    if provider == "github":
        assert ("PUT", "/repos/alice/brakes/topics", {"names": ["safety", "reqmantool"]}) in http.bodies
    else:
        created = next(b for m, p, b in http.bodies if m == "POST" and p == "/api/v4/projects")
        assert created["topics"] == ["reqmantool"]


# --- the provider configuration --------------------------------------------------------------------


def test_github_app_is_a_flag_on_the_github_provider(tmp_path):
    config = {
        "server": {
            "auth_providers": [
                {
                    "id": "github-app",
                    "type": "github",
                    "github_app": True,
                    "url": "https://github.com",
                    "client_id": "app-client-id",
                }
            ]
        }
    }
    settings = load_server_settings(config, tmp_path / "config.json", env={})
    provider = settings.provider("github-app")
    # A GitHub App is a github provider with a flag: same login flow, client, roles and API.
    assert provider is not None and provider.type == "github" and provider.github_app is True
    # GitHub ignores the scope parameter of a GitHub App's authorization; the default stays.
    assert provider.scopes == DEFAULT_GITHUB_SCOPES
    # The flag on another provider type is a configuration error, not something to ignore.
    bad = {"server": {"auth_providers": [{"id": "gitlab", "type": "gitlab", "github_app": True, "client_id": "x"}]}}
    with pytest.raises(ServerConfigError, match="github_app"):
        load_server_settings(bad, tmp_path / "config.json", env={})
