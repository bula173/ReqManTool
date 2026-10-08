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

Slice 3: module edit locks (``module_locks.py``): check out a module for
exclusive editing, installed as ``HOOKS.lock_service``; lock state in
``module_locks.json``; REST routes and WebSocket methods (``module.*``).

Slice 4: attribution and audit. The audit log (``audit.py``,
``audit.jsonl``) records logins, failed logins, logouts, admin changes,
broken module locks and — through ``HOOKS.audit`` — the core's project
create/lock/unlock/remove/purge; server admins read it (``GET
/admin/audit``, ``admin.audit.list``). Commit authorship and the per-user
squash window, and ``by`` in live-sync events, are core behavior driven by
the request identity.

Roles from repository permissions (``repo_access.py``): a project whose
git remote lives on the GitLab / GitHub instance a user logged in with
(GitHub login: ``github_oauth.py``) gives that user the role their
repository permission maps to; the user's OAuth token stays in the session.
New role ``reviewer`` (status sign-off); the requirement status guards
themselves are core rules (``reqman.service``) asked through the
permission guard (``project.review``, ``project.approve``).
"""

from __future__ import annotations

import logging
from typing import Any

from reqman.extensions import EditionContext

from .admin import AdminService, build_admin_router, build_rpc_methods
from .github_oauth import GitHubOAuthClient
from .audit import AuditLog, AuditService, build_audit_router, build_audit_rpc_methods
from .memberships import MembershipRegistry
from .module_locks import ModuleLockRegistry, ModuleLocks, build_lock_router, build_lock_rpc_methods
from .middleware import AuthMiddleware
from .oidc import OidcClient
from .permissions import MembershipEvents, MembershipGuard, Permissions
from .repo_access import RepoAccess
from .routes import build_router
from .sessions import PendingLogins, SessionStore
from .settings import ServerSettings, load_server_settings
from .users import UserRegistry

__all__ = ["ServerEditionImpl", "edition"]

logger = logging.getLogger("reqman_server")

USERS_FILE = "users.json"
SESSIONS_FILE = "sessions.json"
MEMBERSHIPS_FILE = "memberships.json"
MODULE_LOCKS_FILE = "module_locks.json"
AUDIT_FILE = "audit.jsonl"


class ServerEditionImpl:
    name = "ReqManTool server edition"

    def __init__(self) -> None:
        self.settings: ServerSettings | None = None
        self.sessions: SessionStore | None = None
        self.users: UserRegistry | None = None
        self.memberships: MembershipRegistry | None = None
        self.locks: ModuleLocks | None = None
        self.audit: AuditLog | None = None
        self.repo: RepoAccess | None = None

    def install(self, app, context: EditionContext) -> None:
        settings = load_server_settings(
            context.config_values, context.settings.config_file, cors_origins=context.settings.cors_origins
        )
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        users = UserRegistry(settings.state_dir / USERS_FILE, admins=settings.admins, admin_groups=settings.admin_groups)
        audit = AuditLog(settings.state_dir / AUDIT_FILE, users)
        memberships = MembershipRegistry(settings.state_dir / MEMBERSHIPS_FILE)
        sessions = SessionStore(
            settings.state_dir / SESSIONS_FILE if settings.persist_sessions else None,
            max_age=settings.session_max_age_seconds,
            idle=settings.session_idle_seconds,
        )
        clients = {
            p.id: GitHubOAuthClient(p) if p.type == "github" else OidcClient(p) for p in settings.providers
        }
        repo = RepoAccess(settings, clients, sessions, context.project_remote_url, context.project_ids)
        permissions = Permissions(settings, users, memberships, repo=repo if repo.enabled else None)
        guard = MembershipGuard(permissions)
        lock_registry = ModuleLockRegistry(settings.state_dir / MODULE_LOCKS_FILE, settings.module_lock_idle_seconds)
        locks = ModuleLocks(
            lock_registry,
            guard,
            users,
            require_checkout=settings.require_module_checkout,
            project_exists=context.project_exists,
            module_exists=context.module_exists,
            audit=audit,
        )
        repo_roles = repo if repo.enabled else None
        app.include_router(
            build_router(
                settings, clients, sessions, PendingLogins(), users, permissions,
                locks=lock_registry, broadcast=context.broadcast, audit=audit, repo=repo_roles,
            )
        )
        admin = AdminService(
            permissions, guard, users, memberships, sessions, context.project_exists, locks=lock_registry, audit=audit,
            repo=repo_roles,
        )
        audit_service = AuditService(audit, permissions)
        app.include_router(build_admin_router(admin, context.broadcast))
        app.include_router(build_audit_router(audit_service))
        app.include_router(build_lock_router(locks, context.broadcast))
        # The core asks these at its choke points (ProjectService) and in its WebSocket dispatcher.
        context.hooks.permission_guard = guard
        context.hooks.lock_service = locks
        context.hooks.project_events = MembershipEvents(memberships, locks=lock_registry, repo=repo_roles)
        context.hooks.audit = audit
        context.hooks.rpc_methods.update(build_rpc_methods(admin, context.broadcast))
        context.hooks.rpc_methods.update(build_lock_rpc_methods(locks, context.broadcast))
        context.hooks.rpc_methods.update(build_audit_rpc_methods(audit_service))
        app.add_middleware(
            AuthMiddleware, settings=settings, sessions=sessions, users=users, frontend_dir=context.frontend_dir
        )
        self.settings, self.sessions, self.users, self.memberships = settings, sessions, users, memberships
        self.locks = locks
        self.audit = audit
        self.repo = repo_roles
        if not settings.cookie_secure:
            logger.warning("Server edition: the session cookie is not marked Secure (cookie_secure: false)")
        logger.info(
            "Server edition: login with %s; users, memberships and sessions in %s",
            ", ".join(f"{p.name} ({p.issuer})" for p in settings.providers),
            settings.state_dir,
        )
        if repo_roles is not None:
            logger.info(
                "Server edition: roles from repository permissions on %s (cached %d s)",
                ", ".join(p.name for p in settings.providers if p.type in ("gitlab", "github")),
                settings.repo_roles_ttl_seconds,
            )

    def public_info(self) -> dict[str, Any]:
        providers = [p.public_info() for p in self.settings.providers] if self.settings else []
        return {"providers": providers}


edition = ServerEditionImpl()
