from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

from feed_app.db import get_user_source, list_user_sources, update_source_validation
from feed_app.defaults import DEFAULT_DB_PATH, DEFAULT_USER_ID
from feed_app.ingest import fetch_feed, find_feed_entries, first_image_url, parse_feed
from feed_app.models import FeedSource, utc_now_iso


@dataclass(frozen=True)
class ValidationResult:
    source_id: str
    status: str
    last_checked_at: str
    validation_error: str | None = None
    detected_feed_type: str | None = None
    example_item_count: int = 0
    has_images: bool = False
    has_descriptions: bool = False
    has_dates: bool = False
    has_titles: bool = False
    has_urls: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_source(source: FeedSource, *, timeout_seconds: float = 12) -> ValidationResult:
    checked_at = utc_now_iso()
    if source.source_type == "unsupported_v1" or source.status == "unsupported_v1":
        return ValidationResult(
            source_id=source.id,
            status="unsupported_v1",
            last_checked_at=checked_at,
            validation_error="Source needs non-RSS support; unsupported in v1.",
        )
    if source.source_type in {"api", "manual"} and not source.feed_url:
        return ValidationResult(
            source_id=source.id,
            status="unsupported_v1" if source.source_type == "api" else "needs_feed_url",
            last_checked_at=checked_at,
            validation_error="No RSS/Atom feed URL configured for v1.",
        )
    if not source.feed_url:
        return ValidationResult(
            source_id=source.id,
            status="needs_feed_url",
            last_checked_at=checked_at,
            validation_error="Missing RSS/Atom feed URL.",
        )

    try:
        content = fetch_feed(source.feed_url, timeout_seconds=timeout_seconds)
        detected_type = detect_feed_type(content)
        if detected_type == "json":
            return validate_json_feed(source, content, checked_at)
        if detected_type not in {"rss", "atom"}:
            return ValidationResult(
                source_id=source.id,
                status="failed",
                last_checked_at=checked_at,
                validation_error="Response is not valid RSS or Atom XML.",
                detected_feed_type=detected_type,
            )
        root = ET.fromstring(content.lstrip())
        entries = find_feed_entries(root)
        items = parse_feed(content, source)
    except Exception as exc:
        return ValidationResult(
            source_id=source.id,
            status="failed",
            last_checked_at=checked_at,
            validation_error=str(exc)[:1000],
            detected_feed_type="unknown",
        )

    has_titles = any(item.title.strip() for item in items)
    has_urls = any(item.url for item in items)
    has_dates = any(item.published_at for item in items)
    has_descriptions = any(item.excerpt or item.raw_description for item in items)
    has_images = any(item.image_url for item in items) or any(
        first_image_url(entry, None) for entry in entries
    )
    error = None
    status = "working"
    if not items:
        status = "failed"
        error = "Feed parsed but had no items."
    elif not has_titles or not has_urls:
        status = "failed"
        error = "Feed parsed but items are missing title or URL."

    return ValidationResult(
        source_id=source.id,
        status=status,
        last_checked_at=checked_at,
        validation_error=error,
        detected_feed_type=detected_type,
        example_item_count=len(items),
        has_images=has_images,
        has_descriptions=has_descriptions,
        has_dates=has_dates,
        has_titles=has_titles,
        has_urls=has_urls,
    )


def validate_json_feed(source: FeedSource, content: bytes, checked_at: str) -> ValidationResult:
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ValidationResult(
            source_id=source.id,
            status="failed",
            last_checked_at=checked_at,
            validation_error=f"Invalid JSON response: {exc}",
            detected_feed_type="json",
        )
    items = data.get("items") if isinstance(data, dict) else None
    count = len(items) if isinstance(items, list) else 0
    return ValidationResult(
        source_id=source.id,
        status="failed",
        last_checked_at=checked_at,
        validation_error="JSON feed/API detected; RSS/Atom only in v1.",
        detected_feed_type="json",
        example_item_count=count,
    )


def detect_feed_type(content: bytes) -> str:
    stripped = content.lstrip()
    if stripped.startswith((b"{", b"[")):
        return "json"
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError:
        return "unknown"
    name = local_name(root.tag).lower()
    if name in {"rss", "rdf"}:
        return "rss"
    if name == "feed":
        return "atom"
    return "unknown"


def validate_source_to_db(
    db_path: Path = DEFAULT_DB_PATH,
    source_id: str | None = None,
    *,
    source: FeedSource | None = None,
    user_id: str = DEFAULT_USER_ID,
    timeout_seconds: float = 12,
) -> ValidationResult:
    target = source or get_user_source(db_path, source_id or "", user_id=user_id)
    result = validate_source(target, timeout_seconds=timeout_seconds)
    update_source_validation(
        db_path,
        target.id,
        user_id=user_id,
        status=result.status,
        validation_error=result.validation_error,
        detected_feed_type=result.detected_feed_type,
        example_item_count=result.example_item_count,
        has_images=result.has_images,
        has_descriptions=result.has_descriptions,
        has_dates=result.has_dates,
        last_checked_at=result.last_checked_at,
    )
    return result


def validate_all_sources_to_db(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    user_id: str = DEFAULT_USER_ID,
    timeout_seconds: float = 12,
    max_workers: int = 8,
) -> list[ValidationResult]:
    sources = list_user_sources(db_path, user_id=user_id)
    results: list[ValidationResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                validate_source_to_db,
                db_path,
                source.id,
                source=source,
                user_id=user_id,
                timeout_seconds=timeout_seconds,
            ): source.id
            for source in sources
        }
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda result: result.source_id)


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag
