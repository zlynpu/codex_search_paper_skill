#!/usr/bin/env python3
"""Shared configuration, path, deduplication, and atomic-file helpers."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SKILL_ROOT.parents[1]
ARXIV_ID_PATTERN = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", re.I)
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class ConfigError(ValueError):
    """Raised when a configuration cannot be executed safely."""


def default_config_path() -> Path:
    override = os.environ.get("DAILY_PAPER_DIGEST_CONFIG")
    if override:
        return Path(os.path.expandvars(override)).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return (base / "daily-paper-digest" / "config.json").resolve()


def resolve_path(value: str | os.PathLike[str], config_path: Path) -> Path:
    text = os.path.expandvars(os.fspath(value)).strip()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def normalize_arxiv_id(value: str) -> str:
    match = ARXIV_ID_PATTERN.search(value or "")
    return match.group(1).lower() if match else re.sub(r"v\d+$", "", value.strip())


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value)


def slugify(title: str, arxiv_id: str) -> str:
    value = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    value = re.sub(r"-+", "-", value)[:72].rstrip("-")
    return value or f"arxiv-{normalize_arxiv_id(arxiv_id).replace('.', '-')}"


def parse_run_date(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return value


def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a JSON array")
    return value


def allocate_quotas(config: dict[str, Any]) -> dict[str, int]:
    digest = _require_mapping(config.get("digest"), "digest")
    categories = [
        category
        for category in _require_list(digest.get("categories"), "digest.categories")
        if category.get("enabled", True)
    ]
    total = digest.get("total_papers")
    if not isinstance(total, int) or not 1 <= total <= 100:
        raise ConfigError("digest.total_papers must be an integer from 1 to 100")
    if not categories:
        raise ConfigError("at least one digest category must be enabled")
    quotas = [category.get("quota") for category in categories]
    if all(isinstance(value, int) and value >= 0 for value in quotas):
        result = {str(category["key"]): int(category["quota"]) for category in categories}
        if sum(result.values()) != total:
            raise ConfigError(
                f"category quotas sum to {sum(result.values())}, not total_papers={total}"
            )
        return result
    if any(value is not None for value in quotas):
        raise ConfigError("use quota for every enabled category, or weight for every category")
    weights: list[float] = []
    for category in categories:
        value = category.get("weight")
        if not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError("weight must be positive when quotas are omitted")
        weights.append(float(value))
    weight_sum = sum(weights)
    raw = [total * value / weight_sum for value in weights]
    floors = [math.floor(value) for value in raw]
    remaining = total - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: (raw[i] - floors[i], -i), reverse=True)
    for index in order[:remaining]:
        floors[index] += 1
    return {str(category["key"]): floors[i] for i, category in enumerate(categories)}


def validate_config(config: dict[str, Any], config_path: Path) -> dict[str, int]:
    if config.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    schedule = _require_mapping(config.get("schedule"), "schedule")
    run_time = schedule.get("time")
    if not isinstance(run_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", run_time):
        raise ConfigError("schedule.time must use 24-hour HH:MM")
    timezone = schedule.get("timezone")
    if not isinstance(timezone, str) or not timezone:
        raise ConfigError("schedule.timezone is required")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(
            f"unknown IANA timezone {timezone!r}; on Windows install the 'tzdata' package"
        ) from exc
    if not isinstance(schedule.get("catch_up"), bool):
        raise ConfigError("schedule.catch_up must be true or false")
    retry_interval = schedule.get("retry_interval_minutes")
    if not isinstance(retry_interval, int) or not 1 <= retry_interval <= 1440:
        raise ConfigError("schedule.retry_interval_minutes must be from 1 to 1440")

    archive = _require_mapping(config.get("archive"), "archive")
    if not isinstance(archive.get("root"), str) or not archive["root"].strip():
        raise ConfigError("archive.root is required")
    resolve_path(archive["root"], config_path)

    zotero = _require_mapping(config.get("zotero"), "zotero")
    if not isinstance(zotero.get("enabled"), bool) or not isinstance(zotero.get("required"), bool):
        raise ConfigError("zotero.enabled and zotero.required must be true or false")
    if zotero.get("required") and not zotero.get("enabled"):
        raise ConfigError("zotero.required cannot be true when zotero.enabled is false")
    if not isinstance(zotero.get("top_collection"), str) or not zotero["top_collection"].strip():
        raise ConfigError("zotero.top_collection is required")

    digest = _require_mapping(config.get("digest"), "digest")
    categories = _require_list(digest.get("categories"), "digest.categories")
    top_recommendations = digest.get("top_recommendations", 3)
    if not isinstance(top_recommendations, int) or isinstance(top_recommendations, bool) or top_recommendations < 0:
        raise ConfigError("digest.top_recommendations must be a non-negative integer")
    seen: set[str] = set()
    featured_labels: set[str] = set()
    for index, category in enumerate(categories):
        category = _require_mapping(category, f"digest.categories[{index}]")
        key = category.get("key")
        if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
            raise ConfigError(f"invalid category key at index {index}: {key!r}")
        if key in seen:
            raise ConfigError(f"duplicate category key: {key}")
        seen.add(key)
        if not category.get("enabled", True):
            continue
        for field in ("label",):
            if not isinstance(category.get(field), str) or not category[field].strip():
                raise ConfigError(f"category {key}: {field} is required")
        if zotero.get("enabled") and (
            not isinstance(category.get("zotero_collection"), str)
            or not category["zotero_collection"].strip()
        ):
            raise ConfigError(f"category {key}: zotero_collection is required when Zotero is enabled")
        for field in ("search_terms", "arxiv_categories"):
            values = _require_list(category.get(field), f"category {key}.{field}")
            if not values or not all(isinstance(item, str) and item.strip() for item in values):
                raise ConfigError(f"category {key}: {field} must contain strings")
        negative_terms = _require_list(category.get("negative_terms", []), f"category {key}.negative_terms")
        if not all(isinstance(item, str) and item.strip() for item in negative_terms):
            raise ConfigError(f"category {key}: negative_terms must contain non-empty strings")
        minimum_score = category.get("minimum_relevance_score", 0)
        if (
            not isinstance(minimum_score, (int, float))
            or isinstance(minimum_score, bool)
            or not 0 <= float(minimum_score) <= 10000
        ):
            raise ConfigError(f"category {key}: minimum_relevance_score must be from 0 to 10000")
        featured = category.get("featured")
        if featured is not None:
            featured = _require_mapping(featured, f"category {key}.featured")
            label = featured.get("label")
            if not isinstance(label, str) or not label.strip():
                raise ConfigError(f"category {key}: featured.label is required")
            if label in featured_labels:
                raise ConfigError(f"duplicate featured label: {label}")
            featured_labels.add(label)
            priority = featured.get("selection_priority")
            if (
                not isinstance(priority, int)
                or isinstance(priority, bool)
                or not 0 <= priority <= 100
            ):
                raise ConfigError(
                    f"category {key}: featured.selection_priority must be an integer from 0 to 100"
                )
            if featured.get("confidence") not in {"normal", "high", "highest"}:
                raise ConfigError(
                    f"category {key}: featured.confidence must be normal, high, or highest"
                )
            for field in ("outside_top_recommendations", "require_detailed_note"):
                if not isinstance(featured.get(field), bool):
                    raise ConfigError(f"category {key}: featured.{field} must be true or false")
    quotas = allocate_quotas(config)
    enabled_categories = {
        str(category["key"]): category
        for category in categories
        if category.get("enabled", True)
    }
    outside_top_count = sum(
        quotas[key]
        for key, category in enabled_categories.items()
        if isinstance(category.get("featured"), dict)
        and category["featured"].get("outside_top_recommendations")
    )
    total_papers = int(digest["total_papers"])
    if top_recommendations > total_papers - outside_top_count:
        raise ConfigError(
            "digest.top_recommendations exceeds the number of non-special papers"
        )

    selection = _require_mapping(config.get("selection"), "selection")
    for field, minimum, maximum in (
        ("lookback_days", 1, 90),
        ("max_results_per_source", 10, 2000),
        ("minimum_original_figures", 0, 20),
        ("maximum_original_figures", 1, 50),
    ):
        value = selection.get(field)
        if not isinstance(value, int) or not minimum <= value <= maximum:
            raise ConfigError(f"selection.{field} must be from {minimum} to {maximum}")
    if selection["minimum_original_figures"] > selection["maximum_original_figures"]:
        raise ConfigError("minimum_original_figures cannot exceed maximum_original_figures")
    if not isinstance(selection.get("allow_quota_rebalance"), bool):
        raise ConfigError("selection.allow_quota_rebalance must be true or false")
    for field in ("global_negative_terms", "additional_history_files"):
        values = _require_list(selection.get(field, []), f"selection.{field}")
        if not all(isinstance(value, str) for value in values):
            raise ConfigError(f"selection.{field} must contain strings")

    agent = _require_mapping(config.get("agent"), "agent")
    if agent.get("harness") not in {"codex", "claude-code", "qoder", "none", "custom"}:
        raise ConfigError("agent.harness must be codex, claude-code, qoder, none, or custom")
    if agent.get("harness") == "custom" and not agent.get("custom_command"):
        raise ConfigError("agent.custom_command is required for a custom harness")
    timeout = agent.get("timeout_minutes")
    if not isinstance(timeout, int) or not 1 <= timeout <= 1440:
        raise ConfigError("agent.timeout_minutes must be from 1 to 1440")
    custom_command = agent.get("custom_command", [])
    if not isinstance(custom_command, list) or not all(isinstance(part, str) for part in custom_command):
        raise ConfigError("agent.custom_command must be an array of strings")
    return quotas


def load_config(path: str | Path | None = None) -> tuple[dict[str, Any], Path]:
    config_path = Path(path).expanduser().resolve() if path else default_config_path()
    if not config_path.is_file():
        raise ConfigError(
            f"configuration not found: {config_path}; run the repository installer first"
        )
    config = read_json(config_path)
    if not isinstance(config, dict):
        raise ConfigError("configuration root must be an object")
    validate_config(config, config_path)
    return config, config_path


def archive_root(config: dict[str, Any], config_path: Path) -> Path:
    return resolve_path(config["archive"]["root"], config_path)


def history_path(config: dict[str, Any], config_path: Path) -> Path:
    configured = str(config["archive"].get("history_file", "")).strip()
    return (
        resolve_path(configured, config_path)
        if configured
        else archive_root(config, config_path) / "pushed-paper-index.json"
    )


def day_directory(config: dict[str, Any], config_path: Path, run_date: date) -> Path:
    return archive_root(config, config_path) / f"{run_date:%Y/%m/%d}"


def categories_by_key(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(category["key"]): category
        for category in config["digest"]["categories"]
        if category.get("enabled", True)
    }


@dataclass(frozen=True)
class KnownPapers:
    ids: frozenset[str]
    titles: frozenset[str]


def _collect_known_from_value(value: Any, ids: set[str], titles: set[str]) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("arxiv_id"), str):
            ids.add(normalize_arxiv_id(value["arxiv_id"]))
        if isinstance(value.get("title"), str):
            normalized = normalize_title(value["title"])
            if normalized:
                titles.add(normalized)
        for child in value.values():
            _collect_known_from_value(child, ids, titles)
    elif isinstance(value, list):
        for child in value:
            _collect_known_from_value(child, ids, titles)
    elif isinstance(value, str):
        for match in ARXIV_ID_PATTERN.finditer(value):
            ids.add(match.group(1).lower())


def load_known_papers(config: dict[str, Any], config_path: Path) -> KnownPapers:
    ids: set[str] = set()
    titles: set[str] = set()
    paths: list[Path] = [history_path(config, config_path)]
    for raw in config["selection"].get("additional_history_files", []):
        paths.append(resolve_path(raw, config_path))
    root = archive_root(config, config_path)
    if root.exists():
        paths.extend(root.glob("[12][0-9][0-9][0-9]/[01][0-9]/[0-3][0-9]/digest.json"))
    seen_paths: set[Path] = set()
    for path in paths:
        path = path.resolve()
        if path in seen_paths or not path.is_file():
            continue
        seen_paths.add(path)
        try:
            value = read_json(path)
        except (OSError, json.JSONDecodeError):
            text = path.read_text(encoding="utf-8", errors="replace")
            ids.update(match.group(1).lower() for match in ARXIV_ID_PATTERN.finditer(text))
        else:
            _collect_known_from_value(value, ids, titles)
    ids.discard("")
    titles.discard("")
    return KnownPapers(frozenset(ids), frozenset(titles))


def category_counts(papers: Iterable[dict[str, Any]]) -> Counter[str]:
    return Counter(str(paper.get("channel", "")) for paper in papers)


def configured_now(config: dict[str, Any]) -> datetime:
    return datetime.now(ZoneInfo(config["schedule"]["timezone"]))


def safe_child(parent: Path, child: Path) -> Path:
    parent = parent.resolve()
    child = child.resolve()
    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise RuntimeError(f"path escapes configured root: {child}") from exc
    return child


def history_record(paper: dict[str, Any], run_date: date) -> dict[str, str]:
    return {
        "arxiv_id": normalize_arxiv_id(str(paper["arxiv_id"])),
        "title": str(paper["title"]),
        "channel": str(paper["channel"]),
        "pushed_on": run_date.isoformat(),
    }
