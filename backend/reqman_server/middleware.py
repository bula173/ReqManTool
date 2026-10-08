"""Authentication middleware (pure ASGI): every REST request and the WebSocket handshake need a session.

Public (no session needed): ``/auth/login/*``, ``/auth/callback/*``,
``/auth/logout``, ``/edition``, ``/version``, ``/about`` (+ ``/about/*``),
``/health``, and the static frontend (``/``, ``/assets/*``, top-level files of
the bundle). A browser page load of a client-side route never reaches this
middleware: ``SpaNavigationMiddleware`` (outside it) answers it with
``index.html``, which carries no data.

- No or expired session: REST → ``401 {"detail": "Not logged in"}``; the
  WebSocket handshake is refused (close code 4401 before accept, i.e.
  HTTP 403).
- CSRF: a state-changing request (anything but GET/HEAD/OPTIONS) needs the
  session's token in ``X-CSRF-Token`` (else 403). Together with the
  ``SameSite=Lax`` cookie this blocks cross-site form posts and fetches.
- WebSocket: the ``Origin`` must be this server's own origin or one of
  ``cors_origins`` / ``frontend_url`` / ``base_url`` (else close 4403), which
  blocks cross-site WebSocket hijacking; the session is checked again on
  every received message, and the connection is closed (4401) once it ends.
- Server-wide settings (switching the data folder, ``POST /config``, and
  browsing the server's file system, ``/fs/*``) need a server admin (403).
  Project-level permissions come with a later slice (``PermissionGuard``).

For an authenticated request the user is put into
``reqman.extensions.current_identity`` (git commit author, lock owner).
"""

from __future__ import annotations

import hmac
import json
import urllib.parse
from pathlib import Path
from typing import Any

from reqman.extensions import RequestIdentity, identity_scope

from .sessions import Session, SessionStore
from .settings import ServerSettings
from .users import User, UserRegistry

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = b"x-csrf-token"
PUBLIC_EXACT = {"/edition", "/version", "/about", "/health", "/auth/logout"}
PUBLIC_PREFIXES = ("/about/", "/auth/login/", "/auth/callback/")
WS_CLOSE_UNAUTHENTICATED = 4401
WS_CLOSE_FORBIDDEN_ORIGIN = 4403


def _header(scope: dict, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value.decode("latin-1")
    return None


def cookie_value(scope: dict, name: str) -> str | None:
    """The value of cookie ``name``. Parsed by hand: ``http.cookies.SimpleCookie`` gives up on the
    whole header at the first cookie it dislikes (other apps on the same host), hiding ours."""
    for header_name, header_value in scope.get("headers") or []:
        if header_name != b"cookie":
            continue
        for part in header_value.decode("latin-1").split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and key == name:
                return value.strip().strip('"') or None
    return None


def _origin_of(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def identity_of(user: User) -> RequestIdentity:
    return RequestIdentity(user_id=user.id, name=user.name, email=user.email, server_admin=user.server_admin)


class AuthMiddleware:
    def __init__(
        self,
        app,
        settings: ServerSettings,
        sessions: SessionStore,
        users: UserRegistry,
        frontend_dir: Path | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self.sessions = sessions
        self.users = users
        self.frontend_dir = frontend_dir.resolve() if frontend_dir is not None else None
        allowed = {_origin_of(o) for o in settings.cors_origins}
        for url in (settings.frontend_url, settings.base_url or ""):
            if url:
                allowed.add(_origin_of(url))
        self.allowed_origins = {o for o in allowed if o}

    # --- what is public ------------------------------------------------------------

    def _is_static(self, path: str) -> bool:
        if self.frontend_dir is None:
            return path == "/"  # redirects to /docs, which itself needs a session
        if path == "/" or path.startswith("/assets/"):
            return True
        name = path[1:]
        if not name or "/" in name:
            return False
        candidate = (self.frontend_dir / name).resolve()
        return candidate.parent == self.frontend_dir and candidate.is_file()

    def _is_public(self, path: str) -> bool:
        return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES) or self._is_static(path)

    # --- session ---------------------------------------------------------------------

    def session_user(self, scope: dict) -> tuple[Session, User] | None:
        token = cookie_value(scope, self.settings.cookie_name)
        session = self.sessions.get(token)
        if session is None:
            return None
        user = self.users.get(session.user_id)
        if user is None or not user.active:
            self.sessions.delete(token)
            return None
        return session, user

    def _origin_allowed(self, scope: dict) -> bool:
        origin = _header(scope, b"origin")
        if origin is None:
            return True  # not a browser (browsers always send Origin on a WebSocket handshake)
        origin = origin.lower()
        host = _header(scope, b"host") or ""
        own = {f"http://{host}".lower(), f"https://{host}".lower()}
        return origin in own or origin in self.allowed_origins

    # --- ASGI ---------------------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    async def _http(self, scope, receive, send):
        path = scope["path"]
        found = self.session_user(scope)
        if found is None:
            if self._is_public(path):
                await self.app(scope, receive, send)
                return
            await _json_response(send, 401, {"detail": "Not logged in"})
            return
        session, user = found
        method = scope["method"]
        if method not in SAFE_METHODS:
            sent = _header(scope, CSRF_HEADER) or ""
            if not hmac.compare_digest(sent.encode("utf-8"), session.csrf_token.encode("utf-8")):
                await _json_response(send, 403, {"detail": "Missing or invalid CSRF token; reload the page"})
                return
        if not user.server_admin and (path.startswith("/fs/") or (path == "/config" and method not in SAFE_METHODS)):
            await _json_response(send, 403, {"detail": "Only a server administrator can do this"})
            return
        with identity_scope(identity_of(user)):
            await self.app(scope, receive, send)

    async def _websocket(self, scope, receive, send):
        if not self._origin_allowed(scope):
            await send({"type": "websocket.close", "code": WS_CLOSE_FORBIDDEN_ORIGIN})
            return
        found = self.session_user(scope)
        if found is None:
            await send({"type": "websocket.close", "code": WS_CLOSE_UNAUTHENTICATED})
            return
        _session, user = found

        async def checked_receive():
            message = await receive()
            if message["type"] == "websocket.receive" and self.session_user(scope) is None:
                # The session ended (logout, expiry, deactivation): close and end the handler.
                await send({"type": "websocket.close", "code": WS_CLOSE_UNAUTHENTICATED})
                return {"type": "websocket.disconnect", "code": WS_CLOSE_UNAUTHENTICATED}
            return message

        with identity_scope(identity_of(user)):
            await self.app(scope, checked_receive, send)


async def _json_response(send, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
