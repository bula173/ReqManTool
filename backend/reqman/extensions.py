"""Extension points of the core for an *edition* plugged in at start-up.

The community (local) edition never sets any of these: it runs with no
login, the git identity is the user's own (``git_repo.GitHistory``), and
every hook below is a no-op. The multi-user server edition (the separate
``reqman_server`` package, see LICENSING.md) is loaded only when the
effective ``mode`` setting is ``server`` (``load_server_edition``), and it
plugs in through exactly these interfaces:

- ``ServerEdition.install(app, context)``: adds its routes (login, callback,
  logout, ...) and its authentication middleware to the FastAPI app.
- **Request identity** (``RequestIdentity``, ``current_identity``,
  ``identity_scope``): the authenticated user of the current request or
  WebSocket connection, in a ``ContextVar``. The edition's middleware sets
  it; the core reads it where it attributes changes (git commit author,
  who locked a project). ``None`` (always, in local mode) keeps the local
  behavior.
- **Permission guard** (``PermissionGuard``): asked before an action on a
  project (or a server-wide action); raises ``AccessDeniedError`` (a
  ``PermissionError``) to refuse: REST 403, WebSocket error -32003.
  ``ProjectService`` asks it at its choke points (every public method
  taking a ``project_id``, project creation and listing, cross-project
  reads and cascades, libraries and connections); the actions are the
  ``ACTION_*`` constants below. The local edition's ``AllowAllGuard`` is
  skipped without a call (``guard_active``). The requirement status guards
  (sign-off and approval, ``ProjectService._check_status_changes``) ask it
  for ``project.review`` / ``project.approve`` and run only when a guard is
  active, so the local edition never sees them.
- **Project events** (``ProjectEvents``): told after a project was created
  (by whom) or removed, e.g. to make the creator its project admin and to
  forget the memberships of a removed project.
- **WebSocket methods** (``Hooks.rpc_methods``): JSON-RPC methods an
  edition adds to ``/ws`` (sync or async callables taking the params).
- **Login account** (``EditionContext.providers``): the edition installs
  ``ProviderService.login_source`` on the app's provider service, so
  publish / push / pull can authenticate with the logged-in user's OAuth
  token for one git command or API call only (``reqman_server/
  login_account.py``); the local edition has no login source.
- **Module lock service** (``ModuleLockService``): asked before a write to
  the content of a module (its requirements, the links it is the source
  of); raises ``ModuleLockedError`` (REST 423, WebSocket -32005) when
  another user has the module checked out. ``ProjectService`` asks it at
  its choke points (``_records_history`` for methods taking a
  ``module_id``, plus the link, attribute-scope and purge cascades). The
  local edition's ``NoLocks`` is skipped without a call
  (``module_locks_active``). It is also asked for the subject of the
  auto-commits made while the current identity has the module checked out
  (``module_commit_subject``, optional ``commit_subject``), so a checkout's
  name labels the history it produced.
- **Audit trail** (``AuditTrail``): told about the project-level
  administrative actions the core carries out (``AUDIT_*``: project create,
  lock, unlock, remove, purge) after they succeeded, with the acting
  identity. The edition writes them to its audit log together with its own
  events (logins, admin changes). The local edition's ``NoAudit`` is
  skipped without a call; a failing trail is logged, never raised.

``HOOKS`` holds the active guard, lock service, project event listener,
audit trail and extra WebSocket methods (no-ops / empty by default).
This module must stay dependency-free and must never import
``reqman_server``.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

    from .config import Settings

MODE_LOCAL = "local"
MODE_SERVER = "server"
MODES = (MODE_LOCAL, MODE_SERVER)

# The importable name of the server edition package.
SERVER_PACKAGE = "reqman_server"


# --- request identity -----------------------------------------------------------------


@dataclass(frozen=True)
class RequestIdentity:
    """The authenticated user a request (or WebSocket connection) acts for."""

    user_id: str
    name: str
    email: str
    server_admin: bool = False


_current_identity: ContextVar[RequestIdentity | None] = ContextVar("reqman_request_identity", default=None)


def current_identity() -> RequestIdentity | None:
    """The identity of the current request, or ``None`` (local mode, or outside a request)."""
    return _current_identity.get()


@contextmanager
def identity_scope(identity: RequestIdentity | None) -> Iterator[None]:
    """Make ``identity`` the current identity for the code run inside the ``with`` block."""
    token = _current_identity.set(identity)
    try:
        yield
    finally:
        _current_identity.reset(token)


# --- authentication -------------------------------------------------------------------


class IdentityProvider(Protocol):
    """Resolves the identity of an incoming ASGI request (HTTP or WebSocket handshake).

    Returns ``None`` for an unauthenticated request. An edition's
    middleware uses it to decide whether to let a request through and to
    set ``current_identity`` for it.
    """

    def authenticate(self, scope: dict) -> RequestIdentity | None: ...


# --- permission guard -----------------------------------------------------------------

# Project actions (with a ``project_id``), in increasing order of privilege:
# read anything of the project; sign off requirements (change only their
# status attribute, see ``ProjectService`` "status guards"); change its data
# (requirements, modules, links, imports, push/pull); administer it
# (lock/unlock, baselines, attribute definitions, views, link
# configurations, remove, purge, parent, git history/remote/publish,
# members); approve requirements (status → Approved, or out of Approved).
ACTION_PROJECT_READ = "project.read"
ACTION_PROJECT_REVIEW = "project.review"
ACTION_PROJECT_WRITE = "project.write"
ACTION_PROJECT_ADMIN = "project.admin"
ACTION_PROJECT_APPROVE = "project.approve"
# Server-wide actions (``project_id`` is ``None``).
ACTION_PROJECT_CREATE = "project.create"  # create a project (blank, template, clone)
ACTION_LIBRARY_WRITE = "library.write"  # change the shared template / prompt libraries
ACTION_CONNECTIONS_USE = "connections.use"  # use the GitLab/GitHub connections (list, browse, publish)
ACTION_CONNECTIONS_MANAGE = "connections.manage"  # add, change, remove connections (tokens)
ACTION_GIT_REMOTE_URL = "git.remote_url"  # clone from / set a remote to an arbitrary URL or path
ACTION_SERVER_CONFIG = "server.config"  # see and switch the data folder


class AccessDeniedError(PermissionError):
    """An action the permission guard refused (REST 403; WebSocket error code -32003).

    A subclass so an operating-system ``PermissionError`` (EACCES) is never
    mistaken for a refusal of the guard.
    """


class PermissionGuard(Protocol):
    """Decides whether ``identity`` may perform ``action`` (one of the ``ACTION_*`` constants)
    on ``project_id``; raises ``AccessDeniedError`` if not."""

    def check(self, identity: RequestIdentity | None, action: str, project_id: str | None = None) -> None: ...


class AllowAllGuard:
    """The local edition's guard: everything is allowed."""

    def check(self, identity: RequestIdentity | None, action: str, project_id: str | None = None) -> None:
        return None


# --- module edit locks ----------------------------------------------------------------


class ModuleLockedError(Exception):
    """A write to a module that another user has checked out (REST 423; WebSocket error code -32005).

    Also raised when the server requires a checkout before editing
    (``server.require_module_checkout``) and the writer holds none.
    """


class ModuleLockService(Protocol):
    """Checks module edit locks before a write; raises ``ModuleLockedError`` if another user holds one.

    An implementation may also provide ``commit_subject`` (optional: the core
    asks for it with ``getattr``), the subject line of the auto-commits made
    while the current identity has ``module_id`` checked out.
    """

    def check_write(self, identity: RequestIdentity | None, project_id: str, module_id: str) -> None: ...

    def commit_subject(self, project_id: str, module_id: str) -> str | None: ...


class NoLocks:
    """The local edition's lock service: there are no module locks."""

    def check_write(self, identity: RequestIdentity | None, project_id: str, module_id: str) -> None:
        return None

    def commit_subject(self, project_id: str, module_id: str) -> str | None:
        return None


# --- audit trail ----------------------------------------------------------------------

# Project-level administrative actions the core reports to the audit trail.
AUDIT_PROJECT_CREATE = "project.create"
AUDIT_PROJECT_RENAME = "project.rename"
AUDIT_PROJECT_LOCK = "project.lock"
AUDIT_PROJECT_UNLOCK = "project.unlock"
AUDIT_PROJECT_REMOVE = "project.remove"
AUDIT_PROJECT_PURGE = "project.purge"

_logger = logging.getLogger("reqman.extensions")


class AuditTrail(Protocol):
    """Records an administrative action (one of the ``AUDIT_*`` constants) done by ``identity``."""

    def record(
        self,
        identity: RequestIdentity | None,
        action: str,
        project_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...


class NoAudit:
    """The local edition's audit trail: nothing is recorded."""

    def record(
        self,
        identity: RequestIdentity | None,
        action: str,
        project_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        return None


# --- project events -------------------------------------------------------------------


class ProjectEvents(Protocol):
    """Told after a project was created (blank, from a template, cloned) or removed."""

    def project_created(self, identity: RequestIdentity | None, project_id: str) -> None: ...

    def project_removed(self, identity: RequestIdentity | None, project_id: str) -> None: ...


class NoProjectEvents:
    """The local edition's listener: nothing happens."""

    def project_created(self, identity: RequestIdentity | None, project_id: str) -> None:
        return None

    def project_removed(self, identity: RequestIdentity | None, project_id: str) -> None:
        return None


@dataclass
class Hooks:
    """The active edition hooks (no-ops unless an edition replaces them)."""

    permission_guard: PermissionGuard = field(default_factory=AllowAllGuard)
    lock_service: ModuleLockService = field(default_factory=NoLocks)
    project_events: ProjectEvents = field(default_factory=NoProjectEvents)
    audit: AuditTrail = field(default_factory=NoAudit)
    # Extra WebSocket JSON-RPC methods: name -> callable(params) returning the
    # result or an awaitable of it. Core methods win on a name clash.
    rpc_methods: dict[str, Callable[[dict], Any]] = field(default_factory=dict)


HOOKS = Hooks()


def guard_active() -> bool:
    """Whether a permission guard other than the local ``AllowAllGuard`` is installed."""
    return type(HOOKS.permission_guard) is not AllowAllGuard


def module_locks_active() -> bool:
    """Whether a module lock service other than the local ``NoLocks`` is installed."""
    return type(HOOKS.lock_service) is not NoLocks


def check_module_write(project_id: str, module_id: str) -> None:
    """Ask the lock service whether the current identity may write ``module_id``. Free in local mode."""
    service = HOOKS.lock_service
    if type(service) is not NoLocks:
        service.check_write(_current_identity.get(), project_id, module_id)


def module_commit_subject(project_id: str, module_id: str) -> str | None:
    """The subject of the auto-commits made while the current identity edits its checked-out ``module_id``.

    Asked of the lock service (the server edition builds it from the module,
    the user and the checkout's name, ``[module][username][name]``); ``None``
    in the local edition, without a checkout, or when the lock service does
    not implement ``commit_subject``. Commits keep their usual message then.
    """
    service = HOOKS.lock_service
    if type(service) is NoLocks:
        return None
    get = getattr(service, "commit_subject", None)
    if get is None:
        return None
    return get(project_id, module_id)


def audit(action: str, project_id: str | None = None, **details: Any) -> None:
    """Tell the audit trail that the current identity did ``action``. Free in local mode; never raises."""
    trail = HOOKS.audit
    if type(trail) is NoAudit:
        return
    try:
        trail.record(_current_identity.get(), action, project_id, {k: v for k, v in details.items() if v is not None})
    except Exception:
        _logger.exception("Could not record %s of %s in the audit log", action, project_id)


def check_permission(action: str, project_id: str | None = None) -> None:
    """Ask the active guard about the current identity; ``AccessDeniedError`` to refuse. Free in local mode."""
    guard = HOOKS.permission_guard
    if type(guard) is not AllowAllGuard:
        guard.check(_current_identity.get(), action, project_id)


def is_permitted(action: str, project_id: str | None = None) -> bool:
    """``check_permission`` as a boolean."""
    try:
        check_permission(action, project_id)
    except AccessDeniedError:
        return False
    return True


# --- the server edition ---------------------------------------------------------------


@dataclass(frozen=True)
class EditionContext:
    """What the core hands to an edition when installing it."""

    settings: Settings
    # The config file's raw values (the edition reads its own section, e.g. ``server``).
    config_values: dict[str, Any]
    hooks: Hooks
    # The built frontend served from this origin, or ``None`` (API only).
    frontend_dir: Any = None
    # Sends a live-sync event (a JSON object) to every connected WebSocket client.
    broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    # Whether a project id exists in the current data folder.
    project_exists: Callable[[str], bool] | None = None
    # Whether a module exists in a project of the current data folder.
    module_exists: Callable[[str, str], bool] | None = None
    # The ids of the projects of the current data folder.
    project_ids: Callable[[], list[str]] | None = None
    # The URL of a project's git remote (``None``: no remote, no repository, or no such project).
    project_remote_url: Callable[[str], str | None] | None = None
    # Open a repository as a project (``ProjectService.open_repository_project``: clone once,
    # later fetch): ``(clone URL, is-same-repository(remote URL), git auth(url))`` -> result.
    open_repository: Callable[..., dict[str, Any]] | None = None
    # The app-level ``reqman.providers.ProviderService`` (connections, tokens): the edition
    # installs its ``login_source`` on it so publish / push / pull can use the login account.
    providers: Any = None


class ServerEdition(Protocol):
    """What ``reqman_server`` exposes as ``reqman_server.edition``."""

    name: str

    def install(self, app: FastAPI, context: EditionContext) -> None: ...

    def public_info(self) -> dict[str, Any]: ...


class EditionUnavailableError(RuntimeError):
    """``mode: server`` was requested but the server edition package is not installed or is unusable."""


def load_server_edition() -> ServerEdition:
    """Import the server edition package (only called with ``mode: server``).

    Raises ``EditionUnavailableError`` with a clear message when the package
    is missing or does not provide an edition object.
    """
    try:
        module = importlib.import_module(SERVER_PACKAGE)
    except ModuleNotFoundError as exc:
        if exc.name != SERVER_PACKAGE:
            raise
        raise EditionUnavailableError(
            "mode is 'server' (REQMAN_MODE or 'mode' in the config file), but the server edition "
            f"package '{SERVER_PACKAGE}' is not installed. Install it, or set mode to 'local'."
        ) from None
    edition = getattr(module, "edition", None)
    if edition is None or not callable(getattr(edition, "install", None)):
        raise EditionUnavailableError(f"The package '{SERVER_PACKAGE}' does not provide a server edition.")
    return edition
