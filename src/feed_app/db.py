from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from feed_app.defaults import DEFAULT_DB_PATH, DEFAULT_USER_ID
from feed_app.models import FeedItem, FeedSource, utc_now_iso
from feed_app.sources import default_sources, normalize_source


@dataclass(frozen=True)
class AppendItemsResult:
    written: int
    skipped: int


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as connection:
        create_schema(connection)
        ensure_default_user(connection)
        seed_global_sources(connection, default_sources())


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT,
            display_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS global_sources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            default_category TEXT NOT NULL,
            homepage_url TEXT,
            feed_url TEXT,
            source_type TEXT NOT NULL DEFAULT 'rss',
            default_enabled INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            last_checked_at TEXT,
            validation_error TEXT,
            detected_feed_type TEXT,
            example_item_count INTEGER NOT NULL DEFAULT 0,
            has_images INTEGER NOT NULL DEFAULT 0,
            has_descriptions INTEGER NOT NULL DEFAULT 0,
            has_dates INTEGER NOT NULL DEFAULT 0,
            group_name TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_sources (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            global_source_id TEXT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            homepage_url TEXT,
            feed_url TEXT,
            source_type TEXT NOT NULL DEFAULT 'rss',
            enabled INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'untested',
            pull_frequency_minutes INTEGER NOT NULL DEFAULT 360,
            last_fetched_at TEXT,
            fetch_status TEXT,
            last_error TEXT,
            last_checked_at TEXT,
            validation_error TEXT,
            detected_feed_type TEXT,
            example_item_count INTEGER NOT NULL DEFAULT 0,
            has_images INTEGER NOT NULL DEFAULT 0,
            has_descriptions INTEGER NOT NULL DEFAULT 0,
            has_dates INTEGER NOT NULL DEFAULT 0,
            group_name TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (global_source_id) REFERENCES global_sources(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS feed_items (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_scope TEXT NOT NULL CHECK (source_scope IN ('global', 'user')),
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT,
            author TEXT,
            published_at TEXT,
            raw_description TEXT,
            excerpt TEXT,
            image_url TEXT,
            content_html TEXT,
            external_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_feed_item_states (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            feed_item_id TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            is_saved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (feed_item_id) REFERENCES feed_items(id) ON DELETE CASCADE,
            UNIQUE (user_id, feed_item_id)
        );

        CREATE INDEX IF NOT EXISTS idx_user_sources_user_category
            ON user_sources(user_id, category);
        CREATE INDEX IF NOT EXISTS idx_feed_items_source
            ON feed_items(source_scope, source_id);
        CREATE INDEX IF NOT EXISTS idx_feed_items_category
            ON feed_items(category);
        CREATE INDEX IF NOT EXISTS idx_feed_items_created
            ON feed_items(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_user_item_state_saved
            ON user_feed_item_states(user_id, is_saved);
        """
    )
    ensure_columns(connection)


def ensure_columns(connection: sqlite3.Connection) -> None:
    add_missing_columns(
        connection,
        "global_sources",
        {
            "last_checked_at": "TEXT",
            "validation_error": "TEXT",
            "detected_feed_type": "TEXT",
            "example_item_count": "INTEGER NOT NULL DEFAULT 0",
            "has_images": "INTEGER NOT NULL DEFAULT 0",
            "has_descriptions": "INTEGER NOT NULL DEFAULT 0",
            "has_dates": "INTEGER NOT NULL DEFAULT 0",
            "group_name": "TEXT",
        },
    )
    add_missing_columns(
        connection,
        "user_sources",
        {
            "status": "TEXT NOT NULL DEFAULT 'untested'",
            "last_checked_at": "TEXT",
            "validation_error": "TEXT",
            "detected_feed_type": "TEXT",
            "example_item_count": "INTEGER NOT NULL DEFAULT 0",
            "has_images": "INTEGER NOT NULL DEFAULT 0",
            "has_descriptions": "INTEGER NOT NULL DEFAULT 0",
            "has_dates": "INTEGER NOT NULL DEFAULT 0",
            "group_name": "TEXT",
        },
    )


def add_missing_columns(
    connection: sqlite3.Connection, table_name: str, columns: dict[str, str]
) -> None:
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}
    for name, definition in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")


def ensure_default_user(connection: sqlite3.Connection, user_id: str = DEFAULT_USER_ID) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO users (id, email, display_name, created_at, updated_at)
        VALUES (?, NULL, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (user_id, "Personal", now, now),
    )


def seed_global_sources(connection: sqlite3.Connection, sources: Iterable[FeedSource]) -> None:
    now = utc_now_iso()
    for source in sources:
        clean = normalize_source(source)
        connection.execute(
            """
            INSERT INTO global_sources (
                id, name, default_category, homepage_url, feed_url, source_type,
                default_enabled, status, last_checked_at, validation_error,
                detected_feed_type, example_item_count, has_images, has_descriptions,
                has_dates, group_name, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                default_category = excluded.default_category,
                homepage_url = excluded.homepage_url,
                feed_url = excluded.feed_url,
                source_type = excluded.source_type,
                default_enabled = excluded.default_enabled,
                status = excluded.status,
                last_checked_at = excluded.last_checked_at,
                validation_error = excluded.validation_error,
                detected_feed_type = excluded.detected_feed_type,
                example_item_count = excluded.example_item_count,
                has_images = excluded.has_images,
                has_descriptions = excluded.has_descriptions,
                has_dates = excluded.has_dates,
                group_name = excluded.group_name,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                clean.id,
                clean.name,
                clean.category,
                clean.homepage_url,
                clean.feed_url,
                clean.source_type,
                int(clean.enabled),
                clean.status,
                clean.last_checked_at,
                clean.validation_error,
                clean.detected_feed_type,
                clean.example_item_count,
                int(clean.has_images),
                int(clean.has_descriptions),
                int(clean.has_dates),
                clean.group,
                clean.notes,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO user_sources (
                id, user_id, global_source_id, name, category, homepage_url, feed_url,
                source_type, enabled, status, pull_frequency_minutes, fetch_status,
                last_checked_at, validation_error, detected_feed_type, example_item_count,
                has_images, has_descriptions, has_dates, group_name, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                clean.id,
                DEFAULT_USER_ID,
                clean.id,
                clean.name,
                clean.category,
                clean.homepage_url,
                clean.feed_url,
                clean.source_type,
                int(clean.enabled),
                clean.status,
                clean.pull_frequency_minutes,
                "idle",
                clean.last_checked_at,
                clean.validation_error,
                clean.detected_feed_type,
                clean.example_item_count,
                int(clean.has_images),
                int(clean.has_descriptions),
                int(clean.has_dates),
                clean.group,
                clean.notes,
                now,
                now,
            ),
        )
        connection.execute(
            """
            UPDATE user_sources
            SET source_type = ?,
                status = CASE
                    WHEN ? = 'unsupported_v1' THEN 'unsupported_v1'
                    WHEN last_checked_at IS NULL THEN ?
                    ELSE status
                END,
                group_name = COALESCE(group_name, ?),
                notes = CASE
                    WHEN notes IS NULL OR notes = '' THEN ?
                    ELSE notes
                END,
                updated_at = ?
            WHERE id = ? AND user_id = ? AND global_source_id = ?
            """,
            (
                clean.source_type,
                clean.status,
                clean.status,
                clean.group,
                clean.notes,
                now,
                clean.id,
                DEFAULT_USER_ID,
                clean.id,
            ),
        )


def list_user_sources(
    db_path: Path = DEFAULT_DB_PATH, *, user_id: str = DEFAULT_USER_ID
) -> list[FeedSource]:
    initialize_database(db_path)
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM user_sources
            WHERE user_id = ?
            ORDER BY category, name
            """,
            (user_id,),
        ).fetchall()
    return [source_from_row(row) for row in rows]


def get_user_source(
    db_path: Path, source_id: str, *, user_id: str = DEFAULT_USER_ID
) -> FeedSource:
    initialize_database(db_path)
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM user_sources WHERE id = ? AND user_id = ?",
            (source_id.strip().lower(), user_id),
        ).fetchone()
    if row is None:
        from feed_app.models import FeedConfigError

        raise FeedConfigError(f"Unknown source id: {source_id}")
    return source_from_row(row)


def upsert_user_source(
    db_path: Path, source: FeedSource, *, user_id: str = DEFAULT_USER_ID
) -> FeedSource:
    initialize_database(db_path)
    clean = normalize_source(source)
    now = utc_now_iso()
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO user_sources (
                id, user_id, global_source_id, name, category, homepage_url, feed_url,
                source_type, enabled, status, pull_frequency_minutes, last_fetched_at,
                fetch_status, last_error, last_checked_at, validation_error,
                detected_feed_type, example_item_count, has_images, has_descriptions,
                has_dates, group_name, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                homepage_url = excluded.homepage_url,
                feed_url = excluded.feed_url,
                source_type = excluded.source_type,
                enabled = excluded.enabled,
                status = excluded.status,
                pull_frequency_minutes = excluded.pull_frequency_minutes,
                fetch_status = excluded.fetch_status,
                last_error = excluded.last_error,
                last_checked_at = excluded.last_checked_at,
                validation_error = excluded.validation_error,
                detected_feed_type = excluded.detected_feed_type,
                example_item_count = excluded.example_item_count,
                has_images = excluded.has_images,
                has_descriptions = excluded.has_descriptions,
                has_dates = excluded.has_dates,
                group_name = excluded.group_name,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                clean.id,
                user_id,
                clean.global_source_id,
                clean.name,
                clean.category,
                clean.homepage_url,
                clean.feed_url,
                clean.source_type,
                int(clean.enabled),
                clean.status,
                clean.pull_frequency_minutes,
                clean.last_fetched_at,
                clean.fetch_status or "idle",
                clean.last_error,
                clean.last_checked_at,
                clean.validation_error,
                clean.detected_feed_type,
                clean.example_item_count,
                int(clean.has_images),
                int(clean.has_descriptions),
                int(clean.has_dates),
                clean.group,
                clean.notes,
                clean.created_at or now,
                now,
            ),
        )
    return get_user_source(db_path, clean.id, user_id=user_id)


def delete_user_source(db_path: Path, source_id: str, *, user_id: str = DEFAULT_USER_ID) -> None:
    initialize_database(db_path)
    with connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM user_sources WHERE id = ? AND user_id = ?",
            (source_id.strip().lower(), user_id),
        )
    if cursor.rowcount == 0:
        from feed_app.models import FeedConfigError

        raise FeedConfigError(f"Unknown source id: {source_id}")


def toggle_user_source(
    db_path: Path, source_id: str, *, user_id: str = DEFAULT_USER_ID
) -> FeedSource:
    source = get_user_source(db_path, source_id, user_id=user_id)
    return upsert_user_source(
        db_path,
        FeedSource(
            id=source.id,
            name=source.name,
            category=source.category,
            homepage_url=source.homepage_url,
            feed_url=source.feed_url,
            source_type=source.source_type,
            enabled=not source.enabled,
            status=source.status,
            pull_frequency_minutes=source.pull_frequency_minutes,
            global_source_id=source.global_source_id,
            last_fetched_at=source.last_fetched_at,
            fetch_status=source.fetch_status,
            last_error=source.last_error,
            last_checked_at=source.last_checked_at,
            validation_error=source.validation_error,
            detected_feed_type=source.detected_feed_type,
            example_item_count=source.example_item_count,
            has_images=source.has_images,
            has_descriptions=source.has_descriptions,
            has_dates=source.has_dates,
            notes=source.notes,
            group=source.group,
            created_at=source.created_at,
        ),
        user_id=user_id,
    )


def append_feed_items(
    db_path: Path,
    source: FeedSource,
    items: Iterable[FeedItem],
    *,
    user_id: str = DEFAULT_USER_ID,
) -> AppendItemsResult:
    initialize_database(db_path)
    written = 0
    skipped = 0
    with connect(db_path) as connection:
        for item in items:
            source_scope = "global" if source.global_source_id else "user"
            scoped_source_id = source.global_source_id or source.id
            item_id = item.id or stable_item_id(source_scope, scoped_source_id, item.identity)
            now = utc_now_iso()
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO feed_items (
                    id, source_id, source_scope, category, title, url, author,
                    published_at, raw_description, excerpt, image_url, content_html,
                    external_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    scoped_source_id,
                    source_scope,
                    item.category,
                    item.title,
                    item.url,
                    item.author,
                    item.published_at,
                    item.raw_description,
                    item.excerpt,
                    item.image_url,
                    item.content_html,
                    item.external_id,
                    item.created_at,
                    now,
                ),
            )
            if cursor.rowcount:
                written += 1
            else:
                skipped += 1
            state_id = stable_state_id(user_id, item_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO user_feed_item_states (
                    id, user_id, feed_item_id, is_read, is_saved, created_at, updated_at
                ) VALUES (?, ?, ?, 0, 0, ?, ?)
                """,
                (state_id, user_id, item_id, now, now),
            )
        update_source_fetch_status(connection, source.id, user_id=user_id, status="ok", error=None)
    return AppendItemsResult(written=written, skipped=skipped)


def update_source_fetch_status(
    connection: sqlite3.Connection,
    source_id: str,
    *,
    user_id: str,
    status: str,
    error: str | None,
) -> None:
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE user_sources
        SET last_fetched_at = ?, fetch_status = ?, last_error = ?, updated_at = ?
        WHERE id = ? AND user_id = ?
        """,
        (now, status, error, now, source_id, user_id),
    )


def record_source_error(
    db_path: Path, source: FeedSource, error: str, *, user_id: str = DEFAULT_USER_ID
) -> None:
    initialize_database(db_path)
    with connect(db_path) as connection:
        update_source_fetch_status(
            connection, source.id, user_id=user_id, status="error", error=error[:1000]
        )


def update_source_validation(
    db_path: Path,
    source_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    status: str,
    validation_error: str | None,
    detected_feed_type: str | None,
    example_item_count: int,
    has_images: bool,
    has_descriptions: bool,
    has_dates: bool,
    last_checked_at: str,
) -> FeedSource:
    initialize_database(db_path)
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE user_sources
            SET status = ?, validation_error = ?, detected_feed_type = ?,
                example_item_count = ?, has_images = ?, has_descriptions = ?,
                has_dates = ?, last_checked_at = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                status,
                validation_error,
                detected_feed_type,
                example_item_count,
                int(has_images),
                int(has_descriptions),
                int(has_dates),
                last_checked_at,
                last_checked_at,
                source_id,
                user_id,
            ),
        )
    return get_user_source(db_path, source_id, user_id=user_id)


def list_feed_items(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    user_id: str = DEFAULT_USER_ID,
    category: str | None = None,
    source_id: str | None = None,
    search: str | None = None,
    saved_only: bool = False,
    limit: int = 200,
) -> list[FeedItem]:
    initialize_database(db_path)
    clauses = ["state.user_id = ?"]
    args: list[object] = [user_id]
    if category:
        clauses.append("items.category = ?")
        args.append(category)
    if source_id:
        clauses.append("(items.source_id = ? OR sources.id = ?)")
        args.extend((source_id, source_id))
    if search:
        clauses.append("(items.title LIKE ? OR items.excerpt LIKE ? OR sources.name LIKE ?)")
        pattern = f"%{search}%"
        args.extend((pattern, pattern, pattern))
    if saved_only:
        clauses.append("state.is_saved = 1")
    args.append(limit)
    sql = f"""
        SELECT
            items.*,
            sources.name AS source_name,
            state.is_read AS is_read,
            state.is_saved AS is_saved
        FROM feed_items AS items
        JOIN user_feed_item_states AS state ON state.feed_item_id = items.id
        LEFT JOIN user_sources AS sources
            ON sources.user_id = state.user_id
            AND (
                (items.source_scope = 'global' AND sources.global_source_id = items.source_id)
                OR (items.source_scope = 'user' AND sources.id = items.source_id)
            )
        WHERE {' AND '.join(clauses)}
        ORDER BY COALESCE(items.published_at, items.created_at) DESC
        LIMIT ?
    """
    with connect(db_path) as connection:
        rows = connection.execute(sql, tuple(args)).fetchall()
    return [item_from_row(row) for row in rows]


def source_from_row(row: sqlite3.Row) -> FeedSource:
    return FeedSource(
        id=row["id"],
        name=row["name"],
        category=row["category"],
        homepage_url=row["homepage_url"],
        feed_url=row["feed_url"],
        source_type=row["source_type"],
        enabled=bool(row["enabled"]),
        status=row["status"],
        pull_frequency_minutes=int(row["pull_frequency_minutes"]),
        global_source_id=row["global_source_id"],
        last_fetched_at=row["last_fetched_at"],
        fetch_status=row["fetch_status"],
        last_error=row["last_error"],
        last_checked_at=row["last_checked_at"],
        validation_error=row["validation_error"],
        detected_feed_type=row["detected_feed_type"],
        example_item_count=int(row["example_item_count"]),
        has_images=bool(row["has_images"]),
        has_descriptions=bool(row["has_descriptions"]),
        has_dates=bool(row["has_dates"]),
        notes=row["notes"],
        group=row["group_name"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def item_from_row(row: sqlite3.Row) -> FeedItem:
    return FeedItem(
        id=row["id"],
        source_id=row["source_id"],
        source_scope=row["source_scope"],
        source_name=row["source_name"] or row["source_id"],
        category=row["category"],
        title=row["title"],
        url=row["url"],
        author=row["author"],
        published_at=row["published_at"],
        raw_description=row["raw_description"],
        excerpt=row["excerpt"],
        image_url=row["image_url"],
        content_html=row["content_html"],
        external_id=row["external_id"],
        is_read=bool(row["is_read"]),
        is_saved=bool(row["is_saved"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def stable_item_id(source_scope: str, source_id: str, identity: str) -> str:
    return "item_" + stable_hash(f"{source_scope}:{source_id}:{identity}")


def stable_state_id(user_id: str, item_id: str) -> str:
    return "state_" + stable_hash(f"{user_id}:{item_id}")


def stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:24]
