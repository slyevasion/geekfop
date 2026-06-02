from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feed_app.ingest import parse_feed  # noqa: E402
from feed_app.models import FeedSource  # noqa: E402
from feed_app.sources import normalize_source  # noqa: E402


class FeedIngestTests(unittest.TestCase):
    def test_parse_rss_item(self) -> None:
        source = sample_source()
        xml = b"""
        <rss version="2.0">
          <channel>
            <title>Example</title>
            <item>
              <title>RSS title</title>
              <link>https://example.com/post</link>
              <guid>rss-1</guid>
              <pubDate>Sun, 31 May 2026 10:00:00 GMT</pubDate>
              <description><![CDATA[<p>Hello <strong>world</strong></p>]]></description>
              <category>Tools</category>
            </item>
          </channel>
        </rss>
        """

        items = parse_feed(xml, source)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "RSS title")
        self.assertEqual(items[0].url, "https://example.com/post")
        self.assertEqual(items[0].external_id, "rss-1")
        self.assertEqual(items[0].summary, "Hello world")
        self.assertEqual(items[0].tags, ("Tools",))

    def test_parse_atom_entry(self) -> None:
        source = sample_source()
        xml = b"""
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Atom title</title>
            <id>tag:example.com,2026:1</id>
            <updated>2026-05-31T10:00:00Z</updated>
            <link href="https://example.com/atom-post" rel="alternate" />
            <summary>Short atom summary</summary>
            <category term="Research" />
          </entry>
        </feed>
        """

        items = parse_feed(xml, source)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Atom title")
        self.assertEqual(items[0].url, "https://example.com/atom-post")
        self.assertEqual(items[0].external_id, "tag:example.com,2026:1")
        self.assertEqual(items[0].summary, "Short atom summary")
        self.assertEqual(items[0].tags, ("Research",))


def sample_source() -> FeedSource:
    return normalize_source(
        FeedSource(
            id="example",
            name="Example",
            category="AI_TECH",
            feed_url="https://example.com/feed.xml",
        )
    )


if __name__ == "__main__":
    unittest.main()
