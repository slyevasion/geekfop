from __future__ import annotations

import sys
import unittest
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reddit_scraper.scraper import OLD_REDDIT_EXTRACTOR, build_post  # noqa: E402


class PlaywrightExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_reddit_extraction_in_browser(self) -> None:
        html = """
        <html>
          <body>
            <div class="thing link">
              <a class="title" href="/r/LocalLLaMA/comments/abc/test/">Test post</a>
              <a class="author">poster</a>
              <span class="score unvoted">42 points</span>
              <a class="comments" href="/r/LocalLLaMA/comments/abc/test/">7 comments</a>
              <time datetime="2026-05-18T00:00:00+00:00"></time>
            </div>
          </body>
        </html>
        """

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.set_content(html)
                    raw_posts = await page.evaluate(OLD_REDDIT_EXTRACTOR)
                finally:
                    await browser.close()
        except PlaywrightError as exc:
            raise unittest.SkipTest(f"Playwright browser unavailable: {exc}") from exc

        self.assertEqual(len(raw_posts), 1)
        post = build_post(
            raw_posts[0],
            subreddit="LocalLLaMA",
            source_url="https://old.reddit.com/r/LocalLLaMA/new/",
        )

        self.assertIsNotNone(post)
        assert post is not None
        self.assertEqual(post.title, "Test post")
        self.assertEqual(post.author, "poster")
        self.assertEqual(post.score, 42)
        self.assertEqual(post.comment_count, 7)


if __name__ == "__main__":
    unittest.main()
