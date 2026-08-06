#!/usr/bin/env python3
"""Create Zotero collections, import PDFs, and add portable deep links."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from common import (
    ConfigError,
    atomic_write_json,
    atomic_write_text,
    categories_by_key,
    day_directory,
    load_config,
    normalize_arxiv_id,
    parse_run_date,
    resolve_path,
)


CONNECTOR = "http://127.0.0.1:23119"
USER_AGENT = "daily-paper-digest/1.0"
KEY_CHARS = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"


def sqlite_readonly_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def request(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None, timeout: float = 60) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def connector_running() -> bool:
    try:
        status, _ = request(f"{CONNECTOR}/connector/ping", timeout=2)
        return status == 200
    except (OSError, urllib.error.URLError):
        return False


def zotero_process_running() -> bool:
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq zotero.exe", "/NH"],
                check=False,
                capture_output=True,
                text=True,
            )
            return "zotero.exe" in result.stdout.casefold()
        names = ("Zotero", "zotero") if sys.platform == "darwin" else ("zotero",)
        return any(
            subprocess.run(
                ["pgrep", "-x", name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
            for name in names
        )
    except OSError:
        return connector_running()


def detect_database(config: dict[str, Any], config_path: Path) -> Path:
    configured = str(config["zotero"].get("database", "")).strip()
    if configured:
        path = resolve_path(configured, config_path)
        if not path.is_file():
            raise RuntimeError(f"configured Zotero database not found: {path}")
        return path
    candidates = [Path.home() / "Zotero" / "zotero.sqlite"]
    if os.name == "nt":
        candidates.extend(
            [
                Path(os.environ.get("USERPROFILE", Path.home())) / "Zotero" / "zotero.sqlite",
                Path(os.environ.get("APPDATA", Path.home())) / "Zotero" / "zotero.sqlite",
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("Zotero database was not detected; set zotero.database explicitly")


def launch_zotero() -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-gja", "Zotero"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    if os.name == "nt":
        candidates = []
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
            if root:
                candidates.append(Path(root) / "Zotero" / "zotero.exe")
        executable = next((str(path) for path in candidates if path.is_file()), shutil.which("zotero"))
        if executable:
            subprocess.Popen([executable], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        raise RuntimeError("Zotero executable was not detected on Windows")
    executable = shutil.which("zotero")
    if not executable:
        raise RuntimeError("Zotero executable was not found in PATH")
    subprocess.Popen([executable], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_connector(launch: bool) -> None:
    if connector_running():
        return
    if launch:
        launch_zotero()
        for _ in range(45):
            time.sleep(1)
            if connector_running():
                return
    raise RuntimeError("Zotero connector is unavailable at 127.0.0.1:23119")


def snapshot_database(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise RuntimeError(f"Zotero database not found: {source}")
    for attempt in range(5):
        try:
            with sqlite3.connect(sqlite_readonly_uri(source), uri=True, timeout=5) as live:
                with sqlite3.connect(destination) as snapshot:
                    live.backup(snapshot)
            return
        except (PermissionError, sqlite3.Error):
            if attempt == 4:
                raise
            time.sleep(0.5)


def new_key(db: sqlite3.Connection, table: str = "collections") -> str:
    while True:
        key = "".join(random.SystemRandom().choice(KEY_CHARS) for _ in range(8))
        if not db.execute(f"SELECT 1 FROM {table} WHERE key = ?", (key,)).fetchone():
            return key


def get_library_id(db: sqlite3.Connection) -> int:
    try:
        row = db.execute("SELECT libraryID FROM libraries WHERE type = 'user' ORDER BY libraryID LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        row = None
    if not row:
        row = db.execute("SELECT MIN(libraryID) FROM items").fetchone()
    if not row or row[0] is None:
        raise RuntimeError("could not determine the Zotero user library ID")
    return int(row[0])


def get_or_create_collection(db: sqlite3.Connection, library_id: int, name: str, parent_id: int | None) -> tuple[int, str]:
    row = db.execute(
        "SELECT collectionID, key FROM collections WHERE libraryID = ? AND collectionName = ? AND parentCollectionID IS ?",
        (library_id, name, parent_id),
    ).fetchone()
    if row:
        return int(row[0]), str(row[1])
    key = new_key(db)
    cursor = db.execute(
        "INSERT INTO collections (collectionName, parentCollectionID, libraryID, key, version, synced) VALUES (?, ?, ?, ?, 0, 0)",
        (name, parent_id, library_id, key),
    )
    return int(cursor.lastrowid), key


def setup_collections(config: dict[str, Any], config_path: Path) -> None:
    if connector_running() or zotero_process_running():
        raise RuntimeError("close Zotero before creating collections so its database is not edited live")
    database = detect_database(config, config_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = database.with_name(f"zotero.sqlite.daily-paper-backup-{stamp}")
    shutil.copy2(database, backup)
    top_name = str(config["zotero"]["top_collection"])
    with sqlite3.connect(database, timeout=5) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        library_id = get_library_id(db)
        top_id, top_key = get_or_create_collection(db, library_id, top_name, None)
        children: dict[str, tuple[int, str]] = {}
        for category in categories_by_key(config).values():
            children[category["key"]] = get_or_create_collection(
                db, library_id, str(category["zotero_collection"]), top_id
            )
        db.commit()
    print(f"backup={backup}")
    print(f"top={top_name} id={top_id} key={top_key}")
    for key, (collection_id, collection_key) in children.items():
        print(f"{key}: id={collection_id} key={collection_key}")


def load_collection_ids(database: Path, config: dict[str, Any]) -> dict[str, int]:
    top_name = str(config["zotero"]["top_collection"])
    wanted = {key: str(category["zotero_collection"]) for key, category in categories_by_key(config).items()}
    with tempfile.TemporaryDirectory(prefix="daily-paper-zotero-") as temp:
        snapshot = Path(temp) / "zotero.sqlite"
        snapshot_database(database, snapshot)
        with sqlite3.connect(sqlite_readonly_uri(snapshot), uri=True) as db:
            top = db.execute(
                "SELECT collectionID FROM collections WHERE collectionName = ? AND parentCollectionID IS NULL",
                (top_name,),
            ).fetchone()
            if not top:
                raise RuntimeError(f"top-level Zotero collection not found: {top_name}; run setup-collections")
            rows = db.execute(
                "SELECT collectionName, collectionID FROM collections WHERE parentCollectionID = ?",
                (int(top[0]),),
            ).fetchall()
    by_name = {str(name): int(collection_id) for name, collection_id in rows}
    missing = [name for name in wanted.values() if name not in by_name]
    if missing:
        raise RuntimeError(f"missing Zotero child collections: {missing}; run setup-collections")
    return {key: by_name[name] for key, name in wanted.items()}


def normalize_zotero_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def find_item_and_pdf(database: Path, paper: dict[str, Any], collection_id: int) -> tuple[str, str, bool] | None:
    with tempfile.TemporaryDirectory(prefix="daily-paper-zotero-") as temp:
        snapshot = Path(temp) / "zotero.sqlite"
        snapshot_database(database, snapshot)
        with sqlite3.connect(sqlite_readonly_uri(snapshot), uri=True) as db:
            rows = db.execute(
                """
                SELECT DISTINCT i.itemID, i.key, f.fieldName, v.value
                FROM items i
                JOIN itemData d ON d.itemID = i.itemID
                JOIN fields f ON f.fieldID = d.fieldID
                JOIN itemDataValues v ON v.valueID = d.valueID
                LEFT JOIN itemAttachments a ON a.itemID = i.itemID
                LEFT JOIN deletedItems deleted ON deleted.itemID = i.itemID
                WHERE a.itemID IS NULL AND deleted.itemID IS NULL
                  AND f.fieldName IN ('title', 'url', 'DOI', 'extra')
                """
            ).fetchall()
            grouped: dict[tuple[int, str], list[tuple[str, str]]] = {}
            for item_id, key, field, value in rows:
                grouped.setdefault((int(item_id), str(key)), []).append((str(field), str(value or "")))
            wanted_title = normalize_zotero_title(str(paper["title"]))
            identifier = normalize_arxiv_id(str(paper["arxiv_id"]))
            for (item_id, key), fields in sorted(grouped.items(), reverse=True):
                if not any(
                    (field == "title" and normalize_zotero_title(value) == wanted_title) or identifier in value
                    for field, value in fields
                ):
                    continue
                attachment = db.execute(
                    """
                    SELECT i.key FROM itemAttachments a
                    JOIN items i ON i.itemID = a.itemID
                    LEFT JOIN deletedItems deleted ON deleted.itemID = i.itemID
                    WHERE a.parentItemID = ? AND deleted.itemID IS NULL
                      AND (a.contentType = 'application/pdf' OR lower(a.path) LIKE '%.pdf')
                    ORDER BY i.itemID LIMIT 1
                    """,
                    (item_id,),
                ).fetchone()
                in_collection = db.execute(
                    "SELECT 1 FROM collectionItems WHERE collectionID = ? AND itemID = ?",
                    (collection_id, item_id),
                ).fetchone() is not None
                return key, str(attachment[0]) if attachment else "", in_collection
    return None


def creator(author: str) -> dict[str, str]:
    if "," in author:
        last, first = (part.strip() for part in author.split(",", 1))
        return {"firstName": first, "lastName": last, "creatorType": "author"}
    return {"name": author.strip(), "creatorType": "author"}


def import_paper(day: Path, paper: dict[str, Any], collection_id: int) -> tuple[str, str, str]:
    session_id = uuid.uuid4().hex
    connector_id = "daily-paper-" + normalize_arxiv_id(str(paper["arxiv_id"])).replace(".", "")
    item = {
        "id": connector_id,
        "itemType": "journalArticle",
        "title": paper["title"],
        "creators": [creator(str(author)) for author in paper.get("authors", [])],
        "date": str(paper.get("published", ""))[:10],
        "url": paper["paper_url"],
        "archive": "arXiv",
        "archiveLocation": f"arXiv:{normalize_arxiv_id(str(paper['arxiv_id']))}",
        "attachments": [],
        "tags": [{"tag": "daily-paper-digest"}],
    }
    payload = json.dumps({"sessionID": session_id, "uri": paper["paper_url"], "items": [item]}, ensure_ascii=False).encode()
    status, body = request(
        f"{CONNECTOR}/connector/saveItems",
        data=payload,
        headers={"Content-Type": "application/json", "X-Zotero-Connector-API-Version": "3"},
    )
    if status != 201:
        raise RuntimeError(f"Zotero saveItems failed ({status}): {body[:300]!r}")
    pdf_path = day / str(paper.get("source_files", {}).get("pdf", ""))
    if not pdf_path.is_file():
        raise RuntimeError(f"prepared PDF is missing: {pdf_path}")
    pdf = pdf_path.read_bytes()
    metadata = json.dumps(
        {
            "sessionID": session_id,
            "parentItemID": connector_id,
            "title": "Full Text PDF",
            "url": paper.get("pdf_url") or f"https://arxiv.org/pdf/{paper['arxiv_id']}",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    status, body = request(
        f"{CONNECTOR}/connector/saveAttachment",
        data=pdf,
        headers={"Content-Type": "application/pdf", "Content-Length": str(len(pdf)), "X-Metadata": metadata},
        timeout=120,
    )
    if status != 201:
        raise RuntimeError(f"Zotero saveAttachment failed ({status}): {body[:300]!r}")
    status, body = request(
        f"{CONNECTOR}/connector/updateSession",
        data=json.dumps({"sessionID": session_id, "target": f"C{collection_id}", "tags": ["daily-paper-digest"], "note": ""}).encode(),
        headers={"Content-Type": "application/json"},
    )
    if status != 200:
        raise RuntimeError(f"Zotero collection assignment failed ({status}): {body[:300]!r}")
    return session_id, connector_id, str(pdf_path)


def wait_for_import(database: Path, paper: dict[str, Any], collection_id: int) -> tuple[str, str]:
    for _ in range(30):
        time.sleep(0.5)
        found = find_item_and_pdf(database, paper, collection_id)
        if found and found[1] and found[2]:
            return found[0], found[1]
    raise RuntimeError(f"Zotero imported the paper but its categorized PDF was not found: {paper['title']}")


def add_markdown_link(path: Path, uri: str) -> None:
    text = path.read_text(encoding="utf-8")
    line = f"- **Zotero**：[在 Zotero 中打开原论文]({uri})"
    if re.search(r"^- \*\*Zotero\*\*：.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^- \*\*Zotero\*\*：.*$", line, text, flags=re.MULTILINE)
    else:
        paper_line = re.search(r"^- \*\*(?:论文|原论文|论文链接)\*\*：.*$", text, flags=re.MULTILINE)
        if paper_line:
            text = text[: paper_line.end()] + "\n" + line + text[paper_line.end() :]
        else:
            first_break = text.find("\n")
            text = text[: first_break + 1] + "\n" + line + "\n" + text[first_break + 1 :]
    atomic_write_text(path, text)


def link_digest(config: dict[str, Any], config_path: Path, run_date) -> None:
    database = detect_database(config, config_path)
    ensure_connector(bool(config["zotero"].get("launch", True)))
    collection_ids = load_collection_ids(database, config)
    day = day_directory(config, config_path, run_date)
    digest_path = day / "digest.json"
    digest = json.loads(digest_path.read_text(encoding="utf-8"))
    linked: dict[str, str] = {}
    failures: list[str] = []
    updated_papers: list[dict[str, Any]] = []
    for summary in digest["papers"]:
        paper_path = day / f"{summary['slug']}.json"
        paper = json.loads(paper_path.read_text(encoding="utf-8"))
        collection_id = collection_ids[paper["channel"]]
        try:
            found = find_item_and_pdf(database, paper, collection_id)
            if found:
                item_key, attachment_key, in_collection = found
                if not attachment_key:
                    raise RuntimeError(f"existing Zotero item {item_key} has no PDF")
                if not in_collection:
                    raise RuntimeError(
                        f"existing Zotero item {item_key} is not in the configured category collection; move it or remove the duplicate"
                    )
            else:
                import_paper(day, paper, collection_id)
                item_key, attachment_key = wait_for_import(database, paper, collection_id)
            uri = f"zotero://open-pdf/library/items/{attachment_key}"
            paper["zotero_uri"] = uri
            paper["zotero_item_key"] = item_key
            paper["zotero_attachment_key"] = attachment_key
            atomic_write_json(paper_path, paper)
            add_markdown_link(day / f"{paper['slug']}.md", uri)
            linked[paper["slug"]] = uri
            updated_papers.append(paper)
            print(f"linked {paper['arxiv_id']} -> {uri}")
        except Exception as exc:
            failures.append(f"{paper.get('arxiv_id')}: {exc}")
            updated_papers.append(paper)
    digest["papers"] = updated_papers
    atomic_write_json(digest_path, digest)
    digest_markdown = day / "digest.md"
    text = digest_markdown.read_text(encoding="utf-8")
    for slug, uri in linked.items():
        pattern = re.compile(rf"(\[[^\]]+\]\({re.escape(slug)}\.md\))(?!\s*·\s*\[Zotero\])")
        text = pattern.sub(rf"\1 · [Zotero]({uri})", text)
    atomic_write_text(digest_markdown, text)
    if failures:
        raise RuntimeError("Zotero linking failed:\n- " + "\n- ".join(failures))
    print(f"zotero_links={len(linked)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup-collections", "link"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path)
        if name == "link":
            command.add_argument("--date")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config, config_path = load_config(args.config)
        if args.command == "setup-collections":
            setup_collections(config, config_path)
        else:
            link_digest(config, config_path, parse_run_date(args.date))
        return 0
    except (ConfigError, RuntimeError, OSError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"Zotero operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
