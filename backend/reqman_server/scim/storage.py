"""SCIM 2.0 storage (in-memory for now, can be extended to use JSON files)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .models import ScimUser, ScimGroup, ScimListResponse, ScimError


class ScimStorage:
    """Thread-safe SCIM storage."""

    def __init__(self, state_dir: Path | None = None):
        self._lock = threading.Lock()
        self._users: dict[str, dict] = {}  # id -> user dict
        self._users_by_external: dict[str, str] = {}  # external_id -> id
        self._users_by_username: dict[str, str] = {}  # user_name -> id
        self._groups: dict[str, dict] = {}  # id -> group dict
        self._state_dir = state_dir

    # --- Users ---

    def create_user(self, user_data: dict) -> tuple[dict, str | None]:
        """Create a user. Returns (user_data, error)."""
        with self._lock:
            external_id = user_data.get("externalId")
            user_name = user_data.get("userName")
            if external_id and external_id in self._users_by_external:
                return {}, "User with this externalId already exists"
            if user_name and user_name in self._users_by_username:
                return {}, "User with this userName already exists"

            # Generate ID if not provided
            import uuid
            user_id = user_data.get("id") or uuid.uuid4().hex[:12]
            if user_id in self._users:
                return {}, "User with this ID already exists"

            # Ensure meta is set
            from reqman_server.scim.models import ScimMeta, ScimUser
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

            user = {
                "schemas": user_data.get("schemas", ["urn:ietf:params:scim:schemas:core:2.0:User"]),
                "id": user_id,
                "externalId": user_data.get("externalId", ""),
                "userName": user_data.get("userName", ""),
                "name": user_data.get("name", {}),
                "displayName": user_data.get("displayName", ""),
                "active": user_data.get("active", True),
                "emails": user_data.get("emails", []),
                "groups": user_data.get("groups", []),
                "meta": {
                    "resourceType": "User",
                    "created": now,
                    "lastModified": now,
                    "version": "",
                    "location": "",
                },
            }
            # Add optional fields
            for k in ["nickName", "profileUrl", "title", "userType", "preferredLanguage", "locale", "timezone"]:
                if k in user_data:
                    from reqman_server.scim.models import _snake_to_camel
                    camel_key = _snake_to_camel(k)
                    if user_data[k]:
                        pass  # handled below
                for k in ["nickName", "profileUrl", "title", "userType", "preferredLanguage", "locale", "timezone"]:
                    if k in user_data and user_data[k]:
                        pass  # will be included in the dict below

                # Add all extra fields
                for k, v in user_data.items():
                    if k not in ["schemas", "id", "externalId", "userName", "name", "displayName", "active", "emails", "groups", "meta"]:
                        if v not in (None, "", [], ()):
                            user[k] = v

            self._users[user_id] = user
            if user.get("externalId"):
                self._users_by_external[user["externalId"]] = user_id
            if user.get("userName"):
                self._users_by_username[user["userName"]] = user_id

            return user, None

    def get_user(self, user_id: str) -> dict | None:
        with self._lock:
            return self._users.get(user_id)

    def get_user_by_external_id(self, external_id: str) -> dict | None:
        with self._lock:
            uid = self._users_by_external.get(external_id)
            return self._users.get(uid) if uid else None

    def get_user_by_username(self, user_name: str) -> dict | None:
        with self._lock:
            uid = self._users_by_username.get(user_name)
            return self._users.get(uid) if uid else None

    def update_user(self, user_id: str, updates: dict, method: str = "PUT") -> tuple[dict, str | None]:
        """Update a user. method: PUT (replace) or PATCH (merge)."""
        with self._lock:
            user = self._users.get(user_id)
            if not user:
                return {}, "User not found"

            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

            if method == "PATCH":
                # PATCH: merge updates
                for k, v in updates.items():
                    if k == "id" or k == "meta":
                        continue
                    if v is None:
                        user.pop(k, None)
                    else:
                        user[k] = v
            else:
                # PUT: replace (except id, meta)
                for k, v in updates.items():
                    if k == "id" or k == "meta":
                        continue
                    if v is None:
                        user.pop(k, None)
                    else:
                        user[k] = v

            user["meta"]["lastModified"] = now
            return user, None

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            user = self._users.pop(user_id, None)
            if user:
                if user.get("externalId"):
                    self._users_by_external.pop(user.get("externalId"), None)
                if user.get("userName"):
                    self._users_by_username.pop(user.get("userName"), None)
                return True
            return False

    def list_users(self, start_index: int = 1, count: int = 100, filter_: str | None = None) -> tuple[list[dict], int]:
        with self._lock:
            users = list(self._users.values())
            # Simple filter support: userName eq "value"
            if filter_:
                # Simple parser for "userName eq \"value\""
                import re
                m = re.match(r'userName\s+eq\s+"([^"]+)"', filter_)
                if m:
                    filter_value = m.group(1)
                    users = [u for u in users if u.get("userName") == filter_value]
            total = len(users)
            start = max(0, start_index - 1)
            end = start + count
            return users[start:end], total

    # --- Groups ---

    def create_group(self, group_data: dict) -> tuple[dict, str | None]:
        import uuid
        from datetime import datetime, timezone
        with self._lock:
            group_id = group_data.get("id") or uuid.uuid4().hex[:12]
            if group_id in self._groups:
                return {}, "Group with this ID already exists"
            display_name = group_data.get("displayName", "")
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            group = {
                "schemas": group_data.get("schemas", ["urn:ietf:params:scim:schemas:core:2.0:Group"]),
                "id": group_id,
                "displayName": display_name,
                "members": group_data.get("members", []),
                "meta": {
                    "resourceType": "Group",
                    "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "lastModified": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "version": "",
                    "location": "",
                },
            }
            self._groups[group_id] = group
            return group, None

    def get_group(self, group_id: str) -> dict | None:
        with self._lock:
            return self._groups.get(group_id)

    def update_group(self, group_id: str, updates: dict, method: str = "PUT") -> tuple[dict, str | None]:
        from datetime import datetime, timezone
        with self._lock:
            group = self._groups.get(group_id)
            if not group:
                return {}, "Group not found"
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if method == "PATCH":
                for k, v in updates.items():
                    if k == "id" or k == "meta":
                        continue
                    if v is None:
                        group.pop(k, None)
                    else:
                        group[k] = v
            else:
                for k, v in updates.items():
                    if k == "id" or k == "meta":
                        continue
                    if v is None:
                        group.pop(k, None)
                    else:
                        group[k] = v
            group["meta"]["lastModified"] = now
            return group, None

    def delete_group(self, group_id: str) -> bool:
        with self._lock:
            return self._groups.pop(group_id, None) is not None

    def list_groups(self, start_index: int = 1, count: int = 100, filter_: str | None = None) -> tuple[list[dict], int]:
        with self._lock:
            groups = list(self._groups.values())
            if filter_:
                import re
                m = re.match(r'displayName\s+eq\s+"([^"]+)"', filter_)
                if m:
                    filter_value = m.group(1)
                    groups = [g for g in groups if g.get("displayName") == filter_value]
            total = len(groups)
            start = max(0, start_index - 1)
            end = start + count
            return groups[start:end], total


import re
import uuid