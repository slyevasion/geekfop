from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Connection, RowMapping

from feed_app import db as sqlite_db
from feed_app.db import AppendItemsResult
from feed_app.defaults import DEFAULT_DB_PATH, DEFAULT_USER_ID
from feed_app.models import FeedConfigError, FeedItem, FeedSource, utc_now_iso
from feed_app.sources import default_sources, normalize_source


class FeedRepository(Protocol):
    def initialize(self) -> None: ...

    def list_user_sources(self, *, user_id: str = DEFAULT_USER_ID) -> list[FeedSource]: ...

    def get_user_source(self, source_id: str, *, user_id: str = DEFAULT_USER_ID) -> FeedSource: ...

    def upsert_user_source(
        self, source: FeedSource, *, user_id: str = DEFAULT_USER_ID
    ) -> FeedSource: ...

    def delete_user_source(self, source_id: str, *, user_id: str = DEFAULT_USER_ID) -> None: ...

    def toggle_user_source(
        self, source_id: str, *, user_id: str = DEFAULT_USER_ID
    ) -> FeedSource: ...

    def append_feed_items(
        self,
        source: FeedSource,
        items: Iterable[FeedItem],
        *,
        user_id: str = DEFAULT_USER_ID,
    ) -> AppendItemsResult: ...

    def record_source_error(
        self, source: FeedSource, error: str, *, user_id: str = DEFAULT_USER_ID
    ) -> None: ...

    def update_source_validation(
        self,
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
    ) -> FeedSource: ...

    def list_feed_items(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        category: str | None = None,
        source_id: str | None = None,
        search: str | None = None,
        saved_only: bool = False,
        limit: int = 200,
    ) -> list[FeedItem]: ...


class SQLiteRepository:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        sqlite_db.initialize_database(self.db_path)

    def list_user_sources(self, *, user_id: str = DEFAULT_USER_ID) -> list[FeedSource]:
        return sqlite_db.list_user_sources(self.db_path, user_id=user_id)

    def get_user_source(self, source_id: str, *, user_id: str = DEFAULT_USER_ID) -> FeedSource:
        return sqlite_db.get_user_source(self.db_path, source_id, user_id=user_id)

    def upsert_user_source(
        self, source: FeedSource, *, user_id: str = DEFAULT_USER_ID
    ) -> FeedSource:
        return sqlite_db.upsert_user_source(self.db_path, source, user_id=user_id)

    def delete_user_source(self, source_id: str, *, user_id: str = DEFAULT_USER_ID) -> None:
        sqlite_db.delete_user_source(self.db_path, source_id, user_id=user_id)

    def toggle_user_source(self, source_id: str, *, user_id: str = DEFAULT_USER_ID) -> FeedSource:
        return sqlite_db.toggle_user_source(self.db_path, source_id, user_id=user_id)

    def append_feed_items(
        self,
        source: FeedSource,
        items: Iterable[FeedItem],
        *,
        user_id: str = DEFAULT_USER_ID,
    ) -> AppendItemsResult:
        return sqlite_db.append_feed_items(self.db_path, source, items, user_id=user_id)

    def record_source_error(
        self, source: FeedSource, error: str, *, user_id: str = DEFAULT_USER_ID
    ) -> None:
        sqlite_db.record_source_error(self.db_path, source, error, user_id=user_id)

    def update_source_validation(
        self,
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
        return sqlite_db.update_source_validation(
            self.db_path,
            source_id,
            user_id=user_id,
            status=status,
            validation_error=validation_error,
            detected_feed_type=detected_feed_type,
            example_item_count=example_item_count,
            has_images=has_images,
            has_descriptions=has_descriptions,
            has_dates=has_dates,
            last_checked_at=last_checked_at,
        )

    def list_feed_items(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        category: str | None = None,
        source_id: str | None = None,
        search: str | None = None,
        saved_only: bool = False,
        limit: int = 200,
    ) -> list[FeedItem]:
        return sqlite_db.list_feed_items(
            self.db_path,
            user_id=user_id,
            category=category,
            source_id=source_id,
            search=search,
            saved_only=saved_only,
            limit=limit,
        )


metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", String(255), primary_key=True),
    Column("email", String(320)),
    Column("display_name", String(255)),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
)

global_sources = Table(
    "global_sources",
    metadata,
    Column("id", String(255), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("default_category", String(64), nullable=False),
    Column("homepage_url", Text),
    Column("feed_url", Text),
    Column("source_type", String(64), nullable=False, default="rss"),
    Column("default_enabled", Boolean, nullable=False, default=True),
    Column("status", String(64), nullable=False, default="active"),
    Column("last_checked_at", String(64)),
    Column("validation_error", Text),
    Column("detected_feed_type", String(64)),
    Column("example_item_count", Integer, nullable=False, default=0),
    Column("has_images", Boolean, nullable=False, default=False),
    Column("has_descriptions", Boolean, nullable=False, default=False),
    Column("has_dates", Boolean, nullable=False, default=False),
    Column("group_name", String(255)),
    Column("notes", Text),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
)

user_sources = Table(
    "user_sources",
    metadata,
    Column("id", String(255), primary_key=True),
    Column("user_id", String(255), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("global_source_id", String(255), ForeignKey("global_sources.id", ondelete="SET NULL")),
    Column("name", String(255), nullable=False),
    Column("category", String(64), nullable=False),
    Column("homepage_url", Text),
    Column("feed_url", Text),
    Column("source_type", String(64), nullable=False, default="rss"),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("status", String(64), nullable=False, default="untested"),
    Column("pull_frequency_minutes", Integer, nullable=False, default=360),
    Column("last_fetched_at", String(64)),
    Column("fetch_status", String(64)),
    Column("last_error", Text),
    Column("last_checked_at", String(64)),
    Column("validation_error", Text),
    Column("detected_feed_type", String(64)),
    Column("example_item_count", Integer, nullable=False, default=0),
    Column("has_images", Boolean, nullable=False, default=False),
    Column("has_descriptions", Boolean, nullable=False, default=False),
    Column("has_dates", Boolean, nullable=False, default=False),
    Column("group_name", String(255)),
    Column("notes", Text),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
)

feed_items = Table(
    "feed_items",
    metadata,
    Column("id", String(255), primary_key=True),
    Column("source_id", String(255), nullable=False),
    Column("source_scope", String(64), nullable=False),
    Column("category", String(64), nullable=False),
    Column("title", Text, nullable=False),
    Column("url", Text),
    Column("author", String(255)),
    Column("published_at", String(255)),
    Column("raw_description", Text),
    Column("excerpt", Text),
    Column("image_url", Text),
    Column("content_html", Text),
    Column("external_id", Text),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
)

user_feed_item_states = Table(
    "user_feed_item_states",
    metadata,
    Column("id", String(255), primary_key=True),
    Column("user_id", String(255), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("feed_item_id", String(255), ForeignKey("feed_items.id", ondelete="CASCADE")),
    Column("is_read", Boolean, nullable=False, default=False),
    Column("is_saved", Boolean, nullable=False, default=False),
    Column("created_at", String(64), nullable=False),
    Column("updated_at", String(64), nullable=False),
)


class SQLAlchemyRepository:
    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self.database_url = normalize_database_url(database_url)
        self.engine = create_engine(self.database_url, echo=echo, pool_pre_ping=True, future=True)
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        with self.engine.begin() as connection:
            metadata.create_all(connection)
            self._ensure_default_user(connection)
            self._seed_global_sources(connection, default_sources())
        self._initialized = True

    def list_user_sources(self, *, user_id: str = DEFAULT_USER_ID) -> list[FeedSource]:
        self.initialize()
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(user_sources)
                .where(user_sources.c.user_id == user_id)
                .order_by(user_sources.c.category, user_sources.c.name)
            ).mappings()
            return [source_from_mapping(row) for row in rows]

    def get_user_source(self, source_id: str, *, user_id: str = DEFAULT_USER_ID) -> FeedSource:
        self.initialize()
        with self.engine.begin() as connection:
            row = connection.execute(
                select(user_sources).where(
                    user_sources.c.id == source_id.strip().lower(),
                    user_sources.c.user_id == user_id,
                )
            ).mappings().first()
        if row is None:
            raise FeedConfigError(f"Unknown source id: {source_id}")
        return source_from_mapping(row)

    def upsert_user_source(
        self, source: FeedSource, *, user_id: str = DEFAULT_USER_ID
    ) -> FeedSource:
        self.initialize()
        clean = normalize_source(source)
        now = utc_now_iso()
        values = user_source_values(clean, user_id=user_id, now=now)
        with self.engine.begin() as connection:
            exists = row_exists(connection, user_sources, id=clean.id, user_id=user_id)
            if exists:
                update_values = values.copy()
                update_values.pop("id")
                update_values.pop("user_id")
                update_values.pop("created_at")
                connection.execute(
                    update(user_sources)
                    .where(user_sources.c.id == clean.id, user_sources.c.user_id == user_id)
                    .values(**update_values)
                )
            else:
                connection.execute(insert(user_sources).values(**values))
        return self.get_user_source(clean.id, user_id=user_id)

    def delete_user_source(self, source_id: str, *, user_id: str = DEFAULT_USER_ID) -> None:
        self.initialize()
        clean_id = source_id.strip().lower()
        with self.engine.begin() as connection:
            result = connection.execute(
                user_sources.delete().where(
                    user_sources.c.id == clean_id,
                    user_sources.c.user_id == user_id,
                )
            )
        if result.rowcount == 0:
            raise FeedConfigError(f"Unknown source id: {source_id}")

    def toggle_user_source(self, source_id: str, *, user_id: str = DEFAULT_USER_ID) -> FeedSource:
        source = self.get_user_source(source_id, user_id=user_id)
        return self.upsert_user_source(
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
        self,
        source: FeedSource,
        items: Iterable[FeedItem],
        *,
        user_id: str = DEFAULT_USER_ID,
    ) -> AppendItemsResult:
        self.initialize()
        written = 0
        skipped = 0
        with self.engine.begin() as connection:
            for item in items:
                source_scope = "global" if source.global_source_id else "user"
                scoped_source_id = source.global_source_id or source.id
                item_id = item.id or sqlite_db.stable_item_id(
                    source_scope, scoped_source_id, item.identity
                )
                now = utc_now_iso()
                if not row_exists(connection, feed_items, id=item_id):
                    connection.execute(
                        insert(feed_items).values(
                            id=item_id,
                            source_id=scoped_source_id,
                            source_scope=source_scope,
                            category=item.category,
                            title=item.title,
                            url=item.url,
                            author=item.author,
                            published_at=item.published_at,
                            raw_description=item.raw_description,
                            excerpt=item.excerpt,
                            image_url=item.image_url,
                            content_html=item.content_html,
                            external_id=item.external_id,
                            created_at=item.created_at,
                            updated_at=now,
                        )
                    )
                    written += 1
                else:
                    skipped += 1

                state_id = sqlite_db.stable_state_id(user_id, item_id)
                if not row_exists(connection, user_feed_item_states, id=state_id):
                    connection.execute(
                        insert(user_feed_item_states).values(
                            id=state_id,
                            user_id=user_id,
                            feed_item_id=item_id,
                            is_read=False,
                            is_saved=False,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            self._update_source_fetch_status(
                connection, source.id, user_id=user_id, status="ok", error=None
            )
        return AppendItemsResult(written=written, skipped=skipped)

    def record_source_error(
        self, source: FeedSource, error: str, *, user_id: str = DEFAULT_USER_ID
    ) -> None:
        self.initialize()
        with self.engine.begin() as connection:
            self._update_source_fetch_status(
                connection, source.id, user_id=user_id, status="error", error=error[:1000]
            )

    def update_source_validation(
        self,
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
        self.initialize()
        with self.engine.begin() as connection:
            connection.execute(
                update(user_sources)
                .where(user_sources.c.id == source_id, user_sources.c.user_id == user_id)
                .values(
                    status=status,
                    validation_error=validation_error,
                    detected_feed_type=detected_feed_type,
                    example_item_count=example_item_count,
                    has_images=has_images,
                    has_descriptions=has_descriptions,
                    has_dates=has_dates,
                    last_checked_at=last_checked_at,
                    updated_at=last_checked_at,
                )
            )
        return self.get_user_source(source_id, user_id=user_id)

    def list_feed_items(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        category: str | None = None,
        source_id: str | None = None,
        search: str | None = None,
        saved_only: bool = False,
        limit: int = 200,
    ) -> list[FeedItem]:
        self.initialize()
        clauses = [user_feed_item_states.c.user_id == user_id]
        if category:
            clauses.append(feed_items.c.category == category)
        if source_id:
            clauses.append(or_(feed_items.c.source_id == source_id, user_sources.c.id == source_id))
        if search:
            pattern = f"%{search}%"
            clauses.append(
                or_(
                    feed_items.c.title.ilike(pattern),
                    feed_items.c.excerpt.ilike(pattern),
                    user_sources.c.name.ilike(pattern),
                )
            )
        if saved_only:
            clauses.append(user_feed_item_states.c.is_saved.is_(True))

        join_condition = and_(
            user_sources.c.user_id == user_feed_item_states.c.user_id,
            or_(
                and_(
                    feed_items.c.source_scope == "global",
                    user_sources.c.global_source_id == feed_items.c.source_id,
                ),
                and_(
                    feed_items.c.source_scope == "user",
                    user_sources.c.id == feed_items.c.source_id,
                ),
            ),
        )
        statement = (
            select(
                feed_items,
                user_sources.c.name.label("source_name"),
                user_feed_item_states.c.is_read,
                user_feed_item_states.c.is_saved,
            )
            .select_from(
                feed_items.join(
                    user_feed_item_states,
                    user_feed_item_states.c.feed_item_id == feed_items.c.id,
                ).outerjoin(user_sources, join_condition)
            )
            .where(*clauses)
            .order_by(func.coalesce(feed_items.c.published_at, feed_items.c.created_at).desc())
            .limit(limit)
        )
        with self.engine.begin() as connection:
            rows = connection.execute(statement).mappings()
            return [item_from_mapping(row) for row in rows]

    def _ensure_default_user(
        self, connection: Connection, user_id: str = DEFAULT_USER_ID
    ) -> None:
        now = utc_now_iso()
        if row_exists(connection, users, id=user_id):
            connection.execute(
                update(users).where(users.c.id == user_id).values(updated_at=now)
            )
            return
        connection.execute(
            insert(users).values(
                id=user_id,
                email=None,
                display_name="Personal",
                created_at=now,
                updated_at=now,
            )
        )

    def _seed_global_sources(
        self, connection: Connection, sources: Iterable[FeedSource]
    ) -> None:
        now = utc_now_iso()
        for source in sources:
            clean = normalize_source(source)
            global_values = global_source_values(clean, now=now)
            if row_exists(connection, global_sources, id=clean.id):
                update_values = global_values.copy()
                update_values.pop("id")
                update_values.pop("created_at")
                connection.execute(
                    update(global_sources)
                    .where(global_sources.c.id == clean.id)
                    .values(**update_values)
                )
            else:
                connection.execute(insert(global_sources).values(**global_values))

            if not row_exists(connection, user_sources, id=clean.id, user_id=DEFAULT_USER_ID):
                values = user_source_values(
                    clean,
                    user_id=DEFAULT_USER_ID,
                    now=now,
                    fetch_status="idle",
                )
                values["global_source_id"] = clean.id
                connection.execute(
                    insert(user_sources).values(**values)
                )
                continue

            row = connection.execute(
                select(user_sources).where(
                    user_sources.c.id == clean.id,
                    user_sources.c.user_id == DEFAULT_USER_ID,
                )
            ).mappings().one()
            status = row["status"]
            if clean.status == "unsupported_v1":
                status = "unsupported_v1"
            elif row["last_checked_at"] is None:
                status = clean.status
            connection.execute(
                update(user_sources)
                .where(user_sources.c.id == clean.id, user_sources.c.user_id == DEFAULT_USER_ID)
                .values(
                    source_type=clean.source_type,
                    status=status,
                    group_name=row["group_name"] or clean.group,
                    notes=row["notes"] or clean.notes,
                    updated_at=now,
                )
            )

    def _update_source_fetch_status(
        self,
        connection: Connection,
        source_id: str,
        *,
        user_id: str,
        status: str,
        error: str | None,
    ) -> None:
        now = utc_now_iso()
        connection.execute(
            update(user_sources)
            .where(user_sources.c.id == source_id, user_sources.c.user_id == user_id)
            .values(
                last_fetched_at=now,
                fetch_status=status,
                last_error=error,
                updated_at=now,
            )
        )


def repository_for_database_url(database_url: str | None, db_path: Path) -> FeedRepository:
    if database_url:
        return SQLAlchemyRepository(database_url)
    return SQLiteRepository(db_path)


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    return database_url


def row_exists(connection: Connection, table: Table, **where: object) -> bool:
    clauses = [table.c[name] == value for name, value in where.items()]
    return connection.execute(select(table.c.id).where(*clauses).limit(1)).first() is not None


def global_source_values(source: FeedSource, *, now: str) -> dict[str, object]:
    return {
        "id": source.id,
        "name": source.name,
        "default_category": source.category,
        "homepage_url": source.homepage_url,
        "feed_url": source.feed_url,
        "source_type": source.source_type,
        "default_enabled": source.enabled,
        "status": source.status,
        "last_checked_at": source.last_checked_at,
        "validation_error": source.validation_error,
        "detected_feed_type": source.detected_feed_type,
        "example_item_count": source.example_item_count,
        "has_images": source.has_images,
        "has_descriptions": source.has_descriptions,
        "has_dates": source.has_dates,
        "group_name": source.group,
        "notes": source.notes,
        "created_at": now,
        "updated_at": now,
    }


def user_source_values(
    source: FeedSource,
    *,
    user_id: str,
    now: str,
    fetch_status: str | None = None,
) -> dict[str, object]:
    return {
        "id": source.id,
        "user_id": user_id,
        "global_source_id": source.global_source_id,
        "name": source.name,
        "category": source.category,
        "homepage_url": source.homepage_url,
        "feed_url": source.feed_url,
        "source_type": source.source_type,
        "enabled": source.enabled,
        "status": source.status,
        "pull_frequency_minutes": source.pull_frequency_minutes,
        "last_fetched_at": source.last_fetched_at,
        "fetch_status": source.fetch_status or fetch_status or "idle",
        "last_error": source.last_error,
        "last_checked_at": source.last_checked_at,
        "validation_error": source.validation_error,
        "detected_feed_type": source.detected_feed_type,
        "example_item_count": source.example_item_count,
        "has_images": source.has_images,
        "has_descriptions": source.has_descriptions,
        "has_dates": source.has_dates,
        "group_name": source.group,
        "notes": source.notes,
        "created_at": source.created_at or now,
        "updated_at": now,
    }


def source_from_mapping(row: RowMapping) -> FeedSource:
    return FeedSource(
        id=str(row["id"]),
        name=str(row["name"]),
        category=str(row["category"]),
        homepage_url=row["homepage_url"],
        feed_url=row["feed_url"],
        source_type=str(row["source_type"]),
        enabled=bool(row["enabled"]),
        status=str(row["status"]),
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


def item_from_mapping(row: RowMapping) -> FeedItem:
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
