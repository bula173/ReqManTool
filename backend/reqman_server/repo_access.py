"""Roles from repository permissions: a user's role in a project from their GitLab / GitHub access.

A project whose git remote lives on the GitLab or GitHub instance the user
logged in with is *bound* to that repository: the user's role in it is
their permission on the repository, read from the provider's REST API with
the user's own OAuth access token (kept server-side in the session,
``sessions.py``). One source of truth: revoking access in GitLab/GitHub
revokes it in ReqManTool (after the cache lifetime at most).

Mapping (``REPO_ROLES``)::

    GitLab Guest / Planner / Reporter, GitHub Read  → viewer
    GitHub Triage                                    → reviewer
    GitLab Developer, GitHub Write                   → editor
    GitLab Maintainer / Owner, GitHub Maintain/Admin → project-admin

A visible repository without a membership (a public or internal GitLab
project, a public GitHub repository) gives ``Read`` → viewer; one the
user cannot see gives no role.

Binding: the remote URL (``git config remote.origin.url``) is matched to
the login provider's URL — https remotes by host and the URL's path prefix,
ssh / scp-like remotes by host (``git_hosts`` adds other host names, e.g. a
separate ssh host). Projects without a remote, with a remote elsewhere, and
users who logged in with another provider (or a generic OIDC one) are not
bound: their roles come from the in-app memberships and group rules
(``permissions.py``). When bound, the repository permission wins — an
in-app membership or group rule of the same user does not raise or lower
it. Server admins stay in-app (they may do everything).

Cache: a resolved role is reused for ``server.repo_roles.cache_minutes``
(default 5), per (user, project). A failed lookup (provider unreachable,
token refused) keeps the last good role for up to ``grace_minutes``
(default 60) and is retried after ``ERROR_RETRY_SECONDS``; without a good
role the user has no access, and the error says why. Roles are resolved for
every bound project at login, and again on demand (``sync``: the admin
pages' "Sync access now"). An expired access token is renewed with the
session's refresh token (GitLab tokens live two hours).

Network calls go through ``reqman.providers`` (``ProviderHttp``: stdlib
``urllib``, a timeout, no redirects; provider errors verbatim).
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Callable

from reqman.providers import (
    GITHUB,
    GITLAB,
    GitHubProvider,
    GitLabProvider,
    ProviderConnection,
    ProviderError,
    ProviderHttp,
    parse_remote,
)

from .oidc import OidcError
from .sessions import SessionStore
from .settings import ProviderSettings, ServerSettings
from .users import User

logger = logging.getLogger("reqman_server.repo_access")

REPO_PROVIDER_TYPES = (GITLAB, GITHUB)
# Provider permission → ReqManTool role.
REPO_ROLES: dict[str, dict[str, str]] = {
    GITLAB: {
        "Read": "viewer",
        "Guest": "viewer",
        "Planner": "viewer",
        "Reporter": "viewer",
        "Developer": "editor",
        "Maintainer": "project-admin",
        "Owner": "project-admin",
    },
    GITHUB: {
        "Read": "viewer",
        "Triage": "reviewer",
        "Write": "editor",
        "Maintain": "project-admin",
        "Admin": "project-admin",
    },
}
# The lowest provider permission that gives (at least) a role, for the error messages.
NEEDED_PERMISSION: dict[str, dict[str, str]] = {
    GITLAB: {"viewer": "Guest", "reviewer": "Developer", "editor": "Developer", "project-admin": "Maintainer"},
    GITHUB: {"viewer": "Read", "reviewer": "Triage", "editor": "Write", "project-admin": "Maintain"},
}
PROVIDER_LABELS = {GITLAB: "GitLab", GITHUB: "GitHub"}
# A failed lookup is retried after this long (not on every request).
ERROR_RETRY_SECONDS = 30.0
# A remote URL (git config) is read again after this long at most.
REMOTE_TTL_SECONDS = 60.0
# Refresh an access token this long before it expires.
TOKEN_EXPIRY_MARGIN = 60.0
HTTP_TIMEOUT = 10.0
MAX_PARALLEL_LOOKUPS = 8


def _web_url(provider: ProviderSettings) -> str:
    """The provider's web URL (GitLab: the OIDC issuer is the GitLab URL; GitHub: the configured URL)."""
    return provider.issuer.rstrip("/")


@dataclass(frozen=True)
class RepoBinding:
    """A project's repository on a login provider."""

    provider: ProviderSettings
    path: str  # rbc/rbc-gp
    display: str  # gitlab.example.com/rbc/rbc-gp

    @property
    def provider_type(self) -> str:
        return self.provider.type

    def public(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider.id,
            "provider_type": self.provider.type,
            "provider_name": self.provider.name,
            "path": self.path,
            "repository": self.display,
        }


@dataclass(frozen=True)
class RepoRole:
    """A user's role in a bound project, from their repository permission."""

    binding: RepoBinding
    role: str | None  # None: no access (or unknown, see ``error``)
    permission: str | None  # the provider's name for it (``Developer``, ``Write``, ...); None: none
    checked_at: float
    error: str | None = None  # the last lookup failed (the role is the last good one, or None)

    def needs(self, role: str) -> str:
        """``needs Developer on gitlab.example.com/rbc/rbc-gp``."""
        needed = NEEDED_PERMISSION[self.binding.provider_type].get(role, role)
        return f"needs {needed} on {self.binding.display}"

    def describe(self) -> str:
        """``Developer on gitlab.example.com/rbc/rbc-gp`` / ``no access to ...``."""
        if self.permission:
            return f"{self.permission} on {self.binding.display}"
        return f"no access to {self.binding.display}"

    def public(self) -> dict[str, Any]:
        return {
            **self.binding.public(),
            "source": "repo",
            "permission": self.permission,
            "role": self.role,
            "checked_at": int(self.checked_at),
            "error": self.error,
        }


def match_remote(remote_url: str, provider: ProviderSettings) -> RepoBinding | None:
    """The repository of ``remote_url`` on ``provider`` (a ``gitlab``/``github`` login), or ``None``."""
    if provider.type not in REPO_PROVIDER_TYPES:
        return None
    parts = parse_remote(remote_url)
    if parts is None:
        return None
    scheme, host, port, path = parts
    web = urllib.parse.urlsplit(_web_url(provider))
    web_host = (web.hostname or "").lower()
    web_path = web.path.rstrip("/")
    if host != web_host and host not in provider.git_hosts:
        return None
    default_port = {"https": 443, "http": 80}
    if scheme in ("https", "http") and host == web_host and (port or default_port[scheme]) != (
        web.port or default_port.get(web.scheme, 443)
    ):
        return None
    if scheme in ("https", "http") and host == web_host and web_path:
        if not (path == web_path or path.startswith(web_path + "/")):
            return None
        path = path[len(web_path):]
    elif web_path and path.startswith(web_path + "/"):
        path = path[len(web_path):]
    repo = path.strip("/")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    segments = repo.split("/")
    if len(segments) < 2 or any(not s or s in (".", "..") for s in segments):
        return None
    if provider.type == GITHUB and len(segments) != 2:
        return None
    return RepoBinding(provider=provider, path=repo, display=f"{web.netloc.lower()}{web_path}/{repo}")


class RepoAccess:
    """Resolves and caches users' repository roles (see the module docstring)."""

    def __init__(
        self,
        settings: ServerSettings,
        clients: dict[str, Any],
        sessions: SessionStore,
        remote_url: Callable[[str], str | None] | None,
        project_ids: Callable[[], list[str]] | None,
        http: ProviderHttp | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.clients = clients
        self.sessions = sessions
        self.remote_url = remote_url
        self.project_ids = project_ids
        self.http = http or ProviderHttp(timeout=HTTP_TIMEOUT)
        self.clock = clock
        self.ttl = float(settings.repo_roles_ttl_seconds)
        self.grace = float(settings.repo_roles_grace_seconds)
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        # (user id, project id) -> (role, reuse until)
        self._roles: dict[tuple[str, str], tuple[RepoRole, float]] = {}
        # project id -> (remote URL, read at)
        self._remotes: dict[str, tuple[str | None, float]] = {}

    @property
    def enabled(self) -> bool:
        return (
            self.settings.repo_roles
            and self.remote_url is not None
            and any(p.type in REPO_PROVIDER_TYPES for p in self.settings.providers)
        )

    # --- binding -----------------------------------------------------------------------

    def _remote(self, project_id: str) -> str | None:
        now = self.clock()
        with self._lock:
            cached = self._remotes.get(project_id)
            if cached is not None and now - cached[1] < min(self.ttl, REMOTE_TTL_SECONDS):
                return cached[0]
        try:
            url = self.remote_url(project_id) if self.remote_url else None
        except Exception:  # noqa: BLE001 - an unreadable repository is simply not bound
            logger.exception("The remote of project %s could not be read", project_id)
            url = None
        with self._lock:
            self._remotes[project_id] = (url, now)
        return url

    def binding(self, project_id: str, provider_id: str) -> RepoBinding | None:
        """The project's repository on the login provider ``provider_id``, or ``None`` (not bound)."""
        if not self.enabled:
            return None
        provider = self.settings.provider(provider_id)
        if provider is None or provider.type not in REPO_PROVIDER_TYPES:
            return None
        url = self._remote(project_id)
        return match_remote(url, provider) if url else None

    def bindings(self, project_id: str) -> list[RepoBinding]:
        """The project's repository on every repository login provider it lives on (admin pages)."""
        if not self.enabled:
            return []
        url = self._remote(project_id)
        if not url:
            return []
        found = [match_remote(url, p) for p in self.settings.providers]
        return [b for b in found if b is not None]

    # --- roles -------------------------------------------------------------------------

    def role(self, user: User, project_id: str, refresh: bool = False) -> RepoRole | None:
        """The user's repository role in the project, ``None`` when the project is not bound for them."""
        binding = self.binding(project_id, user.provider)
        if binding is None:
            return None
        key = (user.id, project_id)
        now = self.clock()
        with self._lock:
            cached = self._roles.get(key)
        if cached is not None and not refresh and now < cached[1] and cached[0].binding == binding:
            return cached[0]
        result = self._lookup(user, binding)
        previous = cached[0] if cached is not None and cached[0].binding == binding else None
        if result.error is not None:
            if previous is not None and previous.role is not None and now - previous.checked_at < self.grace:
                # Keep the last good role for a while (provider down, token refresh failed).
                result = replace(previous, error=result.error)
            logger.warning("Repository role of %s in %s: %s", user.id, project_id, result.error)
            until = now + ERROR_RETRY_SECONDS
        else:
            until = now + self.ttl
        with self._lock:
            self._roles[key] = (result, until)
        return result

    def cached(self, user_id: str, project_id: str) -> RepoRole | None:
        """The cached role (no lookup; admin pages show other users' roles this way)."""
        with self._lock:
            entry = self._roles.get((user_id, project_id))
        return entry[0] if entry is not None else None

    def forget_remotes(self, project_id: str | None = None) -> None:
        """Read the remote URLs again (of one project, or all) at the next lookup."""
        with self._lock:
            if project_id is None:
                self._remotes.clear()
            else:
                self._remotes.pop(project_id, None)

    def forget(self, user_id: str | None = None, project_id: str | None = None) -> None:
        """Drop cached roles (of a user, of a project, or all) and the remote URLs read so far."""
        with self._lock:
            for key in [k for k in self._roles if (user_id in (None, k[0])) and (project_id in (None, k[1]))]:
                del self._roles[key]
            if project_id is None:
                self._remotes.clear()
            else:
                self._remotes.pop(project_id, None)

    def resolve_user(self, user: User, project_ids: list[str] | None = None) -> dict[str, Any]:
        """Look up the user's role in every bound project now (login, "Sync access now").

        Bypasses the cache; a failed lookup keeps the last good role within the grace period.
        """
        if not self.enabled or self.settings.provider(user.provider) is None:
            return {"projects": 0, "errors": []}
        ids = project_ids if project_ids is not None else (self.project_ids() if self.project_ids else [])
        bound = [pid for pid in ids if self.binding(pid, user.provider) is not None]
        if not bound:
            return {"projects": 0, "errors": []}
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_LOOKUPS, len(bound))) as pool:
            results = list(pool.map(lambda pid: (pid, self.role(user, pid, refresh=True)), bound))
        errors = [
            {"project_id": pid, "user_id": user.id, "error": r.error}
            for pid, r in results
            if r is not None and r.error
        ]
        return {"projects": len(bound), "errors": errors}

    # --- the lookup -------------------------------------------------------------------

    def _lookup(self, user: User, binding: RepoBinding) -> RepoRole:
        now = self.clock()
        provider = binding.provider

        def failed(message: str) -> RepoRole:
            return RepoRole(binding=binding, role=None, permission=None, checked_at=now, error=message)

        found = self.sessions.oauth_session(user.id, provider.id)
        if found is None:
            return failed(
                f"your session holds no {provider.name} token to read your permission on {binding.display}; "
                "log out and log in again"
            )
        key, session = found
        token = session.access_token
        try:
            if session.token_expires and now > session.token_expires - TOKEN_EXPIRY_MARGIN and session.refresh_token:
                token = self._refresh(key, token)
            try:
                permission = self._permission(binding, token, user)
            except ProviderError as exc:
                if exc.status != 401 or not session.refresh_token:
                    raise
                token = self._refresh(key, token)
                permission = self._permission(binding, token, user)
        except ProviderError as exc:
            if exc.status == 401:
                return failed(f"{provider.name} refused your sign-in token ({exc}); log out and log in again")
            return failed(f"{provider.name} permission lookup failed: {exc}")
        except OidcError as exc:
            return failed(f"your {provider.name} sign-in could not be renewed ({exc}); log out and log in again")
        role = REPO_ROLES[provider.type].get(permission) if permission else None
        return RepoRole(binding=binding, role=role, permission=permission, checked_at=now)

    def _permission(self, binding: RepoBinding, token: str, user: User) -> str | None:
        provider = binding.provider
        connection = ProviderConnection(
            id=f"login-{provider.id}", provider=provider.type, name=provider.name, url=_web_url(provider)
        )
        if provider.type == GITLAB:
            # The GitLab OIDC subject is the numeric user id (inherited memberships need it).
            return GitLabProvider(connection, token, self.http, oauth=True).repository_permission(
                binding.path, user.subject
            )
        return GitHubProvider(connection, token, self.http, oauth=True).repository_permission(binding.path)

    def _refresh(self, key: str, used_token: str) -> str:
        """A fresh access token for the session ``key`` (``OidcError`` when the provider refuses)."""
        with self._refresh_lock:
            session = self.sessions.session_by_key(key)
            if session is None:
                raise OidcError("the session ended")
            if session.access_token and session.access_token != used_token:
                return session.access_token  # renewed meanwhile by another request
            if not session.refresh_token:
                raise OidcError("no refresh token")
            client = self.clients.get(session.provider)
            if client is None or not hasattr(client, "refresh_tokens"):
                raise OidcError("the login provider is not configured any more")
            tokens = client.refresh_tokens(session.refresh_token)
            self.sessions.update_tokens(key, tokens)
            logger.info("Renewed the %s token of user %s", session.provider, session.user_id)
            return str(tokens["access_token"])
