"""The FastAPI application: REST routes, the ``/ws`` WebSocket JSON-RPC
endpoint, and the live-sync broadcast connection manager.

Route handlers are intentionally thin — parse the request, call a
``ProjectService`` method via ``_wrap`` (which maps ``FileNotFoundError``
to 404, ``ProjectLockedError`` to 423, ``ConflictError`` and
``NewerFormatError`` to 409 and ``ValueError`` to 400), broadcast on success. All real business
logic lives in ``service.py``; see its module docstring and
docs/ARCHITECTURE.md for why. ``RPC_METHODS`` mirrors the REST surface
one-to-one so a WebSocket client (used for live requirement editing by the
frontend) and a REST client see identical behavior.
"""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import check_config_writable, connections_file, frontend_dir, load_settings, save_data_dir
from .csv_import import MAX_CSV_IMPORT_BYTES
from .test_results_import import MAX_TEST_RESULTS_IMPORT_BYTES
from .formats import NewerFormatError
from .git_repo import GitHistory, GitSettings
from .providers import ProviderService
from .service import RESULT_IMPORT_OPTIONS, ConflictError, ProjectLockedError, ProjectService
from .store import ProjectStore
from .templates import MAX_TEMPLATE_IMPORT_BYTES
from .prompts import MAX_PROMPT_IMPORT_BYTES
from .version import build_id, version_info

logger = logging.getLogger("reqman.app")

# Effective settings: env (CLI flags are applied as env vars by cli.py) >
# config file > default; see config.py.
SETTINGS = load_settings()
CORS_ORIGINS = SETTINGS.cors_origins
# The built frontend served from this origin (release package), or None (API only).
FRONTEND_DIR = frontend_dir()

DATA_DIR = SETTINGS.data_dir
store = ProjectStore(DATA_DIR)
# Per-project git history (REQMAN_GIT*, see git_repo.GitSettings); shared across data directory switches.
history = GitHistory(GitSettings.from_env())
# GitLab/GitHub connections (connections.json beside the config file; tokens in
# the OS keychain, or in memory with REQMAN_TOKEN_STORE=memory); app-level,
# shared across data directory switches like the git history.
providers = ProviderService.from_env(connections_file())
service = ProjectService(store, history=history, providers=providers)

logger.info("Requirements data directory: %s", DATA_DIR)
logger.info("Config file: %s", SETTINGS.config_file)
if FRONTEND_DIR is not None:
    logger.info("Serving the frontend from: %s", FRONTEND_DIR)


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


# --- bundled frontend (single origin) ----------------------------------------
#
# Client-side routes of the SPA (frontend/src/App.tsx). Several are also API
# paths (`/projects/{id}`, `/projects/{id}/modules/{mid}`, baselines,
# traceability report), so a browser *page load* — GET/HEAD whose Accept
# header names text/html — of exactly these paths gets index.html, while
# fetch() calls (Accept: */* or application/json), `/ws`, and downloads
# (`.../export/...`, never a client route) reach the API unchanged.
SPA_ROUTE = re.compile(
    r"^/(?:projects/[^/]+"
    r"(?:/modules/[^/]+|/baselines/[^/]+|/commits(?:/[^/]+(?:/browse)?)?"
    r"|/reports/(?:traceability|verification/[^/]+|execution/[^/]+))?"
    r"|detached/projects/[^/]+/modules/[^/]+)?/?$"
)


def _accepts_html(headers: list[tuple[bytes, bytes]]) -> bool:
    for name, value in headers:
        if name == b"accept" and b"text/html" in value:
            return True
    return False


def _index_response() -> FileResponse:
    assert FRONTEND_DIR is not None
    # index.html names the hashed assets of this build: never cache it.
    return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})


class SpaNavigationMiddleware:
    """Serve index.html for a browser page load of a client-side route (see ``SPA_ROUTE``)."""

    def __init__(self, asgi_app) -> None:
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and FRONTEND_DIR is not None
            and scope["method"] in ("GET", "HEAD")
            and SPA_ROUTE.match(scope["path"])
            and _accepts_html(scope["headers"])
        ):
            await _index_response()(scope, receive, send)
            return
        await self.app(scope, receive, send)


if FRONTEND_DIR is not None:
    app.add_middleware(SpaNavigationMiddleware)
    if (FRONTEND_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="frontend-assets")


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()

    def add(self, websocket: WebSocket) -> None:
        self.connections.add(websocket)

    def discard(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def broadcast(self, project_id: str, exclude: WebSocket | None = None) -> None:
        """``changed`` for the project, then for every other project the last mutation wrote.

        A mutation may write into other projects too (removing a requirement
        deletes the links to it from referencing projects), so each of them
        gets its own event (``ProjectService.take_touched_projects``).
        """
        others = sorted(service.take_touched_projects() - {project_id})
        for changed_id in [project_id, *others]:
            await self._send_all({"event": "changed", "project_id": changed_id}, exclude)

    async def broadcast_removed(self, project_id: str, exclude: WebSocket | None = None) -> None:
        """``changed`` with ``removed: true``: the project no longer exists (clients drop it)."""
        service.take_touched_projects()
        await self._send_all({"event": "changed", "project_id": project_id, "removed": True}, exclude)

    async def broadcast_root_changed(self) -> None:
        await self._send_all({"event": "root_changed"})

    async def broadcast_templates_changed(self, exclude: WebSocket | None = None) -> None:
        await self._send_all({"event": "templates_changed"}, exclude)

    async def broadcast_prompts_changed(self, exclude: WebSocket | None = None) -> None:
        await self._send_all({"event": "prompts_changed"}, exclude)

    async def broadcast_connections_changed(self, exclude: WebSocket | None = None) -> None:
        """A GitLab/GitHub connection was added, changed or removed (never carries a token)."""
        await self._send_all({"event": "connections_changed"}, exclude)

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
    if FRONTEND_DIR is not None:
        return _index_response()
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
    service = ProjectService(store, history=history, providers=providers)
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


class SetProjectReferencesBody(BaseModel):
    # Project ids (null or [] = none). Typed loosely so the service validates
    # it and REST/WebSocket reject alike.
    references: Any = None


class SetRowStylesBody(BaseModel):
    # The full list of rules (null or [] clears them). Typed loosely so the
    # service validates it and REST/WebSocket reject alike.
    row_styles: Any = None


class SaveAsTemplateBody(BaseModel):
    name: str
    description: str = ""


class CreateBaselineBody(BaseModel):
    name: str
    description: str = ""
    # "Lock after baseline": lock the project once the baseline is created.
    lock: bool = False
    lock_reason: str | None = None


class LockProjectBody(BaseModel):
    reason: str | None = None


class RemoveProjectBody(BaseModel):
    # The project name, typed by the user to confirm.
    confirm_name: Any = None
    # The data folder the confirmation was shown for (refused when it changed).
    data_dir: Any = None


class SetGitRemoteBody(BaseModel):
    """`url: null` (or empty) removes the configured remote."""

    url: str | None = None


class SetGitAutoPushBody(BaseModel):
    enabled: bool


class AddProjectFromRemoteBody(BaseModel):
    url: str


class AddProjectFromProviderBody(BaseModel):
    connection_id: str
    # The repository path in the provider ("group/subgroup/name", "owner/name").
    repository: Any = None


class AddProviderConnectionBody(BaseModel):
    provider: Any = None
    url: Any = None
    name: Any = None
    # Written to the OS keychain; never stored in a file, logged or returned.
    token: Any = None


class UpdateProviderConnectionBody(BaseModel):
    name: Any = None
    # A new token (verified first); omitted/empty keeps the stored one.
    token: Any = None


class PublishProjectBody(BaseModel):
    connection_id: str
    namespace_id: Any = None
    name: Any = None
    private: Any = True
    description: Any = None


class StartReviewBody(BaseModel):
    branch: Any = None
    title: Any = None


class AddAttributeBody(BaseModel):
    id: str
    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    choices: list[str] | None = None
    # Module ids the attribute applies to; absent/empty = every module. Typed
    # loosely so the service validates it and REST/WebSocket reject alike.
    modules: Any = None
    # Module types the attribute applies to (with ``modules``: either one).
    module_types: Any = None


class SetAttributeModulesBody(BaseModel):
    modules: Any = None
    drop_values: Any = False
    # Replaces the type scope too; absent/null = no type scope.
    module_types: Any = None


class CreateModuleBody(BaseModel):
    name: str
    prefix: str
    description: str = ""
    # "requirements" (default), "test_cases" or "test_execution"; validated
    # by the service so REST and WebSocket reject alike.
    type: Any = None
    # The module folder to create it in; null/omitted = the top level.
    folder_id: Any = None


class CreateModuleFromTemplateBody(BaseModel):
    template_id: str
    # Overrides of the module template's defaults (null/omitted: the template's).
    name: Any = None
    prefix: Any = None
    description: Any = None
    # The module folder to create it in; null/omitted = the top level.
    folder_id: Any = None


class SaveModuleAsTemplateBody(BaseModel):
    name: str
    description: str = ""
    # A view of the project to save as the template's view; null = none.
    view_id: Any = None


class AddRequirementBody(BaseModel):
    attributes: dict[str, Any] = {}
    parent_id: str | None = None


class UpdateRequirementBody(BaseModel):
    attributes: dict[str, Any] = {}


class MoveRequirementBody(BaseModel):
    parent_id: str | None = None
    # Optional 0-based position among the new parent's children; the service
    # validates it (non-negative int) so REST and WebSocket reject alike.
    index: Any = None


class ReorderRequirementBody(BaseModel):
    direction: Literal["up", "down"]


class ReorderModuleBody(BaseModel):
    direction: Literal["up", "down"]


class CreateModuleFolderBody(BaseModel):
    # Name and parent are validated by the service so REST and WebSocket
    # reject alike; parent_id null/omitted = the top level.
    name: Any = None
    parent_id: Any = None


class RenameModuleFolderBody(BaseModel):
    name: Any = None


class MoveModuleTreeEntryBody(BaseModel):
    # The destination folder (null/omitted = the top level) and the optional
    # 0-based position among its entries; validated by the service.
    folder_id: Any = None
    index: Any = None


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
    # The project of the target module for a cross-project configuration
    # (must be referenced); null/omitted = this project.
    target_project_id: Any = None


class CreateViewBody(BaseModel):
    name: str
    attribute_ids: list[str] = []
    # Optional shared column widths; validated by ProjectService (any JSON
    # shape is accepted here so REST and WebSocket report the same errors).
    column_widths: Any = None


class ViewColumnWidthsBody(BaseModel):
    # A mapping of column key to width, or null to clear the view's widths.
    column_widths: Any


class PromptBody(BaseModel):
    # Shape and values are checked by ``prompts.parse_prompt`` (same errors as WebSocket).
    name: Any = None
    description: Any = None
    selection: Any = None
    free_text: Any = None
    free_text_label: Any = None
    import_mode: Any = None
    body: Any = None


class RenderPromptBody(BaseModel):
    prompt_id: str
    module_id: str
    requirement_ids: Any = None
    selection_module_id: Any = None
    selection_project_id: Any = None
    include_children: Any = False
    free_text: Any = None


# --- error handling -----------------------------------------------------------


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectLockedError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
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


@app.get("/projects/{project_id}/references")
def get_project_references(project_id: str):
    """The projects this one references and the projects referencing it (read-only)."""
    return _wrap(service.project_references, project_id)


@app.put("/projects/{project_id}/references")
async def set_project_references(project_id: str, body: SetProjectReferencesBody):
    project = _wrap(service.set_project_references, project_id, body.references)
    await manager.broadcast(project_id)
    return project.to_dict()


@app.put("/projects/{project_id}/lock")
async def lock_project(project_id: str, body: LockProjectBody):
    """Lock the project: every change of it is refused (423) until it is unlocked."""
    project = _wrap(service.lock_project, project_id, body.reason)
    await manager.broadcast(project_id)
    return project.to_dict()


@app.delete("/projects/{project_id}/lock")
async def unlock_project(project_id: str):
    project = _wrap(service.unlock_project, project_id)
    await manager.broadcast(project_id)
    return project.to_dict()


@app.get("/projects/{project_id}/removal")
def get_project_removal_info(project_id: str):
    """What removing the project would do: blockers (subprojects, referencing projects, lock) and recovery (read-only)."""
    return _wrap(service.project_removal_info, project_id)


@app.delete("/projects/{project_id}")
async def remove_project(project_id: str, body: RemoveProjectBody):
    """Delete the project folder (typed-name confirmation); broadcasts ``changed`` with ``removed: true``."""
    result = _wrap(service.remove_project, project_id, body.confirm_name, body.data_dir)
    await manager.broadcast_removed(project_id)
    return result


@app.post("/projects/{project_id}/save-as-template")
async def save_project_as_template(project_id: str, body: SaveAsTemplateBody):
    template = _wrap(service.save_project_as_template, project_id, body.name, body.description)
    await manager.broadcast_templates_changed()
    return template


# --- templates ----------------------------------------------------------


@app.get("/templates")
def list_templates():
    return service.list_templates()


@app.get("/module-templates")
def list_module_templates():
    return service.list_module_templates()


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


# --- AI prompt library (user data outside projects; broadcasts prompts_changed) ---


@app.get("/prompts")
def list_prompts():
    return service.list_prompts()


@app.post("/prompts")
async def create_prompt(body: PromptBody):
    prompt = _wrap(service.create_prompt, body.model_dump(exclude_none=True))
    await manager.broadcast_prompts_changed()
    return prompt


@app.post("/prompts/import")
async def import_prompt(request: Request):
    # Raw body (Content-Type: application/json), like a template import.
    if _media_type(request) != "application/json":
        raise HTTPException(status_code=415, detail="The prompt file must be sent with Content-Type: application/json")
    raw = await _read_limited_body(request, MAX_PROMPT_IMPORT_BYTES, "prompt")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="The prompt file must be UTF-8 encoded") from exc
    prompt = _wrap(service.import_prompt, text)
    await manager.broadcast_prompts_changed()
    return prompt


@app.get("/prompts/{prompt_id}")
def get_prompt(prompt_id: str):
    return _wrap(service.get_prompt, prompt_id)


@app.get("/prompts/{prompt_id}/export")
def export_prompt(prompt_id: str):
    text = _wrap(service.export_prompt, prompt_id)
    stem = prompt_id.split(":", 1)[-1]
    return Response(
        content=text,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{stem}.prompt.json"'},
    )


@app.put("/prompts/{prompt_id}")
async def update_prompt(prompt_id: str, body: PromptBody):
    prompt = _wrap(service.update_prompt, prompt_id, body.model_dump(exclude_none=True))
    await manager.broadcast_prompts_changed()
    return prompt


@app.delete("/prompts/{prompt_id}", status_code=204)
async def delete_prompt(prompt_id: str):
    _wrap(service.delete_prompt, prompt_id)
    await manager.broadcast_prompts_changed()
    return Response(status_code=204)


@app.post("/projects/{project_id}/prompts/render")
def render_prompt(project_id: str, body: RenderPromptBody):
    """Read-only: the prompt text filled from the project (never broadcast)."""
    return _wrap(
        service.render_prompt,
        project_id,
        body.prompt_id,
        body.module_id,
        body.requirement_ids,
        body.selection_module_id,
        body.selection_project_id,
        body.include_children,
        body.free_text,
    )


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


# --- commit browser (git, read-only) ---------------------------------------------


@app.get("/projects/{project_id}/git/commits")
def list_git_commits(
    project_id: str,
    module_id: str | None = None,
    requirement_id: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1),
    author: str | None = None,
    text: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    return _wrap(
        service.git_commits, project_id, module_id, requirement_id, offset, limit, author, text, since, until
    )


@app.get("/projects/{project_id}/git/commits/{commit}")
def get_git_commit(project_id: str, commit: str):
    return _wrap(service.git_commit, project_id, commit)


@app.get("/projects/{project_id}/git/commits/{commit}/snapshot")
def get_git_commit_snapshot(project_id: str, commit: str):
    return _wrap(service.get_commit_snapshot, project_id, commit)


@app.get("/projects/{project_id}/git/commits/{commit}/modules/{module_id}/requirements")
def list_git_commit_requirements(project_id: str, commit: str, module_id: str):
    return [r.to_dict() for r in _wrap(service.commit_requirements, project_id, commit, module_id)]


# --- remotes (git) ----------------------------------------------------------


@app.put("/projects/{project_id}/git/remote")
async def set_git_remote(project_id: str, body: SetGitRemoteBody):
    status = _wrap(service.set_git_remote, project_id, body.url)
    await manager.broadcast(project_id)
    return status


@app.put("/projects/{project_id}/git/auto-push")
async def set_git_auto_push(project_id: str, body: SetGitAutoPushBody):
    status = _wrap(service.set_git_auto_push, project_id, body.enabled)
    await manager.broadcast(project_id)
    return status


@app.post("/projects/{project_id}/git/push")
async def git_push(project_id: str):
    result = _wrap(service.git_push, project_id)
    await manager.broadcast(project_id)
    return result


@app.post("/projects/{project_id}/git/pull")
async def git_pull(project_id: str):
    try:
        result = _wrap(service.git_pull, project_id)
    except HTTPException as exc:
        # A diverged pull already fetched: the status now shows the sync conflict.
        if _changed_despite_failure(exc.__cause__):
            await manager.broadcast(project_id)
        raise
    await manager.broadcast(project_id)
    return result


@app.post("/projects/from-remote")
async def add_project_from_remote(body: AddProjectFromRemoteBody):
    project = _wrap(service.add_project_from_remote, body.url)
    await manager.broadcast(project.id)
    return project.to_dict()


# --- GitLab / GitHub (providers) ----------------------------------------------


@app.get("/providers/connections")
def list_provider_connections():
    return _wrap(service.list_provider_connections)


@app.post("/providers/connections")
async def add_provider_connection(body: AddProviderConnectionBody):
    connection = _wrap(service.add_provider_connection, body.provider, body.url, body.name, body.token)
    await manager.broadcast_connections_changed()
    return connection


@app.put("/providers/connections/{connection_id}")
async def update_provider_connection(connection_id: str, body: UpdateProviderConnectionBody):
    connection = _wrap(service.update_provider_connection, connection_id, body.name, body.token)
    await manager.broadcast_connections_changed()
    return connection


@app.delete("/providers/connections/{connection_id}")
async def remove_provider_connection(connection_id: str):
    result = _wrap(service.remove_provider_connection, connection_id)
    await manager.broadcast_connections_changed()
    return result


@app.get("/providers/connections/{connection_id}/namespaces")
def list_provider_namespaces(connection_id: str):
    return _wrap(service.provider_namespaces, connection_id)


@app.get("/providers/connections/{connection_id}/repositories")
def list_provider_repositories(connection_id: str, search: str | None = None, page: int = Query(default=1)):
    return _wrap(service.provider_repositories, connection_id, search, page)


@app.post("/projects/from-provider")
async def add_project_from_provider(body: AddProjectFromProviderBody):
    project = _wrap(service.add_project_from_provider, body.connection_id, body.repository)
    await manager.broadcast(project.id)
    return project.to_dict()


async def _broadcast_on_failure(project_id: str, exc: HTTPException) -> None:
    """Broadcast a failed request that still changed the project (``project_changed``)."""
    if _changed_despite_failure(exc.__cause__):
        await manager.broadcast(project_id)


@app.post("/projects/{project_id}/git/publish")
async def publish_project(project_id: str, body: PublishProjectBody):
    try:
        result = _wrap(
            service.publish_project,
            project_id,
            body.connection_id,
            body.namespace_id,
            body.name,
            body.private,
            body.description,
        )
    except HTTPException as exc:
        await _broadcast_on_failure(project_id, exc)
        raise
    await manager.broadcast(project_id)
    return result


@app.get("/projects/{project_id}/git/provider")
def get_git_provider(project_id: str):
    return _wrap(service.provider_info_for_project, project_id)


@app.get("/projects/{project_id}/git/review")
def get_git_review(project_id: str):
    return _wrap(service.review_status, project_id)


@app.post("/projects/{project_id}/git/review")
async def start_git_review(project_id: str, body: StartReviewBody):
    try:
        result = _wrap(service.start_review, project_id, body.branch, body.title)
    except HTTPException as exc:
        await _broadcast_on_failure(project_id, exc)
        raise
    await manager.broadcast(project_id)
    return result


@app.delete("/projects/{project_id}/git/review")
async def forget_git_review(project_id: str):
    status = _wrap(service.forget_review, project_id)
    await manager.broadcast(project_id)
    return status


# --- baselines (git) ----------------------------------------------------------


@app.post("/projects/{project_id}/baselines")
async def create_baseline(project_id: str, body: CreateBaselineBody):
    try:
        baseline = _wrap(
            service.create_baseline, project_id, body.name, body.description, body.lock, body.lock_reason
        )
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
        body.modules,
        body.module_types,
    )
    await manager.broadcast(project_id)
    return project.to_dict()


@app.put("/projects/{project_id}/attributes/{attr_id}/modules")
async def set_attribute_modules(project_id: str, attr_id: str, body: SetAttributeModulesBody):
    result = _wrap(
        service.set_attribute_modules, project_id, attr_id, body.modules, body.drop_values, body.module_types
    )
    await manager.broadcast(project_id)
    return {"project": result["project"].to_dict(), "dropped_values": result["dropped_values"]}


@app.put("/projects/{project_id}/row-styles")
async def set_row_styles(project_id: str, body: SetRowStylesBody):
    project = _wrap(service.set_row_styles, project_id, body.row_styles)
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
        body.target_project_id,
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
    view = _wrap(service.create_view, project_id, body.name, body.attribute_ids, body.column_widths)
    await manager.broadcast(project_id)
    return view.to_dict()


@app.put("/projects/{project_id}/views/{view_id}/column-widths")
async def set_view_column_widths(project_id: str, view_id: str, body: ViewColumnWidthsBody):
    view = _wrap(service.set_view_column_widths, project_id, view_id, body.column_widths)
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


# --- reports (read-only: no broadcast) ------------------------------------------

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
REPORT_MEDIA_TYPES = {"csv": "text/csv", "xlsx": XLSX_MEDIA_TYPE, "docx": DOCX_MEDIA_TYPE}


def _report_download(content: bytes | str, fmt: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=REPORT_MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}.{fmt}"'},
    )


@app.get("/projects/{project_id}/reports/traceability")
def traceability_matrix(project_id: str, config: list[str] = Query(default=[])):
    """Repeat ``config`` for a chain: ``?config=a&config=b``."""
    return _wrap(service.traceability_matrix, project_id, config)


@app.get("/projects/{project_id}/reports/traceability/export/{fmt}")
def export_traceability(project_id: str, fmt: str, config: list[str] = Query(default=[])):
    content = _wrap(service.export_traceability, project_id, config, fmt)
    return _report_download(content, fmt, f"{project_id}-traceability")


@app.get("/projects/{project_id}/modules/{module_id}/reports/verification")
def verification_report(project_id: str, module_id: str):
    return _wrap(service.verification_report, project_id, module_id)


@app.get("/projects/{project_id}/modules/{module_id}/reports/verification/export/{fmt}")
def export_verification_report(project_id: str, module_id: str, fmt: str):
    content = _wrap(service.export_verification_report, project_id, module_id, fmt)
    return _report_download(content, fmt, f"{project_id}-{module_id}-verification")


@app.get("/projects/{project_id}/modules/{module_id}/reports/execution")
def execution_report(project_id: str, module_id: str):
    return _wrap(service.execution_report, project_id, module_id)


@app.get("/projects/{project_id}/modules/{module_id}/reports/execution/export/{fmt}")
def export_execution_report(project_id: str, module_id: str, fmt: str):
    content = _wrap(service.export_execution_report, project_id, module_id, fmt)
    return _report_download(content, fmt, f"{project_id}-{module_id}-execution")


# --- modules ----------------------------------------------------------


@app.post("/projects/{project_id}/modules")
async def create_module(project_id: str, body: CreateModuleBody):
    module = _wrap(
        service.create_module, project_id, body.name, body.prefix, body.description, body.type, body.folder_id
    )
    await manager.broadcast(project_id)
    return module.to_dict()


@app.post("/projects/{project_id}/modules/from-template")
async def create_module_from_template(project_id: str, body: CreateModuleFromTemplateBody):
    result = _wrap(
        service.create_module_from_template,
        project_id,
        body.template_id,
        body.name,
        body.prefix,
        body.description,
        body.folder_id,
    )
    await manager.broadcast(project_id)
    return {**result, "module": result["module"].to_dict()}


@app.post("/projects/{project_id}/modules/{module_id}/save-as-template")
async def save_module_as_template(project_id: str, module_id: str, body: SaveModuleAsTemplateBody):
    template = _wrap(
        service.save_module_as_template, project_id, module_id, body.name, body.description, body.view_id
    )
    await manager.broadcast_templates_changed()
    return template


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


@app.post("/projects/{project_id}/modules/{module_id}/move")
async def move_module(project_id: str, module_id: str, body: MoveModuleTreeEntryBody):
    module = _wrap(service.move_module, project_id, module_id, body.folder_id, body.index)
    await manager.broadcast(project_id)
    return module.to_dict()


# --- module folders (purely organisational: ids, requirements, links unaffected) ---


@app.get("/projects/{project_id}/module-tree")
def get_module_tree(project_id: str):
    return _wrap(service.get_module_tree, project_id)


@app.post("/projects/{project_id}/module-folders")
async def create_module_folder(project_id: str, body: CreateModuleFolderBody):
    folder = _wrap(service.create_module_folder, project_id, body.name, body.parent_id)
    await manager.broadcast(project_id)
    return folder


@app.put("/projects/{project_id}/module-folders/{folder_id}/name")
async def rename_module_folder(project_id: str, folder_id: str, body: RenameModuleFolderBody):
    folder = _wrap(service.rename_module_folder, project_id, folder_id, body.name)
    await manager.broadcast(project_id)
    return folder


@app.delete("/projects/{project_id}/module-folders/{folder_id}")
async def delete_module_folder(project_id: str, folder_id: str):
    result = _wrap(service.delete_module_folder, project_id, folder_id)
    await manager.broadcast(project_id)
    return result


@app.post("/projects/{project_id}/module-folders/{folder_id}/move")
async def move_module_folder(project_id: str, folder_id: str, body: MoveModuleTreeEntryBody):
    folder = _wrap(service.move_module_folder, project_id, folder_id, body.folder_id, body.index)
    await manager.broadcast(project_id)
    return folder


@app.post("/projects/{project_id}/module-folders/{folder_id}/reorder")
async def reorder_module_folder(project_id: str, folder_id: str, body: ReorderModuleBody):
    folder = _wrap(service.reorder_module_folder, project_id, folder_id, body.direction)
    await manager.broadcast(project_id)
    return folder


async def _read_csv_body(request: Request) -> str:
    # The file is sent as the raw request body (Content-Type: text/csv), so
    # no multipart parser dependency is needed. Requiring text/csv also keeps
    # cross-origin "simple" requests (text/plain, form posts) from importing.
    if _media_type(request) != "text/csv":
        raise HTTPException(status_code=415, detail="The CSV file must be sent with Content-Type: text/csv")

    raw = await _read_limited_body(request, MAX_CSV_IMPORT_BYTES, "CSV")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="The CSV file must be UTF-8 encoded") from exc


@app.post("/projects/{project_id}/modules/{module_id}/import/csv/preview")
async def preview_csv_import(project_id: str, module_id: str, request: Request, mode: str | None = None):
    """Read-only: what the import would create, update and link (no write, no broadcast)."""
    csv_text = await _read_csv_body(request)
    return _wrap(service.preview_csv_import, project_id, module_id, csv_text, mode)


@app.post("/projects/{project_id}/modules/{module_id}/import/csv")
async def import_csv(project_id: str, module_id: str, request: Request, mode: str | None = None):
    csv_text = await _read_csv_body(request)
    result = _wrap(service.import_csv, project_id, module_id, csv_text, mode)
    await manager.broadcast(project_id)
    return result


# Test result import (JUnit XML / Robot Framework output.xml) into a
# test_execution module. Like the CSV import, the file is the raw request body
# (Content-Type application/xml or text/xml: no multipart parser dependency, and
# no cross-origin "simple" request can import); the options are query parameters.
_XML_MEDIA_TYPES = ("application/xml", "text/xml")


async def _read_result_file(request: Request) -> bytes:
    if _media_type(request) not in _XML_MEDIA_TYPES:
        raise HTTPException(
            status_code=415, detail="The result file must be sent with Content-Type application/xml or text/xml"
        )
    return await _read_limited_body(request, MAX_TEST_RESULTS_IMPORT_BYTES, "result")


def _result_options(
    config_id: str | None, match_attribute: str | None, executed_by: str | None,
    version: str | None, execution_date: str | None, file_name: str | None,
) -> dict[str, Any]:
    return {
        "config_id": config_id, "match_attribute": match_attribute, "executed_by": executed_by,
        "version": version, "execution_date": execution_date, "file_name": file_name,
    }


@app.post("/projects/{project_id}/modules/{module_id}/import/test-results/preview")
async def preview_test_results(
    project_id: str, module_id: str, request: Request,
    config_id: str | None = None, match_attribute: str | None = None, executed_by: str | None = None,
    version: str | None = None, execution_date: str | None = None, file_name: str | None = None,
):
    """Read-only: how the file's results match the test cases (no write, no broadcast)."""
    raw = await _read_result_file(request)
    options = _result_options(config_id, match_attribute, executed_by, version, execution_date, file_name)
    return _wrap(service.preview_test_results, project_id, module_id, raw, options)


@app.post("/projects/{project_id}/modules/{module_id}/import/test-results")
async def import_test_results(
    project_id: str, module_id: str, request: Request,
    config_id: str | None = None, match_attribute: str | None = None, executed_by: str | None = None,
    version: str | None = None, execution_date: str | None = None, file_name: str | None = None,
):
    raw = await _read_result_file(request)
    options = _result_options(config_id, match_attribute, executed_by, version, execution_date, file_name)
    result = _wrap(service.import_test_results, project_id, module_id, raw, options)
    await manager.broadcast(project_id)
    return result


@app.get("/projects/{project_id}/modules/{module_id}/verification-status")
def verification_statuses(project_id: str, module_id: str):
    """The computed Verification status per requirement of a requirements module (read-only)."""
    return _wrap(service.verification_statuses, project_id, module_id)


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
        service.move_requirement, project_id, module_id, requirement_id, body.parent_id, body.index
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


@app.get("/projects/{project_id}/links/options")
def link_options(
    project_id: str, from_id: str = Query(), to_id: str = Query(), from_project_id: str | None = Query(None)
):
    """Link configurations that can join two requirements (drag-and-drop linking).

    ``from_project_id``: the dragged requirement's project when it is not
    ``project_id`` (the drop target's project).
    """
    return _serialize(_wrap(service.link_options, project_id, from_id, to_id, from_project_id))


@app.get("/projects/{project_id}/incoming-links")
def incoming_links(project_id: str, module_id: str | None = Query(None)):
    """Links into this project from referencing projects (computed on read)."""
    return _wrap(service.incoming_links, project_id, module_id)


@app.delete("/projects/{project_id}/links")
async def unlink_requirements(project_id: str, body: LinkBody):
    result = _wrap(
        service.unlink_requirements, project_id, body.config_id, body.source_id, body.target_id
    )
    await manager.broadcast(project_id)
    return _serialize(result)


# --- WebSocket JSON-RPC -----------------------------------------------------

def _rpc_result_options(params: dict) -> dict[str, Any]:
    """The test result import options of a WebSocket request (same names as the REST query)."""
    return {key: params.get(key) for key in RESULT_IMPORT_OPTIONS}


RPC_METHODS = {
    "version.get": lambda p: version_info(),
    "project.create": lambda p: service.create_project(
        p["name"], p.get("description", ""), p.get("template_id"), p.get("parent_project_id")
    ),
    "project.set_parent": lambda p: service.set_project_parent(p["id"], p.get("parent_project_id")),
    "project.set_row_styles": lambda p: service.set_row_styles(p["id"], p.get("row_styles")),
    "project.set_references": lambda p: service.set_project_references(p["id"], p.get("references")),
    "project.references": lambda p: service.project_references(p["id"]),
    "project.lock": lambda p: service.lock_project(p["id"], p.get("reason")),
    "project.unlock": lambda p: service.unlock_project(p["id"]),
    "project.removal_info": lambda p: service.project_removal_info(p["id"]),
    "project.remove": lambda p: service.remove_project(p["id"], p.get("confirm_name"), p.get("data_dir")),
    "project.save_as_template": lambda p: service.save_project_as_template(
        p["id"], p["name"], p.get("description", "")
    ),
    "template.list": lambda p: service.list_templates(),
    "template.import": lambda p: service.import_template(p["text"]),
    "module_template.list": lambda p: service.list_module_templates(),
    # AI prompt library: mutations broadcast prompts_changed (PROMPT_MUTATING_METHODS).
    "prompt.list": lambda p: service.list_prompts(),
    "prompt.get": lambda p: service.get_prompt(p["prompt_id"]),
    "prompt.create": lambda p: service.create_prompt(p.get("prompt")),
    "prompt.update": lambda p: service.update_prompt(p["prompt_id"], p.get("prompt")),
    "prompt.delete": lambda p: service.delete_prompt(p["prompt_id"]),
    "prompt.import": lambda p: service.import_prompt(p["text"]),
    "prompt.export": lambda p: service.export_prompt(p["prompt_id"]),
    "prompt.render": lambda p: service.render_prompt(
        p["project_id"],
        p["prompt_id"],
        p["module_id"],
        p.get("requirement_ids"),
        p.get("selection_module_id"),
        p.get("selection_project_id"),
        p.get("include_children", False),
        p.get("free_text"),
    ),
    "project.get": lambda p: service.get_project(p["id"]),
    "project.list": lambda p: service.list_projects(),
    "project.purge": lambda p: {"purged": service.purge_project(p["id"])},
    "project.git_status": lambda p: service.git_status(p["id"]),
    "project.git_enable": lambda p: service.enable_history(p["id"]),
    "project.git_log": lambda p: service.git_log(p["id"], p.get("limit")),
    "project.git_set_remote": lambda p: service.set_git_remote(p["id"], p.get("url")),
    "project.git_set_auto_push": lambda p: service.set_git_auto_push(p["id"], p["enabled"]),
    "project.git_push": lambda p: service.git_push(p["id"]),
    "project.git_pull": lambda p: service.git_pull(p["id"]),
    "project.create_from_remote": lambda p: service.add_project_from_remote(p["url"]),
    # GitLab / GitHub. Connection mutations broadcast connections_changed
    # (CONNECTION_MUTATING_METHODS); tokens are accepted, never returned.
    "provider.connections": lambda p: service.list_provider_connections(),
    "provider.add_connection": lambda p: service.add_provider_connection(
        p.get("provider"), p.get("url"), p.get("name"), p.get("token")
    ),
    "provider.update_connection": lambda p: service.update_provider_connection(
        p["id"], p.get("name"), p.get("token")
    ),
    "provider.remove_connection": lambda p: service.remove_provider_connection(p["id"]),
    "provider.namespaces": lambda p: service.provider_namespaces(p["id"]),
    "provider.repositories": lambda p: service.provider_repositories(p["id"], p.get("search"), p.get("page")),
    "project.create_from_provider": lambda p: service.add_project_from_provider(
        p["connection_id"], p.get("repository")
    ),
    "project.git_publish": lambda p: service.publish_project(
        p["id"], p["connection_id"], p.get("namespace_id"), p.get("name"), p.get("private", True),
        p.get("description"),
    ),
    "project.git_provider": lambda p: service.provider_info_for_project(p["id"]),
    "project.git_review_status": lambda p: service.review_status(p["id"]),
    "project.git_review_start": lambda p: service.start_review(p["id"], p.get("branch"), p.get("title")),
    "project.git_review_forget": lambda p: service.forget_review(p["id"]),
    "baseline.create": lambda p: service.create_baseline(
        p["project_id"], p["name"], p.get("description", ""), bool(p.get("lock", False)), p.get("lock_reason")
    ),
    "baseline.list": lambda p: service.list_baselines(p["project_id"]),
    "baseline.get": lambda p: service.get_baseline(p["project_id"], p["name"]),
    "baseline.requirements": lambda p: service.baseline_requirements(
        p["project_id"], p["name"], p["module_id"]
    ),
    "baseline.compare": lambda p: service.compare_baselines(p["project_id"], p["from"], p["to"]),
    # Commit browser: read-only, never broadcast (not in MUTATING_METHODS).
    "commit.list": lambda p: service.git_commits(
        p["project_id"], p.get("module_id"), p.get("requirement_id"), p.get("offset", 0), p.get("limit"),
        p.get("author"), p.get("text"), p.get("since"), p.get("until"),
    ),
    "commit.get": lambda p: service.git_commit(p["project_id"], p["commit"]),
    "commit.snapshot": lambda p: service.get_commit_snapshot(p["project_id"], p["commit"]),
    "commit.requirements": lambda p: service.commit_requirements(p["project_id"], p["commit"], p["module_id"]),
    "attribute.add": lambda p: service.add_attribute_definition(
        p["project_id"], p["id"], p["name"], p.get("type", "string"),
        p.get("required", False), p.get("default"), p.get("choices"), p.get("modules"),
        p.get("module_types"),
    ),
    "attribute.set_modules": lambda p: service.set_attribute_modules(
        p["project_id"], p["id"], p.get("modules"), p.get("drop_values", False), p.get("module_types")
    ),
    "module.create": lambda p: service.create_module(
        p["project_id"], p["name"], p["prefix"], p.get("description", ""), p.get("type"), p.get("folder_id")
    ),
    "module.create_from_template": lambda p: service.create_module_from_template(
        p["project_id"], p["template_id"], p.get("name"), p.get("prefix"), p.get("description"), p.get("folder_id")
    ),
    "module.save_as_template": lambda p: service.save_module_as_template(
        p["project_id"], p["id"], p["name"], p.get("description", ""), p.get("view_id")
    ),
    "module.get": lambda p: service.get_module(p["project_id"], p["id"]),
    "module.list": lambda p: service.list_modules(p["project_id"]),
    "module.purge": lambda p: {"purged": service.purge_module(p["project_id"], p["id"])},
    "module.reorder": lambda p: service.reorder_module(p["project_id"], p["id"], p["direction"]),
    "module.move": lambda p: service.move_module(p["project_id"], p["id"], p.get("folder_id"), p.get("index")),
    "module_tree.get": lambda p: service.get_module_tree(p["project_id"]),
    "module_folder.create": lambda p: service.create_module_folder(
        p["project_id"], p.get("name"), p.get("parent_id")
    ),
    "module_folder.rename": lambda p: service.rename_module_folder(p["project_id"], p["id"], p.get("name")),
    "module_folder.delete": lambda p: service.delete_module_folder(p["project_id"], p["id"]),
    "module_folder.move": lambda p: service.move_module_folder(
        p["project_id"], p["id"], p.get("folder_id"), p.get("index")
    ),
    "module_folder.reorder": lambda p: service.reorder_module_folder(p["project_id"], p["id"], p["direction"]),
    "link_configuration.create": lambda p: service.create_link_configuration(
        p["project_id"], p["name"], p["source_module_id"], p["target_module_id"], p.get("target_project_id")
    ),
    "link_configuration.list": lambda p: service.list_link_configurations(p["project_id"]),
    "link_configuration.remove": lambda p: (
        service.remove_link_configuration(p["project_id"], p["id"]),
        {"id": p["id"], "removed": True},
    )[1],
    "view.create": lambda p: service.create_view(
        p["project_id"], p["name"], p.get("attribute_ids", []), p.get("column_widths")
    ),
    "view.set_column_widths": lambda p: service.set_view_column_widths(
        p["project_id"], p["id"], p["column_widths"]
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
        p["project_id"], p["module_id"], p["id"], p.get("parent_id"), p.get("index")
    ),
    "requirement.reorder": lambda p: service.reorder_requirement(
        p["project_id"], p["module_id"], p["id"], p["direction"]
    ),
    "requirement.copy": lambda p: service.copy_requirement(
        p["project_id"], p["module_id"], p["id"], p.get("target_parent_id")
    ),
    "requirement.import_csv": lambda p: service.import_csv(
        p["project_id"], p["module_id"], p["csv"], p.get("mode")
    ),
    # Read-only, never broadcast (not in MUTATING_METHODS).
    "requirement.preview_csv_import": lambda p: service.preview_csv_import(
        p["project_id"], p["module_id"], p["csv"], p.get("mode")
    ),
    "requirement.link": lambda p: service.link_requirements(
        p["project_id"], p["config_id"], p["source_id"], p["target_id"]
    ),
    "requirement.unlink": lambda p: service.unlink_requirements(
        p["project_id"], p["config_id"], p["source_id"], p["target_id"]
    ),
    "requirement.link_options": lambda p: service.link_options(
        p["project_id"], p["from_id"], p["to_id"], p.get("from_project_id")
    ),
    "link.incoming": lambda p: service.incoming_links(p["project_id"], p.get("module_id")),
    "requirement.preview_test_results": lambda p: service.preview_test_results(
        p["project_id"], p["module_id"], p["xml"], _rpc_result_options(p)
    ),
    "requirement.import_test_results": lambda p: service.import_test_results(
        p["project_id"], p["module_id"], p["xml"], _rpc_result_options(p)
    ),
    # Reports: read-only, never broadcast (not in MUTATING_METHODS).
    "report.verification_status": lambda p: service.verification_statuses(p["project_id"], p["module_id"]),
    "report.traceability": lambda p: service.traceability_matrix(p["project_id"], p.get("config_ids")),
    "report.verification": lambda p: service.verification_report(p["project_id"], p["module_id"]),
    "report.execution": lambda p: service.execution_report(p["project_id"], p["module_id"]),
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


# WebSocket error code of a change refused because the project is locked (REST 423).
PROJECT_LOCKED_RPC_CODE = -32004

MUTATING_METHODS = {
    "project.create", "project.set_parent", "project.purge", "project.git_enable", "baseline.create",
    "project.set_row_styles", "project.set_references", "project.lock", "project.unlock",
    "project.git_set_remote", "project.git_set_auto_push", "project.git_push", "project.git_pull",
    "project.create_from_remote", "project.create_from_provider", "project.git_publish",
    "project.git_review_start", "project.git_review_forget",
    "attribute.add", "attribute.set_modules",
    "module.create", "module.create_from_template", "module.purge", "module.reorder", "module.move",
    "module_folder.create", "module_folder.rename", "module_folder.delete", "module_folder.move",
    "module_folder.reorder",
    "link_configuration.create", "link_configuration.remove",
    "view.create", "view.set_column_widths", "view.remove",
    "requirement.add", "requirement.update", "requirement.remove", "requirement.move",
    "requirement.reorder", "requirement.copy", "requirement.import_csv", "requirement.import_test_results",
    "requirement.link", "requirement.unlink",
}

# Methods that change the data directory's template set rather than a
# project: they broadcast {"event": "templates_changed"} instead of "changed".
TEMPLATE_MUTATING_METHODS = {"project.save_as_template", "template.import", "module.save_as_template"}

# Methods that change the user prompt library: they broadcast {"event": "prompts_changed"}.
PROMPT_MUTATING_METHODS = {"prompt.create", "prompt.update", "prompt.delete", "prompt.import"}

# Methods that change the app-level GitLab/GitHub connections: they broadcast {"event": "connections_changed"}.
CONNECTION_MUTATING_METHODS = {
    "provider.add_connection", "provider.update_connection", "provider.remove_connection",
}


def _project_id_of(method_name: str, params: dict, result: Any) -> str | None:
    if method_name in ("project.create", "project.create_from_remote", "project.create_from_provider"):
        return getattr(result, "id", None)  # None after a failure: nothing to broadcast
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
        if method_name == "project.remove":
            await manager.broadcast_removed(params["id"], exclude=sender)
        elif method_name in MUTATING_METHODS:
            project_id = _project_id_of(method_name, params, result)
            if project_id:
                await manager.broadcast(project_id, exclude=sender)
        elif method_name in TEMPLATE_MUTATING_METHODS:
            await manager.broadcast_templates_changed(exclude=sender)
        elif method_name in PROMPT_MUTATING_METHODS:
            await manager.broadcast_prompts_changed(exclude=sender)
        elif method_name in CONNECTION_MUTATING_METHODS:
            await manager.broadcast_connections_changed(exclude=sender)
        return {"jsonrpc": "2.0", "id": request_id, "result": _serialize(result)}
    except FileNotFoundError as exc:
        return _error_response(request_id, -32001, str(exc))
    except ProjectLockedError as exc:
        return _error_response(request_id, PROJECT_LOCKED_RPC_CODE, str(exc))
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


# Registered last so it never shadows an API route: top-level files of the
# bundled frontend (favicon.svg, icons.svg, ...). Only single-segment paths
# no API route claimed reach it; anything else is a 404 as before.
if FRONTEND_DIR is not None:

    @app.get("/{name}", include_in_schema=False)
    def frontend_file(name: str, request: Request):
        assert FRONTEND_DIR is not None
        candidate = (FRONTEND_DIR / name).resolve()
        if candidate.parent == FRONTEND_DIR.resolve() and candidate.is_file():
            if candidate.name == "index.html":
                return _index_response()
            return FileResponse(candidate)
        if "text/html" in request.headers.get("accept", ""):
            return _index_response()
        raise HTTPException(status_code=404, detail="Not Found")


if __name__ == "__main__":
    # `python -m reqman.app [--dir PATH] [--host H] [--port N]` (etc/start-backend.sh)
    # is `reqman serve`; see cli.py.
    import sys

    from .cli import main

    sys.exit(main(["serve", *sys.argv[1:]]))
