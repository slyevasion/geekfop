# Oxylabs + Playwright Subreddit Scraper Plan

## Docs Findings

- Oxylabs Residential Proxies use a backconnect endpoint at `pr.oxylabs.io:7777`.
- Credentials are passed as proxy username/password, not target HTTP auth.
- Sticky sessions use username parameters like `sessid-*` and `sesstime-*`.
- Playwright supports proxy config globally at browser launch or per browser context.
- Oxylabs recommends blocking images, stylesheets, media, and fonts to reduce traffic.

## Integration Shape

- Read proxy values from `.env`/environment only.
- Launch Chromium with `proxy={server, username, password}`.
- Scrape public subreddit listing pages from `old.reddit.com` for stable selectors.
- Extract normalized post records and append JSONL with permalink dedupe.
- Write per-run metadata for counts, errors, and duration.

## Compliance Guardrails

- Do not commit proxy credentials.
- Only scrape public pages.
- Use bounded page counts and post limits.
- Record and back off on HTTP errors, CAPTCHA, empty pages, and timeouts.
