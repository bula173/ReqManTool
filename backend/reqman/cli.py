"""The ``reqman`` command line (console script, ``python -m reqman``).

Commands:

- ``reqman [serve] [--dir PATH] [--host H] [--port N] [--cors-origins A,B]``
  runs the server (the default command). Flags win over environment
  variables, which win over the config file (see ``config.py``).
- ``reqman init [--data-dir PATH] [--host H] [--port N] [--cors-origins A,B]``
  creates (or completes) the per-user config file and the data directory.
  No root/admin rights are needed. An existing file keeps its values; flags
  given replace them.
- ``reqman config`` prints the effective settings and where each came from.
- ``reqman service install|uninstall [--dry-run] [--no-activate]`` registers a
  per-user background service: a systemd ``--user`` unit on Linux, a launchd
  agent on macOS (see ``service_unit.py``).

``--config PATH`` (before or after the command) selects the config file; it
is the same as ``REQMAN_CONFIG_FILE``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from . import config as cfg
from .formats import NewerFormatError

_COMMANDS = ("serve", "init", "config", "service")


def _add_config_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="config file to use (same as REQMAN_CONFIG_FILE)",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reqman", description="ReqManTool requirements management server")
    parser.add_argument("--version", action="version", version=f"ReqManTool {__version__}")
    _add_config_flag(parser)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    serve = commands.add_parser("serve", help="run the server (default command)")
    _add_config_flag(serve)
    serve.add_argument(
        "--dir", "--data-dir", dest="data_dir", default=None,
        help="requirements storage directory (overrides REQMAN_DATA_DIR and the config file)",
    )
    serve.add_argument("--host", default=None, help=f"bind address (default {cfg.DEFAULT_HOST})")
    serve.add_argument("--port", type=int, default=None, help=f"port (default {cfg.DEFAULT_PORT})")
    serve.add_argument("--cors-origins", default=None, help="comma-separated allowed browser origins")
    serve.add_argument("--tls-cert-file", default=None, help="TLS certificate file (PEM) for HTTPS")
    serve.add_argument("--tls-key-file", default=None, help="TLS private key file (PEM) for HTTPS")
    serve.add_argument("--tls-ca-file", default=None, help="TLS CA certificate file (PEM) for mTLS")

    init = commands.add_parser("init", help="create the per-user config file and data directory")
    _add_config_flag(init)
    init.add_argument("--data-dir", "--dir", dest="data_dir", default=None, help="data directory to use")
    init.add_argument("--host", default=None)
    init.add_argument("--port", type=int, default=None)
    init.add_argument("--cors-origins", default=None, help="comma-separated allowed browser origins")
    init.add_argument("--tls-cert-file", default=None, help="TLS certificate file (PEM) for HTTPS")
    init.add_argument("--tls-key-file", default=None, help="TLS private key file (PEM) for HTTPS")
    init.add_argument("--tls-ca-file", default=None, help="TLS CA certificate file (PEM) for mTLS")

    show = commands.add_parser("config", help="print the effective settings and their sources")
    _add_config_flag(show)

    service = commands.add_parser("service", help="manage the per-user background service")
    _add_config_flag(service)
    service.add_argument("action", choices=["install", "uninstall"])
    service.add_argument(
        "--dry-run", action="store_true", help="print what would be written and run; change nothing"
    )
    service.add_argument(
        "--no-activate", action="store_true",
        help="only write/remove the unit file; do not call systemctl/launchctl",
    )
    return parser


def _apply_config_flag(args: argparse.Namespace) -> None:
    path = getattr(args, "config", None)
    if path:
        os.environ[cfg.ENV_CONFIG_FILE] = str(Path(path).expanduser().resolve())


def _serve(args: argparse.Namespace) -> int:
    # CLI flag > env var: flags are applied as env vars before the app is imported.
    if args.data_dir:
        os.environ[cfg.ENV_DATA_DIR] = str(Path(args.data_dir).expanduser().resolve())
    if args.host:
        os.environ[cfg.ENV_HOST] = args.host
    if args.port is not None:
        os.environ[cfg.ENV_PORT] = str(args.port)
    if args.cors_origins is not None:
        os.environ[cfg.ENV_CORS_ORIGINS] = args.cors_origins
    if args.tls_cert_file:
        os.environ[cfg.ENV_TLS_CERT_FILE] = str(Path(args.tls_cert_file).expanduser().resolve())
    if args.tls_key_file:
        os.environ[cfg.ENV_TLS_KEY_FILE] = str(Path(args.tls_key_file).expanduser().resolve())
    if args.tls_ca_file:
        os.environ[cfg.ENV_TLS_CA_FILE] = str(Path(args.tls_ca_file).expanduser().resolve())
    settings = cfg.load_settings()
    if settings.mode == "server":
        # Fail with a clear message before uvicorn starts when the server edition is missing.
        from .extensions import EditionUnavailableError, load_server_edition

        try:
            load_server_edition()
        except EditionUnavailableError as exc:
            print(f"reqman: error: {exc}", file=sys.stderr)
            return 1

    import uvicorn

    logging.basicConfig(level=logging.INFO)

    # TLS configuration for HTTPS
    ssl_kwargs = {}
    if settings.tls_enabled:
        ssl_kwargs = {
            "ssl_certfile": str(settings.tls_cert_file),
            "ssl_keyfile": str(settings.tls_key_file),
        }
        if settings.tls_ca_file:
            ssl_kwargs["ssl_ca_certs"] = str(settings.tls_ca_file)
            ssl_kwargs["ssl_cert_reqs"] = 2  # ssl.CERT_REQUIRED for mTLS
        logging.info("TLS enabled: serving HTTPS on %s:%d", settings.host, settings.port)
    else:
        logging.info("TLS not configured: serving HTTP on %s:%d", settings.host, settings.port)

    uvicorn.run("reqman.app:app", host=settings.host, port=settings.port, **ssl_kwargs)
    return 0


def _init(args: argparse.Namespace) -> int:
    # `init` targets the per-user file unless --config/REQMAN_CONFIG_FILE says otherwise;
    # it never writes the legacy checkout-local `.reqman/config.json`.
    override = os.environ.get(cfg.ENV_CONFIG_FILE)
    path = Path(override).expanduser() if override else cfg.user_config_file()
    existed = path.exists()
    values = dict(cfg.read_config_values(path))

    if args.data_dir:
        values["data_dir"] = str(Path(args.data_dir).expanduser().resolve())
    elif not values.get("data_dir"):
        values["data_dir"] = str(cfg.user_data_dir())
    if args.host:
        values["host"] = args.host
    else:
        values.setdefault("host", cfg.DEFAULT_HOST)
    if args.port is not None:
        values["port"] = cfg._parse_port(args.port, "--port")
    else:
        values.setdefault("port", cfg.DEFAULT_PORT)
    if args.cors_origins is not None:
        values["cors_origins"] = cfg._parse_origins(args.cors_origins, "--cors-origins")
    else:
        values.setdefault("cors_origins", list(cfg.DEFAULT_CORS_ORIGINS))
    if args.tls_cert_file:
        values["tls_cert_file"] = str(Path(args.tls_cert_file).expanduser().resolve())
    if args.tls_key_file:
        values["tls_key_file"] = str(Path(args.tls_key_file).expanduser().resolve())
    if args.tls_ca_file:
        values["tls_ca_file"] = str(Path(args.tls_ca_file).expanduser().resolve())

    cfg.write_config_values(path, values)
    data_dir = cfg._resolve_dir(values["data_dir"], path.parent.resolve())
    data_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'Updated' if existed else 'Created'} config file: {path}")
    print(f"Data directory: {data_dir}")
    if cfg.config_file().resolve() != path.resolve():
        print(
            f"Note: the server currently reads {cfg.config_file()} instead "
            f"(checkout-local or REQMAN_CONFIG_FILE); pass --config {path} to use this file."
        )
    print("Start the server with: reqman serve")
    return 0


def _show_config(_args: argparse.Namespace) -> int:
    settings = cfg.load_settings()
    info = settings.to_dict()
    info["user_config_file"] = str(cfg.user_config_file())
    info["frontend_dir"] = str(cfg.frontend_dir()) if cfg.frontend_dir() else None
    print(json.dumps(info, indent=2))
    return 0


def _service(args: argparse.Namespace) -> int:
    from . import service_unit

    return service_unit.run(args.action, dry_run=args.dry_run, activate=not args.no_activate)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # `reqman [--dir PATH ...]` without a command means `reqman serve ...`.
    if not any(arg in _COMMANDS or arg in ("-h", "--help", "--version") for arg in argv):
        argv = ["serve", *argv]
    args = parser.parse_args(argv)
    _apply_config_flag(args)
    handlers = {"serve": _serve, "init": _init, "config": _show_config, "service": _service}
    try:
        return handlers[args.command](args)
    except (NewerFormatError, ValueError, OSError) as exc:
        print(f"reqman: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
