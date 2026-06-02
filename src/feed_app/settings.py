from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from feed_app.defaults import DEFAULT_DB_PATH, DEFAULT_RUN_DIR, DEFAULT_USER_ID


@dataclass(frozen=True)
class AppSettings:
    database_url: str | None
    sqlite_db_path: Path
    run_dir: Path
    user_id: str
    cron_secret: str | None
    app_access_token: str | None
    sync_timeout_seconds: float
    sync_limit_per_source: int
    sync_category: str | None
    sync_source_ids: tuple[str, ...]

    @property
    def uses_managed_database(self) -> bool:
        return bool(self.database_url)


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings(
        database_url=_optional_env("DATABASE_URL"),
        sqlite_db_path=Path(os.environ.get("FEED_APP_DB_PATH", str(DEFAULT_DB_PATH))),
        run_dir=Path(os.environ.get("FEED_APP_RUN_DIR", str(DEFAULT_RUN_DIR))),
        user_id=os.environ.get("FEED_APP_USER_ID", DEFAULT_USER_ID),
        cron_secret=_optional_env("CRON_SECRET"),
        app_access_token=_optional_env("APP_ACCESS_TOKEN"),
        sync_timeout_seconds=_float_env("SYNC_TIMEOUT_SECONDS", 12.0),
        sync_limit_per_source=_int_env("SYNC_LIMIT_PER_SOURCE", 20),
        sync_category=_optional_env("SYNC_CATEGORY"),
        sync_source_ids=_csv_env("SYNC_SOURCE_IDS"),
    )


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return int(value)


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return float(value)


def _csv_env(name: str) -> tuple[str, ...]:
    value = os.environ.get(name, "").strip()
    if not value:
        return ()
    return tuple(part.strip().lower() for part in value.split(",") if part.strip())
