"""SCIM 2.0 resource models (RFC 7643)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

from reqman_server.settings import ROLES


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class ScimMeta:
    """SCIM resource metadata."""
    resource_type: str
    created: str = field(default_factory=_now_iso)
    last_modified: str = field(default_factory=_now_iso)
    version: str = ""
    location: str = ""


@dataclass
class ScimName:
    """SCIM name representation."""
    formatted: str = ""
    family_name: str = ""
    given_name: str = ""
    middle_name: str = ""
    honorific_prefix: str = ""
    honorific_suffix: str = ""

    @classmethod
    def from_name(cls, name: str) -> "ScimName":
        parts = name.strip().split()
        if not parts:
            return cls()
        given = parts[0]
        family = parts[-1] if len(parts) > 1 else ""
        middle = " ".join(parts[1:-1]) if len(parts) > 2 else ""
        formatted = " ".join(parts)
        return cls(
            formatted=formatted,
            given_name=given,
            family_name=family,
            middle_name=middle,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            k: v for k, v in {
                "formatted": self.formatted,
                "familyName": self.family_name,
                "givenName": self.given_name,
                "middleName": self.middle_name,
                "honorificPrefix": self.honorific_prefix,
                "honorificSuffix": self.honorific_suffix,
            }.items() if v
        }


@dataclass
class ScimEmail:
    """SCIM email representation."""
    value: str
    type: str = "work"
    primary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "value": self.value,
            "type": self.type,
            "primary": self.primary,
        }.items() if v is not None}


@dataclass
class ScimGroupRef:
    """SCIM group reference."""
    value: str
    display: str = ""
    ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "value": self.value,
            "display": self.display or None,
            "$ref": self.ref or None,
        }.items() if v is not None}


@dataclass
class ScimUser:
    """SCIM 2.0 User resource (RFC 7643)."""
    schemas: list[str] = field(default_factory=lambda: ["urn:ietf:params:scim:schemas:core:2.0:User"])
    id: str = ""
    external_id: str = ""
    user_name: str = ""
    name: ScimName = field(default_factory=ScimName)
    display_name: str = ""
    nick_name: str = ""
    profile_url: str = ""
    title: str = ""
    user_type: str = ""
    preferred_language: str = ""
    locale: str = ""
    timezone: str = ""
    active: bool = True
    emails: list[ScimEmail] = field(default_factory=list)
    phone_numbers: list[dict] = field(default_factory=list)
    ims: list[dict] = field(default_factory=list)
    photos: list[dict] = field(default_factory=list)
    addresses: list[dict] = field(default_factory=list)
    groups: list[ScimGroupRef] = field(default_factory=list)
    entitlements: list[dict] = field(default_factory=list)
    roles: list[dict] = field(default_factory=list)
    x509_certificates: list[dict] = field(default_factory=list)
    meta: ScimMeta = field(default_factory=lambda: ScimMeta(resource_type="User"))

    def to_dict(self) -> dict[str, Any]:
        data = {
            "schemas": self.schemas,
            "id": self.id,
            "externalId": self.external_id,
            "userName": self.user_name,
            "name": self.name.to_dict(),
            "displayName": self.display_name,
            "active": self.active,
            "meta": {
                "resourceType": self.meta.resource_type,
                "created": self.meta.created,
                "lastModified": self.meta.last_modified,
                "version": self.meta.version or None,
                "location": self.meta.location or None,
            },
        }
        if self.nick_name:
            data["nickName"] = self.nick_name
        if self.profile_url:
            data["profileUrl"] = self.profile_url
        if self.title:
            data["title"] = self.title
        if self.user_type:
            data["userType"] = self.user_type
        if self.preferred_language:
            data["preferredLanguage"] = self.preferred_language
        if self.locale:
            data["locale"] = self.locale
        if self.timezone:
            data["timezone"] = self.timezone
        if self.emails:
            data["emails"] = [e.to_dict() for e in self.emails]
        if self.phone_numbers:
            data["phoneNumbers"] = self.phone_numbers
        if self.ims:
            data["ims"] = self.ims
        if self.photos:
            data["photos"] = self.photos
        if self.addresses:
            data["addresses"] = self.addresses
        if self.groups:
            data["groups"] = [g.to_dict() for g in self.groups]
        if self.entitlements:
            data["entitlements"] = self.entitlements
        if self.roles:
            data["roles"] = self.roles
        if self.x509_certificates:
            data["x509Certificates"] = self.x509_certificates
        return {k: v for k, v in data.items() if v not in (None, "", [], ())}


@dataclass
class ScimGroup:
    """SCIM 2.0 Group resource (RFC 7643)."""
    schemas: list[str] = field(default_factory=lambda: ["urn:ietf:params:scim:schemas:core:2.0:Group"])
    id: str = ""
    display_name: str = ""
    members: list[ScimGroupRef] = field(default_factory=list)
    meta: ScimMeta = field(default_factory=lambda: ScimMeta(resource_type="Group"))

    def to_dict(self) -> dict[str, Any]:
        data = {
            "schemas": self.schemas,
            "id": self.id,
            "displayName": self.display_name,
            "members": [m.to_dict() for m in self.members],
            "meta": {
                "resourceType": self.meta.resource_type,
                "created": self.meta.created,
                "lastModified": self.meta.last_modified,
                "version": self.meta.version or None,
                "location": self.meta.location or None,
            },
        }
        return {k: v for k, v in data.items() if v not in (None, "", [], ())}


@dataclass
class ScimListResponse:
    """SCIM list response (RFC 7644)."""
    schemas: list[str] = field(default_factory=lambda: ["urn:ietf:params:scim:api:messages:2.0:ListResponse"])
    total_results: int = 0
    items_per_page: int = 0
    start_index: int = 1
    resources: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemas": self.schemas,
            "totalResults": self.total_results,
            "itemsPerPage": self.items_per_page,
            "startIndex": self.start_index,
            "Resources": self.resources,
        }


@dataclass
class ScimError:
    """SCIM error response (RFC 7644)."""
    schemas: list[str] = field(default_factory=lambda: ["urn:ietf:params:scim:api:messages:2.0:Error"])
    status: int = 400
    scim_type: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "schemas": self.schemas,
            "status": str(self.status),
            "scimType": self.scim_type or None,
            "detail": self.detail or None,
        }.items() if v is not None}