#!/usr/bin/env python3
"""Validate a drafted digest and atomically finalize deduplication history."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import (
    ConfigError,
    allocate_quotas,
    atomic_write_json,
    categories_by_key,
    category_counts,
    day_directory,
    history_path,
    history_record,
    load_config,
    normalize_arxiv_id,
    normalize_title,
    parse_run_date,
    read_json,
)


REQUIRED_SECTIONS = (
    "## 一句话总结",
    "## S｜Situation：研究情境与具体失败模式",
    "## T｜Task：论文要解决的任务与约束",
    "## A｜Action：按论文 Method 逐部分翻译、解释与公式直觉",
    "## R｜Result：实验结果、收益与证据",
    "## 与我的研究方向的关联",
    "## 局限与证据边界",
    "## 原文摘要",
)
STAR_MINIMUMS = {
    "## S｜Situation：研究情境与具体失败模式": 300,
    "## T｜Task：论文要解决的任务与约束": 220,
    "## R｜Result：实验结果、收益与证据": 300,
}
ACTION_HEADING = "## A｜Action：按论文 Method 逐部分翻译、解释与公式直觉"
METHOD_PART_HEADING = re.compile(
    r"^###\s*方法部分\s*(\d+)\s*[：:]\s*(.+)$",
    flags=re.MULTILINE,
)
METHOD_PART_MINIMUM = 220
STAGE_FIELD_MINIMUMS = {
    "name": 2,
    "source_heading": 2,
    "translation": 40,
    "explanation": 80,
    "evidence": 8,
}
FORMULA_HEADING = re.compile(r"^####\s*必要公式与直觉\s*$", flags=re.MULTILINE)
FORBIDDEN_ROLE_LABEL = re.compile(
    r"^\s*(?:\*\*)?(?:翻译|解释)(?:\*\*)?\s*[：:]",
    flags=re.MULTILINE,
)
EQUATION_FIELD_MINIMUMS = {
    "latex": 3,
    "variables": 20,
    "role": 20,
    "intuition": 20,
    "evidence": 4,
}
ZOTERO_URI = re.compile(r"zotero://open-pdf/library/items/[A-Z0-9]{8}")


def compact_length(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def section_text(markdown: str, heading: str) -> str:
    start = markdown.find(heading)
    if start < 0:
        return ""
    start += len(heading)
    match = re.search(r"^##\s+", markdown[start:], flags=re.MULTILINE)
    return markdown[start : start + match.start()] if match else markdown[start:]


def normalize_method_heading(value: str) -> str:
    value = re.sub(r"^\s*\d+(?:\.\d+)*\s*[-:：.]?\s*", "", value)
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()


def italic_paragraph_text(value: str) -> str:
    value = value.strip()
    if value.startswith("**") or not (value.startswith("*") and value.endswith("*")):
        return ""
    return value[1:-1].strip()


def validate_translation_explanation_pairs(block: str, slug: str, index: int) -> re.Match[str]:
    if FORBIDDEN_ROLE_LABEL.search(block):
        raise RuntimeError(
            f"{slug}: Method part {index + 1} must not use 翻译： or 解释： labels"
        )
    formula_matches = list(FORMULA_HEADING.finditer(block))
    if len(formula_matches) != 1:
        raise RuntimeError(
            f"{slug}: Method part {index + 1} must contain one #### 必要公式与直觉 heading"
        )
    prose = block[: formula_matches[0].start()].strip()
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n[ \t]*\n", prose)
        if paragraph.strip()
    ]
    if len(paragraphs) < 2 or len(paragraphs) % 2:
        raise RuntimeError(
            f"{slug}: Method part {index + 1} must pair every body translation "
            "paragraph with an immediately following italic explanation"
        )
    for pair_index in range(0, len(paragraphs), 2):
        translation = paragraphs[pair_index]
        explanation = italic_paragraph_text(paragraphs[pair_index + 1])
        if translation.startswith(("*", "_", "#", "!", "```", "$$")) or compact_length(translation) < 40:
            raise RuntimeError(
                f"{slug}: Method part {index + 1} translation paragraph "
                f"{pair_index // 2 + 1} must be unlabeled regular body text"
            )
        if compact_length(explanation) < 80:
            raise RuntimeError(
                f"{slug}: Method part {index + 1} paragraph pair "
                f"{pair_index // 2 + 1} must end with an immediately following "
                "whole-paragraph italic explanation"
            )
    return formula_matches[0]


def validate_stage(stage: Any, slug: str, index: int) -> None:
    if not isinstance(stage, dict):
        raise RuntimeError(f"{slug}: method_stages[{index}] must be an object")
    for field, minimum in STAGE_FIELD_MINIMUMS.items():
        value = stage.get(field)
        if not isinstance(value, str) or compact_length(value) < minimum:
            raise RuntimeError(f"{slug}: Method part {index + 1} has an empty or vague {field}")
    equations = stage.get("equations")
    if not isinstance(equations, list):
        raise RuntimeError(f"{slug}: Method part {index + 1} equations must be an array")
    if not isinstance(stage.get("equation_note"), str):
        raise RuntimeError(f"{slug}: Method part {index + 1} equation_note must be a string")
    if equations:
        for equation_index, equation in enumerate(equations):
            if not isinstance(equation, dict):
                raise RuntimeError(
                    f"{slug}: Method part {index + 1} equation {equation_index + 1} must be an object"
                )
            for field, minimum in EQUATION_FIELD_MINIMUMS.items():
                value = equation.get(field)
                if not isinstance(value, str) or compact_length(value) < minimum:
                    raise RuntimeError(
                        f"{slug}: Method part {index + 1} equation {equation_index + 1} "
                        f"has an empty or vague {field}"
                    )
    else:
        note = stage.get("equation_note")
        if not isinstance(note, str) or compact_length(note) < 8:
            raise RuntimeError(
                f"{slug}: Method part {index + 1} has no equations and lacks equation_note"
            )


def validate_paper(
    day: Path,
    summary: dict[str, Any],
    config: dict[str, Any],
    require_zotero: bool,
) -> dict[str, Any]:
    slug = str(summary.get("slug", ""))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", slug):
        raise RuntimeError(f"invalid slug: {slug!r}")
    json_path = day / f"{slug}.json"
    markdown_path = day / f"{slug}.md"
    if not json_path.is_file() or not markdown_path.is_file():
        raise RuntimeError(f"{slug}: missing Markdown or JSON note")
    paper = read_json(json_path)
    if normalize_arxiv_id(str(paper.get("arxiv_id", ""))) != normalize_arxiv_id(str(summary.get("arxiv_id", ""))):
        raise RuntimeError(f"{slug}: paper JSON arXiv ID differs from digest")
    if paper.get("channel") != summary.get("channel"):
        raise RuntimeError(f"{slug}: paper JSON category differs from digest")
    markdown = markdown_path.read_text(encoding="utf-8")
    for section in REQUIRED_SECTIONS:
        if section not in markdown:
            raise RuntimeError(f"{slug}: missing required section {section}")
    positions = [markdown.find(section) for section in REQUIRED_SECTIONS]
    if positions != sorted(positions):
        raise RuntimeError(f"{slug}: required sections are not in the STAR contract order")
    if str(paper.get("paper_url", "")) not in markdown:
        raise RuntimeError(f"{slug}: original-paper URL is absent from Markdown")

    for heading, minimum in STAR_MINIMUMS.items():
        length = compact_length(section_text(markdown, heading))
        if length < minimum:
            raise RuntimeError(
                f"{slug}: {heading} has {length} non-space characters; requires {minimum}"
            )

    stages = paper.get("method_stages")
    if not isinstance(stages, list) or not stages:
        raise RuntimeError(f"{slug}: method_stages must contain one object per actual Method part")
    for index, stage in enumerate(stages):
        validate_stage(stage, slug, index)
    action = section_text(markdown, ACTION_HEADING)
    matches = list(METHOD_PART_HEADING.finditer(action))
    if len(matches) != len(stages):
        raise RuntimeError(
            f"{slug}: Action has {len(matches)} Method-part headings for "
            f"{len(stages)} JSON Method parts"
        )
    numbers = [int(match.group(1)) for match in matches]
    if numbers != list(range(1, len(stages) + 1)):
        raise RuntimeError(f"{slug}: Method-part headings must be numbered consecutively from 1")
    for index, (stage, match) in enumerate(zip(stages, matches)):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(action)
        block = action[match.end() : end]
        if compact_length(block) < METHOD_PART_MINIMUM:
            raise RuntimeError(
                f"{slug}: Method part {index + 1} has {compact_length(block)} non-space "
                f"characters; requires {METHOD_PART_MINIMUM}"
            )
        markdown_heading = normalize_method_heading(match.group(2))
        source_heading = normalize_method_heading(str(stage["source_heading"]))
        if source_heading not in markdown_heading:
            raise RuntimeError(
                f"{slug}: Method part {index + 1} heading does not include its original heading"
            )
        formula_heading = validate_translation_explanation_pairs(block, slug, index)
        formula_text = block[formula_heading.end() :]
        if stage["equations"]:
            if "$" not in formula_text or compact_length(formula_text) < 80:
                raise RuntimeError(
                    f"{slug}: Method part {index + 1} must render and explain its equations"
                )
        elif not re.search(r"无关键公式|没有关键公式|不适用|论文未给出", formula_text):
            raise RuntimeError(
                f"{slug}: Method part {index + 1} must explicitly state that no key formula exists"
            )

    allowed_figures = {
        str(figure.get("path"))
        for figure in paper.get("figures", [])
        if isinstance(figure, dict) and figure.get("source_kind") == "original-paper-figure"
    }
    embedded = re.findall(r"!\[([^\]]*)\]\((images/[^)\s]+)\)", markdown)
    references = [reference for _, reference in embedded]
    minimum = min(2, len(allowed_figures))
    if not minimum <= len(references) <= 4:
        raise RuntimeError(f"{slug}: expected {minimum}–4 embedded original figures, found {len(references)}")
    if len(set(references)) != len(references):
        raise RuntimeError(f"{slug}: the same original figure is embedded more than once")
    expected_prefix = f"images/{slug}/"
    for reference in references:
        if not reference.startswith(expected_prefix):
            raise RuntimeError(f"{slug}: image is in another paper's folder: {reference}")
        if reference not in allowed_figures:
            raise RuntimeError(f"{slug}: image is not listed in this paper JSON: {reference}")
        if not (day / reference).is_file():
            raise RuntimeError(f"{slug}: referenced image is missing: {reference}")
    for alt_text, reference in embedded:
        if compact_length(alt_text) < 8:
            raise RuntimeError(f"{slug}: figure caption is too vague for {reference}")
    action_references = re.findall(r"!\[[^\]]*\]\((images/[^)\s]+)\)", action)
    if allowed_figures and not action_references:
        raise RuntimeError(f"{slug}: at least one available original figure must be placed inside Action")

    for figure in paper.get("figures", []):
        if figure.get("source_kind") != "original-paper-figure" or not str(figure.get("source_url", "")).startswith(("https://", "http://")):
            raise RuntimeError(f"{slug}: invalid original-figure provenance")
        if not str(figure.get("path", "")).startswith(expected_prefix):
            raise RuntimeError(f"{slug}: figure manifest path escapes its paper folder")
        if not (day / str(figure["path"])).is_file():
            raise RuntimeError(f"{slug}: figure manifest points to a missing file: {figure['path']}")

    zotero_uri = str(paper.get("zotero_uri", ""))
    if require_zotero:
        if not ZOTERO_URI.fullmatch(zotero_uri):
            raise RuntimeError(f"{slug}: missing valid Zotero PDF deep link")
        if f"]({zotero_uri})" not in markdown:
            raise RuntimeError(f"{slug}: Zotero deep link is absent from Markdown")
    elif zotero_uri and not ZOTERO_URI.fullmatch(zotero_uri):
        raise RuntimeError(f"{slug}: malformed Zotero URI")
    return paper


def verify(
    config: dict[str, Any],
    config_path: Path,
    run_date,
    *,
    require_zotero: bool = False,
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    day = day_directory(config, config_path, run_date)
    digest_json = day / "digest.json"
    digest_markdown = day / "digest.md"
    if not digest_json.is_file() or not digest_markdown.is_file():
        raise RuntimeError(f"missing digest.json or digest.md in {day}")
    digest = read_json(digest_json)
    summaries = digest.get("papers")
    if not isinstance(summaries, list):
        raise RuntimeError("digest.papers must be an array")
    total = int(config["digest"]["total_papers"])
    if len(summaries) != total:
        raise RuntimeError(f"expected {total} papers, found {len(summaries)}")
    identifiers = [normalize_arxiv_id(str(paper.get("arxiv_id", ""))) for paper in summaries]
    titles = [normalize_title(str(paper.get("title", ""))) for paper in summaries]
    if len(set(identifiers)) != total or "" in identifiers:
        raise RuntimeError("duplicate or invalid normalized arXiv IDs in digest")
    if len(set(titles)) != total or "" in titles:
        raise RuntimeError("duplicate or invalid normalized titles in digest")
    counts = category_counts(summaries)
    quotas = allocate_quotas(config)
    if not config["selection"].get("allow_quota_rebalance") and counts != Counter(quotas):
        raise RuntimeError(f"category allocation differs: expected {quotas}, found {dict(counts)}")
    if config["selection"].get("allow_quota_rebalance") and set(counts) - set(quotas):
        raise RuntimeError(f"digest contains disabled or unknown categories: {set(counts) - set(quotas)}")

    actual_markdown = {path.stem for path in day.glob("*.md") if path.name not in {"digest.md", "JOB.md"}}
    actual_json = {path.stem for path in day.glob("*.json") if path.name != "digest.json"}
    expected = {str(paper["slug"]) for paper in summaries}
    if actual_markdown != expected:
        raise RuntimeError(f"paper Markdown set differs: expected {sorted(expected)}, found {sorted(actual_markdown)}")
    if actual_json != expected:
        raise RuntimeError(f"paper JSON set differs: expected {sorted(expected)}, found {sorted(actual_json)}")

    papers = [validate_paper(day, summary, config, require_zotero) for summary in summaries]
    digest_text = digest_markdown.read_text(encoding="utf-8")
    for paper in papers:
        if f"({paper['slug']}.md)" not in digest_text:
            raise RuntimeError(f"digest.md does not link to {paper['slug']}.md")
        if require_zotero and f"[Zotero]({paper['zotero_uri']})" not in digest_text:
            raise RuntimeError(f"digest.md does not contain the Zotero PDF link for {paper['slug']}")
    for category in categories_by_key(config).values():
        if category["label"] not in digest_text:
            raise RuntimeError(f"digest.md is missing category label {category['label']}")
    print(f"verified={day}")
    print(f"papers={len(papers)} categories={dict(counts)} images={sum(len(p['figures']) for p in papers)}")
    return day, digest, papers


def finalize(config: dict[str, Any], config_path: Path, run_date, day: Path, digest: dict[str, Any], papers: list[dict[str, Any]]) -> None:
    target = history_path(config, config_path)
    if target.is_file():
        history = read_json(target)
        if not isinstance(history, dict) or not isinstance(history.get("papers", []), list):
            raise RuntimeError(f"history file has an unsupported format: {target}")
    else:
        history = {"schema_version": 1, "papers": []}
    existing = {
        normalize_arxiv_id(str(item.get("arxiv_id", ""))): item
        for item in history.get("papers", [])
        if isinstance(item, dict)
    }
    for paper in papers:
        identifier = normalize_arxiv_id(str(paper["arxiv_id"]))
        prior = existing.get(identifier)
        if prior and prior.get("pushed_on") != run_date.isoformat():
            raise RuntimeError(f"refusing to finalize duplicate paper already pushed on {prior.get('pushed_on')}: {identifier}")
        if not prior:
            record = history_record(paper, run_date)
            history.setdefault("papers", []).append(record)
            existing[identifier] = record
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    history["updated_at"] = now
    digest["papers"] = papers
    digest["status"] = "published"
    digest["verified_at"] = now
    digest["counts"] = {
        "papers": len(papers),
        "original_images": sum(len(paper["figures"]) for paper in papers),
        "categories": dict(category_counts(papers)),
    }
    atomic_write_json(target, history)
    atomic_write_json(day / "digest.json", digest)
    print(f"finalized={day}")
    print(f"history={target} entries={len(history['papers'])}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--date")
    parser.add_argument("--require-zotero", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config, config_path = load_config(args.config)
        run_date = parse_run_date(args.date)
        require_zotero = args.require_zotero or (
            args.finalize and config["zotero"].get("enabled") and config["zotero"].get("required")
        )
        day, digest, papers = verify(config, config_path, run_date, require_zotero=require_zotero)
        if args.finalize:
            finalize(config, config_path, run_date, day, digest, papers)
        return 0
    except (ConfigError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
