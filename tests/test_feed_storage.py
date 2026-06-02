from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feed_app.models import FeedItem  # noqa: E402
from feed_app.storage import append_items_jsonl, load_seen_identities, read_items  # noqa: E402


class FeedStorageTests(unittest.TestCase):
    def test_append_items_jsonl_dedupes_by_identity(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "items.jsonl"
            items = [
                FeedItem(
                    source_id="src",
                    source_name="Source",
                    category="AI_TECH",
                    title="A",
                    url="https://example.com/a",
                ),
                FeedItem(
                    source_id="src",
                    source_name="Source",
                    category="AI_TECH",
                    title="A duplicate",
                    url="https://example.com/a",
                ),
                FeedItem(
                    source_id="src",
                    source_name="Source",
                    category="AI_TECH",
                    title="B",
                    external_id="b-id",
                ),
            ]

            result = append_items_jsonl(items, path)

            self.assertEqual(result.written, 2)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(load_seen_identities(path), {"https://example.com/a", "b-id"})
            self.assertEqual(len(read_items(path)), 2)


if __name__ == "__main__":
    unittest.main()
