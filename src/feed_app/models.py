from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class FeedConfigError(ValueError):
    """Raised when feed source config is invalid."""


SOURCE_TYPES = ("rss", "atom", "api", "manual", "unsupported_v1")
SOURCE_STATUSES = ("untested", "working", "failed", "needs_feed_url", "unsupported_v1")


@dataclass(frozen=True)
class FeedSource:
    id: str
    name: str
    category: str
    homepage_url: str | None = None
    feed_url: str | None = None
    source_type: str = "rss"
    enabled: bool = True
    status: str = "untested"
    pull_frequency_minutes: int = 360
    global_source_id: str | None = None
    last_fetched_at: str | None = None
    fetch_status: str | None = None
    last_error: str | None = None
    last_checked_at: str | None = None
    validation_error: str | None = None
    detected_feed_type: str | None = None
    example_item_count: int = 0
    has_images: bool = False
    has_descriptions: bool = False
    has_dates: bool = False
    notes: str | None = None
    group: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def site_url(self) -> str | None:
        return self.homepage_url

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["site_url"] = self.homepage_url
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeedSource:
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            category=str(data.get("category", "")),
            homepage_url=optional_str(data.get("homepage_url") or data.get("site_url")),
            feed_url=optional_str(data.get("feed_url")),
            source_type=str(data.get("source_type") or "rss"),
            enabled=bool(data.get("enabled", True)),
            status=str(data.get("status") or "untested"),
            pull_frequency_minutes=int(data.get("pull_frequency_minutes") or 360),
            global_source_id=optional_str(data.get("global_source_id")),
            last_fetched_at=optional_str(data.get("last_fetched_at")),
            fetch_status=optional_str(data.get("fetch_status")),
            last_error=optional_str(data.get("last_error")),
            last_checked_at=optional_str(data.get("last_checked_at")),
            validation_error=optional_str(data.get("validation_error")),
            detected_feed_type=optional_str(data.get("detected_feed_type")),
            example_item_count=int(data.get("example_item_count") or 0),
            has_images=bool(data.get("has_images", False)),
            has_descriptions=bool(data.get("has_descriptions", False)),
            has_dates=bool(data.get("has_dates", False)),
            notes=optional_str(data.get("notes")),
            group=optional_str(data.get("group") or data.get("group_name")),
            created_at=optional_str(data.get("created_at")),
            updated_at=optional_str(data.get("updated_at")),
        )


@dataclass(frozen=True)
class FeedItem:
    source_id: str
    source_name: str
    category: str
    title: str
    id: str | None = None
    source_scope: str = "user"
    url: str | None = None
    author: str | None = None
    published_at: str | None = None
    raw_description: str | None = None
    excerpt: str | None = None
    image_url: str | None = None
    content_html: str | None = None
    external_id: str | None = None
    tags: tuple[str, ...] = ()
    is_read: bool = False
    is_saved: bool = False
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    @property
    def summary(self) -> str | None:
        return self.excerpt

    @property
    def fetched_at(self) -> str:
        return self.created_at

    @property
    def identity(self) -> str:
        return self.external_id or self.url or f"{self.source_id}:{self.title}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        data["summary"] = self.excerpt
        data["fetched_at"] = self.created_at
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeedItem:
        raw_tags = data.get("tags") or ()
        if isinstance(raw_tags, str):
            tags = (raw_tags,)
        else:
            tags = tuple(str(tag) for tag in raw_tags if str(tag).strip())
        return cls(
            source_id=str(data.get("source_id", "")),
            source_name=str(data.get("source_name", "")),
            category=str(data.get("category", "")),
            title=str(data.get("title", "")),
            id=optional_str(data.get("id")),
            source_scope=str(data.get("source_scope") or "user"),
            url=optional_str(data.get("url")),
            author=optional_str(data.get("author")),
            published_at=optional_str(data.get("published_at")),
            raw_description=optional_str(data.get("raw_description")),
            excerpt=optional_str(data.get("excerpt") or data.get("summary")),
            image_url=optional_str(data.get("image_url")),
            content_html=optional_str(data.get("content_html")),
            external_id=optional_str(data.get("external_id")),
            tags=tags,
            is_read=bool(data.get("is_read", False)),
            is_saved=bool(data.get("is_saved", False)),
            created_at=str(data.get("created_at") or data.get("fetched_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or data.get("fetched_at") or utc_now_iso()),
        )


@dataclass(frozen=True)
class SourceIngestResult:
    source_id: str
    source_name: str
    category: str
    feed_url: str | None
    parsed: int = 0
    written: int = 0
    skipped: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IngestRun:
    started_at: str
    finished_at: str
    output_path: str
    sources: tuple[SourceIngestResult, ...]

    @property
    def total_parsed(self) -> int:
        return sum(source.parsed for source in self.sources)

    @property
    def total_written(self) -> int:
        return sum(source.written for source in self.sources)

    @property
    def total_skipped(self) -> int:
        return sum(source.skipped for source in self.sources)

    @property
    def error_count(self) -> int:
        return sum(1 for source in self.sources if source.error)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output_path": self.output_path,
            "total_parsed": self.total_parsed,
            "total_written": self.total_written,
            "total_skipped": self.total_skipped,
            "error_count": self.error_count,
            "sources": [source.to_dict() for source in self.sources],
        }


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
