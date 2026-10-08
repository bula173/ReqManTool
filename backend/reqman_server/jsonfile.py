"""Small JSON files of the server edition (users, sessions): read, and write atomically and privately."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

# The server edition's own files carry their own format version.
SERVER_FILE_FORMAT = 1


def read_json_file(path: Path, what: str) -> dict[str, Any] | None:
    """The file's JSON object, ``None`` if it does not exist; ``ValueError`` if it is malformed or newer."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{what} '{path}' cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{what} '{path}' is not a JSON object")
    version = data.get("format_version", 1)
    if not isinstance(version, int) or version > SERVER_FILE_FORMAT:
        raise ValueError(f"{what} '{path}' was written by a newer ReqManTool (format_version {version!r})")
    return data


def write_json_file(path: Path, data: dict[str, Any]) -> None:
    """Write two-space-indented JSON with a trailing newline, atomically, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"format_version": SERVER_FILE_FORMAT, **data}, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
