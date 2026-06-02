from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feed_app.models import FeedItem  # noqa: E402
from feed_app.repository import SQLAlchemyRepository  # noqa: E402


class SQLAlchemyRepositoryTests(unittest.TestCase):
    def test_seed_append_and_dedupe_items(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo = SQLAlchemyRepository(f"sqlite+pysqlite:///{Path(tmpdir) / 'feed.db'}")
            repo.initialize()
            source = repo.list_user_sources()[0]
            item = FeedItem(
                source_id=source.global_source_id or source.id,
                source_scope="global" if source.global_source_id else "user",
                source_name=source.name,
                category=source.category,
                title="Hosted item",
                url="https://example.com/hosted",
                excerpt="Hosted excerpt",
                external_id="hosted-1",
            )

            first = repo.append_feed_items(source, [item])
            second = repo.append_feed_items(source, [item])
            items = repo.list_feed_items()

            self.assertEqual(source.global_source_id, source.id)
            self.assertEqual(first.written, 1)
            self.assertEqual(second.skipped, 1)
            self.assertEqual(items[0].title, "Hosted item")
            self.assertEqual(items[0].source_name, source.name)

    def test_update_validation(self) -> None:
        with TemporaryDirectory() as tmpdir:
            repo = SQLAlchemyRepository(f"sqlite+pysqlite:///{Path(tmpdir) / 'feed.db'}")
            source = repo.list_user_sources()[0]

            updated = repo.update_source_validation(
                source.id,
                status="working",
                validation_error=None,
                detected_feed_type="rss",
                example_item_count=3,
                has_images=True,
                has_descriptions=True,
                has_dates=True,
                last_checked_at="2026-06-02T00:00:00+00:00",
            )

            self.assertEqual(updated.status, "working")
            self.assertEqual(updated.detected_feed_type, "rss")
            self.assertEqual(updated.example_item_count, 3)
            self.assertTrue(updated.has_images)


if __name__ == "__main__":
    unittest.main()
