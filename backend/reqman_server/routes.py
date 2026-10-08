"""Login, callback, logout and "who am I" routes of the server edition.

- ``GET /auth/login/{provider}?next=/path``: start a login; redirects to the
  provider (OIDC authorization code flow with PKCE, see ``oidc.py``).
- ``GET /auth/callback/{provider}``: the provider's redirect back; creates
  the user (first login) and the session (which keeps the OAuth tokens,
  server-side only), looks up the user's roles from repository permissions
  (``repo_access.py``), sets the session cookie and redirects to
  ``frontend_url`` + ``next``. A failure redirects to
  ``frontend_url/?login_error=<message>`` instead.
- ``GET /auth/me``: the logged-in user, their roles (``permissions``, see
  ``permissions.Permissions.summary``) and the session's CSRF token (401
  without a session — the middleware answers).
- ``POST /auth/logout``: ends the session and clears the cookie (needs the
  CSRF token when a session exists). The user's module edit locks are
  released when no other session of theirs is left.
- Logins, failed logins (of a login started in this browser) and logouts
  are written to the audit log (``audit.py``) when one is given.
- ``GET /health``: liveness probe, public.
"""

from __future__ import annotations

import logging
import secrets
import time
import urllib.parse
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .audit import AUDIT_LOGIN, AUDIT_LOGIN_FAILED, AUDIT_LOGOUT, AuditLog
from .middleware import cookie_value
from .github_oauth import GitHubOAuthClient
from .oidc import OidcClient, OidcError, pkce_pair
from .permissions import Permissions
from .repo_access import RepoAccess
from .sessions import PENDING_LOGIN_TTL, PendingLogin, PendingLogins, SessionStore
from .settings import ServerSettings
from .users import InactiveUserError, UserRegistry

logger = logging.getLogger("reqman_server.auth")

LOGIN_COOKIE = "reqman_login"


def safe_next(value: str | None) -> str:
    """A same-site path to return to after login (``/`` for anything else: no open redirect)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(ch) < 0x20 for ch in value):
        return "/"
    return value


def build_router(
    settings: ServerSettings,
    clients: dict[str, OidcClient | GitHubOAuthClient],
    sessions: SessionStore,
    pending: PendingLogins,
    users: UserRegistry,
    permissions: Permissions | None = None,
    locks: Any = None,
    broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    audit: AuditLog | None = None,
    repo: RepoAccess | None = None,
) -> APIRouter:
    router = APIRouter()

    def client_ip(request: Request) -> str | None:
        return request.client.host if request.client else None

    def login_failed(request: Request, provider_id: str, reason: str, user_id: str | None = None) -> None:
        if audit is not None:
            audit.safe_record(
                None, AUDIT_LOGIN_FAILED, None, {"provider": provider_id, "reason": reason},
                user_id=user_id, ip=client_ip(request),
            )

    def base_url(request: Request) -> str:
        return settings.base_url or str(request.base_url).rstrip("/")

    def frontend_redirect(path: str) -> RedirectResponse:
        # Relative to the backend origin unless the frontend lives elsewhere (dev: Vite on :5173).
        return RedirectResponse(f"{settings.frontend_url}{path}", status_code=303)

    def login_error(message: str) -> RedirectResponse:
        response = frontend_redirect("/?" + urllib.parse.urlencode({"login_error": message}))
        response.delete_cookie(LOGIN_COOKIE, path="/auth/")
        return response

    @router.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @router.get("/auth/login/{provider_id}")
    def login(provider_id: str, request: Request, next: str | None = None):  # noqa: A002 - query name
        client = clients.get(provider_id)
        if client is None:
            raise HTTPException(status_code=404, detail=f"Unknown login provider '{provider_id}'")
        state, nonce, binding = (secrets.token_urlsafe(32) for _ in range(3))
        verifier, challenge = pkce_pair()
        redirect_uri = f"{base_url(request)}/auth/callback/{provider_id}"
        try:
            url = client.authorization_url(redirect_uri, state, nonce, challenge)
        except OidcError as exc:
            return login_error(str(exc))
        pending.add(
            PendingLogin(
                provider=provider_id,
                state=state,
                nonce=nonce,
                code_verifier=verifier,
                redirect_uri=redirect_uri,
                next_path=safe_next(next),
                browser_binding=binding,
                created=time.time(),
            )
        )
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            LOGIN_COOKIE,
            binding,
            max_age=int(PENDING_LOGIN_TTL),
            path="/auth/",
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )
        return response

    @router.get("/auth/callback/{provider_id}")
    def callback(
        provider_id: str,
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ):
        login = pending.take(state)
        # Only failures of a login started in this browser are audited: anyone
        # can call this public route, and must not be able to fill the log.
        binding = cookie_value(request.scope, LOGIN_COOKIE) or ""
        genuine = (
            login is not None
            and login.provider == provider_id
            and secrets.compare_digest(binding.encode(), login.browser_binding.encode())
        )
        if error:
            logger.info("Login with %s refused by the provider: %s", provider_id, error)
            message = f"The login was cancelled or refused ({(error_description or error)[:200]})."
            if genuine:
                login_failed(request, provider_id, message)
            return login_error(message)
        if login is None or login.provider != provider_id:
            return login_error("The login has expired or was already used; please log in again.")
        if not genuine:
            return login_error("The login was started in another browser; please log in again.")
        client = clients.get(provider_id)
        if client is None or not code:
            login_failed(request, provider_id, "The login response is incomplete")
            return login_error("The login response is incomplete; please log in again.")
        try:
            tokens = client.exchange_code(code, login.redirect_uri, login.code_verifier)
            claims = client.identity_from_tokens(tokens, login.nonce, want_groups=settings.wants_groups)
            known = {u.id for u in users.all()}
            user = users.login(claims)
        except OidcError as exc:
            login_failed(request, provider_id, str(exc))
            return login_error(str(exc))
        except InactiveUserError as exc:
            logger.info("Login refused for a deactivated user (%s, %s)", provider_id, claims.subject)
            refused = next(
                (u for u in users.all() if u.provider == provider_id and u.subject == claims.subject), None
            )
            login_failed(request, provider_id, str(exc), user_id=refused.id if refused else None)
            return login_error(str(exc))
        token, _session = sessions.create(user.id, provider=provider_id, oauth_tokens=tokens)
        logger.info("User %s (%s) logged in with %s", user.id, user.username or user.email, provider_id)
        if repo is not None:
            # Roles from repository permissions: resolved now, then cached (see repo_access.py).
            try:
                repo.forget_remotes()
                repo.resolve_user(user)
            except Exception:  # noqa: BLE001 - the login itself succeeded; roles are looked up again on use
                logger.exception("Repository roles of %s could not be resolved at login", user.id)
        if audit is not None:
            audit.safe_record(
                None, AUDIT_LOGIN, None, {"provider": provider_id, "first_login": user.id not in known or None},
                user_id=user.id, user_name=user.name, ip=client_ip(request),
            )
        response = frontend_redirect(login.next_path)
        response.delete_cookie(LOGIN_COOKIE, path="/auth/")
        response.set_cookie(
            settings.cookie_name,
            token,
            max_age=settings.session_max_age_seconds,
            path="/",
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )
        return response

    @router.get("/auth/me")
    def me(request: Request):
        token = cookie_value(request.scope, settings.cookie_name)
        session = sessions.get(token, touch=False)
        user = users.get(session.user_id) if session else None
        if session is None or user is None:
            raise HTTPException(status_code=401, detail="Not logged in")
        body = {"user": user.public(), "csrf_token": session.csrf_token, "expires_at": int(session.expires)}
        if permissions is not None:
            body["permissions"] = permissions.summary(user)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @router.post("/auth/logout")
    async def logout(request: Request):
        token = cookie_value(request.scope, settings.cookie_name)
        session = sessions.get(token, touch=False)
        sessions.delete(token)
        if session is not None:
            logger.info("User %s logged out", session.user_id)
            if audit is not None:
                audit.safe_record(None, AUDIT_LOGOUT, user_id=session.user_id, ip=client_ip(request))
            # ``module_locks.ModuleLockRegistry``: give the user's module locks back on the last logout.
            if locks is not None and not sessions.has_user(session.user_id):
                for project_id in locks.release_user(session.user_id):
                    if broadcast is not None:
                        await broadcast({"event": "module_locks_changed", "project_id": project_id})
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(settings.cookie_name, path="/", secure=settings.cookie_secure, samesite="lax")
        return response

    return router
