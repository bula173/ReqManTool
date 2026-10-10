"""Tool version and build id, shared by ``GET /version`` and the
``version.get`` WebSocket method.

- ``version`` is ``reqman.__version__`` — the single version source, also
  read by the frontend build (``frontend/vite.config.ts``).
- ``build`` identifies the running backend code:
  1. ``REQMAN_BUILD`` environment variable, if set and non-blank (for
     packaged releases or CI, where there is no git checkout);
  2. otherwise the ``BUILD`` file inside the package, written by the release
     build (``etc/build-dist.sh``) — an installed package has no checkout;
  3. otherwise ``"<short commit> (<commit date YYYY-MM-DD>)"`` from the git
     checkout this package lives in, computed once per process. The Python
     backend has no build step, so the *commit* date is used — it is stable
     across server restarts and names the code actually running;
  4. otherwise ``"dev"`` (git missing, not a checkout, or git failed).

``about_info()`` (``GET /about``, ``about.get``) adds what AGPL-3.0 §13 asks
the app to offer its network users: the licence and where the corresponding
source of *this* build is. ``source_ref`` is the git commit or release tag
the build names, or ``None`` when the build is not tied to a published
revision (``dev``, a build id marked ``-dirty``, an unrecognised
``REQMAN_BUILD``, or a checkout with uncommitted changes to tracked files);
``source_link`` then points at the repository itself. The repository is
``source_url`` from the settings (``config.py``), so a modified deployment
can point at its own published source.
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from . import __version__

DEV_BUILD = "dev"
BUILD_ENV_VAR = "REQMAN_BUILD"
_GIT_TIMEOUT_SECONDS = 2
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@lru_cache(maxsize=1)
def _git_build() -> str:
    try:
        result = subprocess.run(
            # %cd + --date=short rather than %cs, which needs git >= 2.21.
            # --no-show-signature (git >= 2.10): a `log.showSignature=true`
            # config would otherwise add gpg lines to stdout.
            ["git", "log", "-1", "--no-show-signature", "--format=%h %cd", "--date=short"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return DEV_BUILD
    parts = result.stdout.split()
    if result.returncode != 0 or len(parts) != 2:
        return DEV_BUILD
    commit, date = parts
    if not _DATE.fullmatch(date):
        return DEV_BUILD
    return f"{commit} ({date})"


BUILD_FILE = Path(__file__).resolve().parent / "BUILD"


@lru_cache(maxsize=1)
def _packaged_build() -> str:
    try:
        return BUILD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_id() -> str:
    override = os.environ.get(BUILD_ENV_VAR, "").strip()
    return override or _packaged_build() or _git_build()


def version_info() -> dict[str, str]:
    return {"version": __version__, "build": build_id()}


# --- licence and corresponding source (AGPL-3.0 §13) --------------------

LICENSE_SPDX = "AGPL-3.0-only"
# A git-derived build id: "<short commit> (<YYYY-MM-DD>)" (see _git_build and etc/build-dist.sh).
_COMMIT_BUILD = re.compile(r"([0-9a-f]{7,40}) \(\d{4}-\d{2}-\d{2}\)")
# A release tag used as the build id (REQMAN_BUILD in CI), e.g. "v0.3.0" or "0.3.0-rc1".
_TAG_BUILD = re.compile(r"v?\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.+-]*)?")


@lru_cache(maxsize=1)
def _git_worktree_modified() -> bool:
    """True when the checkout this package lives in has uncommitted changes to tracked files.

    Untracked files are ignored (they are not part of the running code's
    source tree as published). Errors count as "not modified": the build id
    then already says ``dev`` when git is unusable.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def source_ref(build: str, from_git_checkout: bool) -> str | None:
    """The git commit or tag ``build`` names, or ``None`` if it names no published revision."""
    commit = _COMMIT_BUILD.fullmatch(build)
    if commit:
        if from_git_checkout and _git_worktree_modified():
            return None
        return commit.group(1)
    if _TAG_BUILD.fullmatch(build):
        return build
    return None


def normalize_source_url(url: str) -> str:
    """``url`` without surrounding blanks, trailing slashes and a ``.git`` suffix."""
    url = url.strip().rstrip("/")
    return url.removesuffix(".git")


def source_link(source_url: str, ref: str | None) -> str:
    """Browse URL of ``ref`` in the repository (``<url>/tree/<ref>``, GitHub/GitLab/Gitea style)."""
    base = normalize_source_url(source_url)
    return f"{base}/tree/{ref}" if ref else base


def about_info(source_url: str) -> dict:
    """Version, build, licence and corresponding-source location of the running backend."""
    override = os.environ.get(BUILD_ENV_VAR, "").strip()
    from_git_checkout = not override and not _packaged_build()
    build = build_id()
    ref = source_ref(build, from_git_checkout)
    return {
        "version": __version__,
        "build": build,
        "license": LICENSE_SPDX,
        "source_url": normalize_source_url(source_url),
        "source_ref": ref,
        "source_link": source_link(source_url, ref),
    }
