"""The login account (``reqman_server/login_account.py``): publish / push / pull with the
login OAuth token, without a stored provider connection.

Every provider API call is answered by ``FakeHttp`` (GitLab / GitHub are never
contacted); git authentication is checked through ``git_auth_env`` /
``GitHistory.remote_auth`` (the same hook the real git processes read).
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reqman.extensions import RequestIdentity, identity_scope
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
from reqman_server.login_account import LoginAccountSource, git_login_provider
from reqman_server.sessions import SessionStore
from reqman_server.settings import ProviderSettings, ServerSettings
from reqman_server.users import LoginClaims, UserRegistry

TOKEN = "glpat-login-token-never-stored"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class FakeHttp:
    """Answers provider API requests from ``routes``: ``(method, path)`` -> data, or a ``ProviderError``."""

    def __init__(self, routes: dict | None = None) -> None:
        self.routes = routes or {}
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def request(self, label, method, url, headers, body=None):  # noqa: ANN001 - ProviderHttp's signature
        import urllib.parse

        self.requests.append((method, url, dict(headers)))
        answer = self.routes.get((method, urllib.parse.urlsplit(url).path))
        if answer is None:
            raise ProviderError(f"{label} API error 404: Not Found", status=404)
        if isinstance(answer, ProviderError):
            raise answer
        return HttpResponse(200, answer, {})


@pytest.fixture
def account(tmp_path):
    """A signed-in GitLab user with a session token and a ``LoginAccountSource`` on a ProviderService."""
    provider = ProviderSettings(
        id="gitlab", type="gitlab", name="GitLab", issuer="https://gitlab.example.com", client_id="client"
    )
    settings = ServerSettings(providers=(provider,), state_dir=tmp_path)
    users = UserRegistry(tmp_path / "users.json", admins=(), admin_groups=())
    user = users.login(
        LoginClaims(provider="gitlab", subject="42", name="Alice", email="a@example.com", username="alice")
    )
    clock = Clock()
    sessions = SessionStore(None, max_age=36_000, idle=36_000, clock=clock)
    sessions.create(
        user.id,
        provider="gitlab",
        oauth_tokens={"access_token": TOKEN, "refresh_token": "refresh-token", "expires_in": 7200},
    )
    source = LoginAccountSource(settings, users, sessions, clock=clock)
    http = FakeHttp({("GET", "/api/v4/user"): {"username": "alice", "name": "Alice"}})
    providers = ProviderService(ConnectionStore(None), MemoryTokenStore(), http)
    providers.login_source = source
    return {
        "source": source,
        "providers": providers,
        "settings": settings,
        "users": users,
        "sessions": sessions,
        "user": user,
        "clock": clock,
        "http": http,
        "identity": RequestIdentity(user_id=user.id, name="Alice", email="a@example.com"),
    }


# --- LoginAccountSource ------------------------------------------------------------------


def test_login_account_resolves_the_session_token(account):
    with identity_scope(account["identity"]):
        connection, token = account["source"]()
    assert token == TOKEN
    assert connection.id == LOGIN_CONNECTION_ID
    assert connection.provider == "gitlab"
    assert connection.url == "https://gitlab.example.com"
    # The token never lands in the connection list (there is no stored connection).
    assert account["providers"].list_connections() == []


def test_login_account_refuses_without_identity_or_login_provider(account):
    with pytest.raises(ValueError, match="not signed in"):
        account["source"]()
    oidc = ProviderSettings(id="sso", type="oidc", name="SSO", issuer="https://sso.example.com", client_id="c")
    settings = ServerSettings(providers=(oidc,), state_dir=account["settings"].state_dir)
    source = LoginAccountSource(settings, account["users"], account["sessions"])
    with identity_scope(account["identity"]):
        with pytest.raises(ValueError, match="does not host git repositories"):
            source()
    assert git_login_provider(settings, account["users"].get(account["user"].id)) is None


def test_login_account_renews_an_expiring_token(account):
    sessions, source, clock = account["sessions"], account["source"], account["clock"]
    renewed: list[str] = []
    source.refresh = lambda key, used: renewed.append(used) or "renewed-token"
    clock.now += 7200 - 30  # inside the refresh margin
    with identity_scope(account["identity"]):
        _connection, token = source()
    assert token == "renewed-token" and renewed == [TOKEN]
    # Outside the margin the stored token is used as it is.
    clock.now = 1_000_000.0
    with identity_scope(account["identity"]):
        assert source()[1] == TOKEN
    assert renewed == [TOKEN]


def test_login_account_without_a_session_token_refuses(account):
    account["sessions"].delete_user(account["user"].id)
    with identity_scope(account["identity"]):
        with pytest.raises(ValueError, match="no GitLab token"):
            account["source"]()


# --- ProviderService (the hook points) ----------------------------------------------------


def test_token_and_provider_use_the_login_account(account):
    providers = account["providers"]
    with identity_scope(account["identity"]):
        assert providers.token(LOGIN_CONNECTION_ID) == TOKEN
        # The token is read fresh every call, never cached (sessions refresh it).
        account["sessions"].delete_user(account["user"].id)
        with pytest.raises(ValueError, match="no GitLab token"):
            providers.token(LOGIN_CONNECTION_ID)
        # A provider over the login connection authenticates with the OAuth token (Bearer).
        account["sessions"].create(
            account["user"].id, provider="gitlab", oauth_tokens={"access_token": TOKEN}
        )
        provider = providers.provider(LOGIN_CONNECTION_ID)
        assert provider.kind == "gitlab" and provider.oauth is True
        assert provider.current_user() == {"username": "alice", "name": "Alice"}
    assert account["http"].requests[-1][2]["Authorization"] == f"Bearer {TOKEN}"


def test_git_auth_env_with_the_login_account(account):
    providers = account["providers"]
    with identity_scope(account["identity"]):
        env = providers.git_auth_env("https://gitlab.example.com/acme/brakes.git")
        match = providers.connection_for_remote("https://gitlab.example.com/acme/brakes.git")
    assert env is not None
    assert env["GIT_CONFIG_KEY_0"] == "http.https://gitlab.example.com/.extraHeader"
    expected = base64.b64encode(f"oauth2:{TOKEN}".encode()).decode()
    assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {expected}"
    assert TOKEN not in env["GIT_CONFIG_VALUE_0"]  # base64, never the plain token
    assert env["GIT_CONFIG_KEY_1"] == "http.https://gitlab.example.com/.followRedirects"
    # Only the login host: another https host and a local path stay unauthenticated.
    assert providers.git_auth_env("https://evil.example.com/acme/brakes.git") is None
    assert providers.git_auth_env("/srv/remote.git") is None
    assert match is not None and match[0].id == LOGIN_CONNECTION_ID and match[1] == "acme/brakes"


def test_stored_connection_wins_over_the_login_account(account):
    providers = account["providers"]
    http = FakeHttp({("GET", "/api/v4/user"): {"username": "alice", "name": "Alice"}})
    providers.http = http
    connection = providers.add_connection("gitlab", "https://gitlab.example.com", None, "stored-token")
    match = providers.connection_for_remote("https://gitlab.example.com/acme/brakes.git")
    assert match is not None and match[0].id == connection["id"]
    env = providers.git_auth_env("https://gitlab.example.com/acme/brakes.git")
    stored = base64.b64encode(b"oauth2:stored-token").decode()
    assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {stored}"


def test_local_edition_without_a_login_source_is_unchanged():
    providers = ProviderService(ConnectionStore(None), MemoryTokenStore(), FakeHttp())
    with pytest.raises(ValueError, match="signed-in session"):
        providers.token(LOGIN_CONNECTION_ID)
    with pytest.raises(ValueError, match="signed-in session"):
        providers.provider(LOGIN_CONNECTION_ID)
    # Best effort paths simply find nothing, exactly as before.
    assert providers.login_connection() is None
    assert providers.connection_for_remote("https://gitlab.example.com/a/b.git") is None
    assert providers.git_auth_env("https://gitlab.example.com/a/b.git") is None


def test_service_git_auth_comes_from_the_login_account(account, tmp_path):
    """``ProjectService`` wires ``GitHistory.remote_auth`` to ``git_auth_env``: one git
    process gets the login token through the extra environment only."""
    data = tmp_path / "data"
    data.mkdir()
    service = ProjectService(
        ProjectStore(data), history=GitHistory(GitSettings(enabled=True)), providers=account["providers"]
    )
    with identity_scope(account["identity"]):
        env = service.history.remote_auth("https://gitlab.example.com/acme/brakes.git")
    assert env is not None and "Basic" in env["GIT_CONFIG_VALUE_0"]
    assert TOKEN not in str(service.history.settings)
