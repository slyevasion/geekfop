from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from reddit_scraper.config import (
    ConfigError,
    OxylabsProxyConfig,
    ScraperSettings,
    load_dotenv,
    normalize_subreddits,
)
from reddit_scraper.scraper import RedditScraper
from reddit_scraper.storage import append_posts_jsonl, write_run_metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape public subreddit listings with Playwright."
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env file.")
    parser.add_argument(
        "--subreddit",
        action="append",
        dest="subreddits",
        help="Subreddit name. May be passed more than once. Defaults to configured targets.",
    )
    parser.add_argument("--limit", type=int, help="Max posts per subreddit.")
    parser.add_argument("--pages", type=int, help="Max listing pages per subreddit.")
    parser.add_argument("--output", type=Path, help="JSONL output path.")
    parser.add_argument("--run-dir", type=Path, help="Run metadata directory.")
    parser.add_argument("--base-url", help="Reddit base URL. Defaults to old.reddit.com.")
    parser.add_argument("--headful", action="store_true", help="Run browser with UI.")
    parser.add_argument("--check-proxy", action="store_true", help="Verify Oxylabs proxy and exit.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging level.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s %(message)s")

    try:
        load_dotenv(args.env_file)
        proxy = OxylabsProxyConfig.from_env()
        settings = build_settings(args)
    except ConfigError as exc:
        parser.error(str(exc))

    if args.check_proxy:
        return asyncio.run(run_proxy_check(proxy, settings))
    return asyncio.run(run_scrape(proxy, settings))


def build_settings(args: argparse.Namespace) -> ScraperSettings:
    settings = ScraperSettings.from_env()
    subreddits = normalize_subreddits(args.subreddits) if args.subreddits else None
    return settings.with_overrides(
        subreddits=subreddits,
        limit=args.limit,
        pages=args.pages,
        output_path=args.output,
        run_dir=args.run_dir,
        headless=not args.headful,
        base_url=args.base_url,
    )


async def run_proxy_check(proxy: OxylabsProxyConfig, settings: ScraperSettings) -> int:
    scraper = RedditScraper(proxy=proxy, settings=settings)
    body = await scraper.check_proxy()
    print(body)
    return 0


async def run_scrape(proxy: OxylabsProxyConfig, settings: ScraperSettings) -> int:
    logging.info("Using proxy %s", proxy.safe_label())
    logging.info("Scraping %s", ", ".join(f"r/{name}" for name in settings.subreddits))

    scraper = RedditScraper(proxy=proxy, settings=settings)
    posts, run = await scraper.scrape()
    append_result = append_posts_jsonl(posts, settings.output_path)
    metadata_path = write_run_metadata(run, settings.run_dir)

    logging.info(
        "Collected %s posts; wrote %s new, skipped %s duplicates",
        len(posts),
        append_result.written,
        append_result.skipped,
    )
    logging.info("Run metadata: %s", metadata_path)

    failures = [result for result in run.subreddits if result.error]
    if failures:
        for failure in failures:
            logging.warning("r/%s failed: %s", failure.subreddit, failure.error)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
