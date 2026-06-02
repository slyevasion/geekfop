from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from reddit_scraper.models import RedditPost, ScrapeRun


@dataclass(frozen=True)
class AppendResult:
    path: Path
    written: int
    skipped: int


def append_posts_jsonl(posts: Iterable[RedditPost], path: Path) -> AppendResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = load_seen_identities(path)
    written = 0
    skipped = 0

    with path.open("a", encoding="utf-8") as handle:
        for post in posts:
            if post.identity in seen:
                skipped += 1
                continue
            handle.write(json.dumps(post.to_dict(), ensure_ascii=False) + "\n")
            seen.add(post.identity)
            written += 1

    return AppendResult(path=path, written=written, skipped=skipped)


def load_seen_identities(path: Path) -> set[str]:
    if not path.exists():
        return set()

    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        permalink = data.get("permalink")
        url = data.get("url")
        title = data.get("title")
        subreddit = data.get("subreddit")
        identity = permalink or url or (f"{subreddit}:{title}" if subreddit and title else None)
        if identity:
            seen.add(identity)
    return seen


def write_run_metadata(run: ScrapeRun, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    safe_started = run.started_at.replace(":", "").replace("+", "Z")
    path = run_dir / f"{safe_started}.json"
    path.write_text(
        json.dumps(run.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
