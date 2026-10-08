"""The server edition's settings: the ``server`` section of the config file plus environment overrides.

Example (``config.json``)::

    {
      "format_version": 1,
      "data_dir": "/srv/reqman/data",
      "mode": "server",
      "server": {
        "base_url": "https://reqman.example.com",
        "frontend_url": "",
        "cookie_secure": true,
        "session_max_age_hours": 12,
        "session_idle_minutes": 120,
        "persist_sessions": true,
        "state_dir": null,
        "admins": ["alice@example.com"],
        "auth_providers": [
          {"id": "gitlab", "type": "gitlab", "name": "GitLab", "url": "https://gitlab.example.com",
           "client_id": "...", "client_secret_env": "REQMAN_GITLAB_SECRET"},
          {"id": "sso", "type": "oidc", "name": "Company SSO",
           "issuer": "https://sso.example.com/realms/main", "client_id": "..."}
        ]
      }
    }

Secrets: a provider's client secret comes from ``REQMAN_AUTH_<ID>_CLIENT_SECRET``
(``<ID>`` = the provider id upper-cased, other characters as ``_``), else the
variable named by ``client_secret_env``, else ``client_secret`` in the file.
``REQMAN_AUTH_<ID>_CLIENT_ID`` likewise overrides ``client_id``. The secret is
never logged, returned by an endpoint or sent to the browser.

Other environment overrides: ``REQMAN_SERVER_BASE_URL``,
``REQMAN_SERVER_FRONTEND_URL``, ``REQMAN_SERVER_COOKIE_SECURE`` (``0``/``1``),
``REQMAN_SERVER_STATE_DIR``, ``REQMAN_SERVER_ADMINS`` (comma-separated).
"""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROVIDER_TYPES = ("gitlab", "oidc")
DEFAULT_GITLAB_URL = "https://gitlab.com"
DEFAULT_SCOPES = ("openid", "profile", "email")
_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ServerConfigError(ValueError):
    """The ``server`` section of the config file is missing or invalid."""


@dataclass(frozen=True)
class ProviderSettings:
    id: str
    type: str
    name: str
    issuer: str
    client_id: str
    # repr=False: never shown in a log line or traceback of the settings.
    client_secret: str = field(repr=False, default="")
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    # Allow plain-http endpoints for a non-local issuer (testing only).
    allow_http: bool = False

    def public_info(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "name": self.name, "login_url": f"/auth/login/{self.id}"}


@dataclass(frozen=True)
class ServerSettings:
    providers: tuple[ProviderSettings, ...]
    state_dir: Path
    base_url: str | None = None
    frontend_url: str = ""
    cookie_secure: bool = True
    cookie_name: str = "reqman_session"
    session_max_age_seconds: int = 12 * 3600
    session_idle_seconds: int = 2 * 3600
    persist_sessions: bool = True
    admins: tuple[str, ...] = ()
    cors_origins: tuple[str, ...] = ()

    def provider(self, provider_id: str) -> ProviderSettings | None:
        return next((p for p in self.providers if p.id == provider_id), None)


def is_local_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in _LOCAL_HOSTS


def check_endpoint_url(url: str, what: str, allow_http: bool) -> str:
    """``url`` if it is https (or http to a local host, or ``allow_http``); else ``ServerConfigError``."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and parts.hostname and (allow_http or is_local_url(url)):
        return url
    raise ServerConfigError(f"{what} must be an https URL, got {url!r}")


def _env_key(provider_id: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", provider_id.upper())


def _str(value: Any, what: str, *, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            raise ServerConfigError(f"{what} is required")
        return ""
    if not isinstance(value, str):
        raise ServerConfigError(f"{what} must be a string, got {value!r}")
    return value.strip()


def _bool(value: Any, what: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "on", "0", "false", "no", "off"):
        return value.strip().lower() in ("1", "true", "yes", "on")
    raise ServerConfigError(f"{what} must be true or false, got {value!r}")


def _positive(value: Any, what: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ServerConfigError(f"{what} must be a positive number, got {value!r}")
    return float(value)


def _parse_provider(raw: Any, index: int, env: dict[str, str]) -> ProviderSettings:
    what = f"server.auth_providers[{index}]"
    if not isinstance(raw, dict):
        raise ServerConfigError(f"{what} must be an object")
    provider_type = _str(raw.get("type"), f"{what}.type", required=True).lower()
    if provider_type not in PROVIDER_TYPES:
        raise ServerConfigError(
            f"{what}.type must be one of {', '.join(PROVIDER_TYPES)} (ldap and proxy headers are not "
            f"supported yet), got {provider_type!r}"
        )
    provider_id = _str(raw.get("id"), f"{what}.id") or provider_type
    if not _PROVIDER_ID.match(provider_id):
        raise ServerConfigError(f"{what}.id must be lower-case letters, digits, '-' or '_', got {provider_id!r}")
    allow_http = _bool(raw.get("allow_http"), f"{what}.allow_http", False)
    if provider_type == "gitlab":
        issuer = _str(raw.get("url"), f"{what}.url") or _str(raw.get("issuer"), f"{what}.issuer") or DEFAULT_GITLAB_URL
        name = _str(raw.get("name"), f"{what}.name") or "GitLab"
    else:
        issuer = _str(raw.get("issuer"), f"{what}.issuer", required=True)
        name = _str(raw.get("name"), f"{what}.name") or "Single sign-on"
    issuer = check_endpoint_url(issuer.rstrip("/"), f"{what} issuer/url", allow_http)

    key = _env_key(provider_id)
    client_id = env.get(f"REQMAN_AUTH_{key}_CLIENT_ID", "").strip() or _str(raw.get("client_id"), f"{what}.client_id")
    if not client_id:
        raise ServerConfigError(f"{what}.client_id is required (or REQMAN_AUTH_{key}_CLIENT_ID)")
    secret_env = _str(raw.get("client_secret_env"), f"{what}.client_secret_env")
    client_secret = (
        env.get(f"REQMAN_AUTH_{key}_CLIENT_SECRET", "").strip()
        or (env.get(secret_env, "").strip() if secret_env else "")
        or _str(raw.get("client_secret"), f"{what}.client_secret")
    )
    scopes_raw = raw.get("scopes")
    if scopes_raw is None:
        scopes = DEFAULT_SCOPES
    elif isinstance(scopes_raw, list) and all(isinstance(s, str) and s.strip() for s in scopes_raw):
        scopes = tuple(s.strip() for s in scopes_raw)
        if "openid" not in scopes:
            scopes = ("openid", *scopes)
    else:
        raise ServerConfigError(f"{what}.scopes must be a list of strings")
    return ProviderSettings(
        id=provider_id,
        type=provider_type,
        name=name,
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        allow_http=allow_http,
    )


def load_server_settings(
    config_values: dict[str, Any],
    config_file: Path,
    cors_origins: list[str] | tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> ServerSettings:
    """Parse the ``server`` section (``ServerConfigError`` when it is invalid or names no provider)."""
    env = dict(os.environ if env is None else env)
    raw = config_values.get("server") or {}
    if not isinstance(raw, dict):
        raise ServerConfigError("server must be an object")

    providers_raw = raw.get("auth_providers") or []
    if not isinstance(providers_raw, list):
        raise ServerConfigError("server.auth_providers must be a list")
    providers = tuple(_parse_provider(item, i, env) for i, item in enumerate(providers_raw))
    if not providers:
        raise ServerConfigError(
            f"mode is 'server' but no login provider is configured: add server.auth_providers to {config_file}"
        )
    ids = [p.id for p in providers]
    if len(set(ids)) != len(ids):
        raise ServerConfigError("server.auth_providers: provider ids must be unique")

    base_url = env.get("REQMAN_SERVER_BASE_URL", "").strip() or _str(raw.get("base_url"), "server.base_url") or None
    if base_url:
        if not base_url.lower().startswith(("http://", "https://")):
            raise ServerConfigError(f"server.base_url must be an http(s) URL, got {base_url!r}")
        base_url = base_url.rstrip("/")
    frontend_url = env.get("REQMAN_SERVER_FRONTEND_URL", "").strip() or _str(raw.get("frontend_url"), "server.frontend_url")
    if frontend_url and not frontend_url.lower().startswith(("http://", "https://")):
        raise ServerConfigError(f"server.frontend_url must be an http(s) URL, got {frontend_url!r}")
    frontend_url = frontend_url.rstrip("/")

    cookie_secure = _bool(
        env.get("REQMAN_SERVER_COOKIE_SECURE") or raw.get("cookie_secure"), "server.cookie_secure", True
    )
    state_dir_raw = env.get("REQMAN_SERVER_STATE_DIR", "").strip() or _str(raw.get("state_dir"), "server.state_dir")
    state_dir = Path(state_dir_raw).expanduser() if state_dir_raw else config_file.parent
    if not state_dir.is_absolute():
        state_dir = config_file.parent / state_dir

    admins_env = env.get("REQMAN_SERVER_ADMINS", "")
    admins_raw: Any = [a for a in admins_env.split(",")] if admins_env.strip() else raw.get("admins") or []
    if not isinstance(admins_raw, list) or not all(isinstance(a, str) for a in admins_raw):
        raise ServerConfigError("server.admins must be a list of user names or e-mail addresses")
    admins = tuple(a.strip().lower() for a in admins_raw if a.strip())

    max_age = _positive(raw.get("session_max_age_hours"), "server.session_max_age_hours", 12) * 3600
    idle = _positive(raw.get("session_idle_minutes"), "server.session_idle_minutes", 120) * 60

    return ServerSettings(
        providers=providers,
        state_dir=state_dir.resolve(),
        base_url=base_url,
        frontend_url=frontend_url,
        cookie_secure=cookie_secure,
        session_max_age_seconds=int(max_age),
        session_idle_seconds=int(idle),
        persist_sessions=_bool(raw.get("persist_sessions"), "server.persist_sessions", True),
        admins=admins,
        cors_origins=tuple(cors_origins),
    )
