"""Third-party licence list of the Python backend (``GET /about/third-party``).

The list is built from the installed distributions, not at release-build
time: the backend has no build step, and the packages actually installed in
the running environment are the ones whose licences apply. It starts from
the runtime dependencies of ``reqmantool`` (the installed package's metadata,
or ``requirements.txt`` beside the package in a git checkout) and follows
their ``Requires-Dist`` entries. Requirements that apply only to an extra are
skipped; other environment markers are not evaluated — a requirement counts
if its distribution is installed. Test-only dependencies are never roots.

Each entry: ``name``, ``version``, ``license`` (SPDX ``License-Expression``,
else a short ``License`` field, else the licence classifiers, else
``"UNKNOWN"``), ``homepage`` (or ``None``) and ``license_text`` (the licence
files shipped in the distribution's metadata, or ``None``). The result is
computed once per process.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import metadata
from pathlib import Path

DISTRIBUTION_NAME = "reqmantool"
REQUIREMENTS_FILE = Path(__file__).resolve().parent.parent / "requirements.txt"
UNKNOWN_LICENSE = "UNKNOWN"
# A free-text License field longer than this is a licence text, not a name.
_MAX_LICENSE_NAME = 120
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.IGNORECASE)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str | None:
    """The distribution name of a requirement string, or ``None`` if it applies to an extra only."""
    marker = requirement.split(";", 1)[1] if ";" in requirement else ""
    if re.search(r"\bextra\s*==", marker):
        return None
    match = _REQUIREMENT_NAME.match(requirement)
    return match.group(1) if match else None


def _root_requirements() -> list[str]:
    try:
        requires = metadata.requires(DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        requires = None
    if requires is None:
        try:
            requires = [
                line.split("#", 1)[0].strip()
                for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines()
            ]
        except OSError:
            requires = []
    names = (_requirement_name(item) for item in requires if item and not item.startswith("-"))
    return [name for name in names if name]


def _license_name(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    free_text = (meta.get("License") or "").strip()
    if free_text and free_text.upper() != UNKNOWN_LICENSE and len(free_text) <= _MAX_LICENSE_NAME and "\n" not in free_text:
        return free_text
    classifiers = [
        value.split("::")[-1].strip()
        for value in (meta.get_all("Classifier") or [])
        if value.startswith("License ::") and value.count("::") >= 2
    ]
    if classifiers:
        return " OR ".join(classifiers)
    return free_text.splitlines()[0].strip() if free_text else UNKNOWN_LICENSE


def _homepage(dist: metadata.Distribution) -> str | None:
    meta = dist.metadata
    if meta.get("Home-page"):
        return meta["Home-page"].strip() or None
    for value in meta.get_all("Project-URL") or []:
        label, _, url = value.partition(",")
        if label.strip().lower() in ("homepage", "home", "source", "repository", "source code"):
            return url.strip() or None
    return None


def _license_text(dist: metadata.Distribution) -> str | None:
    texts: list[str] = []
    for file in dist.files or []:
        parts = file.parts
        # Licence files live in the *.dist-info directory (PEP 639: dist-info/licenses/...).
        if not parts or not parts[0].endswith(".dist-info") or not _LICENSE_FILE.match(file.name):
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if text.strip():
            texts.append(text.strip())
    return "\n\n".join(texts) or None


@lru_cache(maxsize=1)
def python_third_party() -> tuple[dict, ...]:
    """The installed runtime dependencies of the backend with their licences, sorted by name."""
    seen: dict[str, dict] = {}
    pending = _root_requirements()
    while pending:
        name = pending.pop()
        key = _normalize(name)
        if key in seen or key == DISTRIBUTION_NAME:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = {
            "name": dist.metadata.get("Name") or name,
            "version": dist.version,
            "license": _license_name(dist),
            "homepage": _homepage(dist),
            "license_text": _license_text(dist),
        }
        for requirement in dist.requires or []:
            child = _requirement_name(requirement)
            if child:
                pending.append(child)
    return tuple(sorted(seen.values(), key=lambda entry: _normalize(entry["name"])))
