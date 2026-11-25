"""Download monthly "spending over £25,000" CSV files from GOV.UK.

GOV.UK exposes every page through its Content API, so we can walk a
department's collection page, find the monthly publications and download
their CSV attachments without scraping HTML.
"""

from __future__ import annotations

import csv
import hashlib
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import RAW_SPEND_DIR, Department, Settings

CONTENT_API = "https://www.gov.uk/api/content"
MANIFEST = RAW_SPEND_DIR / "manifest.csv"

MONTH_NAMES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
TITLE_MONTH = re.compile(r"\b(" + "|".join(MONTH_NAMES) + r")\s+(\d{4})\b", re.IGNORECASE)

_session = requests.Session()
_session.headers["User-Agent"] = "spendsentinel/0.1 (open-data analysis)"


@dataclass
class FetchedFile:
    dept_code: str
    year: int
    month: int
    title: str
    publication_url: str
    file_url: str
    local_path: str
    bytes: int
    sha256: str
    status: str
    fetched_at: str


def _get_json(path: str) -> dict:
    response = _session.get(f"{CONTENT_API}{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def monthly_publications(dept: Department) -> list[tuple[int, int, str, str]]:
    """Return (year, month, title, base_path) for each monthly publication, newest first."""
    collection = _get_json(f"/government/collections/{dept.collection}")
    found: dict[tuple[int, int], tuple[str, str]] = {}
    for doc in collection.get("links", {}).get("documents", []):
        match = TITLE_MONTH.search(doc.get("title", ""))
        if not match:
            continue
        key = (int(match.group(2)), MONTH_NAMES.index(match.group(1).lower()) + 1)
        found.setdefault(key, (doc["title"], doc["base_path"]))
    ordered = sorted(found.items(), reverse=True)
    return [(y, m, title, path) for (y, m), (title, path) in ordered]


def csv_attachments(base_path: str) -> list[str]:
    publication = _get_json(base_path)
    urls = []
    for attachment in publication.get("details", {}).get("attachments", []):
        url = attachment.get("url", "")
        content_type = attachment.get("content_type", "")
        if content_type.startswith("text/csv") or url.lower().endswith(".csv"):
            urls.append(url)
    return urls


def _download(url: str, target: Path) -> tuple[int, str]:
    response = _session.get(url, timeout=120)
    response.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return len(response.content), hashlib.sha256(response.content).hexdigest()


def fetch_department(dept: Department, months: int) -> list[FetchedFile]:
    rows: list[FetchedFile] = []
    publications = monthly_publications(dept)[:months]
    for year, month, title, base_path in publications:
        publication_url = f"https://www.gov.uk{base_path}"
        urls = csv_attachments(base_path)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not urls:
            rows.append(FetchedFile(dept.code, year, month, title, publication_url, "", "", 0, "",
                                    "skipped: no CSV attachment", now))
            continue
        for index, url in enumerate(urls, start=1):
            suffix = "" if len(urls) == 1 else f"_{index}"
            target = RAW_SPEND_DIR / dept.code / f"{year}-{month:02d}{suffix}.csv"
            if target.exists():
                data = target.read_bytes()
                size, digest, status = len(data), hashlib.sha256(data).hexdigest(), "cached"
            else:
                size, digest = _download(url, target)
                status = "downloaded"
                time.sleep(0.5)  # be polite to GOV.UK
            rows.append(FetchedFile(dept.code, year, month, title, publication_url, url,
                                    target.relative_to(RAW_SPEND_DIR).as_posix(), size, digest,
                                    status, now))
    return rows


def write_manifest(rows: list[FetchedFile]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FetchedFile.__dataclass_fields__))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def run(settings: Settings) -> list[FetchedFile]:
    all_rows: list[FetchedFile] = []
    for dept in settings.departments:
        rows = fetch_department(dept, settings.months_per_department)
        got = sum(1 for r in rows if r.local_path)
        print(f"{dept.code}: {got} files ({rows[-1].year}-{rows[-1].month:02d} to "
              f"{rows[0].year}-{rows[0].month:02d})" if rows else f"{dept.code}: nothing found")
        all_rows.extend(rows)
    write_manifest(all_rows)
    return all_rows
