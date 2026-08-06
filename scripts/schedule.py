#!/usr/bin/env python3
"""Install, remove, inspect, or trigger the one-minute due-check scheduler."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = REPOSITORY / "skills" / "daily-paper-digest" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))
from common import archive_root, default_config_path, load_config, resolve_path  # noqa: E402


LABEL = "com.zlynpu.daily-paper-digest"
LINUX_UNIT = "daily-paper-digest"
WINDOWS_TASK = "DailyPaperDigest"


def default_runner(config: dict, config_path: Path) -> Path:
    harness = config["agent"]["harness"]
    roots = {
        "codex": Path.home() / ".agents" / "skills" / "daily-paper-digest",
        "claude-code": Path.home() / ".claude" / "skills" / "daily-paper-digest",
        "qoder": Path.home() / ".qoder" / "skills" / "daily-paper-digest",
    }
    if harness not in roots:
        raise RuntimeError("--runner is required for custom or none harnesses")
    return roots[harness] / "scripts" / "run_daily.py"


def log_paths(config: dict, config_path: Path) -> tuple[Path, Path]:
    configured = str(config["archive"].get("log_directory", "")).strip()
    directory = (
        resolve_path(configured, config_path)
        if configured
        else archive_root(config, config_path) / ".daily-paper-digest" / "logs"
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "scheduler.out.log", directory / "scheduler.err.log"


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def macos(action: str, config: dict, config_path: Path, runner: Path) -> None:
    target = launch_agent_path()
    domain = f"gui/{os.getuid()}"
    if action == "install":
        archive_root(config, config_path).mkdir(parents=True, exist_ok=True)
        stdout, stderr = log_paths(config, config_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": LABEL,
            "ProgramArguments": [sys.executable, str(runner), "--config", str(config_path), "--if-due"],
            "WorkingDirectory": str(archive_root(config, config_path)),
            "StartInterval": 60,
            "RunAtLoad": True,
            "ProcessType": "Background",
            "StandardOutPath": str(stdout),
            "StandardErrorPath": str(stderr),
        }
        with target.open("wb") as stream:
            plistlib.dump(payload, stream)
        subprocess.run(["launchctl", "bootout", domain, str(target)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        print(f"installed={target}")
    elif action == "uninstall":
        subprocess.run(["launchctl", "bootout", domain, str(target)], check=False)
        if target.exists():
            target.unlink()
        print(f"removed={target}")
    elif action == "run-now":
        subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=True)
    else:
        result = subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], check=False)
        raise SystemExit(result.returncode)


def systemd_escape(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def linux(action: str, config: dict, config_path: Path, runner: Path) -> None:
    directory = Path.home() / ".config" / "systemd" / "user"
    service = directory / f"{LINUX_UNIT}.service"
    timer = directory / f"{LINUX_UNIT}.timer"
    if action == "install":
        archive_root(config, config_path).mkdir(parents=True, exist_ok=True)
        directory.mkdir(parents=True, exist_ok=True)
        service.write_text(
            "[Unit]\nDescription=Generate the configured daily paper digest when due\n\n"
            "[Service]\nType=oneshot\n"
            f"WorkingDirectory={systemd_escape(str(archive_root(config, config_path)))}\n"
            f"ExecStart={systemd_escape(sys.executable)} {systemd_escape(str(runner))} --config {systemd_escape(str(config_path))} --if-due\n",
            encoding="utf-8",
        )
        timer.write_text(
            "[Unit]\nDescription=Check the daily paper digest schedule every minute\n\n"
            "[Timer]\nOnBootSec=1min\nOnUnitActiveSec=1min\nAccuracySec=1s\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n",
            encoding="utf-8",
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{LINUX_UNIT}.timer"], check=True)
        print(f"installed={timer}")
    elif action == "uninstall":
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{LINUX_UNIT}.timer"], check=False)
        for path in (timer, service):
            if path.exists():
                path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        print(f"removed={timer}")
    elif action == "run-now":
        subprocess.run(["systemctl", "--user", "start", f"{LINUX_UNIT}.service"], check=True)
    else:
        result = subprocess.run(["systemctl", "--user", "status", f"{LINUX_UNIT}.timer"], check=False)
        raise SystemExit(result.returncode)


def windows(action: str, config_path: Path, runner: Path) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        raise RuntimeError("PowerShell was not found")
    script = SKILL_SCRIPTS / "schedule_windows.ps1"
    arguments = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Action",
        action,
        "-ConfigPath",
        str(config_path),
        "-RunnerPath",
        str(runner),
        "-PythonPath",
        sys.executable,
        "-TaskName",
        WINDOWS_TASK,
    ]
    subprocess.run(arguments, check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "status", "run-now"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--runner", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config, config_path = load_config(args.config)
    runner = args.runner.expanduser().resolve() if args.runner else default_runner(config, config_path)
    if not runner.is_file():
        raise RuntimeError(f"runner not found: {runner}")
    if sys.platform == "darwin":
        macos(args.action, config, config_path, runner)
    elif os.name == "nt":
        windows(args.action, config_path, runner)
    else:
        linux(args.action, config, config_path, runner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
