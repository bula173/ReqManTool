"""Module edit locks ("check out for editing") of the server edition.

An editor checks out a module: an exclusive lock with an owner, the time it
was taken, an optional reason and the time of the owner's last activity.
While it is held, every write to the module's content by anyone else is
refused by the core (``ModuleLockedError``: REST 423, WebSocket -32005);
the owner's own writes refresh it. A lock nobody used for
``server.module_lock_idle_minutes`` (default 60) expires. With
``server.require_module_checkout: true`` a module must be checked out
before anyone can change it; by default unlocked modules stay freely
editable (a checkout is opt-in, like an exclusive edit in DOORS).

Storage: ``module_locks.json`` in the server's state directory (server
state, not project data: projects and their git repositories stay
edition-agnostic). Fields per lock: ``project_id``, ``module_id``,
``user_id``, ``since`` and ``last_activity`` (Unix seconds), ``reason``.
Activity is written at most once a minute; a hand edit of the file is
picked up on the next read.

Releasing: the owner (``unlock``); a project admin of the project or a
server admin (``break_lock``); the owner's logout when no other session of
theirs is left; the owner's deactivation; the project's removal; expiry.

REST (every route needs a session; state-changing ones the CSRF token):

- ``GET /projects/{project_id}/module-locks``: ``{project_id,
  require_checkout, idle_minutes, locks: [lock]}`` (readers of the project).
- ``PUT /projects/{project_id}/modules/{module_id}/lock`` ``{"reason"?: str}``:
  check out (editors; again by the owner: refresh, new reason).
- ``DELETE /projects/{project_id}/modules/{module_id}/lock``: release one's own lock.
- ``POST /projects/{project_id}/modules/{module_id}/lock/break``: break
  anyone's lock (project admins, server admins).

WebSocket methods (``HOOKS.rpc_methods``, same rules and results):
``module.locks``, ``module.lock``, ``module.unlock``, ``module.break_lock``.
Every change broadcasts ``{"event": "module_locks_changed", "project_id":
...}`` (sent only to users who may read the project).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from reqman.extensions import (
    ACTION_PROJECT_ADMIN,
    ACTION_PROJECT_READ,
    ACTION_PROJECT_WRITE,
    AccessDeniedError,
    ModuleLockedError,
    PermissionGuard,
    RequestIdentity,
    current_identity,
)

from .audit import AUDIT_MODULE_LOCK_BREAK
from .jsonfile import read_json_file, write_json_file
from .users import UserRegistry

logger = logging.getLogger("reqman_server.module_locks")

MODULE_LOCKS_CHANGED = "module_locks_changed"
MAX_LOCK_REASON_LENGTH = 500
# How often the owner's activity (``last_activity``) is written to the file at most.
_PERSIST_ACTIVITY_EVERY = 60.0
_UNSAFE_ID = re.compile(r"[/\\\x00]")


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _checked_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value or value in (".", "..") or _UNSAFE_ID.search(value):
        raise ValueError(f"{what} must be an id")
    return value


def _checked_reason(reason: Any) -> str | None:
    """``None`` (keep / none), or the reason with whitespace collapsed."""
    if reason is None:
        return None
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    reason = " ".join(reason.split())
    if len(reason) > MAX_LOCK_REASON_LENGTH:
        raise ValueError(f"The reason is too long: at most {MAX_LOCK_REASON_LENGTH} characters")
    return reason


@dataclass
class ModuleLock:
    project_id: str
    module_id: str
    user_id: str
    since: float
    last_activity: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ModuleLock | None":
        try:
            lock = cls(
                project_id=str(data["project_id"]),
                module_id=str(data["module_id"]),
                user_id=str(data["user_id"]),
                since=float(data["since"]),
                last_activity=float(data.get("last_activity", data["since"])),
                reason=str(data.get("reason") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return lock if lock.project_id and lock.module_id and lock.user_id else None


class ModuleLockRegistry:
    """The locks: in memory, mirrored to ``module_locks.json``; thread-safe."""

    def __init__(
        self, path: Path | None, idle_seconds: float, clock: Callable[[], float] = time.time
    ) -> None:
        self.path = path
        self.idle = idle_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], ModuleLock] = {}
        self._mtime: int | None = None
        self._last_persist = 0.0
        self._load()

    # --- file ---------------------------------------------------------------------------

    def _file_mtime(self) -> int | None:
        if self.path is None:
            return None
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def _load(self) -> None:
        if self.path is None:
            return
        mtime = self._file_mtime()
        data = read_json_file(self.path, "Module lock file") or {}
        raw = data.get("locks") or []
        if not isinstance(raw, list):
            raise ValueError(f"Module lock file '{self.path}': 'locks' must be a list")
        entries: dict[tuple[str, str], ModuleLock] = {}
        for item in raw:
            lock = ModuleLock.from_dict(item) if isinstance(item, dict) else None
            if lock is not None:
                entries[(lock.project_id, lock.module_id)] = lock
        self._entries = entries
        self._mtime = mtime

    def _reload_if_changed(self) -> None:
        if self.path is not None and self._file_mtime() != self._mtime:
            try:
                self._load()
            except ValueError:
                self._mtime = self._file_mtime()  # keep the last good state

    def _save(self, force: bool = True) -> None:
        if self.path is None:
            return
        now = self.clock()
        if not force and now - self._last_persist < _PERSIST_ACTIVITY_EVERY:
            return
        self._last_persist = now
        ordered = [self._entries[key].to_dict() for key in sorted(self._entries)]
        write_json_file(self.path, {"locks": ordered})
        self._mtime = self._file_mtime()

    # --- state ----------------------------------------------------------------------------

    def _alive(self, lock: ModuleLock, now: float) -> bool:
        return now - lock.last_activity < self.idle

    def expires_at(self, lock: ModuleLock) -> float:
        return lock.last_activity + self.idle

    def _current(self, project_id: str, module_id: str) -> ModuleLock | None:
        """The live lock (an expired one is dropped). Call with ``self._lock`` held."""
        self._reload_if_changed()
        lock = self._entries.get((project_id, module_id))
        if lock is not None and not self._alive(lock, self.clock()):
            del self._entries[(project_id, module_id)]
            logger.info("Lock of module %s/%s by %s expired", project_id, module_id, lock.user_id)
            self._save()
            return None
        return lock

    def get(self, project_id: str, module_id: str) -> ModuleLock | None:
        with self._lock:
            return self._current(project_id, module_id)

    def of_project(self, project_id: str) -> list[ModuleLock]:
        with self._lock:
            self._reload_if_changed()
            keys = sorted(key for key in self._entries if key[0] == project_id)
            return [lock for key in keys if (lock := self._current(*key)) is not None]

    def touch(self, project_id: str, module_id: str, user_id: str) -> None:
        """The owner was active in the module: restart the idle timer."""
        with self._lock:
            lock = self._current(project_id, module_id)
            if lock is not None and lock.user_id == user_id:
                lock.last_activity = self.clock()
                self._save(force=False)

    def acquire(self, project_id: str, module_id: str, user_id: str, reason: str | None) -> tuple[ModuleLock, bool]:
        """Take the lock (or refresh one's own): ``(lock, new)``. ``ModuleLockedError`` if someone else holds it."""
        with self._lock:
            lock = self._current(project_id, module_id)
            now = self.clock()
            if lock is not None and lock.user_id != user_id:
                raise ModuleLockedError(lock.user_id)  # the caller words the message
            if lock is not None:
                lock.last_activity = now
                if reason is not None:
                    lock.reason = reason
                self._save()
                return lock, False
            lock = ModuleLock(project_id, module_id, user_id, since=now, last_activity=now, reason=reason or "")
            self._entries[(project_id, module_id)] = lock
            self._save()
            return lock, True

    def release(self, project_id: str, module_id: str) -> ModuleLock | None:
        """Remove the lock (whoever holds it); the removed live lock or ``None``."""
        with self._lock:
            lock = self._current(project_id, module_id)
            if self._entries.pop((project_id, module_id), None) is not None:
                self._save()
            return lock

    def release_user(self, user_id: str) -> list[str]:
        """Remove every lock of a user; the ids of the projects whose locks changed."""
        with self._lock:
            self._reload_if_changed()
            keys = [key for key, lock in self._entries.items() if lock.user_id == user_id]
            for key in keys:
                del self._entries[key]
            if keys:
                self._save()
            return sorted({project_id for project_id, _ in keys})

    def forget_project(self, project_id: str) -> None:
        with self._lock:
            self._reload_if_changed()
            keys = [key for key in self._entries if key[0] == project_id]
            for key in keys:
                del self._entries[key]
            if keys:
                self._save()


class ModuleLocks:
    """The lock rules: the core's ``ModuleLockService`` (``check_write``) and the lock/unlock API."""

    def __init__(
        self,
        registry: ModuleLockRegistry,
        guard: PermissionGuard,
        users: UserRegistry,
        require_checkout: bool = False,
        project_exists: Callable[[str], bool] | None = None,
        module_exists: Callable[[str, str], bool] | None = None,
        audit: Any = None,
    ) -> None:
        self.registry = registry
        # ``audit.AuditLog`` (optional): broken locks are written to the audit log.
        self.audit = audit
        self.guard = guard
        self.users = users
        self.require_checkout = require_checkout
        self.project_exists = project_exists
        self.module_exists = module_exists

    # --- the core's hook ------------------------------------------------------------------

    def check_write(self, identity: RequestIdentity | None, project_id: str, module_id: str) -> None:
        """Refuse a write to a module someone else checked out (or not checked out, if required)."""
        lock = self.registry.get(project_id, module_id)
        if lock is None:
            if self.require_checkout and self._module_known(project_id, module_id):
                raise ModuleLockedError(
                    f"Module '{module_id}' must be checked out for editing before it can be changed "
                    "(Check out for editing)."
                )
            return
        if identity is not None and lock.user_id == identity.user_id:
            self.registry.touch(project_id, module_id, identity.user_id)
            return
        raise ModuleLockedError(self._locked_message(lock))

    def _module_known(self, project_id: str, module_id: str) -> bool:
        # A write to a module that does not exist fails with its own 404 instead.
        return self.module_exists is None or self.module_exists(project_id, module_id)

    # --- descriptions ---------------------------------------------------------------------

    def _owner_name(self, user_id: str) -> str:
        user = self.users.get(user_id)
        if user is None:
            return user_id
        return user.name or user.username or user.email or user_id

    def _locked_message(self, lock: ModuleLock) -> str:
        reason = f" ({lock.reason})" if lock.reason else ""
        return (
            f"Module '{lock.module_id}' is checked out for editing by {self._owner_name(lock.user_id)} "
            f"since {_iso(lock.since)}{reason}. Ask them to release it, or a project admin to break the lock."
        )

    def view(self, lock: ModuleLock) -> dict[str, Any]:
        user = self.users.get(lock.user_id)
        return {
            "project_id": lock.project_id,
            "module_id": lock.module_id,
            "user_id": lock.user_id,
            "user_name": self._owner_name(lock.user_id),
            "user_email": user.email if user else "",
            "since": _iso(lock.since),
            "last_activity": _iso(lock.last_activity),
            "expires_at": _iso(self.registry.expires_at(lock)),
            "reason": lock.reason,
        }

    # --- API --------------------------------------------------------------------------------

    def _identity(self, project_id: Any, action: str) -> RequestIdentity:
        project_id = _checked_id(project_id, "project_id")
        identity = current_identity()
        self.guard.check(identity, action, project_id)
        if identity is None:
            raise AccessDeniedError("Not logged in")
        if self.project_exists is not None and not self.project_exists(project_id):
            raise FileNotFoundError(f"Project '{project_id}' not found")
        return identity

    def list_locks(self, project_id: Any) -> dict[str, Any]:
        self._identity(project_id, ACTION_PROJECT_READ)
        return {
            "project_id": project_id,
            "require_checkout": self.require_checkout,
            "idle_minutes": round(self.registry.idle / 60, 2),
            "locks": [self.view(lock) for lock in self.registry.of_project(project_id)],
        }

    def lock(self, project_id: Any, module_id: Any, reason: Any = None) -> dict[str, Any]:
        identity = self._identity(project_id, ACTION_PROJECT_WRITE)
        module_id = _checked_id(module_id, "module_id")
        reason = _checked_reason(reason)
        if self.module_exists is not None and not self.module_exists(project_id, module_id):
            raise FileNotFoundError(f"Module '{module_id}' not found in project '{project_id}'")
        try:
            lock, new = self.registry.acquire(project_id, module_id, identity.user_id, reason)
        except ModuleLockedError:
            held = self.registry.get(project_id, module_id)
            raise ModuleLockedError(
                self._locked_message(held) if held else f"Module '{module_id}' is checked out by someone else"
            ) from None
        if new:
            logger.info("User %s checked out module %s/%s", identity.user_id, project_id, module_id)
        return {"project_id": project_id, "module_id": module_id, "lock": self.view(lock)}

    def unlock(self, project_id: Any, module_id: Any) -> dict[str, Any]:
        # Read access suffices: someone demoted to viewer can still give their lock back.
        identity = self._identity(project_id, ACTION_PROJECT_READ)
        module_id = _checked_id(module_id, "module_id")
        held = self.registry.get(project_id, module_id)
        if held is not None and held.user_id != identity.user_id:
            raise AccessDeniedError(
                f"Module '{module_id}' is checked out by {self._owner_name(held.user_id)}; only they can "
                "release it (a project admin can break the lock)"
            )
        released = self.registry.release(project_id, module_id) if held is not None else None
        if released is not None:
            logger.info("User %s released module %s/%s", identity.user_id, project_id, module_id)
        return {"project_id": project_id, "module_id": module_id, "lock": None, "released": released is not None}

    def break_lock(self, project_id: Any, module_id: Any) -> dict[str, Any]:
        identity = self._identity(project_id, ACTION_PROJECT_ADMIN)
        module_id = _checked_id(module_id, "module_id")
        released = self.registry.release(project_id, module_id)
        if released is not None:
            logger.info(
                "User %s broke the lock of module %s/%s held by %s",
                identity.user_id, project_id, module_id, released.user_id,
            )
            if self.audit is not None:
                self.audit.safe_record(
                    identity, AUDIT_MODULE_LOCK_BREAK, project_id,
                    {"module_id": module_id, "reason": released.reason, "since": _iso(released.since)},
                    target_user_id=released.user_id,
                )
        return {
            "project_id": project_id,
            "module_id": module_id,
            "lock": None,
            "released": released is not None,
            "previous_owner": self._owner_name(released.user_id) if released else None,
        }


def locks_changed_event(project_id: str) -> dict[str, Any]:
    return {"event": MODULE_LOCKS_CHANGED, "project_id": project_id}


# --- REST -------------------------------------------------------------------------------


class LockBody(BaseModel):
    reason: str | None = None


def _call(fn, *args):
    try:
        return fn(*args)
    except AccessDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModuleLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def build_lock_router(
    locks: ModuleLocks, broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None
) -> APIRouter:
    router = APIRouter()

    async def changed(project_id: str) -> None:
        if broadcast is not None:
            await broadcast(locks_changed_event(project_id))

    @router.get("/projects/{project_id}/module-locks")
    def list_locks(project_id: str):
        return _call(locks.list_locks, project_id)

    @router.put("/projects/{project_id}/modules/{module_id}/lock")
    async def lock(project_id: str, module_id: str, body: LockBody | None = None):
        result = _call(locks.lock, project_id, module_id, body.reason if body else None)
        await changed(project_id)
        return result

    @router.delete("/projects/{project_id}/modules/{module_id}/lock")
    async def unlock(project_id: str, module_id: str):
        result = _call(locks.unlock, project_id, module_id)
        if result["released"]:
            await changed(project_id)
        return result

    @router.post("/projects/{project_id}/modules/{module_id}/lock/break")
    async def break_lock(project_id: str, module_id: str):
        result = _call(locks.break_lock, project_id, module_id)
        if result["released"]:
            await changed(project_id)
        return result

    return router


# --- WebSocket ----------------------------------------------------------------------------


def build_lock_rpc_methods(
    locks: ModuleLocks, broadcast: Callable[[dict[str, Any]], Awaitable[None]] | None
) -> dict[str, Callable[[dict], Any]]:
    """The lock methods for ``HOOKS.rpc_methods`` (errors are mapped by the core dispatcher)."""

    def mutation(fn: Callable[[dict], dict[str, Any]]) -> Callable[[dict], Awaitable[Any]]:
        async def run(params: dict) -> Any:
            result = fn(params)
            if broadcast is not None and result.get("released", True):
                await broadcast(locks_changed_event(result["project_id"]))
            return result

        return run

    return {
        "module.locks": lambda p: locks.list_locks(p.get("project_id")),
        "module.lock": mutation(lambda p: locks.lock(p.get("project_id"), p.get("module_id"), p.get("reason"))),
        "module.unlock": mutation(lambda p: locks.unlock(p.get("project_id"), p.get("module_id"))),
        "module.break_lock": mutation(lambda p: locks.break_lock(p.get("project_id"), p.get("module_id"))),
    }
