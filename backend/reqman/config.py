"""Application settings: *which data directory to use* and how to serve —
distinct from ``store.py``, which only reads/writes files inside whatever
directory it's given.

Settings and their precedence
-----------------------------

Keys: ``data_dir`` (storage root), ``host``, ``port``, ``cors_origins``,
``source_url``, ``mode``.
Each is resolved as CLI flag > environment variable > config file > default:

====================  =======================  ===========================
key                   environment variable     default
====================  =======================  ===========================
``data_dir``          ``REQMAN_DATA_DIR``      ``default_data_dir()``
``host``              ``REQMAN_HOST``          ``localhost``
``port``              ``REQMAN_PORT``          ``8899``
``cors_origins``      ``REQMAN_CORS_ORIGINS``  ``http://localhost:5173``
``source_url``        ``REQMAN_SOURCE_URL``    ``DEFAULT_SOURCE_URL``
``mode``              ``REQMAN_MODE``          ``local``
====================  =======================  ===========================

CLI flags (``reqman serve --dir/--host/--port/--cors-origins``, see
``cli.py``) are applied by setting these environment variables before the
app is imported, so the app itself only reads environment > file > default.
``REQMAN_CORS_ORIGINS`` is comma-separated; in the file ``cors_origins`` is a
list of strings.

``source_url`` is the http(s) URL of the repository holding the source of
the running code, offered in Help ▸ About and the footer (AGPL-3.0 §13; see
``version.about_info``). A deployment of a *modified* ReqManTool must set it
to where that modified source is published.

``mode`` is the edition: ``local`` (the single-user community edition: no
login, identity from git config) or ``server`` (the multi-user server
edition, loaded from the separate ``reqman_server`` package; see
``extensions.py``). The server edition reads its own ``server`` section of
the config file; the core keeps that section untouched.

Which config file (``config_file()``)
-------------------------------------

1. ``REQMAN_CONFIG_FILE`` (also set by ``reqman --config PATH``; tests use it
   to avoid touching a real config file);
2. running from a git checkout (``backend/reqman`` next to ``frontend/``)
   *and* the checkout-local ``.reqman/config.json`` exists: that file. This is
   the location used before ReqManTool had a per-user config file; it keeps
   being honoured so an existing checkout keeps its data folder;
3. the per-user config file (``user_config_file()``):
   ``$XDG_CONFIG_HOME/reqman/config.json`` (default ``~/.config/...``) on
   Linux and other Unixes, ``~/Library/Application Support/ReqManTool/
   config.json`` on macOS, ``%APPDATA%\\ReqManTool\\config.json`` on Windows.

The default data directory is ``<checkout>/example_database`` in a git
checkout (as before) and the per-user data directory (``user_data_dir()``)
in an installed package.

The provider connections file ``connections.json`` lives beside the config
file (``connections_file()``).

File format
-----------

The file starts with its ``format_version`` (``CONFIG_FORMAT``, see
``formats.py``). A file without it is version 1. A config file written by a
newer ReqManTool raises ``NewerFormatError`` when read and is never
overwritten; an unreadable or malformed file is ignored on read and replaced
on the next save, as before versions existed. Writes keep every key (known
keys in ``CONFIG_KEYS`` order, unknown keys after them), two-space indent,
trailing newline.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .formats import CONFIG_FORMAT, check_format_version, upgrade, with_format_version
from .store import _read_json, _write_json

APP_DIR_NAME = "ReqManTool"
# Lower-case directory name used on Linux/XDG platforms.
XDG_APP_DIR_NAME = "reqman"

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent
# The data directory used in a git checkout when nothing else is configured.
CHECKOUT_DATA_DIR = PROJECT_ROOT / "example_database"
# Kept for compatibility with code importing it; see ``default_data_dir()``.
DEFAULT_DATA_DIR = CHECKOUT_DATA_DIR

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8899
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:5173",)
# Upstream repository; override with REQMAN_SOURCE_URL / "source_url" in a modified deployment.
DEFAULT_SOURCE_URL = "https://github.com/bula173/ReqManTool"
# The edition: "local" (community, single user) or "server" (multi-user, reqman_server).
DEFAULT_MODE = "local"
MODES = ("local", "server")

# Known keys, in the order they are written.
CONFIG_KEYS = ("data_dir", "host", "port", "cors_origins", "source_url", "mode", "tls_cert_file", "tls_key_file", "tls_ca_file")

ENV_CONFIG_FILE = "REQMAN_CONFIG_FILE"
ENV_DATA_DIR = "REQMAN_DATA_DIR"
ENV_HOST = "REQMAN_HOST"
ENV_PORT = "REQMAN_PORT"
ENV_CORS_ORIGINS = "REQMAN_CORS_ORIGINS"
ENV_SOURCE_URL = "REQMAN_SOURCE_URL"
ENV_MODE = "REQMAN_MODE"
ENV_FRONTEND_DIR = "REQMAN_FRONTEND_DIR"
ENV_TLS_CERT_FILE = "REQMAN_TLS_CERT_FILE"
ENV_TLS_KEY_FILE = "REQMAN_TLS_KEY_FILE"
ENV_TLS_CA_FILE = "REQMAN_TLS_CA_FILE"

# Built frontend bundled into the package (copied from frontend/dist at release build time).
PACKAGED_FRONTEND_DIR = PACKAGE_DIR / "static"


# --- locations ---------------------------------------------------------------------


def is_checkout() -> bool:
    """True when this package runs from a git checkout (``<root>/backend/reqman`` beside ``<root>/frontend``)."""
    return PACKAGE_DIR.parent.name == "backend" and (PROJECT_ROOT / "frontend" / "package.json").is_file()


def legacy_config_file() -> Path:
    """The checkout-local config file used before the per-user config file existed."""
    return PROJECT_ROOT / ".reqman" / "config.json"


def _platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def user_config_dir(platform: str | None = None) -> Path:
    """The per-user configuration directory for ReqManTool on this platform."""
    platform = platform or _platform()
    home = Path.home()
    if platform == "windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / APP_DIR_NAME
    if platform == "macos":
        return home / "Library" / "Application Support" / APP_DIR_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".config"
    return base / XDG_APP_DIR_NAME


def user_data_dir(platform: str | None = None) -> Path:
    """The per-user default data directory (the storage root) on this platform."""
    platform = platform or _platform()
    home = Path.home()
    if platform == "windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / APP_DIR_NAME / "data"
    if platform == "macos":
        return home / "Library" / "Application Support" / APP_DIR_NAME / "data"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home / ".local" / "share"
    return base / XDG_APP_DIR_NAME


def user_config_file(platform: str | None = None) -> Path:
    return user_config_dir(platform) / "config.json"


def config_file() -> Path:
    """The config file in use (see the module docstring for the lookup order)."""
    override = os.environ.get(ENV_CONFIG_FILE)
    if override:
        return Path(override).expanduser()
    if is_checkout() and legacy_config_file().is_file():
        return legacy_config_file()
    return user_config_file()


# Former private name, kept for callers that still use it.
_config_file = config_file


def default_data_dir() -> Path:
    """The data directory used when neither env nor config file names one."""
    return CHECKOUT_DATA_DIR if is_checkout() else user_data_dir()


def connections_file() -> Path:
    """The GitLab/GitHub provider connections file: ``connections.json`` beside the config file.

    Holds connection metadata only (provider, URL, name, user); tokens are
    kept in the OS keychain (see ``providers.py``).
    """
    return config_file().parent / "connections.json"


def frontend_dir() -> Path | None:
    """The built frontend to serve from the backend origin, or ``None`` (API only).

    ``REQMAN_FRONTEND_DIR`` names a directory holding ``index.html`` (an empty
    value turns serving off); otherwise the frontend bundled in the package
    (``reqman/static``, present in release wheels) is used if it exists. A
    git checkout has no bundled frontend unless one was copied there, so the
    dev setup (Vite on :5173, API on :8899) is unchanged.
    """
    override = os.environ.get(ENV_FRONTEND_DIR)
    if override is not None:
        if not override.strip():
            return None
        candidate = Path(override).expanduser().resolve()
        return candidate if (candidate / "index.html").is_file() else None
    return PACKAGED_FRONTEND_DIR if (PACKAGED_FRONTEND_DIR / "index.html").is_file() else None


# --- reading and writing -------------------------------------------------------------


def _read_config(path: Path) -> dict | None:
    """The config file's JSON object, or ``None`` if it is missing, unreadable or not an object."""
    if not path.exists():
        return None
    try:
        data = _read_json(path)
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def read_config_values(path: Path | None = None) -> dict:
    """The config file's values (without ``format_version``), ``{}`` if missing or malformed.

    Raises ``NewerFormatError`` if the file was written by a newer ReqManTool.
    """
    path = path or config_file()
    data = _read_config(path)
    if data is None:
        return {}
    return upgrade(data, CONFIG_FORMAT, f"Config file '{path}'")


def _ordered(values: dict) -> dict:
    ordered = {key: values[key] for key in CONFIG_KEYS if key in values}
    ordered.update({key: value for key, value in values.items() if key not in ordered})
    return ordered


def write_config_values(path: Path, values: dict) -> None:
    """Write ``values`` to ``path`` at the current ``format_version``, keys in a stable order.

    Raises ``NewerFormatError`` instead of overwriting a file written by a newer ReqManTool.
    """
    existing = _read_config(path)
    if existing is not None:
        check_format_version(existing, CONFIG_FORMAT, f"Config file '{path}'")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, with_format_version(_ordered(values), CONFIG_FORMAT))


def load_saved_data_dir() -> str | None:
    """The saved data directory, or ``None``.

    Raises ``NewerFormatError`` if the config file was written by a newer
    ReqManTool, so a newer setting is never silently ignored.
    """
    data_dir = read_config_values().get("data_dir")
    return data_dir if isinstance(data_dir, str) and data_dir else None


def check_config_writable() -> None:
    """Raise ``NewerFormatError`` (or ``ValueError``) if the config file must not be overwritten."""
    path = config_file()
    data = _read_config(path)
    if data is not None:
        check_format_version(data, CONFIG_FORMAT, f"Config file '{path}'")


def save_data_dir(data_dir: str) -> None:
    """Save the data directory into the config file, keeping its other keys.

    Raises ``NewerFormatError`` instead of overwriting a config file written
    by a newer ReqManTool.
    """
    check_config_writable()
    path = config_file()
    values = dict(read_config_values(path))
    values["data_dir"] = data_dir
    write_config_values(path, values)


# --- effective settings --------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """The effective settings and where each came from (``env``, ``config`` or ``default``)."""

    config_file: Path
    data_dir: Path
    host: str
    port: int
    cors_origins: list[str]
    sources: dict[str, str] = field(default_factory=dict)
    source_url: str = DEFAULT_SOURCE_URL
    mode: str = DEFAULT_MODE
    # TLS settings (None = disabled). When set, the backend serves HTTPS.
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None
    tls_ca_file: Path | None = None  # optional, for mTLS

    def to_dict(self) -> dict:
        return {
            "config_file": str(self.config_file),
            "data_dir": str(self.data_dir),
            "host": self.host,
            "port": self.port,
            "cors_origins": list(self.cors_origins),
            "source_url": self.source_url,
            "mode": self.mode,
            "tls_cert_file": str(self.tls_cert_file) if self.tls_cert_file else None,
            "tls_key_file": str(self.tls_key_file) if self.tls_key_file else None,
            "tls_ca_file": str(self.tls_ca_file) if self.tls_ca_file else None,
            "sources": dict(self.sources),
        }

    @property
    def tls_enabled(self) -> bool:
        """True when TLS is configured (both cert and key files exist)."""
        return self.tls_cert_file is not None and self.tls_key_file is not None


def _resolve_dir(value: str, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if base is not None and not path.is_absolute():
        path = base / path
    return path.resolve()


def _parse_port(value: object, what: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{what}: port must be an integer, got {value!r}")
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{what}: port must be an integer, got {value!r}") from None
    if not 0 < port < 65536:
        raise ValueError(f"{what}: port must be between 1 and 65535, got {port}")
    return port


def _parse_origins(value: object, what: str) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        raise ValueError(f"{what}: cors_origins must be a list of strings, got {value!r}")
    return [item.strip() for item in items if item.strip()]


def _parse_path(value: object, what: str) -> Path | None:
    """Parse an optional path value. Empty string or None returns None."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{what}: must be a string, got {value!r}")
    return Path(value).expanduser().resolve()


def _parse_source_url(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip().lower().startswith(("http://", "https://")):
        raise ValueError(f"{what}: source_url must be an http(s) URL, got {value!r}")
    return value.strip()


def _parse_mode(value: object, what: str) -> str:
    mode = value.strip().lower() if isinstance(value, str) else value
    if mode not in MODES:
        raise ValueError(f"{what}: mode must be one of {', '.join(MODES)}, got {value!r}")
    return mode  # type: ignore[return-value]


def load_settings() -> Settings:
    """Resolve the effective settings: environment > config file > default.

    Raises ``NewerFormatError`` for a config file written by a newer
    ReqManTool and ``ValueError`` for an invalid value.
    """
    path = config_file()
    values = read_config_values(path)
    what_file = f"Config file '{path}'"
    sources: dict[str, str] = {}

    env_data_dir = os.environ.get(ENV_DATA_DIR)
    file_data_dir = values.get("data_dir")
    if env_data_dir:
        data_dir, sources["data_dir"] = _resolve_dir(env_data_dir), "env"
    elif isinstance(file_data_dir, str) and file_data_dir:
        # A relative path in a hand-edited file is relative to the file.
        data_dir, sources["data_dir"] = _resolve_dir(file_data_dir, path.parent.resolve()), "config"
    else:
        data_dir, sources["data_dir"] = default_data_dir().resolve(), "default"

    env_host = os.environ.get(ENV_HOST)
    if env_host:
        host, sources["host"] = env_host, "env"
    elif "host" in values:
        if not isinstance(values["host"], str) or not values["host"]:
            raise ValueError(f"{what_file}: host must be a non-empty string, got {values['host']!r}")
        host, sources["host"] = values["host"], "config"
    else:
        host, sources["host"] = DEFAULT_HOST, "default"

    env_port = os.environ.get(ENV_PORT)
    if env_port:
        port, sources["port"] = _parse_port(env_port, ENV_PORT), "env"
    elif "port" in values:
        port, sources["port"] = _parse_port(values["port"], what_file), "config"
    else:
        port, sources["port"] = DEFAULT_PORT, "default"

    env_origins = os.environ.get(ENV_CORS_ORIGINS)
    if env_origins is not None and env_origins.strip():
        origins, sources["cors_origins"] = _parse_origins(env_origins, ENV_CORS_ORIGINS), "env"
    elif "cors_origins" in values:
        origins, sources["cors_origins"] = _parse_origins(values["cors_origins"], what_file), "config"
    else:
        origins, sources["cors_origins"] = list(DEFAULT_CORS_ORIGINS), "default"

    env_source_url = os.environ.get(ENV_SOURCE_URL)
    if env_source_url is not None and env_source_url.strip():
        source_url, sources["source_url"] = _parse_source_url(env_source_url, ENV_SOURCE_URL), "env"
    elif "source_url" in values:
        source_url, sources["source_url"] = _parse_source_url(values["source_url"], what_file), "config"
    else:
        source_url, sources["source_url"] = DEFAULT_SOURCE_URL, "default"

    env_mode = os.environ.get(ENV_MODE)
    if env_mode is not None and env_mode.strip():
        mode, sources["mode"] = _parse_mode(env_mode, ENV_MODE), "env"
    elif "mode" in values:
        mode, sources["mode"] = _parse_mode(values["mode"], what_file), "config"
    else:
        mode, sources["mode"] = DEFAULT_MODE, "default"

    # TLS settings
    env_tls_cert = os.environ.get(ENV_TLS_CERT_FILE)
    env_tls_key = os.environ.get(ENV_TLS_KEY_FILE)
    env_tls_ca = os.environ.get(ENV_TLS_CA_FILE)

    if env_tls_cert is not None and env_tls_cert.strip():
        tls_cert_file, sources["tls_cert_file"] = _parse_path(env_tls_cert, ENV_TLS_CERT_FILE), "env"
    elif "tls_cert_file" in values:
        tls_cert_file, sources["tls_cert_file"] = _parse_path(values["tls_cert_file"], what_file), "config"
    else:
        tls_cert_file, sources["tls_cert_file"] = None, "default"

    if env_tls_key is not None and env_tls_key.strip():
        tls_key_file, sources["tls_key_file"] = _parse_path(env_tls_key, ENV_TLS_KEY_FILE), "env"
    elif "tls_key_file" in values:
        tls_key_file, sources["tls_key_file"] = _parse_path(values["tls_key_file"], what_file), "config"
    else:
        tls_key_file, sources["tls_key_file"] = None, "default"

    if env_tls_ca is not None and env_tls_ca.strip():
        tls_ca_file, sources["tls_ca_file"] = _parse_path(env_tls_ca, ENV_TLS_CA_FILE), "env"
    elif "tls_ca_file" in values:
        tls_ca_file, sources["tls_ca_file"] = _parse_path(values["tls_ca_file"], what_file), "config"
    else:
        tls_ca_file, sources["tls_ca_file"] = None, "default"

    return Settings(
        config_file=path,
        data_dir=data_dir,
        host=host,
        port=port,
        cors_origins=origins,
        sources=sources,
        source_url=source_url,
        mode=mode,
        tls_cert_file=tls_cert_file,
        tls_key_file=tls_key_file,
        tls_ca_file=tls_ca_file,
    )
