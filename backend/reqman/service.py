"""Business logic for ReqManTool, shared by both the REST API and the
WebSocket JSON-RPC endpoint (see ``app.py``) so the two transports can
never behave differently for the same operation.

``ProjectService`` is where every cross-cutting rule lives: id generation
and allocation, attribute validation (including rejecting direct writes to
system-managed attributes), requirement nesting/cycle checks, directional
link-configuration enforcement, and the cleanup cascades that keep links
and mirrored attributes consistent when a requirement is removed, moved,
or purged. It also owns project templates (create a project from one, save
a project as one) and the subproject tree (``parent_project_id``). See
docs/ARCHITECTURE.md for the reasoning behind each of these.

It also decides when project history is recorded (``git_repo.py``): every
public mutating method is wrapped in ``_records_history``, which notes the
projects whose files the operation wrote (``ProjectStore.recording_writes``)
and, after the operation succeeded, auto-commits them with a message
generated from the changed files (``history.describe_changes``). A new
project gets its repository right after it is created.

Permissions (server edition): the active ``PermissionGuard``
(``extensions.HOOKS``) is asked at the service's choke points, so the REST
and WebSocket APIs share them: every public method whose first parameter
is ``project_id`` checks ``project.read``, ``project.write`` (the
``_records_history`` mutations and the git write operations) or
``project.admin`` (``_requires``) on entry (``_install_permission_checks``);
project creation, listing (filtered to the readable projects), cross-
project reads and cleanup cascades, libraries and connections check
explicitly. In the local edition the guard is ``AllowAllGuard`` and none of
this costs more than a type check. Project admins only: baselines, lock /
unlock, attribute definitions, views and link configurations.

Requirement status guards (server edition, only while a guard is active):
``update_requirement`` needs just ``project.review``, so a reviewer may
sign off; a change of any attribute other than the status still needs
``project.write``. Status changes (update, add, CSV import) go through
``_check_status_changes``: Reviewed only by someone other than the last
content author (git history), Approved only from Reviewed and by
``project.approve`` (see "requirement status guards" below).

Module edit locks (server edition): the active ``ModuleLockService`` is
asked before a write to a module's content (``_check_module_writes``):
``_records_history`` does it for every mutation whose second parameter is
``module_id`` (unless marked ``_module_structure_only``: moving or
reordering a module in the tree changes project.json only), and the
methods that write other modules do it explicitly — link creation and
removal (the configuration's source module, which holds the link, like an
exclusive edit of a DOORS link module), removing a link configuration,
narrowing an attribute's scope (the modules whose values are dropped) and
purging the project (every module). The mirror attribute a link refreshes
in the target requirement is derived data and is not checked. Local
edition: ``NoLocks``, a type test.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .csv_import import ID_COLUMN, LINK_COLUMN_PREFIX, PARENT_COLUMN, RESERVED_COLUMNS
from .extensions import (
    ACTION_CONNECTIONS_MANAGE,
    ACTION_CONNECTIONS_USE,
    ACTION_GIT_REMOTE_URL,
    ACTION_LIBRARY_WRITE,
    ACTION_PROJECT_ADMIN,
    ACTION_PROJECT_APPROVE,
    ACTION_PROJECT_CREATE,
    ACTION_PROJECT_READ,
    ACTION_PROJECT_REVIEW,
    ACTION_PROJECT_WRITE,
    AUDIT_PROJECT_CREATE,
    AUDIT_PROJECT_LOCK,
    AUDIT_PROJECT_PURGE,
    AUDIT_PROJECT_REMOVE,
    AUDIT_PROJECT_UNLOCK,
    HOOKS,
    AccessDeniedError,
    audit,
    check_module_write,
    check_permission,
    current_identity,
    guard_active,
    is_permitted,
    module_locks_active,
)
from .formats import PROJECT_FORMAT, NewerFormatError, upgrade
from .git_repo import (
    DEFAULT_LOG_LIMIT,
    LOG_LIMIT,
    GitError,
    GitHistory,
    RepoConflict,
    local_remote_path,
    validate_baseline_name,
)
from .history import FileChange, build_snapshot, compare_snapshots, describe_changes
from .providers import PROVIDER_LABELS, ProviderService
from . import module_tree, richtext
from .models import (
    DEFAULT_MODULE_TYPE,
    MODULE_TYPES,
    AttributeDefinition,
    LinkConfiguration,
    LinkPair,
    Module,
    ModuleFolder,
    Project,
    ProjectLock,
    Requirement,
    RowStyle,
    View,
)
from .ordering import tree_order
from .prompts import (
    BUILTIN_PROMPTS_DIR,
    MAX_PROMPT_FREE_TEXT_LENGTH,
    MAX_PROMPT_IMPORT_BYTES,
    PROMPT_KEYS,
    Prompt,
    fill_placeholders,
    list_builtin_prompt_ids,
    make_prompt_id,
    parse_prompt,
    parse_prompt_text,
    read_builtin_prompt_text,
    split_prompt_id,
)
from .prompts import BUILTIN_SOURCE as PROMPT_BUILTIN_SOURCE
from .prompts import USER_SOURCE as PROMPT_USER_SOURCE
from .store import PROJECT_SLUG_PATTERN, ProjectStore, validate_prefix
from .templates import (
    BUILTIN_SOURCE,
    BUILTIN_TEMPLATES_DIR,
    LINK_REF_PREFIX,
    MAX_SKELETON_NODES,
    MAX_TEMPLATE_IMPORT_BYTES,
    USER_SOURCE,
    ModuleTemplate,
    ModuleTemplateAttribute,
    ModuleTemplateModule,
    SkeletonNode,
    Template,
    TemplateAttribute,
    TemplateFolder,
    TemplateLinkConfiguration,
    TemplateModule,
    TemplateRowStyle,
    TemplateView,
    list_builtin_module_template_ids,
    list_builtin_template_ids,
    make_template_id,
    parse_module_template_text,
    parse_template,
    parse_template_text,
    read_builtin_module_template_text,
    read_builtin_template_text,
    skeleton_size,
    split_template_id,
)

logger = logging.getLogger("reqman.service")

BUILTIN_ATTRIBUTES = [
    AttributeDefinition(id="object_text", name="ObjectText", type="text", required=True),
]

ATTRIBUTE_TYPES = ("string", "text", "integer", "boolean", "enum", "date")

# A ``date`` attribute value: an ISO 8601 calendar date ``YYYY-MM-DD`` (a
# string, so it sorts and compares as text and round-trips through CSV).
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

# The (source module type, target module type) pairs a link configuration may
# join: requirements trace to requirements, test cases verify requirements and
# test executions execute test cases. Checked when a configuration is created
# (API or template); configurations already stored are never rejected.
LINK_TYPE_PAIRS = (
    ("requirements", "requirements"),
    ("test_cases", "requirements"),
    ("test_execution", "test_cases"),
)

# --- reports (traceability matrix, verification and test execution reports) ---
#
# The reports find the attributes they read by *role*, not by id, so they work
# for any project and not just the RBC template: for each role, the first name
# in ``names`` (priority order) that equals an applicable, non-system
# attribute's name or id wins -- both compared lower-case with punctuation and
# spaces collapsed ("Build/SW Version Under Test" -> "build sw version under
# test"). Failing that, a role with ``contains`` takes the first attribute whose
# name has one of those words; ``only_of_type`` takes the only attribute of the
# role's types, whatever its name.
REPORT_ATTRIBUTE_ROLES: dict[str, dict[str, Any]] = {
    "result": {
        "types": ("enum", "string"),
        "names": ("result", "test result", "execution result", "verdict", "outcome"),
    },
    "execution_date": {
        "types": ("date",),
        "names": ("execution date", "executed on", "date executed", "test date", "run date", "date"),
        "only_of_type": True,
    },
    "executed_by": {
        "types": ("string", "enum"),
        "names": ("executed by", "executor", "tester", "tested by", "run by"),
    },
    "version": {
        "types": ("string", "enum"),
        "names": (
            "build sw version under test", "version under test", "sw version under test",
            "software version under test", "build under test", "build", "version",
        ),
        "contains": ("version", "build"),
    },
    "evidence": {
        "types": ("string", "text"),
        "names": ("evidence log ref", "evidence", "evidence ref", "log ref", "log"),
        "contains": ("evidence",),
    },
    "object_type": {"types": ("enum", "string"), "names": ("object type",)},
    # The requirement status the server edition's sign-off guards watch (``_check_status_changes``).
    "status": {
        "types": ("enum", "string"),
        "names": ("status", "review status", "approval status", "requirement status", "lifecycle status"),
    },
    # The test case attribute a test result import matches on by default (else the requirement id).
    "test_id": {
        "types": ("string", "integer", "enum"),
        "names": (
            "test id", "test case id", "automated test id", "automation id", "external test id",
            "external id", "test key", "test ref",
        ),
    },
}

# Test result import: the pseudo attribute id meaning "match on the test
# case's requirement id", the attribute types a test case can be matched on,
# the value written to a ``string``/``text`` result attribute per result
# category, and the request options.
RESULT_MATCH_ID = "@id"
RESULT_MATCH_TYPES = ("string", "integer", "enum")
RESULT_TEXT_VALUES = {"passed": "Passed", "failed": "Failed", "blocked": "Blocked", "not_run": "Not Run"}
RESULT_IMPORT_OPTIONS = ("config_id", "match_attribute", "executed_by", "version", "execution_date", "file_name")
MAX_RESULT_IMPORT_ERRORS = 20
# Commit browser: page size, the deepest page and the longest author/text filter.
DEFAULT_COMMIT_PAGE = 50
MAX_COMMIT_OFFSET = 1_000_000
MAX_COMMIT_FILTER_LENGTH = 200

# Result values (compared like attribute names) per result category; any other
# value, and no value, counts as not run.
REPORT_RESULT_WORDS: dict[str, tuple[str, ...]] = {
    "passed": ("passed", "pass", "ok", "success", "succeeded"),
    "failed": ("failed", "fail", "failure", "nok", "not ok", "error"),
    "blocked": ("blocked",),
    "not_applicable": ("not applicable", "n a", "na"),
    "not_run": ("not run", "not executed", "pending", "skipped", ""),
}
REPORT_RESULTS = ("passed", "failed", "blocked", "not_run", "not_applicable")

# Status values (compared like attribute names) the server edition's status
# guards treat as signed off (``reviewed``) and approved (``approved``).
STATUS_STAGE_WORDS: dict[str, tuple[str, ...]] = {
    "reviewed": ("reviewed",),
    "approved": ("approved",),
}
REPORT_EXECUTED_RESULTS = ("passed", "failed", "blocked")
VERIFICATION_STATUSES = ("verified", "failed", "not_run", "not_covered")
EXECUTION_TEST_STATUSES = ("passed", "failed", "blocked", "not_run", "not_planned")

# Object Type values (compared like attribute names) of objects that are
# neither requirements nor test cases; the reports leave them out.
REPORT_EXCLUDED_OBJECT_TYPES = frozenset({"heading", "information", "definition", "title", "comment", "note"})

# The most projects one project may reference (``Project.references``).
MAX_PROJECT_REFERENCES = 100
# The longest project lock reason (characters, after whitespace is collapsed).
MAX_LOCK_REASON_LENGTH = 500

# The most link configurations one traceability chain may have.
MAX_TRACE_CHAIN = 10

# Shared view column widths (``View.column_widths``): the key of the
# requirements table's Id column (attribute ids name the other columns) and
# the accepted width range in CSS pixels.
VIEW_ID_COLUMN = "@id"
MIN_COLUMN_WIDTH = 40
MAX_COLUMN_WIDTH = 4000
# The fields ``ProjectService.update_view`` accepts (any non-empty subset).
VIEW_UPDATE_FIELDS = ("name", "attribute_ids", "column_widths", "single_line", "modules")

# Row styling rules (``Project.row_styles``): the most a project may have,
# the keys of one rule, and the color format.
MAX_ROW_STYLES = 200
ROW_STYLE_KEYS = ("attribute_id", "value", "font_color", "background_color", "bold")
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Module folders (``Project.module_order``, project format 7): the deepest
# nesting a folder may be created or moved to (top-level folders are depth 1)
# and the longest folder name.
MAX_MODULE_FOLDER_DEPTH = 16
MAX_MODULE_FOLDER_NAME_LENGTH = 200

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Upper bound on the candidates ``_unique_slug`` tries (``<slug>``, ``<slug>-2``, ...).
_MAX_SLUG_ATTEMPTS = 10_000

# Project id used while dry-running a template build to validate it; never written.
_TEMPLATE_CHECK_PROJECT_ID = "template-check"


class ConflictError(ValueError):
    """The request clashes with existing data (REST 409; WebSocket treats it like any ValueError)."""


class ProjectLockedError(ConflictError):
    """A change of a locked project (REST 423 Locked; WebSocket error code -32004).

    Raised by the one lock guard (``ProjectService._require_unlocked``)
    before anything is written.
    """


# Marks a ``_records_history`` method that may run while its project is locked (unlocking it).
_ALLOWED_WHILE_LOCKED = "_reqman_allowed_while_locked"


def _allowed_while_locked(method: Callable) -> Callable:
    """Exempt a mutation from the project lock guard (apply below ``_records_history``)."""
    setattr(method, _ALLOWED_WHILE_LOCKED, True)
    return method


# Marks a ``_records_history`` method taking a ``module_id`` that changes only
# the module's place in the tree (project.json), not its content: module edit
# locks do not apply to it.
_MODULE_STRUCTURE_ONLY = "_reqman_module_structure_only"


def _module_structure_only(method: Callable) -> Callable:
    """Exempt a ``module_id`` mutation from the module edit lock check (apply below ``_records_history``)."""
    setattr(method, _MODULE_STRUCTURE_ONLY, True)
    return method


# The permission a public ``project_id`` method needs, when not the default
# (``project.write`` for ``_records_history`` mutations, else ``project.read``).
_REQUIRED_ACTION = "_reqman_required_action"


def _requires(action: str) -> Callable[[Callable], Callable]:
    """Set the permission a public ``project_id`` method needs (apply below ``_records_history``)."""

    def mark(method: Callable) -> Callable:
        setattr(method, _REQUIRED_ACTION, action)
        return method

    return mark


def _records_history(method: Callable) -> Callable:
    """Auto-commit the projects a successful mutation wrote (see ``ProjectService._auto_commit``).

    Applied to every public method that writes project files. Nested calls
    commit once, after the outermost one. A failed operation commits
    nothing; whatever it already wrote is included in the next commit.

    A method whose first parameter is ``project_id`` (every mutation of an
    existing project) is also refused with ``ConflictError`` (409), before
    anything is written, while that project has a git sync conflict — its
    ``current`` branch and the configured remote's have diverged (see
    ``GitHistory.sync_conflict_message``); the user resolves it in git.

    A method whose second parameter is ``module_id`` is also checked
    against the module edit locks (server edition, ``_check_module_writes``)
    unless marked ``_module_structure_only``.

    Such a method is also the project lock guard: while the project is
    locked (``Project.locked``) it is refused with ``ProjectLockedError``
    before anything is written, unless marked ``_allowed_while_locked``
    (unlocking). Every change of a project, its modules, folders,
    requirements, links, views, attributes, references and row styles,
    and every import into it, goes through here.
    """
    parameters = list(inspect.signature(method).parameters)
    guards_project = len(parameters) > 1 and parameters[1] == "project_id"
    guards_lock = guards_project and not getattr(method, _ALLOWED_WHILE_LOCKED, False)
    guards_module = (
        guards_project
        and len(parameters) > 2
        and parameters[2] == "module_id"
        and not getattr(method, _MODULE_STRUCTURE_ONLY, False)
    )

    @functools.wraps(method)
    def wrapper(self: "ProjectService", *args: Any, **kwargs: Any) -> Any:
        if self.store.is_recording():
            return method(self, *args, **kwargs)
        self._last_touched.ids = set()
        if guards_project:
            project_id = args[0] if args else kwargs.get("project_id")
            if isinstance(project_id, str):
                if guards_lock:
                    self._require_unlocked(project_id)
                self._require_no_sync_conflict(project_id)
                if guards_module and module_locks_active():
                    module_id = args[1] if len(args) > 1 else kwargs.get("module_id")
                    self._check_module_writes(project_id, [module_id])
        with self.store.recording_writes() as touched:
            result = method(self, *args, **kwargs)
        for project_id in sorted(touched):
            self._auto_commit(project_id)
        self._last_touched.ids = set(touched)
        return result

    setattr(wrapper, _REQUIRED_ACTION, getattr(method, _REQUIRED_ACTION, ACTION_PROJECT_WRITE))
    return wrapper


def _install_permission_checks(cls: type) -> type:
    """Wrap every public method of ``cls`` whose first parameter is ``project_id`` in a permission check.

    The single choke point of the permission guard for project operations:
    the method's ``_REQUIRED_ACTION`` (``project.write`` for
    ``_records_history`` mutations, ``_requires`` for others), else
    ``project.read``, is checked for the current identity before the method
    runs (and so before the lock guard). Nested calls check again (cheap).
    In the local edition (``AllowAllGuard``) the check is a type test.
    """
    for name, member in list(vars(cls).items()):
        if name.startswith("_") or not inspect.isfunction(member):
            continue
        parameters = list(inspect.signature(member).parameters)
        if len(parameters) < 2 or parameters[1] != "project_id":
            continue
        action = getattr(member, _REQUIRED_ACTION, ACTION_PROJECT_READ)

        def make(method: Callable, action: str) -> Callable:
            @functools.wraps(method)
            def checked(self: "ProjectService", *args: Any, **kwargs: Any) -> Any:
                if guard_active():
                    project_id = args[0] if args else kwargs.get("project_id")
                    check_permission(action, project_id if isinstance(project_id, str) else None)
                return method(self, *args, **kwargs)

            return checked

        setattr(cls, name, make(member, action))
    return cls


CSV_IMPORT_MODES = ("create", "update")


@dataclass
class _CsvImportPlan:
    """What a validated CSV import writes (``ProjectService._csv_import_plan``); never persisted."""

    project: Project
    module: Module
    mode: str
    ignored_columns: list[str]
    # (file row number, new requirement with its provisional id)
    created: list[tuple[int, Requirement]] = field(default_factory=list)
    # (file row number, requirement with the new values, {attribute id: (old, new)})
    updates: list[tuple[int, Requirement, dict[str, tuple[Any, Any]]]] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    # (file row number, configuration, source id, plain target id, already linked)
    links: list[tuple[int, LinkConfiguration, str, str, bool]] = field(default_factory=list)


@dataclass
class ProjectStub:
    """A project listed by ``list_projects`` whose project.json cannot be loaded.

    For example one written by a newer ReqManTool, or not valid JSON. It is
    listed read-only, with the reason in ``error``, so one such project never
    breaks the whole list; opening it (``get_project`` and every other
    operation) fails with the same error and its files are never written.
    Its stored parent cannot be trusted, so it is always listed top level.
    Never persisted.
    """

    id: str
    name: str
    error: str
    parent_project_id: None = None

    def to_dict(self) -> dict[str, Any]:
        # ``parent_project_id`` is omitted like a top-level Project's.
        return {"id": self.id, "name": self.name, "error": self.error}


def _display_name(text: str | None, fallback: str) -> str:
    """The ``name`` string of a JSON object in ``text``, stripped, if usable; else ``fallback``.

    Best effort, for listing a file that cannot otherwise be loaded.
    """
    try:
        name = json.loads(text)["name"] if text is not None else None
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        name = None
    if isinstance(name, str):
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:  # e.g. a lone surrogate escape: unlistable
            name = None
    return name.strip() if isinstance(name, str) and name.strip() else fallback


def _unique_slug(
    name: str,
    exists_fn: Callable[[str], bool],
    fallback: str = "item",
    max_length: int | None = None,
) -> str:
    """A slug of ``name`` not taken per ``exists_fn``: the slug itself, else ``<slug>-2``, ``-3``, ...

    With ``max_length`` (for an id used as a file or folder name), a
    candidate longer than that raises ``ValueError``; ids are never
    truncated. At most ``_MAX_SLUG_ATTEMPTS`` candidates are tried, so
    this always ends.
    """
    base = _SLUG_NON_ALNUM.sub("-", name.strip().lower()).strip("-") or fallback
    candidate = base
    for suffix in range(2, _MAX_SLUG_ATTEMPTS + 2):
        if max_length is not None and len(candidate.encode("utf-8")) > max_length:
            raise ValueError(
                f"{fallback.capitalize()} name is too long: its id would be {len(candidate)} "
                f"characters, but the file system allows at most {max_length}"
            )
        if not exists_fn(candidate):
            return candidate
        candidate = f"{base}-{suffix}"
    raise ValueError(f"Cannot find a free id for {fallback} '{base}': too many are taken")


@_install_permission_checks
class ProjectService:
    """Business logic shared by the REST API and the WebSocket RPC endpoint."""

    def __init__(
        self,
        store: ProjectStore,
        builtin_templates_dir: Path | None = None,
        history: GitHistory | None = None,
        providers: ProviderService | None = None,
    ):
        self.store = store
        self.builtin_templates_dir = (
            builtin_templates_dir if builtin_templates_dir is not None else BUILTIN_TEMPLATES_DIR
        )
        self.builtin_prompts_dir = BUILTIN_PROMPTS_DIR
        # Without one, git history is off (plain files): the default for direct use and tests.
        self.history = history if history is not None else GitHistory()
        # GitLab/GitHub connections (app-level; tokens in the OS keychain).
        # Without one: no connections, tokens in memory (direct use and tests).
        self.providers = providers if providers is not None else ProviderService()
        # Remote git operations on a connection's https URL authenticate with its token.
        self.history.remote_auth = self.providers.git_auth_env
        # The projects the last successful mutation of this thread wrote
        # (``_records_history``), so the API can broadcast every one of them:
        # removing a requirement also cleans up links in referencing projects.
        self._last_touched = threading.local()

    def take_touched_projects(self) -> set[str]:
        """The ids of the projects the last successful mutation (this thread) wrote; then forgotten."""
        ids = getattr(self._last_touched, "ids", None) or set()
        self._last_touched.ids = set()
        return set(ids)

    # --- history (git) -----------------------------------------------------------

    def _describer(self, project_id: str) -> Callable[[list[FileChange]], list[str]]:
        """Commit message entries for a project's changed files, named after its current attributes."""

        def describe(changes: list[FileChange]) -> list[str]:
            try:
                project: Any = self.store.load_project(project_id).to_dict()
            except (OSError, ValueError):
                project = None
            return describe_changes(changes, project)

        return describe

    def _auto_commit(self, project_id: str) -> None:
        """Commit a project's changed data files; a git problem is recorded, never raised."""
        if not self.history.enabled:
            return
        try:
            project_dir = self.store.project_path(project_id)
        except FileNotFoundError:
            return
        self.history.auto_commit(project_dir, self._describer(project_id))

    def git_status(self, project_id: str) -> dict[str, Any]:
        """The project's history (git) status: state, explanation, branch, HEAD, pending changes.

        Read-only. A missing project is 404 and a stub (unloadable project)
        fails with its load error, like every other project operation.
        """
        self.store.load_project(project_id)
        return self.history.status(self.store.project_path(project_id))

    @_requires(ACTION_PROJECT_ADMIN)
    def enable_history(self, project_id: str) -> dict[str, Any]:
        """Start recording git history for an existing project; returns the new status.

        See ``GitHistory.enable``. A project inside another repository's work
        tree, whose ``.git`` points to another repository (``foreign``), with
        a merge/cherry-pick/revert/rebase in progress or unresolved
        conflicts, a detached HEAD, or on another branch while a ``current`` branch
        exists is refused with ``ConflictError`` (409); git turned off or
        missing is a ``ValueError`` (400), and so is a failed step
        (``EnableFailed``, whose ``project_changed`` tells the API to
        broadcast anyway). Enabling an already enabled project only commits
        pending changes.
        """
        project = self.store.load_project(project_id)
        project_dir = self.store.project_path(project_id)
        try:
            self.history.enable(project_dir, project.name, self._describer(project_id))
        except RepoConflict as exc:
            raise ConflictError(str(exc)) from exc
        return self.history.status(project_dir)

    def requirement_history(self, project_id: str, module_id: str, requirement_id: str) -> dict[str, Any]:
        """A requirement's change list with per-attribute diffs, and the attribute blame.

        Read-only. The requirement must exist (404 otherwise). A project
        without a readable repository answers ``available: false`` with the
        reason instead of failing.
        """
        self.store.load_requirement(project_id, module_id, requirement_id)
        return self.history.requirement_history(
            self.store.project_path(project_id),
            self.store.requirement_relative_path(module_id, requirement_id),
            requirement_id,
        )

    def git_log(self, project_id: str, limit: int | None = None) -> dict[str, Any]:
        """The newest commits of the project's ``current`` branch (read-only, for the Git panel).

        ``limit`` defaults to ``DEFAULT_LOG_LIMIT`` (20) and is capped at
        ``LOG_LIMIT`` (200). A project without a readable repository answers
        ``available: false`` with the reason instead of failing.
        """
        self.store.load_project(project_id)
        if limit is None:
            limit = DEFAULT_LOG_LIMIT
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError(f"Invalid limit {limit!r}: expected an integer >= 1")
        return self.history.log(self.store.project_path(project_id), min(limit, LOG_LIMIT))

    # --- commit browser (git) -------------------------------------------------------

    @staticmethod
    def _commit_filter_text(value: Any, what: str) -> str | None:
        """A commit browser author/text filter: ``None`` when empty; 400 when too long or not one line."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Invalid {what} filter: expected a string")
        value = value.strip()
        if not value:
            return None
        if len(value) > MAX_COMMIT_FILTER_LENGTH:
            raise ValueError(f"Invalid {what} filter: at most {MAX_COMMIT_FILTER_LENGTH} characters")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError(f"Invalid {what} filter: control characters are not allowed")
        return value

    @staticmethod
    def _commit_filter_date(value: Any, what: str, end_of_day: bool) -> str | None:
        """A commit browser date filter (``YYYY-MM-DD``) as a git ``--since``/``--until`` value."""
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"Invalid {what} date {value!r}: expected YYYY-MM-DD")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid {what} date {value!r}: {exc}") from exc
        # git's date parser only understands years 1970-2099: clamp (an open
        # bound beyond them limits nothing).
        year = int(value[:4])
        if (end_of_day and year > 2099) or (not end_of_day and year < 1970):
            return None
        if year > 2099:
            value = "2099-12-31"
        elif year < 1970:
            value = "1970-01-01"
        return f"{value} 23:59:59" if end_of_day else f"{value} 00:00:00"

    def git_commits(
        self,
        project_id: str,
        module_id: str | None = None,
        requirement_id: str | None = None,
        offset: int | None = 0,
        limit: int | None = None,
        author: str | None = None,
        text: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        """One page of the project's commits for the commit browser (read-only).

        Scope: the whole project, one module (commits that changed its
        folder) or one requirement (``requirement_id`` needs ``module_id``;
        commits that changed its file). The module/requirement must exist
        now (404). Filters: ``author`` and ``text`` (message) are
        case-insensitive fixed strings; ``since``/``until`` are inclusive
        ``YYYY-MM-DD`` dates (committer date, server time zone). Paging with
        ``offset`` and ``limit`` (default ``DEFAULT_COMMIT_PAGE``, capped at
        ``LOG_LIMIT``); ``has_more`` tells whether another page exists. A
        project without a readable repository answers ``available: false``.
        """
        self.store.load_project(project_id)
        if limit is None:
            limit = DEFAULT_COMMIT_PAGE
        for name, value, minimum in (("limit", limit, 1), ("offset", offset or 0, 0)):
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise ValueError(f"Invalid {name} {value!r}: expected an integer >= {minimum}")
        offset = offset or 0
        if offset > MAX_COMMIT_OFFSET:
            raise ValueError(f"Invalid offset {offset}: at most {MAX_COMMIT_OFFSET}")
        path: str | None = None
        if requirement_id is not None and module_id is None:
            raise ValueError("A requirement scope needs its module_id")
        if module_id is not None:
            self.store.load_module(project_id, module_id)  # 404 for an unknown module
            path = f"modules/{module_id}/"
            if requirement_id is not None:
                self.store.load_requirement(project_id, module_id, requirement_id)
                path = self.store.requirement_relative_path(module_id, requirement_id)
        return self.history.list_commits(
            self.store.project_path(project_id),
            offset=offset,
            limit=min(limit, LOG_LIMIT),
            path=path,
            author=self._commit_filter_text(author, "author"),
            text=self._commit_filter_text(text, "text"),
            since=self._commit_filter_date(since, "from", end_of_day=False),
            until=self._commit_filter_date(until, "to", end_of_day=True),
        )

    def _snapshot_project_dict(self, project_id: str, data: Any, what: str) -> Any:
        """A snapshot's project.json upgraded to the current format for comparing (best effort).

        A newer format is refused (``NewerFormatError``, 409); a project.json
        the migrations cannot read is compared as it is.
        """
        if not isinstance(data, dict):
            return data
        try:
            return upgrade(data, PROJECT_FORMAT, what)
        except NewerFormatError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            return data

    def git_commit(self, project_id: str, commit: str) -> dict[str, Any]:
        """A commit's details for the commit browser (read-only).

        The full message, parents, baseline tags and what the commit changed
        compared with its first parent (nothing, for the first commit), at
        requirement level: the baseline compare engine (``compare_snapshots``:
        modules, requirements with per-attribute diffs, links, attribute
        definitions) plus ``notes`` describing project and module file
        changes (module renames, module tree reorders and moves, views, …).
        Older commits' project.json is upgraded through the format
        migrations first. ``commit`` must be a full commit id (404 otherwise).
        """
        self.store.load_project(project_id)
        change = self.history.commit_change(self.store.project_path(project_id), commit)
        what = f"Commit {commit[:12]} of project '{project_id}'"
        old_snapshot = build_snapshot(change["old"])
        new_snapshot = build_snapshot(change["new"])
        old_snapshot["project"] = self._snapshot_project_dict(project_id, old_snapshot["project"], what)
        new_snapshot["project"] = self._snapshot_project_dict(project_id, new_snapshot["project"], what)
        result = compare_snapshots(old_snapshot, new_snapshot)
        self._add_compare_row_styles(result, old_snapshot, new_snapshot)
        structural = [
            FileChange(path, change["old"].get(path), change["new"].get(path))
            for path in change["changed_paths"]
            if path == "project.json" or re.fullmatch(r"modules/[^/]+/module\.json", path)
        ]
        notes = describe_changes(structural, new_snapshot["project"]) if structural else []
        obj = change["commit"]
        return {
            **obj,
            "baselines": change["baselines"],
            "changed_files": len(change["changed_paths"]),
            "notes": notes,
            **result,
        }

    def get_commit_snapshot(self, project_id: str, commit: str) -> dict[str, Any]:
        """The project and modules as they were at a commit ("browse at this commit"). Read-only.

        Same as a baseline (``get_baseline``): read from the commit in memory,
        upgraded like ``load_project``; 404 for an unknown commit, 409 for a
        newer format.
        """
        self.store.load_project(project_id)
        obj, files = self.history.commit_files(self.store.project_path(project_id), commit)
        project = self._snapshot_project(f"Commit {commit[:12]} of project '{project_id}'", files)
        return {
            "commit": commit,
            "subject": obj["subject"],
            "date": obj["date"],
            "author_name": obj["author_name"],
            "project": project.to_dict(),
            "modules": [m.to_dict() for m in self._baseline_modules(project, files)],
        }

    def commit_requirements(self, project_id: str, commit: str, module_id: str) -> list[Requirement]:
        """A module's requirements as they were at a commit (read-only), sorted by id."""
        self.store.load_project(project_id)
        _obj, files = self.history.commit_files(self.store.project_path(project_id), commit)
        self._snapshot_project(f"Commit {commit[:12]} of project '{project_id}'", files)  # rejects a newer format
        return self._snapshot_requirements(files, module_id, f"commit {commit[:12]}")

    # --- remotes (git) -----------------------------------------------------------

    @staticmethod
    def _check_module_writes(project_id: str, module_ids: Any) -> None:
        """Refuse (``ModuleLockedError``) a write to modules another user has checked out (server edition).

        Called before anything is written. Free in the local edition (no
        lock service). Non-string ids (a request naming no module) are
        left to the operation's own validation.
        """
        if not module_locks_active():
            return
        for module_id in dict.fromkeys(module_ids):
            if isinstance(module_id, str):
                check_module_write(project_id, module_id)

    def _require_no_sync_conflict(self, project_id: str) -> None:
        """Refuse a mutation (409) while the project's git sync conflict is unresolved."""
        if not self.history.enabled:
            return
        try:
            project_dir = self.store.project_path(project_id)
        except FileNotFoundError:
            return  # the mutation itself reports the missing project
        message = self.history.sync_conflict_message(project_dir)
        if message is not None:
            raise ConflictError(message)

    def _git_remote_call(self, call: Callable[[], Any]) -> Any:
        """Run a remote git operation, mapping its errors to the service's.

        ``RepoConflict`` becomes ``ConflictError`` (409), carrying over
        ``project_changed`` (a failed pull that already fetched: broadcast
        anyway); ``GitError`` (network, access, git itself) becomes
        ``ValueError`` (400).
        """
        try:
            return call()
        except RepoConflict as exc:
            error = ConflictError(str(exc))
            if getattr(exc, "project_changed", False):
                error.project_changed = True  # type: ignore[attr-defined]
            raise error from exc
        except GitError as exc:
            raise ValueError(str(exc)) from exc

    def _refuse_remote_in_data_dir(self, url: str | None) -> None:
        """Refuse (400) a local-folder remote in the data directory (or the directory itself).

        Pushing there would write another project's refs outside its lock,
        and a project cloned from there would push back into it. Symlinks
        are resolved; ssh/git/https remotes are never checked.
        """
        path = local_remote_path(url or "")
        if path is None or not path.is_absolute():
            return  # a relative path is refused by the URL validator
        data_dir = os.path.realpath(self.store.root)
        target = Path(os.path.realpath(path))
        inside = target == Path(data_dir) or target.is_relative_to(data_dir)
        if not inside:
            # Case-insensitive file systems, bind mounts: compare the folders themselves.
            for folder in (target, *target.parents):
                try:
                    if os.path.samefile(folder, data_dir):
                        inside = True
                        break
                except OSError:
                    continue
        if inside:
            raise ValueError(
                f"Invalid remote URL '{(url or '').strip()}': a local-folder remote must not be "
                f"inside the data directory ({data_dir}); use a repository outside it"
            )

    @_requires(ACTION_PROJECT_ADMIN)
    def set_git_remote(self, project_id: str, url: str | None) -> dict[str, Any]:
        """Set (or remove, with ``None``/empty) the project's remote URL; returns the new git status.

        The URL is validated with ``git ls-remote`` (400 when unreachable).
        Credentials are never stored: they stay with the user's git (ssh
        agent / credential helper).
        """
        self.store.load_project(project_id)
        if url:
            # Server edition: a remote can point anywhere the server reaches (a local path, an internal host).
            check_permission(ACTION_GIT_REMOTE_URL)
        project_dir = self.store.project_path(project_id)
        self._refuse_remote_in_data_dir(url)
        self._git_remote_call(lambda: self.history.set_remote(project_dir, url))
        return self.history.status(project_dir)

    @_requires(ACTION_PROJECT_ADMIN)
    def set_git_auto_push(self, project_id: str, enabled: bool) -> dict[str, Any]:
        """Turn the per-project auto-push opt-in on or off; returns the new git status."""
        self.store.load_project(project_id)
        project_dir = self.store.project_path(project_id)
        self._git_remote_call(lambda: self.history.set_auto_push(project_dir, bool(enabled)))
        return self.history.status(project_dir)

    @_requires(ACTION_PROJECT_WRITE)
    def git_push(self, project_id: str) -> dict[str, Any]:
        """Push ``current``, ``baseline`` and the ``baseline/*`` tags to the project's remote.

        A non-fast-forward rejection (another instance pushed first) is 409:
        pull first. 400 without a configured remote or when git fails.
        """
        self.store.load_project(project_id)
        return self._git_remote_call(lambda: self.history.push(self.store.project_path(project_id)))

    @_requires(ACTION_PROJECT_WRITE)
    def git_pull(self, project_id: str) -> dict[str, Any]:
        """Fetch the project's remote and fast-forward ``current`` (never a merge commit).

        Diverged histories are 409 with ``project_changed`` (the fetch
        already happened, so the status now shows the sync conflict and
        mutations are blocked until the user reconciles the branches in git).
        """
        self.store.load_project(project_id)
        return self._git_remote_call(
            lambda: self.history.pull(self.store.project_path(project_id), self._describer(project_id))
        )

    def add_project_from_remote(self, url: str) -> Project:
        """Clone a remote repository into the data directory as a project ("Add project from remote").

        The clone goes into a staging directory first and is validated like
        any loaded project (its project.json must load); only then is it
        moved into place under the id stored in its project.json, in one
        rename. A taken id is 409 and an invalid clone is 400; either way
        nothing is left behind.
        """
        check_permission(ACTION_PROJECT_CREATE)
        check_permission(ACTION_GIT_REMOTE_URL)
        return self._clone_project(url)

    def _clone_project(self, url: str) -> Project:
        """``add_project_from_remote`` after its permission checks; tells ``project_events``."""
        cleaned = (url or "").strip()
        self._refuse_remote_in_data_dir(cleaned)
        with self.store.staging_store() as staging:
            incoming = staging.root / "incoming"
            self._git_remote_call(lambda: self.history.clone(cleaned, incoming))
            project = staging.load_project("incoming")
            project_id = project.id
            if not isinstance(project_id, str) or not PROJECT_SLUG_PATTERN.fullmatch(project_id):
                raise ValueError(
                    f"The cloned repository's project.json has an unusable project id {project_id!r}"
                )
            # The staged project's folder must carry its final id for the rename.
            incoming.rename(staging.root / project_id)
            try:
                self.store.publish_staged_project(staging, project_id)
            except FileExistsError as exc:
                raise ConflictError(
                    f"A project '{project_id}' already exists in this data directory: {exc}"
                ) from exc
        project = self.store.load_project(project_id)
        self._project_created(project_id)
        return project

    # --- permissions (server edition; see the module docstring) --------------------

    def _project_created(self, project_id: str) -> None:
        """Tell the edition who created ``project_id`` (e.g. to make them its project admin).

        The project exists by now: a listener failure is logged, never raised.
        """
        try:
            HOOKS.project_events.project_created(current_identity(), project_id)
        except Exception:  # noqa: BLE001 - the project was created; report it, keep it
            logger.exception("Project %s was created, but the edition could not record it", project_id)
        audit(AUDIT_PROJECT_CREATE, project_id)

    def _project_removed(self, project_id: str) -> None:
        try:
            HOOKS.project_events.project_removed(current_identity(), project_id)
        except Exception:  # noqa: BLE001 - the project is gone already
            logger.exception("Project %s was removed, but the edition could not record it", project_id)

    def data_dir_token(self) -> str:
        """An opaque, stable id of the data folder, shown instead of its path to users who may not see it.

        ``remove_project`` accepts it in place of the path.
        """
        digest = hashlib.sha256(os.path.realpath(self.store.root).encode("utf-8", "surrogateescape")).hexdigest()
        return f"data-folder:{digest[:16]}"

    @staticmethod
    def _can_read(project_id: str) -> bool:
        """Whether the current identity may read ``project_id`` (always, in the local edition)."""
        return not guard_active() or is_permitted(ACTION_PROJECT_READ, project_id)

    # --- GitLab / GitHub (providers) ----------------------------------------------
    #
    # Connections are app-level settings (not project data); every provider
    # API call runs here, server-side (``providers.py``). Publishing and
    # reviews are git remote operations like Push: they never write project
    # files, so they are not ``_records_history`` mutations and are allowed
    # while the project is locked (as Push and Pull are).

    def list_provider_connections(self) -> list[dict[str, Any]]:
        """The GitLab/GitHub connections (never their tokens; ``has_token`` tells whether one is stored).

        Server edition: empty for a user who may not use the connections.
        """
        if guard_active() and not is_permitted(ACTION_CONNECTIONS_USE):
            return []
        return self.providers.list_connections()

    def add_provider_connection(self, provider: Any, url: Any, name: Any, token: Any) -> dict[str, Any]:
        """Add a connection; the token is verified with the provider and stored in the OS keychain."""
        check_permission(ACTION_CONNECTIONS_MANAGE)
        return self.providers.add_connection(provider, url, name, token)

    def update_provider_connection(self, connection_id: str, name: Any = None, token: Any = None) -> dict[str, Any]:
        """Rename a connection and/or replace its token (verified first)."""
        check_permission(ACTION_CONNECTIONS_MANAGE)
        return self.providers.update_connection(connection_id, name, token)

    def remove_provider_connection(self, connection_id: str) -> dict[str, Any]:
        """Remove a connection and its keychain token. Project remotes stay configured."""
        check_permission(ACTION_CONNECTIONS_MANAGE)
        return self.providers.remove_connection(connection_id)

    def provider_namespaces(self, connection_id: str) -> list[dict[str, Any]]:
        """Where a repository can be created: the user's own namespace, then groups / organisations."""
        check_permission(ACTION_CONNECTIONS_USE)
        return self.providers.provider(connection_id).namespaces()

    def provider_repositories(self, connection_id: str, search: Any = None, page: Any = None) -> dict[str, Any]:
        """One page of the user's repositories (most recently active first), optionally filtered by name."""
        check_permission(ACTION_CONNECTIONS_USE)
        provider = self.providers.provider(connection_id)
        return provider.repositories(self.providers.checked_search(search), self.providers.checked_page(page))

    @_requires(ACTION_PROJECT_ADMIN)
    def publish_project(
        self,
        project_id: str,
        connection_id: str,
        namespace_id: Any = None,
        name: Any = None,
        private: Any = True,
        description: Any = None,
    ) -> dict[str, Any]:
        """"Publish project": create the remote repository, set it as the project's remote, push.

        The project needs its own repository with at least one commit and no
        remote yet (409 otherwise). The repository is private unless
        ``private`` is false. When the repository was created but setting
        the remote or pushing failed, the error says so (and the project is
        reported changed, so clients refresh its status).
        """
        check_permission(ACTION_CONNECTIONS_USE)
        project = self.store.load_project(project_id)
        project_dir = self.store.project_path(project_id)
        status = self.history.status(project_dir)
        if status["state"] not in ("ready", "paused"):
            raise ConflictError(
                status["message"] or "The project has no git repository; use “Enable history (git)” first"
            )
        if status["head"] is None:
            raise ConflictError("The project has no commit yet; make a change or enable history first")
        if status["remote"] and status["remote"]["url"]:
            raise ConflictError(
                f"The project already has a remote ({status['remote']['url']}); remove it in the Git panel "
                "before publishing it elsewhere"
            )
        if namespace_id is not None and not isinstance(namespace_id, str):
            raise ValueError("The namespace id must be a string")
        repo_name = project.name if name is None or (isinstance(name, str) and not name.strip()) else name
        if not isinstance(repo_name, str) or len(repo_name.strip()) > 100 or any(ord(c) < 32 for c in repo_name):
            raise ValueError("The repository name must be one line of at most 100 characters")
        if not isinstance(private, bool):
            raise ValueError("'private' must be true or false")
        if description is not None and not isinstance(description, str):
            raise ValueError("The description must be a string")
        repo_description = (project.description if description is None else description).strip()[:250]
        provider = self.providers.provider(connection_id)
        repository = provider.create_repository(namespace_id or "", repo_name.strip(), private, repo_description)
        clone_url = repository.get("clone_url")
        try:
            if not clone_url:
                raise ValueError(f"{provider.label} answered without a clone URL")
            self._git_remote_call(lambda: self.history.set_remote(project_dir, clone_url))
            pushed = self._git_remote_call(lambda: self.history.push(project_dir))
        except ValueError as exc:
            error = ConflictError if isinstance(exc, ConflictError) else ValueError
            failure = error(
                f"The repository {repository.get('web_url') or repository.get('full_path')} was created on "
                f"{provider.label}, but publishing did not finish: {exc}"
            )
            failure.project_changed = True  # type: ignore[attr-defined]
            raise failure from exc
        return {"repository": repository, "pushed": pushed["pushed"], "status": self.history.status(project_dir)}

    def add_project_from_provider(self, connection_id: str, full_path: Any) -> Project:
        """"Add project from GitLab/GitHub": clone a repository picked from the provider's list.

        The clone URL comes from the provider API (never from the client);
        then it is the same path as ``add_project_from_remote``.
        """
        check_permission(ACTION_PROJECT_CREATE)
        check_permission(ACTION_CONNECTIONS_USE)
        path = self.providers.checked_repository_path(full_path)
        repository = self.providers.provider(connection_id).repository(path)
        clone_url = repository.get("clone_url")
        if not clone_url:
            raise ValueError(f"The repository '{path}' has no HTTPS clone URL")
        # The clone URL comes from the provider, not the client: no ``git.remote_url`` check.
        return self._clone_project(clone_url)

    def _review_target(self, project_dir: Path) -> tuple[Any, str, str]:
        """``(provider, repository path, remote URL)`` of the project's remote; ``ValueError`` without one."""
        status = self.history.status(project_dir)
        remote = status.get("remote") or {}
        url = remote.get("url")
        if not url:
            raise ValueError("The project has no remote; publish it or set a remote URL first")
        match = self.providers.connection_for_remote(url)
        if match is None:
            raise ValueError(
                f"The project's remote ({url}) does not belong to any GitLab / GitHub connection; "
                "add a connection for its server first"
            )
        connection, repo_path = match
        return self.providers.provider(connection.id), repo_path, url

    def _snapshot_at(self, project_dir: Path, project_id: str, commit: str | None) -> dict[str, Any]:
        files: dict[str, bytes | None] = {}
        if commit:
            _obj, files = self.history.commit_files(project_dir, commit)
        snapshot = build_snapshot(files)
        snapshot["project"] = self._snapshot_project_dict(
            project_id, snapshot["project"], f"Commit {(commit or '')[:12]} of project '{project_id}'"
        )
        return snapshot

    def _review_description(
        self, project: Project, project_dir: Path, pushed: dict[str, Any], limit: int = 200
    ) -> str:
        """The generated merge/pull request description: changed requirements and the baseline reference."""
        lines = [
            f"Review of the ReqManTool project **{project.name}** (`{project.id}`).",
            "",
            f"- Commits under review: {pushed['commits']} "
            f"(base `{(pushed['base'] or 'none')[:12]}`, head `{pushed['head'][:12]}`)",
        ]
        try:
            baselines = self.history.list_baselines(project_dir).get("baselines") or []
        except Exception:  # noqa: BLE001 - the description is best effort
            baselines = []
        if baselines:
            latest = baselines[0]
            lines.append(
                f"- Baseline reference: `baseline/{latest['name']}` (commit `{str(latest['commit'])[:12]}`"
                + (f", {latest['date']}" if latest.get("date") else "")
                + ")"
            )
        else:
            lines.append("- Baseline reference: none yet")
        try:
            result = compare_snapshots(
                self._snapshot_at(project_dir, project.id, pushed["base"]),
                self._snapshot_at(project_dir, project.id, pushed["head"]),
            )
        except Exception as exc:  # noqa: BLE001 - never fail the review over its description
            lines += ["", f"_The changed requirements could not be listed: {exc}_"]
            result = None
        if result is not None:
            summary = result["summary"]
            lines += [
                "",
                "### Changed requirements",
                "",
                f"{summary['requirements_added']} added, {summary['requirements_modified']} modified, "
                f"{summary['requirements_removed']} removed; links +{summary['links_added']} "
                f"−{summary['links_removed']}; modules +{summary['modules_added']} −{summary['modules_removed']}; "
                f"attribute definitions +{summary['attributes_added']} −{summary['attributes_removed']} "
                f"~{summary['attributes_changed']}.",
            ]
            shown = 0
            omitted = 0
            for module in result["modules"]:
                change = f" ({module['change']})" if module["change"] else ""
                entries: list[str] = []
                for label, ids in (
                    ("Added", module["added"]),
                    ("Modified", [m["id"] for m in module["modified"]]),
                    ("Removed", module["removed"]),
                ):
                    if not ids:
                        continue
                    room = max(limit - shown, 0)
                    visible, hidden = ids[:room], ids[room:]
                    shown += len(visible)
                    omitted += len(hidden)
                    if visible:
                        entries.append(f"- {label}: " + ", ".join(f"`{i}`" for i in visible))
                if entries:
                    lines += ["", f"#### {module['name']} (`{module['module_id']}`){change}", *entries]
            if omitted:
                lines += ["", f"…and {omitted} more requirement(s); see the commit browser for the full list."]
        lines += [
            "",
            "---",
            "_Generated by ReqManTool. Merge with a merge commit or fast-forward (not squash or rebase), "
            "so ReqManTool instances can fast-forward with Pull._",
        ]
        return "\n".join(lines)

    @_requires(ACTION_PROJECT_WRITE)
    def start_review(
        self, project_id: str, branch: Any = None, title: Any = None
    ) -> dict[str, Any]:
        """Push ``current`` to a review branch and open a merge/pull request into ``current``.

        The project's remote must belong to a connection. ``branch``
        defaults to ``review/<UTC timestamp>``; an open request from that
        branch is reused (re-pushing updates it). The description lists the
        changed requirements since the remote's ``current`` and the latest
        baseline. The request is tracked in ``.git/config`` (per instance)
        for ``review_status``. When the branch was pushed but the request
        could not be opened, the error says so (retry with the same branch).
        """
        project = self.store.load_project(project_id)
        project_dir = self.store.project_path(project_id)
        provider, repo_path, _url = self._review_target(project_dir)
        if branch is None or (isinstance(branch, str) and not branch.strip()):
            branch = f"review/{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
        if not isinstance(branch, str):
            raise ValueError("The review branch must be a string")
        if title is not None and not isinstance(title, str):
            raise ValueError("The title must be a string")
        pushed = self._git_remote_call(lambda: self.history.push_review_branch(project_dir, branch))
        try:
            description = self._review_description(project, project_dir, pushed)
            review = provider.find_open_change_request(repo_path, pushed["branch"])
            if review is None:
                review_title = (title or "").strip() or (
                    f"Review: {project.name} ({pushed['commits']} commit{'s' if pushed['commits'] != 1 else ''})"
                )
                review = provider.create_change_request(
                    repo_path, pushed["branch"], "current", review_title[:250], description
                )
            self._git_remote_call(
                lambda: self.history.set_review(
                    project_dir,
                    {
                        "branch": pushed["branch"],
                        "number": review.get("number"),
                        "url": review.get("url"),
                        "connection": provider.connection.id,
                    },
                )
            )
        except ValueError as exc:
            failure = ValueError(
                f"The branch '{pushed['branch']}' was pushed, but the {self._review_noun(provider)} "
                f"could not be opened: {exc}. Retry with the branch name '{pushed['branch']}'."
            )
            failure.project_changed = True  # type: ignore[attr-defined]
            raise failure from exc
        return {"review": review, "pushed": pushed, "status": self.history.status(project_dir)}

    @staticmethod
    def _review_noun(provider: Any) -> str:
        return "merge request" if provider.kind == "gitlab" else "pull request"

    def review_status(self, project_id: str) -> dict[str, Any]:
        """The tracked merge/pull request, live from the provider: state, approvals (read-only)."""
        self.store.load_project(project_id)
        project_dir = self.store.project_path(project_id)
        status = self.history.status(project_dir)
        tracked = (status.get("remote") or {}).get("review")
        if not tracked:
            return {"tracked": None, "review": None, "provider": None}
        provider, repo_path, _url = self._review_target(project_dir)
        review = provider.change_request(repo_path, tracked["number"])
        return {
            "tracked": tracked,
            "review": review,
            "provider": provider.kind,
            "provider_label": PROVIDER_LABELS.get(provider.kind, provider.kind),
            "repository": repo_path,
        }

    @_requires(ACTION_PROJECT_WRITE)
    def forget_review(self, project_id: str) -> dict[str, Any]:
        """Stop tracking the project's merge/pull request (nothing changes on the provider)."""
        self.store.load_project(project_id)
        project_dir = self.store.project_path(project_id)
        self._git_remote_call(lambda: self.history.set_review(project_dir, None))
        return self.history.status(project_dir)

    def provider_info_for_project(self, project_id: str) -> dict[str, Any]:
        """Which connection (if any) the project's remote belongs to (for the Git panel). Read-only."""
        self.store.load_project(project_id)
        status = self.history.status(self.store.project_path(project_id))
        url = (status.get("remote") or {}).get("url")
        match = self.providers.connection_for_remote(url) if url else None
        if match is None:
            return {"connection": None, "repository": None}
        connection, repo_path = match
        return {
            "connection": {
                "id": connection.id,
                "name": connection.name,
                "provider": connection.provider,
                "provider_label": PROVIDER_LABELS.get(connection.provider, connection.provider),
            },
            "repository": repo_path,
        }

    # --- baselines (git) ---------------------------------------------------------

    @_requires(ACTION_PROJECT_ADMIN)
    def create_baseline(
        self,
        project_id: str,
        name: str,
        description: str = "",
        lock: bool = False,
        lock_reason: Any = None,
    ) -> dict[str, Any]:
        """Create a named baseline: commit pending changes, tag ``baseline/<name>``, fast-forward ``baseline``.

        See ``GitHistory.create_baseline`` for the rules. A nested/foreign/
        paused/absent repository, a duplicate name or a hand-moved
        ``baseline`` branch is ``ConflictError`` (409); an invalid name or
        git off/missing is ``ValueError`` (400).

        Allowed while the project is locked (it changes no data). With
        ``lock`` ("lock after baseline"), the project is locked
        (``lock_project``; reason ``lock_reason`` or "Baseline <name>",
        recording the baseline name) unless it is locked already. The lock
        is written and committed *before* the tag, so the baseline is
        exactly the locked state and reports computed while the project
        stays locked are at that baseline's commit; if the baseline then
        fails, the lock is undone. The result is the baseline plus
        ``locked``: the project's lock afterwards (or null).
        """
        project = self.store.load_project(project_id)
        self._require_no_sync_conflict(project_id)
        locking = bool(lock) and project.locked is None
        if locking:
            reason = self._checked_lock_reason(lock_reason)
            checked_name = validate_baseline_name(name)
            project = self.lock_project(project_id, reason or f"Baseline {checked_name}", checked_name)
        from . import __version__

        try:
            try:
                result = self.history.create_baseline(
                    self.store.project_path(project_id),
                    name,
                    description or "",
                    __version__,
                    PROJECT_FORMAT.current,
                    self._describer(project_id),
                )
            except RepoConflict as exc:
                raise ConflictError(str(exc)) from exc
        except Exception:
            if locking:
                try:
                    self.unlock_project(project_id)
                except Exception:  # noqa: BLE001 - report the baseline failure, not this one
                    logger.exception("Could not undo the lock of %s after a failed baseline", project_id)
            raise
        return {**result, "locked": project.locked.to_dict() if project.locked is not None else None}

    def list_baselines(self, project_id: str) -> dict[str, Any]:
        """The project's baselines (from its ``baseline/*`` tags), newest first. Read-only."""
        self.store.load_project(project_id)
        return self.history.list_baselines(self.store.project_path(project_id))

    def _baseline_files(self, project_id: str, name: str) -> tuple[str, dict[str, bytes | None]]:
        self.store.load_project(project_id)
        return self.history.baseline_files(self.store.project_path(project_id), name)

    def _baseline_project(self, project_id: str, name: str, files: dict[str, bytes | None]) -> Project:
        """The baseline's project.json as a ``Project``, upgraded in memory like ``load_project``.

        A baseline written by a newer ReqManTool raises ``NewerFormatError``
        (REST 409); an unreadable one raises ``ValueError``.
        """
        return self._snapshot_project(f"Baseline '{name}' of project '{project_id}'", files)

    @staticmethod
    def _snapshot_project(what: str, files: dict[str, bytes | None]) -> Project:
        """A snapshot's (baseline's, commit's) project.json as a ``Project``, upgraded like ``load_project``."""
        raw = files.get("project.json")
        if raw is None:
            raise ValueError(f"{what} has no readable project.json")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"{what} has an invalid project.json: not valid JSON ({exc})") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{what} has an invalid project.json: expected a JSON object")
        try:
            return Project.from_dict(upgrade(data, PROJECT_FORMAT, what))
        except KeyError as exc:
            raise ValueError(f"{what} has an invalid project.json: missing key {exc}") from exc
        except (TypeError, AttributeError) as exc:
            raise ValueError(f"{what} has an invalid project.json: {exc}") from exc

    @staticmethod
    def _baseline_modules(project: Project, files: dict[str, bytes | None]) -> list[Module]:
        """The baseline's modules, in the baseline project's (tolerant) module order."""
        modules: dict[str, Module] = {}
        for path, content in files.items():
            match = re.match(r"^modules/([^/]+)/module\.json$", path)
            if match is None or content is None:
                continue
            try:
                modules[match.group(1)] = Module.from_dict(json.loads(content.decode("utf-8-sig")))
            except (ValueError, TypeError, KeyError, RecursionError):
                continue  # an unreadable module file is skipped, like an unreadable requirement
        order = module_tree.module_ids(module_tree.effective_tree(project.module_order, sorted(modules)))
        return [modules[m] for m in order]

    def get_baseline(self, project_id: str, name: str) -> dict[str, Any]:
        """The project and modules as they were at baseline ``name``. Read-only.

        Read from the tag's commit in memory, never from a checkout; a
        missing baseline is 404 and a baseline written by a newer ReqManTool
        is 409 (``NewerFormatError``).
        """
        commit, files = self._baseline_files(project_id, name)
        project = self._baseline_project(project_id, name, files)
        return {
            "name": name,
            "commit": commit,
            "project": project.to_dict(),
            "modules": [m.to_dict() for m in self._baseline_modules(project, files)],
        }

    def baseline_requirements(self, project_id: str, name: str, module_id: str) -> list[Requirement]:
        """A module's requirements as they were at baseline ``name`` (read-only), sorted by id."""
        commit, files = self._baseline_files(project_id, name)
        project = self._baseline_project(project_id, name, files)  # also rejects a newer format
        del commit, project
        return self._snapshot_requirements(files, module_id, f"baseline '{name}'")

    @staticmethod
    def _snapshot_requirements(files: dict[str, bytes | None], module_id: str, where: str) -> list[Requirement]:
        """A module's requirements in a snapshot's files, sorted by id; 404 when the module is not there."""
        if f"modules/{module_id}/module.json" not in files:
            raise FileNotFoundError(f"Module '{module_id}' not found in {where}")
        requirements = []
        prefix = f"modules/{module_id}/requirements/"
        for path, content in files.items():
            if not (path.startswith(prefix) and content is not None):
                continue
            try:
                requirements.append(Requirement.from_dict(json.loads(content.decode("utf-8-sig"))))
            except (ValueError, TypeError, KeyError, RecursionError):
                continue
        return sorted(requirements, key=lambda r: r.id)

    def _working_files(self, project_id: str) -> dict[str, bytes | None]:
        """The project's data files as they are on disk now (including uncommitted changes)."""
        root = self.store.project_path(project_id)
        files: dict[str, bytes | None] = {}

        def add(relative: str) -> None:
            try:
                files[relative] = (root / relative).read_bytes()
            except OSError:
                pass

        add("project.json")
        for module_id in self.store.list_module_ids(project_id):
            add(f"modules/{module_id}/module.json")
            for requirement_id in self.store.list_requirement_ids(project_id, module_id):
                add(self.store.requirement_relative_path(module_id, requirement_id))
        links_dir = root / "links"
        if links_dir.is_dir():
            for path in sorted(links_dir.glob("*.json")):
                if not path.name.startswith("."):
                    add(f"links/{path.name}")
        return files

    def compare_baselines(self, project_id: str, from_name: str, to_name: str) -> dict[str, Any]:
        """Requirement-level differences between two baselines, or a baseline and ``current``.

        ``current`` (either side) means the working files on disk, including
        uncommitted changes. Read-only; a missing baseline is 404, a baseline
        written by a newer ReqManTool is 409.
        """
        self.store.load_project(project_id)  # 404 / stub / newer-format of the live project

        def side(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
            if name == "current":
                files = self._working_files(project_id)
                meta: dict[str, Any] = {"name": "current", "commit": None}
            else:
                commit, files = self._baseline_files(project_id, name)
                meta = {"name": name, "commit": commit}
            snapshot = build_snapshot(files)
            if snapshot["project"] is not None:
                what = (
                    f"Project '{project_id}'" if name == "current" else f"Baseline '{name}' of project '{project_id}'"
                )
                snapshot["project"] = upgrade(snapshot["project"], PROJECT_FORMAT, what)
            return meta, snapshot

        from_meta, from_snapshot = side(from_name)
        to_meta, to_snapshot = side(to_name)
        result = compare_snapshots(from_snapshot, to_snapshot)
        self._add_compare_row_styles(result, from_snapshot, to_snapshot)
        return {"from": from_meta, "to": to_meta, **result}

    @classmethod
    def _add_compare_row_styles(cls, result: dict[str, Any], old: dict[str, Any], new: dict[str, Any]) -> None:
        """Give each compared module the row style of its listed requirements.

        A module entry gains ``row_styles`` (requirement id -> rule) for the
        styled ones only, and only when there is one: added and modified
        requirements are styled by the "to" side's rules and values, removed
        ones by the "from" side's. A side whose project.json predates row
        styles, or cannot be read as a project, styles nothing.
        """

        def rules(snapshot: dict[str, Any]) -> list[RowStyle]:
            try:
                return cls._effective_row_styles(Project.from_dict(snapshot["project"]))
            except (KeyError, TypeError, AttributeError, ValueError):
                return []

        old_rules, new_rules = rules(old), rules(new)
        if not (old_rules or new_rules):
            return
        for module in result["modules"]:
            module_id = module["module_id"]
            styled: dict[str, Any] = {}
            listed = [(i, new, new_rules) for i in module["added"]]
            listed += [(m["id"], new, new_rules) for m in module["modified"]]
            listed += [(i, old, old_rules) for i in module["removed"]]
            for requirement_id, snapshot, side_rules in listed:
                data = snapshot["requirements"].get(module_id, {}).get(requirement_id, {})
                values = data.get("attributes") if isinstance(data, dict) else None
                style = cls._row_style_of(side_rules, values if isinstance(values, dict) else {})
                if style is not None:
                    styled[requirement_id] = style.to_dict()
            if styled:
                module["row_styles"] = styled

    @staticmethod
    def _row_style_of(rules: list[RowStyle], values: dict[str, Any]) -> RowStyle | None:
        """The first of ``rules`` whose attribute value ``values`` has (the DOCX export's rule)."""
        return next((r for r in rules if values.get(r.attribute_id) == r.value), None)

    # --- projects ----------------------------------------------------------

    def create_project(
        self,
        name: str,
        description: str = "",
        template_id: str | None = None,
        parent_project_id: str | None = None,
    ) -> Project:
        """Create a project, blank or from a template, optionally as a subproject.

        From a template, the whole project (``project.json``, modules, empty
        link-pair files) is built and validated in memory with the same
        helpers as the individual create operations, written into a staging
        directory, and moved into the data directory in one rename: an
        invalid template or a failed write leaves nothing behind.

        Server edition: needs ``project.create``, and ``project.write`` on the
        parent for a subproject; the creator is reported to
        ``project_events`` (and so becomes the project's admin).
        """
        check_permission(ACTION_PROJECT_CREATE)
        if isinstance(parent_project_id, str):
            check_permission(ACTION_PROJECT_WRITE, parent_project_id)
        self._check_parent(None, parent_project_id)
        if template_id is None:
            project_id = _unique_slug(
                name, self.store.project_exists, fallback="project", max_length=self.store.max_name_length()
            )
            project = Project(
                id=project_id,
                name=name,
                description=description,
                parent_project_id=parent_project_id,
                attributes=list(BUILTIN_ATTRIBUTES),
            )
            self.store.save_project(project)
            self._start_history(project)
            self._project_created(project.id)
            return project

        template = self._load_template(template_id)
        # Skip any path that is taken, even by a non-project directory: the
        # staged project is moved there by a rename, which must not merge
        # into (or clobber) existing files.
        project_id = _unique_slug(
            name, self.store.project_path_exists, fallback="project", max_length=self.store.max_name_length()
        )
        try:
            project, modules, requirements = self._build_from_template(
                template, project_id, name, description, parent_project_id
            )
        except ValueError as exc:
            raise ValueError(f"Template '{template_id}' is invalid: {exc}") from exc

        with self.store.staging_store() as staging:
            for module in modules:
                staging.save_module(project.id, module)
                for requirement in requirements.get(module.id, []):
                    staging.save_requirement(project.id, module.id, requirement)
            for config in project.link_configurations:
                staging.save_link_pairs(project.id, config.id, [])
            staging.save_project(project)
            try:
                self.store.publish_staged_project(staging, project.id)
            except FileExistsError as exc:
                raise ValueError(f"{exc}; try again") from exc
        # The repository is created in the final folder, after the rename.
        self._start_history(project)
        self._project_created(project.id)
        return project

    def _start_history(self, project: Project) -> None:
        """Give a new project its git repository and initial commit (skipped when not possible)."""
        if self.history.enabled:
            self.history.init_new_project(self.store.project_path(project.id), project.name)

    def get_project(self, project_id: str) -> Project:
        return self.store.load_project(project_id)

    def list_projects(self) -> list[Project | ProjectStub]:
        """Every project, with ``parent_project_id`` as the effective subproject tree.

        Hand-edited data can hold a parent that does not exist, or a cycle;
        both are shown as top level here (only in the returned objects, the
        files are not rewritten), so the tree always terminates.
        ``get_project`` returns the stored value unchanged.

        A project whose project.json cannot be loaded (written by a newer
        ReqManTool, not valid UTF-8 JSON, wrong shape, invalid
        ``format_version``, unreadable) is listed as a ``ProjectStub``
        carrying the error, at the top level, instead of failing the whole
        list. Its subprojects stay under it. Nothing is written.

        Server edition: only the projects the user may read are listed; a
        project whose parent is hidden is shown at the top level.
        """
        projects: list[Project | ProjectStub] = []
        for project_id in self.store.list_project_ids():
            if not self._can_read(project_id):
                continue
            try:
                projects.append(self.store.load_project(project_id))
            except FileNotFoundError:
                continue  # removed since it was listed
            except (OSError, ValueError) as exc:
                logger.debug("Project %s cannot be loaded: %s", project_id, exc)
                projects.append(ProjectStub(id=project_id, name=self._stub_name(project_id), error=str(exc)))
        broken = self._broken_parent_ids(projects)
        for project in projects:
            if project.id in broken:
                project.parent_project_id = None
        return projects

    def _stub_name(self, project_id: str) -> str:
        """The ``name`` stored in an unloadable project.json, if it has a usable one; else the id."""
        try:
            text: str | None = self.store.read_project_text(project_id)
        except (OSError, ValueError):
            text = None
        return _display_name(text, project_id)

    @staticmethod
    def _broken_parent_ids(projects: list[Project | ProjectStub]) -> set[str]:
        """Ids of projects whose stored parent is dangling, themselves, or part of a cycle."""
        ids = {p.id for p in projects}
        parent_of: dict[str, str | None] = {}
        broken: set[str] = set()
        for project in projects:
            parent = project.parent_project_id
            if parent is None:
                parent_of[project.id] = None
            elif not isinstance(parent, str) or parent not in ids or parent == project.id:
                parent_of[project.id] = None
                broken.add(project.id)
            else:
                parent_of[project.id] = parent
        done: set[str] = set()
        for start in parent_of:
            path: list[str] = []
            on_path: set[str] = set()
            node: str | None = start
            while node is not None and node not in done and node not in on_path:
                path.append(node)
                on_path.add(node)
                node = parent_of[node]
            if node is not None and node in on_path:
                broken.update(path[path.index(node):])
            done.update(path)
        return broken

    def _check_parent(self, project_id: str | None, parent_project_id: Any) -> None:
        """Raise unless ``parent_project_id`` may be the parent of ``project_id``.

        ``None`` (top level) is always allowed. Otherwise the parent must be
        one of the ids ``list_project_ids`` returns (never a path such as
        ``a/../b``), other than the project itself, and (for an existing
        project) must not be one of its descendants. The ancestor walk only
        follows ids of that set and stops at a missing, unreadable or
        already-visited project, so hand-edited dangling, path-like or cyclic
        data cannot make it loop or read outside the data directory. A parent
        (or, when moving, any ancestor) written by a newer ReqManTool raises
        ``NewerFormatError``: its own parent cannot be read, so "create under"
        and "move under" it are both refused and nothing is written.
        """
        if parent_project_id is None:
            return
        if not isinstance(parent_project_id, str) or not parent_project_id:
            raise ValueError("parent_project_id must be a project id or null")
        if parent_project_id == project_id:
            raise ValueError("A project cannot be its own parent")
        project_ids = set(self.store.list_project_ids())
        if parent_project_id not in project_ids:
            raise FileNotFoundError(f"Parent project '{parent_project_id}' not found")
        seen: set[str] = set()
        node: Any = parent_project_id
        while isinstance(node, str) and node in project_ids and node not in seen:
            if node == project_id:
                raise ValueError(
                    f"Cannot make '{parent_project_id}' the parent of '{project_id}': "
                    f"'{parent_project_id}' is a subproject of '{project_id}'"
                )
            seen.add(node)
            try:
                node = self.store.load_project(node).parent_project_id
            except NewerFormatError:
                raise  # its parent is unknown, so a cycle cannot be ruled out: refuse (409)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
                break  # missing or malformed project.json: its parent is unknown
            if project_id is None:
                break  # a new project has no subprojects: only its parent's format matters

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def set_project_parent(self, project_id: str, parent_project_id: str | None) -> Project:
        """Move a project in the subproject tree; ``None`` makes it top level.

        Server edition: needs ``project.admin`` on the project and
        ``project.write`` on the new parent.
        """
        project = self.store.load_project(project_id)
        if isinstance(parent_project_id, str) and parent_project_id != project.parent_project_id:
            check_permission(ACTION_PROJECT_WRITE, parent_project_id)
        self._check_parent(project_id, parent_project_id)
        self._check_references_in_use(
            project, self._effective_references(replace(project, parent_project_id=parent_project_id))
        )
        if project.parent_project_id != parent_project_id:
            project.parent_project_id = parent_project_id
            self.store.save_project(project)
        return project

    # --- project lifecycle: lock, unlock, remove ---------------------------------

    @staticmethod
    def _locked_message(project: Project) -> str:
        """"Project 'x' is locked (by ..., on ...: reason)." for a locked project."""
        lock = project.locked or ProjectLock()
        details = []
        if lock.by:
            details.append(f"by {lock.by}")
        if lock.at:
            details.append(f"on {lock.at[:10]}")
        text = " ".join(details)
        if lock.reason:
            text = f"{text}: {lock.reason}" if text else lock.reason
        return f"Project '{project.name}' is locked{f' ({text})' if text else ''}."

    def _require_unlocked(self, project_id: str) -> None:
        """The project lock guard: refuse (``ProjectLockedError``) any change of a locked project.

        A project that cannot be loaded is left to the operation itself,
        which reports it (404, newer format, broken file).
        """
        try:
            project = self.store.load_project(project_id)
        except (OSError, ValueError):
            return
        if project.locked is not None:
            raise ProjectLockedError(
                f"{self._locked_message(project)} Unlock it to make changes; nothing was changed."
            )

    @staticmethod
    def _checked_lock_reason(reason: Any) -> str:
        if reason is None:
            return ""
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        text = " ".join(reason.split())
        if len(text) > MAX_LOCK_REASON_LENGTH:
            raise ValueError(f"The lock reason is too long: at most {MAX_LOCK_REASON_LENGTH} characters")
        return text

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def lock_project(self, project_id: str, reason: Any = None, baseline: str | None = None) -> Project:
        """Lock a project: every change of it is then refused until it is unlocked (409 if already locked).

        Records who (the git identity commits use), when (UTC) and why, and
        the baseline when locked right after creating one. Locking writes
        project.json (and so commits, with history on).
        """
        project = self.store.load_project(project_id)
        text = self._checked_lock_reason(reason)
        if project.locked is not None:
            raise ConflictError(f"{self._locked_message(project)} Unlock it first to lock it again.")
        name, email = self.history.user_identity(self.store.project_path(project_id))
        project.locked = ProjectLock(
            by=name,
            email=email,
            at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            reason=text,
            baseline=baseline,
        )
        self.store.save_project(project)
        audit(AUDIT_PROJECT_LOCK, project_id, reason=text, baseline=baseline)
        return project

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    @_allowed_while_locked
    def unlock_project(self, project_id: str) -> Project:
        """Unlock a project (the one change a locked project allows). Not locked: nothing is written."""
        project = self.store.load_project(project_id)
        if project.locked is not None:
            project.locked = None
            self.store.save_project(project)
            audit(AUDIT_PROJECT_UNLOCK, project_id)
        return project

    def _removal_blockers(self, project_id: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """``(subprojects, referencing projects)`` of ``project_id``, each ``{id, name}``, sorted by id.

        Subprojects by their stored parent. A project that cannot be loaded
        (a stub) is read best-effort from its raw project.json, so a broken
        subproject or referencing project still blocks the removal.
        """
        subprojects: list[dict[str, str]] = []
        referencing: list[dict[str, str]] = []
        for other_id in self.store.list_project_ids():
            if other_id == project_id:
                continue
            try:
                other = self.store.load_project(other_id)
                parent, references, name = other.parent_project_id, other.references, other.name
            except (OSError, ValueError):
                try:
                    raw = json.loads(self.store.read_project_text(other_id))
                except (OSError, ValueError, RecursionError):
                    continue
                if not isinstance(raw, dict):
                    continue
                parent = raw.get("parent_project_id")
                references = raw.get("references") if isinstance(raw.get("references"), list) else []
                name = self._stub_name(other_id)
            entry = {"id": other_id, "name": name if isinstance(name, str) else other_id}
            if parent == project_id:
                subprojects.append(entry)
            elif project_id in references:
                referencing.append(entry)
        return subprojects, referencing

    def _recovery_hint(self, status: dict[str, Any] | None) -> str:
        """How a removed project could be recovered, from its git status (``None``: history off)."""
        state = (status or {}).get("state")
        if state == "nested":
            return (
                f"The data folder is inside the git repository at '{status.get('enclosing_repo')}'. If the "
                "project folder is committed there, it can be restored from that repository with git "
                "(e.g. git restore or git checkout); changes not committed there are lost."
            )
        if state in ("ready", "paused"):
            remote = status.get("remote") or {}
            url = remote.get("url")
            if not url:
                return (
                    "The project's history (its .git folder) lives inside the project folder and is "
                    "deleted with it, including its baselines. It has no remote, so the removal cannot "
                    "be undone unless the data folder is backed up elsewhere. Set a remote and push "
                    "first if you may need it again."
                )
            unsaved = []
            if status.get("pending_changes"):
                unsaved.append("uncommitted changes")
            if remote.get("ahead"):
                unsaved.append(f"{remote['ahead']} unpushed commit(s)")
            elif remote.get("ahead") is None:
                unsaved.append("possibly unpushed commits (the remote was never fetched)")
            tail = (
                f" Not on the remote, and lost: {', '.join(unsaved)} — push first."
                if unsaved
                else " Everything is pushed."
            )
            return (
                "The project's history (its .git folder) lives inside the project folder and is deleted "
                f"with it. A copy remains at its remote {url}: recover it with File ▸ Add project from "
                f"remote (git clone).{tail}"
            )
        return (
            "The project has no git history of its own, so the removal cannot be undone unless the data "
            "folder is backed up elsewhere."
        )

    def project_removal_info(self, project_id: str) -> dict[str, Any]:
        """What removing the project would do and whether it is allowed (read-only).

        ``subprojects`` and ``referenced_by`` block the removal (as does
        ``locked``); ``history`` is the project's git status (``None`` with
        history off) and ``recovery`` explains, from it, whether and how the
        project could be recovered after removal.
        """
        project = self.store.load_project(project_id)
        subprojects, referencing = self._removal_blockers(project_id)
        status = self.history.status(self.store.project_path(project_id)) if self.history.enabled else None
        return {
            "project_id": project_id,
            "name": project.name,
            "locked": project.locked.to_dict() if project.locked is not None else None,
            "subprojects": subprojects,
            "referenced_by": referencing,
            "can_remove": project.locked is None and not subprojects and not referencing,
            "history": status,
            "recovery": self._recovery_hint(status),
        }

    @_requires(ACTION_PROJECT_ADMIN)
    def remove_project(self, project_id: str, confirm_name: Any, data_dir: Any = None) -> dict[str, Any]:
        """Delete a project's folder, with everything in it (its modules, links and own git history).

        Safety rules, all checked before anything is deleted:
        ``confirm_name`` must be the project's name (typed by the user, 400);
        ``data_dir``, when given, must still be this service's data directory
        (409: the folder was switched while the confirmation was open, and
        project ids repeat across folders); a locked project is refused
        (423); so is a project with subprojects or one another project
        references (409, listing them): move or remove those first. Nothing
        outside the project folder is touched (``ProjectStore.delete_project``).
        No referencing project exists, so no other project holds links to it.
        """
        project = self.store.load_project(project_id)
        if data_dir is not None:
            if not isinstance(data_dir, str) or (
                data_dir != self.data_dir_token() and os.path.realpath(data_dir) != os.path.realpath(self.store.root)
            ):
                raise ConflictError(
                    "The data folder changed since the removal was confirmed; nothing was removed. "
                    "Open the project again to remove it."
                )
        if not isinstance(confirm_name, str) or confirm_name.strip() != project.name.strip():
            raise ValueError(f"Type the project name '{project.name}' to confirm its removal; nothing was removed")
        if project.locked is not None:
            raise ProjectLockedError(f"{self._locked_message(project)} Unlock it to remove it; nothing was removed.")
        subprojects, referencing = self._removal_blockers(project_id)
        if subprojects or referencing:
            parts = []
            if subprojects:
                parts.append("it has subprojects " + ", ".join(f"'{p['name']}' ({p['id']})" for p in subprojects))
            if referencing:
                parts.append(
                    "it is referenced by " + ", ".join(f"'{p['name']}' ({p['id']})" for p in referencing)
                )
            todo = []
            if subprojects:
                todo.append("move or remove the subprojects")
            if referencing:
                todo.append("remove those references")
            raise ConflictError(
                f"Project '{project.name}' cannot be removed: {' and '.join(parts)}. "
                f"{' and '.join(todo).capitalize()} first; nothing was removed."
            )
        project_dir = self.store.project_path(project_id)
        with self.history.folder_lock(project_dir):
            leftover = self.store.delete_project(project_id)
        self.history.forget(project_dir)
        if leftover is not None:
            logger.warning("Removed project %s, but could not delete all of %s", project_id, leftover)
        self._project_removed(project_id)
        audit(AUDIT_PROJECT_REMOVE, project_id, name=project.name)
        return {"removed": project_id, "name": project.name, "leftover": leftover}

    # --- project references -----------------------------------------------------

    @staticmethod
    def _effective_references(project: Project) -> list[str]:
        """The projects ``project`` references: its parent (implicitly), then ``references``.

        No duplicates and never the project itself (hand-edited data is
        filtered here, not rewritten). Link configurations may target the
        modules of these projects.
        """
        candidates = [project.parent_project_id] if isinstance(project.parent_project_id, str) else []
        refs: list[str] = []
        for ref in [*candidates, *project.references]:
            if ref and ref != project.id and ref not in refs:
                refs.append(ref)
        return refs

    def _check_references_in_use(self, project: Project, new_effective: list[str]) -> None:
        """Refuse to stop referencing a project that a link configuration of ``project`` targets.

        Removing an explicit reference, or reparenting away from the parent
        (implicitly referenced), would leave cross-project link
        configurations pointing at a project that is no longer referenced.
        The user removes those configurations first (which deletes their
        links); nothing is cleaned up implicitly.
        """
        dropped = set(self._effective_references(project)) - set(new_effective)
        in_use: dict[str, list[str]] = {}
        for config in project.link_configurations:
            if config.target_project_id in dropped:
                in_use.setdefault(config.target_project_id, []).append(config.name)
        if in_use:
            target, names = sorted(in_use.items())[0]
            listed = ", ".join(f"'{n}'" for n in names)
            raise ValueError(
                f"Project '{project.id}' cannot stop referencing project '{target}': link "
                f"configuration(s) {listed} target it. Remove them first"
            )

    def _checked_references(self, project_id: str, references: Any) -> list[str]:
        """``references`` as stored: existing, loadable project ids, never ``project_id``, no duplicates.

        ``None`` means none. A dangling id is not found (404); a project that
        cannot be loaded (newer format 409, broken file 400) cannot be
        referenced either, since its modules cannot be read.
        """
        if references is None:
            return []
        if not isinstance(references, list) or not all(isinstance(r, str) and r for r in references):
            raise ValueError("references must be a list of project ids")
        if len(references) > MAX_PROJECT_REFERENCES:
            raise ValueError(f"A project can reference at most {MAX_PROJECT_REFERENCES} projects")
        listed = set(self.store.list_project_ids())
        result: list[str] = []
        for ref in references:
            if ref == project_id:
                raise ValueError("A project cannot reference itself")
            if ref in result:
                continue
            if ref not in listed:
                raise FileNotFoundError(f"Referenced project '{ref}' not found")
            self.store.load_project(ref)
            result.append(ref)
        return result

    @_records_history
    def set_project_references(self, project_id: str, references: Any) -> Project:
        """Replace the projects ``project_id`` references explicitly (``None``/``[]``: none).

        The parent stays referenced implicitly whether or not it is listed.
        Dropping a reference that a cross-project link configuration
        targets is refused (``_check_references_in_use``).
        """
        project = self.store.load_project(project_id)
        refs = self._checked_references(project_id, references)
        for ref in refs:
            if ref not in project.references and ref != project.parent_project_id:
                check_permission(ACTION_PROJECT_READ, ref)  # server edition: only readable ones are added
        self._check_references_in_use(project, self._effective_references(replace(project, references=refs)))
        if project.references != refs:
            project.references = refs
            self.store.save_project(project)
        return project

    def project_references(self, project_id: str) -> dict[str, Any]:
        """The projects ``project_id`` references and the projects that reference it (read-only).

        ``references``: the effective list (parent first, marked
        ``implicit``), each with its name (or ``error`` when it is missing
        or cannot be loaded) and the number of this project's link
        configurations targeting it. ``referenced_by``: every loadable
        project whose effective references include this one, with its
        configurations targeting this project and their link counts.
        """
        project = self.store.load_project(project_id)
        listed = set(self.store.list_project_ids())
        references: list[dict[str, Any]] = []
        for ref in self._effective_references(project):
            entry: dict[str, Any] = {
                "id": ref,
                "name": ref,
                "implicit": ref == project.parent_project_id and ref not in project.references,
                "link_configurations": sum(1 for c in project.link_configurations if c.target_project_id == ref),
            }
            try:
                if ref not in listed:
                    raise FileNotFoundError(f"Project '{ref}' not found")
                if not self._can_read(ref):
                    entry["error"] = "You have no access to this project"
                    references.append(entry)
                    continue
                entry["name"] = self.store.load_project(ref).name
            except (OSError, ValueError) as exc:
                entry["error"] = str(exc)
            references.append(entry)
        referenced_by: list[dict[str, Any]] = []
        for other in self._other_projects(project_id):
            if project_id not in self._effective_references(other) or not self._can_read(other.id):
                continue
            configs = [
                {
                    "id": c.id,
                    "name": c.name,
                    "links": len(self.store.load_link_pairs(other.id, c.id)),
                }
                for c in other.link_configurations
                if c.target_project_id == project_id
            ]
            referenced_by.append({"id": other.id, "name": other.name, "link_configurations": configs})
        return {"project_id": project_id, "references": references, "referenced_by": referenced_by}

    def _other_projects(self, project_id: str) -> list[Project]:
        """Every loadable project except ``project_id`` (unloadable ones are skipped, never written)."""
        projects: list[Project] = []
        for other_id in self.store.list_project_ids():
            if other_id == project_id:
                continue
            try:
                projects.append(self.store.load_project(other_id))
            except (OSError, ValueError):
                continue
        return projects

    def _load_link_target_project(self, project: Project, config: LinkConfiguration) -> str:
        """The project holding a configuration's target module; checks a cross-project one is referenced."""
        if config.target_project_id is None:
            return project.id
        if config.target_project_id not in self._effective_references(project):
            raise ValueError(
                f"Link configuration '{config.name}' targets project '{config.target_project_id}', "
                f"which project '{project.id}' no longer references"
            )
        if not self.store.project_exists(config.target_project_id):
            raise FileNotFoundError(f"Project '{config.target_project_id}' not found")
        check_permission(ACTION_PROJECT_READ, config.target_project_id)
        self.store.load_project(config.target_project_id)
        return config.target_project_id

    @staticmethod
    def _local_configs(project: Project) -> list[LinkConfiguration]:
        """The project's link configurations between its own modules (not cross-project ones)."""
        return [c for c in project.link_configurations if c.target_project_id is None]

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def add_attribute_definition(
        self,
        project_id: str,
        attr_id: str,
        name: str,
        type: str = "string",
        required: bool = False,
        default: Any = None,
        choices: list[str] | None = None,
        modules: list[str] | None = None,
        module_types: list[str] | None = None,
    ) -> Project:
        """Add an attribute definition, optionally scoped to some modules and/or module types.

        ``modules`` lists the module ids the attribute applies to and
        ``module_types`` the module types it applies to (every module of
        such a type); the attribute applies to a module named by either.
        ``None`` or empty for both means every module (see
        ``_attribute_applies``). Only an optional attribute can be scoped
        (``_check_required_scope``).
        """
        project = self.store.load_project(project_id)
        definition = self._new_attribute_definition(project, attr_id, name, type, required, default, choices)
        definition.modules = self._checked_attribute_modules(project, modules)
        definition.module_types = self._checked_module_types(module_types)
        self._check_required_scope(
            definition.name, definition.required, [*definition.modules, *definition.module_types]
        )
        project.attributes.append(definition)
        self.store.save_project(project)
        return project

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def set_attribute_modules(
        self, project_id: str, attr_id: str, modules: Any, drop_values: Any = False, module_types: Any = None
    ) -> dict[str, Any]:
        """Change the modules and module types an attribute applies to (both empty: every module).

        The whole scope is replaced: ``module_types`` omitted (``None``)
        means no type scope, like an empty list. Values the attribute has in
        requirements of modules outside the new
        scope cannot stay: a non-blank value is refused (``ValueError``)
        unless ``drop_values`` is true, in which case it is removed and
        reported in ``dropped_values``. Blank values there are removed
        silently. System (link-mirror) attributes keep their own scope and
        the built-in ObjectText applies to every module. A required attribute
        applies to every module: only ``[]``/``None`` is accepted for it.
        """
        if not isinstance(drop_values, bool):
            raise ValueError("drop_values must be true or false")
        project = self.store.load_project(project_id)
        attr = next((a for a in project.attributes if a.id == attr_id), None)
        if attr is None:
            raise FileNotFoundError(f"Attribute '{attr_id}' not found in project '{project_id}'")
        if attr.system:
            raise ValueError(f"Attribute '{attr.name}' is system-managed; its modules cannot be changed")
        if attr.id in {a.id for a in BUILTIN_ATTRIBUTES}:
            raise ValueError(f"Attribute '{attr.name}' is built in and applies to every module")
        scope = self._checked_attribute_modules(project, modules)
        type_scope = self._checked_module_types(module_types)
        self._check_required_scope(attr.name, attr.required, [*scope, *type_scope])
        scoped = replace(attr, modules=scope, module_types=type_scope)

        outside: list[tuple[str, Requirement]] = []
        if scope or type_scope:
            for module in self.list_modules(project_id):
                module_id = module.id
                if self._attribute_applies(scoped, module_id, module.type):
                    continue
                outside.extend(
                    (module_id, r)
                    for r in self.store.load_all_requirements(project_id, module_id)
                    if attr_id in r.attributes
                )
        valued = [(m, r) for m, r in outside if not self._is_blank(r.attributes[attr_id])]
        # Values are removed from these modules' requirements: module edit locks apply.
        self._check_module_writes(project_id, [m for m, _ in outside])
        if valued and not drop_values:
            ids = [r.id for _, r in valued]
            shown = ", ".join(ids[:10]) + (f" and {len(ids) - 10} more" if len(ids) > 10 else "")
            raise ValueError(
                f"Attribute '{attr.name}' has values in {len(ids)} requirement(s) of modules it would "
                f"no longer apply to ({shown}); clear them first or drop them"
            )

        attr.modules = scope
        attr.module_types = type_scope
        for module_id, requirement in outside:
            del requirement.attributes[attr_id]
            self.store.save_requirement(project_id, module_id, requirement)
        self.store.save_project(project)
        return {
            "project": project,
            "dropped_values": [{"module_id": m, "requirement_id": r.id} for m, r in valued],
        }

    def _checked_attribute_modules(self, project: Project, modules: Any) -> list[str]:
        """``modules`` as a stored attribute scope: existing module ids in display order.

        ``None`` or an empty list means every module (stored as ``[]``).
        Duplicates collapse; an id naming no module of the project is refused.
        """
        if modules is None:
            return []
        if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
            raise ValueError("Attribute modules must be a list of module ids")
        order = self._module_order(project)
        unknown = sorted(set(modules) - set(order))
        if unknown:
            raise ValueError(f"Unknown module(s) for project '{project.id}': {unknown}")
        wanted = set(modules)
        return [module_id for module_id in order if module_id in wanted]

    @staticmethod
    def _checked_module_types(module_types: Any) -> list[str]:
        """``module_types`` as a stored attribute type scope, in ``MODULE_TYPES`` order.

        ``None`` or an empty list means no type scope (stored as ``[]``).
        Duplicates collapse; an unknown type is refused.
        """
        if module_types is None:
            return []
        if not isinstance(module_types, list) or not all(isinstance(t, str) for t in module_types):
            raise ValueError("Attribute module_types must be a list of module types")
        unknown = sorted(set(module_types) - set(MODULE_TYPES))
        if unknown:
            raise ValueError(f"Unknown module type(s) {unknown}; expected some of {', '.join(MODULE_TYPES)}")
        return [t for t in MODULE_TYPES if t in module_types]

    @staticmethod
    def _checked_module_type(module_type: Any) -> str:
        """``module_type`` if it is one of ``MODULE_TYPES`` (``None``: the default ``requirements``)."""
        if module_type is None:
            return DEFAULT_MODULE_TYPE
        if not isinstance(module_type, str) or module_type not in MODULE_TYPES:
            raise ValueError(f"Unknown module type {module_type!r}; expected one of {', '.join(MODULE_TYPES)}")
        return module_type

    @staticmethod
    def _check_required_scope(name: Any, required: Any, scope: list[str]) -> None:
        """Refuse to limit a required attribute to some modules.

        A required attribute applies to every module; only optional
        attributes can be scoped. Shared by ``add_attribute_definition``,
        ``set_attribute_modules`` and templates (``_build_from_template``).
        """
        if required and scope:
            raise ValueError(
                f"Required attribute '{name}' applies to every module; "
                "only optional attributes can be limited to modules"
            )

    @staticmethod
    def _attribute_applies(attr: AttributeDefinition, module_id: str, module_type: str) -> bool:
        """Whether ``attr`` applies to requirements of module ``module_id`` of type ``module_type``.

        An attribute without a module list and without a module type list
        applies everywhere. Otherwise it applies to the modules it lists and
        to every module of a type it lists. Ids or types in the lists that
        name nothing (hand-edited data) are harmless. A required attribute
        applies everywhere even if a hand-edited file gives it a scope: the
        API never lets one be scoped, and the stored lists are kept (not
        rewritten) until the scope is reset.
        """
        if attr.required or (not attr.modules and not attr.module_types):
            return True
        return module_id in attr.modules or module_type in attr.module_types

    def _module_type(self, project_id: str, module_id: str) -> str:
        """The type of an existing module (``FileNotFoundError`` if it does not exist)."""
        return self.store.load_module(project_id, module_id).type

    @classmethod
    def _applicable_values(
        cls, project: Project, module: Module, attributes: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """``attributes`` without the values of attributes that do not apply to ``module``.

        Returns the kept values and the dropped attribute ids (in project
        attribute order). Unknown keys are kept. Used by the exports; moving
        or copying a requirement to another module must use it too.
        """
        excluded = [
            a.id
            for a in project.attributes
            if a.id in attributes and not cls._attribute_applies(a, module.id, module.type)
        ]
        if not excluded:
            return attributes, []
        return {k: v for k, v in attributes.items() if k not in excluded}, excluded

    def _new_attribute_definition(
        self,
        project: Project,
        attr_id: str,
        name: Any,
        type: Any,
        required: bool,
        default: Any,
        choices: Any,
    ) -> AttributeDefinition:
        """A validated new attribute definition for ``project`` (not yet added or saved).

        The one rule set for attribute definitions: ``add_attribute_definition``
        and templates (create from / save as) both go through it.
        """
        if any(a.id == attr_id for a in project.attributes):
            raise ValueError(f"Attribute '{attr_id}' already defined on project '{project.id}'")
        name = self._checked_attribute_name(project, name)
        if type not in ATTRIBUTE_TYPES:
            raise ValueError(f"Unknown attribute type '{type}'; expected one of {', '.join(ATTRIBUTE_TYPES)}")
        if type == "text" and isinstance(default, str):
            default = richtext.sanitize(default)
        definition = AttributeDefinition(
            id=attr_id, name=name, type=type, required=required, default=default, choices=choices
        )
        self._check_choices_and_default(definition)
        return definition

    @classmethod
    def _check_choices_and_default(cls, attr: AttributeDefinition) -> None:
        """Raise if ``attr``'s choices or default do not fit its type.

        An enum needs a non-empty list of string choices; other types take no
        choices (``None``). The default must be a valid value for the type
        (``None`` means no default), so it can never be merged into a new
        requirement as an invalid value. A required attribute's default must
        not be blank, or no requirement could ever omit the attribute.
        """
        if attr.type == "enum":
            if (
                not isinstance(attr.choices, list)
                or not attr.choices
                or not all(isinstance(c, str) for c in attr.choices)
            ):
                raise ValueError(f"Enum attribute '{attr.name}' needs a non-empty list of choices")
        elif attr.choices is not None:
            raise ValueError(f"Attribute '{attr.name}' of type '{attr.type}' cannot have choices")
        problem = cls._value_problem(attr, attr.default)
        if problem is not None:
            raise ValueError(f"Invalid default value: {problem}")
        if attr.required and attr.default is not None and cls._is_blank(attr.default):
            raise ValueError(f"Attribute '{attr.name}' is required, so its default must not be empty")

    @staticmethod
    def _checked_attribute_name(project: Project, name: Any, kind: str = "Attribute") -> str:
        """Return ``name`` stripped, or raise if it cannot head its own export column.

        CSV/XLSX exports head each attribute column with the attribute's name,
        after the reserved ``Id``/``Module``/``Parent`` columns, and the CSV
        import matches columns by name. A reserved or duplicate name (compared
        case-insensitively) would make a project's own export impossible to
        re-import. Link configuration names are checked too, because each link
        has a mirror attribute with the same name. ``kind`` ("Attribute" or
        "Link") starts the error message. Existing project data is never
        rewritten by this check.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{kind} name must not be empty")
        stripped = name.strip()
        folded = stripped.casefold()
        if folded in {column.casefold() for column in RESERVED_COLUMNS}:
            raise ValueError(
                f"{kind} name '{stripped}' is reserved: {', '.join(RESERVED_COLUMNS)} are "
                "export/import columns"
            )
        for attr in project.attributes:
            if attr.name.strip().casefold() == folded:
                raise ValueError(f"{kind} name '{stripped}' is already used by attribute '{attr.id}'")
        return stripped

    # --- row styles ----------------------------------------------------------------

    @_records_history
    def set_row_styles(self, project_id: str, row_styles: Any) -> Project:
        """Replace the project's row styling rules (None or ``[]`` clears them).

        Each rule styles the rows whose enum attribute ``attribute_id`` has
        ``value``: ``font_color``/``background_color`` (``#rrggbb``, stored
        lower-case, or null) and ``bold``. Validated by ``_checked_row_styles``;
        an invalid list raises ``ValueError`` and nothing is written.
        """
        project = self.store.load_project(project_id)
        project.row_styles = self._checked_row_styles(project, row_styles)
        self.store.save_project(project)
        return project

    @staticmethod
    def _checked_row_styles(project: Project, row_styles: Any) -> list[RowStyle]:
        """``row_styles`` as stored rules for ``project``, in the order given.

        A list of at most ``MAX_ROW_STYLES`` objects with exactly the keys of
        ``ROW_STYLE_KEYS`` (``font_color``, ``background_color`` and ``bold``
        may be omitted). The attribute must be a (non-system) enum attribute
        of the project and the value one of its choices; an (attribute,
        value) pair may appear once, and a rule must set at least one of
        font color, background color or bold. Rules are checked only when
        written: attributes and choices cannot be removed or renamed through
        the API, and a rule that hand-edited data no longer resolves is
        ignored where styles are applied (``_effective_row_styles``).
        """
        if row_styles is None:
            return []
        if not isinstance(row_styles, list):
            raise ValueError("row_styles must be a list of row style rules")
        if len(row_styles) > MAX_ROW_STYLES:
            raise ValueError(f"A project can have at most {MAX_ROW_STYLES} row styles")
        attributes = {a.id: a for a in project.attributes}
        seen: set[tuple[str, str]] = set()
        result: list[RowStyle] = []
        for index, raw in enumerate(row_styles):
            where = f"row_styles[{index}]"
            if not isinstance(raw, dict):
                raise ValueError(f"{where}: must be an object")
            unknown = sorted(str(k) for k in raw if k not in ROW_STYLE_KEYS)
            if unknown:
                raise ValueError(f"{where}: unknown key(s) {unknown}")
            attr_id = raw.get("attribute_id")
            attr = attributes.get(attr_id) if isinstance(attr_id, str) else None
            if attr is None:
                raise ValueError(f"{where}: unknown attribute {attr_id!r}")
            if attr.type != "enum" or attr.system:
                raise ValueError(f"{where}: attribute '{attr.name}' is not an enum attribute")
            value = raw.get("value")
            if not isinstance(value, str) or value not in (attr.choices or []):
                raise ValueError(f"{where}: {value!r} is not a choice of attribute '{attr.name}'")
            if (attr.id, value) in seen:
                raise ValueError(f"{where}: attribute '{attr.name}' value '{value}' already has a row style")
            seen.add((attr.id, value))
            colors: dict[str, str | None] = {}
            for key in ("font_color", "background_color"):
                color = raw.get(key)
                if color is not None and not (isinstance(color, str) and _HEX_COLOR.match(color)):
                    raise ValueError(f"{where}: '{key}' must be a color '#rrggbb' or null")
                colors[key] = color.lower() if color is not None else None
            bold = raw.get("bold", False)
            if not isinstance(bold, bool):
                raise ValueError(f"{where}: 'bold' must be true or false")
            if colors["font_color"] is None and colors["background_color"] is None and not bold:
                raise ValueError(
                    f"{where}: attribute '{attr.name}' value '{value}' sets no font color, background color or bold"
                )
            result.append(RowStyle(attribute_id=attr.id, value=value, bold=bold, **colors))
        return result

    @staticmethod
    def _effective_row_styles(project: Project) -> list[RowStyle]:
        """The project's rules that still resolve: enum attribute, value among its choices.

        Hand-edited data can leave a rule naming a missing attribute or
        choice; such a rule is ignored (not rewritten). The first rule for
        an (attribute, value) pair wins.
        """
        attributes = {a.id: a for a in project.attributes}
        seen: set[tuple[str, str]] = set()
        result: list[RowStyle] = []
        for style in project.row_styles:
            attr = attributes.get(style.attribute_id)
            key = (style.attribute_id, style.value)
            if attr is None or attr.type != "enum" or attr.system or key in seen:
                continue
            if style.value not in (attr.choices or []):
                continue
            if style.font_color is None and style.background_color is None and not style.bold:
                continue
            seen.add(key)
            result.append(style)
        return result

    # --- modules ----------------------------------------------------------------

    @_records_history
    def create_module(
        self,
        project_id: str,
        name: str,
        prefix: str,
        description: str = "",
        type: Any = None,
        folder_id: Any = None,
    ) -> Module:
        """Create a module, appended at the end of the top level or of folder ``folder_id``.

        ``type`` is one of ``MODULE_TYPES`` (``None``: ``requirements``); it
        is chosen at creation and cannot be changed later, so the link
        configurations checked against it stay valid. ``folder_id`` names a
        module folder of the project (``None``: the top level); it is
        checked before anything is written.

        The module file is written first, then project.json with the new
        tree. If the second write fails, the module is still listed: an
        existing module missing from the tree is appended in id order.
        """
        project = self.store.load_project(project_id)
        validate_prefix(prefix)
        module_type = self._checked_module_type(type)
        tree = self._module_tree(project)
        container = self._folder_container(project, tree, folder_id)
        module_id = _unique_slug(
            name,
            lambda c: self.store.module_exists(project_id, c),
            fallback="module",
            max_length=self.store.max_name_length(),
        )
        module = Module(id=module_id, name=name, prefix=prefix, description=description, type=module_type)
        self.store.save_module(project_id, module)
        container.append(module_id)
        project.module_order = tree
        self.store.save_project(project)
        return module

    def get_module(self, project_id: str, module_id: str) -> Module:
        return self.store.load_module(project_id, module_id)

    def _module_tree(self, project: Project) -> list:
        """The project's module tree as displayed (``module_tree.effective_tree``).

        ``project.module_order`` is applied tolerantly, so hand-edited or
        older data always lists every module exactly once: entries naming a
        module that does not exist (or repeating one) are skipped, and
        modules the tree does not mention follow at the top level in id
        order. A project without a stored tree (older data) lists its
        modules in id order. The stored value is not rewritten here; the
        result holds new folder objects, safe to modify.
        """
        return module_tree.effective_tree(project.module_order, self.store.list_module_ids(project.id))

    def _module_order(self, project: Project) -> list[str]:
        """The project's module ids in display order: the tree depth-first (see ``_module_tree``)."""
        return module_tree.module_ids(self._module_tree(project))

    def list_modules(self, project_id: str) -> list[Module]:
        """The project's modules in display order (see ``_module_order``)."""
        if not self.store.project_exists(project_id):
            return []
        project = self.store.load_project(project_id)
        return [self.store.load_module(project_id, mid) for mid in self._module_order(project)]

    def get_module_tree(self, project_id: str) -> list[dict[str, Any]]:
        """The project's module tree as displayed, in the ``project.json`` entry shape.

        ``[{"module": id} | {"folder": id, "name": ..., "children": [...]}]``:
        every module exactly once (see ``_module_tree``).
        """
        project = self.store.load_project(project_id)
        return [e.to_dict() if isinstance(e, ModuleFolder) else {"module": e} for e in self._module_tree(project)]

    def _store_module_tree(self, project: Project, tree: list) -> None:
        """Save ``tree`` as the project's module tree unless it equals the effective one (no write)."""
        if tree != self._module_tree(project):
            project.module_order = tree
            self.store.save_project(project)

    def _folder_container(self, project: Project, tree: list, folder_id: Any) -> list:
        """The entry list of folder ``folder_id`` in ``tree`` (``None``: the top level); 404 if unknown."""
        if folder_id is None:
            return tree
        if not isinstance(folder_id, str):
            raise ValueError("Folder id must be a string or null")
        folder = module_tree.find_folder(tree, folder_id)
        if folder is None:
            raise FileNotFoundError(f"Module folder '{folder_id}' not found in project '{project.id}'")
        return folder.children

    def _locate_folder(self, project: Project, tree: list, folder_id: Any) -> tuple[list, ModuleFolder, ModuleFolder | None]:
        """The list holding folder ``folder_id``, the folder and its parent folder (``None``: top level)."""
        if not isinstance(folder_id, str):
            raise ValueError("Folder id must be a string")
        found = module_tree.find_container(tree, "folder", folder_id)
        if found is None:
            raise FileNotFoundError(f"Module folder '{folder_id}' not found in project '{project.id}'")
        container, parent = found
        folder = container[module_tree.index_of(container, "folder", folder_id)]
        return container, folder, parent

    @staticmethod
    def _checked_folder_name(name: Any, siblings: list, folder_id: str | None = None) -> str:
        """``name`` stripped, if it is a valid name for a folder among ``siblings`` (excluding ``folder_id``).

        Not empty, at most ``MAX_MODULE_FOLDER_NAME_LENGTH`` characters, and
        not the name of a sibling folder (compared case-insensitively).
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Folder name must not be empty")
        name = name.strip()
        if len(name) > MAX_MODULE_FOLDER_NAME_LENGTH:
            raise ValueError(f"Folder name is too long: at most {MAX_MODULE_FOLDER_NAME_LENGTH} characters")
        for entry in siblings:
            if isinstance(entry, ModuleFolder) and entry.id != folder_id and entry.name.strip().casefold() == name.casefold():
                raise ValueError(f"A folder named '{entry.name}' already exists here")
        return name

    @staticmethod
    def _checked_entry_index(index: Any) -> int | None:
        if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index < 0):
            raise ValueError(f"Invalid index {index!r}: must be a non-negative integer")
        return index

    @staticmethod
    def _folder_summary(folder: ModuleFolder, parent: ModuleFolder | None) -> dict[str, Any]:
        """A folder as the folder operations return it."""
        return {"id": folder.id, "name": folder.name, "parent_id": parent.id if parent else None}

    @_records_history
    def create_module_folder(self, project_id: str, name: Any, parent_id: Any = None) -> dict[str, Any]:
        """Create an empty module folder at the end of the top level or of folder ``parent_id``.

        The id is allocated by the server: a slug of the name, suffixed on
        collision with any folder of the project. The name must be unique
        among the sibling folders; nesting is limited to
        ``MAX_MODULE_FOLDER_DEPTH`` levels. Returns ``{id, name, parent_id}``.
        """
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        container = self._folder_container(project, tree, parent_id)
        name = self._checked_folder_name(name, container)
        depth = module_tree.depth_of(tree, parent_id) + 1 if parent_id is not None else 1
        if depth > MAX_MODULE_FOLDER_DEPTH:
            raise ValueError(f"Module folders can be nested at most {MAX_MODULE_FOLDER_DEPTH} levels deep")
        taken = {folder.id for folder, _ in module_tree.iter_folders(tree)}
        folder = ModuleFolder(id=_unique_slug(name, lambda c: c in taken, fallback="folder"), name=name)
        container.append(folder)
        project.module_order = tree
        self.store.save_project(project)
        parent = module_tree.find_folder(tree, parent_id) if parent_id is not None else None
        return self._folder_summary(folder, parent)

    @_records_history
    def rename_module_folder(self, project_id: str, folder_id: Any, name: Any) -> dict[str, Any]:
        """Rename a module folder (its id stays). The new name must be unique among its sibling folders."""
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        container, folder, parent = self._locate_folder(project, tree, folder_id)
        folder.name = self._checked_folder_name(name, container, folder.id)
        self._store_module_tree(project, tree)
        return self._folder_summary(folder, parent)

    @_records_history
    def delete_module_folder(self, project_id: str, folder_id: Any) -> dict[str, Any]:
        """Delete an empty module folder. A folder holding modules or folders is refused (400)."""
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        container, folder, _ = self._locate_folder(project, tree, folder_id)
        if folder.children:
            raise ValueError(
                f"Module folder '{folder.name}' is not empty: move its modules and folders out first"
            )
        container.remove(folder)
        project.module_order = tree
        self.store.save_project(project)
        return {"id": folder.id, "deleted": True}

    @_records_history
    @_module_structure_only
    def move_module(self, project_id: str, module_id: str, folder_id: Any = None, index: Any = None) -> Module:
        """Move a module into folder ``folder_id`` (``None``: the top level).

        Without ``index`` it is appended after the folder's last entry; with
        ``index`` (a non-negative int) it is placed at that 0-based position
        among the folder's entries (modules and folders) after the move; an
        index past the end appends. Moving within the same folder with an
        index repositions it. Module ids, requirements and links are
        unaffected. A move that changes nothing writes nothing.
        """
        index = self._checked_entry_index(index)
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        tree = self._module_tree(project)
        target = self._folder_container(project, tree, folder_id)
        found = module_tree.find_container(tree, "module", module_id)
        if found is None:  # every existing module is in the effective tree
            raise FileNotFoundError(f"Module '{module_id}' not found in project '{project_id}'")
        source, _ = found
        source.remove(module_id)
        target.insert(len(target) if index is None else min(index, len(target)), module_id)
        self._store_module_tree(project, tree)
        return module

    @_records_history
    def move_module_folder(
        self, project_id: str, folder_id: Any, parent_id: Any = None, index: Any = None
    ) -> dict[str, Any]:
        """Move a folder (with everything in it) into folder ``parent_id`` (``None``: the top level).

        ``index`` works as for ``move_module``. A folder cannot be moved into
        itself or one of its subfolders, its name must be unique among the
        new sibling folders, and the result must not nest deeper than
        ``MAX_MODULE_FOLDER_DEPTH``. A move that changes nothing writes
        nothing.
        """
        index = self._checked_entry_index(index)
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        source, folder, _ = self._locate_folder(project, tree, folder_id)
        if parent_id is not None:
            if parent_id == folder.id:
                raise ValueError("A folder cannot be moved into itself")
            if module_tree.find_folder(folder.children, parent_id) is not None:
                raise ValueError("Cannot move a folder into one of its own subfolders")
        target = self._folder_container(project, tree, parent_id)
        self._checked_folder_name(folder.name, target, folder.id)
        parent_depth = module_tree.depth_of(tree, parent_id) if parent_id is not None else 0
        if parent_depth + module_tree.height(folder) > MAX_MODULE_FOLDER_DEPTH:
            raise ValueError(f"Module folders can be nested at most {MAX_MODULE_FOLDER_DEPTH} levels deep")
        source.remove(folder)
        target.insert(len(target) if index is None else min(index, len(target)), folder)
        self._store_module_tree(project, tree)
        parent = module_tree.find_folder(tree, parent_id) if parent_id is not None else None
        return self._folder_summary(folder, parent)

    @staticmethod
    def _swap_with_neighbor(container: list, index: int, direction: str) -> bool:
        neighbor_index = index - 1 if direction == "up" else index + 1
        if not 0 <= neighbor_index < len(container):
            return False
        container[index], container[neighbor_index] = container[neighbor_index], container[index]
        return True

    @_records_history
    @_module_structure_only
    def reorder_module(self, project_id: str, module_id: str, direction: str) -> Module:
        """Move a module one place up or down among the entries of its folder (or the top level).

        Its siblings are the modules and folders of the same folder. Moving
        the first entry up or the last one down changes nothing and writes
        nothing. Otherwise the whole effective tree is stored, which also
        drops entries naming missing modules.
        """
        if direction not in ("up", "down"):
            raise ValueError(f"Invalid direction '{direction}': must be 'up' or 'down'")
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        tree = self._module_tree(project)
        found = module_tree.find_container(tree, "module", module_id)
        if found is not None:
            container, _ = found
            if self._swap_with_neighbor(container, module_tree.index_of(container, "module", module_id), direction):
                project.module_order = tree
                self.store.save_project(project)
        return module

    @_records_history
    def reorder_module_folder(self, project_id: str, folder_id: Any, direction: str) -> dict[str, Any]:
        """Move a folder one place up or down among the entries of its parent folder (or the top level)."""
        if direction not in ("up", "down"):
            raise ValueError(f"Invalid direction '{direction}': must be 'up' or 'down'")
        project = self.store.load_project(project_id)
        tree = self._module_tree(project)
        container, folder, parent = self._locate_folder(project, tree, folder_id)
        if self._swap_with_neighbor(container, module_tree.index_of(container, "folder", folder.id), direction):
            project.module_order = tree
            self.store.save_project(project)
        return self._folder_summary(folder, parent)

    # --- requirements ----------------------------------------------------------------

    @staticmethod
    def _value_problem(attr: AttributeDefinition, value: Any) -> str | None:
        """Why ``value`` is not valid for ``attr``, or None. ``None`` (unset) is always valid."""
        if value is None:
            return None
        if attr.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                return f"{attr.name}: '{value}' is not an integer"
        elif attr.type == "boolean":
            if not isinstance(value, bool):
                return f"{attr.name}: '{value}' is not a boolean"
        elif attr.type == "enum":
            choices = list(attr.choices or [])
            if not isinstance(value, str) or value not in choices:
                return f"{attr.name}: '{value}' is not one of {choices}"
        elif attr.type in ("string", "text"):
            if not isinstance(value, str):
                return f"{attr.name}: expected a string, got {type(value).__name__}"
        elif attr.type == "date":
            if not ProjectService._is_iso_date(value):
                return f"{attr.name}: '{value}' is not a date (expected YYYY-MM-DD)"
        return None

    @staticmethod
    def _is_iso_date(value: Any) -> bool:
        """Whether ``value`` is an existing calendar date written ``YYYY-MM-DD``."""
        if not isinstance(value, str) or not _ISO_DATE.match(value):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True

    def _attribute_problems(
        self, project: Project, attributes: dict[str, Any], *, is_create: bool, module_id: str, module_type: str
    ) -> list[str]:
        """Every reason ``attributes`` cannot be written, in a stable order.

        The one validation path for requirement attributes: REST/WebSocket
        add and update (via ``_validate_attributes``) and the CSV import (one
        call per row) all use it. ``attributes`` must hold typed values; the
        CSV import parses text cells before calling this. ``module_id`` is the
        requirement's module (of type ``module_type``): an attribute that does
        not apply to it cannot be written, and is neither required nor
        defaulted there.
        """
        defined = {a.id: a for a in project.attributes}
        unknown = set(attributes) - set(defined)
        if unknown:
            return [f"Unknown attribute(s) for project '{project.id}': {sorted(unknown)}"]
        system_ids = {a.id for a in project.attributes if a.system} & set(attributes)
        if system_ids:
            return [f"Attribute(s) are system-managed and cannot be set directly: {sorted(system_ids)}"]
        not_applicable = [
            a.name
            for a in project.attributes
            if a.id in attributes and not self._attribute_applies(a, module_id, module_type)
        ]
        if not_applicable:
            return [f"Attribute(s) do not apply to module '{module_id}': {not_applicable}"]
        problems: list[str] = []
        invalid_ids: set[str] = set()
        for attr_id, value in attributes.items():
            problem = self._value_problem(defined[attr_id], value)
            if problem is not None:
                problems.append(problem)
                invalid_ids.add(attr_id)
        # A required attribute must end up with a non-blank value. On create,
        # an omitted attribute gets its (valid) default; an explicit blank
        # value overrides the default and so is rejected. On update, only the
        # attributes being written are checked.
        effective = self._merged_attributes(project, attributes, module_id, module_type) if is_create else attributes
        for attr in project.attributes:
            if not attr.required or attr.system or attr.id in invalid_ids:
                continue
            if not self._attribute_applies(attr, module_id, module_type):
                continue
            if (is_create or attr.id in attributes) and self._is_blank(effective.get(attr.id)):
                problems.append(f"required attribute '{attr.name}' is empty")
        return problems

    @staticmethod
    def _is_blank(value: Any) -> bool:
        """``None``, ``""`` and whitespace-only strings (rich text: its plain projection) count as no value."""
        return value is None or (isinstance(value, str) and not richtext.to_plain(value).strip())

    @staticmethod
    def _sanitized_text_values(project: Project, attributes: Any) -> Any:
        """``attributes`` with every string value of a ``text`` attribute sanitised.

        Rich text values (see ``richtext.py``) are reduced to the allowed
        XHTML subset (disallowed parts are stripped, never rejected); plain
        values are unchanged. Every write path that stores requirement values
        given by a client or copied from disk goes through this: add, update,
        copy (and the CSV import via ``richtext.from_plain``). Anything that
        is not a dict, unknown keys, system attributes and non-string values
        are returned unchanged for validation to judge.
        """
        if not isinstance(attributes, dict):
            return attributes
        text_ids = {a.id for a in project.attributes if a.type == "text" and not a.system}
        return {
            k: richtext.sanitize(v) if k in text_ids and isinstance(v, str) else v for k, v in attributes.items()
        }

    def _validate_attributes(
        self, project: Project, attributes: dict[str, Any], *, is_create: bool, module_id: str, module_type: str
    ) -> None:
        if not isinstance(attributes, dict):
            raise ValueError("Attributes must be an object")
        problems = self._attribute_problems(
            project, attributes, is_create=is_create, module_id=module_id, module_type=module_type
        )
        if problems:
            raise ValueError("; ".join(problems))

    @classmethod
    def _merged_attributes(
        cls, project: Project, attributes: dict[str, Any], module_id: str, module_type: str
    ) -> dict[str, Any]:
        """Attribute defaults overlaid with ``attributes``.

        System attributes, and attributes that do not apply to ``module_id``,
        never get a default. A stored default that is not
        valid for its attribute (legacy data written before defaults were
        checked) is skipped rather than rewritten, so it cannot block or
        corrupt new requirements. A ``text`` default is sanitised like a
        given value (a hand-edited project.json may hold unsanitised markup);
        one ``sanitize`` rejects (over the size limits) is skipped the same
        way. A default the client overrides is not looked at.
        """
        merged: dict[str, Any] = {}
        for a in project.attributes:
            if (
                a.id in attributes
                or a.default is None
                or a.system
                or not cls._attribute_applies(a, module_id, module_type)
                or cls._value_problem(a, a.default) is not None
            ):
                continue
            default = a.default
            if a.type == "text" and isinstance(default, str):
                try:
                    default = richtext.sanitize(default)
                except ValueError:
                    continue
            merged[a.id] = default
        merged.update(attributes)
        return merged

    def _write_new_requirements(
        self, project: Project, module_id: str, requirements: list[Requirement]
    ) -> None:
        """Write new requirement files, then the project (with its advanced id counters).

        If any write fails, the requirement files written so far are deleted
        (best effort) and the error is re-raised, so the module and the
        counters stay as they were. Should the process die between the two
        steps, ``_allocate_requirement_id`` skips ids already on disk, so an
        id is never reused.
        """
        written: list[str] = []
        try:
            for requirement in requirements:
                self.store.save_requirement(project.id, module_id, requirement)
                written.append(requirement.id)
            self.store.save_project(project)
        except BaseException:
            for requirement_id in written:
                try:
                    self.store.delete_requirement(project.id, module_id, requirement_id)
                except OSError:
                    pass
            raise

    @staticmethod
    def _next_sibling_order(requirements: list[Requirement], parent_id: str | None) -> int:
        """Order for a requirement appended after the last sibling under ``parent_id``.

        Orders are 0-based. Removing a requirement leaves a gap, so the count of
        siblings could collide with an existing order; use the highest one + 1.
        """
        return max((r.order for r in requirements if r.parent_id == parent_id), default=-1) + 1

    def _allocate_requirement_id(self, project: Project, module: Module) -> str:
        counter = project.id_counters.get(module.prefix, 0)
        while True:
            counter += 1
            candidate = f"{module.prefix}-{counter:04d}"
            if self.store.find_requirement_module(project.id, candidate) is None:
                project.id_counters[module.prefix] = counter
                return candidate

    @_records_history
    def add_requirement(
        self,
        project_id: str,
        module_id: str,
        attributes: dict[str, Any],
        parent_id: str | None = None,
    ) -> Requirement:
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)

        if parent_id is not None and not self.store.requirement_exists(project_id, module_id, parent_id):
            raise FileNotFoundError(f"Requirement '{parent_id}' not found in module '{module_id}'")

        attributes = self._sanitized_text_values(project, attributes)
        self._validate_attributes(project, attributes, is_create=True, module_id=module_id, module_type=module.type)
        merged = self._merged_attributes(project, attributes, module_id, module.type)
        if guard_active():
            status = self._status_attribute(project, module)
            if status is not None and merged.get(status.id) not in (None, ""):
                self._check_status_changes(project_id, module_id, [(None, None, merged[status.id])])

        requirement_id = self._allocate_requirement_id(project, module)
        siblings = self.store.load_all_requirements(project_id, module_id)
        order = self._next_sibling_order(siblings, parent_id)

        requirement = Requirement(id=requirement_id, attributes=merged, parent_id=parent_id, order=order)
        self._write_new_requirements(project, module_id, [requirement])
        return requirement

    def _descendant_ids(self, requirements: list[Requirement], requirement_id: str) -> set[str]:
        children_of: dict[str | None, list[str]] = {}
        for req in requirements:
            children_of.setdefault(req.parent_id, []).append(req.id)
        descendants: set[str] = set()
        stack = list(children_of.get(requirement_id, []))
        while stack:
            child_id = stack.pop()
            if child_id in descendants:
                continue
            descendants.add(child_id)
            stack.extend(children_of.get(child_id, []))
        return descendants

    @_records_history
    def move_requirement(
        self,
        project_id: str,
        module_id: str,
        requirement_id: str,
        parent_id: str | None,
        index: Any = None,
    ) -> Requirement:
        """Make ``requirement_id`` a child of ``parent_id`` (``None``: top level).

        Without ``index`` the requirement is appended after the new parent's
        last child and no other requirement is touched. With ``index`` (a
        non-negative int) it is placed at that 0-based position among the new
        parent's children after the move; an index past the end appends. The
        new parent's children are then renumbered ``0..n-1`` in their current
        order (ties by id), which also repairs duplicate or gapped orders. Moving
        within the same parent with an index repositions the requirement.
        """
        if index is not None and (isinstance(index, bool) or not isinstance(index, int) or index < 0):
            raise ValueError(f"Invalid index {index!r}: must be a non-negative integer")

        requirements = self.store.load_all_requirements(project_id, module_id)
        if not any(r.id == requirement_id for r in requirements):
            raise FileNotFoundError(f"Requirement '{requirement_id}' not found in module '{module_id}'")

        if parent_id is not None:
            if parent_id == requirement_id:
                raise ValueError("A requirement cannot be its own parent")
            if not any(r.id == parent_id for r in requirements):
                raise FileNotFoundError(f"Requirement '{parent_id}' not found in module '{module_id}'")
            if parent_id in self._descendant_ids(requirements, requirement_id):
                raise ValueError("Cannot move a requirement under one of its own descendants")

        requirement = self.store.load_requirement(project_id, module_id, requirement_id)
        requirement.parent_id = parent_id
        if index is None:
            requirement.order = self._next_sibling_order(
                [r for r in requirements if r.id != requirement_id], parent_id
            )
            self.store.save_requirement(project_id, module_id, requirement)
            return requirement

        siblings = sorted(
            (r for r in requirements if r.parent_id == parent_id and r.id != requirement_id),
            key=lambda r: (r.order, r.id),
        )
        siblings.insert(min(index, len(siblings)), requirement)
        for position, sibling in enumerate(siblings):
            if sibling is requirement or sibling.order != position:
                sibling.order = position
                self.store.save_requirement(project_id, module_id, sibling)
        return requirement

    @_records_history
    def reorder_requirement(
        self, project_id: str, module_id: str, requirement_id: str, direction: str
    ) -> Requirement:
        if direction not in ("up", "down"):
            raise ValueError(f"Invalid direction '{direction}': must be 'up' or 'down'")

        requirements = self.store.load_all_requirements(project_id, module_id)
        target = next((r for r in requirements if r.id == requirement_id), None)
        if target is None:
            raise FileNotFoundError(f"Requirement '{requirement_id}' not found in module '{module_id}'")

        siblings = sorted(
            (r for r in requirements if r.parent_id == target.parent_id), key=lambda r: r.order
        )
        index = next(i for i, r in enumerate(siblings) if r.id == requirement_id)
        neighbor_index = index - 1 if direction == "up" else index + 1

        if 0 <= neighbor_index < len(siblings):
            neighbor = siblings[neighbor_index]
            target.order, neighbor.order = neighbor.order, target.order
            self.store.save_requirement(project_id, module_id, target)
            self.store.save_requirement(project_id, module_id, neighbor)

        return self.store.load_requirement(project_id, module_id, requirement_id)

    @_records_history
    def copy_requirement(
        self, project_id: str, module_id: str, requirement_id: str, target_parent_id: str | None
    ) -> Requirement:
        requirements = self.store.load_all_requirements(project_id, module_id)
        if not any(r.id == requirement_id for r in requirements):
            raise FileNotFoundError(f"Requirement '{requirement_id}' not found in module '{module_id}'")

        if target_parent_id is not None:
            if not any(r.id == target_parent_id for r in requirements):
                raise FileNotFoundError(f"Requirement '{target_parent_id}' not found in module '{module_id}'")
            if target_parent_id == requirement_id or target_parent_id in self._descendant_ids(
                requirements, requirement_id
            ):
                raise ValueError("Cannot copy a requirement under itself or one of its own descendants")

        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        system_ids = {a.id for a in project.attributes if a.system}
        children_of: dict[str | None, list[Requirement]] = {}
        for r in requirements:
            children_of.setdefault(r.parent_id, []).append(r)

        created: list[Requirement] = []

        def duplicate(original: Requirement, new_parent_id: str | None) -> Requirement:
            attributes = self._sanitized_text_values(
                project, {k: v for k, v in original.attributes.items() if k not in system_ids}
            )
            new_id = self._allocate_requirement_id(project, module)
            order = self._next_sibling_order(requirements, new_parent_id)
            copy = Requirement(id=new_id, attributes=attributes, parent_id=new_parent_id, order=order)
            created.append(copy)
            requirements.append(copy)
            for child in sorted(children_of.get(original.id, []), key=lambda r: r.order):
                duplicate(child, new_id)
            return copy

        root_original = next(r for r in requirements if r.id == requirement_id)
        new_root = duplicate(root_original, target_parent_id)
        self._write_new_requirements(project, module_id, created)

        return self.store.load_requirement(project_id, module_id, new_root.id)

    @_records_history
    @_requires(ACTION_PROJECT_REVIEW)
    def update_requirement(
        self, project_id: str, module_id: str, requirement_id: str, attributes: dict[str, Any]
    ) -> Requirement:
        project = self.store.load_project(project_id)
        module_type = self._module_type(project_id, module_id)
        attributes = self._sanitized_text_values(project, attributes)
        self._validate_attributes(project, attributes, is_create=False, module_id=module_id, module_type=module_type)
        requirement = self.store.load_requirement(project_id, module_id, requirement_id)
        if guard_active():
            # Server edition: a reviewer may change only the status; the status guards apply to everyone.
            module = self.store.load_module(project_id, module_id)
            status = self._status_attribute(project, module)
            changed = {k for k, v in attributes.items() if requirement.attributes.get(k) != v}
            if changed - ({status.id} if status else set()):
                check_permission(ACTION_PROJECT_WRITE, project_id)
            if status is not None and status.id in changed:
                self._check_status_changes(
                    project_id, module_id, [(requirement_id, requirement.attributes.get(status.id), attributes[status.id])]
                )
        self._mark_rich_text_format(project, attributes)
        requirement.attributes.update(attributes)
        self.store.save_requirement(project_id, module_id, requirement)
        return requirement

    # --- requirement status guards (server edition) ------------------------------
    #
    # Only while a permission guard is active (``guard_active``): the local
    # edition never runs any of this. The status attribute is found by name
    # like a report attribute (``REPORT_ATTRIBUTE_ROLES["status"]``: the RBC
    # "Status" enum); its values are compared like attribute names
    # (``STATUS_STAGE_WORDS``). Per changed status:
    #
    # - to or from Approved: ``project.approve`` (project admin), and only
    #   from Reviewed (approval never skips the independent review);
    # - to Reviewed: ``project.review`` (reviewer or more) by someone other
    #   than the requirement's last content author (independence, see
    #   ``_require_independent_reviewer``); a new requirement cannot start
    #   as Reviewed;
    # - any other change: ``project.review``.

    def _status_attribute(self, project: Project, module: Module) -> AttributeDefinition | None:
        """The module's requirement status attribute (by name, like a report attribute), or ``None``."""
        return self._report_attribute(project, module, "status")

    @classmethod
    def _status_stage(cls, value: Any) -> str | None:
        """``reviewed`` / ``approved`` for a signed-off / approved status value, else ``None``."""
        key = cls._report_key(value) if isinstance(value, str) else ""
        for stage, words in STATUS_STAGE_WORDS.items():
            if key in words:
                return stage
        return None

    def _check_status_changes(
        self, project_id: str, module_id: str, changes: list[tuple[str | None, Any, Any]]
    ) -> None:
        """Refuse status changes the current identity may not make (``AccessDeniedError``, 403 / -32003).

        ``changes``: ``(requirement id or None for a new one, old value, new value)``.
        A ``ValueError`` (400) for approving a requirement that was not reviewed.
        """
        for requirement_id, old, new in changes:
            if old == new:
                continue
            old_stage, new_stage = self._status_stage(old), self._status_stage(new)
            label = requirement_id or "A new requirement"
            if "approved" in (old_stage, new_stage):
                check_permission(ACTION_PROJECT_APPROVE, project_id)
                if new_stage == "approved" and old_stage != "reviewed":
                    raise ValueError(
                        f"{label} cannot be approved: its status is {old or 'empty'}, and only a reviewed "
                        "requirement can be approved (independent review first)"
                    )
                continue
            check_permission(ACTION_PROJECT_REVIEW, project_id)
            if new_stage == "reviewed":
                if requirement_id is None:
                    raise AccessDeniedError(
                        f"A new requirement cannot start as {new}: its author cannot review it; "
                        "another reviewer signs it off after it was created"
                    )
                self._require_independent_reviewer(project_id, module_id, requirement_id, new)

    def _require_independent_reviewer(self, project_id: str, module_id: str, requirement_id: str, value: Any) -> None:
        """Refuse a sign-off by the requirement's last content author (``AccessDeniedError``).

        The last content author is the author of the newest commit in the
        requirement's git history (``requirement_history``: the
        ``created``/``modified`` computation) that created it or changed one
        of its attributes other than the status and the system (link mirror)
        attributes; moves and reorders do not count. The author is compared
        with the identity commits get (e-mail address, else name). Without a
        readable history, or with changes not committed yet, the last author
        is unknown and the sign-off is refused.
        """
        project_dir = self.store.project_path(project_id)
        project = self.store.load_project(project_id)
        status = self._status_attribute(project, self.store.load_module(project_id, module_id))
        history = self.history.requirement_history(
            project_dir, self.store.requirement_relative_path(module_id, requirement_id), requirement_id
        )
        if not history.get("available"):
            raise AccessDeniedError(
                f"{requirement_id} cannot be marked {value}: independent review needs the project history "
                f"(git) to know who changed it last ({history.get('reason') or 'no history'})"
            )
        if history.get("uncommitted"):
            raise AccessDeniedError(
                f"{requirement_id} cannot be marked {value}: it has changes that are not in the project "
                "history yet, so its last author is unknown"
            )
        # Not content: the status itself, derived attributes (link mirrors), the place in the tree.
        not_content = {a.id for a in project.attributes if a.system} | ({status.id} if status else set())
        author = None
        for entry in history.get("entries") or []:
            content = (
                entry.get("change") != "modified"
                or entry.get("unreadable")
                or any(d.get("kind") == "attribute" and d.get("attribute_id") not in not_content for d in entry.get("diffs") or [])
            )
            if content:
                author = entry
                break
        if author is None:
            raise AccessDeniedError(
                f"{requirement_id} cannot be marked {value}: the project history does not show who wrote it"
            )
        name, email = self.history.user_identity(project_dir)
        author_name, author_email = str(author.get("author_name") or ""), str(author.get("author_email") or "")
        if email and author_email:
            same = email.strip().lower() == author_email.strip().lower()
        else:
            same = bool(name) and name.strip() == author_name.strip()
        if same:
            raise AccessDeniedError(
                f"{requirement_id} cannot be marked {value} by you: you made its last change "
                f"(commit {str(author.get('commit') or '')[:8]}); independent review needs another reviewer"
            )

    def _mark_rich_text_format(self, project: Project, values: dict[str, Any]) -> None:
        """Rewrite project.json at the current format if ``values`` store rich text in an older-format project.

        Rich text needs project format 4 (an older ReqManTool would show it as
        raw markup), but loading upgrades only in memory. Add, copy and
        import always save the project (its id counters); a write that
        does not, such as an update, calls this before writing the values,
        within the same history commit. A plain value leaves the stored
        version as it is.
        """
        text_ids = {a.id for a in project.attributes if a.type == "text" and not a.system}
        if not any(k in text_ids and richtext.is_rich(v) for k, v in values.items()):
            return
        if self.store.stored_project_format_version(project.id) < PROJECT_FORMAT.current:
            self.store.save_project(project)

    def get_requirement(self, project_id: str, module_id: str, requirement_id: str) -> Requirement:
        return self.store.load_requirement(project_id, module_id, requirement_id)

    def list_requirements(self, project_id: str, module_id: str) -> list[Requirement]:
        return self.store.load_all_requirements(project_id, module_id)

    def find_requirement(self, project_id: str, requirement_id: str) -> tuple[str, Requirement]:
        """Locate a requirement anywhere in a project, without knowing its module."""
        module_id = self.store.find_requirement_module(project_id, requirement_id)
        if module_id is None:
            raise FileNotFoundError(f"Requirement '{requirement_id}' not found in project '{project_id}'")
        return module_id, self.store.load_requirement(project_id, module_id, requirement_id)

    def _cleanup_links_for_requirement(self, project_id: str, requirement_id: str) -> None:
        """Remove the project's own link pairs with ``requirement_id`` at either end; refresh the mirrors.

        A cross-project configuration's targets live in another project, so
        only its sources can be ``requirement_id`` here (a target with the
        same id is another project's requirement).
        """
        project = self.store.load_project(project_id)
        for config in project.link_configurations:
            cross = config.target_project_id is not None
            pairs = self.store.load_link_pairs(project_id, config.id)
            remaining = []
            affected: list[str] = []
            for pair in pairs:
                if pair.source_id == requirement_id or (not cross and pair.target_id == requirement_id):
                    other_id = pair.target_id if pair.source_id == requirement_id else pair.source_id
                    affected.append(other_id)
                    continue
                remaining.append(pair)
            if len(remaining) != len(pairs):
                self.store.save_link_pairs(project_id, config.id, remaining)
                if cross:
                    continue  # the other ends are in the target project, which stores no mirror
                for other_id in affected:
                    other_module = self.store.find_requirement_module(project_id, other_id)
                    if other_module is not None:
                        self._refresh_link_attribute(project_id, config.id, other_module, other_id, config)

    def _incoming_link_cleanup(
        self, project_id: str, requirement_ids: set[str]
    ) -> list[tuple[Project, LinkConfiguration, list[LinkPair]]]:
        """The cross-project link pairs of other projects that target ``requirement_ids`` of ``project_id``.

        Each as (referencing project, its configuration, all its pairs).
        Projects that cannot be loaded are skipped (never written).
        """
        affected: list[tuple[Project, LinkConfiguration, list[LinkPair]]] = []
        if not requirement_ids:
            return affected
        for other in self._other_projects(project_id):
            for config in other.link_configurations:
                if config.target_project_id != project_id:
                    continue
                pairs = self.store.load_link_pairs(other.id, config.id)
                if any(p.target_id in requirement_ids for p in pairs):
                    affected.append((other, config, pairs))
        return affected

    def _check_incoming_cleanup(self, affected: list[tuple[Project, LinkConfiguration, list[LinkPair]]]) -> None:
        """Refuse before anything is written if a referencing project to clean up cannot be changed.

        A locked referencing project refuses the whole operation (423): its
        links to the requirements being removed would have to be deleted,
        and a locked project is never written. A git sync conflict there is
        409. A link belongs to its configuration's source module, so that
        module's edit lock there applies too (``ModuleLockedError``, 423).
        """
        others = {other.id: other for other, _, _ in affected}
        source_modules: dict[str, list[str]] = {}
        for other, config, _ in affected:
            source_modules.setdefault(other.id, []).append(config.source_module_id)
        for other_id in sorted(others):
            other = others[other_id]
            if guard_active() and not is_permitted(ACTION_PROJECT_WRITE, other_id):
                raise AccessDeniedError(
                    f"Project '{other_id}' links to the requirement(s) being removed, and you may not "
                    "change it, so those links cannot be deleted there. Ask one of its editors to remove "
                    "the links first; nothing was changed."
                )
            if other.locked is not None:
                raise ProjectLockedError(
                    f"{self._locked_message(other)} It links to the requirement(s) being removed, and those "
                    "links would have to be deleted there. Unlock it, or remove the links first; nothing "
                    "was changed."
                )
            self._require_no_sync_conflict(other_id)
            self._check_module_writes(other_id, source_modules[other_id])

    def _apply_incoming_cleanup(
        self, affected: list[tuple[Project, LinkConfiguration, list[LinkPair]]], requirement_ids: set[str]
    ) -> None:
        """Delete the pairs targeting ``requirement_ids`` in the referencing projects; refresh their source mirrors.

        This writes into the referencing (source) projects, whose
        repositories commit it like any change of theirs; the target
        project never stores anything about links pointing at it.
        """
        for other, config, pairs in affected:
            remaining = [p for p in pairs if p.target_id not in requirement_ids]
            self.store.save_link_pairs(other.id, config.id, remaining)
            for source_id in sorted({p.source_id for p in pairs if p.target_id in requirement_ids}):
                module_id = self.store.find_requirement_module(other.id, source_id)
                if module_id is not None:
                    self._refresh_link_attribute(other.id, config.id, module_id, source_id, config)

    @_records_history
    def remove_requirement(self, project_id: str, module_id: str, requirement_id: str) -> None:
        """Remove a requirement: children are promoted, its links are deleted.

        Links to it from other projects (cross-project link configurations
        of projects referencing this one) are deleted too, in those
        projects, so no project keeps a link to a requirement that no longer
        exists.
        """
        removed = self.store.load_requirement(project_id, module_id, requirement_id)
        incoming = self._incoming_link_cleanup(project_id, {requirement_id})
        self._check_incoming_cleanup(incoming)
        for other in self.store.load_all_requirements(project_id, module_id):
            if other.parent_id == requirement_id:
                other.parent_id = removed.parent_id
                self.store.save_requirement(project_id, module_id, other)

        self._cleanup_links_for_requirement(project_id, requirement_id)
        self._apply_incoming_cleanup(incoming, {requirement_id})
        self.store.delete_requirement(project_id, module_id, requirement_id)

    @_records_history
    def purge_module(self, project_id: str, module_id: str) -> int:
        """Delete every requirement of a module (and its links); the number deleted.

        A missing project is not found and a stub (unloadable project) is
        refused, even when there is nothing to delete. A module that does not
        exist in a loadable project has nothing to purge: 0.
        """
        self.store.load_project(project_id)
        return self._purge_module(project_id, module_id)

    def _purge_module(self, project_id: str, module_id: str) -> int:
        """Delete a module's requirements, their links and the links to them from referencing projects."""
        ids = set(self.store.list_requirement_ids(project_id, module_id))
        incoming = self._incoming_link_cleanup(project_id, ids)
        self._check_incoming_cleanup(incoming)
        for requirement_id in ids:
            self._cleanup_links_for_requirement(project_id, requirement_id)
            self.store.delete_requirement(project_id, module_id, requirement_id)
        self._apply_incoming_cleanup(incoming, ids)
        return len(ids)

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def purge_project(self, project_id: str) -> int:
        # A missing project is not found and a stub (unloadable project) is
        # refused, even when there is nothing to delete.
        self.store.load_project(project_id)
        module_ids = self.store.list_module_ids(project_id)
        self._check_module_writes(project_id, module_ids)
        # Every referencing project to clean up is checked before anything is deleted.
        all_ids = {rid for mid in module_ids for rid in self.store.list_requirement_ids(project_id, mid)}
        self._check_incoming_cleanup(self._incoming_link_cleanup(project_id, all_ids))
        total = 0
        for module_id in module_ids:
            total += self._purge_module(project_id, module_id)
        audit(AUDIT_PROJECT_PURGE, project_id, requirements=total)
        return total

    # --- link configurations ("link modules") ----------------------------------

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def create_link_configuration(
        self,
        project_id: str,
        name: str,
        source_module_id: str,
        target_module_id: str,
        target_project_id: Any = None,
    ) -> LinkConfiguration:
        """Create a link configuration from a module of the project to a module of it or of a referenced project.

        ``target_project_id`` (``None`` or the project itself: a module of
        this project) must be one of the projects this project references
        (its parent or ``references``) for a cross-project configuration.
        The configuration, its link pairs and its mirror attribute all live
        in this (the source) project; the target project is not written.
        """
        project = self.store.load_project(project_id)
        if target_project_id == project_id:
            target_project_id = None
        if target_project_id is not None:
            if not isinstance(target_project_id, str):
                raise ValueError("target_project_id must be a project id or null")
            if target_project_id not in self._effective_references(project):
                raise ValueError(
                    f"Project '{project_id}' does not reference project '{target_project_id}'; "
                    "add it to the project references first"
                )
            if not self.store.project_exists(target_project_id):
                raise FileNotFoundError(f"Project '{target_project_id}' not found")
            check_permission(ACTION_PROJECT_READ, target_project_id)
            self.store.load_project(target_project_id)
        target_owner = target_project_id or project_id
        if not self.store.module_exists(project_id, source_module_id):
            raise FileNotFoundError(f"Module '{source_module_id}' not found in project '{project_id}'")
        if not self.store.module_exists(target_owner, target_module_id):
            raise FileNotFoundError(f"Module '{target_module_id}' not found in project '{target_owner}'")
        config = self._add_link_configuration(
            project,
            name,
            self.store.load_module(project_id, source_module_id),
            self.store.load_module(target_owner, target_module_id),
            target_project_id,
        )
        self.store.save_project(project)
        self.store.save_link_pairs(project_id, config.id, [])
        return config

    def _add_link_configuration(
        self,
        project: Project,
        name: Any,
        source: Module,
        target: Module,
        target_project_id: str | None = None,
    ) -> LinkConfiguration:
        """Add a link configuration and its system mirror attribute to ``project`` in memory.

        The caller passes the two (existing) modules and saves the project.
        The modules' types must form one of ``LINK_TYPE_PAIRS``
        (``_check_link_types``).
        """
        # The link's mirror attribute heads its own export column, so the link
        # name follows the attribute name rule.
        name = self._checked_attribute_name(project, name, kind="Link")
        self._check_link_types(source, target)
        source_module_id, target_module_id = source.id, target.id

        taken_ids = {c.id for c in project.link_configurations} | {a.id for a in project.attributes}
        config_id = _unique_slug(name, lambda c: c in taken_ids, fallback="link")

        config = LinkConfiguration(
            id=config_id,
            name=name,
            source_module_id=source_module_id,
            target_module_id=target_module_id,
            target_project_id=target_project_id,
        )
        project.link_configurations.append(config)
        project.attributes.append(
            AttributeDefinition(id=config_id, name=name, type="text", required=False, system=True)
        )
        return config

    @staticmethod
    def _check_link_types(source: Module, target: Module) -> None:
        """Refuse a link configuration between module types that do not trace to each other.

        Allowed (source → target): requirements → requirements, test cases →
        requirements, test execution → test cases (``LINK_TYPE_PAIRS``).
        Only new configurations are checked: one already stored (hand-edited
        data) keeps loading and working.
        """
        if (source.type, target.type) in LINK_TYPE_PAIRS:
            return
        allowed = ", ".join(f"{s} → {t}" for s, t in LINK_TYPE_PAIRS)
        raise ValueError(
            f"A link from module '{source.name}' ({source.type}) to module '{target.name}' ({target.type}) "
            f"is not allowed; link configurations join {allowed}"
        )

    def list_link_configurations(self, project_id: str) -> list[LinkConfiguration]:
        return self.store.load_project(project_id).link_configurations

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def remove_link_configuration(self, project_id: str, config_id: str) -> None:
        project = self.store.load_project(project_id)
        if not any(c.id == config_id for c in project.link_configurations):
            raise FileNotFoundError(f"Link configuration '{config_id}' not found in project '{project_id}'")
        # Removing a configuration deletes all its links: its source module's lock applies.
        self._check_module_writes(project_id, [self._get_link_configuration(project, config_id).source_module_id])
        project.link_configurations = [c for c in project.link_configurations if c.id != config_id]
        project.attributes = [a for a in project.attributes if a.id != config_id]
        self.store.save_project(project)
        self.store.delete_link_pairs(project_id, config_id)

        for module_id in self.store.list_module_ids(project_id):
            for requirement in self.store.load_all_requirements(project_id, module_id):
                if config_id in requirement.attributes:
                    del requirement.attributes[config_id]
                    self.store.save_requirement(project_id, module_id, requirement)

    # --- views ----------------------------------------------------------------

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def create_view(
        self,
        project_id: str,
        name: str,
        attribute_ids: list[str],
        column_widths: Any = None,
        single_line: Any = None,
        modules: Any = None,
    ) -> View:
        project = self.store.load_project(project_id)
        scope = self._checked_view_modules(project, modules)
        view = self._add_view(project, name, self._checked_view_attribute_ids(project, attribute_ids))
        view.column_widths = self._checked_column_widths(column_widths, view)
        view.single_line = self._checked_single_line(single_line, view)
        view.modules = scope
        self.store.save_project(project)
        return view

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def set_view_column_widths(self, project_id: str, view_id: str, column_widths: Any) -> View:
        """Store (or, with None or an empty mapping, clear) a view's shared column widths."""
        project = self.store.load_project(project_id)
        view = self._get_view(project, view_id)
        view.column_widths = self._checked_column_widths(column_widths, view)
        self.store.save_project(project)
        return view

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def update_view(self, project_id: str, view_id: str, changes: Any) -> View:
        """Change any of a view's ``name``, ``attribute_ids``, ``column_widths``, ``single_line`` and ``modules``.

        ``changes`` maps field names (``VIEW_UPDATE_FIELDS``, at least one) to
        new values; fields not given keep their value. ``attribute_ids`` is
        the ordered column list (the table's column order): defined
        attributes, no duplicates. ``column_widths`` and ``single_line`` may
        be None (or empty) to clear them; they must name the view's columns
        (``VIEW_ID_COLUMN`` or one of the new ``attribute_ids``). When only
        ``attribute_ids`` changes, the stored widths and single-line columns
        of removed columns are dropped and the rest follow the new order.
        ``modules`` is the view's module scope (module ids; None or empty:
        every module). Everything is validated before anything is written.
        """
        if not isinstance(changes, dict):
            raise ValueError("View changes must be an object")
        unknown = [key for key in changes if key not in VIEW_UPDATE_FIELDS]
        if unknown:
            raise ValueError(
                f"Unknown view field(s) {sorted(map(str, unknown))}; expected any of {list(VIEW_UPDATE_FIELDS)}"
            )
        if not changes:
            raise ValueError(f"Nothing to update: give at least one of {list(VIEW_UPDATE_FIELDS)}")
        project = self.store.load_project(project_id)
        view = self._get_view(project, view_id)
        name = self._checked_view_name(project, changes["name"], view.id) if "name" in changes else view.name
        attribute_ids = (
            self._checked_view_attribute_ids(project, changes["attribute_ids"])
            if "attribute_ids" in changes
            else list(view.attribute_ids)
        )
        # New values are validated against the new columns; kept values are
        # pruned to them (never rejected: they may be hand-edited) and reordered.
        updated = View(id=view.id, name=name, attribute_ids=attribute_ids)
        order = list(dict.fromkeys((VIEW_ID_COLUMN, *attribute_ids)))
        if "column_widths" in changes:
            column_widths = self._checked_column_widths(changes["column_widths"], updated)
        else:
            stored = view.column_widths or {}
            column_widths = {
                k: stored[k] for k in order if k in stored and MIN_COLUMN_WIDTH <= stored[k] <= MAX_COLUMN_WIDTH
            } or None
        if "single_line" in changes:
            single_line = self._checked_single_line(changes["single_line"], updated)
        else:
            single_line = [k for k in order if k in (view.single_line or [])] or None
        modules = self._checked_view_modules(project, changes["modules"]) if "modules" in changes else view.modules
        view.name = name
        view.modules = modules
        view.attribute_ids = attribute_ids
        view.column_widths = column_widths
        view.single_line = single_line
        self.store.save_project(project)
        return view

    def _checked_view_modules(self, project: Project, modules: Any) -> list[str]:
        """``modules`` as a stored view scope: existing module ids in display order ([]: every module)."""
        if modules is None:
            return []
        if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
            raise ValueError("View modules must be a list of module ids")
        order = self._module_order(project)
        unknown = sorted(set(modules) - set(order))
        if unknown:
            raise ValueError(f"Unknown module(s) for project '{project.id}': {unknown}")
        return [module_id for module_id in order if module_id in set(modules)]

    @staticmethod
    def _get_view(project: Project, view_id: str) -> View:
        view = next((v for v in project.views if v.id == view_id), None)
        if view is None:
            raise FileNotFoundError(f"View '{view_id}' not found in project '{project.id}'")
        return view

    @staticmethod
    def _checked_view_attribute_ids(project: Project, attribute_ids: Any) -> list[str]:
        """A view's ordered attribute columns: a list of defined attribute ids without duplicates."""
        if not isinstance(attribute_ids, list) or not all(isinstance(a, str) for a in attribute_ids):
            raise ValueError("attribute_ids must be a list of attribute ids")
        duplicates = sorted({a for a in attribute_ids if attribute_ids.count(a) > 1})
        if duplicates:
            raise ValueError(f"Duplicate attribute(s) in view: {duplicates}")
        defined_ids = {a.id for a in project.attributes}
        unknown = set(attribute_ids) - defined_ids
        if unknown:
            raise ValueError(f"Unknown attribute(s) for project '{project.id}': {sorted(unknown)}")
        return list(attribute_ids)

    @staticmethod
    def _checked_single_line(single_line: Any, view: View) -> list[str] | None:
        """Validated single-line (no wrap) columns for ``view``, or None when there are none.

        A list of the view's column keys (``VIEW_ID_COLUMN`` or one of its
        ``attribute_ids``) without duplicates; None or an empty list means
        every column wraps (the key is then not written). Stored in the
        view's column order.
        """
        if single_line is None:
            return None
        if not isinstance(single_line, list) or not all(isinstance(k, str) for k in single_line):
            raise ValueError("single_line must be a list of column keys")
        allowed = (VIEW_ID_COLUMN, *view.attribute_ids)
        seen: set[str] = set()
        for key in single_line:
            if key not in allowed:
                raise ValueError(
                    f"Unknown column '{key}' for view '{view.name}'; expected '{VIEW_ID_COLUMN}' "
                    "or one of the view's attribute ids"
                )
            if key in seen:
                raise ValueError(f"Duplicate column '{key}' in single_line")
            seen.add(key)
        ordered = [key for key in allowed if key in seen]
        return ordered or None

    @staticmethod
    def _checked_column_widths(column_widths: Any, view: View) -> dict[str, int] | None:
        """Validated column widths for ``view``, or None when there are none.

        A mapping of column key to an integer width in pixels within
        [MIN_COLUMN_WIDTH, MAX_COLUMN_WIDTH]. Keys are the view's own
        columns: ``VIEW_ID_COLUMN`` or one of its ``attribute_ids``. None or
        an empty mapping means "no widths" (the key is then not written).
        Widths are per-browser display preferences everywhere else; only a
        view can share them through project.json.
        """
        if column_widths is None:
            return None
        if not isinstance(column_widths, dict):
            raise ValueError("column_widths must be an object mapping column keys to widths")
        allowed = {VIEW_ID_COLUMN, *view.attribute_ids}
        widths: dict[str, int] = {}
        for key, width in column_widths.items():
            if key not in allowed:
                raise ValueError(
                    f"Unknown column '{key}' for view '{view.name}'; expected '{VIEW_ID_COLUMN}' "
                    "or one of the view's attribute ids"
                )
            if (
                not isinstance(width, int)
                or isinstance(width, bool)
                or not MIN_COLUMN_WIDTH <= width <= MAX_COLUMN_WIDTH
            ):
                raise ValueError(
                    f"Width of column '{key}' must be an integer from {MIN_COLUMN_WIDTH} "
                    f"to {MAX_COLUMN_WIDTH}"
                )
            widths[key] = width
        # Stored in the view's column order, whatever order the client sent,
        # so the same widths always serialize identically.
        ordered = {key: widths[key] for key in (VIEW_ID_COLUMN, *view.attribute_ids) if key in widths}
        return ordered or None

    @staticmethod
    def _checked_view_name(project: Project, name: Any, view_id: str | None = None) -> str:
        """Return ``name`` stripped, or raise if it is blank or another view's name.

        Names are compared case-insensitively after stripping, like view
        names in templates, so a project can always be saved as a template.
        ``view_id`` is the view being renamed (its own name is not a clash;
        ``update_view``).
        Existing duplicates in older data are left as they are.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("View name must not be empty")
        stripped = name.strip()
        folded = stripped.casefold()
        for view in project.views:
            if view.id != view_id and isinstance(view.name, str) and view.name.strip().casefold() == folded:
                raise ValueError(f"View name '{stripped}' is already used by view '{view.id}'")
        return stripped

    @classmethod
    def _add_view(cls, project: Project, name: str, attribute_ids: list[str]) -> View:
        """Add a view to ``project`` in memory; the name must be new, every attribute id defined."""
        name = cls._checked_view_name(project, name)
        defined_ids = {a.id for a in project.attributes}
        unknown = set(attribute_ids) - defined_ids
        if unknown:
            raise ValueError(f"Unknown attribute(s) for project '{project.id}': {sorted(unknown)}")

        taken_ids = {v.id for v in project.views}
        view_id = _unique_slug(name, lambda c: c in taken_ids, fallback="view")

        view = View(id=view_id, name=name, attribute_ids=list(attribute_ids))
        project.views.append(view)
        return view

    def list_views(self, project_id: str) -> list[View]:
        return self.store.load_project(project_id).views

    @_records_history
    @_requires(ACTION_PROJECT_ADMIN)
    def remove_view(self, project_id: str, view_id: str) -> None:
        project = self.store.load_project(project_id)
        if not any(v.id == view_id for v in project.views):
            raise FileNotFoundError(f"View '{view_id}' not found in project '{project_id}'")
        project.views = [v for v in project.views if v.id != view_id]
        self.store.save_project(project)

    # --- links ----------------------------------------------------------------

    def _get_link_configuration(self, project: Project, config_id: str) -> LinkConfiguration:
        for config in project.link_configurations:
            if config.id == config_id:
                return config
        raise FileNotFoundError(f"Link configuration '{config_id}' not found in project '{project.id}'")

    def _refresh_link_attribute(
        self,
        project_id: str,
        config_id: str,
        module_id: str,
        requirement_id: str,
        config: LinkConfiguration | None = None,
    ) -> None:
        """Recompute one requirement's read-only mirror of a link configuration.

        ``"> target"`` per outgoing and ``"< source"`` per incoming link,
        ``"; "``-joined. A cross-project configuration's targets are
        project-qualified (``"> rbc-gp:SYRS-0001"``: requirement ids are only
        unique per project) and it has no incoming entries here: its
        targets live in the other project, which stores no mirror (see
        ``incoming_links``).
        """
        if config is None:
            config = next(
                (c for c in self.store.load_project(project_id).link_configurations if c.id == config_id), None
            )
        pairs = self.store.load_link_pairs(project_id, config_id)
        outgoing = [p.target_id for p in pairs if p.source_id == requirement_id]
        if config is not None and config.target_project_id is not None:
            entries = [f"> {config.target_project_id}:{t}" for t in outgoing]
        else:
            incoming = [p.source_id for p in pairs if p.target_id == requirement_id]
            entries = [f"> {t}" for t in outgoing] + [f"< {s}" for s in incoming]

        requirement = self.store.load_requirement(project_id, module_id, requirement_id)
        if entries:
            requirement.attributes[config_id] = "; ".join(entries)
        else:
            requirement.attributes.pop(config_id, None)
        self.store.save_requirement(project_id, module_id, requirement)

    @staticmethod
    def _plain_target_id(config: LinkConfiguration, target_id: str) -> str:
        """``target_id`` without a ``<project>:`` qualifier, which must name the configuration's target project.

        Clients may pass a cross-project target as shown in the mirror
        (``rbc-gp:SYRS-0001``) or plain; a configuration within one project
        takes plain ids only (a requirement id never contains ``:``).
        """
        if ":" not in target_id:
            return target_id
        owner, _, plain = target_id.partition(":")
        if config.target_project_id is None or owner != config.target_project_id:
            raise ValueError(
                f"Target '{target_id}' is not in the target project of link configuration '{config.name}'"
            )
        return plain

    @_records_history
    def link_requirements(
        self, project_id: str, config_id: str, source_id: str, target_id: str
    ) -> dict[str, Requirement]:
        """Link two requirements through a configuration of ``project_id`` (the source project).

        The source must be in the configuration's source module and the
        target in its target module, in the target project for a
        cross-project configuration (which must still be referenced). Only
        this project's files are written: the pair and the source's mirror
        (plus the target's mirror for a configuration within the project).
        """
        project = self.store.load_project(project_id)
        config = self._get_link_configuration(project, config_id)
        # The link belongs to the configuration's source module: its edit lock applies.
        self._check_module_writes(project_id, [config.source_module_id])
        target_id = self._plain_target_id(config, target_id)
        target_project = self._load_link_target_project(project, config)

        source_module = self.store.find_requirement_module(project_id, source_id)
        if source_module is None:
            raise FileNotFoundError(f"Requirement '{source_id}' not found in project '{project_id}'")
        target_module = self.store.find_requirement_module(target_project, target_id)
        if target_module is None:
            raise FileNotFoundError(f"Requirement '{target_id}' not found in project '{target_project}'")

        if source_module != config.source_module_id or target_module != config.target_module_id:
            target_where = (
                f"'{config.target_module_id}'" if config.target_project_id is None
                else f"'{config.target_module_id}' of project '{config.target_project_id}'"
            )
            raise ValueError(
                f"Link configuration '{config.name}' requires source in module "
                f"'{config.source_module_id}' and target in module {target_where}"
            )

        pairs = self.store.load_link_pairs(project_id, config_id)
        if any(p.source_id == source_id and p.target_id == target_id for p in pairs):
            shown = target_id if config.target_project_id is None else f"{config.target_project_id}:{target_id}"
            raise ValueError(f"Link '{config.name}' from '{source_id}' to '{shown}' already exists")
        pairs.append(LinkPair(source_id=source_id, target_id=target_id))
        self.store.save_link_pairs(project_id, config_id, pairs)

        self._refresh_link_attribute(project_id, config_id, source_module, source_id, config)
        if config.target_project_id is None:
            self._refresh_link_attribute(project_id, config_id, target_module, target_id, config)

        return {
            "source": self.store.load_requirement(project_id, source_module, source_id),
            "target": self.store.load_requirement(target_project, target_module, target_id),
        }

    def link_options(
        self, project_id: str, from_id: str, to_id: str, from_project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """The links that may join two requirements, in either direction.

        Used by drag-and-drop linking: ``from_id`` is the dragged requirement
        (of ``from_project_id``, default ``project_id``), ``to_id`` the one it
        was dropped on (of ``project_id``). Every link configuration between
        the two requirements' modules is an option, oriented the configured
        way: a configuration from the dragged requirement's module to the drop
        target's module links ``from_id`` → ``to_id``; one in the opposite
        direction links ``to_id`` → ``from_id``. A configuration within one
        module (both requirements in it) is offered once, in drag direction.

        Across projects, the options are the cross-project configurations of
        either project that target the other one (and that it still
        references); ``project_id`` of an option is the project holding the
        configuration, where the link is created (``POST
        /projects/{project_id}/links``). ``exists`` marks a link that is
        already there (creating it again is rejected by
        ``link_requirements``). No configuration → empty list. Read-only;
        options are listed in configuration order, the drop target's
        project first.
        """
        if from_project_id is None:
            from_project_id = project_id
        if from_id == to_id and from_project_id == project_id:
            raise ValueError("A requirement cannot be linked to itself")
        project = self.store.load_project(project_id)
        if from_project_id != project_id:
            check_permission(ACTION_PROJECT_READ, from_project_id)
        from_project = project if from_project_id == project_id else self.store.load_project(from_project_id)
        from_module = self.store.find_requirement_module(from_project_id, from_id)
        if from_module is None:
            raise FileNotFoundError(f"Requirement '{from_id}' not found in project '{from_project_id}'")
        to_module = self.store.find_requirement_module(project_id, to_id)
        if to_module is None:
            raise FileNotFoundError(f"Requirement '{to_id}' not found in project '{project_id}'")

        def option(holder: Project, config: LinkConfiguration, source: tuple[str, str], target: tuple[str, str]) -> dict[str, Any]:
            pairs = self.store.load_link_pairs(holder.id, config.id)
            exists = any(p.source_id == source[1] and p.target_id == target[1] for p in pairs)
            return {
                "config": config,
                "project_id": holder.id,
                "source_id": source[1],
                "target_id": target[1],
                "source_project_id": source[0],
                "target_project_id": target[0],
                "source_module_id": config.source_module_id,
                "target_module_id": config.target_module_id,
                "exists": exists,
            }

        options: list[dict[str, Any]] = []
        if from_project_id == project_id:
            for config in self._local_configs(project):
                if config.source_module_id == from_module and config.target_module_id == to_module:
                    source, target = from_id, to_id
                elif config.source_module_id == to_module and config.target_module_id == from_module:
                    source, target = to_id, from_id
                else:
                    continue
                options.append(option(project, config, (project_id, source), (project_id, target)))
            return options

        # The drop target's project links to the dragged requirement's project ...
        if from_project_id in self._effective_references(project):
            for config in project.link_configurations:
                if (
                    config.target_project_id == from_project_id
                    and config.source_module_id == to_module
                    and config.target_module_id == from_module
                ):
                    options.append(option(project, config, (project_id, to_id), (from_project_id, from_id)))
        # ... or the dragged requirement's project links to the drop target's.
        if project_id in self._effective_references(from_project):
            for config in from_project.link_configurations:
                if (
                    config.target_project_id == project_id
                    and config.source_module_id == from_module
                    and config.target_module_id == to_module
                ):
                    options.append(option(from_project, config, (from_project_id, from_id), (project_id, to_id)))
        return options

    @_records_history
    def unlink_requirements(
        self, project_id: str, config_id: str, source_id: str, target_id: str
    ) -> dict[str, Requirement | None]:
        """Remove one link of a configuration of ``project_id`` (the source project).

        For a cross-project configuration the target may already be gone
        (or its project no longer readable): the pair is removed anyway and
        ``target`` is None in the result.
        """
        project = self.store.load_project(project_id)
        config = self._get_link_configuration(project, config_id)
        self._check_module_writes(project_id, [config.source_module_id])
        target_id = self._plain_target_id(config, target_id)

        source_module = self.store.find_requirement_module(project_id, source_id)
        if source_module is None:
            raise FileNotFoundError(f"Requirement '{source_id}' not found in project '{project_id}'")
        target_project = config.target_project_id or project_id
        target_module: str | None = None
        if config.target_project_id is None:
            target_module = self.store.find_requirement_module(project_id, target_id)
            if target_module is None:
                raise FileNotFoundError(f"Requirement '{target_id}' not found in project '{project_id}'")
        else:
            try:
                target_module = self.store.find_requirement_module(target_project, target_id)
            except (OSError, ValueError):
                target_module = None

        pairs = self.store.load_link_pairs(project_id, config_id)
        pairs = [p for p in pairs if not (p.source_id == source_id and p.target_id == target_id)]
        self.store.save_link_pairs(project_id, config_id, pairs)

        self._refresh_link_attribute(project_id, config_id, source_module, source_id, config)
        if config.target_project_id is None and target_module is not None:
            self._refresh_link_attribute(project_id, config_id, target_module, target_id, config)

        return {
            "source": self.store.load_requirement(project_id, source_module, source_id),
            "target": (
                self.store.load_requirement(target_project, target_module, target_id)
                if target_module is not None
                else None
            ),
        }

    def incoming_links(self, project_id: str, module_id: str | None = None) -> list[dict[str, Any]]:
        """Links into ``project_id`` from other projects (cross-project configurations), read-only.

        Cross-project links are stored only in the source (referencing)
        project, so the target project's requirement files carry no mirror
        of them; this computes them on read. Each entry: the source project
        (id, name), configuration (id, name), source module and requirement,
        and the target module and requirement of this project. With
        ``module_id``, only links into that module. Projects that cannot be
        loaded are skipped. Sorted by target id, then source project and
        source id.
        """
        self.store.load_project(project_id)
        if module_id is not None and not self.store.module_exists(project_id, module_id):
            raise FileNotFoundError(f"Module '{module_id}' not found in project '{project_id}'")
        result: list[dict[str, Any]] = []
        for other in self._other_projects(project_id):
            if project_id not in self._effective_references(other) or not self._can_read(other.id):
                continue
            for config in other.link_configurations:
                if config.target_project_id != project_id:
                    continue
                if module_id is not None and config.target_module_id != module_id:
                    continue
                for pair in self.store.load_link_pairs(other.id, config.id):
                    result.append(
                        {
                            "project_id": other.id,
                            "project_name": other.name,
                            "config_id": config.id,
                            "config_name": config.name,
                            "source_module_id": config.source_module_id,
                            "source_id": pair.source_id,
                            "target_module_id": config.target_module_id,
                            "target_id": pair.target_id,
                        }
                    )
        result.sort(key=lambda e: (e["target_id"], e["project_id"], e["source_id"], e["config_id"]))
        return result

    # --- templates ------------------------------------------------------------

    def _read_template_text(self, source: str, local_id: str) -> str:
        if source == BUILTIN_SOURCE:
            return read_builtin_template_text(local_id, self.builtin_templates_dir)
        return self.store.read_user_template_text(local_id)

    def _load_template(self, template_id: Any) -> Template:
        """Read and shape-check a template (404 if missing, 400 if malformed)."""
        source, local_id = split_template_id(template_id)
        text = self._read_template_text(source, local_id)
        try:
            return parse_template_text(text, template_id)
        except ValueError as exc:
            raise ValueError(f"Template '{template_id}' is invalid: {exc}") from exc

    def _build_from_template(
        self,
        template: Template,
        project_id: str,
        name: str,
        description: str,
        parent_project_id: str | None,
    ) -> tuple[Project, list[Module], dict[str, list[Requirement]]]:
        """Build a project, its modules and their skeleton headings from ``template`` in memory.

        Uses the same helpers as ``add_attribute_definition``,
        ``create_module``, ``create_link_configuration``, ``create_view`` and
        "Add module from template" (``_apply_module_template``), so a
        template can never produce a project those operations would reject.
        Module templates a module references are loaded and applied in
        module order, after the template's own attributes and scopes and
        before its link configurations; their views follow the template's
        views (skipped when the name is taken). Returns the project, the
        modules (template order) and the skeleton requirements per module id.
        Raises ``ValueError`` naming the offending template entry. Nothing is
        written.
        """
        project = Project(
            id=project_id,
            name=name,
            description=description,
            parent_project_id=parent_project_id,
            attributes=list(BUILTIN_ATTRIBUTES),
        )
        builtin_ids = {a.id for a in BUILTIN_ATTRIBUTES}
        for index, attr in enumerate(template.attributes):
            if attr.id in builtin_ids:
                # Checked here, not by _new_attribute_definition, whose message
                # names the project (an internal id while listing templates).
                raise ValueError(
                    f"attributes[{index}]: '{attr.id}' is a built-in attribute every project already has"
                )
            try:
                project.attributes.append(
                    self._new_attribute_definition(
                        project, attr.id, attr.name, attr.type, attr.required, attr.default, attr.choices
                    )
                )
            except ValueError as exc:
                raise ValueError(f"attributes[{index}] ('{attr.id}'): {exc}") from exc

        modules: list[Module] = []
        module_ids: dict[str, str] = {}
        module_templates: dict[str, ModuleTemplate] = {}
        module_names: set[str] = set()
        for index, tmodule in enumerate(template.modules):
            where = f"modules[{index}] ('{tmodule.name or tmodule.key}')"
            mtemplate: ModuleTemplate | None = None
            if tmodule.module_template is not None:
                try:
                    mtemplate = self._load_module_template(tmodule.module_template)
                except (OSError, ValueError) as exc:
                    raise ValueError(f"{where}: {exc}") from exc
                module_templates[tmodule.key] = mtemplate
            module_name = tmodule.name if tmodule.name is not None else mtemplate.module.name  # type: ignore[union-attr]
            prefix = tmodule.prefix if tmodule.prefix is not None else mtemplate.module.prefix  # type: ignore[union-attr]
            module_description = (
                tmodule.description if tmodule.description is not None else mtemplate.module.description  # type: ignore[union-attr]
            )
            where = f"modules[{index}] ('{module_name}')"
            if module_name.casefold() in module_names:
                raise ValueError(f"{where}: duplicate module name '{module_name}'")
            module_names.add(module_name.casefold())
            try:
                validate_prefix(prefix)
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from exc
            taken = set(module_ids.values())
            module_id = _unique_slug(module_name, lambda c: c in taken, fallback="module")
            module_ids[tmodule.key] = module_id
            try:
                module_type = self._checked_module_type(tmodule.type if tmodule.type is not None else (
                    mtemplate.module.type if mtemplate is not None else None
                ))
                if mtemplate is not None and module_type != self._checked_module_type(mtemplate.module.type):
                    raise ValueError(
                        f"type '{module_type}' differs from module template '{mtemplate.id}' "
                        f"('{self._checked_module_type(mtemplate.module.type)}')"
                    )
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from exc
            modules.append(
                Module(
                    id=module_id,
                    name=module_name,
                    prefix=prefix,
                    description=module_description,
                    type=module_type,
                )
            )
        # The template's declared module order (and folders), not the modules' id order.
        project.module_order = self._tree_from_template(template.module_tree, module_ids, [m.id for m in modules])
        # Attribute scopes name module keys (checked by ``parse_template``);
        # stored as module ids in module order.
        for index, tattr in enumerate(template.attributes):
            if tattr.modules or tattr.module_types:
                definition = next(a for a in project.attributes if a.id == tattr.id)
                try:
                    definition.module_types = self._checked_module_types(tattr.module_types)
                    self._check_required_scope(
                        definition.name, definition.required, [*tattr.modules, *definition.module_types]
                    )
                except ValueError as exc:
                    raise ValueError(f"attributes[{index}] ('{tattr.id}'): {exc}") from exc
                wanted = {module_ids[key] for key in tattr.modules}
                definition.modules = [m.id for m in modules if m.id in wanted]

        # Module templates and heading skeletons, in module order. Ids are
        # allocated from the new project's counters (nothing is on disk yet).
        def allocate(module: Module) -> Callable[[], str]:
            def next_id() -> str:
                counter = project.id_counters.get(module.prefix, 0) + 1
                project.id_counters[module.prefix] = counter
                return f"{module.prefix}-{counter:04d}"

            return next_id

        requirements: dict[str, list[Requirement]] = {}
        pending_views: list[tuple[int, ModuleTemplate, dict[str, str]]] = []
        for index, (tmodule, module) in enumerate(zip(template.modules, modules)):
            mtemplate = module_templates.get(tmodule.key)
            skeleton = tmodule.skeleton if tmodule.skeleton is not None else (mtemplate.skeleton if mtemplate else [])
            if mtemplate is None and not skeleton:
                continue
            try:
                id_map: dict[str, str] = {}
                if mtemplate is not None:
                    id_map = self._merge_template_attributes(project, module, mtemplate.attributes)["id_map"]
                    if mtemplate.view is not None:
                        pending_views.append((index, mtemplate, id_map))
                requirements[module.id] = self._skeleton_requirements(project, module, skeleton, allocate(module))
            except ValueError as exc:
                raise ValueError(f"modules[{index}] ('{module.name}'): {exc}") from exc
        # Scopes of merged attributes in module order.
        self._sort_attribute_scopes(project, module_tree.module_ids(project.module_order))

        link_ids: dict[str, str] = {}
        modules_by_id = {m.id: m for m in modules}
        for index, tconfig in enumerate(template.link_configurations):
            try:
                config = self._add_link_configuration(
                    project,
                    tconfig.name,
                    modules_by_id[module_ids[tconfig.source_module]],
                    modules_by_id[module_ids[tconfig.target_module]],
                )
            except ValueError as exc:
                raise ValueError(f"link_configurations[{index}] ('{tconfig.key}'): {exc}") from exc
            link_ids[tconfig.key] = config.id

        attribute_ids = {a.id for a in BUILTIN_ATTRIBUTES} | {a.id for a in template.attributes}
        for index, tview in enumerate(template.views):
            mapped: list[str] = []
            for ref in tview.attribute_ids:
                if ref.startswith(LINK_REF_PREFIX):
                    key = ref[len(LINK_REF_PREFIX):]
                    if key not in link_ids:
                        raise ValueError(
                            f"views[{index}] ('{tview.name}'): '{ref}' does not name a link "
                            "configuration key of this template"
                        )
                    mapped.append(link_ids[key])
                elif ref in attribute_ids:
                    mapped.append(ref)
                else:
                    raise ValueError(
                        f"views[{index}] ('{tview.name}'): unknown attribute '{ref}' (expected "
                        f"'object_text', a template attribute id, or '{LINK_REF_PREFIX}<key>')"
                    )
            view = self._add_view(project, tview.name, mapped)
            try:
                self._apply_template_view_layout(tview, view, dict(zip(tview.attribute_ids, mapped)))
            except ValueError as exc:
                raise ValueError(f"views[{index}] ('{tview.name}'): {exc}") from exc
            # Module keys were checked by the template parser.
            wanted_modules = {module_ids[key] for key in tview.modules}
            view.modules = [m for m in module_tree.module_ids(project.module_order) if m in wanted_modules]

        for index, mtemplate, id_map in pending_views:
            try:
                self._merge_template_view(project, mtemplate.view, id_map)
            except ValueError as exc:
                raise ValueError(f"modules[{index}] ('{modules[index].name}'): {exc}") from exc

        # Rule messages name the entry (``row_styles[<index>]``).
        project.row_styles = self._checked_row_styles(project, [s.to_dict() for s in template.row_styles])

        return project, modules, requirements

    @classmethod
    def _tree_from_template(cls, entries: list, module_ids: dict[str, str], order: list[str]) -> list:
        """A template's ``module_tree`` as a project's module tree; unplaced modules follow in ``order``.

        Folder ids are slugged from the names (unique in the project); names
        and depth follow the same rules as ``create_module_folder``.
        """
        taken: set[str] = set()

        def build(items: list, depth: int, where: str) -> list:
            result: list = []
            for index, entry in enumerate(items):
                if isinstance(entry, TemplateFolder):
                    at = f"{where}[{index}] ('{entry.name}')"
                    if depth > MAX_MODULE_FOLDER_DEPTH:
                        raise ValueError(
                            f"{at}: module folders can be nested at most {MAX_MODULE_FOLDER_DEPTH} levels deep"
                        )
                    try:
                        name = cls._checked_folder_name(entry.name, result)
                    except ValueError as exc:
                        raise ValueError(f"{at}: {exc}") from exc
                    folder_id = _unique_slug(name, lambda c: c in taken, fallback="folder")
                    taken.add(folder_id)
                    folder = ModuleFolder(id=folder_id, name=name)
                    result.append(folder)
                    folder.children = build(entry.children, depth + 1, f"{at}.children")
                else:
                    result.append(module_ids[entry])
            return result

        tree = build(entries, 1, "module_tree")
        placed = set(module_tree.module_ids(tree))
        tree.extend(module_id for module_id in order if module_id not in placed)
        return tree

    def _validate_template(self, template: Template) -> list[Module]:
        """Dry-run a template build; the modules it would create (template order)."""
        return self._build_from_template(template, _TEMPLATE_CHECK_PROJECT_ID, template.name, "", None)[1]

    @staticmethod
    def _template_summary(template: Template, modules: list[Module]) -> dict[str, Any]:
        """A listing entry; ``modules`` are the built modules (names resolved from module templates)."""
        return {
            "id": template.id,
            "source": template.source,
            "name": template.name,
            "description": template.description,
            "modules": [
                {"key": t.key, "name": m.name, "prefix": m.prefix, "type": m.type}
                for t, m in zip(template.modules, modules)
            ],
            "error": None,
        }

    @staticmethod
    def _fallback_template_name(text: str | None, local_id: str) -> str:
        return _display_name(text, local_id)

    def list_templates(self) -> list[dict[str, Any]]:
        """Built-in templates, then user templates, each sorted by name.

        Every template is fully validated. An invalid one (unreadable, bad
        JSON, wrong shape, unsupported ``format_version``, or a rule a
        project would reject) is still listed, with the reason in ``error``
        and no modules, so one bad file never hides the others; creating a
        project from it fails with the same reason.
        """
        sources = (
            (BUILTIN_SOURCE, list_builtin_template_ids(self.builtin_templates_dir)),
            (USER_SOURCE, self.store.list_user_template_ids()),
        )
        result: list[dict[str, Any]] = []
        for source, local_ids in sources:
            entries: list[dict[str, Any]] = []
            for local_id in local_ids:
                template_id = make_template_id(source, local_id)
                text: str | None = None
                try:
                    text = self._read_template_text(source, local_id)
                    template = parse_template_text(text, template_id)
                    entries.append(self._template_summary(template, self._validate_template(template)))
                except (OSError, ValueError) as exc:
                    logger.debug("Template %s is invalid: %s", template_id, exc)
                    entries.append(
                        {
                            "id": template_id,
                            "source": source,
                            "name": self._fallback_template_name(text, local_id),
                            "description": "",
                            "modules": [],
                            "error": str(exc),
                        }
                    )
            entries.sort(key=lambda e: (e["name"].casefold(), e["id"]))
            result.extend(entries)
        return result

    @staticmethod
    def _template_from_project(
        project: Project,
        modules: list[Module],
        template_id: str,
        name: str,
        description: str,
        tree: list | None = None,
        skeletons: dict[str, list[SkeletonNode]] | None = None,
    ) -> Template:
        """A project's structure as a template; no requirements (only heading skeletons), no id counters, no parent."""
        skeletons = skeletons or {}
        builtin_ids = {a.id for a in BUILTIN_ATTRIBUTES}
        # Cross-project configurations are not saved (their target module is
        # not part of the template); views drop their mirror columns.
        local_configs = ProjectService._local_configs(project)
        link_ids = {c.id for c in local_configs}
        attributes = [
            TemplateAttribute(
                id=a.id,
                name=a.name,
                type=a.type,
                required=a.required,
                default=a.default,
                choices=list(a.choices) if isinstance(a.choices, list) else a.choices,
                # A required attribute applies to every module, whatever a
                # hand-edited file lists (see ``_attribute_applies``).
                modules=[] if a.required else [m.id for m in modules if m.id in a.modules],
                module_types=[] if a.required else [t for t in MODULE_TYPES if t in a.module_types],
            )
            for a in project.attributes
            if not a.system and a.id not in builtin_ids
        ]
        kept_ids = builtin_ids | {a.id for a in attributes}
        views = []
        for view in project.views:
            ref_of: dict[str, str] = {}
            for attr_id in view.attribute_ids:
                if attr_id in link_ids:
                    ref_of[attr_id] = f"{LINK_REF_PREFIX}{attr_id}"
                elif attr_id in kept_ids:
                    ref_of[attr_id] = attr_id
                # else: a reference the project itself no longer resolves
                # (e.g. its link configuration was removed) is dropped.
            refs = [ref_of[a] for a in view.attribute_ids if a in ref_of]
            template_view = ProjectService._template_view(view, refs, ref_of)
            # Module keys are module ids; a scope entry naming no module is dropped.
            template_view.modules = [m.id for m in modules if m.id in view.modules]
            views.append(template_view)
        return Template(
            id=template_id,
            source=USER_SOURCE,
            name=name,
            description=description,
            attributes=attributes,
            modules=[
                TemplateModule(
                    key=m.id,
                    name=m.name,
                    prefix=m.prefix,
                    description=m.description,
                    type=m.type,
                    skeleton=skeletons.get(m.id) or None,
                )
                for m in modules
            ],
            # Folders keep their ids as keys; written only when there is one.
            module_tree=ProjectService._template_tree(tree) if module_tree.has_folders(tree) else [],
            link_configurations=[
                TemplateLinkConfiguration(
                    key=c.id, name=c.name, source_module=c.source_module_id, target_module=c.target_module_id
                )
                for c in local_configs
            ],
            views=views,
            # Rules hand-edited data no longer resolves are not carried over.
            row_styles=[
                TemplateRowStyle(**style.to_dict()) for style in ProjectService._effective_row_styles(project)
            ],
        )

    @staticmethod
    def _template_tree(entries: list) -> list:
        """A project's (effective) module tree as template ``module_tree`` entries (module and folder ids as keys)."""
        return [
            TemplateFolder(key=e.id, name=e.name, children=ProjectService._template_tree(e.children))
            if isinstance(e, ModuleFolder)
            else e
            for e in entries
        ]

    def save_project_as_template(self, project_id: str, name: Any, description: Any = "") -> dict[str, Any]:
        """Write a project's structure as a new user template (``user:<slug>``).

        Saves non-system attributes (except the built-in ``object_text``),
        modules (key = module id) with their heading skeletons (see
        ``_skeleton_from_module``), link configurations (between module keys),
        views (mirror attributes written as ``link:<key>``; references to
        attributes the project no longer has are dropped) and row styles
        (rules that no longer resolve are dropped). Requirements, id
        counters and the parent are not saved. The result is validated
        exactly like a loaded template before anything is written. A name
        already used by any template (built-in or user, case-insensitive)
        raises ``ConflictError``; existing files are never overwritten.
        """
        check_permission(ACTION_LIBRARY_WRITE)
        project = self.store.load_project(project_id)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Template name must not be empty")
        name = name.strip()
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ValueError("Template description must be a string")

        for entry in self.list_templates():
            if entry["name"].casefold() == name.casefold():
                raise ConflictError(f"A template named '{entry['name']}' already exists ({entry['id']})")

        local_id = _unique_slug(name, self.store.user_template_path_exists, fallback="template")
        template_id = make_template_id(USER_SOURCE, local_id)
        modules = self.list_modules(project_id)
        template = self._template_from_project(
            project,
            modules,
            template_id,
            name,
            description,
            self._module_tree(project),
            {m.id: self._skeleton_from_module(project, m) for m in modules},
        )
        data = template.to_dict()
        try:
            # Round-trip through the loader so the saved file is exactly what
            # a later "create from template" will accept.
            built = self._validate_template(parse_template(data, template_id))
        except ValueError as exc:
            raise ValueError(f"Project '{project_id}' cannot be saved as a template: {exc}") from exc
        self.store.save_user_template(local_id, data)
        return self._template_summary(template, built)

    def import_template(self, text: Any) -> dict[str, Any]:
        """Save an uploaded template JSON document as a new user template.

        ``text`` is the file's content (a leading UTF-8 byte order mark is
        ignored). It is checked exactly like a template loaded from disk —
        shape and ``format_version`` (``parse_template_text``), then every
        project rule (``_validate_template``) — so an imported template can
        always be used to create a project. Invalid input raises
        ``ValueError`` and writes nothing.

        Like "Save as template", an import never overwrites anything: a
        template whose name is already used by any template (built-in or
        user, case-insensitive) raises ``ConflictError``. The file name stem
        (the template's identity, ``user:<slug>``) is slugged from the name
        and suffixed on collision; the file's own ``id`` key is
        informational and replaced. The file is written in the canonical
        template shape (stable key order; unknown keys are not kept).
        """
        check_permission(ACTION_LIBRARY_WRITE)
        if not isinstance(text, str):
            raise ValueError("The template file content must be a string")
        # The limit is in UTF-8 bytes, as over REST (a BOM counts, as it does
        # in the request body). "surrogatepass" cannot fail here; lone
        # surrogates are rejected by ``parse_template_text``.
        if len(text.encode("utf-8", "surrogatepass")) > MAX_TEMPLATE_IMPORT_BYTES:
            raise ValueError(
                f"The template file is too large: the limit is {MAX_TEMPLATE_IMPORT_BYTES // (1024 * 1024)} MiB"
            )
        # A provisional id: the real one depends on the (validated) name.
        provisional_id = make_template_id(USER_SOURCE, "import")
        try:
            template = parse_template_text(text, provisional_id)
            built = self._validate_template(template)
        except ValueError as exc:
            raise ValueError(f"The template file is invalid: {exc}") from exc

        for entry in self.list_templates():
            if entry["name"].casefold() == template.name.casefold():
                raise ConflictError(f"A template named '{entry['name']}' already exists ({entry['id']})")

        local_id = _unique_slug(template.name, self.store.user_template_path_exists, fallback="template")
        template.id = make_template_id(USER_SOURCE, local_id)
        self.store.save_user_template(local_id, template.to_dict())
        return self._template_summary(template, built)

    # --- module templates and heading skeletons ---------------------------------
    #
    # A module template (``templates.ModuleTemplate``) describes one module:
    # its default name/prefix/description/type, the attributes that apply to
    # it, an optional view and a heading skeleton. "Add module from template"
    # (``create_module_from_template``) and project templates referencing a
    # module template (``_build_from_template``) apply it through the same
    # in-memory helpers below.

    def _read_module_template_text(self, source: str, local_id: str) -> str:
        if source == BUILTIN_SOURCE:
            return read_builtin_module_template_text(local_id, self.builtin_templates_dir)
        return self.store.read_user_module_template_text(local_id)

    def _load_module_template(self, template_id: Any) -> ModuleTemplate:
        """Read and shape-check a module template (404 if missing, 400 if malformed)."""
        source, local_id = split_template_id(template_id)
        text = self._read_module_template_text(source, local_id)
        try:
            return parse_module_template_text(text, template_id)
        except ValueError as exc:
            raise ValueError(f"Module template '{template_id}' is invalid: {exc}") from exc

    @staticmethod
    def _extend_scope(attr: AttributeDefinition, module: Module) -> bool:
        """Make ``attr`` apply to ``module`` (a new, empty module) by listing it; whether it changed."""
        if ProjectService._attribute_applies(attr, module.id, module.type):
            return False
        attr.modules.append(module.id)
        return True

    def _merge_template_attributes(
        self, project: Project, module: Module, attributes: list[ModuleTemplateAttribute]
    ) -> dict[str, Any]:
        """Make a module template's attributes apply to the new ``module`` of ``project`` (in memory).

        Matched by name (case-insensitive, like attribute names are unique):

        - an existing attribute of the same name must have the same type (a
          link mirror attribute never matches); it is otherwise kept exactly
          as it is (choices, default, required), except that a scoped
          attribute that does not apply to the new module gets the module
          added to its ``modules`` list. The module is new and empty, so no
          existing value can become invalid;
        - a missing attribute is added, optional and scoped to the new module
          only, so no other module and no existing requirement is affected.
          Its id is the template's id, or a free slug of it when taken.

        Returns ``{"id_map": template id -> project id, "added": [ids],
        "extended": [ids]}``. Raises ``ValueError`` naming the attribute.
        """
        id_map: dict[str, str] = {}
        added: list[str] = []
        extended: list[str] = []
        for index, tattr in enumerate(attributes):
            where = f"attributes[{index}] ('{tattr.name}')"
            folded = tattr.name.strip().casefold()
            existing = next(
                (a for a in project.attributes if isinstance(a.name, str) and a.name.strip().casefold() == folded),
                None,
            )
            if existing is not None:
                if existing.system:
                    raise ValueError(f"{where}: '{existing.name}' is a link attribute of the project")
                if existing.type != tattr.type:
                    raise ValueError(
                        f"{where}: the project's attribute '{existing.name}' has type '{existing.type}', "
                        f"the template's has type '{tattr.type}'"
                    )
                if self._extend_scope(existing, module):
                    extended.append(existing.id)
                id_map[tattr.id] = existing.id
                continue
            taken = {a.id for a in project.attributes}
            attr_id = tattr.id if tattr.id not in taken else _unique_slug(
                tattr.id, lambda c: c in taken, fallback="attribute"
            )
            try:
                definition = self._new_attribute_definition(
                    project, attr_id, tattr.name, tattr.type, False, tattr.default, tattr.choices
                )
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from exc
            definition.modules = [module.id]
            project.attributes.append(definition)
            added.append(attr_id)
            id_map[tattr.id] = attr_id
        return {"id_map": id_map, "added": added, "extended": extended}

    @staticmethod
    def _sort_attribute_scopes(project: Project, order: list[str]) -> None:
        """Put every attribute's ``modules`` list in display order (ids naming no module keep their place at the end)."""
        position = {module_id: i for i, module_id in enumerate(order)}
        for attr in project.attributes:
            if len(attr.modules) > 1:
                attr.modules = sorted(attr.modules, key=lambda m: position.get(m, len(position)))
        # View scopes follow the same order.
        for view in project.views:
            if len(view.modules) > 1:
                view.modules = sorted(view.modules, key=lambda m: position.get(m, len(position)))

    def _merge_template_view(self, project: Project, view: TemplateView | None, id_map: dict[str, str]) -> str | None:
        """Add a module template's view unless one of that name exists; the new view's id, or None."""
        if view is None:
            return None
        folded = view.name.strip().casefold()
        if any(isinstance(v.name, str) and v.name.strip().casefold() == folded for v in project.views):
            return None
        refs = [ref if ref == "object_text" else id_map[ref] for ref in view.attribute_ids]
        added = self._add_view(project, view.name, refs)
        try:
            self._apply_template_view_layout(view, added, dict(zip(view.attribute_ids, refs)))
        except ValueError as exc:
            raise ValueError(f"view ('{view.name}'): {exc}") from exc
        return added.id

    @staticmethod
    def _template_view(view: View, refs: list[str], ref_of: dict[str, str]) -> TemplateView:
        """``view`` as a template view: its kept columns as ``refs`` (``ref_of``: attribute id to ref), with its layout."""
        def key_ref(key: str) -> str | None:
            return VIEW_ID_COLUMN if key == VIEW_ID_COLUMN else ref_of.get(key)

        widths = {key_ref(k): w for k, w in (view.column_widths or {}).items() if key_ref(k) is not None}
        single_line = [key_ref(k) for k in (view.single_line or []) if key_ref(k) is not None]
        return TemplateView(
            name=view.name, attribute_ids=refs, column_widths=widths or None, single_line=single_line or None
        )

    @classmethod
    def _apply_template_view_layout(cls, tview: TemplateView, view: View, id_of: dict[str, str]) -> None:
        """Set ``view``'s widths and single-line columns from a template view (``id_of``: ref to attribute id)."""
        def key_id(key: str) -> str:
            if key == VIEW_ID_COLUMN:
                return key
            if key not in id_of:
                raise ValueError(
                    f"Unknown column '{key}'; expected '{VIEW_ID_COLUMN}' or one of the view's attribute_ids"
                )
            return id_of[key]

        if tview.column_widths:
            view.column_widths = cls._checked_column_widths(
                {key_id(k): w for k, w in tview.column_widths.items()}, view
            )
        if tview.single_line:
            view.single_line = cls._checked_single_line([key_id(k) for k in tview.single_line], view)

    @classmethod
    def _skeleton_object_type(cls, project: Project, module: Module) -> AttributeDefinition:
        """The Object Type attribute skeleton headings are typed with (made to apply to ``module``).

        The first non-system ``enum``/``string`` attribute whose name or id
        reads "Object Type" (compared like report attributes).
        """
        for attr in project.attributes:
            if attr.system or attr.type not in ("enum", "string"):
                continue
            if "object type" in (cls._report_key(attr.name), cls._report_key(attr.id)):
                cls._extend_scope(attr, module)
                return attr
        raise ValueError(
            "a heading skeleton needs an 'Object Type' attribute (an enum with a 'Heading' choice, "
            "or a string); add one to the template or to the project"
        )

    @classmethod
    def _object_type_value(cls, attr: AttributeDefinition, wanted: str) -> str:
        """The value of Object Type attribute ``attr`` meaning ``wanted`` ("Heading", "Information")."""
        if attr.type != "enum":
            return wanted
        for choice in attr.choices or []:
            if cls._report_key(choice) == cls._report_key(wanted):
                return choice
        raise ValueError(f"attribute '{attr.name}' has no '{wanted}' choice, needed by the heading skeleton")

    def _skeleton_requirements(
        self, project: Project, module: Module, skeleton: list[SkeletonNode], allocate: Callable[[], str]
    ) -> list[Requirement]:
        """The requirements of a heading skeleton for new ``module``, in tree order (in memory).

        Each node is a requirement with ObjectText = the heading and Object
        Type = Heading; its ``text`` a first child with Object Type =
        Information. Other attributes get their defaults; every requirement
        is validated like ``add_requirement`` (so a required attribute
        without a default fails here). Ids come from ``allocate``.
        """
        if not skeleton:
            return []
        object_type = self._skeleton_object_type(project, module)
        heading_value = self._object_type_value(object_type, "Heading")
        info_value: str | None = None
        result: list[Requirement] = []

        def make(text: str, type_value: str, parent_id: str | None, order: int) -> Requirement:
            values = self._sanitized_text_values(
                project, {"object_text": richtext.from_plain(text), object_type.id: type_value}
            )
            self._validate_attributes(project, values, is_create=True, module_id=module.id, module_type=module.type)
            merged = self._merged_attributes(project, values, module.id, module.type)
            requirement = Requirement(id=allocate(), attributes=merged, parent_id=parent_id, order=order)
            result.append(requirement)
            return requirement

        def build(nodes: list[SkeletonNode], parent_id: str | None, start: int) -> None:
            nonlocal info_value
            for index, node in enumerate(nodes):
                try:
                    heading = make(node.heading, heading_value, parent_id, start + index)
                    if node.text is not None:
                        if info_value is None:
                            info_value = self._object_type_value(object_type, "Information")
                        make(node.text, info_value, heading.id, 0)
                except ValueError as exc:
                    raise ValueError(f"skeleton heading '{node.heading}': {exc}") from exc
                # Child headings follow the informative text.
                build(node.children, heading.id, 1 if node.text is not None else 0)

        build(skeleton, None, 0)
        return result

    def _skeleton_from_module(self, project: Project, module: Module) -> list[SkeletonNode]:
        """A module's headings as a heading skeleton (``[]`` without an Object Type attribute).

        Requirements whose Object Type reads "Heading" are taken in tree
        order, nested as they are; a heading under a non-heading is left out
        (with its subtree). A heading's first child of Object Type
        Information becomes its ``text``. Rich text is reduced to plain text.
        """
        object_type = self._report_attribute(project, module, "object_type")
        if object_type is None:
            return []
        children: dict[str | None, list[Requirement]] = {}
        for requirement in self.store.load_all_requirements(project.id, module.id):
            children.setdefault(requirement.parent_id, []).append(requirement)
        for siblings in children.values():
            siblings.sort(key=lambda r: (r.order, r.id))

        def is_type(requirement: Requirement, word: str) -> bool:
            value = requirement.attributes.get(object_type.id)
            return isinstance(value, str) and self._report_key(value) == word

        def text_of(requirement: Requirement) -> str:
            value = requirement.attributes.get("object_text")
            plain = richtext.to_plain(value) if isinstance(value, str) else ("" if value is None else str(value))
            return str(plain).strip()

        count = 0
        root: list[SkeletonNode] = []
        # Iterative depth-first walk: requirement trees can nest deeply.
        stack: list[tuple[Requirement, list[SkeletonNode]]] = [
            (r, root) for r in reversed(children.get(None, [])) if is_type(r, "heading")
        ]
        while stack:
            requirement, siblings = stack.pop()
            count += 1
            if count > MAX_SKELETON_NODES:
                raise ValueError(
                    f"Module '{module.id}' has more than {MAX_SKELETON_NODES} headings; too many for a template"
                )
            kids = children.get(requirement.id, [])
            text = text_of(kids[0]) if kids and is_type(kids[0], "information") else None
            node = SkeletonNode(heading=text_of(requirement) or "Untitled", text=text or None)
            siblings.append(node)
            stack.extend((k, node.children) for k in reversed(kids) if is_type(k, "heading"))
        return root

    def _module_template_check(self, template: ModuleTemplate) -> dict[str, Any]:
        """Dry-run a module template on an empty project; what it would add (``ValueError`` if invalid)."""
        project = Project(id=_TEMPLATE_CHECK_PROJECT_ID, name=template.name, attributes=list(BUILTIN_ATTRIBUTES))
        project.attributes = [replace(a) for a in project.attributes]
        return self._apply_module_template(project, template, None, None, None, check_only=True)

    def _apply_module_template(
        self,
        project: Project,
        template: ModuleTemplate,
        name: Any,
        prefix: Any,
        description: Any,
        check_only: bool = False,
    ) -> dict[str, Any]:
        """Build the module, merged attributes, view and skeleton of ``template`` for ``project`` (in memory).

        ``name``/``prefix``/``description`` override the template's (None:
        the template's). Returns ``{"module", "requirements", "added",
        "extended", "view_id"}``; ``project`` is changed in place (attributes,
        views, id counters). Nothing is written.
        """
        if name is None:
            name = template.module.name
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Module name must not be empty")
        name = name.strip()
        if prefix is None:
            prefix = template.module.prefix
        if not isinstance(prefix, str):
            raise ValueError("Module prefix must be a string")
        validate_prefix(prefix)
        if description is None:
            description = template.module.description
        if not isinstance(description, str):
            raise ValueError("Module description must be a string")
        module_type = self._checked_module_type(template.module.type)
        if check_only:
            module_id = _unique_slug(name, lambda c: False, fallback="module")
        else:
            module_id = _unique_slug(
                name,
                lambda c: self.store.module_exists(project.id, c),
                fallback="module",
                max_length=self.store.max_name_length(),
            )
        module = Module(id=module_id, name=name, prefix=prefix, description=description, type=module_type)
        merged = self._merge_template_attributes(project, module, template.attributes)
        view_id = self._merge_template_view(project, template.view, merged["id_map"])
        if check_only:
            def allocate() -> str:
                counter = project.id_counters.get(prefix, 0) + 1
                project.id_counters[prefix] = counter
                return f"{prefix}-{counter:04d}"
        else:
            def allocate() -> str:
                return self._allocate_requirement_id(project, module)
        requirements = self._skeleton_requirements(project, module, template.skeleton, allocate)
        return {
            "module": module,
            "requirements": requirements,
            "added": merged["added"],
            "extended": merged["extended"],
            "view_id": view_id,
        }

    @staticmethod
    def _module_template_summary(template: ModuleTemplate) -> dict[str, Any]:
        return {
            "id": template.id,
            "source": template.source,
            "name": template.name,
            "description": template.description,
            "module": {
                "name": template.module.name,
                "prefix": template.module.prefix,
                "description": template.module.description,
                "type": template.module.type or DEFAULT_MODULE_TYPE,
            },
            "attributes": [a.name for a in template.attributes],
            "view": template.view.name if template.view is not None else None,
            "headings": skeleton_size(template.skeleton),
            "error": None,
        }

    def list_module_templates(self) -> list[dict[str, Any]]:
        """Built-in module templates, then user ones, each sorted by name.

        Like ``list_templates``: every module template is fully validated
        (a dry run on an empty project) and an invalid one is still listed,
        with the reason in ``error``.
        """
        sources = (
            (BUILTIN_SOURCE, list_builtin_module_template_ids(self.builtin_templates_dir)),
            (USER_SOURCE, self.store.list_user_module_template_ids()),
        )
        result: list[dict[str, Any]] = []
        for source, local_ids in sources:
            entries: list[dict[str, Any]] = []
            for local_id in local_ids:
                template_id = make_template_id(source, local_id)
                text: str | None = None
                try:
                    text = self._read_module_template_text(source, local_id)
                    template = parse_module_template_text(text, template_id)
                    self._module_template_check(template)
                    entries.append(self._module_template_summary(template))
                except (OSError, ValueError) as exc:
                    logger.debug("Module template %s is invalid: %s", template_id, exc)
                    entries.append(
                        {
                            "id": template_id,
                            "source": source,
                            "name": self._fallback_template_name(text, local_id),
                            "description": "",
                            "module": None,
                            "attributes": [],
                            "view": None,
                            "headings": 0,
                            "error": str(exc),
                        }
                    )
            entries.sort(key=lambda e: (e["name"].casefold(), e["id"]))
            result.extend(entries)
        return result

    @_records_history
    def create_module_from_template(
        self,
        project_id: str,
        template_id: Any,
        name: Any = None,
        prefix: Any = None,
        description: Any = None,
        folder_id: Any = None,
    ) -> dict[str, Any]:
        """Add a module from a module template to an existing project, with its heading skeleton.

        ``name``/``prefix``/``description`` override the template's (None:
        the template's); ``folder_id`` places the module like
        ``create_module``. The template's attributes are merged by name
        (``_merge_template_attributes``: existing attributes are never
        changed except to apply to the new module; missing ones are added
        optional and scoped to it), its view is added unless the name is
        taken, and its skeleton becomes heading requirements with
        server-allocated ids. Everything is validated before anything is
        written; the writes (module file, requirement files, project.json)
        form one history commit.

        Returns ``{"module", "added_attributes", "extended_attributes",
        "view_id", "headings"}``.
        """
        project = self.store.load_project(project_id)
        template = self._load_module_template(template_id)
        tree = self._module_tree(project)
        container = self._folder_container(project, tree, folder_id)
        try:
            applied = self._apply_module_template(project, template, name, prefix, description)
        except ValueError as exc:
            raise ValueError(f"Module template '{template_id}' cannot be added: {exc}") from exc
        module: Module = applied["module"]
        container.append(module.id)
        project.module_order = tree
        self._sort_attribute_scopes(project, module_tree.module_ids(tree))
        self.store.save_module(project_id, module)
        # Requirement files, then project.json (attributes, tree, counters, view).
        self._write_new_requirements(project, module.id, applied["requirements"])
        return {
            "module": module,
            "added_attributes": applied["added"],
            "extended_attributes": applied["extended"],
            "view_id": applied["view_id"],
            "headings": len(applied["requirements"]),
        }

    def save_module_as_template(
        self, project_id: str, module_id: str, name: Any, description: Any = "", view_id: Any = None
    ) -> dict[str, Any]:
        """Write a module's structure as a new user module template (``user:<slug>``).

        Saves the module's name, prefix, description and type, the
        non-system attributes that apply to it (except the built-in
        ``object_text``; required is not kept: merged attributes are always
        optional), the view ``view_id`` if given (references to attributes
        not saved are dropped) and its headings as a skeleton
        (``_skeleton_from_module``). No requirements, link configurations or
        cross-project configuration. The result is validated like a loaded
        module template before it is written; a name used by any module
        template (case-insensitive) raises ``ConflictError``.
        """
        check_permission(ACTION_LIBRARY_WRITE)
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Template name must not be empty")
        name = name.strip()
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ValueError("Template description must be a string")
        view: View | None = None
        if view_id is not None:
            view = next((v for v in project.views if v.id == view_id), None)
            if view is None:
                raise FileNotFoundError(f"View '{view_id}' not found in project '{project_id}'")

        for entry in self.list_module_templates():
            if entry["name"].casefold() == name.casefold():
                raise ConflictError(f"A module template named '{entry['name']}' already exists ({entry['id']})")

        builtin_ids = {a.id for a in BUILTIN_ATTRIBUTES}
        attributes = [
            ModuleTemplateAttribute(
                id=a.id,
                name=a.name,
                type=a.type,
                default=a.default,
                choices=list(a.choices) if isinstance(a.choices, list) else a.choices,
            )
            for a in project.attributes
            if not a.system and a.id not in builtin_ids and self._attribute_applies(a, module.id, module.type)
        ]
        kept = builtin_ids | {a.id for a in attributes}
        template_view = (
            self._template_view(
                view, [a for a in view.attribute_ids if a in kept], {a: a for a in view.attribute_ids if a in kept}
            )
            if view is not None
            else None
        )
        local_id = _unique_slug(name, self.store.user_module_template_path_exists, fallback="template")
        template_id = make_template_id(USER_SOURCE, local_id)
        template = ModuleTemplate(
            id=template_id,
            source=USER_SOURCE,
            name=name,
            description=description,
            module=ModuleTemplateModule(
                name=module.name, prefix=module.prefix, description=module.description, type=module.type
            ),
            attributes=attributes,
            view=template_view,
            skeleton=self._skeleton_from_module(project, module),
        )
        data = template.to_dict()
        try:
            # Round-trip through the loader (as JSON text, so the depth
            # limit applies) and dry-run it, so the file is always usable.
            checked = parse_module_template_text(json.dumps(data), template_id)
            self._module_template_check(checked)
        except ValueError as exc:
            raise ValueError(f"Module '{module_id}' cannot be saved as a template: {exc}") from exc
        self.store.save_user_module_template(local_id, data)
        return self._module_template_summary(template)

    # --- reports ---------------------------------------------------------------
    #
    # Read-only views computed from the stored modules, requirements and link
    # pairs: the traceability matrix, the verification report and the test
    # execution report (see docs/ARCHITECTURE.md "Reports"). Nothing is
    # written and nothing is broadcast.

    @staticmethod
    def _report_key(text: str) -> str:
        """``text`` lower-cased with every run of non-alphanumerics as one space."""
        return " ".join(re.sub(r"[^0-9a-z]+", " ", str(text).lower()).split())

    @classmethod
    def _report_attribute(
        cls, project: Project, module: Module, role: str
    ) -> AttributeDefinition | None:
        """The attribute playing ``role`` (see ``REPORT_ATTRIBUTE_ROLES``) in ``module``.

        Only non-system attributes that apply to the module and have one of
        the role's types are considered. The first role name (in priority
        order) matching an attribute's name or id (both compared by
        ``_report_key``) wins; a role with ``contains`` words then takes the
        first attribute (project order) whose name contains one of them.
        """
        spec = REPORT_ATTRIBUTE_ROLES[role]
        candidates = [
            a for a in project.attributes
            if not a.system and a.type in spec["types"] and cls._attribute_applies(a, module.id, module.type)
        ]
        for wanted in spec["names"]:
            for attr in candidates:
                if wanted in (cls._report_key(attr.name), cls._report_key(attr.id)):
                    return attr
        for word in spec.get("contains", ()):
            for attr in candidates:
                if word in cls._report_key(attr.name).split():
                    return attr
        if spec.get("only_of_type"):
            # e.g. the one date attribute of an execution module, whatever its name
            if len(candidates) == 1:
                return candidates[0]
        return None

    @classmethod
    def _report_result(cls, value: Any) -> str:
        """A stored result value as ``passed | failed | blocked | not_run | not_applicable``."""
        key = cls._report_key(value) if isinstance(value, str) else ""
        for category, words in REPORT_RESULT_WORDS.items():
            if key in words:
                return category
        return "not_run"

    @staticmethod
    def _report_text(requirement: Requirement) -> str:
        value = requirement.attributes.get("object_text")
        if value is None:
            return ""
        text = richtext.to_plain(value) if isinstance(value, str) else str(value)
        return " ".join(str(text).split())

    def _report_items(self, project: Project, module: Module) -> tuple[list[dict[str, Any]], int]:
        """The module's reportable objects in tree order, and how many were left out.

        Objects whose Object Type (an enum found like any report attribute)
        is one of ``REPORT_EXCLUDED_OBJECT_TYPES`` (headings, information,
        definitions...) are not requirements or test cases and are left out
        of every report. Each item: ``id``, ``text`` (plain), ``level``
        (nesting depth) and the requirement itself under ``requirement``.
        """
        from .ordering import tree_order

        object_type = self._report_attribute(project, module, "object_type")
        requirements = self.store.load_all_requirements(project.id, module.id)
        by_id = {r.id: r for r in requirements}
        items: list[dict[str, Any]] = []
        excluded = 0
        for requirement in tree_order(requirements):
            if object_type is not None:
                kind = requirement.attributes.get(object_type.id)
                if isinstance(kind, str) and self._report_key(kind) in REPORT_EXCLUDED_OBJECT_TYPES:
                    excluded += 1
                    continue
            level, parent, seen = 0, requirement.parent_id, {requirement.id}
            while parent in by_id and parent not in seen:
                seen.add(parent)
                level += 1
                parent = by_id[parent].parent_id
            items.append({
                "id": requirement.id,
                "text": self._report_text(requirement),
                "level": level,
                "requirement": requirement,
            })
        return items, excluded

    @staticmethod
    def _report_module(module: Module) -> dict[str, Any]:
        return {"id": module.id, "name": module.name, "type": module.type}

    def _report_links(
        self, project: Project, from_id: str, to_id: str
    ) -> dict[str, set[str]]:
        """Requirement id in module ``from_id`` -> linked ids in module ``to_id``.

        Every link configuration joining the two modules counts, in either
        direction (a configuration from a module to itself counts once).
        """
        linked: dict[str, set[str]] = {}
        for config in self._local_configs(project):
            ends = (config.source_module_id, config.target_module_id)
            if ends == (from_id, to_id):
                forward = True
            elif ends == (to_id, from_id):
                forward = False
            else:
                continue
            for pair in self.store.load_link_pairs(project.id, config.id):
                a, b = (pair.source_id, pair.target_id) if forward else (pair.target_id, pair.source_id)
                linked.setdefault(a, set()).add(b)
        return linked

    def _report_linked_modules(self, project: Project, module: Module, module_type: str) -> list[Module]:
        """Modules of ``module_type`` joined to ``module`` by a link configuration (display order)."""
        joined: set[str] = set()
        for config in self._local_configs(project):
            if config.source_module_id == module.id:
                joined.add(config.target_module_id)
            if config.target_module_id == module.id:
                joined.add(config.source_module_id)
        return [
            m for m in self.list_modules(project.id)
            if m.id in joined and m.id != module.id and m.type == module_type
        ]

    def _execution_reader(self, project: Project, module: Module) -> dict[str, Any]:
        """How the execution records of ``module`` are read: its role attributes."""
        roles = {
            role: self._report_attribute(project, module, role)
            for role in ("result", "execution_date", "executed_by", "version", "evidence")
        }
        return {
            "module": module,
            "roles": roles,
            "attributes": {
                role: ({"id": attr.id, "name": attr.name} if attr else None) for role, attr in roles.items()
            },
        }

    def _execution_record(
        self, reader: dict[str, Any], item: dict[str, Any], module_index: int, item_index: int
    ) -> dict[str, Any]:
        """One execution record (an object of a test_execution module) as report data."""
        values = item["requirement"].attributes
        roles = reader["roles"]

        def value(role: str) -> Any:
            attr = roles[role]
            raw = values.get(attr.id) if attr else None
            return None if raw is None or raw == "" else raw

        raw_result = value("result")
        execution_date = value("execution_date")
        if not self._is_iso_date(execution_date):
            execution_date = None
        return {
            "id": item["id"],
            "module_id": reader["module"].id,
            "text": item["text"],
            "result": self._report_result(raw_result),
            "result_value": raw_result,
            "execution_date": execution_date,
            "executed_by": value("executed_by"),
            "version": value("version"),
            "evidence": value("evidence"),
            # sort key of "latest": date, then module order, then tree order
            "_key": (execution_date or "", module_index, item_index),
        }

    @staticmethod
    def _latest_execution(records: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The latest *executed* record (Passed, Failed or Blocked), or None.

        Latest = greatest Execution Date (a record without one sorts before
        every dated record), then the later module in the project's module
        order, then the later position in that module's tree order. Records
        still Not Run (or Not Applicable) are planned, not executed, and are
        never the latest execution.
        """
        executed = [r for r in records if r["result"] in REPORT_EXECUTED_RESULTS]
        return max(executed, key=lambda r: r["_key"]) if executed else None

    @staticmethod
    def _public_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
        if record is None:
            return None
        return {k: v for k, v in record.items() if not k.startswith("_")}

    @staticmethod
    def _percent(part: int, whole: int) -> float:
        return round(100.0 * part / whole, 1) if whole else 0.0

    def traceability_matrix(self, project_id: str, config_ids: Any) -> dict[str, Any]:
        """A traceability matrix over one link configuration or a chain of them.

        ``config_ids`` lists link configuration ids; each one after the first
        must share a module with the end of the chain so far (e.g. Satisfies
        StRS, Satisfies SyRS, Verifies SwRS, Executes SwTest gives StRS ->
        SyRS -> SwRS -> SwTest -> SwTestExec). The chain starts at the
        first configuration's *target* module (links point downstream ->
        upstream: a test case verifies a requirement), unless the second
        configuration joins that module, in which case it starts at the
        first configuration's source module.

        Returns the module sequence (each with its reportable items), one
        step per configuration (grid links row -> column, covered rows,
        uncovered rows, orphan columns), the trace of every first-module item
        through the whole chain, totals, and the unverified items: items of a
        ``requirements`` module of the chain that reach no item of a later
        ``test_cases`` module.
        """
        project = self.store.load_project(project_id)
        if not isinstance(config_ids, list) or not config_ids or not all(isinstance(c, str) for c in config_ids):
            raise ValueError("Pick at least one link configuration")
        if len(config_ids) > MAX_TRACE_CHAIN:
            raise ValueError(f"A traceability chain has at most {MAX_TRACE_CHAIN} link configurations")
        if len(set(config_ids)) != len(config_ids):
            raise ValueError("A link configuration appears twice in the chain")
        configs = [self._get_link_configuration(project, cid) for cid in config_ids]
        for config in configs:
            if config.target_project_id is not None:
                raise ValueError(
                    f"Link configuration '{config.name}' links to another project; cross-project "
                    "configurations cannot be part of a traceability matrix yet"
                )
        module_by_id = {m.id: m for m in self.list_modules(project_id)}

        first = configs[0]
        chain = [first.target_module_id, first.source_module_id]
        if len(configs) > 1:
            nxt = configs[1]
            if chain[1] not in (nxt.source_module_id, nxt.target_module_id) and chain[0] in (
                nxt.source_module_id, nxt.target_module_id
            ):
                chain.reverse()
        # (config, row side is the config's target?) per step
        orient: list[bool] = [first.target_module_id == chain[0]]
        for config in configs[1:]:
            end = chain[-1]
            if config.target_module_id == end:
                chain.append(config.source_module_id)
                orient.append(True)
            elif config.source_module_id == end:
                chain.append(config.target_module_id)
                orient.append(False)
            else:
                raise ValueError(
                    f"Link configuration '{config.name}' does not continue the chain: "
                    f"it does not join module '{module_by_id[end].name if end in module_by_id else end}'"
                )

        for module_id in chain:
            if module_id not in module_by_id:
                raise FileNotFoundError(f"Module '{module_id}' not found")
        modules = [module_by_id[mid] for mid in chain]

        items_of: list[list[dict[str, Any]]] = []
        excluded_of: list[int] = []
        for module in modules:
            items, excluded = self._report_items(project, module)
            items_of.append(items)
            excluded_of.append(excluded)
        ids_of = [[it["id"] for it in items] for items in items_of]
        id_sets = [set(ids) for ids in ids_of]

        steps: list[dict[str, Any]] = []
        forward: list[dict[str, set[str]]] = []
        for index, (config, row_is_target) in enumerate(zip(configs, orient)):
            rows, columns = ids_of[index], ids_of[index + 1]
            row_set, column_set = id_sets[index], id_sets[index + 1]
            links: dict[str, set[str]] = {}
            for pair in self.store.load_link_pairs(project_id, config.id):
                row, column = (pair.target_id, pair.source_id) if row_is_target else (pair.source_id, pair.target_id)
                if row in row_set and column in column_set:
                    links.setdefault(row, set()).add(column)
            forward.append(links)
            column_index = {cid: i for i, cid in enumerate(columns)}
            linked_columns = {c for targets in links.values() for c in targets}
            uncovered = [r for r in rows if not links.get(r)]
            steps.append({
                "config": config.to_dict(),
                "from_module_id": chain[index],
                "to_module_id": chain[index + 1],
                "links": [
                    [row, column]
                    for row in rows
                    for column in sorted(links.get(row, ()), key=lambda c: column_index[c])
                ],
                "total": len(rows),
                "covered": len(rows) - len(uncovered),
                "covered_percent": self._percent(len(rows) - len(uncovered), len(rows)),
                "uncovered": uncovered,
                "orphans": [c for c in columns if c not in linked_columns],
            })

        def reach(start: str, from_index: int) -> list[set[str]]:
            """Items reached from ``start`` (in module ``from_index``) in each later module."""
            reached: list[set[str]] = []
            frontier = {start}
            for links in forward[from_index:]:
                frontier = {t for s in frontier for t in links.get(s, ())}
                reached.append(frontier)
            return reached

        trace = []
        for item_id in ids_of[0]:
            reached = reach(item_id, 0)
            trace.append({
                "id": item_id,
                "reached": [[i for i in ids_of[k + 1] if i in ids] for k, ids in enumerate(reached)],
                "complete": bool(reached[-1]),
            })

        unverified = []
        for index, module in enumerate(modules):
            later_tests = [k for k in range(index + 1, len(modules)) if modules[k].type == "test_cases"]
            if module.type != "requirements" or not later_tests:
                continue
            test_index = later_tests[0]
            missing = [
                item_id for item_id in ids_of[index]
                if not reach(item_id, index)[test_index - index - 1]
            ]
            unverified.append({
                "module_id": module.id,
                "test_module_id": modules[test_index].id,
                "total": len(ids_of[index]),
                "ids": missing,
            })

        traced = sum(1 for row in trace if row["complete"])
        return {
            "project_id": project_id,
            "modules": [
                {
                    **self._report_module(module),
                    "items": [{k: it[k] for k in ("id", "text", "level")} for it in items_of[i]],
                    "excluded": excluded_of[i],
                }
                for i, module in enumerate(modules)
            ],
            "steps": steps,
            "trace": trace,
            "summary": {
                "total": len(ids_of[0]),
                "covered": steps[0]["covered"],
                "covered_percent": steps[0]["covered_percent"],
                "traced": traced,
                "traced_percent": self._percent(traced, len(ids_of[0])),
            },
            "unverified": unverified,
        }

    def _test_case_results(
        self, project: Project, test_module: Module, exec_modules: list[Module]
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        """Execution records per test case id of ``test_module``, and the readers used."""
        records: dict[str, list[dict[str, Any]]] = {}
        readers = []
        order = {m.id: i for i, m in enumerate(self.list_modules(project.id))}
        for exec_module in exec_modules:
            reader = self._execution_reader(project, exec_module)
            readers.append(reader)
            links = self._report_links(project, exec_module.id, test_module.id)
            items, _ = self._report_items(project, exec_module)
            for position, item in enumerate(items):
                targets = links.get(item["id"])
                if not targets:
                    continue
                record = self._execution_record(reader, item, order.get(exec_module.id, 0), position)
                for test_id in targets:
                    records.setdefault(test_id, []).append(record)
        return records, readers

    def _report_data_source(self, project: Project) -> dict[str, Any]:
        """Which data a report was computed from (shown in its header and exports).

        Reports are computed from the project's current files: ``commit`` is
        the HEAD of its repository and ``pending_changes`` the data files
        changed since; ``baselines`` are the baselines tagged at that commit
        (the data then *is* that baseline when nothing is pending) and
        ``latest_baseline`` the newest one. ``history`` is the git state
        (``off`` when history is turned off). ``locked``: the project's lock.
        A git problem leaves the fields empty; it never fails the report.
        """
        source: dict[str, Any] = {
            "history": "off",
            "commit": None,
            "pending_changes": 0,
            "baselines": [],
            "latest_baseline": None,
            "locked": project.locked.to_dict() if project.locked is not None else None,
        }
        if not self.history.enabled:
            return source
        try:
            project_dir = self.store.project_path(project.id)
            status = self.history.status(project_dir)
            source["history"] = status.get("state")
            source["commit"] = (status.get("head") or {}).get("commit")
            source["pending_changes"] = status.get("pending_changes") or 0
            if source["commit"]:
                baselines = self.history.list_baselines(project_dir).get("baselines") or []
                source["baselines"] = [b["name"] for b in baselines if b.get("commit") == source["commit"]]
                if baselines:
                    source["latest_baseline"] = {"name": baselines[0]["name"], "commit": baselines[0]["commit"]}
        except (GitError, OSError, ValueError, KeyError) as exc:
            logger.debug("No data source for the report of %s: %s", project.id, exc)
        return source

    def verification_report(self, project_id: str, module_id: str) -> dict[str, Any]:
        """``_verification_report`` plus ``data_source`` (``_report_data_source``)."""
        report = self._verification_report(project_id, module_id)
        return {**report, "data_source": self._report_data_source(self.store.load_project(project_id))}

    def _verification_report(self, project_id: str, module_id: str) -> dict[str, Any]:
        """Verification status of every requirement of a ``requirements`` module.

        Covering test cases: objects of ``test_cases`` modules linked to the
        requirement directly (any link configuration between the modules,
        either direction). A test case's result is its latest execution
        (``_latest_execution``) among the objects of ``test_execution``
        modules linked to it; a test case without any execution record falls
        back to its own result attribute (e.g. RBC "Test Result"), if it has
        one and it says Passed, Failed or Blocked.

        Status: ``not_covered`` (no covering test case), ``failed`` (a
        covering test case's result is Failed), ``not_run`` (otherwise, a
        covering test case is Blocked or has no execution), ``verified``
        (every covering test case Passed).
        """
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        if module.type != "requirements":
            raise ValueError(
                f"Module '{module.name}' is a {module.type} module; "
                "the verification report is for a requirements module"
            )
        items, excluded = self._report_items(project, module)
        test_modules = self._report_linked_modules(project, module, "test_cases")

        covering: dict[str, list[dict[str, Any]]] = {}
        exec_modules_seen: dict[str, dict[str, Any]] = {}
        for module_position, test_module in enumerate(test_modules):
            links = self._report_links(project, module.id, test_module.id)
            exec_modules = self._report_linked_modules(project, test_module, "test_execution")
            records, readers = self._test_case_results(project, test_module, exec_modules)
            for reader in readers:
                exec_modules_seen.setdefault(reader["module"].id, {
                    **self._report_module(reader["module"]),
                    "test_module_id": test_module.id,
                    "attributes": reader["attributes"],
                })
            own_result = self._report_attribute(project, test_module, "result")
            tests, _ = self._report_items(project, test_module)
            test_by_id = {t["id"]: (position, t) for position, t in enumerate(tests)}
            for requirement_id, test_ids in links.items():
                for test_id in test_ids:
                    if test_id not in test_by_id:
                        continue
                    position, test = test_by_id[test_id]
                    test_records = records.get(test_id, [])
                    latest = self._latest_execution(test_records)
                    source = "execution" if latest else None
                    result = latest["result"] if latest else "not_run"
                    if not test_records and own_result is not None:
                        own = self._report_result(test["requirement"].attributes.get(own_result.id))
                        if own in REPORT_EXECUTED_RESULTS:
                            result, source = own, "test_case"
                    covering.setdefault(requirement_id, []).append({
                        "id": test_id,
                        "module_id": test_module.id,
                        "text": test["text"],
                        "result": result,
                        "source": source,
                        "executions": len(test_records),
                        "latest": self._public_record(latest),
                        "_order": (module_position, position),
                    })

        rows = []
        totals = {status: 0 for status in VERIFICATION_STATUSES}
        for item in items:
            tests = sorted(covering.get(item["id"], []), key=lambda t: t["_order"])
            results = {t["result"] for t in tests}
            if not tests:
                status = "not_covered"
            elif "failed" in results:
                status = "failed"
            elif results == {"passed"}:
                status = "verified"
            else:
                status = "not_run"
            totals[status] += 1
            rows.append({
                "id": item["id"],
                "text": item["text"],
                "level": item["level"],
                "status": status,
                "tests": [{k: v for k, v in t.items() if not k.startswith("_")} for t in tests],
            })
        total = len(rows)
        return {
            "project_id": project_id,
            "module": self._report_module(module),
            "test_modules": [self._report_module(m) for m in test_modules],
            "execution_modules": list(exec_modules_seen.values()),
            "requirements": rows,
            "totals": {"total": total, **totals, "excluded": excluded},
            "percent": {status: self._percent(totals[status], total) for status in VERIFICATION_STATUSES},
        }

    def execution_report(self, project_id: str, module_id: str) -> dict[str, Any]:
        """``_execution_report`` plus ``data_source`` (``_report_data_source``)."""
        report = self._execution_report(project_id, module_id)
        return {**report, "data_source": self._report_data_source(self.store.load_project(project_id))}

    def _execution_report(self, project_id: str, module_id: str) -> dict[str, Any]:
        """The test execution report of a ``test_execution`` module (one campaign).

        Planned test cases: the objects of the ``test_cases`` modules linked
        to this module. Each one's result in this campaign is its latest
        execution among the module's records linked to it (not_planned when
        no record links to it, not_run when its records are all still Not
        Run). Also: record counts per result, the failed and blocked test
        cases with the requirements they verify (objects of ``requirements``
        modules linked to the test case), records linked to no test case, and
        the distinct builds/versions under test, executors and the execution
        date range of the executed records.
        """
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        if module.type != "test_execution":
            raise ValueError(
                f"Module '{module.name}' is a {module.type} module; "
                "the test execution report is for a test_execution module"
            )
        reader = self._execution_reader(project, module)
        items, excluded = self._report_items(project, module)
        test_modules = self._report_linked_modules(project, module, "test_cases")
        links_by_test_module = {tm.id: self._report_links(project, module.id, tm.id) for tm in test_modules}

        records = []
        by_test: dict[str, list[dict[str, Any]]] = {}
        for position, item in enumerate(items):
            record = self._execution_record(reader, item, 0, position)
            test_ids = []
            for test_module in test_modules:
                test_ids.extend(sorted(links_by_test_module[test_module.id].get(item["id"], ())))
            record["test_case_ids"] = test_ids
            records.append(record)
            for test_id in test_ids:
                by_test.setdefault(test_id, []).append(record)

        record_counts = {r: 0 for r in REPORT_RESULTS}
        for record in records:
            record_counts[record["result"]] += 1

        test_cases = []
        test_counts = {r: 0 for r in EXECUTION_TEST_STATUSES}
        problems = []
        for test_module in test_modules:
            tests, _ = self._report_items(project, test_module)
            requirement_modules = self._report_linked_modules(project, test_module, "requirements")
            req_links = [
                (rm, self._report_links(project, test_module.id, rm.id), {
                    it["id"]: it["text"] for it in self._report_items(project, rm)[0]
                })
                for rm in requirement_modules
            ]
            for test in tests:
                test_records = by_test.get(test["id"], [])
                latest = self._latest_execution(test_records)
                if latest:
                    status = latest["result"]
                elif test_records:
                    status = "not_run"
                else:
                    status = "not_planned"
                test_counts[status] += 1
                entry = {
                    "id": test["id"],
                    "module_id": test_module.id,
                    "text": test["text"],
                    "status": status,
                    "records": [r["id"] for r in test_records],
                    "latest": self._public_record(latest),
                }
                test_cases.append(entry)
                if status in ("failed", "blocked"):
                    affected = []
                    for rm, links, texts in req_links:
                        affected.extend(
                            {"id": rid, "module_id": rm.id, "text": texts[rid]}
                            for rid in sorted(links.get(test["id"], ()))
                            if rid in texts
                        )
                    problems.append({**entry, "requirements": affected})

        executed_records = [r for r in records if r["result"] in REPORT_EXECUTED_RESULTS]
        dates = sorted(r["execution_date"] for r in executed_records if r["execution_date"])

        def distinct(key: str) -> list[str]:
            return sorted({str(r[key]) for r in records if r[key] is not None})

        planned = len(test_cases) - test_counts["not_planned"]
        executed = sum(test_counts[s] for s in REPORT_EXECUTED_RESULTS)
        return {
            "project_id": project_id,
            "module": self._report_module(module),
            "attributes": reader["attributes"],
            "test_modules": [self._report_module(m) for m in test_modules],
            "records": [self._public_record(r) for r in records],
            "record_counts": {"total": len(records), **record_counts, "excluded": excluded},
            "test_cases": test_cases,
            "test_counts": {
                "total": len(test_cases),
                "planned": planned,
                "executed": executed,
                "executed_percent": self._percent(executed, planned),
                "pass_percent": self._percent(test_counts["passed"], executed),
                **test_counts,
            },
            "problems": problems,
            "unlinked_records": [r["id"] for r in records if not r["test_case_ids"]],
            "versions": distinct("version"),
            "executors": distinct("executed_by"),
            "date_range": [dates[0], dates[-1]] if dates else None,
        }

    def export_traceability(self, project_id: str, config_ids: Any, fmt: str) -> bytes | str:
        from .report_export import traceability_to_csv, traceability_to_xlsx

        report = self.traceability_matrix(project_id, config_ids)
        project = self.store.load_project(project_id)
        if fmt == "csv":
            return traceability_to_csv(report)
        if fmt == "xlsx":
            return traceability_to_xlsx(report, project.name)
        raise ValueError(f"Unknown traceability export format '{fmt}'; expected csv or xlsx")

    def export_verification_report(self, project_id: str, module_id: str, fmt: str) -> bytes:
        from .report_export import verification_to_docx, verification_to_xlsx

        if fmt not in ("docx", "xlsx"):
            raise ValueError(f"Unknown report export format '{fmt}'; expected docx or xlsx")
        report = self.verification_report(project_id, module_id)
        project = self.store.load_project(project_id)
        writer = verification_to_docx if fmt == "docx" else verification_to_xlsx
        return writer(report, project.name)

    def export_execution_report(self, project_id: str, module_id: str, fmt: str) -> bytes:
        from .report_export import execution_to_docx, execution_to_xlsx

        if fmt not in ("docx", "xlsx"):
            raise ValueError(f"Unknown report export format '{fmt}'; expected docx or xlsx")
        report = self.execution_report(project_id, module_id)
        project = self.store.load_project(project_id)
        writer = execution_to_docx if fmt == "docx" else execution_to_xlsx
        return writer(report, project.name)

    def verification_statuses(self, project_id: str, module_id: str) -> dict[str, Any]:
        """The computed, read-only "Verification status" of every requirement of a requirements module.

        The same status as ``verification_report`` (``verified | failed |
        not_run | not_covered``) per requirement id, for the table column and
        the detail panel. Objects the reports leave out (headings...) have
        none. Never stored, never exported, not an attribute definition: it
        cannot be written, imported or put in a view's stored attributes.
        """
        report = self._verification_report(project_id, module_id)
        return {
            "project_id": project_id,
            "module_id": module_id,
            "statuses": {row["id"]: row["status"] for row in report["requirements"]},
        }

    # --- test result import (JUnit XML, Robot Framework output.xml) ---------------
    #
    # Results of automated test runs become execution records (requirements)
    # of a test_execution module, each linked to the test case it executes.
    # See docs/ARCHITECTURE.md "Test result import".

    def _result_import_links(
        self, project: Project, module: Module
    ) -> list[tuple[LinkConfiguration, Module, bool]]:
        """The link configurations joining ``module`` to a test_cases module.

        Each as ``(configuration, test cases module, records are the link
        source)``; new configurations always go test_execution -> test_cases,
        but a stored one the other way round works too.
        """
        modules = {m.id: m for m in self.list_modules(project.id)}
        joined: list[tuple[LinkConfiguration, Module, bool]] = []
        for config in self._local_configs(project):
            if config.source_module_id == module.id and config.target_module_id != module.id:
                other, record_is_source = config.target_module_id, True
            elif config.target_module_id == module.id and config.source_module_id != module.id:
                other, record_is_source = config.source_module_id, False
            else:
                continue
            test_module = modules.get(other)
            if test_module is not None and test_module.type == "test_cases":
                joined.append((config, test_module, record_is_source))
        return joined

    @staticmethod
    def _checked_result_options(options: Any) -> dict[str, Any]:
        """The import options with blanks as None; ``execution_date`` must be a date."""
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise ValueError("Import options must be an object")
        checked: dict[str, Any] = {}
        for key in RESULT_IMPORT_OPTIONS:
            value = options.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Option '{key}' must be a string")
            checked[key] = value.strip() if isinstance(value, str) and value.strip() else None
        if checked["execution_date"] is not None and not ProjectService._is_iso_date(checked["execution_date"]):
            raise ValueError(f"Execution date '{checked['execution_date']}' is not a date (expected YYYY-MM-DD)")
        name = checked["file_name"] or "results.xml"
        checked["file_name"] = re.split(r"[\\/]", name)[-1][:200] or "results.xml"
        return checked

    @classmethod
    def _result_value(cls, attr: AttributeDefinition, category: str) -> str | None:
        """The value of result attribute ``attr`` meaning ``category``, or None when it has none."""
        if attr.type == "enum":
            for choice in attr.choices or []:
                if isinstance(choice, str) and cls._report_result(choice) == category:
                    return choice
            return None
        return RESULT_TEXT_VALUES.get(category)

    def _test_results_plan(
        self, project_id: str, module_id: str, content: Any, options: Any
    ) -> dict[str, Any]:
        """Parse a result file and match it to test cases, writing nothing.

        Shared by ``preview_test_results`` and ``import_test_results``: the
        import writes exactly what the preview showed, or nothing when the
        plan has ``errors``.
        """
        from .test_results_import import MAX_TEST_RESULTS_IMPORT_BYTES, parse_test_results

        if not isinstance(content, (str, bytes)):
            raise ValueError("The result file content must be a string")
        if len(content) > MAX_TEST_RESULTS_IMPORT_BYTES:
            raise ValueError(
                f"The result file is too large: the limit is {MAX_TEST_RESULTS_IMPORT_BYTES // (1024 * 1024)} MiB"
            )
        opts = self._checked_result_options(options)
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        if module.type != "test_execution":
            raise ValueError(
                f"Module '{module.name}' is a {module.type} module; "
                "test results are imported into a test_execution module"
            )

        joined = self._result_import_links(project, module)
        if not joined:
            raise ValueError(
                f"Module '{module.name}' is not linked to a test cases module: "
                "add a link configuration from it to a test_cases module first"
            )
        if opts["config_id"] is None:
            config, test_module, record_is_source = joined[0]
        else:
            chosen = next((j for j in joined if j[0].id == opts["config_id"]), None)
            if chosen is None:
                self._get_link_configuration(project, opts["config_id"])  # 404 when unknown
                raise ValueError(
                    f"Link configuration '{opts['config_id']}' does not join module '{module.name}' "
                    "to a test cases module"
                )
            config, test_module, record_is_source = chosen

        match_options = [{"id": RESULT_MATCH_ID, "name": "Requirement id"}] + [
            {"id": a.id, "name": a.name}
            for a in project.attributes
            if not a.system and a.type in RESULT_MATCH_TYPES
            and self._attribute_applies(a, test_module.id, test_module.type)
        ]
        default_match = self._report_attribute(project, test_module, "test_id")
        match = opts["match_attribute"] or (default_match.id if default_match else RESULT_MATCH_ID)
        if match not in {o["id"] for o in match_options}:
            raise ValueError(
                f"Cannot match on '{match}': pick the requirement id or a string, integer or enum "
                f"attribute of module '{test_module.name}'"
            )

        file_format, results = parse_test_results(content)

        # Test cases by their match key (lower-cased, punctuation collapsed).
        tests, _ = self._report_items(project, test_module)
        by_key: dict[str, list[dict[str, Any]]] = {}
        for test in tests:
            raw = test["id"] if match == RESULT_MATCH_ID else test["requirement"].attributes.get(match)
            if raw is None or isinstance(raw, bool):
                continue
            key = self._report_key(str(raw))
            if key:
                by_key.setdefault(key, []).append(test)

        # Word counts of the keys by their first word: a partial match only tries the runs
        # of words that can equal a key (linear in the result's name, not cubic).
        key_lengths: dict[str, set[int]] = {}
        for key in by_key:
            words = key.split()
            key_lengths.setdefault(words[0], set()).add(len(words))

        def lookup(keys: list[str], partial: bool) -> dict[str, dict[str, Any]]:
            found: dict[str, dict[str, Any]] = {}
            for text in keys:
                tokens = self._report_key(text).split()
                if not partial:
                    spans = [" ".join(tokens)]
                else:
                    # every run of whole words: "SWT-0001 Login" contains "swt 0001"
                    spans = [
                        " ".join(tokens[i:i + length])
                        for i in range(len(tokens))
                        for length in sorted(key_lengths.get(tokens[i], ()))
                        if i + length <= len(tokens)
                    ]
                for span in spans:
                    for test in by_key.get(span, ()):
                        found.setdefault(test["id"], test)
            return found

        reader = self._execution_reader(project, module)
        roles: dict[str, AttributeDefinition | None] = reader["roles"]
        errors: list[str] = []
        warnings: list[str] = []
        result_attr = roles["result"]
        if result_attr is None:
            errors.append(
                f"Module '{module.name}' has no result attribute (e.g. 'Result') to store the test results in"
            )
        for role, label, value in (
            ("executed_by", "Executed by", opts["executed_by"]),
            ("version", "Build/version", opts["version"]),
        ):
            if value is not None and roles[role] is None:
                warnings.append(f"{label} is not stored: module '{module.name}' has no such attribute")
        if roles["execution_date"] is None:
            warnings.append(f"Execution dates are not stored: module '{module.name}' has no date attribute")
        if roles["evidence"] is None:
            warnings.append(f"Evidence is not stored: module '{module.name}' has no evidence/log attribute")

        today = date.today().isoformat()
        text_ids = {a.id for a in project.attributes if a.type == "text"}
        rows: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        seen_errors: set[str] = set()
        matched_tests: set[str] = set()
        for result in results:
            keys = result.keys()
            found = lookup(keys, partial=False)
            how = "exact" if found else None
            if not found:
                found = lookup(keys, partial=True)
                how = "partial" if found else None
            state = "matched" if len(found) == 1 else ("ambiguous" if found else "unmatched")
            test = next(iter(found.values())) if state == "matched" else None
            execution_date = opts["execution_date"] or result.date or today
            row = {
                "index": result.index,
                "name": result.name,
                "full_name": result.full_name,
                "status": result.status,
                "raw_status": result.raw_status,
                "message": result.message,
                "execution_date": execution_date,
                "state": state,
                "match": how if state == "matched" else None,
                "test_case_id": test["id"] if test else None,
                "candidates": sorted(found) if state == "ambiguous" else [],
            }
            rows.append(row)
            if test is None or result_attr is None:
                continue
            matched_tests.add(test["id"])

            result_value = self._result_value(result_attr, result.status)
            if result_value is None:
                problem = (
                    f"Result attribute '{result_attr.name}' has no value for {result.status.replace('_', ' ')} "
                    f"results (choices: {', '.join(map(str, result_attr.choices or []))})"
                )
                if problem not in seen_errors:
                    seen_errors.add(problem)
                    errors.append(problem)
                continue
            values: dict[str, Any] = {
                "object_text": result.name or result.full_name or test["id"],
                result_attr.id: result_value,
            }
            if roles["execution_date"] is not None:
                values[roles["execution_date"].id] = execution_date
            if roles["executed_by"] is not None and opts["executed_by"] is not None:
                values[roles["executed_by"].id] = opts["executed_by"]
            if roles["version"] is not None and opts["version"] is not None:
                values[roles["version"].id] = opts["version"]
            if roles["evidence"] is not None:
                values[roles["evidence"].id] = f"{opts['file_name']}: {result.full_name or result.name}"
            # Result files are plain text: never read a value as rich text markup.
            values = {k: richtext.from_plain(v) if k in text_ids and isinstance(v, str) else v for k, v in values.items()}
            for problem in self._attribute_problems(
                project, values, is_create=True, module_id=module.id, module_type=module.type
            ):
                if problem not in seen_errors:
                    seen_errors.add(problem)
                    errors.append(problem)
            records.append({"row": row, "values": values, "test_id": test["id"]})

        counts = {state: sum(1 for r in rows if r["state"] == state) for state in ("matched", "unmatched", "ambiguous")}
        if not errors and counts["matched"] == 0:
            errors.append(
                f"No test result matches a test case of module '{test_module.name}' by "
                f"{next(o['name'] for o in match_options if o['id'] == match)}"
            )
        if counts["ambiguous"]:
            warnings.append(
                f"{counts['ambiguous']} result(s) match several test cases and are skipped; "
                "pick a more specific match attribute"
            )
        if counts["unmatched"]:
            warnings.append(f"{counts['unmatched']} result(s) match no test case and are skipped")
        if len(errors) > MAX_RESULT_IMPORT_ERRORS:
            hidden = len(errors) - MAX_RESULT_IMPORT_ERRORS
            errors = errors[:MAX_RESULT_IMPORT_ERRORS] + [f"... and {hidden} more problem(s)"]

        return {
            "project": project,
            "module": module,
            "config": config,
            "test_module": test_module,
            "record_is_source": record_is_source,
            "records": records,
            "public": {
                "project_id": project_id,
                "format": file_format,
                "file_name": opts["file_name"],
                "module": self._report_module(module),
                "test_module": self._report_module(test_module),
                "config": config.to_dict(),
                "link_configurations": [
                    {"id": c.id, "name": c.name, "test_module": self._report_module(tm)} for c, tm, _ in joined
                ],
                "match_attribute": match,
                "match_options": match_options,
                "options": {k: opts[k] for k in ("executed_by", "version", "execution_date")},
                "attributes": reader["attributes"],
                "results": rows,
                "summary": {
                    "total": len(rows),
                    **counts,
                    **{s: sum(1 for r in rows if r["status"] == s) for s in ("passed", "failed", "not_run")},
                },
                "test_cases_not_in_file": [
                    {"id": t["id"], "text": t["text"]} for t in tests if t["id"] not in matched_tests
                ],
                "errors": errors,
                "warnings": warnings,
            },
        }

    def preview_test_results(
        self, project_id: str, module_id: str, content: Any, options: Any = None
    ) -> dict[str, Any]:
        """What ``import_test_results`` would do with a result file; writes nothing.

        Lists every result with its match (``matched`` to one test case,
        ``ambiguous``, ``unmatched``), the test cases the file does not
        cover, the link configuration and match attribute used (and the
        alternatives), and the ``errors`` that would refuse the import.
        """
        return self._test_results_plan(project_id, module_id, content, options)["public"]

    @_records_history
    def import_test_results(
        self, project_id: str, module_id: str, content: Any, options: Any = None
    ) -> dict[str, Any]:
        """Create one execution record per matched test result, linked to its test case.

        ``content`` is a JUnit XML or Robot Framework output.xml file
        (``test_results_import.py``). Options (all optional): ``config_id``
        (the link configuration to a test_cases module; default the first),
        ``match_attribute`` (``@id`` or a test case attribute id; default the
        test case's "Test ID"-like attribute, else ``@id``), ``executed_by``,
        ``version``, ``execution_date`` (``YYYY-MM-DD``; default the date in
        the file, else today) and ``file_name`` (for Evidence/Log Ref).

        A result matches a test case whose key equals its name, qualified name,
        a Robot tag or a JUnit property value, else whose key is a run of whole
        words of one of those (``SWT-0001 Login`` matches ``SWT-0001``).
        Results matching no or several test cases are skipped (reported in the
        preview). All-or-nothing: any validation problem refuses the whole
        file before anything is written. Records get server-allocated ids and
        are appended at the module root in file order.
        """
        plan = self._test_results_plan(project_id, module_id, content, options)
        public = plan["public"]
        if public["errors"]:
            raise ValueError("; ".join(public["errors"]))
        project: Project = plan["project"]
        module: Module = plan["module"]
        config: LinkConfiguration = plan["config"]
        test_module: Module = plan["test_module"]
        record_is_source: bool = plan["record_is_source"]

        existing = self.store.load_all_requirements(project_id, module.id)
        order = self._next_sibling_order(existing, None)
        created: list[Requirement] = []
        new_pairs: list[LinkPair] = []
        merged = [
            self._merged_attributes(project, record["values"], module.id, module.type) for record in plan["records"]
        ]
        if guard_active():
            # Server edition: the status guards apply to the new records' (defaulted) status
            # too, like ``add_requirement`` and ``import_csv`` (before anything is written).
            status = self._status_attribute(project, module)
            if status is not None:
                self._check_status_changes(
                    project_id,
                    module.id,
                    [(None, None, a[status.id]) for a in merged if a.get(status.id) not in (None, "")],
                )
        for record, attributes in zip(plan["records"], merged):
            requirement_id = self._allocate_requirement_id(project, module)
            test_id = record["test_id"]
            attributes[config.id] = f"> {test_id}" if record_is_source else f"< {test_id}"
            created.append(Requirement(id=requirement_id, attributes=attributes, parent_id=None, order=order))
            order += 1
            new_pairs.append(
                LinkPair(source_id=requirement_id, target_id=test_id)
                if record_is_source
                else LinkPair(source_id=test_id, target_id=requirement_id)
            )

        self._write_new_requirements(project, module.id, created)
        pairs = self.store.load_link_pairs(project_id, config.id) + new_pairs
        self.store.save_link_pairs(project_id, config.id, pairs)
        # Refresh the linked test cases' mirror attribute (like ``_refresh_link_attribute``,
        # but from the pairs in memory: one file read for any number of test cases).
        linked_tests = set(r["test_id"] for r in plan["records"])
        outgoing: dict[str, list[str]] = {}
        incoming: dict[str, list[str]] = {}
        for pair in pairs:
            if pair.source_id in linked_tests:
                outgoing.setdefault(pair.source_id, []).append(pair.target_id)
            if pair.target_id in linked_tests:
                incoming.setdefault(pair.target_id, []).append(pair.source_id)
        for test_id in sorted(linked_tests):
            entries = [f"> {t}" for t in outgoing.get(test_id, [])] + [f"< {s}" for s in incoming.get(test_id, [])]
            test_case = self.store.load_requirement(project_id, test_module.id, test_id)
            test_case.attributes[config.id] = "; ".join(entries)
            self.store.save_requirement(project_id, test_module.id, test_case)

        return {
            "imported": len(created),
            "requirement_ids": [r.id for r in created],
            "linked": len(new_pairs),
            "skipped": public["summary"]["unmatched"] + public["summary"]["ambiguous"],
            "format": public["format"],
            "config_id": config.id,
            "test_module_id": test_module.id,
            "warnings": public["warnings"],
        }

    # --- export ----------------------------------------------------------------

    def _export_modules(
        self, project: Project, keep_rich: bool = False
    ) -> list[tuple[Module, list[Requirement]]]:
        """Each module (display order) with its requirements, as the exports see them.

        A requirement shows only the values of attributes that apply to its
        module (a value left over from hand-edited data is not exported).

        Rich text values of ``text`` attributes are exported as their
        plain-text projection (``richtext.to_plain``) by the table exports
        (CSV, XLSX). The formatted exports (DOCX, ReqIF) pass ``keep_rich``
        and get rich values as stored. A rich value with no visible text is
        exported as ``""`` by every export (not as markup, nor as a projection
        such as ``"- "`` of a blank list item).
        A rich-looking value of any other attribute type is plain text and
        is never touched.
        """
        text_ids = {a.id for a in project.attributes if a.type == "text"}
        result: list[tuple[Module, list[Requirement]]] = []
        for module in self.list_modules(project.id):
            requirements = []
            for requirement in self.store.load_all_requirements(project.id, module.id):
                kept, dropped = self._applicable_values(project, module, requirement.attributes)
                rich = {k: richtext.blocks(kept[k]) for k in text_ids if richtext.is_rich(kept.get(k))}
                rich = {
                    k: (richtext.to_plain(kept[k]) if visible else "")
                    for k, visible in rich.items()
                    if not (keep_rich and visible)
                }
                if rich:
                    kept = {k: rich.get(k, v) for k, v in kept.items()}
                requirements.append(replace(requirement, attributes=kept) if dropped or rich else requirement)
            result.append((module, requirements))
        return result

    def export_reqif(self, project_id: str) -> str:
        from .reqif_export import export_project_to_reqif

        project = self.store.load_project(project_id)
        modules = self._export_modules(project, keep_rich=True)
        # Cross-project links become no SPEC-RELATION: their targets are not
        # in this file. Their mirror attribute (project-qualified targets,
        # "> rbc-gp:SYRS-0001") is exported like every attribute.
        local_configs = self._local_configs(project)
        link_pairs = {config.id: self.store.load_link_pairs(project_id, config.id) for config in local_configs}
        return export_project_to_reqif(project, modules, local_configs, link_pairs)

    def export_csv(self, project_id: str) -> str:
        from .csv_export import export_project_to_csv

        project = self.store.load_project(project_id)
        modules = self._export_modules(project)
        return export_project_to_csv(project, modules)

    def export_xlsx(self, project_id: str) -> bytes:
        from .xlsx_export import export_project_to_xlsx

        project = self.store.load_project(project_id)
        modules = self._export_modules(project)
        return export_project_to_xlsx(project, modules)

    def export_docx(self, project_id: str) -> bytes:
        from .docx_export import export_project_to_docx

        project = self.store.load_project(project_id)
        modules = self._export_modules(project, keep_rich=True)
        return export_project_to_docx(project, modules, self._effective_row_styles(project))

    # --- import ----------------------------------------------------------------

    # The CSV import's modes: "create" makes every row a new requirement;
    # "update" lets a row whose Id is an existing requirement of the module
    # change it (only its non-empty cells), every other row is created.
    def _csv_import_plan(
        self, project_id: str, module_id: str, csv_text: Any, mode: Any = None
    ) -> "_CsvImportPlan":
        """Validate a CSV import completely and work out what it would write (nothing is written).

        Shared by ``preview_csv_import`` (read-only diff) and ``import_csv``.
        Every problem is collected and raised together as one
        ``CsvImportError`` (all-or-nothing). See ``import_csv`` for the rules.
        """
        from .csv_import import MAX_CSV_IMPORT_BYTES, CsvImportError, parse_requirements_csv

        if mode is None:
            mode = "create"
        if mode not in CSV_IMPORT_MODES:
            raise ValueError(f"Unknown CSV import mode {mode!r}; expected one of {', '.join(CSV_IMPORT_MODES)}")
        if not isinstance(csv_text, str):
            raise ValueError("CSV content must be a string")
        if len(csv_text) > MAX_CSV_IMPORT_BYTES:
            raise ValueError(
                f"The CSV file is too large: the limit is {MAX_CSV_IMPORT_BYTES // (1024 * 1024)} MiB"
            )
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        existing = self.store.load_all_requirements(project_id, module_id)
        by_id = {r.id: r for r in existing}
        outgoing = [c for c in project.link_configurations if c.source_module_id == module_id]

        def link_column(name: str) -> LinkConfiguration:
            for matches in (
                [c for c in outgoing if c.name == name],
                [c for c in outgoing if c.id == name],
                [c for c in outgoing if c.name.casefold() == name.casefold()],
            ):
                if matches:
                    return matches[0]
            available = [c.name for c in outgoing]
            raise ValueError(
                f"no link configuration '{name}' starts in module '{module.name}'"
                + (f"; available: {available}" if available else " (it has no outgoing link configurations)")
            )

        parsed = parse_requirements_csv(
            project.attributes,
            csv_text,
            validate=lambda values: self._attribute_problems(
                project, values, is_create=True, module_id=module_id, module_type=module.type
            ),
            applies=lambda attr: self._attribute_applies(attr, module_id, module.type),
            link_column=link_column,
            # Parent may name an existing requirement only in update mode (a
            # create import keeps its rule: Parent is a row of the file).
            existing_ids=set(by_id) if mode == "update" else None,
            update_ids=set(by_id) if mode == "update" else None,
            validate_update=lambda values: self._attribute_problems(
                project, values, is_create=False, module_id=module_id, module_type=module.type
            ),
        )

        errors: list[str] = []
        text_ids = {a.id for a in project.attributes if a.type == "text"}

        def stored(values: dict[str, Any]) -> dict[str, Any]:
            # CSV cells are plain text: one that looks like rich text is kept
            # as the literal text (``richtext.from_plain``), never as markup.
            return {
                k: richtext.from_plain(v) if k in text_ids and isinstance(v, str) else v for k, v in values.items()
            }

        file_rows = {row.key: row for row in parsed.rows if row.key is not None}
        row_ids: dict[int, str] = {}
        next_order: dict[str | None, int] = {}
        plan = _CsvImportPlan(project=project, module=module, mode=mode, ignored_columns=parsed.ignored_columns)

        for row in parsed.rows:  # parents before children
            if row.existing:
                requirement = by_id[row.key]
                if row.parent_key is not None:
                    holder = file_rows.get(row.parent_key)
                    parent = row.parent_key if holder is None or holder.existing else None
                    if parent != requirement.parent_id:
                        errors.append(
                            f"Row {row.row_number}: Parent '{row.parent_key}' differs from the current parent of "
                            f"'{requirement.id}' ({requirement.parent_id or 'module root'}); the import does not "
                            "move requirements (leave Parent empty or unchanged)"
                        )
                        continue
                row_ids[row.row_number] = requirement.id
                values = stored(row.attributes)
                changes: dict[str, tuple[Any, Any]] = {}
                for attr_id, new in values.items():
                    old = requirement.attributes.get(attr_id)
                    if old == new:
                        continue
                    # Rich text whose plain projection is the cell text is unchanged
                    # (an AI echoing a formatted requirement keeps its formatting).
                    if attr_id in text_ids and richtext.is_rich(old) and richtext.to_plain(old) == row.attributes[attr_id]:
                        continue
                    changes[attr_id] = (old, new)
                if changes:
                    updated = replace(
                        requirement,
                        attributes={**requirement.attributes, **{k: v for k, (_, v) in changes.items()}},
                    )
                    plan.updates.append((row.row_number, updated, changes))
                else:
                    plan.unchanged.append(requirement.id)
                continue

            requirement_id = self._allocate_requirement_id(project, module)
            row_ids[row.row_number] = requirement_id
            parent_id: str | None = None
            if row.parent_key is not None:
                holder = file_rows.get(row.parent_key)
                if holder is not None:
                    if holder.row_number not in row_ids:  # its own row failed above
                        continue
                    parent_id = row_ids[holder.row_number]
                else:
                    parent_id = row.parent_key
            if parent_id not in next_order:
                next_order[parent_id] = self._next_sibling_order(existing, parent_id)
            order = next_order[parent_id]
            next_order[parent_id] = order + 1
            attributes = self._merged_attributes(project, stored(row.attributes), module_id, module.type)
            plan.created.append(
                (row.row_number, Requirement(id=requirement_id, attributes=attributes, parent_id=parent_id, order=order))
            )

        target_projects: dict[str, str | None] = {}
        pairs_cache: dict[str, list[LinkPair]] = {}
        seen: set[tuple[str, str, str]] = set()
        for row in sorted(parsed.rows, key=lambda r: r.row_number):
            source_id = row_ids.get(row.row_number)
            if source_id is None:
                continue
            for config, header, targets in row.links:
                if config.id not in target_projects:
                    try:
                        target_projects[config.id] = self._load_link_target_project(project, config)
                    except (OSError, ValueError) as exc:
                        target_projects[config.id] = None
                        errors.append(f"Column '{header}': {exc}")
                target_project = target_projects[config.id]
                if target_project is None:
                    continue
                for target in targets:
                    local_row = (
                        file_rows.get(target)
                        if config.target_project_id is None and config.target_module_id == module_id
                        else None
                    )
                    if local_row is not None:
                        target_id = row_ids.get(local_row.row_number)
                        if target_id is None:
                            continue
                    else:
                        try:
                            target_id = self._plain_target_id(config, target)
                        except ValueError as exc:
                            errors.append(f"Row {row.row_number}: {header}: {exc}")
                            continue
                        if not self.store.requirement_exists(target_project, config.target_module_id, target_id):
                            where = (
                                f"module '{config.target_module_id}'"
                                if config.target_project_id is None
                                else f"module '{config.target_module_id}' of project '{config.target_project_id}'"
                            )
                            errors.append(f"Row {row.row_number}: {header}: '{target}' is not a requirement of {where}")
                            continue
                    if source_id == target_id and config.target_project_id is None:
                        errors.append(f"Row {row.row_number}: {header}: a requirement cannot be linked to itself")
                        continue
                    if (config.id, source_id, target_id) in seen:
                        continue
                    seen.add((config.id, source_id, target_id))
                    if config.id not in pairs_cache:
                        pairs_cache[config.id] = self.store.load_link_pairs(project_id, config.id)
                    exists = any(p.source_id == source_id and p.target_id == target_id for p in pairs_cache[config.id])
                    plan.links.append((row.row_number, config, source_id, target_id, exists))

        if errors:
            raise CsvImportError(errors)
        return plan

    def preview_csv_import(self, project_id: str, module_id: str, csv_text: Any, mode: Any = None) -> dict[str, Any]:
        """What ``import_csv`` would do, without writing anything (read-only; never broadcast).

        Raises the same errors as the import. ``created`` lists the new
        requirements with provisional ids (allocated again on import),
        ``updated`` each changed requirement with its old and new values,
        ``unchanged`` the matched requirements the file does not change and
        ``links`` the links to create (``exists``: already there, skipped).
        Text values are shown as plain text.
        """
        plan = self._csv_import_plan(project_id, module_id, csv_text, mode)
        names = {a.id: a.name for a in plan.project.attributes}
        text_ids = {a.id for a in plan.project.attributes if a.type == "text"}

        def shown(attr_id: str, value: Any) -> Any:
            return richtext.to_plain(value) if attr_id in text_ids else value

        return {
            "mode": plan.mode,
            "created": [
                {
                    "row": row,
                    "id": r.id,
                    "parent_id": r.parent_id,
                    "attributes": {k: shown(k, v) for k, v in r.attributes.items()},
                }
                for row, r in plan.created
            ],
            "updated": [
                {
                    "row": row,
                    "id": r.id,
                    "changes": [
                        {"attribute_id": k, "name": names.get(k, k), "old": shown(k, old), "new": shown(k, new)}
                        for k, (old, new) in changes.items()
                    ],
                }
                for row, r, changes in plan.updates
            ],
            "unchanged": plan.unchanged,
            "links": [self._planned_link(row, config, s, t, exists) for row, config, s, t, exists in plan.links],
            "ignored_columns": plan.ignored_columns,
        }

    @staticmethod
    def _planned_link(row: int, config: LinkConfiguration, source_id: str, target_id: str, exists: bool) -> dict[str, Any]:
        return {
            "row": row,
            "config_id": config.id,
            "config_name": config.name,
            "source_id": source_id,
            "target_id": target_id if config.target_project_id is None else f"{config.target_project_id}:{target_id}",
            "exists": exists,
        }

    @_records_history
    def import_csv(self, project_id: str, module_id: str, csv_text: str, mode: Any = None) -> dict[str, Any]:
        """Import requirements into one module from CSV text (see ``csv_import.py``).

        All-or-nothing: the whole file is validated (``_csv_import_plan``)
        before anything is written, so an invalid file leaves the project
        unchanged. Rows are checked with the same ``_attribute_problems`` as
        add/update requirement. If writing new requirements fails part-way,
        ``_write_new_requirements`` removes the files already written and the
        id counters stay unchanged.

        - New requirements get server-allocated ids. Root rows are appended
          after the module's existing root requirements; nested rows keep
          their file order under their parent. Parent names a row of the
          file (by its Id key); in update mode it may also name an existing
          requirement of the module.
        - ``mode="update"``: a row whose Id is an existing requirement of the
          module updates it: each non-empty cell replaces the value (empty
          cells leave values unchanged); its Parent must be empty or its
          current parent (the import never moves requirements).
        - ``Link: <configuration>`` columns create links of a configuration
          starting in this module (cross-project ones too, with targets
          ``<project>:<id>`` or plain), validated like ``link_requirements``;
          links that already exist are skipped. Only this project is written.
        """
        plan = self._csv_import_plan(project_id, module_id, csv_text, mode)
        project = plan.project
        created = [r for _, r in plan.created]
        if guard_active():
            # Server edition: the status guards apply to imported statuses too (before anything is written).
            status = self._status_attribute(project, plan.module)
            if status is not None:
                self._check_status_changes(
                    project_id,
                    module_id,
                    [(None, None, r.attributes[status.id]) for r in created if r.attributes.get(status.id) not in (None, "")]
                    + [
                        (r.id, changes[status.id][0], changes[status.id][1])
                        for _, r, changes in plan.updates
                        if status.id in changes
                    ],
                )
        if created:
            self._write_new_requirements(project, module_id, created)
        if plan.updates:
            self._mark_rich_text_format(
                project, {k: v for _, _, changes in plan.updates for k, (_, v) in changes.items()}
            )
        for _, requirement, _ in plan.updates:
            self.store.save_requirement(project_id, module_id, requirement)

        new_links = [(config, s, t) for _, config, s, t, exists in plan.links if not exists]
        refresh: dict[tuple[str, str, str], LinkConfiguration] = {}
        for config in {c.id: c for c, _, _ in new_links}.values():
            pairs = self.store.load_link_pairs(project_id, config.id)
            for c, source_id, target_id in new_links:
                if c.id != config.id:
                    continue
                if not any(p.source_id == source_id and p.target_id == target_id for p in pairs):
                    pairs.append(LinkPair(source_id=source_id, target_id=target_id))
                refresh[(config.id, module_id, source_id)] = config
                if config.target_project_id is None:
                    refresh[(config.id, config.target_module_id, target_id)] = config
            self.store.save_link_pairs(project_id, config.id, pairs)
        for (config_id, owner_module, requirement_id), config in refresh.items():
            self._refresh_link_attribute(project_id, config_id, owner_module, requirement_id, config)

        # The result keeps its original keys; update mode adds the update
        # counts and link columns the link count, so older clients are unaffected.
        result: dict[str, Any] = {
            "imported": len(created),
            "requirement_ids": [r.id for _, r in sorted(plan.created, key=lambda item: item[0])],
        }
        if plan.mode == "update":
            result["updated"] = len(plan.updates)
            result["updated_ids"] = [r.id for _, r, _ in sorted(plan.updates, key=lambda item: item[0])]
            result["unchanged"] = len(plan.unchanged)
        if plan.links:
            result["linked"] = len(new_links)
        result["ignored_columns"] = plan.ignored_columns
        return result

    # --- AI prompt library -------------------------------------------------------
    #
    # Prompts (``prompts.py``) are user data outside every project: creating,
    # editing, deleting or importing one never touches a project (no lock, no
    # history) and broadcasts ``prompts_changed``. Rendering is read-only.
    # ReqManTool never sends a prompt anywhere: the user copies it.

    def _read_prompt_text(self, source: str, local_id: str) -> str:
        if source == PROMPT_BUILTIN_SOURCE:
            return read_builtin_prompt_text(local_id, self.builtin_prompts_dir)
        return self.store.read_user_prompt_text(local_id)

    def _load_prompt(self, prompt_id: Any) -> Prompt:
        """Read and check a prompt (404 if missing, 400 if malformed)."""
        source, local_id = split_prompt_id(prompt_id)
        text = self._read_prompt_text(source, local_id)
        try:
            return parse_prompt_text(text, prompt_id)
        except ValueError as exc:
            raise ValueError(f"Prompt '{prompt_id}' is invalid: {exc}") from exc

    def list_prompts(self) -> list[dict[str, Any]]:
        """Built-in prompts, then user prompts, each sorted by name.

        An invalid prompt file is still listed, with the reason in ``error``
        and an empty body, so one bad file never hides the others.
        """
        sources = (
            (PROMPT_BUILTIN_SOURCE, list_builtin_prompt_ids(self.builtin_prompts_dir)),
            (PROMPT_USER_SOURCE, self.store.list_user_prompt_ids()),
        )
        result: list[dict[str, Any]] = []
        for source, local_ids in sources:
            entries: list[dict[str, Any]] = []
            for local_id in local_ids:
                prompt_id = make_prompt_id(source, local_id)
                text: str | None = None
                try:
                    text = self._read_prompt_text(source, local_id)
                    entry = parse_prompt_text(text, prompt_id).to_api()
                    entry["error"] = None
                except (OSError, ValueError) as exc:
                    entry = {
                        **Prompt(id=prompt_id, name=_display_name(text, local_id)).to_api(),
                        "error": str(exc),
                    }
                entries.append(entry)
            entries.sort(key=lambda e: (e["name"].casefold(), e["id"]))
            result.extend(entries)
        return result

    def get_prompt(self, prompt_id: Any) -> dict[str, Any]:
        return {**self._load_prompt(prompt_id).to_api(), "error": None}

    def _check_prompt_name_free(self, name: str, own_id: str | None = None) -> None:
        for entry in self.list_prompts():
            if entry["id"] != own_id and entry["name"].casefold() == name.casefold():
                raise ConflictError(f"A prompt named '{entry['name']}' already exists ({entry['id']})")

    @staticmethod
    def _prompt_fields(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("A prompt must be an object")
        return {k: v for k, v in data.items() if k in PROMPT_KEYS}

    def create_prompt(self, data: Any) -> dict[str, Any]:
        """Save a new user prompt (``user:<slug of its name>``); the name must be unused (409)."""
        check_permission(ACTION_LIBRARY_WRITE)
        prompt = parse_prompt(self._prompt_fields(data), make_prompt_id(PROMPT_USER_SOURCE, "new"))
        self._check_prompt_name_free(prompt.name)
        local_id = _unique_slug(prompt.name, self.store.user_prompt_path_exists, fallback="prompt")
        prompt.id = make_prompt_id(PROMPT_USER_SOURCE, local_id)
        self.store.save_user_prompt(local_id, prompt.to_dict())
        return {**prompt.to_api(), "error": None}

    def update_prompt(self, prompt_id: Any, data: Any) -> dict[str, Any]:
        """Replace a user prompt's content (its id stays). Built-in prompts are read-only."""
        check_permission(ACTION_LIBRARY_WRITE)
        source, local_id = split_prompt_id(prompt_id)
        if source != PROMPT_USER_SOURCE:
            raise ValueError("Built-in prompts are read-only; duplicate the prompt to change it")
        self.store.read_user_prompt_text(local_id)  # 404 if missing (an invalid file may be repaired)
        prompt = parse_prompt(self._prompt_fields(data), prompt_id)
        self._check_prompt_name_free(prompt.name, own_id=prompt_id)
        self.store.save_user_prompt(local_id, prompt.to_dict())
        return {**prompt.to_api(), "error": None}

    def delete_prompt(self, prompt_id: Any) -> None:
        check_permission(ACTION_LIBRARY_WRITE)
        source, local_id = split_prompt_id(prompt_id)
        if source != PROMPT_USER_SOURCE:
            raise ValueError("Built-in prompts cannot be deleted")
        self.store.delete_user_prompt(local_id)

    def export_prompt(self, prompt_id: Any) -> str:
        """The prompt as a file to share (the canonical prompt JSON; import it with ``import_prompt``)."""
        return json.dumps(self._load_prompt(prompt_id).to_dict(), indent=2) + "\n"

    def import_prompt(self, text: Any) -> dict[str, Any]:
        """Save an uploaded prompt file as a new user prompt (never overwrites; a used name is 409)."""
        check_permission(ACTION_LIBRARY_WRITE)
        if not isinstance(text, str):
            raise ValueError("The prompt file content must be a string")
        if len(text.encode("utf-8", "surrogatepass")) > MAX_PROMPT_IMPORT_BYTES:
            raise ValueError(f"The prompt file is too large: the limit is {MAX_PROMPT_IMPORT_BYTES // 1024} KiB")
        try:
            prompt = parse_prompt_text(text, make_prompt_id(PROMPT_USER_SOURCE, "import"))
        except ValueError as exc:
            raise ValueError(f"The prompt file is invalid: {exc}") from exc
        self._check_prompt_name_free(prompt.name)
        local_id = _unique_slug(prompt.name, self.store.user_prompt_path_exists, fallback="prompt")
        prompt.id = make_prompt_id(PROMPT_USER_SOURCE, local_id)
        self.store.save_user_prompt(local_id, prompt.to_dict())
        return {**prompt.to_api(), "error": None}

    @staticmethod
    def _csv_column_name(project: Project, attr: AttributeDefinition) -> str:
        """The header the CSV import matches ``attr`` by: its name, or its id where the name is ambiguous."""
        if attr.name in RESERVED_COLUMNS or sum(1 for a in project.attributes if a.name == attr.name) > 1:
            return attr.id
        return attr.name

    @staticmethod
    def _csv_line(cells: list[str]) -> str:
        import csv as _csv
        import io as _io

        buffer = _io.StringIO()
        _csv.writer(buffer, lineterminator="\n").writerow(cells)
        return buffer.getvalue().rstrip("\n")

    @staticmethod
    def _csv_cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            return richtext.to_plain(value)
        return str(value)

    _ATTRIBUTE_TYPE_HELP = {
        "string": "single-line text",
        "text": "text (several lines allowed; quote the cell)",
        "integer": "integer",
        "boolean": "true or false",
        "enum": "exactly one of the listed values",
        "date": "date written YYYY-MM-DD",
    }

    def _prompt_link_lines(self, project: Project, module: Module) -> tuple[list[str], list[str]]:
        """Link column headers and guide lines for configurations starting in ``module``."""
        headers: list[str] = []
        lines: list[str] = []
        for config in project.link_configurations:
            if config.source_module_id != module.id:
                continue
            owner = config.target_project_id or project.id
            try:
                target_name = self.store.load_module(owner, config.target_module_id).name
            except (OSError, ValueError):
                target_name = config.target_module_id
            header = f"{LINK_COLUMN_PREFIX} {config.name}"
            headers.append(header)
            where = f'module "{target_name}"'
            if config.target_project_id is not None:
                where += (
                    f' of project "{config.target_project_id}" (write each id as '
                    f"{config.target_project_id}:<id>, e.g. as shown in the selected requirements)"
                )
            lines.append(
                f'- "{header}" (optional): ids of existing requirements in {where} that this row links to '
                f'("{config.name}"); several ids separated by ";". Fill it so every new row stays traced.'
            )
        return headers, lines

    def _import_rules(self, module: Module, mode: str, has_links: bool) -> str:
        rules = [
            "- Answer with exactly one CSV code block (```csv ... ```) and nothing else: no explanations before or after it.",
            "- The first line is the header given above, unchanged. Use only those columns; columns may be left empty.",
            "- Separate cells with commas. Quote a cell that contains a comma, a quote or a line break with double "
            'quotes, and write a quote inside it as "".',
            f'- Every row becomes a new requirement of module "{module.name}". ReqManTool allocates the real ids: '
            "the Id cell is only a short key of the row (e.g. N1, N2) so that Parent cells can refer to it.",
            "- Parent: empty for a top-level requirement, or the Id key of another row of your answer.",
            "- Required columns must not be empty. Enum columns take exactly one of the listed values. Dates are "
            "YYYY-MM-DD. Booleans are true or false.",
        ]
        if mode == "update":
            rules[3] = (
                f'- To change an existing requirement of module "{module.name}", keep its Id exactly as given: only the '
                "non-empty cells of that row are written, an empty cell leaves the value unchanged. Leave out "
                "requirements you do not change. A row with a new short Id key (e.g. N1) becomes a new requirement; "
                "ReqManTool allocates its real id."
            )
            rules[4] = (
                "- Parent: empty for a top-level requirement, the Id key of another row of your answer, or the id "
                "of an existing requirement of this module. Do not change the Parent of an existing requirement "
                "(leave it empty or as given)."
            )
        if has_links:
            rules.append("- Link columns list ids of existing requirements exactly as given; invent no ids.")
        rules.append("- The whole answer is rejected if any row is invalid, so check every row against these rules.")
        return "\n".join(rules)

    def render_prompt(
        self,
        project_id: str,
        prompt_id: Any,
        module_id: str,
        requirement_ids: Any = None,
        selection_module_id: Any = None,
        selection_project_id: Any = None,
        include_children: Any = False,
        free_text: Any = None,
    ) -> dict[str, Any]:
        """Fill a prompt's placeholders for a target module and a selection (read-only).

        ``module_id`` is the target module (where the AI's CSV answer is
        imported): its exact import header, column guide and link columns
        are described. The selection is ``requirement_ids`` of
        ``selection_module_id`` (default: the target module) in
        ``selection_project_id`` (default: this project; its ids are then
        written ``<project>:<id>``), plus their descendants with
        ``include_children``, in tree order.
        """
        project = self.store.load_project(project_id)
        module = self.store.load_module(project_id, module_id)
        prompt = self._load_prompt(prompt_id)
        if free_text is None:
            free_text = ""
        if not isinstance(free_text, str):
            raise ValueError("free_text must be a string")
        if len(free_text) > MAX_PROMPT_FREE_TEXT_LENGTH:
            raise ValueError(f"The free text is too long (at most {MAX_PROMPT_FREE_TEXT_LENGTH} characters)")
        if prompt.free_text == "required" and not free_text.strip():
            label = prompt.free_text_label or "free text"
            raise ValueError(f"Prompt '{prompt.name}' needs the {label}")
        if not isinstance(include_children, bool):
            raise ValueError("include_children must be true or false")

        sel_project_id = selection_project_id or project_id
        if not isinstance(sel_project_id, str):
            raise ValueError("selection_project_id must be a project id")
        if sel_project_id != project_id:
            check_permission(ACTION_PROJECT_READ, sel_project_id)
        sel_project = project if sel_project_id == project_id else self.store.load_project(sel_project_id)
        sel_module_id = selection_module_id or module_id
        if not isinstance(sel_module_id, str):
            raise ValueError("selection_module_id must be a module id")
        sel_module = self.store.load_module(sel_project_id, sel_module_id)
        if requirement_ids is None:
            requirement_ids = []
        if not isinstance(requirement_ids, list) or not all(isinstance(r, str) for r in requirement_ids):
            raise ValueError("requirement_ids must be a list of requirement ids")
        selected: list[Requirement] = []
        if requirement_ids:
            all_requirements = self.store.load_all_requirements(sel_project_id, sel_module_id)
            known = {r.id for r in all_requirements}
            missing = [r for r in requirement_ids if r not in known]
            if missing:
                raise FileNotFoundError(f"Requirement(s) {missing} not found in module '{sel_module_id}'")
            wanted = set(requirement_ids)
            if include_children:
                for requirement_id in requirement_ids:
                    wanted |= self._descendant_ids(all_requirements, requirement_id)
            selected = [r for r in tree_order(all_requirements) if r.id in wanted]
        if prompt.selection == "required" and not selected:
            raise ValueError(f"Prompt '{prompt.name}' needs selected requirements")

        qualify = (lambda rid: f"{sel_project_id}:{rid}") if sel_project_id != project_id else (lambda rid: rid)
        sel_attributes = [
            a for a in sel_project.attributes
            if not a.system and self._attribute_applies(a, sel_module.id, sel_module.type)
        ]
        selected_lines = [
            self._csv_line([ID_COLUMN, PARENT_COLUMN] + [self._csv_column_name(sel_project, a) for a in sel_attributes])
        ]
        for requirement in selected:
            selected_lines.append(
                self._csv_line(
                    [qualify(requirement.id), qualify(requirement.parent_id) if requirement.parent_id else ""]
                    + [self._csv_cell(requirement.attributes.get(a.id)) for a in sel_attributes]
                )
            )
        selected_csv = "\n".join(selected_lines) + "\n" if selected else "(no requirements selected)\n"

        attributes = [
            a for a in project.attributes if not a.system and self._attribute_applies(a, module.id, module.type)
        ]
        link_headers, link_lines = self._prompt_link_lines(project, module)
        mode = prompt.import_mode if prompt.import_mode in CSV_IMPORT_MODES else "create"
        header = self._csv_line(
            [ID_COLUMN, PARENT_COLUMN] + [self._csv_column_name(project, a) for a in attributes] + link_headers
        )
        guide = [
            f"- {ID_COLUMN}: "
            + (
                "the id of an existing requirement to change it, or a short new key (e.g. N1) for a new requirement"
                if mode == "update"
                else "a short key of the row (e.g. N1) used only by Parent cells; may be empty"
            ),
            f"- {PARENT_COLUMN}: empty (top level) or the Id key of another row"
            + (", or an existing requirement id of this module" if mode == "update" else ""),
        ]
        for a in attributes:
            parts = [self._ATTRIBUTE_TYPE_HELP.get(a.type, a.type)]
            if a.required:
                parts.append("required")
            if a.type == "enum" and a.choices:
                parts.append("one of: " + " | ".join(str(c) for c in a.choices))
            if a.default is not None and a.default != "":
                parts.append(f"default when empty: {self._csv_cell(a.default)}")
            guide.append(f"- {self._csv_column_name(project, a)}: " + "; ".join(parts))

        values = {
            "project_name": project.name,
            "project_description": project.description or "",
            "module_name": module.name,
            "module_description": module.description or "",
            "module_type": module.type,
            "module_prefix": module.prefix,
            "csv_header": header,
            "attribute_guide": "\n".join(guide),
            "link_columns": (
                "Link columns (optional, include them in the header only as given):\n" + "\n".join(link_lines)
                if link_lines
                else "This module has no link columns."
            ),
            "import_rules": (
                self._import_rules(module, mode, bool(link_lines))
                if prompt.import_mode != "none"
                else "Answer as a review; do not answer with CSV."
            ),
            "selection_module_name": sel_module.name if selected else "(none)",
            "selected_ids": ", ".join(qualify(r.id) for r in selected) or "(none)",
            "selected_csv": selected_csv,
            "free_text": free_text.strip() or "(none)",
        }
        return {
            "prompt": prompt.to_api(),
            "text": fill_placeholders(prompt.body, values),
            "module_id": module.id,
            "module_name": module.name,
            "import_mode": prompt.import_mode,
            "selected_ids": [qualify(r.id) for r in selected],
        }
