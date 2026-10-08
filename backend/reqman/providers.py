"""GitLab and GitHub integration: provider connections, tokens and the REST APIs.

One provider abstraction (``Provider``) with two implementations:
``GitLabProvider`` (GitLab REST v4, gitlab.com or a self-hosted URL) and
``GitHubProvider`` (GitHub REST, github.com or a GitHub Enterprise URL).
Every API call runs server-side, through ``ProviderHttp`` (stdlib
``urllib``; a timeout on every request, no redirects followed, so a token
header is never forwarded to another host). Provider error messages are
passed on verbatim (``ProviderError``, a ``ValueError``: REST 400).

Connections (``ProviderConnection``: provider, URL, name, user name) are
app-level settings, stored in ``connections.json`` beside the config file
(``config.connections_file``), never in project data or git. The personal
access token of a connection (local edition) lives in the OS keychain
through the ``keyring`` package (``KeyringTokenStore``); it is never
written to a file, a log, a git config or a remote URL, and never sent to
the browser. ``REQMAN_TOKEN_STORE=memory`` keeps tokens in process memory
instead (tests, scratch servers).

Server edition (later): the abstraction point is ``TokenStore`` /
``ProviderService.token``: a server edition resolves the token of the
logged-in user's OAuth login instead of a keychain entry; everything
above it (providers, git authentication) stays the same.

git over HTTPS authenticates with the token through ``git_auth_env``:
``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_<n>``/``GIT_CONFIG_VALUE_<n>``
environment variables of the one git process (not its command line, not
``.git/config``) set ``http.<connection URL>/.extraHeader`` to a Basic
authorization header — scoped by git to URLs under the connection's URL —
and ``http.<connection URL>/.followRedirects=false``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .formats import CONNECTIONS_FORMAT, check_format_version, upgrade, with_format_version

logger = logging.getLogger("reqman.providers")

GITLAB = "gitlab"
GITHUB = "github"
PROVIDERS = (GITLAB, GITHUB)
PROVIDER_LABELS = {GITLAB: "GitLab", GITHUB: "GitHub"}
DEFAULT_URLS = {GITLAB: "https://gitlab.com", GITHUB: "https://github.com"}

# Every provider API request gives up after this many seconds.
HTTP_TIMEOUT_SECONDS = 20.0
# Largest provider API response read (repository lists are paged well below it).
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# The keychain "service" under which tokens are stored (account: the connection id).
TOKEN_SERVICE = "ReqManTool provider token"
MAX_TOKEN_LENGTH = 1024
MAX_NAME_LENGTH = 100
MAX_SEARCH_LENGTH = 100
REPOSITORY_PAGE_SIZE = 50
# GitHub has no server-side name filter for /user/repos: at most this many pages are scanned.
GITHUB_SEARCH_PAGES = 5

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_CONNECTION_ID_RE = re.compile(r"[^a-z0-9]+")
_REPOSITORY_PATH_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.\-]*(/[A-Za-z0-9_.][A-Za-z0-9_.\-]*)+$")
_SCP_RE = re.compile(r"^(?:[^@/:\s]+@)?([^@/:\s]{2,}):(.+)$")


class ProviderError(ValueError):
    """A provider API call failed; the message carries the provider's own error verbatim.

    ``status`` is the HTTP status of an error response (``None``: no
    response, e.g. unreachable or a timeout).
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# --- connections -----------------------------------------------------------------


@dataclass
class ProviderConnection:
    """A GitLab/GitHub connection (metadata only; the token is in the ``TokenStore``)."""

    id: str
    provider: str
    name: str
    # The web URL of the instance, no trailing slash: https://gitlab.com, https://ghe.example.com.
    url: str
    # The token owner's user name, read from the API when the token was set.
    username: str = ""
    created: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderConnection":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        return cls(
            id=str(values.get("id", "")),
            provider=str(values.get("provider", "")),
            name=str(values.get("name", "")),
            url=str(values.get("url", "")),
            username=str(values.get("username", "") or ""),
            created=str(values.get("created", "") or ""),
        )

    @property
    def api_url(self) -> str:
        if self.provider == GITLAB:
            return f"{self.url}/api/v4"
        parsed = urllib.parse.urlsplit(self.url)
        if (parsed.hostname or "").lower() == "github.com":
            return "https://api.github.com"
        return f"{self.url}/api/v3"  # GitHub Enterprise Server


class ConnectionStore:
    """The connections file (``path``), or connections in memory when ``path`` is ``None``.

    Written with two-space indentation and a trailing newline, its
    ``format_version`` first (``CONNECTIONS_FORMAT``); a file written by a
    newer ReqManTool is refused (``NewerFormatError``) and never overwritten.
    """

    def __init__(self, path: Path | None):
        self.path = path
        self._memory: list[ProviderConnection] = []

    def _what(self) -> str:
        return f"Provider connections file '{self.path}'"

    def load(self) -> list[ProviderConnection]:
        if self.path is None:
            return list(self._memory)
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{self._what()} cannot be read: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{self._what()} is not a JSON object")
        data = upgrade(data, CONNECTIONS_FORMAT, self._what())
        items = data.get("connections")
        if not isinstance(items, list):
            return []
        return [ProviderConnection.from_dict(item) for item in items if isinstance(item, dict)]

    def save(self, connections: list[ProviderConnection]) -> None:
        if self.path is None:
            self._memory = list(connections)
            return
        if self.path.exists():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                existing = None
            if isinstance(existing, dict):
                check_format_version(existing, CONNECTIONS_FORMAT, self._what())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = with_format_version({"connections": [c.to_dict() for c in connections]}, CONNECTIONS_FORMAT)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


# --- tokens ---------------------------------------------------------------------------


class TokenStore(Protocol):
    """Where connection tokens are kept. Server edition: a per-user OAuth token source."""

    def get(self, connection_id: str) -> str | None: ...

    def set(self, connection_id: str, token: str) -> None: ...

    def delete(self, connection_id: str) -> None: ...


class MemoryTokenStore:
    """Tokens in process memory only (tests, scratch servers: ``REQMAN_TOKEN_STORE=memory``)."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def get(self, connection_id: str) -> str | None:
        return self._tokens.get(connection_id)

    def set(self, connection_id: str, token: str) -> None:
        self._tokens[connection_id] = token

    def delete(self, connection_id: str) -> None:
        self._tokens.pop(connection_id, None)


class KeyringTokenStore:
    """Tokens in the OS keychain (macOS Keychain, Windows Credential Locker, Secret Service)."""

    def __init__(self, service: str = TOKEN_SERVICE):
        self.service = service

    @staticmethod
    def _keyring() -> Any:
        try:
            import keyring  # noqa: PLC0415 - optional at import time; needed only with connections
        except ImportError as exc:
            raise ValueError(
                "The OS keychain cannot be used: the 'keyring' package is not installed "
                "(pip install -r backend/requirements.txt)"
            ) from exc
        return keyring

    def get(self, connection_id: str) -> str | None:
        keyring = self._keyring()
        try:
            return keyring.get_password(self.service, connection_id)
        except Exception as exc:  # noqa: BLE001 - keyring backends raise their own types
            raise ValueError(f"The OS keychain could not be read: {exc}") from exc

    def set(self, connection_id: str, token: str) -> None:
        keyring = self._keyring()
        try:
            keyring.set_password(self.service, connection_id, token)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"The token could not be stored in the OS keychain: {exc}") from exc

    def delete(self, connection_id: str) -> None:
        keyring = self._keyring()
        try:
            keyring.delete_password(self.service, connection_id)
        except Exception as exc:  # noqa: BLE001 - a missing entry is fine
            errors = getattr(keyring, "errors", None)
            if errors is not None and isinstance(exc, errors.PasswordDeleteError):
                return
            raise ValueError(f"The token could not be removed from the OS keychain: {exc}") from exc


def token_store_from_env(environ: dict[str, str] | None = None) -> TokenStore:
    env = os.environ if environ is None else environ
    if env.get("REQMAN_TOKEN_STORE", "").strip().lower() == "memory":
        return MemoryTokenStore()
    return KeyringTokenStore()


# --- HTTP ----------------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the token header would go along to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass
class HttpResponse:
    status: int
    data: Any
    headers: dict[str, str]


class ProviderHttp:
    """JSON over HTTPS with a timeout on every request (stdlib ``urllib``)."""

    def __init__(self, timeout: float = HTTP_TIMEOUT_SECONDS):
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect())

    def request(
        self,
        label: str,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Any = None,
    ) -> HttpResponse:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        for key, value in headers.items():
            request.add_header(key, value)
        request.add_header("Accept", headers.get("Accept", "application/json"))
        request.add_header("User-Agent", "ReqManTool")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        # Messages name the URL without its query (search terms stay out of errors).
        where = url.split("?", 1)[0]
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
                response_headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            try:
                raw_error = exc.read(MAX_RESPONSE_BYTES)
            except Exception:  # noqa: BLE001
                raw_error = b""
            if 300 <= exc.code < 400:
                location = exc.headers.get("Location") if exc.headers else None
                raise ProviderError(
                    f"{label} API redirected {method} {where} to {location or 'another address'} "
                    f"(HTTP {exc.code}); check the connection URL"
                ) from exc
            raise ProviderError(
                f"{label} API error {exc.code}: {_error_text(raw_error, exc.reason)}", status=exc.code
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderError(f"{label} did not answer within {self.timeout:g} s ({where})") from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise ProviderError(f"{label} did not answer within {self.timeout:g} s ({where})") from exc
            raise ProviderError(f"{label} could not be reached ({where}): {reason}") from exc
        except OSError as exc:
            raise ProviderError(f"{label} could not be reached ({where}): {exc}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError(f"{label} answered {where} with more than {MAX_RESPONSE_BYTES} bytes")
        if not raw.strip():
            return HttpResponse(status, None, response_headers)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProviderError(f"{label} answered {where} with something that is not JSON") from exc
        return HttpResponse(status, parsed, response_headers)


def _error_text(raw: bytes, reason: Any) -> str:
    """The provider's own error message from an error response body, verbatim."""
    try:
        data = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        parts: list[str] = []
        for key in ("message", "error", "error_description"):
            value = data.get(key)
            if value:
                parts.append(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            parts.append(json.dumps(errors, ensure_ascii=False))
        if parts:
            return " ".join(parts)
    text = raw.decode("utf-8", "replace").strip() if raw else ""
    if text:
        return text[:1000]
    return str(reason or "no details")


# --- providers --------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class Provider:
    """One connection's API with its token. Subclasses implement GitLab REST v4 and GitHub REST."""

    kind = ""

    def __init__(self, connection: ProviderConnection, token: str, http: ProviderHttp, oauth: bool = False):
        self.connection = connection
        self._token = token
        self.http = http
        # An OAuth access token (server edition login) instead of a personal access token.
        self.oauth = oauth

    @property
    def label(self) -> str:
        return PROVIDER_LABELS[self.kind]

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _call(self, method: str, path: str, body: Any = None, query: dict[str, Any] | None = None) -> HttpResponse:
        url = self.connection.api_url + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        return self.http.request(self.label, method, url, self._headers(), body)

    def _json(self, method: str, path: str, body: Any = None, query: dict[str, Any] | None = None) -> Any:
        return self._call(method, path, body, query).data

    # The operations every provider offers.
    def current_user(self) -> dict[str, str]:
        raise NotImplementedError

    def namespaces(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def create_repository(self, namespace_id: str, name: str, private: bool, description: str) -> dict[str, Any]:
        raise NotImplementedError

    def repositories(self, search: str | None, page: int) -> dict[str, Any]:
        raise NotImplementedError

    def repository(self, full_path: str) -> dict[str, Any]:
        raise NotImplementedError

    def find_open_change_request(self, full_path: str, source_branch: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def create_change_request(
        self, full_path: str, source_branch: str, target_branch: str, title: str, description: str
    ) -> dict[str, Any]:
        raise NotImplementedError

    def change_request(self, full_path: str, number: int) -> dict[str, Any]:
        raise NotImplementedError

    def repository_permission(self, full_path: str, user_id: str | None = None) -> str | None:
        """The token owner's permission on a repository, as the provider names it, or ``None``.

        GitLab: ``Guest``, ``Planner``, ``Reporter``, ``Developer``,
        ``Maintainer``, ``Owner`` (the highest of the project and inherited
        group memberships; ``user_id`` is the GitLab user id, needed for
        inherited memberships), ``Read`` for a visible (public/internal)
        project without membership. GitHub: ``Read``, ``Triage``, ``Write``,
        ``Maintain``, ``Admin``. ``None``: the repository does not exist for
        the token's owner (no access). Other failures raise ``ProviderError``.
        """
        raise NotImplementedError


# GitLab access levels (``access_level``) and their names, lowest first.
GITLAB_ACCESS_LEVELS = {10: "Guest", 15: "Planner", 20: "Reporter", 30: "Developer", 40: "Maintainer", 50: "Owner"}
# GitHub repository permissions (``permissions`` of ``GET /repos/{owner}/{repo}``), highest first.
GITHUB_PERMISSIONS = (("admin", "Admin"), ("maintain", "Maintain"), ("push", "Write"), ("triage", "Triage"), ("pull", "Read"))


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-.") or "project"


class GitLabProvider(Provider):
    kind = GITLAB

    def _headers(self) -> dict[str, str]:
        if self.oauth:
            return {"Authorization": f"Bearer {self._token}"}
        return {"PRIVATE-TOKEN": self._token}

    def repository_permission(self, full_path: str, user_id: str | None = None) -> str | None:
        try:
            project = self._json("GET", f"/projects/{self._project_ref(full_path)}")
        except ProviderError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(project, dict):
            raise ProviderError(f"GitLab answered /projects/{full_path} with something that is not a project")
        levels: list[int] = []
        permissions = project.get("permissions") if isinstance(project.get("permissions"), dict) else {}
        for key in ("project_access", "group_access"):
            entry = permissions.get(key)
            if isinstance(entry, dict) and isinstance(entry.get("access_level"), int):
                levels.append(entry["access_level"])
        if user_id and str(user_id).isdigit() and project.get("id") is not None:
            # Includes memberships inherited from parent groups (``permissions`` may not).
            try:
                member = self._json(
                    "GET", f"/projects/{urllib.parse.quote(str(project['id']), safe='')}/members/all/{user_id}"
                )
            except ProviderError as exc:
                if exc.status != 404:
                    raise
            else:
                if isinstance(member, dict) and isinstance(member.get("access_level"), int):
                    if member.get("state") in (None, "active"):
                        levels.append(member["access_level"])
        known = [level for level in levels if level >= min(GITLAB_ACCESS_LEVELS)]
        if known:
            # An unknown level (a future one) counts as the highest named level below it.
            best = max(level for level in GITLAB_ACCESS_LEVELS if level <= max(known))
            return GITLAB_ACCESS_LEVELS[best]
        # Visible (public or internal) without a membership: the code can be read.
        return "Read"

    @staticmethod
    def _project_ref(full_path: str) -> str:
        return urllib.parse.quote(full_path, safe="")

    @staticmethod
    def _repository(data: dict[str, Any]) -> dict[str, Any]:
        visibility = data.get("visibility")
        return {
            "id": str(data.get("id", "")),
            "full_path": str(data.get("path_with_namespace", "")),
            "name": str(data.get("name", "")),
            "description": data.get("description") or "",
            "private": visibility != "public",
            "visibility": visibility,
            "web_url": data.get("web_url"),
            "clone_url": data.get("http_url_to_repo"),
            "ssh_url": data.get("ssh_url_to_repo"),
            "default_branch": data.get("default_branch"),
            "updated": _iso(data.get("last_activity_at")),
        }

    def current_user(self) -> dict[str, str]:
        data = self._json("GET", "/user")
        if not isinstance(data, dict) or not data.get("username"):
            raise ProviderError("GitLab answered /user without a user name")
        return {"username": str(data["username"]), "name": str(data.get("name") or data["username"])}

    def namespaces(self) -> list[dict[str, Any]]:
        data = self._json("GET", "/namespaces", query={"per_page": 100})
        result = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict):
                result.append(
                    {
                        "id": str(item.get("id")),
                        "path": str(item.get("full_path") or item.get("path") or ""),
                        "name": str(item.get("name") or item.get("full_path") or ""),
                        "kind": "user" if item.get("kind") == "user" else "group",
                    }
                )
        result.sort(key=lambda ns: (ns["kind"] != "user", ns["path"].lower()))
        return result

    def create_repository(self, namespace_id: str, name: str, private: bool, description: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "path": _slug(name),
            "visibility": "private" if private else "public",
            "description": description,
            "initialize_with_readme": False,
        }
        if namespace_id:
            if not namespace_id.isdigit():
                raise ValueError(f"Invalid GitLab namespace id {namespace_id!r}")
            body["namespace_id"] = int(namespace_id)
        return self._repository(self._json("POST", "/projects", body))

    def repositories(self, search: str | None, page: int) -> dict[str, Any]:
        response = self._call(
            "GET",
            "/projects",
            query={
                "membership": "true",
                "simple": "true",
                "order_by": "last_activity_at",
                "per_page": REPOSITORY_PAGE_SIZE,
                "page": page,
                "search": search or None,
            },
        )
        items = response.data if isinstance(response.data, list) else []
        next_page = response.headers.get("x-next-page", "")
        return {
            "repositories": [self._repository(item) for item in items if isinstance(item, dict)],
            "page": page,
            "has_more": bool(next_page.strip()) if next_page else len(items) >= REPOSITORY_PAGE_SIZE,
        }

    def repository(self, full_path: str) -> dict[str, Any]:
        return self._repository(self._json("GET", f"/projects/{self._project_ref(full_path)}"))

    def _merge_request(self, full_path: str, data: dict[str, Any], with_approvals: bool) -> dict[str, Any]:
        state = data.get("state")
        result: dict[str, Any] = {
            "number": data.get("iid"),
            "title": data.get("title") or "",
            "url": data.get("web_url"),
            "state": "open" if state in ("opened", "locked") else state,
            "draft": bool(data.get("draft") or data.get("work_in_progress")),
            "source_branch": data.get("source_branch"),
            "target_branch": data.get("target_branch"),
            "author": (data.get("author") or {}).get("username"),
            "merge_status": data.get("detailed_merge_status") or data.get("merge_status"),
            "created": _iso(data.get("created_at")),
            "updated": _iso(data.get("updated_at")),
            "approvals": None,
            "approvals_error": None,
        }
        if with_approvals and isinstance(result["number"], int):
            try:
                approvals = self._json(
                    "GET",
                    f"/projects/{self._project_ref(full_path)}/merge_requests/{result['number']}/approvals",
                )
            except ProviderError as exc:
                result["approvals_error"] = str(exc)
            else:
                if isinstance(approvals, dict):
                    result["approvals"] = {
                        "approved_by": [
                            str((entry.get("user") or {}).get("username") or "")
                            for entry in approvals.get("approved_by") or []
                            if isinstance(entry, dict)
                        ],
                        "changes_requested_by": [],
                        "required": approvals.get("approvals_required"),
                        "left": approvals.get("approvals_left"),
                        "approved": bool(approvals.get("approved")),
                    }
        return result

    def find_open_change_request(self, full_path: str, source_branch: str) -> dict[str, Any] | None:
        data = self._json(
            "GET",
            f"/projects/{self._project_ref(full_path)}/merge_requests",
            query={"state": "opened", "source_branch": source_branch},
        )
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict):
                return self._merge_request(full_path, item, with_approvals=False)
        return None

    def create_change_request(
        self, full_path: str, source_branch: str, target_branch: str, title: str, description: str
    ) -> dict[str, Any]:
        data = self._json(
            "POST",
            f"/projects/{self._project_ref(full_path)}/merge_requests",
            {
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
                "remove_source_branch": True,
            },
        )
        return self._merge_request(full_path, data if isinstance(data, dict) else {}, with_approvals=False)

    def change_request(self, full_path: str, number: int) -> dict[str, Any]:
        data = self._json("GET", f"/projects/{self._project_ref(full_path)}/merge_requests/{number}")
        return self._merge_request(full_path, data if isinstance(data, dict) else {}, with_approvals=True)


class GitHubProvider(Provider):
    kind = GITHUB

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def _repo_ref(full_path: str) -> str:
        owner, _, name = full_path.partition("/")
        return f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"

    @staticmethod
    def _repository(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(data.get("id", "")),
            "full_path": str(data.get("full_name", "")),
            "name": str(data.get("name", "")),
            "description": data.get("description") or "",
            "private": bool(data.get("private")),
            "visibility": data.get("visibility") or ("private" if data.get("private") else "public"),
            "web_url": data.get("html_url"),
            "clone_url": data.get("clone_url"),
            "ssh_url": data.get("ssh_url"),
            "default_branch": data.get("default_branch"),
            "updated": _iso(data.get("pushed_at") or data.get("updated_at")),
        }

    def current_user(self) -> dict[str, str]:
        data = self._json("GET", "/user")
        if not isinstance(data, dict) or not data.get("login"):
            raise ProviderError("GitHub answered /user without a login")
        return {"username": str(data["login"]), "name": str(data.get("name") or data["login"])}

    def namespaces(self) -> list[dict[str, Any]]:
        user = self.current_user()
        result = [{"id": user["username"], "path": user["username"], "name": user["name"], "kind": "user"}]
        orgs = self._json("GET", "/user/orgs", query={"per_page": 100})
        for item in orgs if isinstance(orgs, list) else []:
            if isinstance(item, dict) and item.get("login"):
                login = str(item["login"])
                result.append({"id": login, "path": login, "name": login, "kind": "group"})
        result[1:] = sorted(result[1:], key=lambda ns: ns["path"].lower())
        return result

    def create_repository(self, namespace_id: str, name: str, private: bool, description: str) -> dict[str, Any]:
        body = {"name": _slug(name), "private": private, "description": description, "auto_init": False}
        user = self.connection.username or self.current_user()["username"]
        if not namespace_id or namespace_id == user:
            data = self._json("POST", "/user/repos", body)
        else:
            data = self._json("POST", f"/orgs/{urllib.parse.quote(namespace_id, safe='')}/repos", body)
        return self._repository(data if isinstance(data, dict) else {})

    def _repo_page(self, page: int, per_page: int) -> list[dict[str, Any]]:
        data = self._json(
            "GET",
            "/user/repos",
            query={
                "per_page": per_page,
                "page": page,
                "sort": "updated",
                "affiliation": "owner,collaborator,organization_member",
            },
        )
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def repositories(self, search: str | None, page: int) -> dict[str, Any]:
        if not search:
            items = self._repo_page(page, REPOSITORY_PAGE_SIZE)
            return {
                "repositories": [self._repository(item) for item in items],
                "page": page,
                "has_more": len(items) >= REPOSITORY_PAGE_SIZE,
            }
        # /user/repos has no name filter: scan the most recently updated
        # repositories and filter by name (one result page).
        needle = search.lower()
        matches: list[dict[str, Any]] = []
        scanned_all = False
        for scan in range(1, GITHUB_SEARCH_PAGES + 1):
            items = self._repo_page(scan, 100)
            matches += [item for item in items if needle in str(item.get("full_name", "")).lower()]
            if len(items) < 100:
                scanned_all = True
                break
        start = (page - 1) * REPOSITORY_PAGE_SIZE
        chunk = matches[start : start + REPOSITORY_PAGE_SIZE]
        return {
            "repositories": [self._repository(item) for item in chunk],
            "page": page,
            "has_more": len(matches) > start + REPOSITORY_PAGE_SIZE,
            "note": None
            if scanned_all
            else f"Only your {GITHUB_SEARCH_PAGES * 100} most recently updated repositories were searched",
        }

    def repository(self, full_path: str) -> dict[str, Any]:
        data = self._json("GET", f"/repos/{self._repo_ref(full_path)}")
        return self._repository(data if isinstance(data, dict) else {})

    def repository_permission(self, full_path: str, user_id: str | None = None) -> str | None:
        try:
            data = self._json("GET", f"/repos/{self._repo_ref(full_path)}")
        except ProviderError as exc:
            if exc.status == 404:
                return None
            raise
        permissions = data.get("permissions") if isinstance(data, dict) else None
        if not isinstance(permissions, dict):
            # Public repository read with a token that may not see its permissions.
            return "Read" if isinstance(data, dict) and not data.get("private") else None
        for key, name in GITHUB_PERMISSIONS:
            if permissions.get(key) is True:
                return name
        return None

    def _pull_request(self, full_path: str, data: dict[str, Any], with_reviews: bool) -> dict[str, Any]:
        state = data.get("state")
        if state == "closed" and (data.get("merged") or data.get("merged_at")):
            state = "merged"
        result: dict[str, Any] = {
            "number": data.get("number"),
            "title": data.get("title") or "",
            "url": data.get("html_url"),
            "state": state,
            "draft": bool(data.get("draft")),
            "source_branch": (data.get("head") or {}).get("ref"),
            "target_branch": (data.get("base") or {}).get("ref"),
            "author": (data.get("user") or {}).get("login"),
            "merge_status": data.get("mergeable_state"),
            "created": _iso(data.get("created_at")),
            "updated": _iso(data.get("updated_at")),
            "approvals": None,
            "approvals_error": None,
        }
        if with_reviews and isinstance(result["number"], int):
            try:
                reviews = self._json(
                    "GET", f"/repos/{self._repo_ref(full_path)}/pulls/{result['number']}/reviews", query={"per_page": 100}
                )
            except ProviderError as exc:
                result["approvals_error"] = str(exc)
            else:
                latest: dict[str, str] = {}
                for review in reviews if isinstance(reviews, list) else []:
                    if not isinstance(review, dict):
                        continue
                    login = (review.get("user") or {}).get("login")
                    review_state = review.get("state")
                    if login and review_state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                        latest[str(login)] = str(review_state)
                approved = sorted(user for user, s in latest.items() if s == "APPROVED")
                result["approvals"] = {
                    "approved_by": approved,
                    "changes_requested_by": sorted(u for u, s in latest.items() if s == "CHANGES_REQUESTED"),
                    "required": None,
                    "left": None,
                    "approved": bool(approved) and not any(s == "CHANGES_REQUESTED" for s in latest.values()),
                }
        return result

    def find_open_change_request(self, full_path: str, source_branch: str) -> dict[str, Any] | None:
        owner = full_path.partition("/")[0]
        data = self._json(
            "GET",
            f"/repos/{self._repo_ref(full_path)}/pulls",
            query={"state": "open", "head": f"{owner}:{source_branch}"},
        )
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict):
                return self._pull_request(full_path, item, with_reviews=False)
        return None

    def create_change_request(
        self, full_path: str, source_branch: str, target_branch: str, title: str, description: str
    ) -> dict[str, Any]:
        data = self._json(
            "POST",
            f"/repos/{self._repo_ref(full_path)}/pulls",
            {"title": title, "head": source_branch, "base": target_branch, "body": description},
        )
        return self._pull_request(full_path, data if isinstance(data, dict) else {}, with_reviews=False)

    def change_request(self, full_path: str, number: int) -> dict[str, Any]:
        data = self._json("GET", f"/repos/{self._repo_ref(full_path)}/pulls/{number}")
        return self._pull_request(full_path, data if isinstance(data, dict) else {}, with_reviews=True)


_PROVIDER_CLASSES: dict[str, type[Provider]] = {GITLAB: GitLabProvider, GITHUB: GitHubProvider}


# --- remote URLs -------------------------------------------------------------------------


def parse_remote(url: str) -> tuple[str, str, int | None, str] | None:
    """``(scheme, host, port, path)`` of an https/ssh/scp-like remote URL, else ``None``."""
    url = (url or "").strip()
    if "://" in url:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("https", "http", "ssh", "git+ssh", "ssh+git") or not parsed.hostname:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        return parsed.scheme, parsed.hostname.lower(), port, urllib.parse.unquote(parsed.path)
    match = _SCP_RE.match(url)
    if match:
        return "ssh", match.group(1).lower(), None, "/" + match.group(2).lstrip("/")
    return None


def normalize_connection_url(provider: str, url: str | None) -> str:
    """The connection's web URL without trailing slash; ``ValueError`` explains what is accepted."""
    text = (url or "").strip() or DEFAULT_URLS[provider]
    parsed = urllib.parse.urlsplit(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("https", "http") or not host:
        raise ValueError(f"Invalid {PROVIDER_LABELS[provider]} URL '{text}': expected https://host[/path]")
    if parsed.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"Invalid {PROVIDER_LABELS[provider]} URL '{text}': use https:// (the token must not travel unencrypted)"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            f"Invalid {PROVIDER_LABELS[provider]} URL '{text}': no user name, query or fragment allowed"
        )
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid {PROVIDER_LABELS[provider]} URL '{text}': {exc}") from exc
    path = parsed.path.rstrip("/")
    if provider == GITLAB and path.endswith("/api/v4"):
        path = path[: -len("/api/v4")]
    if provider == GITHUB:
        if host == "api.github.com":
            host, path = "github.com", ""
        elif path.endswith("/api/v3"):
            path = path[: -len("/api/v3")]
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    if ":" in host:
        netloc = f"[{host}]" if parsed.port is None else f"[{host}]:{parsed.port}"
    return f"{parsed.scheme}://{netloc}{path}"


# --- the service ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ProviderService:
    """Connections, tokens and provider access; called by ``ProjectService`` (never by routes)."""

    def __init__(
        self,
        connections: ConnectionStore | None = None,
        tokens: TokenStore | None = None,
        http: ProviderHttp | None = None,
    ):
        self.connections = connections if connections is not None else ConnectionStore(None)
        self.tokens: TokenStore = tokens if tokens is not None else MemoryTokenStore()
        self.http = http if http is not None else ProviderHttp()
        self._lock = threading.Lock()
        # Keychain reads are cached per process (auto-push asks on every change).
        self._token_cache: dict[str, str | None] = {}

    @classmethod
    def from_env(cls, connections_path: Path) -> "ProviderService":
        return cls(ConnectionStore(connections_path), token_store_from_env())

    # --- connections ---

    def _find(self, connections: list[ProviderConnection], connection_id: str) -> ProviderConnection:
        for connection in connections:
            if connection.id == connection_id:
                return connection
        raise FileNotFoundError(f"Provider connection '{connection_id}' not found")

    def get_connection(self, connection_id: str) -> ProviderConnection:
        return self._find(self.connections.load(), connection_id)

    def token(self, connection_id: str) -> str | None:
        """The connection's token (local edition: from the OS keychain). Never sent to a client."""
        if connection_id not in self._token_cache:
            self._token_cache[connection_id] = self.tokens.get(connection_id)
        return self._token_cache[connection_id]

    def _public(self, connection: ProviderConnection) -> dict[str, Any]:
        try:
            has_token: bool | None = bool(self.token(connection.id))
            token_error = None
        except ValueError as exc:
            has_token, token_error = None, str(exc)
        return {
            **connection.to_dict(),
            "provider_label": PROVIDER_LABELS.get(connection.provider, connection.provider),
            "has_token": has_token,
            "token_error": token_error,
        }

    def list_connections(self) -> list[dict[str, Any]]:
        return [self._public(c) for c in self.connections.load()]

    @staticmethod
    def _checked_token(token: Any) -> str:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("The access token must not be empty")
        token = token.strip()
        if len(token) > MAX_TOKEN_LENGTH or any(ord(ch) < 33 or ord(ch) == 127 for ch in token):
            raise ValueError("The access token is not valid: it must be one word of printable characters")
        return token

    @staticmethod
    def _checked_name(name: Any, fallback: str) -> str:
        if name is None or (isinstance(name, str) and not name.strip()):
            return fallback
        if not isinstance(name, str):
            raise ValueError("The connection name must be a string")
        name = name.strip()
        if len(name) > MAX_NAME_LENGTH or any(ord(ch) < 32 for ch in name):
            raise ValueError(f"The connection name must be one line of at most {MAX_NAME_LENGTH} characters")
        return name

    def _verify(self, connection: ProviderConnection, token: str) -> dict[str, str]:
        provider = _PROVIDER_CLASSES[connection.provider](connection, token, self.http)
        return provider.current_user()

    def add_connection(self, provider: Any, url: Any, name: Any, token: Any) -> dict[str, Any]:
        """Add a connection; the token is checked against the API (``GET /user``) first."""
        if provider not in PROVIDERS:
            raise ValueError(f"Invalid provider {provider!r}: expected one of {', '.join(PROVIDERS)}")
        if url is not None and not isinstance(url, str):
            raise ValueError("The URL must be a string")
        clean_url = normalize_connection_url(provider, url)
        token = self._checked_token(token)
        host = urllib.parse.urlsplit(clean_url).hostname or provider
        draft = ProviderConnection(
            id="",
            provider=provider,
            name=self._checked_name(name, f"{PROVIDER_LABELS[provider]} ({host})"),
            url=clean_url,
        )
        user = self._verify(draft, token)
        with self._lock:
            connections = self.connections.load()
            for existing in connections:
                if existing.provider == provider and existing.url == clean_url and existing.username == user["username"]:
                    raise ValueError(
                        f"A connection to {clean_url} as '{user['username']}' already exists ('{existing.name}'); "
                        "update its token instead"
                    )
            base = _CONNECTION_ID_RE.sub("-", f"{provider}-{host}-{user['username']}".lower()).strip("-")
            taken = {c.id for c in connections}
            connection_id, n = base, 2
            while connection_id in taken:
                connection_id, n = f"{base}-{n}", n + 1
            draft.id = connection_id
            draft.username = user["username"]
            draft.created = _now()
            self.tokens.set(connection_id, token)
            self._token_cache.pop(connection_id, None)
            try:
                self.connections.save([*connections, draft])
            except Exception:
                self.tokens.delete(connection_id)
                self._token_cache.pop(connection_id, None)
                raise
        return self._public(draft)

    def update_connection(self, connection_id: str, name: Any = None, token: Any = None) -> dict[str, Any]:
        """Rename a connection and/or replace its token (a new token is checked first)."""
        with self._lock:
            connections = self.connections.load()
            connection = self._find(connections, connection_id)
            new_name = self._checked_name(name, connection.name)
            username = connection.username
            if token is not None and not (isinstance(token, str) and not token.strip()):
                clean = self._checked_token(token)
                user = self._verify(connection, clean)
                if connection.username and user["username"] != connection.username:
                    raise ValueError(
                        f"The token belongs to '{user['username']}', not '{connection.username}'; "
                        "add a new connection for another account"
                    )
                username = user["username"]
                self.tokens.set(connection_id, clean)
                self._token_cache.pop(connection_id, None)
            connection.name = new_name
            connection.username = username
            self.connections.save(connections)
        return self._public(connection)

    def remove_connection(self, connection_id: str) -> dict[str, Any]:
        with self._lock:
            connections = self.connections.load()
            connection = self._find(connections, connection_id)
            self.tokens.delete(connection_id)
            self._token_cache.pop(connection_id, None)
            self.connections.save([c for c in connections if c.id != connection_id])
        return {"id": connection.id, "removed": True}

    # --- providers ---

    def provider(self, connection_id: str) -> Provider:
        connection = self.get_connection(connection_id)
        if connection.provider not in _PROVIDER_CLASSES:
            raise ValueError(f"Connection '{connection.name}' has an unknown provider '{connection.provider}'")
        token = self.token(connection_id)
        if not token:
            raise ValueError(
                f"No access token is stored for the connection '{connection.name}' (OS keychain); "
                "set it again under GitLab / GitHub connections"
            )
        return _PROVIDER_CLASSES[connection.provider](connection, token, self.http)

    def connection_for_remote(self, remote_url: str) -> tuple[ProviderConnection, str] | None:
        """The connection a remote URL belongs to and the repository path in it, or ``None``.

        https remotes match scheme, host, port and the connection URL's path
        prefix; ssh remotes match the host. Read-only and never raises for
        a malformed URL.
        """
        parts = parse_remote(remote_url)
        if parts is None:
            return None
        scheme, host, port, path = parts
        try:
            connections = self.connections.load()
        except ValueError:
            return None
        for connection in connections:
            conn = urllib.parse.urlsplit(connection.url)
            conn_host = (conn.hostname or "").lower()
            conn_path = conn.path.rstrip("/")
            if scheme in ("https", "http"):
                if scheme != conn.scheme or host != conn_host or (port or None) != (conn.port or None):
                    continue
                if conn_path and not (path == conn_path or path.startswith(conn_path + "/")):
                    continue
                rest = path[len(conn_path) :]
            else:
                if host != conn_host:
                    continue
                rest = path[len(conn_path) :] if conn_path and path.startswith(conn_path + "/") else path
            repo = rest.strip("/")
            if repo.endswith(".git"):
                repo = repo[: -len(".git")]
            if _REPOSITORY_PATH_RE.match(repo):
                return connection, repo
        return None

    def git_auth_env(self, remote_url: str) -> dict[str, str] | None:
        """Environment for one git process reaching ``remote_url`` with the connection's token.

        Only for an https URL under a connection's URL (http only on a
        loopback host). Never raises: without a match or a token git runs
        as before (the user's own credential helper / ssh agent).
        """
        try:
            parts = parse_remote(remote_url)
            if parts is None or parts[0] not in ("https", "http"):
                return None
            match = self.connection_for_remote(remote_url)
            if match is None:
                return None
            connection = match[0]
            token = self.token(connection.id)
            if not token:
                return None
            user = "oauth2" if connection.provider == GITLAB else "x-access-token"
            credentials = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
            scope = connection.url + "/"
            return {
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": f"http.{scope}.extraHeader",
                "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credentials}",
                "GIT_CONFIG_KEY_1": f"http.{scope}.followRedirects",
                "GIT_CONFIG_VALUE_1": "false",
            }
        except Exception as exc:  # noqa: BLE001 - git falls back to the user's own credentials
            logger.warning("No provider credentials for a git remote: %s", type(exc).__name__)
            return None

    @staticmethod
    def checked_search(search: Any) -> str | None:
        if search is None:
            return None
        if not isinstance(search, str):
            raise ValueError("The search text must be a string")
        search = search.strip()
        if len(search) > MAX_SEARCH_LENGTH or any(ord(ch) < 32 for ch in search):
            raise ValueError(f"The search text must be one line of at most {MAX_SEARCH_LENGTH} characters")
        return search or None

    @staticmethod
    def checked_page(page: Any) -> int:
        if page is None:
            return 1
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 1000:
            raise ValueError(f"Invalid page {page!r}: expected an integer from 1 to 1000")
        return page

    @staticmethod
    def checked_repository_path(full_path: Any) -> str:
        if not isinstance(full_path, str) or not _REPOSITORY_PATH_RE.match(full_path.strip()):
            raise ValueError(f"Invalid repository path {full_path!r}: expected 'group/name'")
        return full_path.strip()
