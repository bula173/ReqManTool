"""Project memberships: ``memberships.json`` in the server's state directory.

Server state, not project data: the projects (and their git repositories)
stay edition-agnostic, so a project can move between a local and a server
installation unchanged. One entry per (project, user), in a stable order
(by project id, then user id). Fields: ``project_id``, ``user_id``,
``role`` (``viewer`` / ``editor`` / ``project-admin``), ``added_at``,
``added_by`` (user id, empty when added by the server itself).

A project's entries are removed with the project (``forget_project``), so a
new project that reuses the id starts without members. A hand edit of the
file is picked up on the next read (like ``users.json``).
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .jsonfile import read_json_file, write_json_file
from .settings import ROLES


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Membership:
    project_id: str
    user_id: str
    role: str
    added_at: str = ""
    added_by: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Membership | None":
        project_id, user_id, role = data.get("project_id"), data.get("user_id"), data.get("role")
        if not (isinstance(project_id, str) and project_id and isinstance(user_id, str) and user_id):
            return None
        if role not in ROLES:
            return None
        return cls(
            project_id=project_id,
            user_id=user_id,
            role=role,
            added_at=str(data.get("added_at") or ""),
            added_by=str(data.get("added_by") or ""),
        )


class MembershipRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], Membership] = {}
        self._mtime: int | None = None
        self._load()

    def _file_mtime(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def _load(self) -> None:
        mtime = self._file_mtime()
        data = read_json_file(self.path, "Membership file") or {}
        raw = data.get("memberships") or []
        if not isinstance(raw, list):
            raise ValueError(f"Membership file '{self.path}': 'memberships' must be a list")
        entries: dict[tuple[str, str], Membership] = {}
        for item in raw:
            membership = Membership.from_dict(item) if isinstance(item, dict) else None
            if membership is not None:
                entries[(membership.project_id, membership.user_id)] = membership
        self._entries = entries
        self._mtime = mtime

    def _reload_if_changed(self) -> None:
        if self._file_mtime() != self._mtime:
            try:
                self._load()
            except ValueError:
                self._mtime = self._file_mtime()  # keep the last good state

    def _save(self) -> None:
        ordered = [self._entries[key].to_dict() for key in sorted(self._entries)]
        write_json_file(self.path, {"memberships": ordered})
        self._mtime = self._file_mtime()

    # --- reads ------------------------------------------------------------------------

    def role(self, project_id: str, user_id: str) -> str | None:
        with self._lock:
            self._reload_if_changed()
            entry = self._entries.get((project_id, user_id))
            return entry.role if entry else None

    def of_project(self, project_id: str) -> list[Membership]:
        with self._lock:
            self._reload_if_changed()
            return [m for (pid, _), m in sorted(self._entries.items()) if pid == project_id]

    def of_user(self, user_id: str) -> dict[str, str]:
        """``{project id: role}`` of a user's memberships."""
        with self._lock:
            self._reload_if_changed()
            return {pid: m.role for (pid, uid), m in sorted(self._entries.items()) if uid == user_id}

    # --- changes ----------------------------------------------------------------------

    def set_role(self, project_id: str, user_id: str, role: str, added_by: str = "") -> Membership:
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}, got {role!r}")
        with self._lock:
            self._reload_if_changed()
            entry = self._entries.get((project_id, user_id))
            if entry is None:
                entry = Membership(project_id, user_id, role, added_at=_now_iso(), added_by=added_by)
                self._entries[(project_id, user_id)] = entry
            elif entry.role == role:
                return entry
            else:
                entry.role = role
            self._save()
            return entry

    def remove(self, project_id: str, user_id: str) -> bool:
        with self._lock:
            self._reload_if_changed()
            if self._entries.pop((project_id, user_id), None) is None:
                return False
            self._save()
            return True

    def forget_project(self, project_id: str) -> None:
        with self._lock:
            self._reload_if_changed()
            keys = [key for key in self._entries if key[0] == project_id]
            for key in keys:
                del self._entries[key]
            if keys:
                self._save()
