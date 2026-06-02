from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class RedditPost:
    subreddit: str
    title: str
    url: str | None = None
    permalink: str | None = None
    author: str | None = None
    score: int | None = None
    comment_count: int | None = None
    created_at: str | None = None
    scraped_at: str = field(default_factory=utc_now_iso)
    source_url: str | None = None

    @property
    def identity(self) -> str:
        return self.permalink or self.url or f"{self.subreddit}:{self.title}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RedditPost:
        return cls(**data)


@dataclass(frozen=True)
class SubredditResult:
    subreddit: str
    requested: int
    collected: int
    source_urls: tuple[str, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScrapeRun:
    started_at: str
    finished_at: str
    subreddits: tuple[SubredditResult, ...]
    total_posts: int
    output_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "subreddits": [result.to_dict() for result in self.subreddits],
            "total_posts": self.total_posts,
            "output_path": self.output_path,
        }
