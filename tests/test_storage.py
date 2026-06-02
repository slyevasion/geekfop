from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reddit_scraper.models import RedditPost, ScrapeRun, SubredditResult  # noqa: E402
from reddit_scraper.storage import (  # noqa: E402
    append_posts_jsonl,
    load_seen_identities,
    write_run_metadata,
)


class StorageTests(unittest.TestCase):
    def test_append_posts_jsonl_dedupes_by_permalink(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "posts.jsonl"
            posts = [
                RedditPost(subreddit="LocalLLaMA", title="A", permalink="https://reddit.test/a"),
                RedditPost(subreddit="LocalLLaMA", title="A duplicate", permalink="https://reddit.test/a"),
                RedditPost(subreddit="LocalLLaMA", title="B", permalink="https://reddit.test/b"),
            ]

            result = append_posts_jsonl(posts, path)

            self.assertEqual(result.written, 2)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(load_seen_identities(path), {"https://reddit.test/a", "https://reddit.test/b"})

    def test_write_run_metadata(self) -> None:
        with TemporaryDirectory() as tmpdir:
            run = ScrapeRun(
                started_at="2026-05-18T00:00:00+00:00",
                finished_at="2026-05-18T00:00:01+00:00",
                subreddits=(SubredditResult("LocalLLaMA", 1, 1, ("https://old.reddit.com/r/LocalLLaMA/new/",)),),
                total_posts=1,
                output_path="posts.jsonl",
            )

            path = write_run_metadata(run, Path(tmpdir))

            self.assertTrue(path.exists())
            self.assertIn("LocalLLaMA", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
