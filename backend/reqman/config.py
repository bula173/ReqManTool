"""Persistence for *which data directory to use* — distinct from
``store.py``, which only reads/writes files inside whatever directory it's
given. The chosen path survives a restart via ``.reqman/config.json`` next
to the repo (or ``REQMAN_CONFIG_FILE`` to point elsewhere, used by tests to
avoid polluting the real config file). An explicit ``REQMAN_DATA_DIR`` env
var or ``--dir`` CLI flag always overrides the saved value.

The file starts with its ``format_version`` (``CONFIG_FORMAT``, see
``formats.py``). A file without it is version 1. A config file written by a
newer ReqManTool raises ``NewerFormatError`` when read and is never
overwritten; an unreadable or malformed file is ignored on read and replaced
on the next save, as before versions existed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .formats import CONFIG_FORMAT, check_format_version, upgrade, with_format_version

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "example_database"


def _config_file() -> Path:
    override = os.environ.get("REQMAN_CONFIG_FILE")
    if override:
        return Path(override)
    return PROJECT_ROOT / ".reqman" / "config.json"


def connections_file() -> Path:
    """The GitLab/GitHub provider connections file: ``connections.json`` beside the config file.

    Holds connection metadata only (provider, URL, name, user); tokens are
    kept in the OS keychain (see ``providers.py``).
    """
    return _config_file().parent / "connections.json"


def _read_config(config_file: Path) -> dict | None:
    """The config file's JSON object, or ``None`` if it is missing, unreadable or not an object."""
    if not config_file.exists():
        return None
    try:
        data = json.loads(config_file.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def load_saved_data_dir() -> str | None:
    """The saved data directory, or ``None``.

    Raises ``NewerFormatError`` if the config file was written by a newer
    ReqManTool, so a newer setting is never silently ignored.
    """
    config_file = _config_file()
    data = _read_config(config_file)
    if data is None:
        return None
    data = upgrade(data, CONFIG_FORMAT, f"Config file '{config_file}'")
    data_dir = data.get("data_dir")
    return data_dir if isinstance(data_dir, str) and data_dir else None


def check_config_writable() -> None:
    """Raise ``NewerFormatError`` (or ``ValueError``) if the config file must not be overwritten."""
    config_file = _config_file()
    data = _read_config(config_file)
    if data is not None:
        check_format_version(data, CONFIG_FORMAT, f"Config file '{config_file}'")


def save_data_dir(data_dir: str) -> None:
    """Save the data directory, at the current config ``format_version``.

    Raises ``NewerFormatError`` instead of overwriting a config file written
    by a newer ReqManTool.
    """
    check_config_writable()
    config_file = _config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data = with_format_version({"data_dir": data_dir}, CONFIG_FORMAT)
    config_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
