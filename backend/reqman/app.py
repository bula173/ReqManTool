"""The FastAPI application: REST routes, the ``/ws`` WebSocket JSON-RPC
endpoint, and the live-sync broadcast connection manager.

Route handlers are intentionally thin — parse the request, call a
``ProjectService`` method via ``_wrap`` (which maps ``FileNotFoundError``
to 404, ``ConflictError`` and ``NewerFormatError`` to 409 and
``ValueError`` to 400), broadcast on success. All real business
logic lives in ``service.py``; see its module docstring and
docs/ARCHITECTURE.md for why. ``RPC_METHODS`` mirrors the REST surface
one-to-one so a WebSocket client (used for live requirement editing by the
frontend) and a REST client see identical behavior.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

from .config import DEFAULT_DATA_DIR, check_config_writable, load_saved_data_dir, save_data_dir
from .csv_import import MAX_CSV_IMPORT_BYTES
from .formats import NewerFormatError
from .git_repo import GitHistory, GitSettings
from .service import ConflictError, ProjectService
from .store import ProjectStore
from .templates import MAX_TEMPLATE_IMPORT_BYTES
from .version import build_id, version_info

logger = logging.getLogger("reqman.app")

CORS_ORIGINS = os.environ.get("REQMAN_CORS_ORIGINS", "http://localhost:5173").split(",")


def _initial_data_dir() -> Path:
    env_value = os.environ.get("REQMAN_DATA_DIR")
    if env_value:
        return Path(env_value).resolve()
    saved = load_saved_data_dir()
    if saved:
        return Path(saved).resolve()
    return DEFAULT_DATA_DIR.resolve()


DATA_DIR = _initial_data_dir()
store = ProjectStore(DATA_DIR)
# Per-project git history (REQMAN_GIT*, see git_repo.GitSettings); shared across data directory switches.
history = GitHistory(GitSettings.from_env())
service = ProjectService(store, history=history)

logger.info("Requirements data directory: %s", DATA_DIR)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Resolve the (cached) git build id before serving: WebSocket methods run
    # synchronously on the event loop, so the first `version.get` must not
    # wait on the git subprocess.
    build_id()
    yield


app = FastAPI(title="ReqManTool", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    def add(self, websocket: WebSocket) -> None:
        self.connections.add(websocket)

    def discard(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def broadcast(self, project_id: str, exclude: WebSocket | None = None) -> None:
        await self._send_all({"event": "changed", "project_id": project_id}, exclude)

    async def broadcast_root_changed(self) -> None:
        await self._send_all({"event": "root_changed"})

    async def broadcast_templates_changed(self, exclude: WebSocket | None = None) -> None:
        await self._send_all({"event": "templates_changed"}, exclude)

    async def _send_all(self, message: dict, exclude: WebSocket | None = None) -> None:
        for connection in list(self.connections):
            if connection is exclude:
                continue
            try:
                await connection.send_json(message)
            except Exception:  # noqa: BLE001 - a dead connection shouldn't break the broadcast
                self.connections.discard(connection)


manager = ConnectionManager()


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


# --- version ----------------------------------------------------------


@app.get("/version")
def get_version():
    return version_info()


# --- config ----------------------------------------------------------


class SetDataDirBody(BaseModel):
    data_dir: str


@app.get("/config")
def get_config():
    return {"data_dir": str(DATA_DIR)}


@app.post("/config")
async def set_config(body: SetDataDirBody):
    global DATA_DIR, store, service

    new_dir = Path(body.data_dir).expanduser().resolve()
    # Refuse before switching anything if the config file must not be
    # overwritten (written by a newer ReqManTool).
    _wrap(check_config_writable)
    try:
        new_store = ProjectStore(new_dir)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot use '{new_dir}': {exc}") from exc

    _wrap(save_data_dir, str(new_dir))
    DATA_DIR = new_dir
    store = new_store
    service = ProjectService(store, history=history)
    logger.info("Requirements data directory changed to: %s", DATA_DIR)

    await manager.broadcast_root_changed()
    return {"data_dir": str(DATA_DIR)}


@app.get("/fs/browse")
def browse_folders(path: str | None = None):
    target = Path(path).expanduser().resolve() if path else DATA_DIR.parent

    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: '{target}'")

    directories = []
    try:
        entries = list(os.scandir(target))
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot list '{target}': {exc}") from exc

    for entry in entries:
        try:
            if entry.is_dir():
                directories.append(entry.name)
        except OSError:
            continue
    directories.sort(key=str.lower)

    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "directories": [{"name": name, "path": str(target / name)} for name in directories],
    }


# --- request bodies -----------------------------------------------------------


class CreateProjectBody(BaseModel):
    name: str
    description: str = ""
    template_id: str | None = None
    parent_project_id: str | None = None


class SetProjectParentBody(BaseModel):
    parent_project_id: str | None = None


class SaveAsTemplateBody(BaseModel):
    name: str
    description: str = ""


class CreateBaselineBody(BaseModel):
    name: str
    description: str = ""


class AddAttributeBody(BaseModel):
    id: str
    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    choices: list[str] | None = None


class CreateModuleBody(BaseModel):
    name: str
    prefix: str
    description: str = ""


class AddRequirementBody(BaseModel):
    attributes: dict[str, Any] = {}
    parent_id: str | None = None


class UpdateRequirementBody(BaseModel):
    attributes: dict[str, Any] = {}


class MoveRequirementBody(BaseModel):
    parent_id: str | None = None


class ReorderRequirementBody(BaseModel):
    direction: Literal["up", "down"]


class ReorderModuleBody(BaseModel):
    direction: Literal["up", "down"]


class CopyRequirementBody(BaseModel):
    target_parent_id: str | None = None


class LinkBody(BaseModel):
    config_id: str
    source_id: str
    target_id: str


class CreateLinkConfigurationBody(BaseModel):
    name: str
    source_module_id: str
    target_module_id: str


class CreateViewBody(BaseModel):
    name: str
    attribute_ids: list[str] = []


# --- error handling -----------------------------------------------------------


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ConflictError, NewerFormatError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _media_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";", 1)[0].strip().lower()


async def _read_limited_body(request: Request, limit: int, kind: str) -> bytes:
    """The raw request body; 413 as soon as it exceeds ``limit`` bytes."""
    too_large = HTTPException(
        status_code=413,
        detail=f"The {kind} file is too large: the limit is {limit // (1024 * 1024)} MiB",
    )
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit() and int(content_length) > limit:
        raise too_large
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise too_large
    return bytes(raw)


# --- projects ----------------------------------------------------------


@app.post("/projects")
async def create_project(body: CreateProjectBody):
    project = _wrap(
        service.create_project, body.name, body.description, body.template_id, body.parent_project_id
    )
    await manager.broadcast(project.id)
    return project.to_dict()


@app.put("/projects/{project_id}/parent")
async def set_project_parent(project_id: str, body: SetProjectParentBody):
    project = _wrap(service.set_project_parent, project_id, body.parent_project_id)
    await manager.broadcast(project_id)
    return project.to_dict()


@app.post("/projects/{project_id}/save-as-template")
async def save_project_as_template(project_id: str, body: SaveAsTemplateBody):
    template = _wrap(service.save_project_as_template, project_id, body.name, body.description)
    await manager.broadcast_templates_changed()
    return template


# --- templates ----------------------------------------------------------


@app.get("/templates")
def list_templates():
    return service.list_templates()


@app.post("/templates/import")
async def import_template(request: Request):
    # The template file is sent as the raw request body (Content-Type:
    # application/json), like the CSV import: no multipart dependency, and a
    # cross-origin "simple" request (text/plain, form post) cannot import.
    if _media_type(request) != "application/json":
        raise HTTPException(
            status_code=415, detail="The template file must be sent with Content-Type: application/json"
        )
    raw = await _read_limited_body(request, MAX_TEMPLATE_IMPORT_BYTES, "template")
    try:
        # Plain UTF-8: a leading byte order mark is kept and ignored by the
        # template parser, exactly as for template files read from disk.
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="The template file must be UTF-8 encoded") from exc
    template = _wrap(service.import_template, text)
    await manager.broadcast_templates_changed()
    return template


@app.get("/projects")
def list_projects():
    return [p.to_dict() for p in _wrap(service.list_projects)]


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    return _wrap(service.get_project, project_id).to_dict()


# --- history (git) ----------------------------------------------------------


@app.get("/projects/{project_id}/git")
def get_git_status(project_id: str):
    return _wrap(service.git_status, project_id)


def _changed_despite_failure(exc: BaseException) -> bool:
    """Whether a failed request still changed the project (``EnableFailed``): broadcast it anyway."""
    return bool(getattr(exc, "project_changed", False))


@app.post("/projects/{project_id}/git/enable")
async def enable_git_history(project_id: str):
    try:
        status = _wrap(service.enable_history, project_id)
    except HTTPException as exc:
        if _changed_despite_failure(exc.__cause__):
            await manager.broadcast(project_id)
        raise
    await manager.broadcast(project_id)
    return status


@app.get("/projects/{project_id}/git/log")
def get_git_log(project_id: str, limit: int = Query(default=20, ge=1)):
    return _wrap(service.git_log, project_id, limit)


# --- baselines (git) ----------------------------------------------------------


@app.post("/projects/{project_id}/baselines")
async def create_baseline(project_id: str, body: CreateBaselineBody):
    try:
        baseline = _wrap(service.create_baseline, project_id, body.name, body.description)
    except HTTPException as exc:
        # A part-way failure (BaselineFailed) may have committed pending changes.
        if _changed_despite_failure(exc.__cause__):
            await manager.broadcast(project_id)
        raise
    await manager.broadcast(project_id)
    return baseline


@app.get("/projects/{project_id}/baselines")
def list_baselines(project_id: str):
    return _wrap(service.list_baselines, project_id)


# Declared before /baselines/{name}; the name "compare" is reserved.
@app.get("/projects/{project_id}/baselines/compare")
def compare_baselines(
    project_id: str,
    from_name: str = Query(alias="from"),
    to_name: str = Query(alias="to"),
):
    return _wrap(service.compare_baselines, project_id, from_name, to_name)


@app.get("/projects/{project_id}/baselines/{name}")
def get_baseline(project_id: str, name: str):
    return _wrap(service.get_baseline, project_id, name)


@app.get("/projects/{project_id}/baselines/{name}/modules/{module_id}/requirements")
def list_baseline_requirements(project_id: str, name: str, module_id: str):
    return [r.to_dict() for r in _wrap(service.baseline_requirements, project_id, name, module_id)]


@app.post("/projects/{project_id}/attributes")
async def add_attribute(project_id: str, body: AddAttributeBody):
    project = _wrap(
        service.add_attribute_definition,
        project_id,
        body.id,
        body.name,
        body.type,
        body.required,
        body.default,
        body.choices,
    )
    await manager.broadcast(project_id)
    return project.to_dict()


@app.post("/projects/{project_id}/link-configurations")
async def create_link_configuration(project_id: str, body: CreateLinkConfigurationBody):
    config = _wrap(
        service.create_link_configuration,
        project_id,
        body.name,
        body.source_module_id,
        body.target_module_id,
    )
    await manager.broadcast(project_id)
    return config.to_dict()


@app.get("/projects/{project_id}/link-configurations")
def list_link_configurations(project_id: str):
    return [c.to_dict() for c in _wrap(service.list_link_configurations, project_id)]


@app.delete("/projects/{project_id}/link-configurations/{config_id}")
async def remove_link_configuration(project_id: str, config_id: str):
    _wrap(service.remove_link_configuration, project_id, config_id)
    await manager.broadcast(project_id)
    return {"id": config_id, "removed": True}


@app.post("/projects/{project_id}/views")
async def create_view(project_id: str, body: CreateViewBody):
    view = _wrap(service.create_view, project_id, body.name, body.attribute_ids)
    await manager.broadcast(project_id)
    return view.to_dict()


@app.get("/projects/{project_id}/views")
def list_views(project_id: str):
    return [v.to_dict() for v in _wrap(service.list_views, project_id)]


@app.delete("/projects/{project_id}/views/{view_id}")
async def remove_view(project_id: str, view_id: str):
    _wrap(service.remove_view, project_id, view_id)
    await manager.broadcast(project_id)
    return {"id": view_id, "removed": True}


@app.get("/projects/{project_id}/requirements/{requirement_id}")
def find_requirement(project_id: str, requirement_id: str):
    module_id, requirement = _wrap(service.find_requirement, project_id, requirement_id)
    return {"module_id": module_id, "requirement": requirement.to_dict()}


@app.post("/projects/{project_id}/purge")
async def purge_project(project_id: str):
    purged = _wrap(service.purge_project, project_id)
    await manager.broadcast(project_id)
    return {"purged": purged}


@app.get("/projects/{project_id}/export/reqif")
def export_reqif(project_id: str):
    xml = _wrap(service.export_reqif, project_id)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{project_id}.reqif"'},
    )


@app.get("/projects/{project_id}/export/csv")
def export_csv(project_id: str):
    csv_text = _wrap(service.export_csv, project_id)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{project_id}.csv"'},
    )


@app.get("/projects/{project_id}/export/xlsx")
def export_xlsx(project_id: str):
    xlsx_bytes = _wrap(service.export_xlsx, project_id)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{project_id}.xlsx"'},
    )


@app.get("/projects/{project_id}/export/docx")
def export_docx(project_id: str):
    docx_bytes = _wrap(service.export_docx, project_id)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{project_id}.docx"'},
    )


# --- modules ----------------------------------------------------------


@app.post("/projects/{project_id}/modules")
async def create_module(project_id: str, body: CreateModuleBody):
    module = _wrap(service.create_module, project_id, body.name, body.prefix, body.description)
    await manager.broadcast(project_id)
    return module.to_dict()


@app.get("/projects/{project_id}/modules")
def list_modules(project_id: str):
    return [m.to_dict() for m in _wrap(service.list_modules, project_id)]


@app.get("/projects/{project_id}/modules/{module_id}")
def get_module(project_id: str, module_id: str):
    return _wrap(service.get_module, project_id, module_id).to_dict()


@app.post("/projects/{project_id}/modules/{module_id}/reorder")
async def reorder_module(project_id: str, module_id: str, body: ReorderModuleBody):
    module = _wrap(service.reorder_module, project_id, module_id, body.direction)
    await manager.broadcast(project_id)
    return module.to_dict()


@app.post("/projects/{project_id}/modules/{module_id}/import/csv")
async def import_csv(project_id: str, module_id: str, request: Request):
    # The file is sent as the raw request body (Content-Type: text/csv), so
    # no multipart parser dependency is needed. Requiring text/csv also keeps
    # cross-origin "simple" requests (text/plain, form posts) from importing.
    if _media_type(request) != "text/csv":
        raise HTTPException(status_code=415, detail="The CSV file must be sent with Content-Type: text/csv")

    raw = await _read_limited_body(request, MAX_CSV_IMPORT_BYTES, "CSV")
    try:
        csv_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="The CSV file must be UTF-8 encoded") from exc
    result = _wrap(service.import_csv, project_id, module_id, csv_text)
    await manager.broadcast(project_id)
    return result


@app.post("/projects/{project_id}/modules/{module_id}/purge")
async def purge_module(project_id: str, module_id: str):
    purged = _wrap(service.purge_module, project_id, module_id)
    await manager.broadcast(project_id)
    return {"purged": purged}


# --- requirements ----------------------------------------------------------


@app.post("/projects/{project_id}/modules/{module_id}/requirements")
async def add_requirement(project_id: str, module_id: str, body: AddRequirementBody):
    requirement = _wrap(
        service.add_requirement, project_id, module_id, body.attributes, body.parent_id
    )
    await manager.broadcast(project_id)
    return requirement.to_dict()


@app.get("/projects/{project_id}/modules/{module_id}/requirements")
def list_requirements(project_id: str, module_id: str):
    return [r.to_dict() for r in _wrap(service.list_requirements, project_id, module_id)]


@app.get("/projects/{project_id}/modules/{module_id}/requirements/{requirement_id}")
def get_requirement(project_id: str, module_id: str, requirement_id: str):
    return _wrap(service.get_requirement, project_id, module_id, requirement_id).to_dict()


@app.get("/projects/{project_id}/modules/{module_id}/requirements/{requirement_id}/history")
def get_requirement_history(project_id: str, module_id: str, requirement_id: str):
    return _wrap(service.requirement_history, project_id, module_id, requirement_id)


@app.put("/projects/{project_id}/modules/{module_id}/requirements/{requirement_id}")
async def update_requirement(project_id: str, module_id: str, requirement_id: str, body: UpdateRequirementBody):
    requirement = _wrap(
        service.update_requirement, project_id, module_id, requirement_id, body.attributes
    )
    await manager.broadcast(project_id)
    return requirement.to_dict()


@app.delete("/projects/{project_id}/modules/{module_id}/requirements/{requirement_id}")
async def remove_requirement(project_id: str, module_id: str, requirement_id: str):
    _wrap(service.remove_requirement, project_id, module_id, requirement_id)
    await manager.broadcast(project_id)
    return {"id": requirement_id, "removed": True}


@app.put("/projects/{project_id}/modules/{module_id}/requirements/{requirement_id}/parent")
async def move_requirement(project_id: str, module_id: str, requirement_id: str, body: MoveRequirementBody):
    requirement = _wrap(
        service.move_requirement, project_id, module_id, requirement_id, body.parent_id
    )
    await manager.broadcast(project_id)
    return requirement.to_dict()


@app.post("/projects/{project_id}/modules/{module_id}/requirements/{requirement_id}/reorder")
async def reorder_requirement(project_id: str, module_id: str, requirement_id: str, body: ReorderRequirementBody):
    requirement = _wrap(
        service.reorder_requirement, project_id, module_id, requirement_id, body.direction
    )
    await manager.broadcast(project_id)
    return requirement.to_dict()


@app.post("/projects/{project_id}/modules/{module_id}/requirements/{requirement_id}/copy")
async def copy_requirement(project_id: str, module_id: str, requirement_id: str, body: CopyRequirementBody):
    requirement = _wrap(
        service.copy_requirement, project_id, module_id, requirement_id, body.target_parent_id
    )
    await manager.broadcast(project_id)
    return requirement.to_dict()


# --- links ----------------------------------------------------------


@app.post("/projects/{project_id}/links")
async def link_requirements(project_id: str, body: LinkBody):
    result = _wrap(
        service.link_requirements, project_id, body.config_id, body.source_id, body.target_id
    )
    await manager.broadcast(project_id)
    return {"source": result["source"].to_dict(), "target": result["target"].to_dict()}


@app.delete("/projects/{project_id}/links")
async def unlink_requirements(project_id: str, body: LinkBody):
    result = _wrap(
        service.unlink_requirements, project_id, body.config_id, body.source_id, body.target_id
    )
    await manager.broadcast(project_id)
    return {"source": result["source"].to_dict(), "target": result["target"].to_dict()}


# --- WebSocket JSON-RPC -----------------------------------------------------

RPC_METHODS = {
    "version.get": lambda p: version_info(),
    "project.create": lambda p: service.create_project(
        p["name"], p.get("description", ""), p.get("template_id"), p.get("parent_project_id")
    ),
    "project.set_parent": lambda p: service.set_project_parent(p["id"], p.get("parent_project_id")),
    "project.save_as_template": lambda p: service.save_project_as_template(
        p["id"], p["name"], p.get("description", "")
    ),
    "template.list": lambda p: service.list_templates(),
    "template.import": lambda p: service.import_template(p["text"]),
    "project.get": lambda p: service.get_project(p["id"]),
    "project.list": lambda p: service.list_projects(),
    "project.purge": lambda p: {"purged": service.purge_project(p["id"])},
    "project.git_status": lambda p: service.git_status(p["id"]),
    "project.git_enable": lambda p: service.enable_history(p["id"]),
    "project.git_log": lambda p: service.git_log(p["id"], p.get("limit")),
    "baseline.create": lambda p: service.create_baseline(
        p["project_id"], p["name"], p.get("description", "")
    ),
    "baseline.list": lambda p: service.list_baselines(p["project_id"]),
    "baseline.get": lambda p: service.get_baseline(p["project_id"], p["name"]),
    "baseline.requirements": lambda p: service.baseline_requirements(
        p["project_id"], p["name"], p["module_id"]
    ),
    "baseline.compare": lambda p: service.compare_baselines(p["project_id"], p["from"], p["to"]),
    "attribute.add": lambda p: service.add_attribute_definition(
        p["project_id"], p["id"], p["name"], p.get("type", "string"),
        p.get("required", False), p.get("default"), p.get("choices"),
    ),
    "module.create": lambda p: service.create_module(
        p["project_id"], p["name"], p["prefix"], p.get("description", "")
    ),
    "module.get": lambda p: service.get_module(p["project_id"], p["id"]),
    "module.list": lambda p: service.list_modules(p["project_id"]),
    "module.purge": lambda p: {"purged": service.purge_module(p["project_id"], p["id"])},
    "module.reorder": lambda p: service.reorder_module(p["project_id"], p["id"], p["direction"]),
    "link_configuration.create": lambda p: service.create_link_configuration(
        p["project_id"], p["name"], p["source_module_id"], p["target_module_id"]
    ),
    "link_configuration.list": lambda p: service.list_link_configurations(p["project_id"]),
    "link_configuration.remove": lambda p: (
        service.remove_link_configuration(p["project_id"], p["id"]),
        {"id": p["id"], "removed": True},
    )[1],
    "view.create": lambda p: service.create_view(
        p["project_id"], p["name"], p.get("attribute_ids", [])
    ),
    "view.list": lambda p: service.list_views(p["project_id"]),
    "view.remove": lambda p: (
        service.remove_view(p["project_id"], p["id"]),
        {"id": p["id"], "removed": True},
    )[1],
    "requirement.add": lambda p: service.add_requirement(
        p["project_id"], p["module_id"], p.get("attributes", {}), p.get("parent_id")
    ),
    "requirement.update": lambda p: service.update_requirement(
        p["project_id"], p["module_id"], p["id"], p.get("attributes", {})
    ),
    "requirement.get": lambda p: service.get_requirement(p["project_id"], p["module_id"], p["id"]),
    "requirement.list": lambda p: service.list_requirements(p["project_id"], p["module_id"]),
    "requirement.history": lambda p: service.requirement_history(p["project_id"], p["module_id"], p["id"]),
    "requirement.remove": lambda p: (
        service.remove_requirement(p["project_id"], p["module_id"], p["id"]),
        {"id": p["id"], "removed": True},
    )[1],
    "requirement.move": lambda p: service.move_requirement(
        p["project_id"], p["module_id"], p["id"], p.get("parent_id")
    ),
    "requirement.reorder": lambda p: service.reorder_requirement(
        p["project_id"], p["module_id"], p["id"], p["direction"]
    ),
    "requirement.copy": lambda p: service.copy_requirement(
        p["project_id"], p["module_id"], p["id"], p.get("target_parent_id")
    ),
    "requirement.import_csv": lambda p: service.import_csv(
        p["project_id"], p["module_id"], p["csv"]
    ),
    "requirement.link": lambda p: service.link_requirements(
        p["project_id"], p["config_id"], p["source_id"], p["target_id"]
    ),
    "requirement.unlink": lambda p: service.unlink_requirements(
        p["project_id"], p["config_id"], p["source_id"], p["target_id"]
    ),
}


def _serialize(value: Any) -> Any:
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


def _error_response(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


MUTATING_METHODS = {
    "project.create", "project.set_parent", "project.purge", "project.git_enable", "baseline.create",
    "attribute.add",
    "module.create", "module.purge", "module.reorder",
    "link_configuration.create", "link_configuration.remove",
    "view.create", "view.remove",
    "requirement.add", "requirement.update", "requirement.remove", "requirement.move",
    "requirement.reorder", "requirement.copy", "requirement.import_csv",
    "requirement.link", "requirement.unlink",
}

# Methods that change the data directory's template set rather than a
# project: they broadcast {"event": "templates_changed"} instead of "changed".
TEMPLATE_MUTATING_METHODS = {"project.save_as_template", "template.import"}


def _project_id_of(method_name: str, params: dict, result: Any) -> str | None:
    if method_name == "project.create":
        return result.id
    return params.get("project_id") if "project_id" in params else params.get("id")


async def _dispatch(request: dict, sender: WebSocket | None = None) -> dict:
    request_id = request.get("id")
    method_name = request.get("method")
    params = request.get("params") or {}

    method = RPC_METHODS.get(method_name)
    if method is None:
        return _error_response(request_id, -32601, f"Method not found: {method_name}")

    try:
        result = method(params)
        if method_name in MUTATING_METHODS:
            project_id = _project_id_of(method_name, params, result)
            if project_id:
                await manager.broadcast(project_id, exclude=sender)
        elif method_name in TEMPLATE_MUTATING_METHODS:
            await manager.broadcast_templates_changed(exclude=sender)
        return {"jsonrpc": "2.0", "id": request_id, "result": _serialize(result)}
    except FileNotFoundError as exc:
        return _error_response(request_id, -32001, str(exc))
    except (ValueError, KeyError) as exc:
        if method_name in MUTATING_METHODS and _changed_despite_failure(exc):
            project_id = _project_id_of(method_name, params, None)
            if project_id:
                await manager.broadcast(project_id, exclude=sender)
        return _error_response(request_id, -32602, str(exc))
    except Exception as exc:  # noqa: BLE001 - surfaced to the RPC caller
        logger.exception("Unhandled error in RPC method %s", method_name)
        return _error_response(request_id, -32000, str(exc))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    manager.add(websocket)
    try:
        while True:
            raw_message = await websocket.receive_text()
            try:
                request = json.loads(raw_message)
            except json.JSONDecodeError:
                await websocket.send_json(_error_response(None, -32700, "Parse error"))
                continue
            await websocket.send_json(await _dispatch(request, sender=websocket))
    except WebSocketDisconnect:
        pass
    finally:
        manager.discard(websocket)


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="ReqManTool server")
    parser.add_argument(
        "--dir", default=None, help="Requirements storage directory (overrides any saved setting)"
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args()

    if args.dir:
        os.environ["REQMAN_DATA_DIR"] = args.dir
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("reqman.app:app", host=args.host, port=args.port)
