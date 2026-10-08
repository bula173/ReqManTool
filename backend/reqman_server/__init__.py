"""ReqManTool server edition: multi-user login, sessions and the user registry.

Loaded by the core only when the effective ``mode`` is ``server``
(``reqman.extensions.load_server_edition``); the local edition never
imports it. It plugs into the core exclusively through
``reqman.extensions`` (``ServerEdition.install``, the request identity,
and — in later slices — the permission guard and module lock service).
It lives in this repository for now and is meant to move to the separately
licensed ``reqman-server`` package (see LICENSING.md); keep it importing
from ``reqman`` only, never the other way round.

Slice 1: authentication (GitLab and generic OpenID Connect, ``oidc.py``),
server-side sessions with CSRF protection (``sessions.py``,
``middleware.py``), the user registry (``users.py``) and the login routes
(``routes.py``). Configuration: the ``server`` section of the config file
(``settings.py``).

Slice 2: project memberships (``memberships.py``), roles and the
permission guard (``permissions.py``, installed as
``HOOKS.permission_guard`` and ``HOOKS.project_events``), and the admin
pages' REST routes and WebSocket methods (``admin.py``, the latter through
``HOOKS.rpc_methods``).
"""

from __future__ import annotations

import logging
from typing import Any

from reqman.extensions import EditionContext

from .admin import AdminService, build_admin_router, build_rpc_methods
from .memberships import MembershipRegistry
from .middleware import AuthMiddleware
from .oidc import OidcClient
from .permissions import MembershipEvents, MembershipGuard, Permissions
from .routes import build_router
from .sessions import PendingLogins, SessionStore
from .settings import ServerSettings, load_server_settings
from .users import UserRegistry

__all__ = ["ServerEditionImpl", "edition"]

logger = logging.getLogger("reqman_server")

USERS_FILE = "users.json"
SESSIONS_FILE = "sessions.json"
MEMBERSHIPS_FILE = "memberships.json"


class ServerEditionImpl:
    name = "ReqManTool server edition"

    def __init__(self) -> None:
        self.settings: ServerSettings | None = None
        self.sessions: SessionStore | None = None
        self.users: UserRegistry | None = None
        self.memberships: MembershipRegistry | None = None

    def install(self, app, context: EditionContext) -> None:
        settings = load_server_settings(
            context.config_values, context.settings.config_file, cors_origins=context.settings.cors_origins
        )
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        users = UserRegistry(settings.state_dir / USERS_FILE, admins=settings.admins, admin_groups=settings.admin_groups)
        memberships = MembershipRegistry(settings.state_dir / MEMBERSHIPS_FILE)
        permissions = Permissions(settings, users, memberships)
        guard = MembershipGuard(permissions)
        sessions = SessionStore(
            settings.state_dir / SESSIONS_FILE if settings.persist_sessions else None,
            max_age=settings.session_max_age_seconds,
            idle=settings.session_idle_seconds,
        )
        clients = {p.id: OidcClient(p) for p in settings.providers}
        app.include_router(build_router(settings, clients, sessions, PendingLogins(), users, permissions))
        admin = AdminService(permissions, guard, users, memberships, sessions, context.project_exists)
        app.include_router(build_admin_router(admin, context.broadcast))
        # The core asks these at its choke points (ProjectService) and in its WebSocket dispatcher.
        context.hooks.permission_guard = guard
        context.hooks.project_events = MembershipEvents(memberships)
        context.hooks.rpc_methods.update(build_rpc_methods(admin, context.broadcast))
        app.add_middleware(
            AuthMiddleware, settings=settings, sessions=sessions, users=users, frontend_dir=context.frontend_dir
        )
        self.settings, self.sessions, self.users, self.memberships = settings, sessions, users, memberships
        if not settings.cookie_secure:
            logger.warning("Server edition: the session cookie is not marked Secure (cookie_secure: false)")
        logger.info(
            "Server edition: login with %s; users, memberships and sessions in %s",
            ", ".join(f"{p.name} ({p.issuer})" for p in settings.providers),
            settings.state_dir,
        )

    def public_info(self) -> dict[str, Any]:
        providers = [p.public_info() for p in self.settings.providers] if self.settings else []
        return {"providers": providers}


edition = ServerEditionImpl()
