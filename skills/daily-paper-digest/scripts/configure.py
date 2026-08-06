#!/usr/bin/env python3
"""Inspect, validate, and explicitly edit daily-paper-digest configuration."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from common import (
    ConfigError,
    allocate_quotas,
    atomic_write_json,
    default_config_path,
    load_config,
    read_json,
    validate_config,
)


def assignment(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected KEY=VALUE")
    key, assigned = value.split("=", 1)
    if not key or not assigned:
        raise argparse.ArgumentTypeError("expected non-empty KEY=VALUE")
    return key, assigned


def category_map(config: dict) -> dict[str, dict]:
    return {str(item["key"]): item for item in config["digest"]["categories"]}


def command_path(_: argparse.Namespace) -> int:
    print(default_config_path())
    return 0


def command_validate(args: argparse.Namespace) -> int:
    config, path = load_config(args.config)
    quotas = validate_config(config, path)
    print(f"config={path}")
    print(f"time={config['schedule']['time']} timezone={config['schedule']['timezone']}")
    print(f"total={config['digest']['total_papers']} quotas={json.dumps(quotas, ensure_ascii=False)}")
    return 0


def command_show(args: argparse.Namespace) -> int:
    config, _ = load_config(args.config)
    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


def command_init(args: argparse.Namespace) -> int:
    destination = Path(args.config).expanduser().resolve() if args.config else default_config_path()
    template = Path(args.template).expanduser().resolve()
    if destination.exists() and not args.force:
        raise ConfigError(f"refusing to overwrite existing config: {destination}; add --force")
    value = read_json(template)
    validate_config(value, destination)
    atomic_write_json(destination, value)
    print(destination)
    return 0


def command_set(args: argparse.Namespace) -> int:
    config, path = load_config(args.config)
    updated = copy.deepcopy(config)
    if args.time is not None:
        updated["schedule"]["time"] = args.time
    if args.timezone is not None:
        updated["schedule"]["timezone"] = args.timezone
    if args.archive_root is not None:
        updated["archive"]["root"] = args.archive_root
    if args.total is not None:
        updated["digest"]["total_papers"] = args.total
    if args.harness is not None:
        updated["agent"]["harness"] = args.harness
    categories = category_map(updated)
    for key in args.enable:
        if key not in categories:
            raise ConfigError(f"unknown category: {key}")
        categories[key]["enabled"] = True
    for key in args.disable:
        if key not in categories:
            raise ConfigError(f"unknown category: {key}")
        categories[key]["enabled"] = False
    for key, value in args.quota:
        if key not in categories:
            raise ConfigError(f"unknown category: {key}")
        categories[key]["quota"] = int(value)
        categories[key].pop("weight", None)
    for key, value in args.weight:
        if key not in categories:
            raise ConfigError(f"unknown category: {key}")
        categories[key].pop("quota", None)
        categories[key]["weight"] = float(value)
    for key, value in args.terms:
        if key not in categories:
            raise ConfigError(f"unknown category: {key}")
        terms = [term.strip() for term in value.split("|") if term.strip()]
        if not terms:
            raise ConfigError(f"category {key}: at least one search term is required")
        categories[key]["search_terms"] = terms
    validate_config(updated, path)
    atomic_write_json(path, updated)
    print(f"updated={path}")
    print(f"quotas={json.dumps(allocate_quotas(updated), ensure_ascii=False)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    path = commands.add_parser("path", help="print the default configuration path")
    path.set_defaults(func=command_path)
    validate = commands.add_parser("validate", help="validate a configuration")
    validate.add_argument("--config", type=Path)
    validate.set_defaults(func=command_validate)
    show = commands.add_parser("show", help="print the effective configuration")
    show.add_argument("--config", type=Path)
    show.set_defaults(func=command_show)
    initialize = commands.add_parser("init", help="copy and validate a template")
    initialize.add_argument("--template", type=Path, required=True)
    initialize.add_argument("--config", type=Path)
    initialize.add_argument("--force", action="store_true")
    initialize.set_defaults(func=command_init)
    update = commands.add_parser("set", help="set schedule, paths, topics, or allocation")
    update.add_argument("--config", type=Path)
    update.add_argument("--time")
    update.add_argument("--timezone")
    update.add_argument("--archive-root")
    update.add_argument("--total", type=int)
    update.add_argument("--harness", choices=("codex", "claude-code", "qoder", "none", "custom"))
    update.add_argument("--quota", action="append", type=assignment, default=[], metavar="KEY=COUNT")
    update.add_argument("--weight", action="append", type=assignment, default=[], metavar="KEY=WEIGHT")
    update.add_argument("--terms", action="append", type=assignment, default=[], metavar="KEY=TERM1|TERM2")
    update.add_argument("--enable", action="append", default=[], metavar="KEY")
    update.add_argument("--disable", action="append", default=[], metavar="KEY")
    update.set_defaults(func=command_set)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
