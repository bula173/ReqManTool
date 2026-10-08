"""The server edition's audit log: an append-only JSON-lines file in the state directory.

One line per event (``audit.jsonl``, owner-only permissions), never
rewritten by the server; a line that cannot be parsed (a hand edit, a
write cut short by a crash) is skipped when reading. Each entry:

``{"time": ISO UTC, "action": ..., "user_id": ..., "user_name": ...,
"project_id"?: ..., "target_user_id"?: ..., "target_user_name"?: ...,
"ip"?: ..., "details"?: {...}}`` (keys without a value are left out).

Actions:

- ``auth.login`` (``details.provider``, ``details.first_login``),
  ``auth.login_failed`` (``details.provider``, ``details.reason``; only for
  a callback that matched a login started in this browser, so random
  requests to the public callback cannot fill the log), ``auth.logout``.
- ``admin.user_update`` (``details.active`` / ``details.server_admin``:
  the flags set), ``admin.member_set`` (``details.role``,
  ``details.previous_role``), ``admin.member_remove``
  (``details.previous_role``).
- ``module.lock_break`` (``details.module_id``; the previous owner is the
  target user).
- From the core (``reqman.extensions.AUDIT_*``, through ``HOOKS.audit``):
  ``project.create``, ``project.lock`` (``details.reason``,
  ``details.baseline``), ``project.unlock``, ``project.remove``
  (``details.name``), ``project.purge`` (``details.requirements``).

Reading (``GET /admin/audit``, WebSocket ``admin.audit.list``; server
admins only) goes backwards from the end of the file: newest first, in
pages of ``limit`` entries; ``before`` is the byte offset where the
previous page ended (``next_before`` of the previous answer), so pages
stay stable while new entries are appended. Optional filters: ``action``
(an action, or a prefix ending in ``.`` such as ``auth.``), ``user_id``
(actor or target) and ``project_id``.

There is no rotation: an administrator may move the file away (a new one
is started on the next event).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException

from reqman.extensions import AccessDeniedError, RequestIdentity, current_identity

logger = logging.getLogger("reqman_server.audit")

# Actions of the server edition (the core's are ``reqman.extensions.AUDIT_*``).
AUDIT_LOGIN = "auth.login"
AUDIT_LOGIN_FAILED = "auth.login_failed"
AUDIT_LOGOUT = "auth.logout"
AUDIT_USER_UPDATE = "admin.user_update"
AUDIT_MEMBER_SET = "admin.member_set"
AUDIT_MEMBER_REMOVE = "admin.member_remove"
AUDIT_MODULE_LOCK_BREAK = "module.lock_break"

DEFAULT_PAGE = 100
MAX_PAGE = 500
# Longest text value kept in an entry (reasons, names): the log must not grow without bound per event.
MAX_TEXT = 500
_CHUNK = 64 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> Any:
    """``value`` with long strings cut (recursively for dicts and lists)."""
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[: MAX_TEXT - 1] + "…"
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class AuditLog:
    """Appends audit entries; the core's ``AuditTrail`` (``HOOKS.audit``) and the edition's own recorder."""

    def __init__(self, path: Path, users: Any = None) -> None:
        self.path = path
        # ``users.UserRegistry`` (optional): names of the users an entry mentions by id.
        self.users = users
        self._lock = threading.Lock()

    # --- writing -----------------------------------------------------------------------

    def _user_name(self, user_id: str | None) -> str | None:
        if not user_id or self.users is None:
            return None
        user = self.users.get(user_id)
        if user is None:
            return None
        return user.name or user.username or user.email or None

    def record(
        self,
        identity: RequestIdentity | None,
        action: str,
        project_id: str | None = None,
        details: dict[str, Any] | None = None,
        *,
        user_id: str | None = None,
        user_name: str | None = None,
        target_user_id: str | None = None,
        ip: str | None = None,
    ) -> None:
        """Append one entry. The actor is ``identity`` (or ``user_id``/``user_name``, e.g. at login)."""
        actor_id = user_id or (identity.user_id if identity is not None else None)
        actor_name = user_name or (identity.name if identity is not None else None) or self._user_name(actor_id)
        entry: dict[str, Any] = {
            "time": _now_iso(),
            "action": action,
            "user_id": actor_id,
            "user_name": actor_name,
            "project_id": project_id,
            "target_user_id": target_user_id,
            "target_user_name": self._user_name(target_user_id),
            "ip": ip,
            "details": details or None,
        }
        line = json.dumps(_clean(entry), ensure_ascii=False, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)

    def safe_record(self, *args: Any, **kwargs: Any) -> None:
        """``record`` for the edition's own events: a failure is logged, never raised (the action happened)."""
        try:
            self.record(*args, **kwargs)
        except Exception:  # noqa: BLE001 - never fail a login/admin action because of the log
            logger.exception("Could not write the audit log %s", self.path)

    # --- reading -----------------------------------------------------------------------

    def read(
        self,
        limit: Any = None,
        before: Any = None,
        action: Any = None,
        user_id: Any = None,
        project_id: Any = None,
    ) -> dict[str, Any]:
        """A page of entries, newest first (see the module docstring)."""
        if limit is None:
            limit = DEFAULT_PAGE
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE:
            raise ValueError(f"limit must be a number from 1 to {MAX_PAGE}")
        if before is not None and (isinstance(before, bool) or not isinstance(before, int) or before < 0):
            raise ValueError("before must be a byte offset (next_before of the previous page)")
        for name, value in (("action", action), ("user_id", user_id), ("project_id", project_id)):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
        action = action or None
        user_id = user_id or None
        project_id = project_id or None

        def wanted(entry: dict[str, Any]) -> bool:
            if action is not None:
                name = entry.get("action")
                if not isinstance(name, str):
                    return False
                if action.endswith(".") and not name.startswith(action):
                    return False
                if not action.endswith(".") and name != action:
                    return False
            if user_id is not None and user_id not in (entry.get("user_id"), entry.get("target_user_id")):
                return False
            return project_id is None or entry.get("project_id") == project_id

        entries: list[dict[str, Any]] = []
        next_before: int | None = None
        try:
            handle = open(self.path, "rb")
        except FileNotFoundError:
            return {"entries": [], "next_before": None}
        with handle:
            size = os.fstat(handle.fileno()).st_size
            end = size if before is None else min(before, size)
            for start, raw in self._lines_backwards(handle, end):
                try:
                    entry = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict) or not wanted(entry):
                    continue
                if len(entries) == limit:
                    # One more match exists: the next page ends where this line ends.
                    next_before = start + len(raw) + 1
                    break
                entries.append(entry)
        return {"entries": entries, "next_before": next_before}

    @staticmethod
    def _lines_backwards(handle, end: int):
        """``(start offset, line bytes)`` of the complete lines before ``end``, last first."""
        position = end
        buffer = b""
        while position > 0:
            size = min(_CHUNK, position)
            position -= size
            handle.seek(position)
            buffer = handle.read(size) + buffer
            lines = buffer.split(b"\n")
            # The first piece may be cut; keep it for the next round (unless at the start).
            buffer = lines[0]
            offset = position + len(lines[0]) + 1
            starts = []
            for line in lines[1:]:
                starts.append((offset, line))
                offset += len(line) + 1
            for start, line in reversed(starts):
                if line.strip():
                    yield start, line
        if buffer.strip():
            yield 0, buffer


class AuditService:
    """The admin-only read API over the log."""

    def __init__(self, log: AuditLog, permissions: Any) -> None:
        self.log = log
        self.permissions = permissions

    def list(self, params: dict[str, Any]) -> dict[str, Any]:
        user = self.permissions.active_user(current_identity())
        if user is None:
            raise AccessDeniedError("Not logged in")
        if not user.server_admin:
            raise AccessDeniedError("Only a server administrator can read the audit log")
        return self.log.read(
            params.get("limit"),
            params.get("before"),
            params.get("action"),
            params.get("user_id"),
            params.get("project_id"),
        )


def build_audit_router(audit: AuditService) -> APIRouter:
    router = APIRouter()

    @router.get("/admin/audit")
    def list_audit(
        limit: int | None = None,
        before: int | None = None,
        action: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
    ):
        params = {"limit": limit, "before": before, "action": action, "user_id": user_id, "project_id": project_id}
        try:
            return audit.list(params)
        except AccessDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


def build_audit_rpc_methods(audit: AuditService) -> dict[str, Callable[[dict], Any | Awaitable[Any]]]:
    """``admin.audit.list`` for ``HOOKS.rpc_methods`` (errors are mapped by the core dispatcher)."""
    return {"admin.audit.list": lambda p: audit.list(p if isinstance(p, dict) else {})}
