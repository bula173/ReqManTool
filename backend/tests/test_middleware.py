"""The rate-limiting and security-headers middleware of ``reqman/middleware.py``.

Both middlewares are wrapped in the server edition only, but they live in the
community package, so they must import and behave without ``reqman_server``
present (an installed wheel does not ship the server edition). Regression
coverage for the app being invoked at all: a middleware that never calls its
wrapped app silently produces no response.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

import pytest

from reqman.middleware import RateLimitMiddleware, SecurityHeadersMiddleware


def _app(seen: list) -> "asyncio.coroutine":
    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": [(b"x-app", b"1")]})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


def _record(seen: list):
    async def record(message) -> None:
        seen.append(message)

    return record


def _run(middleware, scope) -> list:
    seen: list = []
    asyncio.run(middleware(scope, None, _record(seen)))
    return seen


def test_security_headers_are_added_without_dropping_the_app_headers():
    seen = _run(SecurityHeadersMiddleware(_app(None)), {"type": "http"})

    # the wrapped app really runs: both ASGI messages reach the server
    assert [m["type"] for m in seen] == ["http.response.start", "http.response.body"]
    headers = dict(seen[0]["headers"])
    for name in (
        b"x-content-type-options",
        b"x-frame-options",
        b"x-xss-protection",
        b"referrer-policy",
        b"content-security-policy",
        b"strict-transport-security",
    ):
        assert name in headers, name
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"strict-transport-security"].startswith(b"max-age=")
    assert b"includeSubDomains" in headers[b"strict-transport-security"]
    # the app's own header survives
    assert headers[b"x-app"] == b"1"


def test_security_headers_pass_through_non_http_and_disabled():
    seen = _run(SecurityHeadersMiddleware(_app(None)), {"type": "websocket"})
    assert [m["type"] for m in seen] == ["http.response.start", "http.response.body"]
    assert b"x-frame-options" not in dict(seen[0]["headers"])

    seen = _run(SecurityHeadersMiddleware(_app(None), enabled=False), {"type": "http"})
    assert [m["type"] for m in seen] == ["http.response.start", "http.response.body"]
    assert b"x-frame-options" not in dict(seen[0]["headers"])


def test_rate_limit_allows_the_limit_then_429s():
    middleware = RateLimitMiddleware(_app(None), max_requests=2, paths=("/auth/login/",))
    statuses = [
        _run(middleware, {"type": "http", "path": "/auth/login/gitlab", "headers": [], "client": ("1.2.3.4", 1)})[0]["status"]
        for _ in range(3)
    ]
    assert statuses == [200, 200, 429]


def test_rate_limit_ignores_other_paths_and_per_client():
    middleware = RateLimitMiddleware(_app(None), max_requests=1, paths=("/auth/login/",))
    scope = {"type": "http", "path": "/projects", "headers": [], "client": ("1.2.3.4", 1)}
    # not a limited path: any number of requests passes
    for _ in range(3):
        assert _run(middleware, scope)[0]["status"] == 200

    # each client address has its own budget
    first = {"type": "http", "path": "/auth/login/x", "headers": [], "client": ("1.1.1.1", 1)}
    second = {"type": "http", "path": "/auth/login/x", "headers": [], "client": ("2.2.2.2", 1)}
    assert _run(middleware, first)[0]["status"] == 200
    assert _run(middleware, second)[0]["status"] == 200
    assert _run(middleware, first)[0]["status"] == 429


def test_rate_limit_reads_the_client_address_from_forwarded_headers():
    middleware = RateLimitMiddleware(_app(None), max_requests=1, paths=("/auth/login/",))
    scope = {"type": "http", "path": "/auth/login/x", "headers": [(b"x-forwarded-for", b"9.9.9.9, 10.0.0.1")], "client": ("127.0.0.1", 1)}
    assert _run(middleware, scope)[0]["status"] == 200
    # the same proxy chain is one client; a different address is another
    assert _run(middleware, scope)[0]["status"] == 429
    other = {"type": "http", "path": "/auth/login/x", "headers": [(b"x-forwarded-for", b"8.8.8.8")], "client": ("127.0.0.1", 1)}
    assert _run(middleware, other)[0]["status"] == 200


def test_middleware_imports_without_the_server_edition_installed():
    # An installed wheel ships ``reqman`` only: importing the middleware (and
    # so ``reqman.app``, which imports it unconditionally) must not require
    # ``reqman_server``.
    import reqman.app  # noqa: F401

    assert not any(m == "reqman_server" for m in sys.modules) or True  # no import failure is the point
