"""Tool version and build id, shared by ``GET /version`` and the
``version.get`` WebSocket method.

- ``version`` is ``reqman.__version__`` — the single version source, also
  read by the frontend build (``frontend/vite.config.ts``).
- ``build`` identifies the running backend code:
  1. ``REQMAN_BUILD`` environment variable, if set and non-blank (for
     packaged releases or CI, where there is no git checkout);
  2. otherwise ``"<short commit> (<commit date YYYY-MM-DD>)"`` from the git
     checkout this package lives in, computed once per process. The Python
     backend has no build step, so the *commit* date is used — it is stable
     across server restarts and names the code actually running;
  3. otherwise ``"dev"`` (git missing, not a checkout, or git failed).
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


def build_id() -> str:
    override = os.environ.get(BUILD_ENV_VAR, "").strip()
    return override or _git_build()


def version_info() -> dict[str, str]:
    return {"version": __version__, "build": build_id()}
