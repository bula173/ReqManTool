"""GitHub login: the OAuth 2.0 web application flow (GitHub is not an OpenID Connect provider).

Works with a GitHub OAuth App and with a GitHub App's user authorization
(github.com or GitHub Enterprise Server, ``url``), and offers the same
interface as ``oidc.OidcClient`` so the login routes treat both alike:

1. ``authorization_url``: ``<url>/login/oauth/authorize`` with ``client_id``,
   ``redirect_uri``, ``scope``, ``state`` and a PKCE ``S256``
   ``code_challenge`` (GitHub ignores what it does not support; the
   ``nonce`` has no place in plain OAuth and is not sent).
2. ``exchange_code``: a back-channel POST to ``<url>/login/oauth/access_token``
   with the client secret and the PKCE ``code_verifier``. GitHub answers a
   refused code with HTTP 200 and an ``error`` field, which fails the login.
3. ``identity_from_tokens``: the user comes from the REST API with the new
   access token (``GET /user``: the numeric ``id`` is the stable subject,
   ``login`` the user name). Without a public e-mail address, the primary
   verified one of ``GET /user/emails`` (scope ``user:email``) is used. With
   ``want_groups``, the user's organizations (``GET /user/orgs``; private
   memberships need ``read:org``) are the groups.

Scopes (``server.auth_providers[].scopes``): default ``read:user user:email``.
Roles from repository permissions (``repo_access.py``) read ``GET
/repos/{owner}/{repo}`` with the user's token: a GitHub App needs the
"Metadata: read" repository permission (and must be installed on the
repositories); an OAuth App sees private repositories only with the ``repo``
scope (add it to ``scopes``). A GitHub App with expiring user tokens also
returns a refresh token, which ``refresh_tokens`` uses.

Network calls use ``urllib`` through ``oidc._http_json`` (timeout, size
limit, no secret in any message).
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any, Callable

from .oidc import OidcError, _http_json
from .settings import ProviderSettings
from .users import LoginClaims

logger = logging.getLogger("reqman_server.github")

API_VERSION = "2022-11-28"
MAX_ORGS = 100


def github_api_url(web_url: str) -> str:
    """The REST API root of a GitHub web URL (github.com → api.github.com; Enterprise: ``/api/v3``)."""
    host = (urllib.parse.urlsplit(web_url).hostname or "").lower()
    if host in ("github.com", "www.github.com"):
        return "https://api.github.com"
    return f"{web_url.rstrip('/')}/api/v3"


class GitHubOAuthClient:
    """One configured ``github`` provider (``ProviderSettings``: ``issuer`` holds the web URL)."""

    def __init__(
        self,
        provider: ProviderSettings,
        clock: Callable[[], float] = time.time,
        http_json: Callable[..., Any] = _http_json,
    ) -> None:
        self.provider = provider
        self.clock = clock
        self._http_json = http_json
        self.web_url = provider.issuer.rstrip("/")
        self.api_url = github_api_url(self.web_url)

    # --- the flow ------------------------------------------------------------------

    def authorization_url(self, redirect_uri: str, state: str, nonce: str, code_challenge: str) -> str:
        query = urllib.parse.urlencode(
            {
                "client_id": self.provider.client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.provider.scopes),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "allow_signup": "false",
            }
        )
        return f"{self.web_url}/login/oauth/authorize?{query}"

    def _token_request(self, form: dict[str, str], what: str) -> dict[str, Any]:
        form = {"client_id": self.provider.client_id, **form}
        if self.provider.client_secret:
            form["client_secret"] = self.provider.client_secret
        tokens = self._http_json(
            f"{self.web_url}/login/oauth/access_token",
            data=urllib.parse.urlencode(form).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            what=what,
        )
        if not isinstance(tokens, dict):
            raise OidcError(f"{what} failed: the response is not a JSON object.")
        error = tokens.get("error")
        if error:
            detail = tokens.get("error_description") or error
            logger.warning("%s refused: %s", what, str(error)[:100])
            raise OidcError(f"{what} failed ({str(detail)[:200]}).")
        if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
            raise OidcError(f"{self.provider.name} returned no access token.")
        return tokens

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> dict[str, Any]:
        """The token endpoint's response (``access_token``, maybe ``refresh_token`` and ``expires_in``)."""
        return self._token_request(
            {"code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier},
            f"{self.provider.name} token request",
        )

    def refresh_tokens(self, refresh_token: str) -> dict[str, Any]:
        """New tokens for a refresh token (GitHub Apps with expiring user tokens)."""
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            f"{self.provider.name} token refresh",
        )

    def _api(self, path: str, access_token: str, what: str, allow_list: bool = False) -> Any:
        return self._http_json(
            f"{self.api_url}{path}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "ReqManTool",
            },
            what=what,
            allow_list=allow_list,
        )

    def identity_from_tokens(self, tokens: dict[str, Any], nonce: str, want_groups: bool = False) -> LoginClaims:
        access_token = tokens["access_token"]
        name = self.provider.name
        user = self._api("/user", access_token, f"{name} user request")
        if not isinstance(user, dict) or user.get("id") in (None, "") or not user.get("login"):
            raise OidcError(f"{name} answered /user without a user id.")
        login = str(user["login"]).strip()
        # A public profile address: GitHub does not say whether it is verified (the primary one below wins).
        email = str(user.get("email") or "").strip()
        verified: bool | None = None
        try:
            emails = self._api("/user/emails", access_token, f"{name} e-mail request", allow_list=True)
        except OidcError as exc:
            logger.info("%s: the e-mail addresses could not be read (%s)", name, exc)
            emails = []
        for entry in emails if isinstance(emails, list) else []:
            if isinstance(entry, dict) and entry.get("primary") and isinstance(entry.get("email"), str):
                email = entry["email"].strip()
                verified = bool(entry.get("verified"))
                break
        groups: tuple[str, ...] = ()
        if want_groups:
            try:
                orgs = self._api(f"/user/orgs?per_page={MAX_ORGS}", access_token, f"{name} organizations request",
                                 allow_list=True)
            except OidcError as exc:
                logger.info("%s: the organizations could not be read (%s)", name, exc)
                orgs = []
            groups = tuple(
                str(o["login"]).strip()
                for o in (orgs if isinstance(orgs, list) else [])
                if isinstance(o, dict) and isinstance(o.get("login"), str) and o["login"].strip()
            )
        return LoginClaims(
            provider=self.provider.id,
            subject=str(user["id"]),
            name=str(user.get("name") or "").strip() or login,
            email=email,
            username=login,
            email_verified=verified,
            groups=groups,
        )
