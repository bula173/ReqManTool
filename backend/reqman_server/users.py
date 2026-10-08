"""The user registry: ``users.json`` in the server's state directory.

A user is created on their first login, keyed by ``(provider, subject)`` —
the identity provider's stable subject id, never the e-mail address, which
can change. Fields (stable order): ``id``, ``name``, ``email``,
``username``, ``provider``, ``subject``, ``server_admin``, ``active``,
``created_at``, ``last_login_at``, ``groups`` (the identity provider's
group names at the last login, for ``server.group_roles``).

Server admins: if ``server.admins`` lists user names / e-mail addresses,
a user matching one of them is made an admin at login (an e-mail address
counts only when the provider does not report it as unverified), as is a
member of one of the ``server.admin_groups``; without ``server.admins``,
the first user ever created becomes the admin. The flag is never removed
automatically; demote someone on the admin page (``set_flags``) or in
``users.json``. ``active: false`` refuses the user's logins and ends their
sessions.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .jsonfile import read_json_file, write_json_file


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class InactiveUserError(Exception):
    """A deactivated user tried to log in."""


@dataclass
class User:
    id: str
    name: str
    email: str
    username: str
    provider: str
    subject: str
    server_admin: bool = False
    active: bool = True
    created_at: str = ""
    last_login_at: str = ""
    groups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or ""),
            email=str(data.get("email") or ""),
            username=str(data.get("username") or ""),
            provider=str(data.get("provider") or ""),
            subject=str(data.get("subject") or ""),
            server_admin=bool(data.get("server_admin", False)),
            active=bool(data.get("active", True)),
            created_at=str(data.get("created_at") or ""),
            last_login_at=str(data.get("last_login_at") or ""),
            groups=[g for g in data.get("groups") or [] if isinstance(g, str)]
            if isinstance(data.get("groups"), list)
            else [],
        )

    def public(self) -> dict:
        """What the browser gets about the logged-in user."""
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "username": self.username,
            "server_admin": self.server_admin,
        }

    def admin_view(self) -> dict:
        """What the server admins' user list shows (never the provider subject)."""
        return {
            **self.public(),
            "provider": self.provider,
            "active": self.active,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
            "groups": list(self.groups),
        }


@dataclass(frozen=True)
class LoginClaims:
    """The identity a provider vouched for at login."""

    provider: str
    subject: str
    name: str
    email: str
    username: str
    email_verified: bool | None = None
    groups: tuple[str, ...] = ()


class UserRegistry:
    def __init__(self, path: Path, admins: tuple[str, ...] = (), admin_groups: tuple[str, ...] = ()) -> None:
        self.path = path
        self.admins = tuple(a.lower() for a in admins)
        self.admin_groups = frozenset(admin_groups)
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._mtime: int | None = None
        self._load()

    def _file_mtime(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def _load(self) -> None:
        mtime = self._file_mtime()
        data = read_json_file(self.path, "User registry") or {}
        raw_users = data.get("users") or []
        if not isinstance(raw_users, list):
            raise ValueError(f"User registry '{self.path}': 'users' must be a list")
        users: dict[str, User] = {}
        for raw in raw_users:
            if isinstance(raw, dict) and raw.get("id"):
                user = User.from_dict(raw)
                users[user.id] = user
        self._users = users
        self._mtime = mtime

    def _reload_if_changed(self) -> None:
        """Pick up a hand edit of ``users.json`` (e.g. ``active: false``); keep the last good state on error."""
        if self._file_mtime() != self._mtime:
            try:
                self._load()
            except ValueError:
                self._mtime = self._file_mtime()

    def get(self, user_id: str) -> User | None:
        with self._lock:
            self._reload_if_changed()
            return self._users.get(user_id)

    def all(self) -> list[User]:
        with self._lock:
            self._reload_if_changed()
            return list(self._users.values())

    def find(self, query: str) -> User | None:
        """The user whose id, e-mail address or user name is ``query`` (case-insensitive), else ``None``.

        Several users with the same e-mail address or user name (different
        providers): ``ValueError`` asking for the id.
        """
        wanted = query.strip().lower()
        if not wanted:
            return None
        with self._lock:
            self._reload_if_changed()
            if query.strip() in self._users:
                return self._users[query.strip()]
            found = [u for u in self._users.values() if wanted in (u.email.lower(), u.username.lower())]
        if len(found) > 1:
            raise ValueError(f"Several users match '{query}'; use the user id")
        return found[0] if found else None

    def set_flags(self, user_id: str, active: bool | None = None, server_admin: bool | None = None) -> User:
        """Activate/deactivate a user and/or set the server admin flag; ``KeyError`` for an unknown user."""
        with self._lock:
            self._reload_if_changed()
            user = self._users.get(user_id)
            if user is None:
                raise KeyError(user_id)
            if active is not None:
                user.active = active
            if server_admin is not None:
                user.server_admin = server_admin
            self._save()
            return user

    def _is_configured_admin(self, claims: LoginClaims) -> bool:
        if self.admin_groups and self.admin_groups.intersection(claims.groups):
            return True
        if not self.admins:
            return False
        if claims.username and claims.username.lower() in self.admins:
            return True
        return bool(claims.email) and claims.email_verified is not False and claims.email.lower() in self.admins

    def login(self, claims: LoginClaims) -> User:
        """The user for ``claims``, created on the first login and updated (name, e-mail) on later ones.

        Raises ``InactiveUserError`` for a deactivated user.
        """
        with self._lock:
            self._reload_if_changed()
            user = next(
                (u for u in self._users.values() if u.provider == claims.provider and u.subject == claims.subject),
                None,
            )
            now = _now_iso()
            if user is None:
                first = not self._users
                user = User(
                    id=uuid.uuid4().hex[:12],
                    name=claims.name or claims.username or claims.email or claims.subject,
                    email=claims.email,
                    username=claims.username,
                    provider=claims.provider,
                    subject=claims.subject,
                    server_admin=self._is_configured_admin(claims) or (first and not self.admins),
                    active=True,
                    created_at=now,
                    groups=list(claims.groups),
                )
                self._users[user.id] = user
            else:
                if not user.active:
                    raise InactiveUserError("This account is deactivated; ask a server administrator.")
                user.name = claims.name or user.name
                user.email = claims.email or user.email
                user.username = claims.username or user.username
                user.groups = list(claims.groups)
                if self._is_configured_admin(claims):
                    user.server_admin = True
            user.last_login_at = now
            self._save()
            return user

    def _save(self) -> None:
        write_json_file(self.path, {"users": [u.to_dict() for u in self._users.values()]})
        self._mtime = self._file_mtime()
