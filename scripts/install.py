#!/usr/bin/env python3
"""Install the canonical skill into one or more agent harnesses."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_SKILL = REPOSITORY / "skills" / "daily-paper-digest"
TEMPLATE = REPOSITORY / "config.example.json"
sys.path.insert(0, str(SOURCE_SKILL / "scripts"))
from common import (  # noqa: E402
    ConfigError,
    archive_root,
    atomic_write_json,
    default_config_path,
    read_json,
    validate_config,
)


HARNESS_PARENTS = {
    "codex": Path.home() / ".agents" / "skills",
    "claude-code": Path.home() / ".claude" / "skills",
    "qoder": Path.home() / ".qoder" / "skills",
    "qoderwork": Path.home() / ".qoderwork" / "skills",
}


def assignment(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected KEY=VALUE")
    key, assigned = value.split("=", 1)
    return key, assigned


def install_destination(name: str, target: Path | None) -> Path:
    if name == "custom":
        if not target:
            raise ConfigError("--target is required for --harness custom")
        return target.expanduser().resolve()
    return (HARNESS_PARENTS[name] / "daily-paper-digest").resolve()


def copy_skill(destination: Path) -> Path | None:
    backup = None
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = destination.with_name(f"{destination.name}.backup-{stamp}")
        destination.rename(backup)
    try:
        shutil.copytree(SOURCE_SKILL, destination)
    except Exception:
        if backup and backup.exists() and not destination.exists():
            backup.rename(destination)
        raise
    return backup


def configured_categories(config: dict) -> dict[str, dict]:
    return {str(category["key"]): category for category in config["digest"]["categories"]}


def write_config(args: argparse.Namespace, automation_harness: str) -> Path:
    path = args.config.expanduser().resolve() if args.config else default_config_path()
    if path.is_file():
        config = read_json(path)
    else:
        config = read_json(TEMPLATE)
    config = copy.deepcopy(config)
    if args.archive_root:
        config["archive"]["root"] = args.archive_root
    if args.time:
        config["schedule"]["time"] = args.time
    if args.timezone:
        config["schedule"]["timezone"] = args.timezone
    if args.total is not None:
        config["digest"]["total_papers"] = args.total
    config["agent"]["harness"] = automation_harness
    categories = configured_categories(config)
    for key, raw in args.quota:
        if key not in categories:
            raise ConfigError(f"unknown category in --quota: {key}")
        categories[key]["quota"] = int(raw)
        categories[key].pop("weight", None)
    for key, raw in args.terms:
        if key not in categories:
            raise ConfigError(f"unknown category in --terms: {key}")
        terms = [part.strip() for part in raw.split("|") if part.strip()]
        if not terms:
            raise ConfigError(f"category {key} needs at least one search term")
        categories[key]["search_terms"] = terms
    validate_config(config, path)
    archive_root(config, path).mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, config)
    return path


def select_runtime(installed: dict[str, Path], automation_harness: str) -> Path:
    key = automation_harness
    if key in installed:
        return installed[key]
    if automation_harness == "qoder" and "qoderwork" in installed:
        raise ConfigError("QoderWork can load the skill but has no local headless runner; also install qoder or choose another --agent-harness")
    if "custom" in installed:
        return installed["custom"]
    raise ConfigError(
        f"automation harness {automation_harness!r} was not installed; install it or pass --agent-harness"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--harness",
        action="append",
        choices=("codex", "claude-code", "qoder", "qoderwork", "all", "custom"),
        help="repeat to install multiple harnesses; default: codex",
    )
    parser.add_argument("--target", type=Path, help="full skill directory for the custom harness")
    parser.add_argument("--agent-harness", choices=("codex", "claude-code", "qoder", "none", "custom"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--archive-root")
    parser.add_argument("--time")
    parser.add_argument("--timezone")
    parser.add_argument("--total", type=int)
    parser.add_argument("--quota", action="append", type=assignment, default=[], metavar="KEY=COUNT")
    parser.add_argument("--terms", action="append", type=assignment, default=[], metavar="KEY=TERM1|TERM2")
    parser.add_argument("--no-schedule", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        requested = args.harness or ["codex"]
        if "all" in requested:
            requested = ["codex", "claude-code", "qoder"]
        requested = list(dict.fromkeys(requested))
        default_agent = next((name for name in requested if name in {"codex", "claude-code", "qoder"}), "none")
        automation_harness = args.agent_harness or default_agent
        installed: dict[str, Path] = {}
        for name in requested:
            destination = install_destination(name, args.target)
            backup = copy_skill(destination)
            installed[name] = destination
            print(f"installed {name}: {destination}")
            if backup:
                print(f"backup {name}: {backup}")
        config_path = write_config(args, automation_harness)
        print(f"config: {config_path}")
        validate = SOURCE_SKILL / "scripts" / "configure.py"
        subprocess.run([sys.executable, str(validate), "validate", "--config", str(config_path)], check=True)
        if not args.no_schedule and automation_harness != "none":
            runtime = select_runtime(installed, automation_harness)
            scheduler = REPOSITORY / "scripts" / "schedule.py"
            subprocess.run(
                [
                    sys.executable,
                    str(scheduler),
                    "install",
                    "--config",
                    str(config_path),
                    "--runner",
                    str(runtime / "scripts" / "run_daily.py"),
                ],
                check=True,
            )
        elif args.no_schedule:
            print("schedule: skipped")
        return 0
    except (ConfigError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
