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
        "admin_groups": ["acme/reqman-admins"],
        "group_roles": [
          {"group": "acme/requirements", "role": "editor", "projects": ["*"]},
          {"group": "acme/auditors", "role": "viewer", "projects": ["brake-system"]}
        ],
        "project_creation": "everyone",
        "module_lock_idle_minutes": 60,
        "require_module_checkout": true,
        "repo_roles": {"enabled": true, "cache_minutes": 5, "grace_minutes": 60},
        "discovery": {"enabled": true, "cache_minutes": 15, "max_repos": 200},
        "auth_providers": [
          {"id": "gitlab", "type": "gitlab", "name": "GitLab", "url": "https://gitlab.example.com",
           "client_id": "...", "client_secret_env": "REQMAN_GITLAB_SECRET",
           "git_hosts": ["ssh.gitlab.example.com"]},
          {"id": "github", "type": "github", "url": "https://github.com", "client_id": "..."},
          {"id": "github-app", "type": "github", "github_app": true, "url": "https://github.com",
           "client_id": "..."},
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

Roles (``permissions.py``): a user's role in a project is the highest of
their membership (``memberships.json``, managed on the admin pages) and
the ``group_roles`` rules matching one of their identity provider groups
(``"projects": ["*"]`` = every project; the default). ``admin_groups``
makes a group's members server admins at login. ``project_creation``:
``everyone`` (default; the creator becomes the project admin) or
``admins`` (server admins only).

Roles from repository permissions (``repo_access.py``): with
``repo_roles.enabled`` (default true) a project whose git remote is on a
``gitlab`` / ``github`` login provider gives its users the role of their
repository permission (cached ``cache_minutes``, last good role kept for
``grace_minutes`` when a lookup fails); ``gitlab`` logins then also request
the ``read_api`` scope. ``git_hosts``: other host names of the provider's
git remotes (e.g. an ssh host). ``github`` is plain OAuth 2.0 (default
scopes ``read:user user:email``; see ``github_oauth.py``); a GitHub App is
a ``github`` provider with ``github_app: true`` — the same login flow,
token refresh and roles, but fine-grained repository permissions installed
per organisation / repository.

Project discovery (``discovery.py``): with ``discovery.enabled`` (default
true, needs ``repo_roles``) users logged in through a ``gitlab`` / ``github``
provider see the ReqManTool projects in their repositories (topic
``reqmantool``, or a ReqManTool ``project.json`` at the root of the
``current`` branch); the scan of at most ``max_repos`` repositories (default
200) is cached per user for ``cache_minutes`` (default 15). A ``github_app``
provider scans the repositories of the app's installations the user can
access instead of ``GET /user/repos``.

Module edit locks (``module_locks.py``): a lock nobody used for
``module_lock_idle_minutes`` (default 60) expires; with
``require_module_checkout`` (default true) a module must be checked out
before anyone can change it. A checkout needs a name, which labels the
commits of the editing session (``[module][username][name]``). Set it to
``false`` to make the checkout opt-in, so unlocked modules stay freely
editable.

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

PROVIDER_TYPES = ("gitlab", "github", "oidc", "ldap", "saml", "proxy_header", "gerrit")
# Project roles, lowest first (``permissions.ROLE_RANK``).
ROLES = ("viewer", "reviewer", "editor", "project-admin")
PROJECT_CREATION = ("everyone", "admins")
DEFAULT_GITLAB_URL = "https://gitlab.com"
DEFAULT_GITHUB_URL = "https://github.com"
DEFAULT_SCOPES = ("openid", "profile", "email")
# GitHub login is plain OAuth 2.0 (no OpenID Connect): the profile and the verified e-mail addresses.
DEFAULT_GITHUB_SCOPES = ("read:user", "user:email")
# The GitLab scope the repository permission lookup needs (``GET /projects/:id``, ``/members/all``).
GITLAB_API_SCOPE = "read_api"
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
    # Other host names of the provider's git remotes (e.g. a separate ssh host), for repository roles.
    git_hosts: tuple[str, ...] = ()
    # A GitHub App instead of a GitHub OAuth App (only meaningful for type "github"): fine-grained
    # repository permissions installed per organisation / repository; discovery lists the
    # installations' repositories instead of ``GET /user/repos`` (``discovery.py``).
    github_app: bool = False
    # OIDC preset (only for type "oidc"): pre-configured templates for common providers.
    # One of: "keycloak", "entra_id", "okta", "google_workspace", "authentik", or "" (custom).
    oidc_preset: str = ""
    # OIDC group claim (only for type "oidc"): the claim name in the ID token/userinfo that contains group memberships.
    # Default: "groups" (used by Keycloak, Entra ID, Okta, Google Workspace, Authentik).
    oidc_group_claim: str = "groups"
    # LDAP / Active Directory settings (type "ldap")
    ldap_url: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = field(repr=False, default="")
    ldap_user_base: str = ""
    ldap_user_filter: str = "(uid={username})"
    ldap_group_base: str = ""
    ldap_group_filter: str = "(member={user_dn})"
    ldap_group_name_attr: str = "cn"
    ldap_user_name_attr: str = "uid"
    ldap_user_email_attr: str = "mail"
    ldap_user_id_attr: str = "uid"
    ldap_tls: bool = True
    ldap_tls_verify: bool = True
    ldap_follow_referrals: bool = False
    # SAML 2.0 settings (type "saml")
    saml_entity_id: str = ""
    saml_acs_url: str = ""
    saml_idp_metadata_url: str = ""
    saml_idp_entity_id: str = ""
    saml_idp_sso_url: str = ""
    saml_idp_x509_cert: str = ""
    saml_sp_x509_cert: str = ""
    saml_sp_private_key: str = field(repr=False, default="")
    saml_name_id_format: str = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
    saml_attribute_mapping: dict[str, str] = field(default_factory=dict)
    # Trusted reverse-proxy header settings (type "proxy_header")
    proxy_header_name: str = "X-Forwarded-User"
    proxy_header_email: str = "X-Forwarded-Email"
    proxy_header_name_attr: str = "X-Forwarded-Name"
    proxy_header_groups: str = "X-Forwarded-Groups"
    proxy_header_signature: str = ""
    proxy_header_jwks_url: str = ""
    proxy_header_jwks_cache_ttl: int = 3600
    proxy_header_trusted_issuers: tuple[str, ...] = ()
    proxy_header_verify_signature: bool = True

    def public_info(self) -> dict[str, Any]:
        info = {"id": self.id, "type": self.type, "name": self.name, "login_url": f"/auth/login/{self.id}"}
        if self.type == "oidc":
            info["oidc_preset"] = self.oidc_preset
            info["oidc_group_claim"] = self.oidc_group_claim
        return info


@dataclass(frozen=True)
class GroupRole:
    """``server.group_roles`` entry: members of ``group`` get ``role`` in ``projects`` (``("*",)``: all)."""

    group: str
    role: str
    projects: tuple[str, ...] = ("*",)

    def applies_to(self, project_id: str) -> bool:
        return "*" in self.projects or project_id in self.projects


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
    max_sessions_per_user: int = 5
    admins: tuple[str, ...] = ()
    cors_origins: tuple[str, ...] = ()
    admin_groups: tuple[str, ...] = ()
    group_roles: tuple[GroupRole, ...] = ()
    project_creation: str = "everyone"
    module_lock_idle_seconds: int = 60 * 60
    require_module_checkout: bool = True
    # Roles from repository permissions (``repo_access.py``): on/off, cache lifetime, grace on errors.
    repo_roles: bool = True
    repo_roles_ttl_seconds: int = 5 * 60
    repo_roles_grace_seconds: int = 60 * 60
    # Project discovery in the login provider's repositories (``discovery.py``).
    discovery: bool = True
    discovery_ttl_seconds: int = 15 * 60
    discovery_max_repos: int = 200

    @property
    def wants_groups(self) -> bool:
        """Whether logins should collect the user's groups (a group setting is configured)."""
        return bool(self.admin_groups or self.group_roles)

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


def _parse_provider(raw: Any, index: int, env: dict[str, str], repo_roles: bool = False) -> ProviderSettings:
    what = f"server.auth_providers[{index}]"
    if not isinstance(raw, dict):
        raise ServerConfigError(f"{what} must be an object")
    provider_type = _str(raw.get("type"), f"{what}.type", required=True).lower()
    if provider_type not in PROVIDER_TYPES:
        raise ServerConfigError(
            f"{what}.type must be one of {', '.join(PROVIDER_TYPES)}, got {provider_type!r}"
        )
    provider_id = _str(raw.get("id"), f"{what}.id") or provider_type
    if not _PROVIDER_ID.match(provider_id):
        raise ServerConfigError(f"{what}.id must be lower-case letters, digits, '-' or '_', got {provider_id!r}")
    allow_http = _bool(raw.get("allow_http"), f"{what}.allow_http", False)

    # Common fields
    issuer = ""
    name = ""
    client_id = ""
    client_secret = ""
    scopes = DEFAULT_SCOPES
    git_hosts = ()
    github_app = False
    oidc_preset = ""
    oidc_group_claim = "groups"

    # LDAP-specific fields
    ldap_url = ""
    ldap_bind_dn = ""
    ldap_bind_password = ""
    ldap_user_base = ""
    ldap_user_filter = "(uid={username})"
    ldap_group_base = ""
    ldap_group_filter = "(member={user_dn})"
    ldap_group_name_attr = "cn"
    ldap_user_name_attr = "uid"
    ldap_user_email_attr = "mail"
    ldap_user_id_attr = "uid"
    ldap_tls = True
    ldap_tls_verify = True
    ldap_follow_referrals = False

    # SAML-specific fields
    saml_entity_id = ""
    saml_acs_url = ""
    saml_idp_metadata_url = ""
    saml_idp_entity_id = ""
    saml_idp_sso_url = ""
    saml_idp_x509_cert = ""
    saml_sp_x509_cert = ""
    saml_sp_private_key = ""
    saml_name_id_format = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
    saml_attribute_mapping = {}

    # Proxy header-specific fields
    proxy_header_name = "X-Forwarded-User"
    proxy_header_email = "X-Forwarded-Email"
    proxy_header_name_attr = "X-Forwarded-Name"
    proxy_header_groups = "X-Forwarded-Groups"
    proxy_header_signature = ""
    proxy_header_jwks_url = ""
    proxy_header_jwks_cache_ttl = 3600
    proxy_header_trusted_issuers = ()
    proxy_header_verify_signature = True

    if provider_type == "gitlab":
        issuer = _str(raw.get("url"), f"{what}.url") or _str(raw.get("issuer"), f"{what}.issuer") or DEFAULT_GITLAB_URL
        name = _str(raw.get("name"), f"{what}.name") or "GitLab"
    elif provider_type == "github":
        issuer = _str(raw.get("url"), f"{what}.url") or DEFAULT_GITHUB_URL
        if (urllib.parse.urlsplit(issuer).hostname or "").lower() == "api.github.com":
            issuer = DEFAULT_GITHUB_URL
        name = _str(raw.get("name"), f"{what}.name") or "GitHub"
    elif provider_type == "oidc":
        issuer = _str(raw.get("issuer"), f"{what}.issuer", required=True)
        name = _str(raw.get("name"), f"{what}.name") or "Single sign-on"
        # Apply OIDC preset if specified
        preset = _str(raw.get("oidc_preset"), f"{what}.oidc_preset") or ""
        if preset:
            preset_config = OIDC_PRESETS.get(preset)
            if not preset_config:
                raise ServerConfigError(f"{what}.oidc_preset must be one of {', '.join(OIDC_PRESETS.keys())}, got {preset!r}")
            # Apply preset defaults (can be overridden by explicit config)
            if "scopes" not in raw:
                scopes = preset_config["scopes"]
            # Note: group_claim is used by OidcClient.identity_from_tokens
        # Parse OIDC group claim (can be overridden by explicit config)
        oidc_group_claim = _str(raw.get("oidc_group_claim"), f"{what}.oidc_group_claim") or preset_config.get("group_claim", "groups")
        if oidc_group_claim and not isinstance(oidc_group_claim, str):
            raise ServerConfigError(f"{what}.oidc_group_claim must be a string")
    elif provider_type == "ldap":
        issuer = _str(raw.get("url"), f"{what}.url", required=True)
        name = _str(raw.get("name"), f"{what}.name") or "LDAP / Active Directory"
    elif provider_type == "saml":
        issuer = _str(raw.get("entity_id"), f"{what}.entity_id", required=True)
        name = _str(raw.get("name"), f"{what}.name") or "SAML 2.0"
    elif provider_type == "proxy_header":
        issuer = _str(raw.get("issuer"), f"{what}.issuer", required=True)
        name = _str(raw.get("name"), f"{what}.name") or "Trusted Proxy Header"
    elif provider_type == "gerrit":
        issuer = _str(raw.get("url"), f"{what}.url", required=True)
        name = _str(raw.get("name"), f"{what}.name") or "Gerrit"
    else:
        issuer = _str(raw.get("issuer"), f"{what}.issuer", required=True)
        name = _str(raw.get("name"), f"{what}.name") or "Single sign-on"

    if provider_type in ("gitlab", "github", "oidc", "saml", "gerrit"):
        issuer = check_endpoint_url(issuer.rstrip("/"), f"{what} issuer/url", allow_http)
    elif provider_type == "ldap":
        if not issuer.startswith(("ldap://", "ldaps://")):
            raise ServerConfigError(f"{what}.url must be an ldap:// or ldaps:// URL")
        # LDAP URLs don't go through check_endpoint_url

    # OIDC presets: pre-configured templates for common providers
    OIDC_PRESETS = {
        "keycloak": {
            "scopes": ("openid", "profile", "email", "groups"),
            "group_claim": "groups",
        },
        "entra_id": {
            "scopes": ("openid", "profile", "email", "groups"),
            "group_claim": "groups",
        },
        "okta": {
            "scopes": ("openid", "profile", "email", "groups"),
            "group_claim": "groups",
        },
        "google_workspace": {
            "scopes": ("openid", "profile", "email", "groups"),
            "group_claim": "groups",
        },
        "authentik": {
            "scopes": ("openid", "profile", "email", "groups"),
            "group_claim": "groups",
        },
    }

    key = _env_key(provider_id)
    if provider_type in ("gitlab", "github", "oidc", "saml", "gerrit"):
        client_id = env.get(f"REQMAN_AUTH_{_env_key(provider_id)}_CLIENT_ID", "").strip() or _str(raw.get("client_id"), f"{what}.client_id")
        if not client_id:
            raise ServerConfigError(f"{what}.client_id is required (or REQMAN_AUTH_{_env_key(provider_id)}_CLIENT_ID)")
        secret_env = _str(raw.get("client_secret_env"), f"{what}.client_secret_env")
        client_secret = (
            env.get(f"REQMAN_AUTH_{_env_key(provider_id)}_CLIENT_SECRET", "").strip()
            or (env.get(secret_env, "").strip() if secret_env else "")
            or _str(raw.get("client_secret"), f"{what}.client_secret")
        )
        if provider_type == "saml":
            # For SAML, client_id is the SP entity ID
            pass
        else:
            if not client_id:
                raise ServerConfigError(f"{what}.client_id is required (or REQMAN_AUTH_{_env_key(provider_id)}_CLIENT_ID)")
    elif provider_type == "ldap":
        # LDAP uses bind DN/password instead of client_id/secret
        ldap_bind_dn = _str(raw.get("bind_dn"), f"{what}.bind_dn", required=True)
        ldap_bind_password = env.get(f"REQMAN_AUTH_{_env_key(provider_id)}_BIND_PASSWORD", "").strip() or _str(raw.get("bind_password"), f"{what}.bind_password")
        if not ldap_bind_password:
            raise ServerConfigError(f"{what}.bind_password is required (or REQMAN_AUTH_{_env_key(provider_id)}_BIND_PASSWORD)")
    elif provider_type == "proxy_header":
        # Proxy header uses trusted issuer validation
        proxy_header_trusted_issuers = tuple(_string_list(raw.get("trusted_issuers"), f"{what}.trusted_issuers"))
        if not proxy_header_trusted_issuers:
            raise ServerConfigError(f"{what}.trusted_issuers is required for proxy_header type")
        proxy_header_signature = env.get(f"REQMAN_AUTH_{_env_key(provider_id)}_SIGNATURE", "").strip() or _str(raw.get("signature"), f"{what}.signature")
        proxy_header_verify_signature = _bool(raw.get("verify_signature"), f"{what}.verify_signature", True)

    # Type-specific fields
    if provider_type == "ldap":
        ldap_url = _str(raw.get("url"), f"{what}.url", required=True)
        ldap_bind_dn = _str(raw.get("bind_dn"), f"{what}.bind_dn", required=True)
        ldap_user_base = _str(raw.get("user_base"), f"{what}.user_base", required=True)
        ldap_user_filter = _str(raw.get("user_filter"), f"{what}.user_filter") or "(uid={username})"
        ldap_group_base = _str(raw.get("group_base"), f"{what}.group_base", required=True)
        ldap_group_filter = _str(raw.get("group_filter"), f"{what}.group_filter") or "(member={user_dn})"
        ldap_group_name_attr = _str(raw.get("group_name_attr"), f"{what}.group_name_attr") or "cn"
        ldap_user_name_attr = _str(raw.get("user_name_attr"), f"{what}.user_name_attr") or "uid"
        ldap_user_email_attr = _str(raw.get("user_email_attr"), f"{what}.user_email_attr") or "mail"
        ldap_user_id_attr = _str(raw.get("user_id_attr"), f"{what}.user_id_attr") or "uid"
        ldap_tls = _bool(raw.get("tls"), f"{what}.tls", True)
        ldap_tls_verify = _bool(raw.get("tls_verify"), f"{what}.tls_verify", True)
        ldap_follow_referrals = _bool(raw.get("follow_referrals"), f"{what}.follow_referrals", False)
        scopes = ()  # LDAP doesn't use OAuth scopes
    elif provider_type == "saml":
        saml_entity_id = _str(raw.get("entity_id"), f"{what}.entity_id", required=True)
        saml_acs_url = _str(raw.get("acs_url"), f"{what}.acs_url", required=True)
        saml_idp_metadata_url = _str(raw.get("idp_metadata_url"), f"{what}.idp_metadata_url")
        saml_idp_entity_id = _str(raw.get("idp_entity_id"), f"{what}.idp_entity_id", required=True)
        saml_idp_sso_url = _str(raw.get("idp_sso_url"), f"{what}.idp_sso_url", required=True)
        saml_idp_x509_cert = _str(raw.get("idp_x509_cert"), f"{what}.idp_x509_cert", required=True)
        saml_sp_x509_cert = _str(raw.get("sp_x509_cert"), f"{what}.sp_x509_cert")
        saml_sp_private_key = env.get(f"REQMAN_AUTH_{_env_key(provider_id)}_SP_PRIVATE_KEY", "").strip() or _str(raw.get("sp_private_key"), f"{what}.sp_private_key")
        saml_name_id_format = _str(raw.get("name_id_format"), f"{what}.name_id_format") or "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
        saml_attribute_mapping = _dict_of_str(raw.get("attribute_mapping"), f"{what}.attribute_mapping") or {}
        scopes = ()  # SAML doesn't use OAuth scopes
    elif provider_type == "proxy_header":
        proxy_header_name = _str(raw.get("header_name"), f"{what}.header_name") or "X-Forwarded-User"
        proxy_header_email = _str(raw.get("header_email"), f"{what}.header_email") or "X-Forwarded-Email"
        proxy_header_name_attr = _str(raw.get("header_name_attr"), f"{what}.header_name_attr") or "X-Forwarded-Name"
        proxy_header_groups = _str(raw.get("header_groups"), f"{what}.header_groups") or "X-Forwarded-Groups"
        proxy_header_jwks_url = _str(raw.get("jwks_url"), f"{what}.jwks_url")
        proxy_header_jwks_cache_ttl = int(_positive(raw.get("jwks_cache_ttl"), f"{what}.jwks_cache_ttl", 3600))
        proxy_header_trusted_issuers = tuple(_string_list(raw.get("trusted_issuers"), f"{what}.trusted_issuers"))
        if not proxy_header_trusted_issuers:
            raise ServerConfigError(f"{what}.trusted_issuers is required for proxy_header type")
        proxy_header_verify_signature = _bool(raw.get("verify_signature"), f"{what}.verify_signature", True)
        scopes = ()  # Proxy header doesn't use OAuth scopes
    elif provider_type == "gerrit":
        # Gerrit uses OAuth 2.0 (via the OIDC frontend) or its own OAuth plugin
        # The issuer is the Gerrit base URL
        scopes = ("openid", "profile", "email")
    else:
        scopes_raw = raw.get("scopes")
        if scopes_raw is None:
            scopes = DEFAULT_GITHUB_SCOPES if provider_type == "github" else DEFAULT_SCOPES
        elif isinstance(scopes_raw, list) and all(isinstance(s, str) and s.strip() for s in scopes_raw):
            scopes = tuple(s.strip() for s in scopes_raw)
            if "openid" not in scopes and provider_type != "github":
                scopes = ("openid", *scopes)
        else:
            raise ServerConfigError(f"{what}.scopes must be a list of strings")

    if provider_type in ("gitlab", "github", "oidc"):
        client_id = env.get(f"REQMAN_AUTH_{_env_key(provider_id)}_CLIENT_ID", "").strip() or _str(raw.get("client_id"), f"{what}.client_id")
        if not client_id:
            raise ServerConfigError(f"{what}.client_id is required (or REQMAN_AUTH_{_env_key(provider_id)}_CLIENT_ID)")
        secret_env = _str(raw.get("client_secret_env"), f"{what}.client_secret_env")
        client_secret = (
            env.get(f"REQMAN_AUTH_{_env_key(provider_id)}_CLIENT_SECRET", "").strip()
            or (env.get(secret_env, "").strip() if secret_env else "")
            or _str(raw.get("client_secret"), f"{what}.client_secret")
        )
    elif provider_type == "ldap":
        pass  # handled above
    elif provider_type == "saml":
        pass  # handled above
    elif provider_type == "proxy_header":
        pass  # handled above

    if provider_type in ("gitlab", "github", "oidc", "saml", "gerrit"):
        scopes_raw = raw.get("scopes")
        if scopes_raw is None:
            scopes = DEFAULT_GITHUB_SCOPES if provider_type == "github" else DEFAULT_SCOPES
        elif isinstance(scopes_raw, list) and all(isinstance(s, str) and s.strip() for s in scopes_raw):
            scopes = tuple(s.strip() for s in scopes_raw)
            if "openid" not in scopes and provider_type != "github":
                scopes = ("openid", *scopes)
        else:
            raise ServerConfigError(f"{what}.scopes must be a list of strings")

    if provider_type in ("gitlab", "gerrit") and repo_roles and GITLAB_API_SCOPE not in scopes and "api" not in scopes:
        scopes = (*scopes, GITLAB_API_SCOPE)

    git_hosts = tuple(h.lower() for h in _string_list(raw.get("git_hosts"), f"{what}.git_hosts"))
    github_app = _bool(raw.get("github_app"), f"{what}.github_app", False)
    if github_app and provider_type != "github":
        raise ServerConfigError(f"{what}.github_app can only be set on a provider of type github")

    return ProviderSettings(
        id=provider_id,
        type=provider_type,
        name=name,
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        allow_http=allow_http,
        git_hosts=git_hosts,
        github_app=github_app,
        ldap_url=ldap_url,
        ldap_bind_dn=ldap_bind_dn,
        ldap_bind_password=ldap_bind_password,
        ldap_user_base=ldap_user_base,
        ldap_user_filter=ldap_user_filter,
        ldap_group_base=ldap_group_base,
        ldap_group_filter=ldap_group_filter,
        ldap_group_name_attr=ldap_group_name_attr,
        ldap_user_name_attr=ldap_user_name_attr,
        ldap_user_email_attr=ldap_user_email_attr,
        ldap_user_id_attr=ldap_user_id_attr,
        ldap_tls=ldap_tls,
        ldap_tls_verify=ldap_tls_verify,
        ldap_follow_referrals=ldap_follow_referrals,
        saml_entity_id=saml_entity_id,
        saml_acs_url=saml_acs_url,
        saml_idp_metadata_url=saml_idp_metadata_url,
        saml_idp_entity_id=saml_idp_entity_id,
        saml_idp_sso_url=saml_idp_sso_url,
        saml_idp_x509_cert=saml_idp_x509_cert,
        saml_sp_x509_cert=saml_sp_x509_cert,
        saml_sp_private_key=saml_sp_private_key,
        saml_name_id_format=saml_name_id_format,
        saml_attribute_mapping=saml_attribute_mapping,
        proxy_header_name=proxy_header_name,
        proxy_header_email=proxy_header_email,
        proxy_header_name_attr=proxy_header_name_attr,
        proxy_header_groups=proxy_header_groups,
        proxy_header_signature=proxy_header_signature,
        proxy_header_jwks_url=proxy_header_jwks_url,
        proxy_header_jwks_cache_ttl=proxy_header_jwks_cache_ttl,
        proxy_header_trusted_issuers=proxy_header_trusted_issuers,
        proxy_header_verify_signature=proxy_header_verify_signature,
        oidc_preset=oidc_preset,
        oidc_group_claim=oidc_group_claim,
    )


def _string_list(value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ServerConfigError(f"{what} must be a list of non-empty strings")
    return tuple(v.strip() for v in value)


def _dict_of_str(value: Any, what: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ServerConfigError(f"{what} must be an object")
    result = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ServerConfigError(f"{what} must be a string-to-string mapping")
        result[k.strip()] = v.strip()
    return result


def _parse_group_roles(raw: Any) -> tuple[GroupRole, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ServerConfigError("server.group_roles must be a list")
    rules = []
    for index, item in enumerate(raw):
        what = f"server.group_roles[{index}]"
        if not isinstance(item, dict):
            raise ServerConfigError(f"{what} must be an object")
        group = _str(item.get("group"), f"{what}.group", required=True)
        role = _str(item.get("role"), f"{what}.role", required=True)
        if role not in ROLES:
            raise ServerConfigError(f"{what}.role must be one of {', '.join(ROLES)}, got {role!r}")
        projects = _string_list(item.get("projects"), f"{what}.projects") or ("*",)
        rules.append(GroupRole(group=group, role=role, projects=projects))
    return tuple(rules)


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
    repo_raw = raw.get("repo_roles")
    if repo_raw is None:
        repo_raw = {}
    elif isinstance(repo_raw, bool):
        repo_raw = {"enabled": repo_raw}
    elif not isinstance(repo_raw, dict):
        raise ServerConfigError("server.repo_roles must be an object (or true/false)")
    repo_roles = _bool(repo_raw.get("enabled"), "server.repo_roles.enabled", True)
    repo_ttl = _positive(repo_raw.get("cache_minutes"), "server.repo_roles.cache_minutes", 5) * 60
    repo_grace = _positive(repo_raw.get("grace_minutes"), "server.repo_roles.grace_minutes", 60) * 60
    discovery_raw = raw.get("discovery")
    if discovery_raw is None:
        discovery_raw = {}
    elif isinstance(discovery_raw, bool):
        discovery_raw = {"enabled": discovery_raw}
    elif not isinstance(discovery_raw, dict):
        raise ServerConfigError("server.discovery must be an object (or true/false)")
    discovery = _bool(discovery_raw.get("enabled"), "server.discovery.enabled", True)
    discovery_ttl = _positive(discovery_raw.get("cache_minutes"), "server.discovery.cache_minutes", 15) * 60
    max_repos = discovery_raw.get("max_repos", 200)
    if isinstance(max_repos, bool) or not isinstance(max_repos, int) or not 1 <= max_repos <= 5000:
        raise ServerConfigError(f"server.discovery.max_repos must be an integer from 1 to 5000, got {max_repos!r}")
    providers = tuple(_parse_provider(item, i, env, repo_roles) for i, item in enumerate(providers_raw))
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

    project_creation = _str(raw.get("project_creation"), "server.project_creation") or "everyone"
    if project_creation not in PROJECT_CREATION:
        raise ServerConfigError(
            f"server.project_creation must be one of {', '.join(PROJECT_CREATION)}, got {project_creation!r}"
        )

    max_age = _positive(raw.get("session_max_age_hours"), "server.session_max_age_hours", 12) * 3600
    idle = _positive(raw.get("session_idle_minutes"), "server.session_idle_minutes", 120) * 60
    lock_idle = _positive(raw.get("module_lock_idle_minutes"), "server.module_lock_idle_minutes", 60) * 60
    max_sessions = int(_positive(raw.get("max_sessions_per_user"), "server.max_sessions_per_user", 5))

    return ServerSettings(
        providers=providers,
        state_dir=state_dir.resolve(),
        base_url=base_url,
        frontend_url=frontend_url,
        cookie_secure=cookie_secure,
        session_max_age_seconds=int(max_age),
        session_idle_seconds=int(idle),
        persist_sessions=_bool(raw.get("persist_sessions"), "server.persist_sessions", True),
        max_sessions_per_user=max_sessions,
        admins=admins,
        cors_origins=tuple(cors_origins),
        admin_groups=_string_list(raw.get("admin_groups"), "server.admin_groups"),
        group_roles=_parse_group_roles(raw.get("group_roles")),
        project_creation=project_creation,
        module_lock_idle_seconds=int(lock_idle),
        require_module_checkout=_bool(
            raw.get("require_module_checkout"), "server.require_module_checkout", True
        ),
        repo_roles=repo_roles,
        repo_roles_ttl_seconds=int(repo_ttl),
        repo_roles_grace_seconds=int(max(repo_grace, repo_ttl)),
        discovery=discovery,
        discovery_ttl_seconds=int(discovery_ttl),
        discovery_max_repos=max_repos,
    )
