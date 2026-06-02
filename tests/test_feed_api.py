from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feed_app.api import create_app  # noqa: E402
from feed_app.models import IngestRun  # noqa: E402
from feed_app.repository import SQLiteRepository  # noqa: E402
from feed_app.settings import AppSettings  # noqa: E402


class FeedApiTests(unittest.TestCase):
    def test_app_access_token_protects_api(self) -> None:
        with make_client() as client:
            blocked = client.get("/api/categories")
            allowed = client.get("/api/categories", headers={"X-Feed-App-Token": "app-token"})

            self.assertEqual(blocked.status_code, 401)
            self.assertEqual(allowed.status_code, 200)
            self.assertIn("AI_TECH", allowed.json()["categories"])

    def test_cron_secret_protects_cron_sync(self) -> None:
        run = IngestRun(
            started_at="2026-06-02T00:00:00+00:00",
            finished_at="2026-06-02T00:00:01+00:00",
            output_path="managed-db",
            sources=(),
        )
        with make_client() as client:
            blocked = client.get("/api/cron/sync")
            with patch("feed_app.api.run_sync", return_value=run) as sync:
                allowed = client.get(
                    "/api/cron/sync",
                    headers={"Authorization": "Bearer cron-token"},
                )

            self.assertEqual(blocked.status_code, 401)
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.json()["run"]["output_path"], "managed-db")
            sync.assert_called_once()


def make_client() -> TestClient:
    tmpdir = TemporaryDirectory()
    db_path = Path(tmpdir.name) / "feed.sqlite3"
    settings = AppSettings(
        database_url=None,
        sqlite_db_path=db_path,
        run_dir=Path(tmpdir.name) / "runs",
        user_id="local",
        cron_secret="cron-token",
        app_access_token="app-token",
        sync_timeout_seconds=1.0,
        sync_limit_per_source=1,
        sync_category=None,
        sync_source_ids=(),
    )
    client = TestClient(create_app(settings, SQLiteRepository(db_path)))
    client._tmpdir = tmpdir  # type: ignore[attr-defined]
    return client


if __name__ == "__main__":
    unittest.main()
