"""Server-side login sessions and pending logins.

A session is identified by a random token kept only in the browser's
HTTP-only cookie; the server stores the SHA-256 of it, so ``sessions.json``
(optional, owner-readable only) holds no usable cookie value. A session
ends at logout, after ``session_max_age_seconds`` (absolute) or after
``session_idle_seconds`` without a request. Each session has its own CSRF
token (synchronizer token): the frontend reads it from ``GET /auth/me`` and
sends it as ``X-CSRF-Token`` with every state-changing request.

A pending login (``PendingLogins``) holds the OAuth ``state``, ``nonce`` and
PKCE ``code_verifier`` between the redirect to the provider and the
callback (in memory, ten minutes, single use), bound to the browser by a
random value in a short-lived cookie.

A session also keeps the OAuth tokens of its login (``access_token``,
``refresh_token``, ``token_expires``), server-side only: the repository
permission lookup (``repo_access.py``) calls the GitLab/GitHub API with
them. They are never sent to the browser or logged, and leave the server
only to the provider that issued them; ``sessions.json`` is written owner
readable only (0600). They go with the session (logout, expiry).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .jsonfile import read_json_file, write_json_file

# last_seen is written to sessions.json at most this often (idle expiry is approximate across restarts).
_PERSIST_LAST_SEEN_EVERY = 60.0
PENDING_LOGIN_TTL = 600.0
_MAX_PENDING_LOGINS = 1000


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Session:
    user_id: str
    csrf_token: str
    created: float
    last_seen: float
    expires: float
    provider: str = ""
    # OAuth tokens of the login (server-side only; repr=False keeps them out of logs and tracebacks).
    access_token: str = field(default="", repr=False)
    refresh_token: str = field(default="", repr=False)
    # When the access token expires (epoch seconds; 0: unknown / does not expire).
    token_expires: float = 0.0


class SessionStore:
    def __init__(
        self,
        path: Path | None,
        max_age: int,
        idle: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.max_age = max_age
        self.idle = idle
        self.clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._last_persist = 0.0
        if path is not None:
            data = read_json_file(path, "Session file") or {}
            raw = data.get("sessions") or {}
            if isinstance(raw, dict):
                for key, value in raw.items():
                    try:
                        self._sessions[key] = Session(**value)
                    except TypeError:
                        continue
            self._purge_expired()

    def _alive(self, session: Session, now: float) -> bool:
        return now < session.expires and now - session.last_seen < self.idle

    def _purge_expired(self) -> bool:
        now = self.clock()
        dead = [key for key, s in self._sessions.items() if not self._alive(s, now)]
        for key in dead:
            del self._sessions[key]
        return bool(dead)

    def create(
        self, user_id: str, provider: str = "", oauth_tokens: dict | None = None
    ) -> tuple[str, Session]:
        """A new session: ``(cookie token, session)``; ``oauth_tokens``: the token endpoint's response."""
        token = secrets.token_urlsafe(32)
        now = self.clock()
        session = Session(
            user_id=user_id,
            csrf_token=secrets.token_urlsafe(32),
            created=now,
            last_seen=now,
            expires=now + self.max_age,
            provider=provider,
        )
        _apply_tokens(session, oauth_tokens or {}, now)
        with self._lock:
            self._purge_expired()
            self._sessions[_hash(token)] = session
            self._persist(force=True)
        return token, session

    def get(self, token: str | None, touch: bool = True) -> Session | None:
        """The live session of a cookie token (its idle timer restarted), or ``None``."""
        if not token:
            return None
        key = _hash(token)
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return None
            now = self.clock()
            if not self._alive(session, now):
                del self._sessions[key]
                self._persist(force=True)
                return None
            if touch:
                session.last_seen = now
                self._persist(force=False)
            return session

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            if self._sessions.pop(_hash(token), None) is not None:
                self._persist(force=True)

    def delete_user(self, user_id: str) -> None:
        """End every session of a user (e.g. deactivated)."""
        with self._lock:
            keys = [k for k, s in self._sessions.items() if s.user_id == user_id]
            for key in keys:
                del self._sessions[key]
            if keys:
                self._persist(force=True)

    def has_user(self, user_id: str) -> bool:
        """Whether the user has a live session left (e.g. in another browser)."""
        with self._lock:
            now = self.clock()
            return any(s.user_id == user_id and self._alive(s, now) for s in self._sessions.values())

    def oauth_session(self, user_id: str, provider: str) -> tuple[str, Session] | None:
        """``(session key, session)`` of the user's newest live session of ``provider`` with an access token."""
        with self._lock:
            now = self.clock()
            found = [
                (key, s)
                for key, s in self._sessions.items()
                if s.user_id == user_id and s.provider == provider and s.access_token and self._alive(s, now)
            ]
        if not found:
            return None
        return max(found, key=lambda item: item[1].created)

    def session_by_key(self, key: str) -> Session | None:
        """The live session stored under ``key`` (the hash of its cookie token), or ``None``."""
        with self._lock:
            session = self._sessions.get(key)
            return session if session is not None and self._alive(session, self.clock()) else None

    def update_tokens(self, key: str, oauth_tokens: dict) -> None:
        """Store refreshed OAuth tokens (a token endpoint response) in the session ``key`` (if still there)."""
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return
            _apply_tokens(session, oauth_tokens, self.clock())
            self._persist(force=True)

    def user_ids(self) -> set[str]:
        """The users with a live session."""
        with self._lock:
            now = self.clock()
            return {s.user_id for s in self._sessions.values() if self._alive(s, now)}

    def _persist(self, force: bool) -> None:
        if self.path is None:
            return
        now = self.clock()
        if not force and now - self._last_persist < _PERSIST_LAST_SEEN_EVERY:
            return
        self._last_persist = now
        write_json_file(self.path, {"sessions": {k: asdict(s) for k, s in self._sessions.items()}})


def _apply_tokens(session: Session, tokens: dict, now: float) -> None:
    access = tokens.get("access_token")
    if not isinstance(access, str) or not access:
        return
    session.access_token = access
    refresh = tokens.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        session.refresh_token = refresh  # else keep the old one (not every refresh rotates it)
    expires_in = tokens.get("expires_in")
    if isinstance(expires_in, str) and expires_in.strip().isdigit():
        expires_in = int(expires_in.strip())
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) and expires_in > 0:
        session.token_expires = now + float(expires_in)
    else:
        session.token_expires = 0.0


@dataclass(frozen=True)
class PendingLogin:
    provider: str
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    next_path: str
    # Random value also set in the browser's login cookie (binds the callback to this browser).
    browser_binding: str
    created: float


class PendingLogins:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, PendingLogin] = {}

    def add(self, pending: PendingLogin) -> None:
        with self._lock:
            now = self.clock()
            for key in [k for k, p in self._pending.items() if now - p.created > PENDING_LOGIN_TTL]:
                del self._pending[key]
            while len(self._pending) >= _MAX_PENDING_LOGINS:
                del self._pending[next(iter(self._pending))]
            self._pending[pending.state] = pending

    def take(self, state: str | None) -> PendingLogin | None:
        """The pending login of ``state`` (removed: single use), or ``None`` if unknown or expired."""
        if not state:
            return None
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or self.clock() - pending.created > PENDING_LOGIN_TTL:
            return None
        return pending
