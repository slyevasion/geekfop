from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from feed_app.defaults import CATEGORIES, load_source_definitions  # noqa: E402
from feed_app.models import FeedConfigError, FeedSource  # noqa: E402
from feed_app.sources import (  # noqa: E402
    default_sources,
    load_sources,
    normalize_source,
    remove_source,
    save_sources,
    update_source,
)


class FeedSourceTests(unittest.TestCase):
    def test_source_config_has_required_phase_2_fields(self) -> None:
        required = {
            "id",
            "name",
            "category",
            "homepage_url",
            "feed_url",
            "source_type",
            "enabled",
            "status",
            "notes",
        }

        for source in load_source_definitions():
            self.assertTrue(required.issubset(source), source.get("name"))
            self.assertIn(source["category"], CATEGORIES)

    def test_default_sources_cover_categories_and_requested_names(self) -> None:
        sources = default_sources()
        categories = {source.category for source in sources}
        names = {source.name for source in sources}

        self.assertEqual(categories, set(CATEGORIES))
        self.assertEqual(len({source.id for source in sources}), len(sources))
        self.assertIn("OpenAI News", names)
        self.assertIn("ComfyUI GitHub Releases", names)
        self.assertIn("ScienceDaily Health & Medicine", names)
        self.assertIn("Interconnects AI", names)
        self.assertIn("FashionNetwork", names)
        self.assertIn("European Parliament", names)

    def test_enabled_source_requires_feed_url(self) -> None:
        with self.assertRaises(FeedConfigError):
            normalize_source(FeedSource(id="no-url", name="No URL", category="AI_TECH"))

        source = normalize_source(
            FeedSource(id="no-url", name="No URL", category="AI_TECH", enabled=False)
        )
        self.assertFalse(source.enabled)
        self.assertIsNone(source.feed_url)

    def test_save_load_update_remove_round_trip(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sources.json"
            sources = [
                FeedSource(
                    id="one",
                    name="One",
                    category="AI_TECH",
                    feed_url="https://example.com/feed.xml",
                )
            ]

            save_sources(sources, path)
            loaded = load_sources(path)
            self.assertEqual(loaded[0].id, "one")

            updated = update_source(loaded, "one", name="One Updated", enabled=False)
            self.assertEqual(updated[0].name, "One Updated")
            self.assertFalse(updated[0].enabled)

            removed = remove_source(updated, "one")
            self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
