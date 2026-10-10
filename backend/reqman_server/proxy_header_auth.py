"""Trusted reverse-proxy header authentication for ReqManTool server edition.

Supports authentication via HTTP headers set by a trusted reverse proxy
(Cloudflare Access, oauth2-proxy, Pomerium, etc.) with optional JWT signature
verification via JWKS.
"""

from __future__ import annotations

import json
import logging
import time
import threading
from dataclasses import dataclass
from typing import Any, Optional

import urllib.request

from .settings import ProviderSettings, ServerConfigError

logger = logging.getLogger("reqman_server.proxy_header")


class ProxyHeaderError(Exception):
    """Proxy header authentication or configuration error; message is safe for users."""
    pass


@dataclass
class ProxyHeaderUser:
    """User information from proxy headers."""
    username: str
    name: str
    email: str
    user_id: str
    groups: tuple[str, ...]


class ProxyHeaderClient:
    """Trusted proxy header authentication client."""

    def __init__(self, provider: ProviderSettings):
        self.provider = provider
        self._jwks_cache: dict[str, Any] = {}
        self._jwks_fetched_at = 0.0
        self._lock = threading.Lock()

    def _fetch_jwks(self) -> dict[str, Any]:
        """Fetch and cache JWKS from the configured URL."""
        if not self.provider.proxy_header_jwks_url:
            return {}

        now = time.time()
        with self._lock:
            if self._jwks_cache and (now - self._jwks_fetched_at) < self.provider.proxy_header_jwks_cache_ttl:
                return self._jwks_cache

            try:
                req = urllib.request.Request(
                    self.provider.proxy_header_jwks_url,
                    headers={"Accept": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = resp.read().decode("utf-8")
                    jwks = json.loads(body)
                    if not isinstance(jwks, dict) or "keys" not in jwks:
                        raise ProxyHeaderError("Invalid JWKS format: missing 'keys'")
                    self._jwks_cache = jwks
                    self._jwks_fetched_at = time.time()
                    return jwks
            except urllib.error.HTTPError as e:
                raise ProxyHeaderError(f"Failed to fetch JWKS: HTTP {e.code}")
            except urllib.error.URLError as e:
                raise ProxyHeaderError(f"Failed to reach JWKS endpoint: {e.reason}")
            except json.JSONDecodeError:
                raise ProxyHeaderError("Invalid JSON in JWKS response")
            except Exception as e:
                raise ProxyHeaderError(f"Failed to fetch JWKS: {e}")

    def _verify_jwt_signature(self, token: str, jwks: dict[str, Any]) -> dict[str, Any]:
        """Verify JWT signature using JWKS. Returns the decoded payload."""
        try:
            import jwt
            from jwt.algorithms import RSAAlgorithm
        except ImportError:
            raise ProxyHeaderError("PyJWT is required for JWT verification. Install with: pip install PyJWT[crypto]")

        # Parse header to get key ID
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise ProxyHeaderError("JWT header missing 'kid'")

        # Find matching key in JWKS
        jwk_key = None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                jwk_key = key
                break

        if not jwk_key:
            raise ProxyHeaderError(f"No matching key found for kid: {kid}")

        # Convert JWK to PEM
        public_key = RSAAlgorithm.from_jwk(json.dumps(jwk_key))

        # Verify and decode
        try:
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                options={"verify_aud": False, "verify_iss": False},
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise ProxyHeaderError("Token has expired")
        except jwt.InvalidTokenError as e:
            raise ProxyHeaderError(f"Invalid JWT: {e}")

    def verify_request(self, headers: dict[str, str]) -> tuple[str, str, str, tuple[str, ...]]:
        """
        Verify the request headers and extract user info.
        Returns (username, name, email, groups).
        """
        # Check required header
        username = headers.get(self.provider.proxy_header_name)
        if not username:
            raise ProxyHeaderError(f"Required header '{self.provider.proxy_header_name}' not present")

        # Optional headers
        email = headers.get(self.provider.proxy_header_email, "")
        name = headers.get(self.provider.proxy_header_name_attr, "")
        groups_header = headers.get(self.provider.proxy_header_groups, "")

        # Parse groups (comma-separated or JSON array)
        groups = []
        if groups_header:
            try:
                # Try JSON first
                groups = json.loads(groups_header)
                if not isinstance(groups, list):
                    groups = [groups_header]
            except json.JSONDecodeError:
                # Fall back to comma-separated
                groups = [g.strip() for g in groups_header.split(",") if g.strip()]

        # Verify signature if configured
        if self.provider.proxy_header_verify_signature and self.provider.proxy_header_jwks_url:
            # Look for JWT in Authorization header or custom header
            auth_header = headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            else:
                # Check for custom header
                token = headers.get("X-Auth-Token", "")

            if not token:
                raise ProxyHeaderError("JWT token required for signature verification")

            jwks = self._fetch_jwks()
            try:
                self._verify_jwt_signature(token, jwks)
            except Exception as e:
                raise ProxyHeaderError(f"JWT verification failed: {e}")

        # Validate trusted issuers if configured
        if self.provider.proxy_header_trusted_issuers:
            # Could check a header like X-Forwarded-Issuer
            issuer = headers.get("X-Forwarded-Issuer", "")
            if issuer and issuer not in self.provider.proxy_header_trusted_issuers:
                raise ProxyHeaderError(f"Untrusted issuer: {issuer}")

        # Extract user ID (use username as fallback)
        user_id = username

        return username, username, "", tuple()

    def extract_user(self, headers: dict[str, str]) -> "ProxyHeaderUser":
        """Extract user info from headers without JWT verification."""
        username, name, email, groups = self._extract_basic_info(headers)
        return ProxyHeaderUser(
            username=username,
            name=name,
            email=email,
            user_id=username,
            groups=groups,
        )

    def _extract_basic_info(self, headers: dict[str, str]) -> tuple[str, str, str, tuple[str, ...]]:
        """Extract basic user info without verification."""
        username = headers.get(self.provider.proxy_header_name, "")
        if not username:
            raise ProxyHeaderError(f"Required header '{self.provider.proxy_header_name}' not present")

        email = headers.get(self.provider.proxy_header_email, "")
        name = headers.get(self.provider.proxy_header_name_attr, "")
        groups_header = headers.get(self.provider.proxy_header_groups, "")

        groups = []
        if groups_header:
            try:
                groups = json.loads(groups_header)
                if not isinstance(groups, list):
                    groups = [groups_header]
            except json.JSONDecodeError:
                groups = [g.strip() for g in groups_header.split(",") if g.strip()]

        return username, name, email, tuple(groups)

    def test_connection(self) -> bool:
        """Test the proxy header configuration."""
        if self.provider.proxy_header_verify_signature and not self.provider.proxy_header_jwks_url:
            return False
        if self.provider.proxy_header_verify_signature and self.provider.proxy_header_jwks_url:
            try:
                self._fetch_jwks()
            except Exception as e:
                logger.error("JWKS fetch test failed: %s", e)
                return False
        return True