#!/usr/bin/env python3
"""Search, deduplicate, select, and download paper-owned source material."""

from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any

from common import (
    ConfigError,
    allocate_quotas,
    archive_root,
    atomic_write_json,
    atomic_write_text,
    categories_by_key,
    day_directory,
    load_config,
    load_known_papers,
    normalize_arxiv_id,
    normalize_title,
    parse_run_date,
    slugify,
)


USER_AGENT = "daily-paper-digest/1.0 (+https://github.com/zlynpu/codex_search_paper_skill)"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
ARXIV_ID_RE = re.compile(r"(?:abs/|pdf/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def fetch_bytes(url: str, *, timeout: float = 60, attempts: int = 3) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                try:
                    body = response.read()
                except http.client.IncompleteRead as exc:
                    body = exc.partial
                return body, response.headers.get_content_type()
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def collapse(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def parse_atom(body: bytes, source_category: str) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    records: list[dict[str, Any]] = []
    for entry in root.findall("a:entry", ATOM):
        identifier = normalize_arxiv_id(entry.findtext("a:id", default="", namespaces=ATOM))
        if not ARXIV_ID_RE.fullmatch(identifier):
            continue
        links = {link.attrib.get("rel", ""): link.attrib.get("href", "") for link in entry.findall("a:link", ATOM)}
        records.append(
            {
                "arxiv_id": identifier,
                "title": collapse(entry.findtext("a:title", default="", namespaces=ATOM)),
                "abstract": collapse(entry.findtext("a:summary", default="", namespaces=ATOM)),
                "authors": [collapse(node.findtext("a:name", default="", namespaces=ATOM)) for node in entry.findall("a:author", ATOM)],
                "published": entry.findtext("a:published", default="", namespaces=ATOM),
                "updated": entry.findtext("a:updated", default="", namespaces=ATOM),
                "paper_url": links.get("alternate") or f"https://arxiv.org/abs/{identifier}",
                "pdf_url": links.get("related") or f"https://arxiv.org/pdf/{identifier}",
                "source_categories": [source_category],
            }
        )
    return records


def parse_rss(body: bytes, source_category: str) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    records: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        link = collapse(item.findtext("link", default=""))
        identifier = normalize_arxiv_id(link)
        if not ARXIV_ID_RE.fullmatch(identifier):
            continue
        description = item.findtext("description", default="")
        description = re.sub(r"<[^>]+>", " ", description)
        records.append(
            {
                "arxiv_id": identifier,
                "title": collapse(item.findtext("title", default="")),
                "abstract": collapse(description),
                "authors": [],
                "published": collapse(item.findtext("pubDate", default="")),
                "updated": "",
                "paper_url": link or f"https://arxiv.org/abs/{identifier}",
                "pdf_url": f"https://arxiv.org/pdf/{identifier}",
                "source_categories": [source_category],
            }
        )
    return records


def parse_recent_html(body: bytes, source_category: str) -> list[dict[str, Any]]:
    page = body.decode("utf-8", errors="replace")
    records: list[dict[str, Any]] = []
    for definition, details in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", page, re.I | re.S):
        identifier_match = re.search(r"href\s*=\s*['\"]/abs/(\d{4}\.\d{4,5})(?:v\d+)?['\"]", definition, re.I)
        title_match = re.search(
            r"<div\b[^>]*class=['\"][^'\"]*list-title[^'\"]*['\"][^>]*>.*?"
            r"<span\b[^>]*class=['\"][^'\"]*descriptor[^'\"]*['\"][^>]*>Title:</span>(.*?)</div>",
            details,
            re.I | re.S,
        )
        if not identifier_match or not title_match:
            continue
        identifier = normalize_arxiv_id(identifier_match.group(1))
        author_block = re.search(r"<div\b[^>]*class=['\"][^'\"]*list-authors[^'\"]*['\"][^>]*>(.*?)</div>", details, re.I | re.S)
        authors = []
        if author_block:
            authors = [collapse(re.sub(r"<[^>]+>", " ", value)) for value in re.findall(r"<a\b[^>]*>(.*?)</a>", author_block.group(1), re.I | re.S)]
        records.append(
            {
                "arxiv_id": identifier,
                "title": collapse(re.sub(r"<[^>]+>", " ", title_match.group(1))),
                "abstract": "",
                "authors": [author for author in authors if author],
                "published": "",
                "updated": "",
                "paper_url": f"https://arxiv.org/abs/{identifier}",
                "pdf_url": f"https://arxiv.org/pdf/{identifier}",
                "source_categories": [source_category],
            }
        )
    return records


def merge_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        identifier = normalize_arxiv_id(str(record.get("arxiv_id", "")))
        if not identifier:
            continue
        if identifier not in merged:
            merged[identifier] = record
            continue
        current = merged[identifier]
        current["source_categories"] = sorted(
            set(current.get("source_categories", [])) | set(record.get("source_categories", []))
        )
        for field in ("title", "abstract", "authors", "published", "updated", "paper_url", "pdf_url"):
            if not current.get(field) and record.get(field):
                current[field] = record[field]
    return list(merged.values())


def discover(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    max_results = int(config["selection"]["max_results_per_source"])
    source_categories = sorted(
        {
            source
            for category in categories_by_key(config).values()
            for source in category["arxiv_categories"]
        }
    )
    all_records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for index, source in enumerate(source_categories):
        query = urllib.parse.urlencode(
            {
                "search_query": f"cat:{source}",
                "start": 0,
                "max_results": max_results,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
        )
        api_url = f"https://export.arxiv.org/api/query?{query}"
        try:
            body, _ = fetch_bytes(api_url)
            parsed = parse_atom(body, source)
            if not parsed:
                raise RuntimeError("arXiv API returned no entries")
            all_records.extend(parsed)
        except Exception as api_error:
            rss_url = f"https://rss.arxiv.org/rss/{source}"
            fallback_records: list[dict[str, Any]] = []
            fallback_errors: list[str] = []
            try:
                body, _ = fetch_bytes(rss_url)
                parsed = parse_rss(body, source)
                if not parsed:
                    raise RuntimeError("arXiv RSS returned no entries")
                fallback_records.extend(parsed)
            except Exception as rss_error:
                fallback_errors.append(f"RSS: {rss_error}")
            listing_url = f"https://arxiv.org/list/{source}/recent?skip=0&show={max_results}"
            try:
                body, _ = fetch_bytes(listing_url)
                parsed = parse_recent_html(body, source)
                if not parsed:
                    raise RuntimeError("arXiv recent HTML returned no entries")
                fallback_records.extend(parsed)
            except Exception as listing_error:
                fallback_errors.append(f"HTML: {listing_error}")
            if fallback_records:
                all_records.extend(fallback_records)
                errors.append(
                    {
                        "source": api_url,
                        "error": str(api_error),
                        "fallback": f"{rss_url}; {listing_url}",
                    }
                )
            else:
                errors.append(
                    {
                        "source": source,
                        "error": f"API: {api_error}; " + "; ".join(fallback_errors),
                    }
                )
        if index + 1 < len(source_categories):
            time.sleep(3)
    return merge_records(all_records), errors


def contains_term(text: str, term: str) -> bool:
    text = text.casefold()
    term = term.casefold().strip()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9+-]{1,4}", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
    return term in text


def score_for_category(record: dict[str, Any], category: dict[str, Any], global_negative: list[str]) -> float:
    title = str(record.get("title", ""))
    abstract = str(record.get("abstract", ""))
    combined = f"{title} {abstract}"
    negatives = [*global_negative, *category.get("negative_terms", [])]
    if any(contains_term(combined, term) for term in negatives):
        return -1.0
    score = 0.0
    for term in category["search_terms"]:
        if contains_term(title, term):
            score += 8.0
        elif contains_term(abstract, term):
            score += 2.0
    source_overlap = set(record.get("source_categories", [])) & set(category["arxiv_categories"])
    score += 0.25 * len(source_overlap)
    published = parse_datetime(str(record.get("published", "")))
    if published:
        age = max(0.0, (datetime.now(timezone.utc) - published).total_seconds() / 86400)
        score += max(0.0, 2.0 - age / 7.0)
    return score


def meta_values(page: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        attrs = {
            name.casefold(): html.unescape(value)
            for name, _, value in re.findall(r"([:\w-]+)\s*=\s*(['\"])(.*?)\2", tag, re.I | re.S)
        }
        name = attrs.get("name") or attrs.get("property")
        content = attrs.get("content")
        if name and content is not None:
            values.setdefault(name.casefold(), []).append(collapse(content))
    return values


def text_from_html(page: str) -> str:
    page = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", page, flags=re.I | re.S)
    page = re.sub(r"</(?:p|div|section|h[1-6]|li|tr|figure)>", "\n", page, flags=re.I)
    page = re.sub(r"<[^>]+>", " ", page)
    lines = [collapse(line) for line in html.unescape(page).splitlines()]
    return "\n".join(line for line in lines if line)


def figure_blocks(page: str, base_url: str) -> list[dict[str, str]]:
    blocks = re.findall(r"<figure\b[^>]*>(.*?)</figure>", page, flags=re.I | re.S)
    figures: list[dict[str, str]] = []
    for block in blocks:
        image = re.search(r"<img\b[^>]*\bsrc\s*=\s*(['\"])(.*?)\1", block, re.I | re.S)
        if not image:
            continue
        source = html.unescape(image.group(2)).strip()
        if not source or source.startswith("data:") or "/static/" in source:
            continue
        caption_match = re.search(r"<figcaption\b[^>]*>(.*?)</figcaption>", block, re.I | re.S)
        caption = text_from_html(caption_match.group(1)) if caption_match else ""
        figures.append({"source_url": urllib.parse.urljoin(base_url, source), "caption": collapse(caption)})
    return figures


def extension_for(body: bytes, content_type: str, url: str) -> str:
    lowered = content_type.casefold()
    if "svg" in lowered or body.lstrip().startswith(b"<svg"):
        return ".svg"
    if body.startswith(b"\x89PNG"):
        return ".png"
    if body.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if body.startswith(b"RIFF") and b"WEBP" in body[:16]:
        return ".webp"
    suffix = Path(urllib.parse.urlparse(url).path).suffix.casefold()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"} else ".bin"


def enrich_metadata(record: dict[str, Any]) -> tuple[dict[str, Any], str]:
    identifier = normalize_arxiv_id(str(record["arxiv_id"]))
    body, _ = fetch_bytes(f"https://arxiv.org/abs/{identifier}")
    page = body.decode("utf-8", errors="replace")
    meta = meta_values(page)
    enriched = dict(record)
    enriched.update(
        {
            "arxiv_id": identifier,
            "title": (meta.get("citation_title") or [record.get("title", "")])[0],
            "authors": meta.get("citation_author") or record.get("authors", []),
            "published": (meta.get("citation_date") or [record.get("published", "")])[0],
            "abstract": (meta.get("citation_abstract") or [record.get("abstract", "")])[0],
            "paper_url": f"https://arxiv.org/abs/{identifier}",
            "pdf_url": (meta.get("citation_pdf_url") or [f"https://arxiv.org/pdf/{identifier}"])[0],
        }
    )
    return enriched, page


def materialize(
    record: dict[str, Any],
    category_key: str,
    score: float,
    workspace: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    enriched, abstract_page = enrich_metadata(record)
    identifier = enriched["arxiv_id"]
    slug = slugify(str(enriched["title"]), identifier)
    if (workspace / "sources" / slug).exists() or (workspace / "images" / slug).exists():
        slug = f"{slug}-{identifier.replace('.', '-')}"
    source_dir = workspace / "sources" / slug
    image_dir = workspace / "images" / slug
    source_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(source_dir / "abstract.html", abstract_page)

    html_page = ""
    html_url = ""
    html_errors: list[str] = []
    for url in (
        f"https://arxiv.org/html/{identifier}v1",
        f"https://arxiv.org/html/{identifier}",
        f"https://ar5iv.labs.arxiv.org/html/{identifier}",
    ):
        try:
            body, content_type = fetch_bytes(url, timeout=90, attempts=2)
            candidate = body.decode("utf-8", errors="replace")
            if "<html" not in candidate[:2000].casefold() and "html" not in content_type:
                raise RuntimeError(f"unexpected content type {content_type}")
            html_page, html_url = candidate, url
            break
        except Exception as exc:
            html_errors.append(f"{url}: {exc}")
    if html_page:
        atomic_write_text(source_dir / "paper.html", html_page)
        atomic_write_text(source_dir / "full-text.txt", text_from_html(html_page))

    pdf_error = ""
    try:
        pdf, _ = fetch_bytes(str(enriched["pdf_url"]), timeout=120, attempts=2)
        if not pdf.startswith(b"%PDF"):
            raise RuntimeError("download is not a PDF")
        (source_dir / "paper.pdf").write_bytes(pdf)
    except Exception as exc:
        pdf_error = str(exc)
    if pdf_error and config["zotero"].get("enabled"):
        raise RuntimeError(f"PDF is required for enabled Zotero integration: {pdf_error}")

    maximum = int(config["selection"]["maximum_original_figures"])
    figures: list[dict[str, str]] = []
    figure_errors: list[str] = []
    for candidate in figure_blocks(html_page, html_url) if html_page else []:
        if len(figures) >= maximum:
            break
        try:
            body, content_type = fetch_bytes(candidate["source_url"], timeout=45, attempts=2)
            if len(body) < 256:
                raise RuntimeError("image payload is too small")
            extension = extension_for(body, content_type, candidate["source_url"])
            if extension == ".bin":
                raise RuntimeError(f"unrecognized image content type {content_type}")
            filename = f"figure-{len(figures) + 1:02d}{extension}"
            (image_dir / filename).write_bytes(body)
            figures.append(
                {
                    "path": f"images/{slug}/{filename}",
                    "caption": candidate["caption"],
                    "source_url": candidate["source_url"],
                    "source_kind": "original-paper-figure",
                }
            )
        except Exception as exc:
            figure_errors.append(f"{candidate['source_url']}: {exc}")

    minimum = int(config["selection"]["minimum_original_figures"])
    if len(figures) < minimum:
        raise RuntimeError(
            f"only {len(figures)} original figures, configured minimum is {minimum}; "
            + ("; ".join(html_errors[-2:] + figure_errors[-2:]))
        )
    enriched.update(
        {
            "channel": category_key,
            "slug": slug,
            "selection_score": round(score, 3),
            "figures": figures,
            "source_files": {
                "abstract_html": f"sources/{slug}/abstract.html",
                "paper_html": f"sources/{slug}/paper.html" if html_page else "",
                "full_text": f"sources/{slug}/full-text.txt" if html_page else "",
                "pdf": f"sources/{slug}/paper.pdf" if not pdf_error else "",
            },
            "source_warnings": {"html": html_errors, "figures": figure_errors, "pdf": pdf_error},
            "status": "prepared",
            "method_stages": [],
        }
    )
    return enriched


def config_fingerprint(config: dict[str, Any]) -> str:
    selected = {
        "digest": config["digest"],
        "selection": config["selection"],
        "zotero": config["zotero"],
    }
    encoded = json.dumps(selected, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def job_markdown(config_path: Path, run_date: date, papers: list[dict[str, Any]], quotas: dict[str, int]) -> str:
    slugs = "\n".join(f"- `{paper['slug']}` — {paper['title']}" for paper in papers)
    return f"""# Daily paper job: {run_date.isoformat()}

Use the installed `$daily-paper-digest` skill and obey its note contract.

- Config: `{config_path}`
- Date: `{run_date.isoformat()}`
- Papers: {len(papers)}
- Exact allocation: `{json.dumps(quotas, ensure_ascii=False)}`

Prepared papers:

{slugs}

Read every `<slug>.json` plus its `source_files`. Write `<slug>.md`, update the JSON analytical fields including `method_stages`, and write `digest.md`.

Every paper note must follow the exact STAR headings in `references/note-contract.md`:

- Situation: research/application setting, concrete failure mode, consequence, and why prior approaches are insufficient.
- Task: exact input/output, objective, constraints/assumptions, evaluation target, and scope boundary.
- Action: first recover the paper's own Method outline. If Method has N explicit first-level subsections or named components, write exactly N `### 方法部分 N：中文标题（Original Method Heading）` sections in the same order; a one-part Method gets one section. Never split or merge parts to hit a fixed count. For every part write **翻译**, **解释**, and **公式与直觉**. Preserve every necessary formula, define its variables, calculation order, optimization/inference role, and intuitive meaning; if no key formula exists, explicitly say so without inventing one. Add exactly one corresponding `method_stages` object using `name`, `source_heading`, `translation`, `explanation`, `evidence`, `equations`, and `equation_note`.
- Result: metrics with dataset/environment, direction, baseline, and condition; decisive ablations or qualitative evidence; what the evidence proves and does not prove.

Use only each paper's listed `figures`, keeping paths under `images/<slug>/`. Place 2–4 necessary original figures immediately after the relevant STAR explanation, with specific Chinese captions; at least one available figure must appear inside Action. Do not update the history index and do not invent evidence. Finish only after all files are complete; the deterministic verifier runs next.
"""


def prepare(config: dict[str, Any], config_path: Path, run_date: date, refresh: bool = False) -> Path:
    root = archive_root(config, config_path)
    day = day_directory(config, config_path, run_date)
    fingerprint = config_fingerprint(config)
    if (day / "digest.json").is_file() and not refresh:
        existing = json.loads((day / "digest.json").read_text(encoding="utf-8"))
        if existing.get("config_fingerprint") == fingerprint:
            print(f"reuse={day}")
            return day
        raise RuntimeError(f"digest already exists with different configuration: {day}; use --refresh")
    if day.exists() and refresh:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = day.with_name(f"{day.name}.backup-{stamp}")
        day.rename(backup)
        print(f"backup={backup}")
    elif day.exists() and any(day.iterdir()):
        raise RuntimeError(f"non-empty day directory has no reusable digest: {day}; use --refresh")

    quotas = allocate_quotas(config)
    categories = categories_by_key(config)
    known = load_known_papers(config, config_path)
    records, source_errors = discover(config)
    cutoff = datetime.combine(run_date - timedelta(days=int(config["selection"]["lookback_days"])), day_time.min, tzinfo=timezone.utc)
    fresh: list[dict[str, Any]] = []
    for record in records:
        identifier = normalize_arxiv_id(str(record.get("arxiv_id", "")))
        title_key = normalize_title(str(record.get("title", "")))
        published = parse_datetime(str(record.get("published", "")))
        if identifier in known.ids or (title_key and title_key in known.titles):
            continue
        if published and published < cutoff:
            continue
        fresh.append(record)

    ranked: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    global_negative = list(config["selection"].get("global_negative_terms", []))
    for key, category in categories.items():
        scored = [
            (score_for_category(record, category, global_negative), record)
            for record in fresh
        ]
        ranked[key] = sorted(
            ((score, record) for score, record in scored if score > 0),
            key=lambda item: (item[0], item[1].get("published", ""), item[1]["arxiv_id"]),
            reverse=True,
        )

    day.parent.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    failed_ids: set[str] = set()
    failures: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix=f".{run_date.isoformat()}-", dir=day.parent) as temporary:
        build = Path(temporary) / "day"
        build.mkdir()
        for key, quota in quotas.items():
            if quota == 0:
                continue
            filled = 0
            for score, record in ranked[key]:
                identifier = normalize_arxiv_id(str(record["arxiv_id"]))
                if identifier in selected_ids or identifier in failed_ids:
                    continue
                try:
                    paper = materialize(record, key, score, build, config)
                except Exception as exc:
                    failed_ids.add(identifier)
                    failures.append({"arxiv_id": identifier, "channel": key, "error": str(exc)})
                    continue
                if normalize_title(paper["title"]) in {normalize_title(item["title"]) for item in selected}:
                    failures.append({"arxiv_id": identifier, "channel": key, "error": "duplicate normalized title in current run"})
                    continue
                selected.append(paper)
                selected_ids.add(identifier)
                filled += 1
                print(f"selected {key} {identifier} {paper['title']}", flush=True)
                if filled == quota:
                    break
            if filled != quota and not config["selection"].get("allow_quota_rebalance"):
                report = {
                    "date": run_date.isoformat(),
                    "required_quotas": quotas,
                    "selected_counts": {candidate: sum(p["channel"] == candidate for p in selected) for candidate in quotas},
                    "source_errors": source_errors,
                    "candidate_counts": {candidate: len(items) for candidate, items in ranked.items()},
                    "failures": failures,
                }
                log_dir = root / ".daily-paper-digest" / "logs"
                atomic_write_json(log_dir / f"candidate-report-{run_date.isoformat()}.json", report)
                raise RuntimeError(f"category {key} requires {quota} papers but only {filled} passed; see {log_dir}")

        if len(selected) < int(config["digest"]["total_papers"]) and config["selection"].get("allow_quota_rebalance"):
            pooled = sorted(
                (
                    (score, key, record)
                    for key, candidates in ranked.items()
                    for score, record in candidates
                ),
                key=lambda item: (item[0], item[2].get("published", ""), item[2]["arxiv_id"]),
                reverse=True,
            )
            for score, key, record in pooled:
                identifier = normalize_arxiv_id(str(record["arxiv_id"]))
                if identifier in selected_ids or identifier in failed_ids:
                    continue
                try:
                    paper = materialize(record, key, score, build, config)
                except Exception as exc:
                    failed_ids.add(identifier)
                    failures.append({"arxiv_id": identifier, "channel": key, "error": str(exc)})
                    continue
                title_key = normalize_title(paper["title"])
                if title_key in {normalize_title(item["title"]) for item in selected}:
                    failures.append({"arxiv_id": identifier, "channel": key, "error": "duplicate normalized title in current run"})
                    continue
                selected.append(paper)
                selected_ids.add(identifier)
                print(f"rebalanced {key} {identifier} {paper['title']}", flush=True)
                if len(selected) == int(config["digest"]["total_papers"]):
                    break

        if len(selected) != int(config["digest"]["total_papers"]):
            report = {
                "date": run_date.isoformat(),
                "required_quotas": quotas,
                "selected_counts": {candidate: sum(p["channel"] == candidate for p in selected) for candidate in quotas},
                "source_errors": source_errors,
                "candidate_counts": {candidate: len(items) for candidate, items in ranked.items()},
                "failures": failures,
            }
            log_dir = root / ".daily-paper-digest" / "logs"
            atomic_write_json(log_dir / f"candidate-report-{run_date.isoformat()}.json", report)
            raise RuntimeError(
                f"required {config['digest']['total_papers']} papers but only {len(selected)} passed; see {log_dir}"
            )

        top_count = min(int(config["digest"].get("top_recommendations", 3)), len(selected))
        top_ids = {
            paper["arxiv_id"]
            for paper in sorted(selected, key=lambda item: item["selection_score"], reverse=True)[:top_count]
        }
        for paper in selected:
            paper["top_recommendation"] = paper["arxiv_id"] in top_ids
            atomic_write_json(build / f"{paper['slug']}.json", paper)
        payload = {
            "schema_version": 1,
            "date": run_date.isoformat(),
            "search_window": {
                "from": (run_date - timedelta(days=int(config["selection"]["lookback_days"]))).isoformat(),
                "to": run_date.isoformat(),
            },
            "config_fingerprint": fingerprint,
            "config_path": str(config_path),
            "status": "prepared",
            "quotas": quotas,
            "counts": {"papers": len(selected), "original_images": sum(len(p["figures"]) for p in selected)},
            "source_errors": source_errors,
            "selection_failures": failures,
            "papers": selected,
        }
        atomic_write_json(build / "digest.json", payload)
        atomic_write_text(build / "JOB.md", job_markdown(config_path, run_date, selected, quotas))
        build.rename(day)
    print(f"prepared={day}")
    return day


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--date")
    parser.add_argument("--refresh", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config, config_path = load_config(args.config)
        prepare(config, config_path, parse_run_date(args.date), args.refresh)
        return 0
    except (ConfigError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"prepare failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
