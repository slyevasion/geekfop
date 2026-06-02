from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reddit_scraper.scraper import (  # noqa: E402
    build_post,
    parse_comment_count,
    parse_score,
    subreddit_url,
)


class ScraperHelperTests(unittest.TestCase):
    def test_subreddit_url(self) -> None:
        self.assertEqual(
            subreddit_url("https://old.reddit.com", "r/LocalLLaMA"),
            "https://old.reddit.com/r/LocalLLaMA/new/",
        )

    def test_parse_score(self) -> None:
        self.assertEqual(parse_score("1,234 points"), 1234)
        self.assertEqual(parse_score("1.5k points"), 1500)
        self.assertIsNone(parse_score("score hidden"))

    def test_parse_comment_count(self) -> None:
        self.assertEqual(parse_comment_count("32 comments"), 32)
        self.assertEqual(parse_comment_count("comment"), 0)
        self.assertIsNone(parse_comment_count("share"))

    def test_build_post(self) -> None:
        post = build_post(
            {
                "title": " Test title ",
                "url": "/r/LocalLLaMA/comments/abc/test/",
                "permalink": "/r/LocalLLaMA/comments/abc/test/",
                "author": "user",
                "score": "12 points",
                "comment_count": "3 comments",
                "created_at": "2026-05-18T00:00:00+00:00",
            },
            subreddit="LocalLLaMA",
            source_url="https://old.reddit.com/r/LocalLLaMA/new/",
        )

        self.assertIsNotNone(post)
        assert post is not None
        self.assertEqual(post.title, "Test title")
        self.assertEqual(post.score, 12)
        self.assertEqual(post.comment_count, 3)
        self.assertEqual(post.permalink, "https://old.reddit.com/r/LocalLLaMA/comments/abc/test/")


if __name__ == "__main__":
    unittest.main()
