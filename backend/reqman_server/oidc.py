"""OpenID Connect login (authorization code flow with PKCE) — GitLab and any OIDC provider.

GitLab (gitlab.com or self-hosted) is an OpenID Connect provider, so the
``gitlab`` provider type is this client with the GitLab URL as the issuer;
the ``oidc`` type (Keycloak, Entra ID, ...) is the same with any issuer.

Flow
----

1. ``authorization_url``: the endpoints come from issuer discovery
   (``<issuer>/.well-known/openid-configuration``; its ``issuer`` must equal
   the configured one). The browser is sent to the authorization endpoint
   with ``response_type=code``, ``scope`` (``openid profile email``),
   ``state`` and ``nonce`` (random, single use) and a PKCE ``S256``
   ``code_challenge``.
2. ``exchange_code``: the code is exchanged at the token endpoint by a
   direct, back-channel POST with the client secret (HTTP Basic, or
   ``client_secret_post`` if that is the only method the provider lists)
   and the PKCE ``code_verifier``.
3. ``identity_from_tokens``: the ID token's claims are read and validated —
   ``iss`` (the discovered issuer), ``aud`` (contains the client id; ``azp``
   must be the client id when there are several audiences), ``exp`` (not
   expired, 60 s leeway), ``iat`` (not in the future), ``nonce`` (equals the
   one sent) and ``sub`` (present). Name, e-mail and user name are taken
   from the ID token, completed from the userinfo endpoint when missing
   (whose ``sub`` must match). Group memberships (for ``server.group_roles``
   / ``server.admin_groups``) come from the ``groups`` claim (Keycloak,
   GitLab's userinfo) or GitLab's ``groups_direct`` ID token claim; with
   ``want_groups`` and neither in the ID token, from the userinfo endpoint.

The ID token's *signature is not verified*. OpenID Connect Core 1.0
§3.1.3.7 (item 6) allows a client to rely on TLS server validation instead
when the ID token is received directly from the token endpoint over TLS,
which is the only way this client obtains it; so no JOSE/JWKS library is
needed. For that reason every endpoint must be https (http is accepted only
for a local host, or with ``allow_http`` for testing), and the
``alg: none`` header is refused anyway.

Network calls use ``urllib`` (no extra dependency), with a timeout and a
response size limit. The client secret is never logged or put in an error
message.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .settings import ProviderSettings, ServerConfigError, check_endpoint_url
from .users import LoginClaims

logger = logging.getLogger("reqman_server.oidc")

HTTP_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 1024 * 1024
CLOCK_LEEWAY = 60.0
DISCOVERY_TTL = 3600.0


class OidcError(Exception):
    """The login failed; the message is safe to show to the user (no secrets)."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def pkce_pair() -> tuple[str, str]:
    """``(code_verifier, S256 code_challenge)`` (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)  # 86 characters of [A-Za-z0-9_-]
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def decode_jwt_claims(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(header, claims)`` of a compact JWS, without verifying its signature (see the module docstring)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise OidcError("The provider returned a malformed ID token.")
    try:
        header = json.loads(b64url_decode(parts[0]))
        claims = json.loads(b64url_decode(parts[1]))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OidcError("The provider returned a malformed ID token.") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise OidcError("The provider returned a malformed ID token.")
    return header, claims


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str | None
    token_auth_methods: tuple[str, ...]


def _http_json(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    what: str,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:  # noqa: S310 - URL checked by caller
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read(MAX_RESPONSE_BYTES))
            if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                detail = f" ({payload['error'][:100]})"
        except (ValueError, OSError):
            pass
        logger.warning("%s failed: HTTP %s%s", what, exc.code, detail)
        raise OidcError(f"{what} failed: HTTP {exc.code}{detail}.") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        logger.warning("%s failed: %s", what, reason)
        raise OidcError(f"{what} failed: the identity provider cannot be reached.") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise OidcError(f"{what} failed: the response is too large.")
    try:
        payload = json.loads(body)
    except ValueError:
        raise OidcError(f"{what} failed: the response is not JSON.") from None
    if not isinstance(payload, dict):
        raise OidcError(f"{what} failed: the response is not a JSON object.")
    return payload


class OidcClient:
    """One configured provider (``ProviderSettings``)."""

    def __init__(
        self,
        provider: ProviderSettings,
        clock: Callable[[], float] = time.time,
        http_json: Callable[..., dict[str, Any]] = _http_json,
    ) -> None:
        self.provider = provider
        self.clock = clock
        self._http_json = http_json
        self._discovery: Discovery | None = None
        self._discovered_at = 0.0
        self._lock = threading.Lock()

    # --- discovery ---------------------------------------------------------------

    def discovery(self) -> Discovery:
        with self._lock:
            if self._discovery is not None and self.clock() - self._discovered_at < DISCOVERY_TTL:
                return self._discovery
        url = f"{self.provider.issuer}/.well-known/openid-configuration"
        doc = self._http_json(url, what=f"{self.provider.name} discovery")
        issuer = doc.get("issuer")
        if not isinstance(issuer, str) or issuer.rstrip("/") != self.provider.issuer:
            raise OidcError(
                f"{self.provider.name}: the discovery document names issuer {issuer!r}, "
                f"expected {self.provider.issuer!r}."
            )
        try:
            endpoints = {
                key: check_endpoint_url(str(doc.get(key) or ""), key, self.provider.allow_http)
                for key in ("authorization_endpoint", "token_endpoint")
            }
            userinfo = doc.get("userinfo_endpoint")
            userinfo = (
                check_endpoint_url(userinfo, "userinfo_endpoint", self.provider.allow_http)
                if isinstance(userinfo, str) and userinfo
                else None
            )
        except ServerConfigError as exc:
            raise OidcError(f"{self.provider.name}: {exc}.") from None
        methods = doc.get("token_endpoint_auth_methods_supported")
        discovery = Discovery(
            issuer=issuer,
            authorization_endpoint=endpoints["authorization_endpoint"],
            token_endpoint=endpoints["token_endpoint"],
            userinfo_endpoint=userinfo,
            token_auth_methods=tuple(m for m in methods if isinstance(m, str))
            if isinstance(methods, list)
            else ("client_secret_basic",),
        )
        with self._lock:
            self._discovery, self._discovered_at = discovery, self.clock()
        return discovery

    # --- the flow ------------------------------------------------------------------

    def authorization_url(self, redirect_uri: str, state: str, nonce: str, code_challenge: str) -> str:
        discovery = self.discovery()
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.provider.client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.provider.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if urllib.parse.urlsplit(discovery.authorization_endpoint).query else "?"
        return f"{discovery.authorization_endpoint}{separator}{query}"

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> dict[str, Any]:
        """The token endpoint's response (``id_token``, ``access_token``, ...)."""
        discovery = self.discovery()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "client_id": self.provider.client_id,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        secret = self.provider.client_secret
        if secret:
            methods = discovery.token_auth_methods
            if "client_secret_basic" not in methods and "client_secret_post" in methods:
                form["client_secret"] = secret
            else:
                # RFC 6749 §2.3.1: form-urlencode id and secret before Basic encoding.
                raw = f"{urllib.parse.quote(self.provider.client_id, safe='')}:{urllib.parse.quote(secret, safe='')}"
                headers["Authorization"] = "Basic " + base64.b64encode(raw.encode("utf-8")).decode("ascii")
        tokens = self._http_json(
            discovery.token_endpoint,
            data=urllib.parse.urlencode(form).encode("ascii"),
            headers=headers,
            what=f"{self.provider.name} token request",
        )
        if not isinstance(tokens.get("id_token"), str):
            raise OidcError(f"{self.provider.name} returned no ID token (is the 'openid' scope allowed?).")
        return tokens

    def validate_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        """The ID token's claims, validated (see the module docstring); ``OidcError`` otherwise."""
        discovery = self.discovery()
        header, claims = decode_jwt_claims(id_token)
        if str(header.get("alg", "")).lower() in ("", "none"):
            raise OidcError("The ID token is not signed.")
        if claims.get("iss") != discovery.issuer:
            raise OidcError("The ID token was issued by another provider.")
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience if isinstance(audience, list) else []
        if self.provider.client_id not in audiences:
            raise OidcError("The ID token is meant for another application.")
        if len(audiences) > 1 and claims.get("azp") != self.provider.client_id:
            raise OidcError("The ID token is meant for another application.")
        now = self.clock()
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or isinstance(exp, bool) or now > exp + CLOCK_LEEWAY:
            raise OidcError("The ID token has expired; please log in again.")
        iat = claims.get("iat")
        if isinstance(iat, (int, float)) and not isinstance(iat, bool) and iat > now + CLOCK_LEEWAY:
            raise OidcError("The ID token was issued in the future; check the server clock.")
        if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], nonce):
            raise OidcError("The login response does not match this login (nonce); please try again.")
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise OidcError("The ID token names no user (sub).")
        return claims

    def userinfo(self, access_token: str) -> dict[str, Any]:
        discovery = self.discovery()
        if not discovery.userinfo_endpoint:
            return {}
        return self._http_json(
            discovery.userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            what=f"{self.provider.name} userinfo request",
        )

    def identity_from_tokens(self, tokens: dict[str, Any], nonce: str, want_groups: bool = False) -> LoginClaims:
        claims = self.validate_id_token(tokens["id_token"], nonce)
        info: dict[str, Any] = dict(claims)
        access_token = tokens.get("access_token")
        missing_groups = want_groups and not any(key in claims for key in GROUP_CLAIMS)
        missing_profile = not (claims.get("email") and (claims.get("name") or claims.get("nickname")))
        if (missing_profile or missing_groups) and isinstance(access_token, str):
            extra = self.userinfo(access_token)
            if extra:
                if extra.get("sub") != claims["sub"]:
                    raise OidcError("The userinfo response names another user.")
                for key, value in extra.items():
                    info.setdefault(key, value)
                    if not info.get(key):
                        info[key] = value

        def text(key: str) -> str:
            value = info.get(key)
            return value.strip() if isinstance(value, str) else ""

        username = text("preferred_username") or text("nickname")
        verified = info.get("email_verified")
        return LoginClaims(
            provider=self.provider.id,
            subject=claims["sub"],
            name=text("name") or username,
            email=text("email"),
            username=username,
            email_verified=verified if isinstance(verified, bool) else None,
            groups=groups_of(info),
        )


# Claims naming the user's groups: ``groups`` (Keycloak, Entra ID, GitLab userinfo), ``groups_direct`` (GitLab ID token).
GROUP_CLAIMS = ("groups", "groups_direct")
MAX_GROUPS = 1000


def groups_of(info: dict[str, Any]) -> tuple[str, ...]:
    """The group names of the claims (``GROUP_CLAIMS``), de-duplicated, at most ``MAX_GROUPS``."""
    groups: list[str] = []
    for key in GROUP_CLAIMS:
        value = info.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item.strip() and item.strip() not in groups:
                groups.append(item.strip()[:255])
    return tuple(groups[:MAX_GROUPS])
