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
  project; raises ``PermissionError`` to refuse. Defined now, enforced by
  a later slice of the server edition.
- **Module lock service** (``ModuleLockService``): asked before a write to
  a module; raises ``ModuleLockedError`` when another user holds the lock.
  Defined now, implemented by a later slice.

``HOOKS`` holds the active guard and lock service (no-ops by default).
This module must stay dependency-free and must never import
``reqman_server``.
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator, Protocol

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


class PermissionGuard(Protocol):
    """Decides whether ``identity`` may perform ``action`` (e.g. ``"project.read"``,
    ``"project.write"``, ``"server.admin"``) on ``project_id``; raises ``PermissionError`` if not."""

    def check(self, identity: RequestIdentity | None, action: str, project_id: str | None = None) -> None: ...


class AllowAllGuard:
    """The local edition's guard: everything is allowed."""

    def check(self, identity: RequestIdentity | None, action: str, project_id: str | None = None) -> None:
        return None


# --- module edit locks ----------------------------------------------------------------


class ModuleLockedError(Exception):
    """A write to a module that another user has checked out (REST 423)."""


class ModuleLockService(Protocol):
    """Checks module edit locks before a write; raises ``ModuleLockedError`` if another user holds one."""

    def check_write(self, identity: RequestIdentity | None, project_id: str, module_id: str) -> None: ...


class NoLocks:
    """The local edition's lock service: there are no module locks."""

    def check_write(self, identity: RequestIdentity | None, project_id: str, module_id: str) -> None:
        return None


@dataclass
class Hooks:
    """The active permission guard and module lock service (no-ops unless an edition replaces them)."""

    permission_guard: PermissionGuard = field(default_factory=AllowAllGuard)
    lock_service: ModuleLockService = field(default_factory=NoLocks)


HOOKS = Hooks()


# --- the server edition ---------------------------------------------------------------


@dataclass(frozen=True)
class EditionContext:
    """What the core hands to an edition when installing it."""

    settings: "Settings"
    # The config file's raw values (the edition reads its own section, e.g. ``server``).
    config_values: dict[str, Any]
    hooks: Hooks
    # The built frontend served from this origin, or ``None`` (API only).
    frontend_dir: Any = None


class ServerEdition(Protocol):
    """What ``reqman_server`` exposes as ``reqman_server.edition``."""

    name: str

    def install(self, app: "FastAPI", context: EditionContext) -> None: ...

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
