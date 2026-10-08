"""Roles and the permission guard of the server edition.

A user's role in a project comes from

- their permission on the project's git repository, when the project's
  remote lives on the GitLab / GitHub instance the user logged in with
  (``repo_access.py``; it then wins over everything below), else
- the highest of their membership (``memberships.json``, see
  ``memberships.py``) and every ``server.group_roles`` rule naming one of
  their identity provider groups (recorded at login, ``User.groups``) and
  the project (or ``*``).

Roles, lowest first: ``viewer`` (read), ``reviewer`` (read, and sign off
requirements: change only their status, see the status guards of
``reqman.service``), ``editor`` (read and change the project's content),
``project-admin`` (also baselines, lock/unlock, attribute definitions,
views, link configurations, approval, remove, purge, parent, git history
and remote settings, publish, and the project's members). Server admins
may do everything, in every project.

A refusal names the missing role and where the role comes from, e.g.
"needs Developer on gitlab.example.com/rbc/rbc-gp".

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

from dataclasses import dataclass

from reqman.extensions import (
    ACTION_LIBRARY_WRITE,
    ACTION_PROJECT_ADMIN,
    ACTION_PROJECT_APPROVE,
    ACTION_PROJECT_CREATE,
    ACTION_PROJECT_READ,
    ACTION_PROJECT_REVIEW,
    ACTION_PROJECT_WRITE,
    AccessDeniedError,
    RequestIdentity,
)

from .memberships import MembershipRegistry
from .repo_access import RepoAccess, RepoRole
from .settings import ROLES, ServerSettings
from .users import User, UserRegistry

logger = logging.getLogger("reqman_server.permissions")

ROLE_RANK = {role: rank for rank, role in enumerate(ROLES, start=1)}
# The role each project action needs.
ACTION_ROLE = {
    ACTION_PROJECT_READ: "viewer",
    ACTION_PROJECT_REVIEW: "reviewer",
    ACTION_PROJECT_WRITE: "editor",
    ACTION_PROJECT_ADMIN: "project-admin",
    ACTION_PROJECT_APPROVE: "project-admin",
}
ACTION_RANK = {action: ROLE_RANK[role] for action, role in ACTION_ROLE.items()}
ACTION_WORDS = {
    ACTION_PROJECT_READ: "see it",
    ACTION_PROJECT_REVIEW: "change requirements",
    ACTION_PROJECT_WRITE: "change its content",
    ACTION_PROJECT_ADMIN: "administer it (baselines, locks, attributes, views, link configurations, settings)",
    ACTION_PROJECT_APPROVE: "approve requirements",
}
ROLE_LABELS = {"viewer": "viewer", "reviewer": "reviewer", "editor": "editor", "project-admin": "project admin"}


@dataclass(frozen=True)
class ProjectAccess:
    """A user's role in a project and where it comes from."""

    role: str | None
    # ``repo`` (repository permission), ``member`` (membership), ``group`` (group rule), or None.
    source: str | None
    repo: RepoRole | None = None


def highest(roles: list[str | None]) -> str | None:
    known = [r for r in roles if r in ROLE_RANK]
    return max(known, key=ROLE_RANK.__getitem__) if known else None


class Permissions:
    """Role lookups shared by the guard, ``/auth/me`` and the admin pages."""

    def __init__(
        self,
        settings: ServerSettings,
        users: UserRegistry,
        memberships: MembershipRegistry,
        repo: RepoAccess | None = None,
    ) -> None:
        self.settings = settings
        self.users = users
        self.memberships = memberships
        self.repo = repo

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

    def in_app_access(self, user: User, project_id: str) -> ProjectAccess:
        member = self.memberships.role(project_id, user.id)
        role = highest([member, self.group_role(user, project_id)])
        return ProjectAccess(role, None if role is None else "member" if role == member else "group")

    def access(self, user: User, project_id: str) -> ProjectAccess:
        """The role and its source: the repository permission of a bound project, else in-app."""
        if self.repo is not None:
            repo = self.repo.role(user, project_id)
            if repo is not None:
                return ProjectAccess(repo.role, "repo", repo)
        return self.in_app_access(user, project_id)

    def role(self, user: User, project_id: str) -> str | None:
        return self.access(user, project_id).role

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
        sources: dict[str, dict[str, Any]] = {}
        if self.repo is not None and self.repo.enabled and self.repo.project_ids is not None:
            try:
                project_ids = self.repo.project_ids()
            except (OSError, ValueError):
                project_ids = []
            for project_id in project_ids:
                repo = self.repo.role(user, project_id)
                if repo is not None:
                    # Bound: the repository permission is the role, even "none" (null).
                    roles[project_id] = repo.role  # type: ignore[assignment]
                    sources[project_id] = repo.public()
        return {
            "server_admin": user.server_admin,
            "can_create_projects": self.can_create_projects(user),
            "roles": dict(sorted(roles.items())),
            "default_role": self.default_role(user),
            # Projects whose roles come from repository permissions: the repository, permission, errors.
            "repo_roles": dict(sorted(sources.items())),
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
            access = self.permissions.access(user, project_id)
            role, repo = access.role, access.repo
            if role is not None and ROLE_RANK[role] >= needed:
                return
            words = ACTION_WORDS[action]
            if repo is not None:
                if repo.error and role is None:
                    raise AccessDeniedError(
                        f"Your access to project '{project_id}' comes from {repo.binding.display} "
                        f"and cannot be checked: {repo.error}"
                    )
                have = repo.describe()
                if role is None:
                    raise AccessDeniedError(
                        f"You have no access to project '{project_id}': it {repo.needs('viewer')} (you have {have})"
                    )
                raise AccessDeniedError(
                    f"You may not {words} in project '{project_id}': it {repo.needs(ACTION_ROLE[action])} "
                    f"(you have {have}, {ROLE_LABELS[role]})"
                )
            if role is None:
                raise AccessDeniedError(f"You have no access to project '{project_id}'")
            source = "group rule" if access.source == "group" else "membership"
            hint = " (a reviewer may change only requirement statuses)" if role == "reviewer" else ""
            raise AccessDeniedError(
                f"Your role in project '{project_id}' is {ROLE_LABELS[role]} ({source}): you may not {words}; "
                f"it needs the {ROLE_LABELS[ACTION_ROLE[action]]} role{hint}"
            )
        if action == ACTION_PROJECT_CREATE:
            if self.permissions.can_create_projects(user):
                return
            raise AccessDeniedError("Only a server administrator can create projects on this server")
        if action == ACTION_LIBRARY_WRITE:
            return
        raise AccessDeniedError("Only a server administrator can do this")


class MembershipEvents:
    """``ProjectEvents`` of the server edition: creator → project admin; removal → memberships
    (and module edit locks) forgotten."""

    def __init__(self, memberships: MembershipRegistry, locks: Any = None, repo: RepoAccess | None = None) -> None:
        self.memberships = memberships
        # ``repo_access.RepoAccess`` (optional): a removed project's cached repository roles are dropped.
        self.repo = repo
        # ``module_locks.ModuleLockRegistry`` (optional): a removed project's locks are dropped too.
        self.locks = locks

    def project_created(self, identity: RequestIdentity | None, project_id: str) -> None:
        if self.repo is not None:
            self.repo.forget(project_id=project_id)
        if identity is not None:
            self.memberships.set_role(project_id, identity.user_id, "project-admin", added_by=identity.user_id)
            logger.info("User %s created project %s (project admin)", identity.user_id, project_id)

    def project_removed(self, identity: RequestIdentity | None, project_id: str) -> None:
        self.memberships.forget_project(project_id)
        if self.repo is not None:
            self.repo.forget(project_id=project_id)
        if self.locks is not None:
            self.locks.forget_project(project_id)
        logger.info("Project %s removed by %s; its memberships are forgotten", project_id,
                    identity.user_id if identity else "?")
