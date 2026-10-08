"""Project discovery: the ReqManTool projects in the logged-in user's GitHub / GitLab repositories.

A user logged in through a ``github`` or ``gitlab`` provider sees, without
any provider "connection", the repositories they can access that hold a
ReqManTool project, and opens one with a click. Everything runs server-side
with the OAuth access token of the user's login (``sessions.py``): the token
is never sent to the browser, logged or written anywhere (not into a git
config or a remote URL either).

Discovery (``DiscoveryService.scan``), per user, in a background thread:

- GitHub: ``GET /user/repos`` (owner, collaborator, organization member;
  paginated, newest first); a repository is a candidate when its
  ``topics`` include ``reqmantool``, or when ``project.json`` at the root of
  its ``current`` branch (the branch ReqManTool works on and clones) parses
  as a ReqManTool project (``GET /repos/{owner}/{repo}/contents/project.json``
  with the raw media type).
- GitLab: ``GET /projects?membership=true&topic=reqmantool`` plus ``GET
  /projects?membership=true`` scanned the same way (``GET
  /projects/:id/repository/files/project.json/raw?ref=current``).

At most ``server.discovery.max_repos`` repositories are looked at per scan
(``truncated`` tells when more were left out), to stay within the
providers' rate limits. The result is cached per user for
``server.discovery.cache_minutes``; ``refresh`` starts a new scan. Provider
errors are shown verbatim (``error`` for the whole scan, ``warnings`` for
single repositories). A repository tagged ``reqmantool`` whose
``project.json`` is missing or unreadable is still listed, with
``project_error``.

Opening (``DiscoveryService.open``): the repository is read again from the
provider API with the user's token (its clone URL never comes from the
browser); the user needs at least the permission that gives the ``viewer``
role (``repo_access.REPO_ROLES``: GitHub Read, GitLab Guest). The server
then clones it once into its data directory (shared by every user; later
opens fetch and fast-forward it) through the core
(``EditionContext.open_repository``), authenticating git with the user's
token for those git processes only (``reqman.git_repo.remote_auth_scope``).
Access to the opened project follows the repository permissions
(``repo_access.py``), so discovery needs ``server.repo_roles`` enabled; the
opener is not made a member.

Slice 1: GitHub OAuth Apps and GitLab. A GitHub OAuth App sees private
repositories only with the broad ``repo`` scope (``scopes`` of the
provider); GitHub App providers (fine-grained, per-installation) are a later
slice.

REST (session needed; ``POST`` needs the CSRF token):

- ``GET /discovery/projects``: the cached list (starts a scan when there is
  none or it is stale; ``status: "scanning"`` until it finished).
- ``POST /discovery/refresh``: start a new scan now; the list as above.
- ``POST /discovery/open`` ``{"provider": id, "repo": "owner/name"}``: open
  it; ``{"project_id", "cloned", "updated", "notes", ...}``. Broadcasts
  ``{"event": "changed", "project_id": ...}``.

WebSocket methods (``HOOKS.rpc_methods``, same rules and results):
``discovery.list``, ``discovery.refresh``, ``discovery.open``.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from reqman.extensions import AccessDeniedError, current_identity
from reqman.git_repo import local_remote_path
from reqman.providers import (
    GITHUB,
    GITLAB,
    PROJECT_TOPIC,
    GitHubProvider,
    GitLabProvider,
    Provider,
    ProviderConnection,
    ProviderError,
    ProviderHttp,
    ProviderService,
    git_auth_config,
    parse_remote,
)
from reqman.service import ConflictError

from .oidc import OidcError
from .permissions import Permissions
from .repo_access import NEEDED_PERMISSION, REPO_PROVIDER_TYPES, REPO_ROLES, TOKEN_EXPIRY_MARGIN, RepoAccess, match_remote
from .sessions import SessionStore
from .settings import ProviderSettings, ServerSettings
from .users import User

logger = logging.getLogger("reqman_server.discovery")

PAGE_SIZE = 100
# The branch ReqManTool works on (``reqman.git_repo.WORKING_BRANCH``): a clone checks it out.
PROJECT_BRANCH = "current"
PROJECT_FILE = "project.json"
GITHUB_RAW = "application/vnd.github.raw+json"
# Parallel project.json reads per scan (the providers throttle bursts).
SCAN_WORKERS = 4
# A failed scan is retried after this long when the list is asked for again.
ERROR_RETRY_SECONDS = 60.0
MAX_WARNINGS = 20
HTTP_TIMEOUT = 15.0


def looks_like_project(data: Any) -> bool:
    """Whether a parsed ``project.json`` is a ReqManTool project file."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not isinstance(data.get("name"), str):
        return False
    return "format_version" in data or ("attributes" in data and "id_counters" in data)


def _same_url(a: str, b: str) -> bool:
    def norm(url: str) -> str:
        url = url.strip().rstrip("/")
        return url[: -len(".git")] if url.endswith(".git") else url

    return bool(a and b) and norm(a) == norm(b)


@dataclass
class _Scan:
    status: str = "scanning"  # "scanning" | "idle"
    started_at: float = 0.0
    finished_at: float | None = None
    repositories: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    scanned: int = 0


class DiscoveryService:
    def __init__(
        self,
        settings: ServerSettings,
        sessions: SessionStore,
        permissions: Permissions,
        repo: RepoAccess,
        project_ids: Callable[[], list[str]] | None,
        project_remote_url: Callable[[str], str | None] | None,
        open_repository: Callable[..., dict[str, Any]] | None,
        http: ProviderHttp | None = None,
        clock: Callable[[], float] = time.time,
        start: Callable[[Callable[[], None]], None] | None = None,
        allow_local_remotes: bool = False,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.permissions = permissions
        self.repo = repo
        self.project_ids = project_ids
        self.project_remote_url = project_remote_url
        self.open_repository = open_repository
        self.http = http or ProviderHttp(timeout=HTTP_TIMEOUT)
        self.clock = clock
        # Runs a scan: a daemon thread by default (tests run it inline).
        self._start = start or (lambda fn: threading.Thread(target=fn, name="reqman-discovery", daemon=True).start())
        # Tests only: accept a local-folder clone URL (a bare repository standing in for the provider).
        self.allow_local_remotes = allow_local_remotes
        self._lock = threading.Lock()
        self._scans: dict[str, _Scan] = {}
        self._open_locks: dict[tuple[str, str], threading.Lock] = {}

    # --- who is asking ----------------------------------------------------------------

    def _user(self) -> User:
        user = self.permissions.active_user(current_identity())
        if user is None:
            raise AccessDeniedError("Not logged in")
        return user

    def _provider(self, user: User) -> tuple[ProviderSettings | None, str | None]:
        """The user's login provider when discovery works for them, else ``(None, why not)``."""
        if not self.settings.discovery:
            return None, "Project discovery is turned off on this server (server.discovery.enabled)."
        if self.open_repository is None:
            return None, "This server cannot open repositories."
        if not self.repo.enabled:
            return None, "Project discovery needs roles from repository permissions (server.repo_roles)."
        provider = self.settings.provider(user.provider)
        if provider is None or provider.type not in REPO_PROVIDER_TYPES:
            return None, "Log in with GitHub or GitLab to see the projects in your repositories."
        return provider, None

    # --- the user's token ---------------------------------------------------------------

    def _token(self, user: User, provider: ProviderSettings) -> tuple[str, str]:
        """``(session key, access token)`` of the user's login, renewed when it is about to expire."""
        found = self.sessions.oauth_session(user.id, provider.id)
        if found is None:
            raise ValueError(f"Your session holds no {provider.name} token; log out and log in again.")
        key, session = found
        token = session.access_token
        if session.token_expires and self.clock() > session.token_expires - TOKEN_EXPIRY_MARGIN and session.refresh_token:
            token = self._renew(provider, key, token)
        return key, token

    def _renew(self, provider: ProviderSettings, key: str, token: str) -> str:
        try:
            return self.repo.refresh_token(key, token)
        except OidcError as exc:
            raise ValueError(
                f"Your {provider.name} sign-in could not be renewed ({exc}); log out and log in again."
            ) from exc

    def _api(self, provider: ProviderSettings, token: str) -> Provider:
        connection = ProviderConnection(
            id=f"login-{provider.id}", provider=provider.type, name=provider.name, url=provider.issuer.rstrip("/")
        )
        cls = GitLabProvider if provider.type == GITLAB else GitHubProvider
        return cls(connection, token, self.http, oauth=True)

    def _with_api(self, user: User, provider: ProviderSettings, fn: Callable[[Provider], Any]) -> tuple[Any, str]:
        """``fn(api)`` with the user's token, once more with a renewed token after a 401; ``(result, token)``."""
        key, token = self._token(user, provider)
        try:
            return fn(self._api(provider, token)), token
        except ProviderError as exc:
            session = self.sessions.session_by_key(key)
            if exc.status != 401 or session is None or not session.refresh_token:
                raise
        token = self._renew(provider, key, token)
        return fn(self._api(provider, token)), token

    # --- the list -----------------------------------------------------------------------

    def list(self) -> dict[str, Any]:
        """The cached list of the user's ReqManTool repositories (a scan starts when it is missing or stale)."""
        user = self._user()
        provider, reason = self._provider(user)
        if provider is None:
            return {"enabled": False, "reason": reason}
        now = self.clock()
        with self._lock:
            scan = self._scans.get(user.id)
            stale = scan is None or (
                scan.status != "scanning"
                and scan.finished_at is not None
                and now - scan.finished_at >= (ERROR_RETRY_SECONDS if scan.error else self.settings.discovery_ttl_seconds)
            )
        if stale:
            self._begin(user, provider)
        return self._public(user, provider)

    def refresh(self) -> dict[str, Any]:
        """Start a new scan now (unless one is running); the list."""
        user = self._user()
        provider, reason = self._provider(user)
        if provider is None:
            return {"enabled": False, "reason": reason}
        self._begin(user, provider)
        return self._public(user, provider)

    def _begin(self, user: User, provider: ProviderSettings) -> None:
        with self._lock:
            previous = self._scans.get(user.id)
            if previous is not None and previous.status == "scanning":
                return
            scan = _Scan(status="scanning", started_at=self.clock())
            if previous is not None:
                scan.repositories = previous.repositories  # shown while the new scan runs
            self._scans[user.id] = scan
        self._start(lambda: self._run(user, provider, scan))

    def _run(self, user: User, provider: ProviderSettings, scan: _Scan) -> None:
        try:
            result = self.scan(user, provider)
        except (ProviderError, ValueError) as exc:
            result = _Scan(error=str(exc), repositories=scan.repositories)
        except Exception as exc:  # noqa: BLE001 - reported to the user; the thread must not die silently
            logger.exception("Project discovery for %s failed", user.id)
            result = _Scan(error=f"The scan failed: {type(exc).__name__}", repositories=scan.repositories)
        result.status = "idle"
        result.started_at = scan.started_at
        result.finished_at = self.clock()
        with self._lock:
            if self._scans.get(user.id) is scan:
                self._scans[user.id] = result
        logger.info(
            "Project discovery for %s on %s: %d repositories scanned, %d projects%s",
            user.id, provider.name, result.scanned, len(result.repositories),
            f" (error: {result.error})" if result.error else "",
        )

    def forget(self, user_id: str | None = None) -> None:
        with self._lock:
            if user_id is None:
                self._scans.clear()
            else:
                self._scans.pop(user_id, None)

    def _public(self, user: User, provider: ProviderSettings) -> dict[str, Any]:
        with self._lock:
            scan = self._scans.get(user.id) or _Scan(status="idle")
            # ``_id`` (the provider's repository id) is internal.
            repositories = [{k: v for k, v in r.items() if not k.startswith("_")} for r in scan.repositories]
            body = {
                "enabled": True,
                "provider": {"id": provider.id, "type": provider.type, "name": provider.name},
                "status": scan.status,
                "scanned_at": int(scan.finished_at) if scan.finished_at else None,
                "error": scan.error,
                "warnings": list(scan.warnings),
                "truncated": scan.truncated,
                "max_repos": self.settings.discovery_max_repos,
            }
        remotes = self._project_remotes()
        for entry in repositories:
            same = self._same_repository(provider, entry["full_name"], entry.get("clone_url") or "")
            opened = next((pid for pid, url in remotes if same(url)), None)
            entry["already_opened"] = opened is not None
            entry["opened_project_id"] = opened
        body["repositories"] = repositories
        return body

    def _project_remotes(self) -> list[tuple[str, str]]:
        if self.project_ids is None or self.project_remote_url is None:
            return []
        result = []
        for project_id in self.project_ids():
            try:
                url = self.project_remote_url(project_id)
            except Exception:  # noqa: BLE001 - an unreadable repository is simply not matched
                url = None
            if url:
                result.append((project_id, url))
        return result

    @staticmethod
    def _same_repository(provider: ProviderSettings, full_name: str, clone_url: str) -> Callable[[str], bool]:
        wanted = full_name.lower()

        def same(remote_url: str) -> bool:
            binding = match_remote(remote_url, provider)
            if binding is not None and binding.path.lower() == wanted:
                return True
            return _same_url(remote_url, clone_url)

        return same

    # --- the scan -----------------------------------------------------------------------

    def scan(self, user: User, provider: ProviderSettings) -> _Scan:
        """Look through the user's repositories now (``ProviderError`` / ``ValueError`` when listing fails)."""
        result = _Scan(status="idle")
        if provider.type == GITHUB:
            candidates = self._github_repositories(user, provider, result)
        else:
            candidates = self._gitlab_repositories(user, provider, result)
        result.scanned = len(candidates)
        _key, token = self._token(user, provider)
        api = self._api(provider, token)
        stop = threading.Event()

        def check(entry: dict[str, Any]) -> dict[str, Any] | None:
            if stop.is_set():
                return entry if entry["topic"] else None
            try:
                data = self._project_file(api, provider, entry)
            except ProviderError as exc:
                if exc.status == 404:
                    if entry["topic"]:
                        entry["project_error"] = (
                            f"No {PROJECT_FILE} on the '{PROJECT_BRANCH}' branch; it cannot be opened"
                        )
                        return entry
                    return None
                if exc.status in (401, 403, 429):
                    stop.set()  # rate limit or token refused: the remaining repositories are not read
                self._warn(result, f"{entry['full_name']}: {exc}")
                if entry["topic"]:
                    entry["project_error"] = str(exc)
                    return entry
                return None
            if looks_like_project(data):
                entry["project_id"] = data["id"]
                entry["project_name"] = data["name"]
                return entry
            if entry["topic"]:
                entry["project_error"] = f"{PROJECT_FILE} is not a ReqManTool project"
                return entry
            return None

        with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
            found = [e for e in pool.map(check, candidates) if e is not None]
        if stop.is_set():
            result.truncated = True
        result.repositories = sorted(found, key=lambda e: e["full_name"].lower())
        return result

    @staticmethod
    def _warn(result: _Scan, message: str) -> None:
        if len(result.warnings) < MAX_WARNINGS:
            result.warnings.append(message)

    def _entry(self, provider: ProviderSettings, repository: dict[str, Any], topic: bool) -> dict[str, Any]:
        return {
            "provider_id": provider.id,
            "provider_type": provider.type,
            "provider_name": provider.name,
            "full_name": repository["full_path"],
            "web_url": repository.get("web_url"),
            "clone_url": repository.get("clone_url"),
            "default_branch": repository.get("default_branch"),
            "private": bool(repository.get("private")),
            "topic": topic,
            "project_id": None,
            "project_name": None,
            "project_error": None,
            "_id": repository.get("id"),
        }

    def _github_repositories(self, user: User, provider: ProviderSettings, result: _Scan) -> list[dict[str, Any]]:
        limit = self.settings.discovery_max_repos
        found: list[dict[str, Any]] = []
        page = 1
        while True:
            response, _token = self._with_api(
                user,
                provider,
                lambda api, page=page: api.get(
                    "/user/repos",
                    {
                        "per_page": PAGE_SIZE,
                        "page": page,
                        "sort": "updated",
                        "affiliation": "owner,collaborator,organization_member",
                    },
                ),
            )
            items = [i for i in response.data if isinstance(i, dict)] if isinstance(response.data, list) else []
            for item in items:
                if len(found) >= limit:
                    result.truncated = True
                    return found
                repository = GitHubProvider._repository(item)
                if not repository["full_path"]:
                    continue
                topics = item.get("topics") if isinstance(item.get("topics"), list) else []
                found.append(self._entry(provider, repository, PROJECT_TOPIC in topics))
            if len(items) < PAGE_SIZE:
                return found
            page += 1

    def _gitlab_pages(
        self, user: User, provider: ProviderSettings, query: dict[str, Any], limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            response, _token = self._with_api(
                user,
                provider,
                lambda api, page=page: api.get("/projects", {**query, "per_page": PAGE_SIZE, "page": page}),
            )
            batch = [i for i in response.data if isinstance(i, dict)] if isinstance(response.data, list) else []
            for item in batch:
                if len(items) >= limit:
                    return items, True
                items.append(item)
            # GitLab names the next page (empty on the last one); without the header, a short page is the last.
            next_page = response.headers.get("x-next-page")
            if not batch or len(batch) < PAGE_SIZE or (next_page is not None and not next_page.strip()):
                return items, False
            page += 1

    def _gitlab_repositories(self, user: User, provider: ProviderSettings, result: _Scan) -> list[dict[str, Any]]:
        limit = self.settings.discovery_max_repos
        base = {"membership": "true", "simple": "true", "order_by": "last_activity_at"}
        tagged, more_tagged = self._gitlab_pages(user, provider, {**base, "topic": PROJECT_TOPIC}, limit)
        found: dict[str, dict[str, Any]] = {}
        for item in tagged:
            repository = GitLabProvider._repository(item)
            if repository["full_path"]:
                found[repository["full_path"]] = self._entry(provider, repository, True)
        room = limit - len(found)
        more = more_tagged
        if room > 0:
            others, more_others = self._gitlab_pages(user, provider, base, room)
            more = more or more_others
            for item in others:
                repository = GitLabProvider._repository(item)
                path = repository["full_path"]
                if path and path not in found:
                    topics = item.get("topics") if isinstance(item.get("topics"), list) else []
                    found[path] = self._entry(provider, repository, PROJECT_TOPIC in topics)
        else:
            more = True
        result.truncated = more
        return list(found.values())

    @staticmethod
    def _project_file(api: Provider, provider: ProviderSettings, entry: dict[str, Any]) -> Any:
        if provider.type == GITHUB:
            owner, _, name = entry["full_name"].partition("/")
            path = f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}/contents/{PROJECT_FILE}"
            return api.get(path, {"ref": PROJECT_BRANCH}, accept=GITHUB_RAW).data
        ref = urllib.parse.quote(str(entry.get("_id") or entry["full_name"]), safe="")
        return api.get(f"/projects/{ref}/repository/files/{PROJECT_FILE}/raw", {"ref": PROJECT_BRANCH}).data

    # --- opening ------------------------------------------------------------------------

    def _check_clone_url(self, url: Any, provider: ProviderSettings, full_name: str) -> str:
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"{provider.name} gave no HTTPS clone URL for '{full_name}'")
        url = url.strip()
        if self.allow_local_remotes and local_remote_path(url) is not None:
            return url
        parts = parse_remote(url)
        binding = match_remote(url, provider)
        if (
            parts is None
            or parts[0] not in ("https", "http")
            or binding is None
            or binding.path.lower() != full_name.lower()
        ):
            raise ValueError(
                f"{provider.name} gave the clone URL {url!r} for '{full_name}', which is not a repository "
                f"of {provider.issuer}; it is not cloned"
            )
        return url

    def open(self, provider_id: Any, repo: Any) -> dict[str, Any]:
        """Open a repository as a project: clone it once (shared), later fetch it. See the module docstring."""
        user = self._user()
        provider, reason = self._provider(user)
        if provider is None:
            raise ValueError(reason)
        if provider_id != provider.id:
            raise ValueError(
                f"You are logged in with {provider.name} ('{provider.id}'); repositories of '{provider_id}' "
                "cannot be opened with that login"
            )
        full_name = ProviderService.checked_repository_path(repo)
        if provider.type == GITHUB and full_name.count("/") != 1:
            raise ValueError(f"Invalid repository path {full_name!r}: expected 'owner/name'")

        def lookup(api: Provider) -> tuple[dict[str, Any] | None, str | None]:
            # The permission first: a repository the user cannot see is a 404 there (no access).
            permission = api.repository_permission(full_name, user.subject)
            if not permission or REPO_ROLES[provider.type].get(permission) is None:
                return None, permission
            return api.repository(full_name), permission

        (repository, permission), token = self._with_api(user, provider, lookup)
        role = REPO_ROLES[provider.type].get(permission) if permission else None
        if repository is None or role is None:
            needed = NEEDED_PERMISSION[provider.type]["viewer"]
            raise AccessDeniedError(
                f"Opening {full_name} needs at least {needed} access on {provider.name} ({provider.issuer})"
            )
        canonical = repository.get("full_path") or full_name
        clone_url = self._check_clone_url(repository.get("clone_url"), provider, canonical)
        web_url = provider.issuer.rstrip("/")

        def auth(url: str) -> dict[str, str] | None:
            parts = parse_remote(url)
            if parts is None or parts[0] not in ("https", "http") or match_remote(url, provider) is None:
                return None
            return git_auth_config(provider.type, web_url, token)

        with self._lock:
            lock = self._open_locks.setdefault((provider.id, canonical.lower()), threading.Lock())
        with lock:
            assert self.open_repository is not None
            result = self.open_repository(clone_url, self._same_repository(provider, canonical, clone_url), auth)
        project_id = result["project_id"]
        # The new project's remote binds it to the repository: roles are looked up from it now.
        self.repo.forget(project_id=project_id)
        logger.info(
            "User %s opened %s (%s) as project %s (%s)", user.id, canonical, permission, project_id,
            "cloned" if result.get("cloned") else "already there",
        )
        return {
            **result,
            "provider_id": provider.id,
            "repository": canonical,
            "permission": permission,
            "role": role,
        }


# --- REST -------------------------------------------------------------------------------


class OpenBody(BaseModel):
    provider: str
    repo: str


def _call(fn, *args):
    try:
        return fn(*args)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def build_discovery_router(
    discovery: DiscoveryService, broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None
) -> APIRouter:
    router = APIRouter()

    @router.get("/discovery/projects")
    def list_projects():
        return _call(discovery.list)

    @router.post("/discovery/refresh")
    def refresh():
        return _call(discovery.refresh)

    @router.post("/discovery/open")
    async def open_project(body: OpenBody):
        result = await run_in_threadpool(_call, discovery.open, body.provider, body.repo)
        if broadcast is not None:
            await broadcast({"event": "changed", "project_id": result["project_id"]})
        return result

    return router


# --- WebSocket ----------------------------------------------------------------------------


def build_discovery_rpc_methods(
    discovery: DiscoveryService, broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None
) -> dict[str, Callable[[dict], Any]]:
    """``discovery.*`` for ``HOOKS.rpc_methods`` (errors are mapped by the core dispatcher)."""

    async def open_project(params: dict) -> Any:
        result = await run_in_threadpool(discovery.open, params.get("provider"), params.get("repo"))
        if broadcast is not None:
            await broadcast({"event": "changed", "project_id": result["project_id"]})
        return result

    return {
        "discovery.list": lambda p: discovery.list(),
        "discovery.refresh": lambda p: discovery.refresh(),
        "discovery.open": open_project,
    }
