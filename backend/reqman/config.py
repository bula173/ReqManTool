"""Persistence for *which data directory to use* — distinct from
``store.py``, which only reads/writes files inside whatever directory it's
given. The chosen path survives a restart via ``.reqman/config.json`` next
to the repo (or ``REQMAN_CONFIG_FILE`` to point elsewhere, used by tests to
avoid polluting the real config file). An explicit ``REQMAN_DATA_DIR`` env
var or ``--dir`` CLI flag always overrides the saved value.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "example_database"


def _config_file() -> Path:
    override = os.environ.get("REQMAN_CONFIG_FILE")
    if override:
        return Path(override)
    return PROJECT_ROOT / ".reqman" / "config.json"


def load_saved_data_dir() -> str | None:
    config_file = _config_file()
    if not config_file.exists():
        return None
    try:
        data = json.loads(config_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    data_dir = data.get("data_dir")
    return data_dir if isinstance(data_dir, str) and data_dir else None


def save_data_dir(data_dir: str) -> None:
    config_file = _config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps({"data_dir": data_dir}, indent=2) + "\n")
