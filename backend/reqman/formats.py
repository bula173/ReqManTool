"""On-disk format versions and the migrations between them.

Two kinds of file carry an integer ``format_version`` as their first key:

- ``project.json`` (``PROJECT_FORMAT``). A project's module, requirement and
  link-pair files carry no version of their own: they are governed by their
  project's version.
- the app config file, ``.reqman/config.json`` (``CONFIG_FORMAT``).

Project templates keep their own, independent ``format_version`` (see
``templates.py``); it is not handled here.

Rules:

- A file without the key is version 1, the format written before versions
  existed, and keeps loading unchanged.
- Loading never rewrites a file. ``upgrade`` brings an older file's data to
  the current version *in memory*, one explicit step at a time
  (``FileFormat.migrations[n]`` turns version ``n`` into ``n + 1``); the
  current version is written by the next real save (``with_format_version``).
- A file whose version is newer than the running tool supports raises
  ``NewerFormatError`` on read, and every write path checks the version on
  disk first, so such a file is never misread or overwritten.
- Bump a ``current`` version for a change an older reader would
  misinterpret, and add the migration step in the same change. Also bump it
  for any new persisted key that must survive a round-trip through an older
  tool: ``Project.from_dict``/``to_dict`` drop unknown keys, so a versioned
  tool that loads and saves a project would silently delete a key added
  later without a bump. ``Project.module_order`` was added without a bump
  only because the tools it could be lost through predate versioning and
  cannot be protected anyway; from this release on, new keys bump the
  version (module folders therefore must).
- A project migration that changes module, requirement or link-pair files
  must upgrade those files as well before the project is saved at the new
  version, because they are read with their project's version.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable

FORMAT_VERSION_KEY = "format_version"

# The version of files written before ``format_version`` existed.
UNVERSIONED = 1

Migration = Callable[[dict], dict]


@dataclass(frozen=True)
class FileFormat:
    """A versioned file kind: its name in messages, current version and upgrade steps."""

    name: str
    current: int
    # ``migrations[n]`` returns version ``n`` data upgraded to ``n + 1``.
    migrations: dict[int, Migration] = field(default_factory=dict)


class NewerFormatError(ValueError):
    """A file was written by a newer ReqManTool than this one (REST 409).

    The file is neither interpreted nor overwritten.
    """


PROJECT_FORMAT = FileFormat(name="project", current=1)
CONFIG_FORMAT = FileFormat(name="config", current=1)


def stored_format_version(data: dict, what: str) -> int:
    """The version recorded in ``data`` (``UNVERSIONED`` if missing).

    Raises ``ValueError`` for a value that is not a positive integer.
    ``what`` names the file in the message.
    """
    if FORMAT_VERSION_KEY not in data:
        return UNVERSIONED
    version = data[FORMAT_VERSION_KEY]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"{what} has an invalid format_version {version!r}: expected a positive integer")
    return version


def check_format_version(data: dict, fmt: FileFormat, what: str) -> int:
    """Return ``data``'s version, or raise ``NewerFormatError`` if this tool cannot read it."""
    version = stored_format_version(data, what)
    if version > fmt.current:
        raise NewerFormatError(
            f"{what} was written by a newer ReqManTool (format_version {version}; this "
            f"ReqManTool supports {fmt.name} format_version {fmt.current} or older). Update "
            "ReqManTool to use it; the file was not changed."
        )
    return version


def upgrade(data: dict, fmt: FileFormat, what: str) -> dict:
    """``data`` brought to ``fmt.current`` in memory, without the ``format_version`` key.

    The input is not modified. Raises ``NewerFormatError`` for a newer file
    and ``ValueError`` for an invalid version or a missing migration step.
    """
    version = check_format_version(data, fmt, what)
    result = {k: v for k, v in data.items() if k != FORMAT_VERSION_KEY}
    if version < fmt.current:
        result = copy.deepcopy(result)
    while version < fmt.current:
        step = fmt.migrations.get(version)
        if step is None:
            raise ValueError(
                f"{what} has format_version {version}, but this ReqManTool has no migration "
                f"from {fmt.name} format_version {version} to {version + 1}"
            )
        result = step(result)
        result.pop(FORMAT_VERSION_KEY, None)
        version += 1
    return result


def with_format_version(data: dict, fmt: FileFormat) -> dict:
    """``data`` with ``format_version`` set to the current version, as its first key."""
    return {FORMAT_VERSION_KEY: fmt.current, **{k: v for k, v in data.items() if k != FORMAT_VERSION_KEY}}

