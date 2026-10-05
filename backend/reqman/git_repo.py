"""Per-project git history through the git command-line tool (no Python dependency).

Every project folder can be its own git repository (modules are plain
folders, never submodules). ``GitHistory`` finds out what state a project
folder is in, creates repositories, auto-commits after every change and
reads a requirement file's history. ``ProjectService`` decides *when* (after
each successful mutation, on project creation, on "Enable history") and
supplies the commit message text (see ``history.py``).

Safety rules, all enforced here:

- **Never touch an enclosing repository.** A project folder whose data
  directory lies inside another repository's work tree (for example a data
  directory inside the ReqManTool checkout) is ``nested``: no ``git init``,
  no commit, ever — even if the project folder has a repository of its own.
  Detection uses ``git rev-parse --show-toplevel`` from the project folder
  and from its parent. Every writing command then names the project's own
  repository explicitly (``--git-dir``/``--work-tree``), so a ``.git``
  removed in between makes git fail instead of discovering an outer repo.
  Inherited ``GIT_DIR``/``GIT_WORK_TREE``/``GIT_CONFIG_*``/... variables
  are dropped.
- **Only the folder's own ``.git`` directory.** ``<project folder>/.git``
  must be a real directory (not a symlink) and be both the git dir and the
  common dir git reports. A ``.git`` symlink or file pointing to another
  repository (separate git dir) or a linked worktree is ``foreign``: no
  init, commit or checkout, since those would change the other
  repository's HEAD, index and refs.
- **No hooks.** Every git command runs with ``core.hooksPath`` set to
  ``/dev/null`` (on Windows: an empty directory of ReqManTool's), overriding
  repository, global and ``GIT_CONFIG_*`` hooks paths alike, and ``git init`` copies no template,
  so no hook can block or alter an auto-commit. ``core.fsmonitor`` (a
  configured program), ``gc.auto`` and ``maintenance.auto`` are off too, so
  no other program of the repository's configuration runs. ``.gitattributes``
  filters (e.g. Git LFS) are honoured on purpose: they decide how the user's
  repository stores files.
- **Never during a merge, cherry-pick, revert or rebase, or with unresolved
  conflicts** (``MERGE_HEAD``, ``CHERRY_PICK_HEAD``, ``REVERT_HEAD``,
  ``sequencer/``, ``rebase-merge/`` or ``rebase-apply/`` in the git dir, or
  unmerged index entries, e.g. after a ``git stash pop`` conflict): the
  folder is ``paused``. Staging would mark the user's conflicts resolved
  with the markers inside; nothing is staged or committed until the user
  has finished, and the next change then commits everything saved meanwhile.
- **Only data files are committed**: ``project.json``,
  ``modules/*/module.json``, ``modules/*/requirements/*.json`` and
  ``links/*.json`` (``git add -A`` and ``git commit --only`` with those
  pathspecs). Other files in the project folder (``.DS_Store``, editor
  backups, ...), and anything the user staged themselves, are left alone.
  Temporary files of atomic writes (``*.tmp``) are also excluded through
  ``.git/info/exclude``.
- **Only on ``current``.** A repository on another branch or with a
  detached HEAD is ``paused`` as well: nothing is committed (files are still
  saved), and the status says why.
- **Squash safely.** A change amends the previous commit only if that commit
  carries the ``ReqMan-Session`` trailer, has exactly one parent, has the
  same author name and email, was last committed within the squash window,
  and no ref other than ``refs/heads/current`` contains it (no tag, no
  ``baseline`` or other branch, no remote-tracking branch, no stash).
- **A git failure never loses data**: the files are already saved; the error
  is kept as ``last_error`` (shown in the status) and, since every commit
  stages all changed data files, the next successful commit includes them.

Git operations that write are serialized per project folder by a lock.
Reads (status, history) take no lock and run with ``GIT_OPTIONAL_LOCKS=0``.
"""

from __future__ import annotations

import atexit
import getpass
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .history import FileChange, Revision, commit_message, requirement_history, session_of

logger = logging.getLogger("reqman.git")

WORKING_BRANCH = "current"
WORKING_REF = f"refs/heads/{WORKING_BRANCH}"
# Baselines: creating one fast-forwards ``refs/heads/baseline`` to ``current``
# (never a merge) and adds an annotated ``baseline/<name>`` tag. Both then
# contain the baselined commit, so the squash rule (no ref other than
# ``refs/heads/current`` contains HEAD) never amends it.
BASELINE_BRANCH = "baseline"
BASELINE_REF = f"refs/heads/{BASELINE_BRANCH}"
BASELINE_TAG_PREFIX = "baseline/"
# Conservative baseline names: they appear in tag refs, URLs and the UI.
# ``git check-ref-format`` is run as well (belt and braces).
BASELINE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_BASELINE_NAME_LENGTH = 100
# ``current`` names the working state in compare requests; ``compare`` is a
# REST path segment under /baselines/.
RESERVED_BASELINE_NAMES = frozenset({"current", "compare"})
# Trailers of the annotated tag's message (machine-readable baseline facts).
BASELINE_TRAILER = "Reqman-Baseline"
BASELINE_DATE_TRAILER = "Reqman-Baseline-Date"
BASELINE_TOOL_TRAILER = "Reqman-Tool-Version"
BASELINE_FORMAT_TRAILER = "Reqman-Project-Format"
_TRAILER_LINE = re.compile(r"^(Reqman-[A-Za-z-]+):[ \t]*(.*)$")
DEFAULT_LOG_LIMIT = 20
LOG_LIMIT = 200
# The data files ReqManTool owns inside a project folder (see store.py), each
# kind with its pathspec: only these are ever staged, committed or counted.
# (``*`` in a ``:(glob)`` pathspec does not match ``/``.) Names starting with
# a dot (macOS AppleDouble ``._X.json`` files, ...) are no data files.
_DATA_FILES = (
    (re.compile(r"^project\.json$"), ":(literal)project.json"),
    (re.compile(r"^modules/[^/]+/module\.json$"), ":(glob)modules/*/module.json"),
    (re.compile(r"^modules/[^/]+/requirements/[^/.][^/]*\.json$"), ":(glob)modules/*/requirements/[!.]*.json"),
    (re.compile(r"^links/[^/.][^/]*\.json$"), ":(glob)links/[!.]*.json"),
)
DATA_PATHSPECS = tuple(spec for _pattern, spec in _DATA_FILES)
EXCLUDE_PATTERNS = ("*.tmp",)
# The hooks directory on POSIX, and the fallback on Windows if no empty
# directory can be made (see ``_no_hooks_dir``).
_DEV_NULL_HOOKS = "/dev/null"
# Files and directories in the git dir while an operation is in progress
# (checked in this order), with the operation's name for the status message.
# ``sequencer/`` remains between the picks of a multi-commit cherry-pick or
# revert, after a conflict was resolved and committed.
_OPERATIONS_IN_PROGRESS = (
    ("MERGE_HEAD", "A merge"),
    ("CHERRY_PICK_HEAD", "A cherry-pick"),
    ("REVERT_HEAD", "A revert"),
    ("rebase-merge", "A rebase"),
    ("rebase-apply", "A rebase (or 'git am')"),
    (
        "sequencer",
        "A cherry-pick or revert (see 'git cherry-pick --quit' or '--abort', or the same 'git revert' options)",
    ),
)

DEFAULT_SQUASH_MINUTES = 10.0
HISTORY_LIMIT = 200
_TIMEOUT_SECONDS = 60
# How long a resolved commit identity (``git var GIT_AUTHOR_IDENT``) is reused.
_IDENTITY_TTL_SECONDS = 30.0

# States of a project folder (``RepoInfo.state`` / ``status()["state"]``).
STATE_OFF = "off"  # git integration turned off for this server
STATE_GIT_MISSING = "git_missing"  # no usable git executable
STATE_NO_REPO = "no_repo"  # plain files; "Enable history" can create the repository
STATE_NESTED = "nested"  # inside another repository's work tree: never init, never commit
STATE_FOREIGN = "foreign"  # .git is a gitfile/linked worktree of another repository: never touched
STATE_READY = "ready"  # own repository on `current`: auto-commit active
STATE_PAUSED = "paused"  # own repository, other branch, detached HEAD or operation in progress: no commits
STATE_ERROR = "error"  # git could not inspect the folder (e.g. "dubious ownership")

# Inherited variables that are dropped: they would make git use another
# repository or index than the one we name, override our ``-c`` settings
# (``GIT_CONFIG_PARAMETERS``/``GIT_CONFIG_COUNT``, plus the
# ``GIT_CONFIG_KEY_<n>``/``GIT_CONFIG_VALUE_<n>`` prefixes below) or change
# how our pathspecs are read.
_REPO_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_QUARANTINE_PATH",
    "GIT_INDEX_VERSION",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_LITERAL_PATHSPECS",
    "GIT_GLOB_PATHSPECS",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
)
_DROPPED_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
# For commands given a file path (not one of the DATA_PATHSPECS): no pathspec magic.
_LITERAL_PATHS = {"GIT_LITERAL_PATHSPECS": "1"}

_OFF_VALUES = {"0", "off", "false", "no", "disabled"}


class GitError(Exception):
    """A git command failed (or could not run)."""


class GitNotFound(GitError):
    """The git executable is missing."""


class RepoConflict(ValueError):
    """The repository is in a state that does not allow the request (REST 409)."""


class EnableFailed(ValueError):
    """"Enable history" failed part-way (REST 400).

    The failure is recorded as ``last_error`` and the repository may already
    have changed (e.g. created, but the initial commit failed), so callers
    broadcast a project change even though the request failed
    (``project_changed``).
    """

    project_changed = True


class BaselineFailed(ValueError):
    """Creating a baseline failed part-way (REST 400).

    The pending-changes commit may already exist (which is a consistent
    state on its own — the same commit an auto-commit would have made), so
    callers broadcast a project change even though the request failed.
    """

    project_changed = True


def validate_baseline_name(name: str) -> str:
    """The trimmed baseline name, or ``ValueError`` explaining the rule.

    Allowed: 1–100 characters of ``A-Za-z0-9._-``, starting with a letter or
    digit, without ``..``, not ending in ``.`` or ``.lock``, and not the
    reserved words ``current`` or ``compare``. (A subset of what
    ``git check-ref-format`` allows for ``refs/tags/baseline/<name>``, which
    is checked as well when the tag is made.)
    """
    name = (name or "").strip()
    rule = (
        "a baseline name must be 1–100 characters of letters, digits, '.', '_' or '-', start "
        "with a letter or digit, contain no '..' and not end in '.' or '.lock'"
    )
    if not name:
        raise ValueError(f"Baseline name must not be empty: {rule}")
    if (
        len(name) > MAX_BASELINE_NAME_LENGTH
        or not BASELINE_NAME_PATTERN.match(name)
        or ".." in name
        or name.endswith(".")
        or name.endswith(".lock")
    ):
        raise ValueError(f"Invalid baseline name '{name}': {rule}")
    if name in RESERVED_BASELINE_NAMES:
        raise ValueError(f"Invalid baseline name '{name}': the name is reserved")
    return name


def _parse_baseline_message(message: str) -> dict[str, Any]:
    """Description and ``Reqman-*`` trailers of a baseline tag message.

    The message is ``Baseline <name>``, an optional description paragraph
    and a final paragraph of ``Reqman-*: value`` trailers; the description
    is everything between the subject and the trailers.
    """
    paragraphs = [p for p in message.strip().split("\n\n") if p.strip()]
    trailers: dict[str, str] = {}
    if paragraphs:
        matches = [_TRAILER_LINE.match(line) for line in paragraphs[-1].splitlines()]
        if matches and all(matches):
            trailers = {m.group(1): m.group(2).strip() for m in matches if m}
            paragraphs = paragraphs[:-1]
    description = "\n\n".join(paragraphs[1:]) if len(paragraphs) > 1 else ""
    raw_format = trailers.get(BASELINE_FORMAT_TRAILER, "")
    return {
        "description": description,
        "date": trailers.get(BASELINE_DATE_TRAILER),
        "tool_version": trailers.get(BASELINE_TOOL_TRAILER) or None,
        "format_version": int(raw_format) if raw_format.isdigit() else None,
    }


_TAGGER_LINE = re.compile(rb"^tagger (.*?) <(.*?)> (\d+)(?: ([+-]\d{4}))?$")


def _parse_tag_object(raw: bytes) -> dict[str, Any]:
    """Tagger and message fields of a raw annotated tag object (``cat-file``)."""
    header, _sep, message = raw.partition(b"\n\n")
    tagger = None
    tagger_date = None
    for line in header.splitlines():
        match = _TAGGER_LINE.match(line)
        if match:
            tagger = {
                "name": match.group(1).decode("utf-8", "replace"),
                "email": match.group(2).decode("utf-8", "replace"),
            }
            try:
                offset = match.group(4).decode() if match.group(4) else "+0000"
                tz = timezone(
                    timedelta(hours=int(offset[:3]), minutes=int(offset[0] + offset[3:]))
                )
                tagger_date = datetime.fromtimestamp(int(match.group(3)), tz).isoformat()
            except (ValueError, OverflowError, OSError):
                tagger_date = None
    fields = _parse_baseline_message(message.decode("utf-8", "replace"))
    return {**fields, "tagger": tagger, "tagger_date": tagger_date}


def data_pathspec(path: str) -> str | None:
    """The ``DATA_PATHSPECS`` entry matching a project-relative path, or ``None`` if it is no data file."""
    for pattern, spec in _DATA_FILES:
        if pattern.match(path):
            return spec
    return None


@dataclass(frozen=True)
class GitSettings:
    """Server-wide git settings, from environment variables (see ``from_env``)."""

    enabled: bool = True
    squash_window_seconds: float = DEFAULT_SQUASH_MINUTES * 60
    author_name: str | None = None
    author_email: str | None = None
    executable: str = "git"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "GitSettings":
        """Settings from the environment.

        - ``REQMAN_GIT``: ``off``/``0``/``false``/``no`` turns history off (default on).
        - ``REQMAN_GIT_SQUASH_MINUTES``: the squash window in minutes (default
          10; ``0`` never squashes). An invalid value falls back to the default.
        - ``REQMAN_GIT_AUTHOR_NAME``/``REQMAN_GIT_AUTHOR_EMAIL``: the commit
          author; each falls back to what git itself would use (the
          repository's and the user's git config, ``GIT_AUTHOR_*``), then
          to the login name and ``<login>@localhost``.
        - ``REQMAN_GIT_EXECUTABLE``: the git command (default ``git``).
        """
        env = os.environ if environ is None else environ
        enabled = env.get("REQMAN_GIT", "").strip().lower() not in _OFF_VALUES
        window = DEFAULT_SQUASH_MINUTES
        raw_window = env.get("REQMAN_GIT_SQUASH_MINUTES", "").strip()
        if raw_window:
            try:
                window = float(raw_window)
                if not window >= 0 or window == float("inf"):
                    raise ValueError(raw_window)
            except ValueError:
                logger.warning(
                    "Ignoring REQMAN_GIT_SQUASH_MINUTES=%r: expected a number of minutes >= 0", raw_window
                )
                window = DEFAULT_SQUASH_MINUTES
        return cls(
            enabled=enabled,
            squash_window_seconds=window * 60,
            author_name=env.get("REQMAN_GIT_AUTHOR_NAME", "").strip() or None,
            author_email=env.get("REQMAN_GIT_AUTHOR_EMAIL", "").strip() or None,
            executable=env.get("REQMAN_GIT_EXECUTABLE", "").strip() or "git",
        )


@dataclass
class RepoInfo:
    """What a project folder is, git-wise."""

    state: str
    message: str | None = None
    git_dir: str | None = None  # absolute; set for ready/paused
    work_tree: Path | None = None
    enclosing: str | None = None  # the enclosing work tree's top level (nested)
    branch: str | None = None  # short branch name; None when detached
    detached: bool = False
    in_progress: bool = False  # paused by a merge/cherry-pick/revert/rebase or unresolved conflicts


@dataclass
class _Head:
    commit: str
    parents: list[str]
    author_name: str
    author_email: str
    committer_time: int
    date: str
    subject: str
    body: str


def _clean_env(extra: Mapping[str, str] | None = None, read_only: bool = False) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _REPO_ENV_VARS and not k.startswith(_DROPPED_ENV_PREFIXES)
    }
    env.update(
        {
            "LC_ALL": "C",
            "LANGUAGE": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_EDITOR": ":",
            "GIT_PAGER": "cat",
        }
    )
    if read_only:
        env["GIT_OPTIONAL_LOCKS"] = "0"
    if extra:
        env.update(extra)
    return env


def _subcommand(args: list[str]) -> str:
    """The git subcommand of ``args``: the first argument that is no option (nor a ``-c``/``-C`` value)."""
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in ("-c", "-C"):
            skip = True
        elif not arg.startswith("-"):
            return arg
    return args[0] if args else ""


_WINDOWS = os.name == "nt"
_hooks_lock = threading.Lock()
_hooks_path: str | None = None  # Windows only: the empty directory, or ``/dev/null`` after a failure


def _is_real_dir(path: str) -> bool:
    """An existing directory that is no symlink or junction (Windows reports no useful modes or owners)."""
    is_junction = getattr(os.path, "isjunction", None)
    return os.path.isdir(path) and not os.path.islink(path) and not (is_junction is not None and is_junction(path))


def _remove_dir(path: str) -> None:
    try:
        os.rmdir(path)
    except OSError:
        pass


def _no_hooks_dir() -> str:
    """The ``core.hooksPath`` of every git command: a path where git never finds a hook.

    On POSIX ``/dev/null``: git finds no executable ``/dev/null/<hook>``, and
    no one can remove or replace it. On Windows git would look for hooks in
    a ``dev\\null`` folder of the drive root, which other users may be able
    to create, so an empty directory is used instead: made once per process
    with ``tempfile.mkdtemp`` (in the user's own temp folder, removed at
    exit) and made again under the same name if it went missing (e.g.
    removed by a temp cleaner). If it cannot be made, or is no real
    directory (a symlink or junction), ``/dev/null`` is used for the rest of
    the process: never a new directory per command.
    """
    global _hooks_path
    if not _WINDOWS:
        return _DEV_NULL_HOOKS
    with _hooks_lock:
        path = _hooks_path
        if path == _DEV_NULL_HOOKS:
            return path
        try:
            if path is None:
                _hooks_path = _DEV_NULL_HOOKS  # unless the directory below is made and checked
                path = tempfile.mkdtemp(prefix="reqman-no-hooks-")
                atexit.register(_remove_dir, path)
            elif not os.path.lexists(path):
                os.mkdir(path, 0o700)
        except OSError as exc:
            logger.warning("Cannot create an empty hooks directory (%s); using %s", exc, _DEV_NULL_HOOKS)
            _hooks_path = _DEV_NULL_HOOKS
            return _hooks_path
        if not _is_real_dir(path):
            logger.warning("The hooks directory %s is no real directory; using %s", path, _DEV_NULL_HOOKS)
            path = _DEV_NULL_HOOKS
        _hooks_path = path
        return path


def _stderr(result: subprocess.CompletedProcess) -> str:
    text = result.stderr.decode("utf-8", "replace").strip() if result.stderr else ""
    return text or f"exit status {result.returncode}"


class GitHistory:
    """Git operations on project folders, with per-folder locking and error bookkeeping."""

    def __init__(self, settings: GitSettings | None = None, clock: Callable[[], float] = time.time):
        self.settings = settings if settings is not None else GitSettings(enabled=False)
        self.clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._last_errors: dict[str, str] = {}
        self._version: str | None = None
        self._version_checked = False
        # git dir -> (monotonic time, identity) of the last `git var` lookup
        self._identities: dict[str, tuple[float, tuple[str, str]]] = {}

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    # --- running git ------------------------------------------------------------

    def _run(
        self,
        args: list[str],
        cwd: Path | None,
        *,
        input: bytes | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        read_only: bool = False,
    ) -> subprocess.CompletedProcess:
        # No hooks, ever: ``--no-verify`` alone still runs prepare-commit-msg,
        # post-commit, post-rewrite, post-checkout, reference-transaction, ...
        # No other configured program either: no fsmonitor, no automatic gc or
        # maintenance after a commit. (``.gitattributes`` filters, e.g. Git LFS,
        # are honoured on purpose: they define how the repository stores files.)
        command = [
            self.settings.executable,
            "-c",
            f"core.hooksPath={_no_hooks_dir()}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            *args,
        ]
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                input=input,
                capture_output=True,
                env=_clean_env(env, read_only),
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitNotFound(f"git was not found ('{self.settings.executable}')") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitError(f"git {_subcommand(args)} could not run: {exc}") from exc
        if check and result.returncode != 0:
            raise GitError(f"git {_subcommand(args)} failed: {_stderr(result)}")
        return result

    def _repo(self, info: RepoInfo, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        """Run git on the project's own repository, named explicitly (never discovered)."""
        assert info.git_dir is not None and info.work_tree is not None
        return self._run(
            [f"--git-dir={info.git_dir}", f"--work-tree={info.work_tree}", *args], info.work_tree, **kwargs
        )

    def git_version(self) -> str | None:
        """``git --version`` output, or ``None`` if git cannot run (checked once per process)."""
        if not self._version_checked:
            try:
                result = self._run(["--version"], None)
                self._version = result.stdout.decode("utf-8", "replace").strip() or "git"
            except GitError:
                self._version = None
            self._version_checked = True
        return self._version

    def _lock(self, project_dir: Path) -> threading.Lock:
        key = str(project_dir)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    # --- detection ---------------------------------------------------------------

    def _toplevel(self, directory: Path) -> tuple[Path, str, Path] | None:
        """``(top level, absolute git dir, common dir)`` of the work tree containing ``directory``.

        ``None`` if ``directory`` is in no work tree. Discovery crosses file
        system boundaries, so an enclosing repository on another mount is
        still found.
        """
        result = self._run(
            ["rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir"],
            directory,
            check=False,
            read_only=True,
            env={"GIT_DISCOVERY_ACROSS_FILESYSTEM": "1"},
        )
        if result.returncode != 0:
            error = _stderr(result)
            if "not a git repository" in error:
                return None
            raise GitError(f"git cannot inspect '{directory}': {error}")
        lines = result.stdout.decode("utf-8", "replace").splitlines()
        if len(lines) < 3 or not lines[0] or not lines[2]:
            raise GitError(f"git cannot inspect '{directory}': unexpected rev-parse output")
        common = Path(lines[2])
        if not common.is_absolute():
            common = directory / common  # relative to the directory git ran in
        return Path(lines[0]).resolve(), lines[1], common.resolve()

    def detect(self, project_dir: Path) -> RepoInfo:
        """The git state of a project folder (see the ``STATE_*`` constants)."""
        if not self.settings.enabled:
            return RepoInfo(
                STATE_OFF,
                "Git history is turned off for this server (REQMAN_GIT=off): changes are saved as plain files.",
            )
        if self.git_version() is None:
            return RepoInfo(
                STATE_GIT_MISSING,
                f"git was not found ('{self.settings.executable}'): changes are saved as plain files without "
                "history. Install git and restart ReqManTool to record history.",
            )
        folder = project_dir.resolve()
        try:
            enclosing = self._toplevel(folder.parent)
            if enclosing is not None:
                return RepoInfo(
                    STATE_NESTED,
                    f"The data directory is inside the git repository at '{enclosing[0]}'. ReqManTool never "
                    "creates or commits to a repository inside another one, so history is off for this "
                    "project. Move the data directory out of that repository to enable history.",
                    enclosing=str(enclosing[0]),
                )
            dot_git = folder / ".git"
            if dot_git.is_symlink():
                # Whatever it points to, it is not the folder's own repository.
                return RepoInfo(STATE_FOREIGN, self._foreign_message(str(dot_git.resolve())))
            own = self._toplevel(folder)
            if own is None:
                return RepoInfo(
                    STATE_NO_REPO,
                    "This project has no git repository: changes are saved as plain files without history. "
                    "Use “Enable history (git)” to start recording history.",
                )
            toplevel, git_dir, common_dir = own
            if toplevel != folder:  # cannot happen when the parent is not in a work tree; be safe
                return RepoInfo(
                    STATE_NESTED,
                    f"The project folder is inside the git repository at '{toplevel}'. Move the data "
                    "directory out of that repository to enable history.",
                    enclosing=str(toplevel),
                )
            # ``folder`` is a real path and ``.git`` is no symlink, so ``own_git_dir``
            # is the folder's own ``.git``, not where a link points to.
            own_git_dir = folder / ".git"
            if (
                not own_git_dir.is_dir()
                or own_git_dir.is_symlink()
                or Path(git_dir).resolve() != own_git_dir
                or common_dir != own_git_dir
            ):
                # A ``.git`` file (``gitdir: ...``) or a linked worktree: committing or
                # switching branches here would change the other repository.
                return RepoInfo(STATE_FOREIGN, self._foreign_message(str(common_dir)))
            info = RepoInfo(STATE_READY, None, git_dir=git_dir, work_tree=folder)
            ref = self._repo(info, ["symbolic-ref", "-q", "HEAD"], check=False, read_only=True)
            full = ref.stdout.decode("utf-8", "replace").strip() if ref.returncode == 0 else ""
            if ref.returncode == 0:
                info.branch = full.removeprefix("refs/heads/")
            else:
                info.detached = True
            busy = self._operation_in_progress(info)
            if busy is not None:
                info.state = STATE_PAUSED
                info.in_progress = True
                info.message = (
                    f"{busy}: auto-commit is paused (changes are still saved as files; nothing is staged or "
                    "committed). Finish or abort it and resolve any conflicts to resume history."
                )
                return info
            if ref.returncode == 0:
                if full == WORKING_REF:
                    return info
                info.state = STATE_PAUSED
                info.message = (
                    f"The project repository is on branch '{info.branch}', not '{WORKING_BRANCH}': "
                    "auto-commit is paused (changes are still saved as files). "
                    f"Switch back to '{WORKING_BRANCH}' to resume, or use “Enable history (git)” "
                    f"if there is no '{WORKING_BRANCH}' branch yet."
                )
                return info
            info.state = STATE_PAUSED
            info.message = (
                "The project repository has a detached HEAD: auto-commit is paused (changes are still "
                f"saved as files). Check out the '{WORKING_BRANCH}' branch to resume."
            )
            return info
        except GitError as exc:
            return RepoInfo(STATE_ERROR, f"git cannot inspect this project folder: {exc}")

    @staticmethod
    def _foreign_message(other: str) -> str:
        return (
            "The project folder's .git points to another repository (separate git dir or linked "
            f"worktree); history is not supported for it. That repository ('{other}') is "
            "never changed; changes are saved as plain files."
        )

    def _operation_in_progress(self, info: RepoInfo) -> str | None:
        """What keeps the repository busy (an operation in progress or unresolved conflicts), or ``None``.

        Cheap enough for every mutation: a few file checks in the git dir and
        one ``git ls-files -u``. Unmerged entries anywhere count, not just in
        data files: a deliberate, conservative choice (``commit --only``
        itself would still work outside a merge). Staging a conflicted data
        file would mark it resolved with the markers inside, and the user is
        visibly in the middle of resolving conflicts.
        """
        git_dir = Path(info.git_dir or "")
        for name, operation in _OPERATIONS_IN_PROGRESS:
            if os.path.lexists(git_dir / name):
                return f"{operation} is in progress in the project repository"
        unmerged = self._repo(info, ["ls-files", "-u", "-z"], read_only=True)
        if unmerged.stdout:
            return (
                "The project repository has unresolved conflicts (unmerged files, e.g. after a "
                "'git stash pop' conflict)"
            )
        return None

    # --- repository facts -----------------------------------------------------------

    def _head(self, info: RepoInfo, read_only: bool = False) -> _Head | None:
        """The HEAD commit, or ``None`` on an unborn branch."""
        result = self._repo(
            info,
            ["log", "-1", "--no-show-signature", "--format=%H%x00%P%x00%an%x00%ae%x00%ct%x00%cI%x00%s%x00%B"],
            check=False,
            read_only=read_only,
        )
        if result.returncode != 0:
            verify = self._repo(info, ["rev-parse", "-q", "--verify", "HEAD"], check=False, read_only=True)
            if verify.returncode != 0:
                return None
            raise GitError(f"git log failed: {_stderr(result)}")
        fields = result.stdout.decode("utf-8", "replace").split("\x00")
        if len(fields) < 8:
            raise GitError("git log returned an unexpected format")
        return _Head(
            commit=fields[0],
            parents=fields[1].split(),
            author_name=fields[2],
            author_email=fields[3],
            committer_time=int(fields[4] or 0),
            date=fields[5],
            subject=fields[6],
            body=fields[7],
        )

    def _changed_paths(self, info: RepoInfo, read_only: bool = False) -> list[str]:
        """Data files that differ from HEAD (modified, added, deleted, untracked); nothing else."""
        result = self._repo(
            info,
            ["status", "--porcelain", "-z", "-uall", "--no-renames", "--", *DATA_PATHSPECS],
            read_only=read_only,
        )
        paths = []
        for record in result.stdout.decode("utf-8", "surrogateescape").split("\x00"):
            if len(record) > 3 and data_pathspec(record[3:]) is not None:
                paths.append(record[3:])
        return paths

    def _identity(self, info: RepoInfo) -> tuple[str, str]:
        """The commit author: configured, else git's own (config / GIT_AUTHOR_*), else the login name."""
        name, email = self.settings.author_name, self.settings.author_email
        if name and email:
            return name, email
        cached = self._identities.get(info.git_dir or "")
        if cached is not None and time.monotonic() - cached[0] < _IDENTITY_TTL_SECONDS:
            return cached[1]
        identity = self._resolve_identity(info, name, email)
        self._identities[info.git_dir or ""] = (time.monotonic(), identity)
        return identity

    def _resolve_identity(self, info: RepoInfo, name: str | None, email: str | None) -> tuple[str, str]:
        if not (name and email):
            result = self._repo(info, ["var", "GIT_AUTHOR_IDENT"], check=False, read_only=True)
            match = re.match(r"^(.*?) <(.*)> \d+ [+-]\d{4}$", result.stdout.decode("utf-8", "replace").strip())
            if result.returncode == 0 and match:
                name = name or match.group(1).strip() or None
                email = email or match.group(2).strip() or None
        if not (name and email):
            try:
                login = getpass.getuser()
            except Exception:  # noqa: BLE001 - no login name available
                login = "reqman"
            name = name or login
            email = email or f"{login}@localhost"
        return name, email

    def _can_squash(self, info: RepoInfo, head: _Head, identity: tuple[str, str]) -> bool:
        window = self.settings.squash_window_seconds
        if window <= 0 or len(head.parents) != 1 or session_of(head.body) is None:
            return False
        if (head.author_name, head.author_email) != identity:
            return False
        if self.clock() - head.committer_time > window:
            return False
        refs = self._repo(info, ["for-each-ref", "--contains", head.commit, "--format=%(refname)"])
        others = [r for r in refs.stdout.decode("utf-8", "replace").split() if r != WORKING_REF]
        return not others

    def _blobs(
        self,
        info: RepoInfo,
        specs: list[str],
        read_only: bool = False,
        kinds: tuple[bytes, ...] = (b"blob",),
    ) -> list[bytes | None]:
        """Contents of object specs via one ``git cat-file --batch`` (``None`` if missing).

        Only objects of the wanted ``kinds`` (blobs by default; tag objects
        for reading baseline tags) return their bytes; anything else is ``None``.
        """
        if not specs:
            return []
        result = self._repo(
            info, ["cat-file", "--batch"], input="".join(f"{s}\n" for s in specs).encode(), read_only=read_only
        )
        out = result.stdout
        blobs: list[bytes | None] = []
        pos = 0
        for _spec in specs:
            end = out.index(b"\n", pos)
            header = out[pos:end].split()
            pos = end + 1
            if len(header) == 3 and header[1] in (b"blob", b"tree", b"commit", b"tag"):
                size = int(header[2])
                blobs.append(out[pos : pos + size] if header[1] in kinds else None)
                pos += size + 1
            else:  # "<spec> missing" / "ambiguous"
                blobs.append(None)
        return blobs

    def _ensure_excludes(self, info: RepoInfo) -> None:
        result = self._repo(info, ["rev-parse", "--git-path", "info/exclude"])
        path = Path(result.stdout.decode("utf-8", "replace").strip())
        if not path.is_absolute():
            path = info.work_tree / path  # type: ignore[operator]
        existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        missing = [p for p in EXCLUDE_PATTERNS if p not in existing.splitlines()]
        if missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            prefix = "" if not existing or existing.endswith("\n") else "\n"
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(prefix + "# ReqManTool: temporary files of atomic writes\n")
                handle.write("".join(f"{p}\n" for p in missing))

    # --- committing ---------------------------------------------------------------

    def _commit_pending(
        self,
        info: RepoInfo,
        describe: Callable[[list[FileChange]], list[str]] | None,
        *,
        message: str | None = None,
        allow_squash: bool = True,
    ) -> bool:
        """Commit every changed data file on ``current``; amend the session commit when allowed.

        ``message`` (an initial/enable commit) is used as is, without a
        session trailer, so such a commit is never amended. Otherwise the
        message is generated by ``describe`` from the files' contents before
        and after. Returns whether a commit was made (or dropped).
        """
        changed = self._changed_paths(info)
        if not changed:
            return False
        # Only the data pathspecs that match a changed file: ``add``/``commit`` fail on
        # a pathspec matching nothing. (Patterns, not the file list: no length limit.)
        matched = {data_pathspec(path) for path in changed}
        pathspec = [spec for spec in DATA_PATHSPECS if spec in matched]
        head = self._head(info)
        identity = self._identity(info)
        amend = bool(message is None and allow_squash and head and self._can_squash(info, head, identity))
        # Checked again right before staging (``detect`` ran a few git commands
        # ago): a merge, rebase, ... the user started meanwhile pauses the commit.
        if self._operation_in_progress(info) is not None:
            return False
        self._repo(info, ["add", "-A", "--", *pathspec])

        if message is None:
            base = head.parents[0] if (amend and head) else (head.commit if head else None)
            if base is None:
                entries = describe([]) if describe else []
                changes: list[FileChange] = []
            else:
                # All data paths, not just those changed now: an amended
                # message describes the whole session (base = its parent).
                diff = self._repo(
                    info,
                    ["diff", "--cached", "--name-status", "-z", "--no-renames", base, "--", *DATA_PATHSPECS],
                )
                fields = [f for f in diff.stdout.decode("utf-8", "surrogateescape").split("\x00") if f]
                pairs = [p for p in zip(fields[0::2], fields[1::2]) if data_pathspec(p[1]) is not None]
                if not pairs:
                    if amend and head:
                        # The session's changes cancel out: drop the session commit.
                        self._repo(info, ["reset", "-q", "--soft", "HEAD~1"])
                        return True
                    return False
                old_specs = [f"{base}:{path}" for status, path in pairs if status != "A"]
                old_blobs = iter(self._blobs(info, old_specs))
                changes = []
                for status, path in pairs:
                    old = next(old_blobs) if status != "A" else None
                    new = None
                    if status != "D":
                        try:
                            new = (info.work_tree / path).read_bytes()  # type: ignore[operator]
                        except OSError:
                            new = None
                    changes.append(FileChange(path=path, old=old, new=new))
                entries = describe(changes) if describe else []
            session = session_of(head.body) if (amend and head) else uuid.uuid4().hex[:12]
            message = commit_message(entries, session)

        name, email = identity
        env = {
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
        # No signing and no hooks (``_run`` disables every hook; ``--no-verify`` is
        # belt and braces): an auto-commit must never wait for input.
        args = ["-c", "commit.gpgsign=false", "commit", "-q", "--no-verify"]
        args += ["--amend"] if amend else []
        args += ["--only", "-F", "-", "--", *pathspec]
        self._repo(info, args, input=message.encode("utf-8"), env=env)
        return True

    def _record(self, project_dir: Path, error: str | None) -> None:
        key = str(project_dir)
        if error is None:
            self._last_errors.pop(key, None)
        else:
            self._last_errors[key] = error
            logger.warning("Git history for %s: %s", project_dir, error)

    def auto_commit(self, project_dir: Path, describe: Callable[[list[FileChange]], list[str]]) -> None:
        """Commit a project's changed data files after a mutation. Never raises.

        Does nothing unless the folder is ``ready`` (own repository on
        ``current``). A git failure is recorded as ``last_error``.
        """
        if not self.settings.enabled:
            return
        with self._lock(project_dir):
            try:
                info = self.detect(project_dir)
                if info.state != STATE_READY:
                    return
                self._commit_pending(info, describe)
                self._record(project_dir, None)
            except Exception as exc:  # noqa: BLE001 - the files are saved; never fail the mutation
                if not isinstance(exc, (GitError, OSError)):
                    logger.exception("Unexpected auto-commit error for %s", project_dir)
                self._record(project_dir, f"Auto-commit failed (the files are saved; retried on the next change): {exc}")

    def _init(self, project_dir: Path) -> RepoInfo:
        """``git init`` a folder that ``detect`` reported as ``no_repo``, on branch ``current``.

        No template is copied (``--template=``), so no hook from
        ``init.templateDir`` lands in the repository; ``_ensure_excludes``
        writes ``info/exclude`` itself.
        """
        self._run(["init", "-q", "--template="], project_dir)
        info = self.detect(project_dir)
        if info.git_dir is None:
            raise GitError(f"the new repository was not found ({info.message})")
        self._repo(info, ["symbolic-ref", "HEAD", WORKING_REF])
        self._ensure_excludes(info)
        info = self.detect(project_dir)
        if info.state != STATE_READY:
            raise GitError(f"the new repository is not usable ({info.message})")
        return info

    def init_new_project(self, project_dir: Path, project_name: str) -> None:
        """Give a newly created project its repository and initial commit. Never raises.

        Skipped (the status explains why) when git is off or missing, or the
        folder is nested in another repository's work tree.
        """
        if not self.settings.enabled:
            return
        with self._lock(project_dir):
            try:
                info = self.detect(project_dir)
                if info.state != STATE_NO_REPO:
                    return
                info = self._init(project_dir)
                self._commit_pending(info, None, message=f"Create project '{project_name}'\n")
                self._record(project_dir, None)
            except Exception as exc:  # noqa: BLE001 - the project is created; never fail it
                if not isinstance(exc, (GitError, OSError)):
                    logger.exception("Unexpected error creating the repository of %s", project_dir)
                self._record(project_dir, f"Creating the project repository failed: {exc}")

    def enable(
        self, project_dir: Path, project_name: str, describe: Callable[[list[FileChange]], list[str]]
    ) -> None:
        """Start recording history for an existing project ("Enable history (git)").

        - no repository: ``git init`` on ``current`` and commit the data files;
        - own repository on another branch, with no ``current`` branch yet:
          create ``current`` at HEAD and switch to it (this changes no file),
          then commit pending data changes;
        - already on ``current``: commit pending data changes (idempotent).

        Raises ``ValueError`` when git is off or missing, ``RepoConflict``
        for a nested or foreign folder, a merge/cherry-pick/revert/rebase in
        progress or unresolved conflicts, a detached HEAD or an existing
        ``current`` branch that is not checked out (ReqManTool never switches
        to it itself, since that would change files) — none of which changes
        anything — and ``EnableFailed`` when a step failed (recorded as
        ``last_error``; earlier steps, e.g. ``git init``, may have succeeded).
        """
        if not self.settings.enabled:
            raise ValueError(
                "Git history is turned off for this server (REQMAN_GIT=off); restart without it to enable history"
            )
        with self._lock(project_dir):
            info = self.detect(project_dir)
            if info.state in (STATE_GIT_MISSING, STATE_ERROR, STATE_OFF):
                raise ValueError(info.message or "git is not available")
            if info.state in (STATE_NESTED, STATE_FOREIGN):
                raise RepoConflict(info.message or "The project folder is inside another git repository")
            try:
                if info.state == STATE_NO_REPO:
                    info = self._init(project_dir)
                    self._commit_pending(info, None, message=f"Enable history (git) for project '{project_name}'\n")
                elif info.state == STATE_PAUSED:
                    if info.in_progress or info.detached:
                        raise RepoConflict(info.message or "The project repository is busy or has a detached HEAD")
                    exists = self._repo(
                        info, ["show-ref", "--verify", "-q", WORKING_REF], check=False, read_only=True
                    )
                    if exists.returncode == 0:
                        raise RepoConflict(
                            f"The project repository is on branch '{info.branch}' and a '{WORKING_BRANCH}' "
                            f"branch already exists. Switch to it yourself (git switch {WORKING_BRANCH}); "
                            "ReqManTool does not switch branches, since that changes files."
                        )
                    if self._head(info) is None:
                        self._repo(info, ["symbolic-ref", "HEAD", WORKING_REF])
                    else:
                        self._repo(info, ["checkout", "-q", "-b", WORKING_BRANCH])
                    self._ensure_excludes(info)
                    info = self.detect(project_dir)
                    if info.state != STATE_READY:
                        raise GitError(info.message or "the repository is not on the working branch")
                    if self._head(info) is None:
                        self._commit_pending(
                            info, None, message=f"Enable history (git) for project '{project_name}'\n"
                        )
                    else:
                        self._commit_pending(info, describe, allow_squash=False)
                else:  # ready
                    self._ensure_excludes(info)
                    if self._head(info) is None:
                        self._commit_pending(
                            info, None, message=f"Enable history (git) for project '{project_name}'\n"
                        )
                    else:
                        self._commit_pending(info, describe)
            except RepoConflict:
                raise  # refused before anything was changed
            except Exception as exc:  # noqa: BLE001 - reported as a 400 with the recorded error
                if not isinstance(exc, (GitError, OSError)):
                    logger.exception("Unexpected error enabling history for %s", project_dir)
                self._record(project_dir, f"Enabling history failed: {exc}")
                raise EnableFailed(f"Enabling history failed: {exc}") from exc
            self._record(project_dir, None)

    # --- baselines ---------------------------------------------------------------

    def _rev(self, info: RepoInfo, ref: str) -> str | None:
        """The commit a ref resolves to, or ``None`` if it does not exist."""
        result = self._repo(info, ["rev-parse", "-q", "--verify", f"{ref}^{{commit}}"], check=False, read_only=True)
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", "replace").strip() or None

    def create_baseline(
        self,
        project_dir: Path,
        name: str,
        description: str,
        tool_version: str,
        format_version: int,
        describe: Callable[[list[FileChange]], list[str]],
    ) -> dict[str, Any]:
        """Create baseline ``name``: commit pending changes, tag, fast-forward ``baseline``.

        Order (so a mid-way failure leaves a consistent state): every check
        that can refuse runs first and changes nothing; then (1) pending data
        changes are committed — exactly the commit the next auto-commit would
        have made, consistent on its own; (2) the annotated tag
        ``baseline/<name>`` is created at HEAD; (3) ``refs/heads/baseline``
        is fast-forwarded to HEAD with a compare-and-swap on its old value —
        if that fails, the tag is deleted again. Once the tag and branch
        contain HEAD, the squash rule (ref containment) makes sure no later
        change ever amends the baselined commit, which also closes the
        squash window.

        Raises ``ValueError`` for an invalid name or git off/missing,
        ``RepoConflict`` (409) when the folder is not ``ready``, the tag
        exists, or ``baseline`` was moved by hand so it is no longer an
        ancestor of ``current``, and ``BaselineFailed`` when a later step
        failed (recorded as ``last_error``).
        """
        name = validate_baseline_name(name)
        if not self.settings.enabled:
            raise ValueError(
                "Git history is turned off for this server (REQMAN_GIT=off); baselines need history"
            )
        with self._lock(project_dir):
            info = self.detect(project_dir)
            if info.state in (STATE_GIT_MISSING, STATE_ERROR, STATE_OFF):
                raise ValueError(info.message or "git is not available")
            if info.state != STATE_READY:
                raise RepoConflict(
                    info.message
                    or "The project is not recording history, so a baseline cannot be created"
                )
            tag_ref = f"refs/tags/{BASELINE_TAG_PREFIX}{name}"
            fmt = self._repo(info, ["check-ref-format", tag_ref], check=False, read_only=True)
            if fmt.returncode != 0:
                raise ValueError(f"Invalid baseline name '{name}': git does not accept it as a tag name")
            if self._rev(info, tag_ref) is not None:
                raise RepoConflict(f"A baseline named '{name}' already exists")
            if self._head(info) is None and not self._changed_paths(info, read_only=True):
                raise RepoConflict("The project has no commit and no changes yet; nothing to baseline")
            old_baseline = self._rev(info, BASELINE_REF)
            if old_baseline is not None:
                ancestor = self._repo(
                    info, ["merge-base", "--is-ancestor", old_baseline, "HEAD"], check=False, read_only=True
                )
                if ancestor.returncode == 1:
                    raise RepoConflict(
                        f"The '{BASELINE_BRANCH}' branch is not an ancestor of '{WORKING_BRANCH}' (it was "
                        "moved by hand); nothing was changed. Move it back onto the "
                        f"'{WORKING_BRANCH}' history to create baselines again."
                    )
                if ancestor.returncode != 0:
                    raise RepoConflict(f"git merge-base failed: {_stderr(ancestor)}")
            try:
                # (1) The baseline covers exactly what is on disk.
                self._commit_pending(info, describe)
                head = self._head(info)
                if head is None:
                    raise GitError("the repository has no commit to baseline")
                date = datetime.now(timezone.utc).isoformat(timespec="seconds")
                parts = [f"Baseline {name}"]
                if description.strip():
                    parts.append(description.strip())
                parts.append(
                    "\n".join(
                        [
                            f"{BASELINE_TRAILER}: {name}",
                            f"{BASELINE_DATE_TRAILER}: {date}",
                            f"{BASELINE_TOOL_TRAILER}: {tool_version}",
                            f"{BASELINE_FORMAT_TRAILER}: {format_version}",
                        ]
                    )
                )
                message = "\n\n".join(parts) + "\n"
                tagger_name, tagger_email = self._identity(info)
                env = {
                    "GIT_AUTHOR_NAME": tagger_name,
                    "GIT_AUTHOR_EMAIL": tagger_email,
                    "GIT_COMMITTER_NAME": tagger_name,
                    "GIT_COMMITTER_EMAIL": tagger_email,
                }
                # (2) The tag (the baseline's source of truth), …
                self._repo(
                    info,
                    ["-c", "tag.gpgsign=false", "tag", "-a", f"{BASELINE_TAG_PREFIX}{name}", "-F", "-", "HEAD"],
                    input=message.encode("utf-8"),
                    env=env,
                )
                # … then (3) the branch, compare-and-swapped against the value
                # checked above; if it fails, the tag is removed again.
                try:
                    self._repo(info, ["update-ref", BASELINE_REF, head.commit, old_baseline or ""])
                except GitError:
                    self._repo(info, ["update-ref", "-d", tag_ref], check=False)
                    raise
            except RepoConflict:
                raise
            except Exception as exc:  # noqa: BLE001 - reported as a 400 with the recorded error
                if not isinstance(exc, (GitError, OSError)):
                    logger.exception("Unexpected error creating a baseline for %s", project_dir)
                self._record(project_dir, f"Creating baseline '{name}' failed: {exc}")
                raise BaselineFailed(f"Creating baseline '{name}' failed: {exc}") from exc
            self._record(project_dir, None)
            return {
                "name": name,
                "commit": head.commit,
                "annotated": True,
                "description": description.strip(),
                "date": date,
                "tagger": {"name": tagger_name, "email": tagger_email},
                "tagger_date": date,
                "tool_version": tool_version,
                "format_version": format_version,
            }

    def list_baselines(self, project_dir: Path) -> dict[str, Any]:
        """The project's baselines from its ``baseline/*`` tags, newest first.

        A lightweight or foreign ``baseline/*`` tag (no annotated tag object,
        or a message without the ``Reqman-*`` trailers) is listed too, with
        ``annotated``/``description``/version fields reduced accordingly, so
        a tag the user made by hand still shows up. ``available: false`` with
        a ``reason`` when the project has no readable repository.
        """
        info = self.detect(project_dir)
        empty: dict[str, Any] = {"available": False, "reason": info.message, "baselines": []}
        if info.state not in (STATE_READY, STATE_PAUSED):
            return empty
        try:
            result = self._repo(
                info,
                [
                    "for-each-ref",
                    "--sort=-creatordate",
                    "--format=%(refname)%00%(objecttype)%00%(objectname)%00%(*objectname)%00%(creatordate:iso-strict)",
                    f"refs/tags/{BASELINE_TAG_PREFIX}",
                ],
                read_only=True,
            )
            rows = [
                line.split("\x00")
                for line in result.stdout.decode("utf-8", "replace").splitlines()
                if line.count("\x00") == 4
            ]
            tag_objects = iter(
                self._blobs(
                    info, [row[2] for row in rows if row[1] == "tag"], read_only=True, kinds=(b"tag",)
                )
            )
            baselines = []
            for refname, objecttype, objectname, peeled, created in rows:
                entry: dict[str, Any] = {
                    "name": refname.removeprefix(f"refs/tags/{BASELINE_TAG_PREFIX}"),
                    "commit": peeled or objectname,
                    "annotated": objecttype == "tag",
                    "description": None,
                    "date": created or None,
                    "tagger": None,
                    "tagger_date": None,
                    "tool_version": None,
                    "format_version": None,
                }
                if objecttype == "tag":
                    raw = next(tag_objects)
                    if raw is not None:
                        fields = _parse_tag_object(raw)
                        entry.update(fields)
                        entry["date"] = fields.get("date") or fields.get("tagger_date") or entry["date"]
                baselines.append(entry)
            return {"available": True, "reason": None, "baselines": baselines}
        except GitError as exc:
            return {**empty, "reason": f"The baselines cannot be read: {exc}"}

    def baseline_files(self, project_dir: Path, name: str) -> tuple[str, dict[str, bytes | None]]:
        """``(commit, {relative path: bytes})`` of the data files at baseline ``name``.

        Read from the tag's commit in memory (``ls-tree`` + ``cat-file
        --batch``), never a checkout. ``FileNotFoundError`` when the baseline
        (or a readable repository) does not exist.
        """
        missing = FileNotFoundError(f"Baseline '{name}' not found")
        if not BASELINE_NAME_PATTERN.match(name or ""):
            raise missing
        info = self.detect(project_dir)
        if info.state not in (STATE_READY, STATE_PAUSED):
            raise missing
        commit = self._rev(info, f"refs/tags/{BASELINE_TAG_PREFIX}{name}")
        if commit is None:
            raise missing
        tree = self._repo(info, ["ls-tree", "-r", "-z", "--name-only", commit], read_only=True)
        paths = [
            p
            for p in tree.stdout.decode("utf-8", "surrogateescape").split("\x00")
            if p and data_pathspec(p) is not None
        ]
        blobs = self._blobs(info, [f"{commit}:{path}" for path in paths], read_only=True)
        return commit, dict(zip(paths, blobs))

    def log(self, project_dir: Path, limit: int) -> dict[str, Any]:
        """The newest ``limit`` commits of ``current`` (first-parent, newest first), for the Git panel."""
        info = self.detect(project_dir)
        empty: dict[str, Any] = {"available": False, "reason": info.message, "entries": [], "truncated": False}
        if info.state not in (STATE_READY, STATE_PAUSED):
            return empty
        try:
            if self._head(info, read_only=True) is None:
                return {**empty, "available": True, "reason": None}
            result = self._repo(
                info,
                [
                    "log",
                    "--first-parent",
                    "--no-show-signature",
                    f"--max-count={limit + 1}",
                    "--format=%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%s",
                    "HEAD",
                ],
                read_only=True,
            )
            entries = []
            for record in result.stdout.decode("utf-8", "replace").split("\x1e"):
                meta = record.strip("\n").split("\x1f")
                if len(meta) >= 6:
                    entries.append(
                        {
                            "commit": meta[0],
                            "author_name": meta[1],
                            "author_email": meta[2],
                            "authored_date": meta[3],
                            "date": meta[4],
                            "subject": meta[5],
                        }
                    )
            return {
                "available": True,
                "reason": None,
                "entries": entries[:limit],
                "truncated": len(entries) > limit,
            }
        except GitError as exc:
            return {**empty, "reason": f"The log cannot be read: {exc}"}

    # --- reading ----------------------------------------------------------------

    def status(self, project_dir: Path) -> dict[str, Any]:
        """The project's git status for the UI: state, explanation, branch, HEAD, pending changes."""
        info = self.detect(project_dir)
        result: dict[str, Any] = {
            "state": info.state,
            "message": info.message,
            "git_version": self.git_version() if self.settings.enabled else None,
            "branch": info.branch,
            "head": None,
            "enclosing_repo": info.enclosing,
            "pending_changes": 0,
            "last_error": self._last_errors.get(str(project_dir)),
            "can_enable": info.state == STATE_NO_REPO,
            "squash_window_minutes": self.settings.squash_window_seconds / 60,
            "author": None,
        }
        if info.state not in (STATE_READY, STATE_PAUSED):
            return result
        try:
            head = self._head(info, read_only=True)
            if head is not None:
                result["head"] = {
                    "commit": head.commit,
                    "subject": head.subject,
                    "author_name": head.author_name,
                    "author_email": head.author_email,
                    "date": head.date,
                }
            result["pending_changes"] = len(self._changed_paths(info, read_only=True))
            name, email = self._identity(info)
            result["author"] = {"name": name, "email": email}
            if info.state == STATE_PAUSED and not (info.detached or info.in_progress):
                exists = self._repo(info, ["show-ref", "--verify", "-q", WORKING_REF], check=False, read_only=True)
                result["can_enable"] = exists.returncode != 0
            elif info.state == STATE_READY and head is None:
                result["can_enable"] = True
        except GitError as exc:
            result["state"] = STATE_ERROR
            result["message"] = f"git cannot read this project's repository: {exc}"
        return result

    def requirement_history(
        self, project_dir: Path, relative_path: str, requirement_id: str, limit: int | None = None
    ) -> dict[str, Any]:
        """The change list and attribute blame of one requirement file (see ``history.py``).

        The commits are those of ``git log --first-parent`` that changed the
        file (at most ``limit``, default ``HISTORY_LIMIT``), so each is compared
        with its first parent: a merge is listed with what it brought into
        the branch, and commits of a merged side branch are not listed
        themselves. No ``--follow``: requirement files are never renamed.

        Returns ``available: false`` with a ``reason`` when the project has no
        readable repository (git off or missing, no repository, nested, foreign).
        """
        if limit is None:
            limit = HISTORY_LIMIT  # read at call time
        info = self.detect(project_dir)
        empty: dict[str, Any] = {
            "available": False,
            "reason": info.message,
            "entries": [],
            "blame": {},
            "truncated": False,
            "uncommitted": False,
        }
        if info.state not in (STATE_READY, STATE_PAUSED):
            return empty
        try:
            if self._head(info, read_only=True) is None:
                return {**empty, "available": True, "reason": None, "uncommitted": True}
            log = self._repo(
                info,
                [
                    "log",
                    "--first-parent",
                    "--no-show-signature",
                    f"--max-count={limit + 1}",
                    "--format=%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%s",
                    "HEAD",
                    "--",
                    relative_path,
                ],
                read_only=True,
                env=_LITERAL_PATHS,
            )
            commits: list[list[str]] = []
            for record in log.stdout.decode("utf-8", "replace").split("\x1e"):
                meta = record.strip("\n").split("\x1f")
                if len(meta) >= 6:
                    commits.append(meta)
            blobs = self._blobs(info, [f"{meta[0]}:{relative_path}" for meta in commits], read_only=True)
            revisions = [
                Revision(
                    commit=meta[0],
                    author_name=meta[1],
                    author_email=meta[2],
                    authored_date=meta[3],
                    date=meta[4],
                    subject=meta[5],
                    content=blob,
                )
                for meta, blob in zip(commits, blobs)
            ]
            result = requirement_history(requirement_id, revisions, truncated=len(revisions) > limit)
            pending = self._repo(
                info, ["status", "--porcelain", "--", relative_path], read_only=True, env=_LITERAL_PATHS
            )
            return {
                "available": True,
                "reason": None,
                **result,
                "uncommitted": bool(pending.stdout.strip()),
            }
        except (GitError, ValueError) as exc:
            return {**empty, "reason": f"The history cannot be read: {exc}"}
