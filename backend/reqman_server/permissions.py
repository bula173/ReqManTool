"""Roles and the permission guard of the server edition.

A user's role in a project is the highest of

- their membership (``memberships.json``, see ``memberships.py``), and
- every ``server.group_roles`` rule naming one of their identity provider
  groups (recorded at login, ``User.groups``) and the project (or ``*``).

Roles, lowest first: ``viewer`` (read), ``editor`` (read and change the
project's data), ``project-admin`` (also lock/unlock, remove, purge,
parent, git history and remote settings, publish, and the project's
members). Server admins may do everything, in every project.

``MembershipGuard`` is installed as ``HOOKS.permission_guard``; the core
asks it at its choke points (``reqman.service``). Server-wide actions:
``project.create`` per ``server.project_creation``; ``library.write``
(templates, prompts) for every user; anything else (connections, remote
URLs, the data folder, unknown actions) for server admins only. The user
is looked up on every check, so a deactivation, a new server admin flag
or a membership change applies at once, also to open WebSocket
connections.

``MembershipEvents`` (``HOOKS.project_events``) makes the creator of a
project its project admin and forgets the memberships of a removed
project.
"""

from __future__ import annotations

import logging
from typing import Any

from reqman.extensions import (
    ACTION_LIBRARY_WRITE,
    ACTION_PROJECT_ADMIN,
    ACTION_PROJECT_CREATE,
    ACTION_PROJECT_READ,
    ACTION_PROJECT_WRITE,
    AccessDeniedError,
    RequestIdentity,
)

from .memberships import MembershipRegistry
from .settings import ROLES, ServerSettings
from .users import User, UserRegistry

logger = logging.getLogger("reqman_server.permissions")

ROLE_RANK = {role: rank for rank, role in enumerate(ROLES, start=1)}
ACTION_RANK = {ACTION_PROJECT_READ: 1, ACTION_PROJECT_WRITE: 2, ACTION_PROJECT_ADMIN: 3}
ACTION_WORDS = {
    ACTION_PROJECT_READ: "see it",
    ACTION_PROJECT_WRITE: "change it (editor role needed)",
    ACTION_PROJECT_ADMIN: "administer it (project admin role needed)",
}


def highest(roles: list[str | None]) -> str | None:
    known = [r for r in roles if r in ROLE_RANK]
    return max(known, key=ROLE_RANK.__getitem__) if known else None


class Permissions:
    """Role lookups shared by the guard, ``/auth/me`` and the admin pages."""

    def __init__(self, settings: ServerSettings, users: UserRegistry, memberships: MembershipRegistry) -> None:
        self.settings = settings
        self.users = users
        self.memberships = memberships

    def active_user(self, identity: RequestIdentity | None) -> User | None:
        if identity is None:
            return None
        user = self.users.get(identity.user_id)
        return user if user is not None and user.active else None

    def group_role(self, user: User, project_id: str) -> str | None:
        groups = set(user.groups)
        return highest([r.role for r in self.settings.group_roles if r.group in groups and r.applies_to(project_id)])

    def default_role(self, user: User) -> str | None:
        """The role group rules give the user in every project (``"projects": ["*"]``)."""
        groups = set(user.groups)
        return highest([r.role for r in self.settings.group_roles if r.group in groups and "*" in r.projects])

    def role(self, user: User, project_id: str) -> str | None:
        return highest([self.memberships.role(project_id, user.id), self.group_role(user, project_id)])

    def can_create_projects(self, user: User) -> bool:
        return user.server_admin or self.settings.project_creation == "everyone"

    def summary(self, user: User) -> dict[str, Any]:
        """What the browser needs to show the user's roles (``/auth/me``, ``/auth/permissions``)."""
        roles = dict(self.memberships.of_user(user.id))
        groups = set(user.groups)
        for rule in self.settings.group_roles:
            if rule.group not in groups:
                continue
            for project_id in rule.projects:
                if project_id != "*":
                    roles[project_id] = highest([roles.get(project_id), rule.role]) or rule.role
        return {
            "server_admin": user.server_admin,
            "can_create_projects": self.can_create_projects(user),
            "roles": dict(sorted(roles.items())),
            "default_role": self.default_role(user),
        }


class MembershipGuard:
    """``PermissionGuard`` of the server edition (see the module docstring)."""

    def __init__(self, permissions: Permissions) -> None:
        self.permissions = permissions

    def check(self, identity: RequestIdentity | None, action: str, project_id: str | None = None) -> None:
        user = self.permissions.active_user(identity)
        if user is None:
            raise AccessDeniedError("Not logged in")
        if user.server_admin:
            return
        needed = ACTION_RANK.get(action)
        if needed is not None:
            if not project_id:
                raise AccessDeniedError("No project given")
            role = self.permissions.role(user, project_id)
            if role is None:
                raise AccessDeniedError(f"You have no access to project '{project_id}'")
            if ROLE_RANK[role] < needed:
                raise AccessDeniedError(
                    f"Your role in project '{project_id}' is {role}: you may not {ACTION_WORDS[action]}"
                )
            return
        if action == ACTION_PROJECT_CREATE:
            if self.permissions.can_create_projects(user):
                return
            raise AccessDeniedError("Only a server administrator can create projects on this server")
        if action == ACTION_LIBRARY_WRITE:
            return
        raise AccessDeniedError("Only a server administrator can do this")


class MembershipEvents:
    """``ProjectEvents`` of the server edition: creator → project admin; removal → memberships forgotten."""

    def __init__(self, memberships: MembershipRegistry) -> None:
        self.memberships = memberships

    def project_created(self, identity: RequestIdentity | None, project_id: str) -> None:
        if identity is not None:
            self.memberships.set_role(project_id, identity.user_id, "project-admin", added_by=identity.user_id)
            logger.info("User %s created project %s (project admin)", identity.user_id, project_id)

    def project_removed(self, identity: RequestIdentity | None, project_id: str) -> None:
        self.memberships.forget_project(project_id)
        logger.info("Project %s removed by %s; its memberships are forgotten", project_id,
                    identity.user_id if identity else "?")
