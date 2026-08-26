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

from PIL import Image, UnidentifiedImageError

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
    "## A｜Action：把论文方法完整走一遍",
    "## R｜Result：实验结果、收益与证据",
    "## 与我的研究方向的关联",
    "## 局限与证据边界",
    "## 原文摘要",
)
STAR_MINIMUMS = {
    "## S｜Situation：研究情境与具体失败模式": 220,
    "## T｜Task：论文要解决的任务与约束": 160,
    "## R｜Result：实验结果、收益与证据": 220,
}
ACTION_HEADING = "## A｜Action：把论文方法完整走一遍"
METHOD_PART_HEADING = re.compile(
    r"^###\s*方法部分\s*(\d+)\s*[：:]\s*(.+)$",
    flags=re.MULTILINE,
)
ACTION_INTRO_MINIMUM = 220
METHOD_PART_MINIMUM = 450
SUBMODULE_HEADING = re.compile(r"^####\s+(.+)$", flags=re.MULTILINE)
SUBMODULE_MINIMUM = 180
STAGE_FIELD_MINIMUMS = {
    "name": 2,
    "source_heading": 2,
    "translation": 40,
    "explanation": 80,
    "overview": 25,
    "walkthrough": 120,
    "evidence": 8,
}
SUBMODULE_FIELD_MINIMUMS = {
    "name": 2,
    "source_heading": 2,
    "input": 20,
    "output": 20,
    "purpose": 30,
    "evidence": 8,
}
FORBIDDEN_ROLE_LABEL = re.compile(
    r"^\s*(?:\*\*)?(?:翻译|解释)(?:\*\*)?\s*[：:]",
    flags=re.MULTILINE,
)
GENERIC_METHOD_PATTERNS = (
    re.compile(r"原文的.+部分把该组件放在完整流水线中的对应位置"),
    re.compile(r"其输入延续自上一部分提供的数据、状态或中间表示"),
    re.compile(r"输出则作为下一部分训练目标、条件表示或推理接口"),
    re.compile(r"先把上游给出的信息整理成该模块真正需要的形式"),
    re.compile(r"通俗地说，这一步是在解决.+这一局部瓶颈"),
    re.compile(r"视觉、语言、轨迹或潜状态"),
    re.compile(r"生成图像、问答结论、智能体轨迹、机器人动作"),
    re.compile(r"编辑后的图像、视频问答、诊断标签、多智能体答案、机器人动作"),
    re.compile(r"读取该阶段需要的"),
    re.compile(r"论文专属中间结果"),
    re.compile(r"从概念名称变成"),
    re.compile(r"完成“.+?”后可供下一部分读取的结构化状态"),
)
PROCESS_LEAK_PATTERNS = (
    re.compile(r"原文操作证据为"),
    re.compile(r"原文证据位于"),
    re.compile(r"可核对的转换文本"),
    re.compile(r"这段证据限定"),
    re.compile(r"证据位置(?:是|为|位于)?"),
    re.compile(r"对应依据为"),
    re.compile(r"而非根据类似论文推测"),
    re.compile(r"从官方摘要和原文图示可确认"),
    re.compile(r"(?:官方|转换后的)\s*HTML"),
    re.compile(r"(?:LaTeXML|html_feedback)/issues", re.IGNORECASE),
    re.compile(r"(?:原文|论文)没有报告的(?:隐藏|参数|细节)"),
)
RAW_SOURCE_PATTERNS = (
    re.compile(r"^\s*>\s*[A-Za-z][A-Za-z\s,.;:()\-]{80,}$", re.MULTILINE),
    re.compile(r"(?:原文|英文)(?:摘录|引用|原句)\s*[：:]"),
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


def validate_original_image(path: Path, slug: str) -> None:
    suffix = path.suffix.casefold()
    if suffix == ".svg":
        prefix = path.read_bytes()[:4096].lstrip().lower()
        if b"<svg" not in prefix:
            raise RuntimeError(f"{slug}: manifested SVG has no SVG root: {path.name}")
        return
    expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".gif": "GIF", ".webp": "WEBP"}.get(suffix)
    if not expected:
        raise RuntimeError(f"{slug}: unsupported original-figure extension: {path.name}")
    try:
        with Image.open(path) as image:
            width, height = image.size
            actual = str(image.format or "").upper()
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise RuntimeError(f"{slug}: original figure is not decodable: {path.name}: {exc}") from exc
    if actual != expected:
        raise RuntimeError(f"{slug}: figure signature {actual} does not match {path.name}")
    if width < 64 or height < 64:
        raise RuntimeError(f"{slug}: original figure is implausibly small: {path.name} {width}x{height}")


def section_text(markdown: str, heading: str) -> str:
    start = markdown.find(heading)
    if start < 0:
        return ""
    start += len(heading)
    match = re.search(r"^##\s+", markdown[start:], flags=re.MULTILINE)
    return markdown[start : start + match.start()] if match else markdown[start:]


def cjk_ratio(value: str) -> float:
    cjk = len(re.findall(r"[\u3400-\u9fff]", value))
    letters = len(re.findall(r"[A-Za-z\u3400-\u9fff]", value))
    return cjk / max(letters, 1)


def validate_reader_facing_prose(markdown: str, slug: str) -> None:
    """Keep source-audit mechanics out of the final reader-facing note."""
    for pattern in PROCESS_LEAK_PATTERNS:
        if pattern.search(markdown):
            raise RuntimeError(
                f"{slug}: final note leaks source-verification or extraction-process language"
            )
    for pattern in RAW_SOURCE_PATTERNS:
        if pattern.search(markdown):
            raise RuntimeError(f"{slug}: final note contains a raw source excerpt")

    abstract = section_text(markdown, "## 原文摘要")
    if compact_length(abstract) < 80 or cjk_ratio(abstract) < 0.55:
        raise RuntimeError(
            f"{slug}: 原文摘要 must be a fluent Chinese translation/condensation, not raw English"
        )

    paragraphs = [
        re.sub(r"\s+", "", paragraph)
        for paragraph in re.split(r"\n\s*\n", markdown)
        if compact_length(paragraph) >= 80 and not paragraph.lstrip().startswith("!")
    ]
    if any(count > 1 for count in Counter(paragraphs).values()):
        raise RuntimeError(f"{slug}: final note repeats a long paragraph as padding")


def normalize_method_heading(value: str) -> str:
    value = re.sub(r"^\s*\d+(?:\.\d+)*\s*[-:：.]?\s*", "", value)
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE).casefold()


def validate_submodule(submodule: Any, slug: str, stage_index: int, submodule_index: int) -> None:
    label = f"{slug}: Method part {stage_index + 1} submodule {submodule_index + 1}"
    if not isinstance(submodule, dict):
        raise RuntimeError(f"{label} must be an object")
    for field, minimum in SUBMODULE_FIELD_MINIMUMS.items():
        value = submodule.get(field)
        if not isinstance(value, str) or compact_length(value) < minimum:
            raise RuntimeError(f"{label} has an empty or vague {field}")
    operations = submodule.get("operations")
    if not isinstance(operations, list) or len(operations) < 2:
        raise RuntimeError(f"{label} operations must contain at least two ordered steps")
    for operation_index, operation in enumerate(operations):
        if not isinstance(operation, str) or compact_length(operation) < 15:
            raise RuntimeError(
                f"{label} operation {operation_index + 1} is empty or too vague"
            )


def validate_stage(stage: Any, slug: str, index: int) -> None:
    if not isinstance(stage, dict):
        raise RuntimeError(f"{slug}: method_stages[{index}] must be an object")
    for field, minimum in STAGE_FIELD_MINIMUMS.items():
        value = stage.get(field)
        if not isinstance(value, str) or compact_length(value) < minimum:
            raise RuntimeError(f"{slug}: Method part {index + 1} has an empty or vague {field}")
    submodules = stage.get("submodules")
    if not isinstance(submodules, list) or not submodules:
        raise RuntimeError(
            f"{slug}: Method part {index + 1} must enumerate every nested submodule"
        )
    for submodule_index, submodule in enumerate(submodules):
        validate_submodule(submodule, slug, index, submodule_index)
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
    for field in (
        "top_recommendation",
        "special_recommendation",
        "special_recommendation_label",
        "recommendation_confidence",
        "recommendation_priority",
    ):
        if paper.get(field) != summary.get(field):
            raise RuntimeError(f"{slug}: paper JSON {field} differs from digest")
    markdown = markdown_path.read_text(encoding="utf-8")
    for section in REQUIRED_SECTIONS:
        if section not in markdown:
            raise RuntimeError(f"{slug}: missing required section {section}")
    positions = [markdown.find(section) for section in REQUIRED_SECTIONS]
    if positions != sorted(positions):
        raise RuntimeError(f"{slug}: required sections are not in the STAR contract order")
    if str(paper.get("paper_url", "")) not in markdown:
        raise RuntimeError(f"{slug}: original-paper URL is absent from Markdown")
    validate_reader_facing_prose(markdown, slug)

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
    if compact_length(action[: matches[0].start()]) < ACTION_INTRO_MINIMUM:
        raise RuntimeError(
            f"{slug}: Action must begin with a concrete end-to-end workflow and running example"
        )
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
        if FORBIDDEN_ROLE_LABEL.search(block):
            raise RuntimeError(
                f"{slug}: Method part {index + 1} must not use 翻译： or 解释： labels"
            )
        for pattern in GENERIC_METHOD_PATTERNS:
            if pattern.search(block):
                raise RuntimeError(
                    f"{slug}: Method part {index + 1} contains generic method filler"
                )
        submodules = stage["submodules"]
        submodule_matches = list(SUBMODULE_HEADING.finditer(block))
        if len(submodule_matches) != len(submodules):
            raise RuntimeError(
                f"{slug}: Method part {index + 1} has {len(submodule_matches)} submodule "
                f"headings for {len(submodules)} JSON submodules"
            )
        for submodule_index, (submodule, submodule_match) in enumerate(
            zip(submodules, submodule_matches)
        ):
            markdown_submodule = normalize_method_heading(submodule_match.group(1))
            expected_submodule = normalize_method_heading(str(submodule["name"]))
            if expected_submodule not in markdown_submodule:
                raise RuntimeError(
                    f"{slug}: Method part {index + 1} submodule {submodule_index + 1} "
                    "heading does not match its JSON name"
                )
            content_start = submodule_match.end()
            next_heading = re.search(
                r"^#{3,4}\s+", block[content_start:], flags=re.MULTILINE
            )
            content_end = (
                content_start + next_heading.start() if next_heading else len(block)
            )
            submodule_text = block[content_start:content_end]
            if compact_length(submodule_text) < SUBMODULE_MINIMUM:
                raise RuntimeError(
                    f"{slug}: Method part {index + 1} submodule {submodule_index + 1} "
                    f"has {compact_length(submodule_text)} non-space characters; "
                    f"requires {SUBMODULE_MINIMUM}"
                )
            if not re.search(
                r"例如|举例|为例|例子|具体|案例|figure|case",
                submodule_text,
                re.IGNORECASE,
            ):
                raise RuntimeError(
                    f"{slug}: Method part {index + 1} submodule {submodule_index + 1} "
                    "does not continue a concrete example"
                )
        if stage["equations"]:
            if "$" not in block or compact_length(block) < 800:
                raise RuntimeError(
                    f"{slug}: Method part {index + 1} must integrate and explain its equations"
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
        figure_path = day / str(figure["path"])
        if not figure_path.is_file():
            raise RuntimeError(f"{slug}: figure manifest points to a missing file: {figure['path']}")
        validate_original_image(figure_path, slug)

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

    category_map = categories_by_key(config)
    expected_top = int(config["digest"].get("top_recommendations", 3))
    top_papers = [paper for paper in summaries if paper.get("top_recommendation") is True]
    if len(top_papers) != expected_top:
        raise RuntimeError(
            f"expected {expected_top} Top recommendations, found {len(top_papers)}"
        )
    for paper in summaries:
        category = category_map[str(paper["channel"])]
        featured = category.get("featured")
        expected_special = bool(featured)
        if paper.get("special_recommendation") is not expected_special:
            raise RuntimeError(
                f"{paper['slug']}: special recommendation flag differs from category configuration"
            )
        expected_label = str(featured["label"]) if featured else ""
        expected_confidence = str(featured["confidence"]) if featured else "normal"
        expected_priority = int(featured["selection_priority"]) if featured else 0
        if paper.get("special_recommendation_label") != expected_label:
            raise RuntimeError(f"{paper['slug']}: special recommendation label is incorrect")
        if paper.get("recommendation_confidence") != expected_confidence:
            raise RuntimeError(f"{paper['slug']}: recommendation confidence is incorrect")
        if paper.get("recommendation_priority") != expected_priority:
            raise RuntimeError(f"{paper['slug']}: recommendation priority is incorrect")
        if (
            featured
            and featured.get("outside_top_recommendations")
            and paper.get("top_recommendation") is True
        ):
            raise RuntimeError(
                f"{paper['slug']}: featured recommendation must remain outside Top recommendations"
            )

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
        featured = category.get("featured")
        if featured and not re.search(
            rf"^##\s+{re.escape(str(featured['label']))}\s*$",
            digest_text,
            flags=re.MULTILINE,
        ):
            raise RuntimeError(
                f"digest.md is missing standalone featured section {featured['label']}"
            )
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
