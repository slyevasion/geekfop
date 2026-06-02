from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from feed_app.models import FeedItem, IngestRun


@dataclass(frozen=True)
class AppendFeedResult:
    path: Path
    written: int
    skipped: int


def append_items_jsonl(items: Iterable[FeedItem], path: Path) -> AppendFeedResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = load_seen_identities(path)
    written = 0
    skipped = 0

    with path.open("a", encoding="utf-8") as handle:
        for item in items:
            if item.identity in seen:
                skipped += 1
                continue
            handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
            seen.add(item.identity)
            written += 1
    return AppendFeedResult(path=path, written=written, skipped=skipped)


def load_seen_identities(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen: set[str] = set()
    for item in read_items(path):
        seen.add(item.identity)
    return seen


def read_items(path: Path, *, limit: int | None = None) -> list[FeedItem]:
    if not path.exists():
        return []
    items: list[FeedItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        items.append(FeedItem.from_dict(data))
    items.sort(key=lambda item: item.fetched_at, reverse=True)
    if limit is not None:
        return items[:limit]
    return items


def write_run_metadata(run: IngestRun, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    safe_started = run.started_at.replace(":", "").replace("+", "Z")
    path = run_dir / f"{safe_started}.json"
    path.write_text(
        json.dumps(run.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
