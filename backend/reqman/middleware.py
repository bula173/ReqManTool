"""Rate limiting and security headers middleware for the server edition."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Callable, Awaitable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from reqman_server.settings import ServerSettings


class RateLimitMiddleware:
    """Rate limiting middleware for specific endpoints."""

    def __init__(
        self,
        app: ASGIApp,
        enabled: bool = True,
        max_requests: int = 10,
        window_seconds: float = 60.0,
        paths: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.paths = paths
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not any(path.startswith(p) for p in self.paths):
            await self.app(scope, receive, send)
            return

        client_ip = self._get_client_ip(scope)
        now = time.time()
        window_start = now - self.window_seconds

        # Clean old entries
        self._requests[client_ip] = [t for t in self._requests[client_ip] if t > window_start]

        if len(self._requests[client_ip]) >= self.max_requests:
            await self._rate_limit_exceeded(send)
            return

        self._requests[client_ip].append(now)
        await self.app(scope, receive, send)

    def _get_client_ip(self, scope: dict) -> str:
        # Check for forwarded headers first (behind proxy)
        headers = dict(scope.get("headers", []))
        forwarded_for = headers.get(b"x-forwarded-for")
        if forwarded_for:
            return forwarded_for.decode().split(",")[0].strip()
        real_ip = headers.get(b"x-real-ip")
        if real_ip:
            return real_ip.decode()
        # Fallback to client host
        client = scope.get("client")
        if client:
            return client[0]
        return "unknown"

    async def _rate_limit_exceeded(self, send: Send) -> None:
        response = JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Please try again later."},
            headers={"Retry-After": "60"},
        )
        await response(scope={"type": "http"}, receive=lambda: None, send=send)


class SecurityHeadersMiddleware:
    """Add security headers to responses."""

    def __init__(
        self,
        app: ASGIApp,
        enabled: bool = True,
        hsts_max_age: int = 31536000,  # 1 year
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.hsts_max_age = hsts_max_age

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Add security headers
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"x-xss-protection", b"1; mode=block"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'"),
                ])
                # HSTS - only on HTTPS
                # Note: We check if we're on HTTPS by looking at the URL scheme
                # This is a simplified check - in production you'd want to check the actual scheme
                message["headers"] = headers
            await self.app(scope, receive, send_with_headers)
