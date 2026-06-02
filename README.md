# AI Technology Recon Gatherer

Local recon tools:

- Multi-source RSS/Atom feed app for categorized preview cards.
- Playwright scraper for public subreddit listings through Oxylabs residential proxies.

## Feed App V1

The feed app focuses only on source configuration, RSS/Atom ingestion,
categorization, preview cards, source management, and a basic local UI.

Not included yet: AI summarization, filtering, ranking, semantic search, Obsidian export,
notifications, or browser scraping.

### Feed Categories

- `AI_TECH`
- `CREATIVE_AI`
- `PSYCHOLOGY`
- `HEALTH_LOOKS`
- `FASHION`
- `MARKETS`
- `CRYPTO`
- `DATA_SCIENCE`
- `POLICY`

### Feed Setup

```bash
uv sync
PYTHONPATH=src uv run python -m feed_app init-db
```

This creates `data/feed_app.sqlite3` and seeds default global sources plus a personal
`local` user. Some sources start disabled when an official feed URL is not confirmed yet;
add or edit them in the UI or CLI.

Bundled source config lives at `config/feed_sources.json`. Each source includes category,
homepage URL, feed URL, source type, enabled state, validation status, notes, and optional group.

The SQLite schema is already shaped for multi-user support:

- `users`
- `global_sources`
- `user_sources`
- `feed_items`
- `user_feed_item_states`

### Feed Sync

```bash
PYTHONPATH=src uv run python -m feed_app sync --limit-per-source 25
```

Output:

- Items write to `data/feed_app.sqlite3`.
- Run metadata writes to `data/feed_runs/<timestamp>.json`.
- Dedupe key is external feed id first, URL second, then source/title.

### Feed UI

```bash
PYTHONPATH=src uv run python -m feed_app serve
```

Open `http://127.0.0.1:8765`.

The UI supports:

- Mobile-first Home, Categories, Sources, Saved, and Settings navigation.
- Category/source browsing.
- Visually distinct category cards.
- Preview cards with title, source, date, excerpt, image when available, and original link.
- Sync button for enabled feeds.
- Source add/edit/delete/enable/disable, homepage URL, feed URL, notes, and pull frequency.

### Hosted Feed API

The hosted path uses FastAPI and keeps the same UI/API routes as the local server.

```bash
PYTHONPATH=src uv run uvicorn feed_app.api:app --reload
```

For Vercel Hobby deployment, configure a managed Postgres database such as Neon and set:

```bash
DATABASE_URL=postgresql://...
CRON_SECRET=long-random-cron-secret
APP_ACCESS_TOKEN=long-random-personal-login-token
SYNC_TIMEOUT_SECONDS=12
SYNC_LIMIT_PER_SOURCE=20
```

`DATABASE_URL` switches the hosted API from local SQLite to SQLAlchemy/Postgres. On startup,
the app creates the same tables and seeds sources from `config/feed_sources.json`.

`vercel.json` defines one daily cron run at `/api/cron/sync`. Vercel calls it with the
`CRON_SECRET` authorization header. The manual Sync/Refresh buttons still work when you open
the app. If `APP_ACCESS_TOKEN` is set, the browser asks for it once and stores it in local
storage as `feedAppToken`.

Optional sync filters:

```bash
SYNC_CATEGORY=AI_TECH
SYNC_SOURCE_IDS=openai-news,interconnects-ai
```

### Feed Source CLI

```bash
PYTHONPATH=src uv run python -m feed_app list-sources
PYTHONPATH=src uv run python -m feed_app add-source \
  --id my-feed \
  --name "My Feed" \
  --category AI_TECH \
  --homepage-url https://example.com \
  --feed-url https://example.com/feed.xml
PYTHONPATH=src uv run python -m feed_app set-source my-feed --enabled false
PYTHONPATH=src uv run python -m feed_app remove-source my-feed
```

Validate one or all feeds without scraping or browser automation:

```bash
PYTHONPATH=src uv run python -m feed_app validate-source openai-news
PYTHONPATH=src uv run python -m feed_app validate-sources --timeout 10 --workers 8
```

Validation stores per-source fields in SQLite: `status`, `last_checked_at`,
`validation_error`, `detected_feed_type`, `example_item_count`, `has_images`,
`has_descriptions`, and `has_dates`.

## Reddit Scraper

## Targets

- `r/LocalLLaMA`
- `r/LocalLLM`
- `r/AI_Agents`
- `r/PromptEngineering`
- `r/automation`
- `r/AI_developers`

## Setup

```bash
uv sync
uv run playwright install chromium
```

Populate `.env` from `.env.example`. Put real Oxylabs credentials in local `.env` only.

## Run

```bash
PYTHONPATH=src uv run python -m reddit_scraper --limit 50
```

Check proxy before scraping:

```bash
PYTHONPATH=src uv run python -m reddit_scraper --check-proxy
```

Scrape one subreddit:

```bash
PYTHONPATH=src uv run python -m reddit_scraper --subreddit LocalLLaMA --limit 25
```

## Output

- Posts append to `data/reddit_posts.jsonl`.
- Run metadata writes to `data/runs/<timestamp>.json`.
- Dedupe key is permalink first, URL second.

## Benchmark

Rank scraped posts with the local heuristic benchmark:

```bash
PYTHONPATH=src uv run python -m reddit_scraper.benchmark \
  --input data/reddit_posts.jsonl \
  --output data/benchmarks/reddit_post_benchmark.md \
  --jsonl-output data/benchmarks/reddit_post_benchmark.jsonl
```

Run the optional OpenCode/LangChain critic against the top heuristic results:

```bash
PYTHONPATH=src uv run python -m reddit_scraper.benchmark \
  --critic \
  --critic-limit 20 \
  --input data/reddit_posts.jsonl \
  --output data/benchmarks/reddit_post_benchmark.md \
  --jsonl-output data/benchmarks/reddit_post_benchmark.jsonl
```

Set `OPENCODE_API_KEY` in local `.env`. Optional overrides: `OPENCODE_BASE_URL`, `OPENCODE_MODEL`.

## Notes

- Default base URL is `https://old.reddit.com` for stable markup and lower bandwidth.
- Oxylabs proxy credentials are read from environment only.
- The scraper blocks image, stylesheet, media, and font requests to reduce traffic.
- Live scraping can fail on Reddit rate limits, CAPTCHA, or unavailable subreddits; failures are recorded in run metadata.
