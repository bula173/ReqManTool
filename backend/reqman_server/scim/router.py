"""SCIM 2.0 router (RFC 7644)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.status import HTTP_201_CREATED, HTTP_404_NOT_FOUND, HTTP_409_CONFLICT

from reqman_server.scim.models import ScimListResponse, ScimError, ScimUser, ScimGroup
from reqman_server.scim.storage import ScimStorage


router = APIRouter(prefix="/scim/v2", tags=["SCIM"])


def get_scim_storage(request: Request) -> ScimStorage:
    return request.app.state.scim_storage


def scim_error(status: int, detail: str, scim_type: str = "") -> JSONResponse:
    from reqman_server.scim.models import ScimError
    error = ScimError(status=status, detail=detail, scim_type=scim_type)
    return JSONResponse(status_code=status, content=error.to_dict())


# --- Service Provider Configuration ---

@router.get("/ServiceProviderConfig")
def service_provider_config():
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 100},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "name": "OAuth Bearer Token",
                "description": "Authentication via OAuth 2.0 Bearer Token",
                "type": "oauthbearertoken",
                "primary": True,
            }
        ],
    }


@router.get("/ResourceTypes")
def resource_types():
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 2,
        "itemsPerPage": 2,
        "startIndex": 1,
        "Resources": [
            {
                "name": "User",
                "description": "User Account",
                "endpoint": "/scim/v2/Users",
                "schema": "urn:ietf:params:scim:schemas:core:2.0:User",
                "schemaExtensions": [],
            },
            {
                "name": "Group",
                "description": "Group",
                "endpoint": "/scim/v2/Groups",
                "schema": "urn:ietf:params:scim:schemas:core:2.0:Group",
                "schemaExtensions": [],
            },
        ],
    }


@router.get("/Schemas")
def schemas():
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 2,
        "itemsPerPage": 2,
        "startIndex": 1,
        "Resources": [
            {
                "id": "urn:ietf:params:scim:schemas:core:2.0:User",
                "name": "User",
                "description": "User Account",
                "attributes": [
                    {"name": "userName", "type": "string", "multiValued": False, "required": True, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "server"},
                    {"name": "name", "type": "complex", "multiValued": False, "required": False, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "displayName", "type": "string", "multiValued": False, "required": False, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "active", "type": "boolean", "multiValued": False, "required": True, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "emails", "type": "complex", "multiValued": True, "required": False, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "groups", "type": "complex", "multiValued": True, "required": False, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                ],
            },
            {
                "id": "urn:ietf:params:scim:schemas:core:2.0:Group",
                "name": "Group",
                "description": "Group",
                "attributes": [
                    {"name": "displayName", "type": "string", "multiValued": False, "required": True, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "members", "type": "complex", "multiValued": True, "required": False, "caseExact": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                ],
            },
        ],
    }


# --- Users ---

@router.get("/Users")
def list_users(
    startIndex: int = Query(1, ge=1),
    count: int = Query(100, ge=1, le=100),
    filter: str | None = Query(None, alias="filter"),
    storage: ScimStorage = Depends(get_scim_storage),
):
    users, total = storage.list_users(startIndex=startIndex, count=count, filter_=filter)
    resp = ScimListResponse(
        total_results=total,
        items_per_page=count,
        start_index=startIndex,
        resources=users,
    )
    return resp.to_dict()


@router.post("/Users", status_code=HTTP_201_CREATED)
def create_user(user: dict, request: Request, storage: ScimStorage = Depends(get_scim_storage)):
    user_data, error = storage.create_user(user)
    if error:
        if "already exists" in error:
            return scim_error(HTTP_409_CONFLICT, error, "uniqueness")
        return scim_error(400, error)
    location = f"{str(request.base_url).rstrip('/')}/scim/v2/Users/{user_data['id']}"
    return JSONResponse(status_code=HTTP_201_CREATED, content=user_data, headers={"Location": location})


@router.get("/Users/{user_id}")
def get_user(user_id: str, storage: ScimStorage = Depends(get_scim_storage)):
    user = storage.get_user(user_id)
    if not user:
        return scim_error(HTTP_404_NOT_FOUND, f"User {user_id} not found", "noTarget")
    return user


@router.put("/Users/{user_id}")
def replace_user(user_id: str, user: dict, storage: ScimStorage = Depends(get_scim_storage)):
    user_data, error = storage.update_user(user_id, user, method="PUT")
    if error:
        return scim_error(HTTP_404_NOT_FOUND, error)
    return user_data


@router.patch("/Users/{user_id}")
def patch_user(user_id: str, user: dict, storage: ScimStorage = Depends(get_scim_storage)):
    user_data, error = storage.update_user(user_id, user, method="PATCH")
    if error:
        return scim_error(HTTP_404_NOT_FOUND, error)
    return user_data


@router.delete("/Users/{user_id}", status_code=204)
def delete_user(user_id: str, storage: ScimStorage = Depends(get_scim_storage)):
    deleted = storage.delete_user(user_id)
    if not deleted:
        return scim_error(HTTP_404_NOT_FOUND, "User not found")
    return None


# --- Groups ---

@router.get("/Groups")
def list_groups(
    startIndex: int = Query(1, ge=1),
    count: int = Query(100, ge=1, le=100),
    filter: str | None = Query(None, alias="filter"),
    storage: ScimStorage = Depends(get_scim_storage),
):
    groups, total = storage.list_groups(startIndex=startIndex, count=count, filter_=filter)
    resp = ScimListResponse(
        total_results=total,
        items_per_page=count,
        start_index=startIndex,
        resources=groups,
    )
    return resp.to_dict()


@router.post("/Groups", status_code=HTTP_201_CREATED)
def create_group(group: dict, request: Request, storage: ScimStorage = Depends(get_scim_storage)):
    group_data, error = storage.create_group(group)
    if error:
        if "already exists" in error:
            return scim_error(HTTP_409_CONFLICT, error, "uniqueness")
        return scim_error(400, error)
    return group_data


@router.get("/Groups/{group_id}")
def get_group(group_id: str, storage: ScimStorage = Depends(get_scim_storage)):
    group = storage.get_group(group_id)
    if not group:
        return scim_error(HTTP_404_NOT_FOUND, f"Group {group_id} not found", "noTarget")
    return group


@router.put("/Groups/{group_id}")
def replace_group(group_id: str, group: dict, storage: ScimStorage = Depends(get_scim_storage)):
    group_data, error = storage.update_group(group_id, group, method="PUT")
    if error:
        return scim_error(HTTP_404_NOT_FOUND, error)
    return group_data


@router.patch("/Groups/{group_id}")
def patch_group(group_id: str, group: dict, storage: ScimStorage = Depends(get_scim_storage)):
    group_data, error = storage.update_group(group_id, group, method="PATCH")
    if error:
        return scim_error(HTTP_404_NOT_FOUND, error)
    return group_data


@router.delete("/Groups/{group_id}", status_code=204)
def delete_group(group_id: str, storage: ScimStorage = Depends(get_scim_storage)):
    deleted = storage.delete_group(group_id)
    if not deleted:
        return scim_error(HTTP_404_NOT_FOUND, "Group not found")
    return None


# --- Bulk (not implemented) ---

@router.post("/Bulk", status_code=501)
def bulk():
    return scim_error(501, "Bulk operations not implemented", "notSupported")


# --- Helper ---

def scim_error(status: int, detail: str, scim_type: str = "") -> JSONResponse:
    from reqman_server.scim.models import ScimError
    error = ScimError(status=status, detail=detail, scim_type=scim_type)
    return JSONResponse(status_code=status, content=error.to_dict())