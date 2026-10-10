"""LDAP / Active Directory authentication for ReqManTool server edition.

Supports LDAPS and StartTLS, user/group lookup with nested group support.
Uses python-ldap (requires libldap2-dev / openldap-devel).
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Any, Optional

from .settings import ProviderSettings, ServerConfigError

logger = logging.getLogger("reqman_server.ldap")

try:
    import ldap
    import ldap.modlist as modlist
    LDAP_AVAILABLE = True
except ImportError:
    LDAP_AVAILABLE = False
    ldap = None  # type: ignore


class LdapError(Exception):
    """LDAP authentication or configuration error; message is safe for users."""
    pass


@dataclass
class LdapUser:
    """User information retrieved from LDAP."""
    dn: str
    username: str
    name: str
    email: str
    user_id: str
    groups: tuple[str, ...]


def _get_ldap_option(ldap_module: Any, option: int, default: Any = None) -> Any:
    """Safely get an LDAP option that may not exist in all python-ldap versions."""
    try:
        return ldap_module.get_option(option)
    except (AttributeError, ldap.LDAPError):
        return default


class LdapClient:
    """LDAP/Active Directory client for authentication and user lookup."""

    def __init__(self, provider: ProviderSettings):
        if not LDAP_AVAILABLE:
            raise LdapError("python-ldap is not installed. Install with: pip install python-ldap")

        self.provider = provider
        self._conn: Optional[Any] = None

    def _create_connection(self) -> Any:
        """Create and configure an LDAP connection."""
        conn = ldap.initialize(self.provider.ldap_url)

        # TLS settings
        if self.provider.ldap_tls:
            if self.provider.ldap_url.startswith("ldaps://"):
                # LDAPS: TLS is implicit
                conn.set_option(ldap.OPT_X_TLS_REQUIRE_CERT,
                                ldap.OPT_X_TLS_DEMAND if self.provider.ldap_tls_verify
                                else ldap.OPT_X_TLS_NEVER)
            else:
                # StartTLS
                conn.set_option(ldap.OPT_X_TLS_REQUIRE_CERT,
                                ldap.OPT_X_TLS_DEMAND if self.provider.ldap_tls_verify
                                else ldap.OPT_X_TLS_NEVER)
                conn.start_tls_s()

        # Referrals
        conn.set_option(ldap.OPT_REFERRALS, int(self.provider.ldap_follow_referrals))

        # Timeouts
        conn.set_option(ldap.OPT_TIMEOUT, 10)
        conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 10)

        return conn

    def _bind_service_account(self, conn: Any) -> None:
        """Bind with the service account (bind DN/password)."""
        if not self.provider.ldap_bind_dn or not self.provider.ldap_bind_password:
            raise LdapError("Service account bind DN and password are required")
        try:
            conn.simple_bind_s(self.provider.ldap_bind_dn, self.provider.ldap_bind_password)
        except ldap.INVALID_CREDENTIALS:
            raise LdapError("Service account bind failed: invalid credentials")
        except ldap.LDAPError as e:
            raise LdapError(f"Service account bind failed: {e}")

    def _search_user(self, conn: Any, username: str) -> tuple[str, dict[str, list[str]]]:
        """Search for a user by username, return (dn, attributes)."""
        filter_str = self.provider.ldap_user_filter.replace("{username}", ldap.filter.escape_filter_chars(username))
        try:
            result = conn.search_s(
                self.provider.ldap_user_base,
                ldap.SCOPE_SUBTREE,
                filter_str,
                [self.provider.ldap_user_name_attr,
                 self.provider.ldap_user_email_attr,
                 self.provider.ldap_user_id_attr]
            )
        except ldap.LDAPError as e:
            raise LdapError(f"User search failed: {e}")

        if not result:
            raise LdapError("Invalid username or password")
        if len(result) > 1:
            raise LdapError("Multiple users found for username")

        dn, attrs = result[0]
        return dn, attrs

    def _get_user_groups(self, conn: Any, user_dn: str) -> list[str]:
        """Get the user's group names (supports nested groups)."""
        filter_str = self.provider.ldap_group_filter.replace("{user_dn}", ldap.filter.escape_filter_chars(user_dn))
        try:
            result = conn.search_s(
                self.provider.ldap_group_base,
                ldap.SCOPE_SUBTREE,
                filter_str,
                [self.provider.ldap_group_name_attr]
            )
        except ldap.LDAPError as e:
            logger.warning("Group search failed: %s", e)
            return []

        groups = []
        for _, attrs in result:
            group_names = attrs.get(self.provider.ldap_group_name_attr, [])
            for name in group_names:
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                if name not in groups:
                    groups.append(name)

        return groups

    def authenticate(self, username: str, password: str) -> LdapUser:
        """Authenticate a user and return their information with groups."""
        conn = self._create_connection()
        try:
            # First bind with service account to search for the user
            self._bind_service_account(conn)

            # Find the user
            user_dn, attrs = self._search_user(conn, username)

            # Get user attributes
            name_attr = self.provider.ldap_user_name_attr
            email_attr = self.provider.ldap_user_email_attr
            id_attr = self.provider.ldap_user_id_attr

            user_name = ""
            if name_attr in attrs and attrs[name_attr]:
                val = attrs[name_attr][0]
                user_name = val.decode("utf-8", errors="replace") if isinstance(val, bytes) else str(val)

            user_email = ""
            if email_attr in attrs and attrs[email_attr]:
                val = attrs[email_attr][0]
                user_email = val.decode("utf-8", errors="replace") if isinstance(val, bytes) else str(val)

            user_id = ""
            if id_attr in attrs and attrs[id_attr]:
                val = attrs[id_attr][0]
                user_id = val.decode("utf-8", errors="replace") if isinstance(val, bytes) else str(val)

            # Get user's groups
            group_names = self._get_user_groups(conn, user_dn)

            # Now try to bind as the user to verify password
            try:
                user_conn = ldap.initialize(self.provider.ldap_url)
                if self.provider.ldap_tls:
                    if self.provider.ldap_url.startswith("ldaps://"):
                        user_conn.set_option(ldap.OPT_X_TLS_REQUIRE_CERT,
                                            ldap.OPT_X_TLS_DEMAND if self.provider.ldap_tls_verify
                                            else ldap.OPT_X_TLS_NEVER)
                    else:
                        user_conn.set_option(ldap.OPT_X_TLS_REQUIRE_CERT,
                                            ldap.OPT_X_TLS_DEMAND if self.provider.ldap_tls_verify
                                            else ldap.OPT_X_TLS_NEVER)
                        user_conn.start_tls_s()
                user_conn.set_option(ldap.OPT_REFERRALS, int(self.provider.ldap_follow_referrals))
                user_conn.set_option(ldap.OPT_TIMEOUT, 10)
                user_conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 10)
                user_conn.simple_bind_s(user_dn, password)
            except ldap.INVALID_CREDENTIALS:
                raise LdapError("Invalid username or password")
            except ldap.LDAPError as e:
                raise LdapError(f"User bind failed: {e}")
            finally:
                try:
                    user_conn.unbind_s()
                except Exception:
                    pass

            return LdapUser(
                dn=user_dn,
                username=user_id or username,
                name=user_name,
                email=user_email,
                user_id=user_id or username,
                groups=tuple(group_names),
            )

        finally:
            try:
                conn.unbind_s()
            except Exception:
                pass

    def test_connection(self) -> bool:
        """Test the LDAP connection with service account."""
        try:
            conn = self._create_connection()
            self._bind_service_account(conn)
            # Try a simple search to verify access
            conn.search_s(self.provider.ldap_user_base, ldap.SCOPE_BASE, "(objectClass=*)", ["1.1"])
            conn.unbind_s()
            return True
        except Exception as e:
            logger.error("LDAP test connection failed: %s", e)
            return False