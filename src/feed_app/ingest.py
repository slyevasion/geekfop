from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from feed_app.db import append_feed_items, record_source_error
from feed_app.defaults import DEFAULT_DB_PATH, DEFAULT_RUN_DIR, DEFAULT_USER_ID
from feed_app.models import FeedItem, FeedSource, IngestRun, SourceIngestResult, utc_now_iso
from feed_app.storage import write_run_metadata

MAX_FEED_BYTES = 6_000_000
USER_AGENT = "AIReconFeedApp/0.1 (+https://local.feed.app)"
_TAG_RE = re.compile(r"<[^>]+>")
_IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


class FeedIngestError(RuntimeError):
    """Raised when feed fetching or parsing fails."""


def ingest_sources(
    sources: Iterable[FeedSource],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    run_dir: Path | None = DEFAULT_RUN_DIR,
    user_id: str = DEFAULT_USER_ID,
    timeout_seconds: float = 20,
    limit_per_source: int = 40,
    append_items_func=append_feed_items,
    record_error_func=record_source_error,
    output_path: str | None = None,
) -> IngestRun:
    started_at = utc_now_iso()
    results: list[SourceIngestResult] = []
    for source in sources:
        if (
            not source.enabled
            or not source.feed_url
            or source.source_type not in {"rss", "atom"}
            or source.status == "unsupported_v1"
        ):
            continue
        try:
            content = fetch_feed(source.feed_url, timeout_seconds=timeout_seconds)
            items = parse_feed(content, source)
            limited_items = items[:limit_per_source]
            append_result = append_items_func(db_path, source, limited_items, user_id=user_id)
            results.append(
                SourceIngestResult(
                    source_id=source.id,
                    source_name=source.name,
                    category=source.category,
                    feed_url=source.feed_url,
                    parsed=len(limited_items),
                    written=append_result.written,
                    skipped=append_result.skipped,
                )
            )
        except Exception as exc:  # Network feeds are allowed to fail independently.
            error = str(exc)
            record_error_func(db_path, source, error, user_id=user_id)
            results.append(
                SourceIngestResult(
                    source_id=source.id,
                    source_name=source.name,
                    category=source.category,
                    feed_url=source.feed_url,
                    error=error,
                )
            )

    run = IngestRun(
        started_at=started_at,
        finished_at=utc_now_iso(),
        output_path=output_path or str(db_path),
        sources=tuple(results),
    )
    if run_dir is not None:
        write_run_metadata(run, run_dir)
    return run


def fetch_feed(url: str, *, timeout_seconds: float) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            content = response.read(MAX_FEED_BYTES + 1)
    except HTTPError as exc:
        raise FeedIngestError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise FeedIngestError(f"Fetch failed for {url}: {exc.reason}") from exc
    if len(content) > MAX_FEED_BYTES:
        raise FeedIngestError(f"Feed too large: {url}")
    return content


def parse_feed(content: bytes | str, source: FeedSource) -> list[FeedItem]:
    try:
        root = ET.fromstring(content.lstrip())
    except ET.ParseError as exc:
        raise FeedIngestError(f"Invalid XML from {source.name}: {exc}") from exc

    item_elements = find_feed_entries(root)
    fetched_at = utc_now_iso()
    items: list[FeedItem] = []
    for element in item_elements:
        item = parse_entry(element, source, fetched_at=fetched_at)
        if item.title.strip():
            items.append(item)
    return items


def find_feed_entries(root: ET.Element) -> list[ET.Element]:
    root_name = local_name(root.tag).lower()
    if root_name == "feed":
        return [child for child in root if local_name(child.tag).lower() == "entry"]
    items = [element for element in root.iter() if local_name(element.tag).lower() == "item"]
    if items:
        return items
    return [element for element in root.iter() if local_name(element.tag).lower() == "entry"]


def parse_entry(element: ET.Element, source: FeedSource, *, fetched_at: str) -> FeedItem:
    title = clean_text(first_child_text(element, ("title",)) or "Untitled", max_chars=220)
    url = first_link(element)
    raw_description = first_child_raw(element, ("description", "summary", "encoded", "content"))
    excerpt = clean_text(raw_description or "", max_chars=420)
    author = first_author(element)
    image_url = first_image_url(element, raw_description)
    published_at = first_child_text(
        element, ("pubDate", "published", "updated", "dc:date", "date")
    )
    external_id = first_child_text(element, ("guid", "id")) or url
    tags = tuple(
        sorted({clean_text(tag, max_chars=80) for tag in category_values(element) if tag.strip()})
    )
    return FeedItem(
        source_id=source.global_source_id or source.id,
        source_scope="global" if source.global_source_id else "user",
        source_name=source.name,
        category=source.category,
        title=title,
        url=url,
        author=clean_text(author or "", max_chars=160) or None,
        published_at=clean_text(published_at or "", max_chars=120) or None,
        raw_description=raw_description,
        excerpt=excerpt or None,
        image_url=image_url,
        content_html=raw_description if raw_description and "<" in raw_description else None,
        external_id=clean_text(external_id or "", max_chars=500) or None,
        tags=tags,
        created_at=fetched_at,
        updated_at=fetched_at,
    )


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def children_named(element: ET.Element, names: tuple[str, ...]) -> list[ET.Element]:
    clean_names = {name.split(":")[-1].lower() for name in names}
    return [child for child in element if local_name(child.tag).lower() in clean_names]


def first_child_text(element: ET.Element, names: tuple[str, ...]) -> str | None:
    for child in children_named(element, names):
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return None


def first_child_raw(element: ET.Element, names: tuple[str, ...]) -> str | None:
    for child in children_named(element, names):
        parts = [child.text or ""]
        parts.extend(ET.tostring(grandchild, encoding="unicode") for grandchild in child)
        parts.append(child.tail or "")
        raw = "".join(parts).strip()
        if raw:
            return raw
    return None


def first_author(element: ET.Element) -> str | None:
    creator = first_child_text(element, ("creator", "author"))
    if creator:
        return creator
    for author in children_named(element, ("author",)):
        name = first_child_text(author, ("name",))
        if name:
            return name
    return None


def first_link(element: ET.Element) -> str | None:
    text_link = first_child_text(element, ("link",))
    if text_link and not text_link.startswith("http"):
        text_link = None
    for child in children_named(element, ("link",)):
        href = child.attrib.get("href", "").strip()
        rel = child.attrib.get("rel", "alternate").strip().lower()
        if href and rel in {"", "alternate"}:
            return href
    return text_link


def first_image_url(element: ET.Element, raw_description: str | None) -> str | None:
    for child in element.iter():
        name = local_name(child.tag).lower()
        if name == "enclosure" and child.attrib.get("type", "").startswith("image/"):
            url = child.attrib.get("url", "").strip()
            if url:
                return url
        if name in {"thumbnail", "content"}:
            url = child.attrib.get("url", "").strip()
            mime_type = child.attrib.get("type", "")
            medium = child.attrib.get("medium", "")
            if url and (mime_type.startswith("image/") or medium == "image" or name == "thumbnail"):
                return url
    if raw_description:
        match = _IMG_RE.search(raw_description)
        if match:
            return html.unescape(match.group(1))
    return None


def category_values(element: ET.Element) -> list[str]:
    values: list[str] = []
    for child in children_named(element, ("category",)):
        term = child.attrib.get("term") or child.attrib.get("label")
        if term:
            values.append(term)
        text = "".join(child.itertext()).strip()
        if text:
            values.append(text)
    return values


def clean_text(value: str, *, max_chars: int) -> str:
    text = html.unescape(value)
    text = _TAG_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."
