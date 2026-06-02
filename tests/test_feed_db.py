from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feed_app.db import (  # noqa: E402
    append_feed_items,
    initialize_database,
    list_feed_items,
    list_user_sources,
    toggle_user_source,
    upsert_user_source,
)
from feed_app.models import FeedItem, FeedSource  # noqa: E402


class FeedDatabaseTests(unittest.TestCase):
    def test_schema_has_multi_user_tables_and_default_user(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "feed.sqlite3"
            initialize_database(db_path)

            with sqlite3.connect(db_path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                user = connection.execute("SELECT id FROM users WHERE id = 'local'").fetchone()

            self.assertTrue(
                {
                    "users",
                    "global_sources",
                    "user_sources",
                    "feed_items",
                    "user_feed_item_states",
                }.issubset(tables)
            )
            self.assertEqual(user, ("local",))
            self.assertGreater(len(list_user_sources(db_path)), 0)

    def test_user_source_edit_and_toggle(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "feed.sqlite3"
            source = FeedSource(
                id="custom",
                name="Custom",
                category="AI_TECH",
                homepage_url="https://example.com",
                feed_url="https://example.com/feed.xml",
                pull_frequency_minutes=120,
            )

            saved = upsert_user_source(db_path, source)
            toggled = toggle_user_source(db_path, saved.id)

            self.assertEqual(saved.homepage_url, "https://example.com")
            self.assertEqual(saved.pull_frequency_minutes, 120)
            self.assertFalse(toggled.enabled)

    def test_feed_items_are_shared_with_user_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "feed.sqlite3"
            source = list_user_sources(db_path)[0]
            item = FeedItem(
                source_id=source.global_source_id or source.id,
                source_scope="global" if source.global_source_id else "user",
                source_name=source.name,
                category=source.category,
                title="Item",
                url="https://example.com/item",
                excerpt="Short excerpt",
                image_url="https://example.com/image.jpg",
                external_id="external-1",
            )

            first = append_feed_items(db_path, source, [item])
            second = append_feed_items(db_path, source, [item])
            items = list_feed_items(db_path)

            self.assertEqual(first.written, 1)
            self.assertEqual(second.skipped, 1)
            self.assertEqual(items[0].excerpt, "Short excerpt")
            self.assertEqual(items[0].image_url, "https://example.com/image.jpg")


if __name__ == "__main__":
    unittest.main()
