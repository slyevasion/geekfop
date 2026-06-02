from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from reddit_scraper.config import OxylabsProxyConfig, ScraperSettings, normalize_subreddit
from reddit_scraper.models import RedditPost, ScrapeRun, SubredditResult, utc_now_iso

LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

OLD_REDDIT_EXTRACTOR = """
() => Array.from(document.querySelectorAll('.thing.link')).map((el) => {
  const text = (selector) => el.querySelector(selector)?.textContent?.trim() || null;
  const attr = (selector, name) => el.querySelector(selector)?.getAttribute(name) || null;
  const titleEl = el.querySelector('a.title');
  const commentsEl = el.querySelector('a.comments');
  const timeEl = el.querySelector('time');
  return {
    title: titleEl?.textContent?.trim() || null,
    url: titleEl?.href || attr('a.title', 'href'),
    permalink: commentsEl?.href || attr('a.comments', 'href'),
    author: text('a.author'),
    score: text('.score.unvoted') || text('.score.likes') || text('.score.dislikes'),
    comment_count: text('a.comments'),
    created_at: timeEl?.getAttribute('datetime') || null,
  };
})
"""


class RedditScraper:
    def __init__(
        self,
        proxy: OxylabsProxyConfig,
        settings: ScraperSettings,
        logger: logging.Logger | None = None,
    ) -> None:
        self.proxy = proxy
        self.settings = settings
        self.logger = logger or LOGGER

    async def check_proxy(self) -> str:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self.settings.headless,
                proxy=self.proxy.to_playwright_proxy(),
            )
            try:
                context = await self._new_context(browser)
                page = await context.new_page()
                await page.goto(
                    "https://ip.oxylabs.io/location",
                    wait_until="domcontentloaded",
                    timeout=self.settings.nav_timeout_ms,
                )
                return (await page.locator("body").inner_text()).strip()
            finally:
                await browser.close()

    async def scrape(
        self, subreddits: Sequence[str] | None = None
    ) -> tuple[list[RedditPost], ScrapeRun]:
        started_at = utc_now_iso()
        target_subreddits = tuple(subreddits or self.settings.subreddits)
        all_posts: list[RedditPost] = []
        results: list[SubredditResult] = []

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self.settings.headless,
                proxy=self.proxy.to_playwright_proxy(),
            )
            try:
                context = await self._new_context(browser)
                page = await context.new_page()
                for subreddit in target_subreddits:
                    posts, result = await self.scrape_subreddit(page, subreddit)
                    all_posts.extend(posts)
                    results.append(result)
            finally:
                await browser.close()

        run = ScrapeRun(
            started_at=started_at,
            finished_at=utc_now_iso(),
            subreddits=tuple(results),
            total_posts=len(all_posts),
            output_path=str(self.settings.output_path),
        )
        return all_posts, run

    async def scrape_subreddit(
        self, page: Any, subreddit_name: str
    ) -> tuple[list[RedditPost], SubredditResult]:
        subreddit = normalize_subreddit(subreddit_name)
        posts: list[RedditPost] = []
        seen: set[str] = set()
        visited_urls: list[str] = []
        current_url = subreddit_url(self.settings.base_url, subreddit)

        try:
            for _page_number in range(self.settings.pages_per_subreddit):
                if len(posts) >= self.settings.max_posts_per_subreddit:
                    break

                visited_urls.append(current_url)
                await self._goto_listing(page, current_url)
                raw_posts = await page.evaluate(OLD_REDDIT_EXTRACTOR)

                for raw_post in raw_posts:
                    post = build_post(raw_post, subreddit=subreddit, source_url=current_url)
                    if not post or post.identity in seen:
                        continue
                    seen.add(post.identity)
                    posts.append(post)
                    if len(posts) >= self.settings.max_posts_per_subreddit:
                        break

                next_url = await page.locator("span.next-button a").first.get_attribute("href")
                if not next_url:
                    break
                current_url = urljoin(current_url, next_url)
                await asyncio.sleep(1)

            result = SubredditResult(
                subreddit=subreddit,
                requested=self.settings.max_posts_per_subreddit,
                collected=len(posts),
                source_urls=tuple(visited_urls),
            )
            return posts, result
        except Exception as exc:  # noqa: BLE001 - error text belongs in run metadata.
            self.logger.warning("Failed scraping r/%s: %s", subreddit, exc)
            result = SubredditResult(
                subreddit=subreddit,
                requested=self.settings.max_posts_per_subreddit,
                collected=len(posts),
                source_urls=tuple(visited_urls),
                error=f"{type(exc).__name__}: {exc}",
            )
            return posts, result

    async def _new_context(self, browser: Any) -> Any:
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York",
        )
        context.set_default_timeout(self.settings.nav_timeout_ms)
        await context.route("**/*", self._block_resources)
        return context

    async def _block_resources(self, route: Any) -> None:
        if route.request.resource_type in self.settings.block_resource_types:
            await route.abort()
            return
        await route.continue_()

    async def _goto_listing(self, page: Any, url: str) -> None:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.settings.nav_timeout_ms,
        )
        if response and response.status >= 400:
            raise RuntimeError(f"HTTP {response.status} for {url}")
        try:
            await page.wait_for_selector(".thing.link, .errorpage", timeout=15_000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"No listing posts found for {url}") from exc

        if await page.locator(".errorpage").count():
            title = await page.locator(".errorpage").first.inner_text()
            raise RuntimeError(normalize_whitespace(title))


def subreddit_url(base_url: str, subreddit: str) -> str:
    return f"{base_url.rstrip('/')}/r/{normalize_subreddit(subreddit)}/new/"


def build_post(raw: dict[str, Any], *, subreddit: str, source_url: str) -> RedditPost | None:
    title = normalize_whitespace(raw.get("title"))
    if not title:
        return None

    return RedditPost(
        subreddit=subreddit,
        title=title,
        url=normalize_url(raw.get("url"), source_url),
        permalink=normalize_url(raw.get("permalink"), source_url),
        author=normalize_whitespace(raw.get("author")),
        score=parse_score(raw.get("score")),
        comment_count=parse_comment_count(raw.get("comment_count")),
        created_at=normalize_whitespace(raw.get("created_at")),
        source_url=source_url,
    )


def normalize_url(url: str | None, base_url: str) -> str | None:
    cleaned = normalize_whitespace(url)
    if not cleaned:
        return None
    return urljoin(base_url, cleaned)


def normalize_whitespace(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def parse_score(value: str | None) -> int | None:
    text = normalize_whitespace(value)
    if not text or text in {"score hidden", "•"}:
        return None
    lowered = text.lower().replace(",", "")
    if "k" in lowered:
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*k", lowered)
        if match:
            return int(float(match.group(1)) * 1000)
    match = re.search(r"-?\d+", lowered)
    return int(match.group(0)) if match else None


def parse_comment_count(value: str | None) -> int | None:
    text = normalize_whitespace(value)
    if not text:
        return None
    lowered = text.lower().replace(",", "")
    if "comment" not in lowered:
        return None
    match = re.search(r"\d+", lowered)
    return int(match.group(0)) if match else 0
