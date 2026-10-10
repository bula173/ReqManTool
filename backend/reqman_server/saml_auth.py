"""SAML 2.0 Service Provider implementation for ReqManTool server edition.

Supports SAML 2.0 Web Browser SSO profile with HTTP-Redirect and HTTP-POST bindings.
Uses python3-saml (requires xmlsec1).
"""

from __future__ import annotations

import base64
import logging
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from .settings import ProviderSettings, ServerConfigError, check_endpoint_url

logger = logging.getLogger("reqman_server.saml")

try:
    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    from onelogin.saml2.settings import OneLogin_Saml2_Settings
    from onelogin.saml2.utils import OneLogin_Saml2_Utils
    SAML_AVAILABLE = True
except ImportError:
    SAML_AVAILABLE = False
    OneLogin_Saml2_Auth = None  # type: ignore
    OneLogin_Saml2_Settings = None  # type: ignore
    OneLogin_Saml2_Utils = None  # type: ignore


class SamlError(Exception):
    """SAML authentication or configuration error; message is safe for users."""
    pass


@dataclass
class SamlUser:
    """User information from SAML assertion."""
    username: str
    name: str
    email: str
    user_id: str
    groups: tuple[str, ...]


class SamlClient:
    """SAML 2.0 Service Provider client."""

    def __init__(self, provider: ProviderSettings):
        if not SAML_AVAILABLE:
            raise SamlError("python3-saml is not installed. Install with: pip install python3-saml")

        self.provider = provider
        self._settings = self._build_settings()

    def _build_settings(self) -> OneLogin_Saml2_Settings:
        """Build the SAML settings object from provider configuration.

        If ``saml_idp_metadata_url`` is provided, it is validated at config time
        (must be HTTPS). The metadata is NOT fetched at runtime to avoid SSRF.
        The caller should fetch and parse metadata separately if needed.
        """
        sp_data = {
            "entityId": self.provider.saml_entity_id,
            "assertionConsumerService": {
                "url": self.provider.saml_acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
            },
            "singleLogoutService": {
                "url": f"{self.provider.saml_acs_url.replace('/acs', '/sls')}",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
            },
            "NameIDFormat": self.provider.saml_name_id_format,
            "x509cert": self.provider.saml_sp_x509_cert if self.provider.saml_sp_x509_cert else "",
            "privateKey": self.provider.saml_sp_private_key if self.provider.saml_sp_private_key else "",
        }

        idp_data = {
            "entityId": self.provider.saml_idp_entity_id,
            "singleSignOnService": {
                "url": self.provider.saml_idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
            },
            "singleLogoutService": {
                "url": self.provider.saml_idp_sso_url.replace("/sso", "/slo") if "/sso" in self.provider.saml_idp_sso_url else "",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
            },
            "x509cert": self.provider.saml_idp_x509_cert,
        }

        # Validate metadata URL at config time (no runtime fetch to prevent SSRF).
        # If metadata is needed, it should be pre-fetched and its values
        # (entityId, SSO URL, certificate) put directly into the config.
        if self.provider.saml_idp_metadata_url:
            try:
                check_endpoint_url(self.provider.saml_idp_metadata_url, "saml_idp_metadata_url", self.provider.allow_http)
            except ServerConfigError as exc:
                raise SamlError(f"Invalid SAML metadata URL: {exc}") from None
            logger.info("SAML metadata URL configured (not fetched at runtime): %s", self.provider.saml_idp_metadata_url)

        settings_dict = {
            "strict": True,
            "debug": False,
            "sp": sp_data,
            "idp": idp_data,
            "security": {
                "nameIdEncrypted": False,
                "assertionEncrypted": False,
                "signMetadata": True,
                "signAssertions": True,
                "signRequests": True,
                "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
                "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
                "wantAssertionsSigned": True,
                "wantAssertionsEncrypted": False,
                "wantNameIdEncrypted": False,
                "wantMessagesSigned": True,
                "requestedAuthnContext": True,
                "requestedAuthnContextComparison": "exact",
                "requestedAuthnContextClassRef": ["urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"],
            },
            "organization": {
                "en-US": {
                    "name": "ReqManTool",
                    "displayname": "ReqManTool",
                    "url": "https://reqmantool.example.com"
                }
            },
            "contacts": {
                "technical": {
                    "givenName": "ReqManTool",
                    "emailAddress": "admin@reqmantool.example.com"
                },
                "support": {
                    "givenName": "Support",
                    "emailAddress": "support@reqmantool.example.com"
                }
            }
        }

        try:
            settings = OneLogin_Saml2_Settings(settings_dict, custom_base_path=None, validate=True)
            return settings
        except Exception as e:
            raise SamlError(f"Invalid SAML configuration: {e}")

    def get_login_url(self, relay_state: str = "") -> str:
        """Generate the SAML login URL (HTTP-Redirect binding)."""
        try:
            # Create a mock request for the auth object
            request = {
                "https": "on",
                "http_host": self.provider.issuer.replace("https://", "").replace("http://", ""),
                "script_name": "/auth/login/saml",
                "get_data": {"RelayState": relay_state} if relay_state else {},
                "post_data": {},
            }
            auth = OneLogin_Saml2_Auth(request, self._settings)
            return auth.login(relay_state)
        except Exception as e:
            raise SamlError(f"Failed to generate SAML login URL: {e}")

    def process_acs(self, request_data: dict[str, str]) -> SamlUser:
        """Process the Assertion Consumer Service (ACS) response."""
        try:
            request = {
                "https": "on",
                "http_host": self.provider.issuer.replace("https://", "").replace("http://", ""),
                "script_name": "/auth/callback/saml",
                "get_data": {},
                "post_data": request_data,
            }
            auth = OneLogin_Saml2_Auth(request, self._settings)
            auth.process_response()

            errors = auth.get_errors()
            if errors:
                raise SamlError(f"SAML response validation failed: {', '.join(errors)}")

            if not auth.is_authenticated():
                raise SamlError("SAML authentication failed: user not authenticated")

            # Extract user attributes
            attributes = auth.get_attributes()
            name_id = auth.get_nameid()
            session_index = auth.get_session_index()

            # Map attributes using provider's attribute mapping
            attr_map = self.provider.saml_attribute_mapping
            email = self._get_attr(attributes, attr_map.get("email", "email"), "email", "mail")
            username = self._get_attr(attributes, attr_map.get("username", "username"), "username", "uid", "userid")
            name = self._get_attr(attributes, attr_map.get("name", "name"), "name", "cn", "displayName")
            groups = self._get_groups(attributes, attr_map.get("groups", "groups"))

            user_id = self._get_attr(attributes, attr_map.get("user_id", "user_id"), "user_id", "uid", "userid", "employeeNumber") or name_id

            return SamlUser(
                username=self._get_attr(attributes, attr_map.get("username", "username"), "username", "uid") or name_id,
                name=name,
                email=email,
                user_id=user_id,
                groups=tuple(groups) if groups else (),
            )
        except Exception as e:
            raise SamlError(f"SAML ACS processing failed: {e}")

    def _get_attr(self, attributes: dict, *keys: str) -> str:
        """Get the first available attribute from the list of keys."""
        for key in keys:
            if key in attributes:
                val = attributes[key]
                if isinstance(val, list) and val:
                    return val[0]
                elif val:
                    return str(val)
        return ""

    def _get_groups(self, attributes: dict, group_key: str) -> list[str]:
        """Extract group names from SAML attributes."""
        if group_key not in attributes:
            return []
        val = attributes[group_key]
        if isinstance(val, list):
            return [str(v) for v in val if v]
        elif val:
            return [str(val)]
        return []

    def get_logout_url(self, name_id: str, session_index: str, relay_state: str = "") -> str:
        """Generate the SAML logout URL."""
        try:
            request = {
                "https": "on",
                "http_host": self.provider.issuer.replace("https://", "").replace("http://", ""),
                "script_name": "/auth/logout/saml",
                "get_data": {},
                "post_data": {},
            }
            auth = OneLogin_Saml2_Auth(request, self._settings)
            return auth.logout(name_id=name_id, session_index=session_index, relay_state=relay_state)
        except Exception as e:
            raise SamlError(f"Failed to generate SAML logout URL: {e}")

    def test_configuration(self) -> bool:
        """Test the SAML configuration."""
        try:
            # Validate settings can be created
            _ = self._settings
            # Check required fields
            if not self.provider.saml_entity_id or not self.provider.saml_acs_url:
                return False
            if not self.provider.saml_idp_entity_id or not self.provider.saml_idp_sso_url:
                return False
            if not self.provider.saml_idp_x509_cert:
                return False
            return True
        except Exception as e:
            logger.error("SAML configuration test failed: %s", e)
            return False