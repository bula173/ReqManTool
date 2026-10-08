"""Admin pages of the server edition: users (server admins) and project members (project admins).

REST (every route needs a session; state-changing ones the CSRF token):

- ``GET /auth/permissions``: the logged-in user's roles (also in ``/auth/me``).
- ``GET /admin/users``: every user (server admins).
- ``PUT /admin/users/{user_id}`` ``{"active"?: bool, "server_admin"?: bool}``:
  activate/deactivate (ends the user's sessions), grant/revoke server admin
  (server admins; never one's own account).
- ``GET /admin/projects/{project_id}/members``: the project's members and the
  users with a role from a group rule (project admins of it, server admins).
- ``PUT /admin/projects/{project_id}/members`` ``{"user": id | e-mail | user
  name, "role": viewer | editor | project-admin}``: add a member or change
  the role.
- ``DELETE /admin/projects/{project_id}/members/{user_id}``: remove a member.

WebSocket methods (``HOOKS.rpc_methods``, same rules and results):
``auth.permissions``, ``admin.users.list``, ``admin.users.update``,
``admin.members.list``, ``admin.members.set``, ``admin.members.remove``.

Every change broadcasts ``{"event": "permissions_changed"}``: clients
refetch their roles and the project list. A refusal is ``AccessDeniedError``
(REST 403, WebSocket -32003); a project admin cannot leave their project
without a project admin (400; server admins can). Every change is written
to the audit log (``audit.py``: ``admin.user_update``, ``admin.member_set``,
``admin.member_remove``) when one is given.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from reqman.extensions import ACTION_PROJECT_ADMIN, AccessDeniedError, RequestIdentity, current_identity

from .audit import AUDIT_MEMBER_REMOVE, AUDIT_MEMBER_SET, AUDIT_USER_UPDATE, AuditLog
from .memberships import MembershipRegistry
from .permissions import MembershipGuard, Permissions
from .sessions import SessionStore
from .settings import ROLES
from .users import User, UserRegistry

logger = logging.getLogger("reqman_server.admin")

PERMISSIONS_CHANGED = {"event": "permissions_changed"}


class AdminService:
    def __init__(
        self,
        permissions: Permissions,
        guard: MembershipGuard,
        users: UserRegistry,
        memberships: MembershipRegistry,
        sessions: SessionStore,
        project_exists: Callable[[str], bool] | None,
        locks: Any = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.permissions = permissions
        self.audit = audit
        self.guard = guard
        self.users = users
        self.memberships = memberships
        self.sessions = sessions
        self.project_exists = project_exists
        # ``module_locks.ModuleLockRegistry`` (optional): a deactivated user's module locks are released.
        self.locks = locks

    # --- who is asking ----------------------------------------------------------------

    def _actor(self) -> User:
        user = self.permissions.active_user(current_identity())
        if user is None:
            raise AccessDeniedError("Not logged in")
        return user

    def _server_admin(self) -> User:
        user = self._actor()
        if not user.server_admin:
            raise AccessDeniedError("Only a server administrator can do this")
        return user

    def _project_admin(self, project_id: Any) -> User:
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_id must be a project id")
        identity: RequestIdentity | None = current_identity()
        self.guard.check(identity, ACTION_PROJECT_ADMIN, project_id)
        if self.project_exists is not None and not self.project_exists(project_id):
            raise FileNotFoundError(f"Project '{project_id}' not found")
        return self._actor()

    # --- the user's own permissions ---------------------------------------------------

    def my_permissions(self) -> dict[str, Any]:
        return self.permissions.summary(self._actor())

    # --- users (server admins) --------------------------------------------------------

    def list_users(self) -> dict[str, Any]:
        self._server_admin()
        users = sorted(self.users.all(), key=lambda u: (u.name.lower(), u.email.lower(), u.id))
        return {"users": [u.admin_view() for u in users]}

    def update_user(self, user_id: Any, active: Any = None, server_admin: Any = None) -> dict[str, Any]:
        actor = self._server_admin()
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a user id")
        for name, value in (("active", active), ("server_admin", server_admin)):
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"'{name}' must be true or false")
        if user_id == actor.id and (active is False or server_admin is False):
            raise ValueError("You cannot deactivate your own account or remove your own server admin flag")
        try:
            user = self.users.set_flags(user_id, active=active, server_admin=server_admin)
        except KeyError:
            raise FileNotFoundError(f"User '{user_id}' not found") from None
        if active is False:
            self.sessions.delete_user(user_id)
            if self.locks is not None:
                # The permissions_changed broadcast makes every client refetch its project queries (locks too).
                self.locks.release_user(user_id)
        logger.info(
            "Server admin %s changed user %s: active=%s server_admin=%s", actor.id, user_id, active, server_admin
        )
        self._audit(AUDIT_USER_UPDATE, None, user_id, {"active": active, "server_admin": server_admin})
        return user.admin_view()

    def _audit(self, action: str, project_id: str | None, target: str, details: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit.safe_record(current_identity(), action, project_id, details, target_user_id=target)

    # --- project members (project admins, server admins) ------------------------------

    def list_members(self, project_id: Any) -> dict[str, Any]:
        self._project_admin(project_id)
        users = {u.id: u for u in self.users.all()}
        members: list[dict[str, Any]] = []
        listed: set[str] = set()
        for membership in self.memberships.of_project(project_id):
            user = users.get(membership.user_id)
            members.append(
                self._member(project_id, membership.user_id, user, membership.role, "member", membership.added_at)
            )
            listed.add(membership.user_id)
        for user in users.values():
            group_role = self.permissions.group_role(user, project_id)
            if group_role is not None and user.id not in listed:
                members.append(self._member(project_id, user.id, user, group_role, "group", ""))
        members.sort(key=lambda m: (-ROLES.index(m["role"]), m["name"].lower(), m["user_id"]))
        return {"project_id": project_id, "roles": list(ROLES), "members": members}

    def _member(
        self, project_id: str, user_id: str, user: User | None, role: str, source: str, added_at: str
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "name": user.name if user else user_id,
            "email": user.email if user else "",
            "username": user.username if user else "",
            "active": user.active if user else False,
            "server_admin": user.server_admin if user else False,
            "role": role,
            "source": source,
            # The role group rules give (the effective role is the higher one).
            "group_role": self.permissions.group_role(user, project_id) if user else None,
            "added_at": added_at,
        }

    def set_member(self, project_id: Any, user: Any, role: Any) -> dict[str, Any]:
        actor = self._project_admin(project_id)
        if not isinstance(user, str) or not user.strip():
            raise ValueError("Name the user by e-mail address, user name or id")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        target = self.users.find(user)
        if target is None:
            raise FileNotFoundError(
                f"No user '{user.strip()}'. Users appear here after their first login."
            )
        current = self.memberships.role(project_id, target.id)
        if current == "project-admin" and role != "project-admin":
            self._keep_a_project_admin(actor, project_id, target.id)
        self.memberships.set_role(project_id, target.id, role, added_by=actor.id)
        logger.info("%s set %s as %s of project %s", actor.id, target.id, role, project_id)
        if current != role:
            self._audit(AUDIT_MEMBER_SET, project_id, target.id, {"role": role, "previous_role": current})
        return self.list_members(project_id)

    def remove_member(self, project_id: Any, user_id: Any) -> dict[str, Any]:
        actor = self._project_admin(project_id)
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a user id")
        current = self.memberships.role(project_id, user_id)
        if current is None:
            raise FileNotFoundError(f"User '{user_id}' is not a member of project '{project_id}'")
        if current == "project-admin":
            self._keep_a_project_admin(actor, project_id, user_id)
        self.memberships.remove(project_id, user_id)
        logger.info("%s removed %s from project %s", actor.id, user_id, project_id)
        self._audit(AUDIT_MEMBER_REMOVE, project_id, user_id, {"previous_role": current})
        return self.list_members(project_id)

    def _keep_a_project_admin(self, actor: User, project_id: str, leaving: str) -> None:
        """A project admin (not a server admin) may not leave the project without a project admin."""
        if actor.server_admin:
            return
        others = [
            m for m in self.memberships.of_project(project_id) if m.role == "project-admin" and m.user_id != leaving
        ]
        if not others:
            raise ValueError(
                "The project would have no project admin left; make someone else project admin first"
            )


# --- REST -------------------------------------------------------------------------------


class UpdateUserBody(BaseModel):
    active: bool | None = None
    server_admin: bool | None = None


class SetMemberBody(BaseModel):
    user: str
    role: str


def _call(fn, *args):
    try:
        return fn(*args)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def build_admin_router(
    admin: AdminService, broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None
) -> APIRouter:
    router = APIRouter()

    async def changed() -> None:
        if broadcast is not None:
            await broadcast(dict(PERMISSIONS_CHANGED))

    @router.get("/auth/permissions")
    def my_permissions():
        return _call(admin.my_permissions)

    @router.get("/admin/users")
    def list_users():
        return _call(admin.list_users)

    @router.put("/admin/users/{user_id}")
    async def update_user(user_id: str, body: UpdateUserBody):
        result = _call(admin.update_user, user_id, body.active, body.server_admin)
        await changed()
        return result

    @router.get("/admin/projects/{project_id}/members")
    def list_members(project_id: str):
        return _call(admin.list_members, project_id)

    @router.put("/admin/projects/{project_id}/members")
    async def set_member(project_id: str, body: SetMemberBody):
        result = _call(admin.set_member, project_id, body.user, body.role)
        await changed()
        return result

    @router.delete("/admin/projects/{project_id}/members/{user_id}")
    async def remove_member(project_id: str, user_id: str):
        result = _call(admin.remove_member, project_id, user_id)
        await changed()
        return result

    return router


# --- WebSocket ----------------------------------------------------------------------------


def build_rpc_methods(
    admin: AdminService, broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None
) -> dict[str, Callable[[dict], Any]]:
    """The admin methods for ``HOOKS.rpc_methods`` (errors are mapped by the core dispatcher)."""

    def mutation(fn: Callable[[dict], Any]) -> Callable[[dict], Awaitable[Any]]:
        async def run(params: dict) -> Any:
            result = fn(params)
            if broadcast is not None:
                await broadcast(dict(PERMISSIONS_CHANGED))
            return result

        return run

    return {
        "auth.permissions": lambda p: admin.my_permissions(),
        "admin.users.list": lambda p: admin.list_users(),
        "admin.users.update": mutation(
            lambda p: admin.update_user(p.get("user_id"), p.get("active"), p.get("server_admin"))
        ),
        "admin.members.list": lambda p: admin.list_members(p.get("project_id")),
        "admin.members.set": mutation(lambda p: admin.set_member(p.get("project_id"), p.get("user"), p.get("role"))),
        "admin.members.remove": mutation(lambda p: admin.remove_member(p.get("project_id"), p.get("user_id"))),
    }
