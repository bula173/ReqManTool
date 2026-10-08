"""``reqman service install|uninstall``: a per-user background service.

- Linux: a systemd ``--user`` unit, ``$XDG_CONFIG_HOME/systemd/user/reqman.service``
  (default ``~/.config/systemd/user``), activated with
  ``systemctl --user enable --now``.
- macOS: a launchd agent, ``~/Library/LaunchAgents/<LAUNCHD_LABEL>.plist``,
  activated with ``launchctl bootstrap gui/<uid>``; logs go to
  ``~/Library/Logs/ReqManTool/``.

Both run ``<this python> -m reqman serve --config <config file>`` so the
service uses the same interpreter (e.g. the pipx venv) and the same config
file as the command that installed it; from a git checkout the service
starts in ``backend/`` so the package is importable. No root/admin rights are needed.
``dry_run`` prints the file and commands without changing anything;
``activate=False`` writes/removes the file but does not call
systemctl/launchctl (the commands are printed instead).
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config as cfg

SYSTEMD_UNIT_NAME = "reqman.service"
LAUNCHD_LABEL = "io.github.reqmantool.reqman"


@dataclass(frozen=True)
class ServicePlan:
    """What installing (or uninstalling) the service writes and runs."""

    path: Path
    content: str
    install_commands: list[list[str]]
    uninstall_commands: list[list[str]]


def _server_command(config_file: Path) -> list[str]:
    return [sys.executable, "-m", "reqman", "serve", "--config", str(config_file)]


def _working_dir() -> Path | None:
    """``backend/`` when running from a git checkout (``reqman`` is importable from there, not installed)."""
    return cfg.PACKAGE_DIR.parent if cfg.is_checkout() else None


def _systemd_quote(arg: str) -> str:
    if arg and all(ch not in arg for ch in ' \t"\'\\$%;'):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$").replace("%", "%%")
    return f'"{escaped}"'


def systemd_unit_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".config"
    return base / "systemd" / "user"


def systemd_plan(config_file: Path) -> ServicePlan:
    exec_start = " ".join(_systemd_quote(arg) for arg in _server_command(config_file))
    path_env = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    working_dir = _working_dir()
    lines = [
        "[Unit]",
        "Description=ReqManTool requirements management server",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={exec_start}",
        *([f"WorkingDirectory={_systemd_quote(str(working_dir))}"] if working_dir else []),
        f"Environment={_systemd_quote('PATH=' + path_env)}",
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=default.target",
    ]
    content = "\n".join(lines) + "\n"
    return ServicePlan(
        path=systemd_unit_dir() / SYSTEMD_UNIT_NAME,
        content=content,
        install_commands=[
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT_NAME],
        ],
        uninstall_commands=[
            ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME],
        ],
    )


def launchd_plan(config_file: Path) -> ServicePlan:
    home = Path.home()
    log_dir = home / "Library" / "Logs" / cfg.APP_DIR_NAME
    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": _server_command(config_file),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")},
        "StandardOutPath": str(log_dir / "reqman.log"),
        "StandardErrorPath": str(log_dir / "reqman.log"),
    }
    if _working_dir():
        plist["WorkingDirectory"] = str(_working_dir())
    domain = f"gui/{os.getuid()}" if hasattr(os, "getuid") else "gui/0"
    path = home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    return ServicePlan(
        path=path,
        content=plistlib.dumps(plist, sort_keys=False).decode("utf-8"),
        install_commands=[["launchctl", "bootstrap", domain, str(path)]],
        uninstall_commands=[["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"]],
    )


def plan_for_platform(platform: str | None = None, config_file: Path | None = None) -> ServicePlan:
    platform = platform or cfg._platform()
    config_file = (config_file or cfg.config_file()).expanduser().resolve()
    if platform == "linux":
        return systemd_plan(config_file)
    if platform == "macos":
        return launchd_plan(config_file)
    raise ValueError("reqman service is supported on Linux (systemd --user) and macOS (launchd) only")


def _show(commands: list[list[str]]) -> str:
    return "\n".join("  " + shlex.join(command) for command in commands)


def _run(commands: list[list[str]], check: bool) -> None:
    for command in commands:
        result = subprocess.run(command, check=False)
        if check and result.returncode != 0:
            raise OSError(f"'{shlex.join(command)}' failed with exit code {result.returncode}")


def run(action: str, *, dry_run: bool = False, activate: bool = True, platform: str | None = None) -> int:
    plan = plan_for_platform(platform)
    if action == "install":
        if dry_run:
            print(f"Would write {plan.path}:\n")
            print(plan.content)
            print("Would run:\n" + _show(plan.install_commands))
            return 0
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        if plan.path.name.endswith(".plist"):
            # launchd does not create the log directory.
            (Path.home() / "Library" / "Logs" / cfg.APP_DIR_NAME).mkdir(parents=True, exist_ok=True)
        plan.path.write_text(plan.content, encoding="utf-8")
        print(f"Wrote {plan.path}")
        if activate:
            # Replace a loaded agent/unit so a reinstall picks up the new file.
            if plan.path.name.endswith(".plist"):
                _run(plan.uninstall_commands, check=False)
            _run(plan.install_commands, check=True)
            print("Service started.")
        else:
            print("To start it, run:\n" + _show(plan.install_commands))
        return 0

    if action == "uninstall":
        if dry_run:
            print("Would run:\n" + _show(plan.uninstall_commands))
            print(f"Would remove {plan.path}")
            return 0
        if activate:
            _run(plan.uninstall_commands, check=False)
        else:
            print("If it is running, stop it with:\n" + _show(plan.uninstall_commands))
        if plan.path.exists():
            plan.path.unlink()
            print(f"Removed {plan.path}")
        else:
            print(f"Nothing to remove: {plan.path} does not exist")
        if activate and plan.path.name.endswith(".service"):
            _run([["systemctl", "--user", "daemon-reload"]], check=False)
        return 0

    raise ValueError(f"Unknown service action: {action}")
