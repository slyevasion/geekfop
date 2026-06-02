from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feed_app.db import get_user_source, upsert_user_source  # noqa: E402
from feed_app.models import FeedSource  # noqa: E402
from feed_app.validator import validate_source, validate_source_to_db  # noqa: E402


class FeedValidatorTests(unittest.TestCase):
    def test_missing_feed_url_needs_feed_url(self) -> None:
        source = FeedSource(id="manual", name="Manual", category="AI_TECH", enabled=False)

        result = validate_source(source)

        self.assertEqual(result.status, "needs_feed_url")
        self.assertEqual(result.detected_feed_type, None)

    def test_valid_rss_detects_fields(self) -> None:
        source = FeedSource(
            id="rss",
            name="RSS",
            category="AI_TECH",
            feed_url="https://example.com/feed.xml",
        )
        xml = b"""
        <rss version="2.0">
          <channel>
            <item>
              <title>Post</title>
              <link>https://example.com/post</link>
              <pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate>
              <description><![CDATA[<p>Body <img src="https://example.com/a.jpg"></p>]]></description>
            </item>
          </channel>
        </rss>
        """

        with patch("feed_app.validator.fetch_feed", return_value=xml):
            result = validate_source(source)

        self.assertEqual(result.status, "working")
        self.assertEqual(result.detected_feed_type, "rss")
        self.assertEqual(result.example_item_count, 1)
        self.assertTrue(result.has_titles)
        self.assertTrue(result.has_urls)
        self.assertTrue(result.has_dates)
        self.assertTrue(result.has_descriptions)
        self.assertTrue(result.has_images)

    def test_validate_source_to_db_updates_source(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "feed.sqlite3"
            source = FeedSource(
                id="rss",
                name="RSS",
                category="AI_TECH",
                feed_url="https://example.com/feed.xml",
            )
            upsert_user_source(db_path, source)
            xml = b"""
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>Atom Post</title>
                <id>tag:example.com,2026:1</id>
                <updated>2026-06-01T10:00:00Z</updated>
                <link href="https://example.com/post" />
                <summary>Body</summary>
              </entry>
            </feed>
            """

            with patch("feed_app.validator.fetch_feed", return_value=xml):
                validate_source_to_db(db_path, "rss")

            updated = get_user_source(db_path, "rss")
            self.assertEqual(updated.status, "working")
            self.assertEqual(updated.detected_feed_type, "atom")
            self.assertEqual(updated.example_item_count, 1)


if __name__ == "__main__":
    unittest.main()
