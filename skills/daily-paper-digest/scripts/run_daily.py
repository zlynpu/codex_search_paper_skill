#!/usr/bin/env python3
"""Run the complete digest workflow through Codex, Claude Code, Qoder, or a custom harness."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from common import (
    ConfigError,
    archive_root,
    atomic_write_json,
    configured_now,
    day_directory,
    load_config,
    parse_run_date,
    read_json,
    resolve_path,
)
from prepare_digest import prepare
from verify_digest import finalize, verify
from zotero_bridge import link_digest


def state_path(config: dict[str, Any], config_path: Path) -> Path:
    return archive_root(config, config_path) / ".daily-paper-digest" / "state.json"


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1}
    value = read_json(path)
    return value if isinstance(value, dict) else {"schema_version": 1}


def save_state(path: Path, value: dict[str, Any]) -> None:
    value["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    atomic_write_json(path, value)


class RunLock:
    def __init__(self, path: Path, stale_minutes: int):
        self.path = path
        self.stale_minutes = stale_minutes
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age = time.time() - self.path.stat().st_mtime
            if age > self.stale_minutes * 60:
                self.path.unlink()
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(f"another daily-paper run holds {self.path}") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}, stream)
        self.acquired = True
        return self

    def __exit__(self, *_):
        if self.acquired and self.path.exists():
            self.path.unlink()


def due(config: dict[str, Any], state: dict[str, Any], now: datetime) -> tuple[bool, str]:
    today = now.date().isoformat()
    completion_field = "last_prepared_date" if config["agent"]["harness"] == "none" else "last_completed_date"
    if state.get(completion_field) == today:
        return False, f"already completed for {today}"
    hour, minute = (int(part) for part in config["schedule"]["time"].split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < target:
        return False, f"not due until {target.isoformat()}"
    if not config["schedule"].get("catch_up", True) and now >= target + timedelta(minutes=1):
        return False, "exact scheduled minute was missed and catch_up is disabled"
    last_attempt = state.get("last_attempt_at")
    if last_attempt:
        try:
            parsed = datetime.fromisoformat(str(last_attempt))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
            retry = int(config["schedule"].get("retry_interval_minutes", 30))
            if elapsed < timedelta(minutes=retry):
                return False, f"waiting for {retry}-minute retry interval"
        except ValueError:
            pass
    return True, "due"


def executable(config: dict[str, Any], fallback: str) -> str:
    override = str(config["agent"].get("executable", "")).strip()
    value = override or shutil.which(fallback)
    if not value:
        raise RuntimeError(f"{fallback!r} executable was not found; set agent.executable")
    return str(value)


def harness_command(
    config: dict[str, Any],
    config_path: Path,
    day: Path,
    run_date,
    prompt: str,
) -> tuple[list[str], str | None]:
    harness = config["agent"]["harness"]
    if harness == "codex":
        return [
            executable(config, "codex"),
            "exec",
            "--cd",
            str(day),
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "-",
        ], prompt
    if harness == "claude-code":
        return [
            executable(config, "claude"),
            "-p",
            "--permission-mode",
            "acceptEdits",
            prompt,
        ], None
    if harness == "qoder":
        return [
            executable(config, "qodercli"),
            "--prompt",
            prompt,
            "--permission-mode",
            "accept_edits",
        ], None
    if harness == "custom":
        replacements = {
            "prompt": prompt,
            "prompt_file": str(day / "JOB.md"),
            "workspace": str(day),
            "config": str(config_path),
            "date": run_date.isoformat(),
        }
        command = [str(part).format(**replacements) for part in config["agent"]["custom_command"]]
        if not command:
            raise RuntimeError("agent.custom_command is empty")
        return command, None
    raise RuntimeError(f"harness {harness!r} does not run an agent")


def log_directory(config: dict[str, Any], config_path: Path) -> Path:
    configured = str(config["archive"].get("log_directory", "")).strip()
    return (
        resolve_path(configured, config_path)
        if configured
        else archive_root(config, config_path) / ".daily-paper-digest" / "logs"
    )


def invoke_agent(config: dict[str, Any], config_path: Path, run_date, day: Path) -> Path:
    prompt = (day / "JOB.md").read_text(encoding="utf-8")
    command, stdin = harness_command(config, config_path, day, run_date, prompt)
    logs = log_directory(config, config_path)
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"agent-{run_date.isoformat()}.log"
    timeout = int(config["agent"].get("timeout_minutes", 120)) * 60
    with log.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("command=" + json.dumps(command, ensure_ascii=False) + "\n")
        stream.flush()
        result = subprocess.run(
            command,
            cwd=day,
            input=stdin,
            text=True,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(f"{config['agent']['harness']} exited with {result.returncode}; see {log}")
    return log


def run(config: dict[str, Any], config_path: Path, run_date, *, refresh: bool, prepare_only: bool) -> Path:
    day = prepare(config, config_path, run_date, refresh)
    if prepare_only or config["agent"]["harness"] == "none":
        return day
    log = invoke_agent(config, config_path, run_date, day)
    print(f"agent_log={log}")
    verify(config, config_path, run_date, require_zotero=False)
    if config["zotero"].get("enabled"):
        try:
            link_digest(config, config_path, run_date)
        except Exception as exc:
            if config["zotero"].get("required"):
                raise
            print(
                f"warning=optional Zotero linking failed; finalizing notes without links: {exc}",
                file=sys.stderr,
            )
    require_zotero = bool(config["zotero"].get("enabled") and config["zotero"].get("required"))
    day, digest, papers = verify(config, config_path, run_date, require_zotero=require_zotero)
    finalize(config, config_path, run_date, day, digest, papers)
    return day


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--date")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--if-due", action="store_true")
    mode.add_argument("--force", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config, config_path = load_config(args.config)
        now = configured_now(config)
        state_file = state_path(config, config_path)
        state = load_state(state_file)
        if args.if_due:
            should_run, reason = due(config, state, now)
            if not should_run:
                print(f"skip={reason}")
                return 0
            run_date = now.date()
        else:
            run_date = parse_run_date(args.date) if args.date else now.date()
        timeout = int(config["agent"].get("timeout_minutes", 120)) + 30
        lock_path = state_file.with_name("run.lock")
        with RunLock(lock_path, timeout):
            state = load_state(state_file)
            state["last_attempt_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state["last_attempt_date"] = run_date.isoformat()
            save_state(state_file, state)
            try:
                day = run(config, config_path, run_date, refresh=args.refresh, prepare_only=args.prepare_only)
            except Exception as exc:
                state = load_state(state_file)
                state["last_error"] = str(exc)
                state["last_failed_date"] = run_date.isoformat()
                save_state(state_file, state)
                raise
            state = load_state(state_file)
            if args.prepare_only or config["agent"]["harness"] == "none":
                state["last_prepared_date"] = run_date.isoformat()
            else:
                state["last_completed_date"] = run_date.isoformat()
            state.pop("last_error", None)
            save_state(state_file, state)
            print(f"completed={day}")
        return 0
    except (ConfigError, RuntimeError, ValueError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"daily run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
