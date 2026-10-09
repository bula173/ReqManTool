"""Publish / push / pull with the login account: the OAuth token of the sign-in session.

A user signed in through a ``github`` or ``gitlab`` provider (a ``gerrit``
one later) can publish a project and push / pull its remote without a
provider "connection": the host they logged in with *is* the repository
host. ``LoginAccountSource`` is installed as
``reqman.providers.ProviderService.login_source`` and answers, per call,
``(ProviderConnection, token)`` for the current request's identity:

- the connection is synthetic (id ``login``, the provider's web URL), so
  ``connection_for_remote`` / ``git_auth_env`` treat it like any other;
- the token is the session's OAuth access token (``sessions.py``),
  renewed with the refresh token when it is about to expire, and never
  cached, stored in a file, logged, returned to the browser or written
  into a git config / remote URL — it only ever rides one git process
  (``reqman.git_repo`` ``GIT_CONFIG_*`` extra header) or one provider
  API call.

Login providers that host repositories (``LOGIN_GIT_PROVIDER_TYPES``);
a plain OIDC single sign-on login has no git host and answers ``None``
(``describe``) / a refusal message (``__call__``).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from reqman.extensions import current_identity
from reqman.providers import LOGIN_CONNECTION_ID, ProviderConnection

from .oidc import OidcError
from .sessions import SessionStore
from .settings import ProviderSettings, ServerSettings
from .users import User, UserRegistry

logger = logging.getLogger("reqman_server.login_account")

# Login provider types whose host is a git host (publish, push, pull, review).
LOGIN_GIT_PROVIDER_TYPES = ("gitlab", "github")
# Refresh an access token this long before it expires (same margin as repo_access).
TOKEN_EXPIRY_MARGIN = 60.0


def git_login_provider(settings: ServerSettings, user: User | None) -> ProviderSettings | None:
    """The user's login provider when it is a git host, else ``None``."""
    if user is None or not user.provider:
        return None
    provider = settings.provider(user.provider)
    if provider is None or provider.type not in LOGIN_GIT_PROVIDER_TYPES:
        return None
    return provider


class LoginAccountSource:
    """``ProviderService.login_source`` for the server edition (see the module docstring)."""

    def __init__(
        self,
        settings: ServerSettings,
        users: UserRegistry,
        sessions: SessionStore,
        refresh: Callable[[str, str], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.users = users
        self.sessions = sessions
        # ``RepoAccess.refresh_token``: renews an expired session token (may be omitted in tests).
        self.refresh = refresh
        self.clock = clock

    # --- who is asking ---------------------------------------------------------------

    def _identity_user(self) -> User:
        identity = current_identity()
        if identity is None:
            raise ValueError("You are not signed in; log in to use the login account")
        user = self.users.get(identity.user_id)
        if user is None or not user.active:
            raise ValueError("Your account is not active; ask a server administrator")
        return user

    def _token(self, provider: ProviderSettings, user: User) -> str:
        """The user's OAuth access token for ``provider``, renewed when it is about to expire."""
        found = self.sessions.oauth_session(user.id, provider.id)
        if found is None:
            raise ValueError(f"Your session holds no {provider.name} token; log out and log in again")
        key, session = found
        token = session.access_token
        if not token:
            raise ValueError(
                f"Your {provider.name} sign-in carries no access token; log out and log in again"
            )
        expired = session.token_expires and self.clock() > session.token_expires - TOKEN_EXPIRY_MARGIN
        if expired and session.refresh_token and self.refresh is not None:
            try:
                token = self.refresh(key, token)
            except OidcError as exc:
                raise ValueError(
                    f"Your {provider.name} sign-in could not be renewed ({exc}); log out and log in again"
                ) from exc
        return token

    # --- the hook ---------------------------------------------------------------------

    def connection(self, provider: ProviderSettings) -> ProviderConnection:
        """The synthetic ``login`` connection of a provider (web URL, no stored token)."""
        return ProviderConnection(
            id=LOGIN_CONNECTION_ID,
            provider=provider.type,
            name=provider.name,
            url=provider.issuer.rstrip("/"),
        )

    def __call__(self) -> tuple[ProviderConnection, str]:
        """``(connection, OAuth token)`` of the current identity's login (``ValueError`` when there is none)."""
        user = self._identity_user()
        provider = git_login_provider(self.settings, user)
        if provider is None:
            signed_in = self.settings.provider(user.provider)
            name = signed_in.name if signed_in is not None else user.provider
            raise ValueError(
                f"You are signed in with {name}, which does not host git repositories; "
                "sign in with GitHub or GitLab, or use a provider connection"
            )
        return self.connection(provider), self._token(provider, user)

    def describe(self, user: User | None) -> dict[str, Any] | None:
        """What ``GET /auth/me`` tells the browser about the login account, or ``None``."""
        provider = git_login_provider(self.settings, user)
        if provider is None:
            return None
        return {"id": LOGIN_CONNECTION_ID, "type": provider.type, "name": provider.name, "url": provider.issuer}
