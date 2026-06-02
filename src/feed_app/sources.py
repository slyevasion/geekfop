from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from feed_app.defaults import CATEGORIES, load_source_definitions
from feed_app.models import SOURCE_STATUSES, SOURCE_TYPES, FeedConfigError, FeedSource, optional_str

_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def default_sources() -> list[FeedSource]:
    return [normalize_source(FeedSource.from_dict(data)) for data in load_source_definitions()]


def ensure_source_file(path: Path) -> list[FeedSource]:
    if not path.exists():
        sources = default_sources()
        save_sources(sources, path)
        return sources
    return load_sources(path)


def load_sources(path: Path) -> list[FeedSource]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeedConfigError(f"Invalid JSON in source config: {path}") from exc

    raw_sources = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(raw_sources, list):
        raise FeedConfigError("Source config must be a list or an object with a sources list.")
    return normalize_sources(FeedSource.from_dict(item) for item in raw_sources)


def save_sources(sources: list[FeedSource], path: Path) -> None:
    normalized = normalize_sources(sources)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "categories": list(CATEGORIES),
        "sources": [source.to_dict() for source in normalized],
    }
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_path.replace(path)


def normalize_sources(sources: Iterable[FeedSource]) -> list[FeedSource]:
    normalized: list[FeedSource] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, FeedSource):
            raise FeedConfigError("Source entries must be FeedSource objects.")
        clean_source = normalize_source(source)
        if clean_source.id in seen:
            raise FeedConfigError(f"Duplicate source id: {clean_source.id}")
        seen.add(clean_source.id)
        normalized.append(clean_source)
    return normalized


def normalize_source(source: FeedSource) -> FeedSource:
    source_id = source.id.strip().lower()
    name = source.name.strip()
    category = source.category.strip().upper()
    homepage_url = optional_str(source.homepage_url)
    feed_url = optional_str(source.feed_url)
    source_type = source.source_type.strip().lower() or "rss"
    status = source.status.strip().lower() or "untested"
    notes = optional_str(source.notes)
    group = optional_str(source.group)

    if not _SOURCE_ID_RE.fullmatch(source_id):
        raise FeedConfigError(f"Invalid source id: {source.id!r}")
    if not name:
        raise FeedConfigError(f"Source {source_id} must have a name.")
    if category not in CATEGORIES:
        raise FeedConfigError(f"Source {source_id} has invalid category: {source.category!r}")
    if source_type not in SOURCE_TYPES:
        raise FeedConfigError(f"Source {source_id} has invalid source_type: {source.source_type!r}")
    if status not in SOURCE_STATUSES:
        raise FeedConfigError(f"Source {source_id} has invalid status: {source.status!r}")
    if source.enabled and source_type in {"rss", "atom"} and not feed_url:
        raise FeedConfigError(f"Enabled source {source_id} must have a feed_url.")
    if source.pull_frequency_minutes <= 0:
        raise FeedConfigError(f"Source {source_id} pull frequency must be greater than zero.")
    if source_type == "unsupported_v1":
        status = "unsupported_v1"
    elif not feed_url and status == "untested":
        status = "needs_feed_url"
    validate_url(feed_url, "feed_url", source_id)
    validate_url(homepage_url, "homepage_url", source_id)

    return replace(
        source,
        id=source_id,
        name=name,
        category=category,
        homepage_url=homepage_url,
        feed_url=feed_url,
        source_type=source_type,
        status=status,
        notes=notes,
        group=group,
    )


def validate_url(url: str | None, field_name: str, source_id: str) -> None:
    if not url:
        return
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FeedConfigError(f"Source {source_id} has invalid {field_name}: {url!r}")


def upsert_source(sources: list[FeedSource], source: FeedSource) -> list[FeedSource]:
    clean_source = normalize_source(source)
    result: list[FeedSource] = []
    replaced = False
    for existing in sources:
        if existing.id == clean_source.id:
            result.append(clean_source)
            replaced = True
        else:
            result.append(existing)
    if not replaced:
        result.append(clean_source)
    return normalize_sources(result)


def update_source(
    sources: list[FeedSource],
    source_id: str,
    *,
    name: str | None = None,
    category: str | None = None,
    feed_url: str | None = None,
    homepage_url: str | None = None,
    source_type: str | None = None,
    enabled: bool | None = None,
    status: str | None = None,
    pull_frequency_minutes: int | None = None,
    notes: str | None = None,
    group: str | None = None,
) -> list[FeedSource]:
    normalized_id = source_id.strip().lower()
    result: list[FeedSource] = []
    found = False
    for source in sources:
        if source.id != normalized_id:
            result.append(source)
            continue
        found = True
        result.append(
            normalize_source(
                replace(
                    source,
                    name=source.name if name is None else name,
                    category=source.category if category is None else category,
                    feed_url=source.feed_url if feed_url is None else optional_str(feed_url),
                    homepage_url=(
                        source.homepage_url if homepage_url is None else optional_str(homepage_url)
                    ),
                    source_type=source.source_type if source_type is None else source_type,
                    enabled=source.enabled if enabled is None else enabled,
                    status=source.status if status is None else status,
                    pull_frequency_minutes=(
                        source.pull_frequency_minutes
                        if pull_frequency_minutes is None
                        else pull_frequency_minutes
                    ),
                    notes=source.notes if notes is None else optional_str(notes),
                    group=source.group if group is None else optional_str(group),
                )
            )
        )
    if not found:
        raise FeedConfigError(f"Unknown source id: {source_id}")
    return normalize_sources(result)


def remove_source(sources: list[FeedSource], source_id: str) -> list[FeedSource]:
    normalized_id = source_id.strip().lower()
    result = [source for source in sources if source.id != normalized_id]
    if len(result) == len(sources):
        raise FeedConfigError(f"Unknown source id: {source_id}")
    return normalize_sources(result)


def parse_bool(value: str) -> bool:
    cleaned = value.strip().lower()
    if cleaned in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if cleaned in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    raise FeedConfigError(f"Invalid boolean value: {value!r}")
