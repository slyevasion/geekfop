# ruff: noqa: E501
from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from feed_app.db import (
    delete_user_source,
    get_user_source,
    initialize_database,
    list_feed_items,
    list_user_sources,
    toggle_user_source,
    upsert_user_source,
)
from feed_app.defaults import CATEGORIES, DEFAULT_USER_ID
from feed_app.ingest import ingest_sources
from feed_app.models import FeedConfigError, FeedSource
from feed_app.sources import parse_bool
from feed_app.validator import validate_all_sources_to_db, validate_source_to_db


@dataclass(frozen=True)
class FeedAppState:
    db_path: Path
    run_dir: Path
    user_id: str = DEFAULT_USER_ID


class FeedHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], state: FeedAppState) -> None:
        self.state = state
        super().__init__(server_address, FeedRequestHandler)


class FeedRequestHandler(BaseHTTPRequestHandler):
    server: FeedHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/":
                self.send_html(APP_HTML)
            elif path == "/api/categories":
                self.send_json({"categories": list(CATEGORIES)})
            elif path == "/api/sources":
                sources = list_user_sources(self.server.state.db_path, user_id=self.server.state.user_id)
                self.send_json({"sources": [source.to_dict() for source in sources]})
            elif path == "/api/items":
                self.send_json(self.api_items(parsed.query))
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except FeedConfigError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/sync":
                sources = list_user_sources(self.server.state.db_path, user_id=self.server.state.user_id)
                run = ingest_sources(
                    sources,
                    db_path=self.server.state.db_path,
                    run_dir=self.server.state.run_dir,
                    user_id=self.server.state.user_id,
                )
                self.send_json({"run": run.to_dict()})
            elif path == "/api/sources":
                source = source_from_payload(self.read_json_body())
                saved = upsert_user_source(
                    self.server.state.db_path, source, user_id=self.server.state.user_id
                )
                self.send_json({"source": saved.to_dict()})
            elif path.startswith("/api/sources/") and path.endswith("/toggle"):
                self.api_toggle_source(path)
            elif path.startswith("/api/sources/") and path.endswith("/validate"):
                self.api_validate_source(path)
            elif path == "/api/validate-sources":
                results = validate_all_sources_to_db(
                    self.server.state.db_path, user_id=self.server.state.user_id
                )
                self.send_json({"results": [result.to_dict() for result in results]})
            elif path.startswith("/api/sources/") and path.endswith("/refresh"):
                self.send_json({"message": "Refresh placeholder. Feed sync stays separate in v1."})
            elif path == "/api/refresh":
                self.send_json({"message": "Refresh-all placeholder. Feed sync stays separate in v1."})
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except (FeedConfigError, ValueError, json.JSONDecodeError) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            source_id = source_id_from_path(path)
            if not source_id:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
                return
            current = get_user_source(
                self.server.state.db_path, source_id, user_id=self.server.state.user_id
            )
            data = current.to_dict()
            data.update(self.read_json_body())
            data["id"] = source_id
            source = source_from_payload(data)
            saved = upsert_user_source(
                self.server.state.db_path, source, user_id=self.server.state.user_id
            )
            self.send_json({"source": saved.to_dict()})
        except (FeedConfigError, ValueError, json.JSONDecodeError) as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            source_id = source_id_from_path(path)
            if not source_id:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
                return
            delete_user_source(self.server.state.db_path, source_id, user_id=self.server.state.user_id)
            self.send_json({"removed": source_id})
        except FeedConfigError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def api_items(self, query: str) -> dict[str, object]:
        params = parse_qs(query)
        category = first_param(params, "category")
        source_id = first_param(params, "source_id")
        search = first_param(params, "q")
        saved_only = first_param(params, "saved") in {"1", "true", "yes"}
        limit = clamp_int(first_param(params, "limit") or "200", default=200, low=1, high=500)
        items = list_feed_items(
            self.server.state.db_path,
            user_id=self.server.state.user_id,
            category=category,
            source_id=source_id,
            search=search,
            saved_only=saved_only,
            limit=limit,
        )
        placeholders = []
        if category and not saved_only:
            sources = list_user_sources(self.server.state.db_path, user_id=self.server.state.user_id)
            placeholders = [
                source_placeholder(source)
                for source in sources
                if source.category == category and source.status in {"failed", "needs_feed_url", "unsupported_v1"}
            ]
        return {
            "items": [item.to_dict() for item in items],
            "placeholders": placeholders,
            "total": len(items),
        }

    def api_toggle_source(self, path: str) -> None:
        source_id = source_id_from_path(path.removesuffix("/toggle"))
        if not source_id:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        source = toggle_user_source(
            self.server.state.db_path, source_id, user_id=self.server.state.user_id
        )
        self.send_json({"source": source.to_dict()})

    def api_validate_source(self, path: str) -> None:
        source_id = source_id_from_path(path.removesuffix("/validate"))
        if not source_id:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        result = validate_source_to_db(
            self.server.state.db_path, source_id, user_id=self.server.state.user_id
        )
        self.send_json({"result": result.to_dict()})

    def read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 1_000_000:
            raise ValueError("Request body too large.")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object.")
        return data

    def send_html(self, html_body: str) -> None:
        body = html_body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status=status)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(*, host: str, port: int, db_path: Path, run_dir: Path) -> None:
    initialize_database(db_path)
    state = FeedAppState(db_path=db_path, run_dir=run_dir)
    server = FeedHTTPServer((host, port), state)
    print(f"Feed app running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping feed app.")
    finally:
        server.server_close()


def source_id_from_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[:2] == ["api", "sources"]:
        return unquote(parts[2]).strip().lower()
    return None


def source_from_payload(payload: dict[str, object]) -> FeedSource:
    enabled_value = payload.get("enabled", True)
    if isinstance(enabled_value, str):
        enabled = parse_bool(enabled_value)
    else:
        enabled = bool(enabled_value)
    return FeedSource(
        id=str(payload.get("id") or ""),
        name=str(payload.get("name") or ""),
        category=str(payload.get("category") or ""),
        homepage_url=string_or_none(payload.get("homepage_url") or payload.get("site_url")),
        feed_url=string_or_none(payload.get("feed_url")),
        source_type=str(payload.get("source_type") or "rss"),
        enabled=enabled,
        status=str(payload.get("status") or "untested"),
        pull_frequency_minutes=int(payload.get("pull_frequency_minutes") or 360),
        global_source_id=string_or_none(payload.get("global_source_id")),
        last_fetched_at=string_or_none(payload.get("last_fetched_at")),
        fetch_status=string_or_none(payload.get("fetch_status")),
        last_error=string_or_none(payload.get("last_error")),
        last_checked_at=string_or_none(payload.get("last_checked_at")),
        validation_error=string_or_none(payload.get("validation_error")),
        detected_feed_type=string_or_none(payload.get("detected_feed_type")),
        example_item_count=int(payload.get("example_item_count") or 0),
        has_images=bool(payload.get("has_images", False)),
        has_descriptions=bool(payload.get("has_descriptions", False)),
        has_dates=bool(payload.get("has_dates", False)),
        notes=string_or_none(payload.get("notes")),
        group=string_or_none(payload.get("group") or payload.get("group_name")),
        created_at=string_or_none(payload.get("created_at")),
    )


def source_placeholder(source: FeedSource) -> dict[str, object]:
    if source.status == "unsupported_v1":
        message = "Unsupported in v1. Needs API/manual support later; no scraping."
    elif source.status == "needs_feed_url":
        message = "Needs clean RSS/Atom feed URL before it can appear in the feed."
    else:
        message = source.validation_error or "Feed validation failed."
    return {
        "id": source.id,
        "source_name": source.name,
        "category": source.category,
        "status": source.status,
        "source_type": source.source_type,
        "homepage_url": source.homepage_url,
        "feed_url": source.feed_url,
        "notes": source.notes,
        "message": message,
        "group": source.group,
    }


def first_param(params: dict[str, list[str]], name: str) -> str | None:
    value = (params.get(name) or [""])[0].strip()
    return value or None


def string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def clamp_int(value: str, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(low, min(high, parsed))


APP_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Recon Feed</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090a0f;
      --surface: rgba(20, 22, 31, .82);
      --surface-strong: rgba(28, 31, 43, .96);
      --line: rgba(255,255,255,.12);
      --text: #f4f1ea;
      --muted: #a7aab8;
      --accent: #b6ff5f;
      --blue: #80d7ff;
      --pink: #ff8fce;
      --orange: #ffb45f;
      --shadow: rgba(0, 0, 0, .38);
      --nav-h: 5.4rem;
    }
    * { box-sizing: border-box; }
    html { background: var(--bg); }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at 12% -8%, rgba(128, 215, 255, .24), transparent 19rem),
        radial-gradient(circle at 92% 10%, rgba(255, 143, 206, .14), transparent 18rem),
        linear-gradient(160deg, #0a0b11 0%, #11131d 48%, #090a0f 100%);
      font-family: ui-rounded, "SF Pro Rounded", "Avenir Next", "Nunito Sans", system-ui, sans-serif;
      padding-bottom: calc(var(--nav-h) + env(safe-area-inset-bottom));
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(rgba(255,255,255,.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);
      background-size: 34px 34px;
      mask-image: linear-gradient(to bottom, black, transparent 74%);
    }
    button, input, select, textarea { font: inherit; }
    button { -webkit-tap-highlight-color: transparent; }
    a { color: inherit; }
    .shell { width: min(100%, 1180px); margin: 0 auto; padding: .9rem .85rem 1.2rem; }
    .hero { padding: 1.1rem .2rem .75rem; }
    .kicker { color: var(--accent); font-size: .72rem; letter-spacing: .16em; text-transform: uppercase; font-weight: 800; }
    h1 { margin: .2rem 0 .35rem; font-size: clamp(2.25rem, 16vw, 5rem); line-height: .86; letter-spacing: -.075em; }
    .subhead { margin: 0; color: var(--muted); line-height: 1.45; max-width: 42rem; }
    .hero-actions { display: flex; gap: .55rem; margin-top: .9rem; overflow-x: auto; padding-bottom: .25rem; }
    .btn {
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255,255,255,.065);
      color: var(--text);
      padding: .72rem .95rem;
      cursor: pointer;
      white-space: nowrap;
      box-shadow: 0 .7rem 1.8rem rgba(0,0,0,.18);
    }
    .btn.primary { border-color: rgba(182,255,95,.76); background: linear-gradient(135deg, #d8ff74, #7cffd2); color: #10130d; font-weight: 900; }
    .btn.danger { color: #ff9d9d; }
    .screen { display: none; animation: lift .24s ease both; }
    .screen.active { display: block; }
    @keyframes lift { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
    .quick-cats { display: flex; gap: .7rem; overflow-x: auto; scroll-snap-type: x mandatory; padding: .35rem 0 1rem; margin: 0 -.85rem 0 0; }
    .cat-card {
      flex: 0 0 10.4rem;
      min-height: 8.2rem;
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 1.45rem;
      padding: .85rem;
      color: white;
      text-align: left;
      background: linear-gradient(135deg, var(--c1), var(--c2));
      scroll-snap-align: start;
      position: relative;
      overflow: hidden;
      box-shadow: 0 1rem 2.6rem rgba(0,0,0,.28);
    }
    .cat-card::after { content: ""; position: absolute; width: 7rem; height: 7rem; right: -2.6rem; bottom: -2.4rem; border-radius: 50%; background: rgba(255,255,255,.18); }
    .cat-card.active { outline: 2px solid rgba(255,255,255,.8); }
    .cat-code { opacity: .72; font-size: .68rem; letter-spacing: .12em; text-transform: uppercase; }
    .cat-name { position: relative; z-index: 1; margin-top: 1.8rem; font-weight: 900; font-size: 1.15rem; letter-spacing: -.035em; }
    .cat-count { position: relative; z-index: 1; margin-top: .35rem; opacity: .78; font-size: .82rem; }
    .panel {
      border: 1px solid var(--line);
      border-radius: 1.35rem;
      background: var(--surface);
      box-shadow: 0 1rem 3rem var(--shadow);
      backdrop-filter: blur(18px);
    }
    .toolbar { display: grid; gap: .7rem; padding: .85rem; margin-bottom: .85rem; }
    .search-row { display: grid; grid-template-columns: 1fr auto; gap: .55rem; }
    input, select, textarea {
      width: 100%;
      min-height: 2.85rem;
      border: 1px solid var(--line);
      border-radius: 1rem;
      background: rgba(255,255,255,.07);
      color: var(--text);
      padding: .78rem .9rem;
      outline: none;
    }
    textarea { min-height: 5.2rem; resize: vertical; }
    input:focus, select:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 4px rgba(182,255,95,.12); }
    .status { color: var(--muted); font-size: .86rem; padding: 0 .1rem; min-height: 1.3rem; }
    .feed-list { display: grid; gap: .75rem; }
    .feed-card {
      display: grid;
      grid-template-columns: 5.5rem 1fr;
      gap: .8rem;
      border: 1px solid rgba(255,255,255,.12);
      border-radius: 1.25rem;
      background: linear-gradient(145deg, rgba(255,255,255,.09), rgba(255,255,255,.045));
      padding: .72rem;
      overflow: hidden;
    }
    .thumb {
      min-height: 6.2rem;
      border-radius: 1rem;
      background: linear-gradient(135deg, var(--c1), var(--c2));
      background-size: cover;
      background-position: center;
      display: grid;
      place-items: end start;
      padding: .55rem;
      color: white;
      font-weight: 900;
      letter-spacing: -.04em;
    }
    .feed-body { min-width: 0; }
    .meta { display: flex; gap: .35rem; flex-wrap: wrap; margin-bottom: .35rem; }
    .pill { border: 1px solid rgba(255,255,255,.12); border-radius: 999px; color: var(--muted); padding: .18rem .45rem; font-size: .68rem; }
    .pill.category { color: white; background: linear-gradient(135deg, var(--c1), var(--c2)); border: none; }
    .feed-card h2 { margin: 0; font-size: 1.02rem; line-height: 1.18; letter-spacing: -.035em; }
    .feed-card p { margin: .45rem 0 0; color: #ced1da; font-size: .86rem; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
    .feed-card.placeholder { opacity: .86; border-style: dashed; }
    .feed-card.placeholder .thumb { filter: saturate(.55); }
    .read { display: inline-flex; margin-top: .55rem; color: var(--blue); font-weight: 750; text-decoration: none; font-size: .84rem; }
    .section-title { display: flex; align-items: center; justify-content: space-between; margin: 1rem .15rem .65rem; color: var(--muted); font-size: .76rem; letter-spacing: .13em; text-transform: uppercase; }
    .sources-desktop { display: none !important; }
    .source-stack { display: grid; gap: .65rem; }
    .source-bundle { border: 1px solid rgba(255,255,255,.1); border-radius: 1.2rem; background: rgba(255,255,255,.035); overflow: hidden; }
    .source-bundle > summary { list-style: none; padding: .8rem; color: var(--muted); font-size: .78rem; letter-spacing: .08em; text-transform: uppercase; cursor: pointer; }
    .source-bundle > summary::-webkit-details-marker { display: none; }
    .source-bundle-list { display: grid; gap: .55rem; padding: 0 .55rem .55rem; }
    .source-row {
      border: 1px solid rgba(255,255,255,.11);
      border-radius: 1.12rem;
      background: rgba(255,255,255,.055);
      padding: .8rem;
    }
    details.source-row { padding: 0; overflow: hidden; }
    .source-summary { list-style: none; padding: .8rem; cursor: pointer; }
    .source-summary::-webkit-details-marker { display: none; }
    .source-row.off { opacity: .58; }
    .source-main { display: flex; justify-content: space-between; gap: .7rem; align-items: flex-start; }
    .source-main strong { display: block; letter-spacing: -.025em; }
    .source-main small { color: var(--muted); }
    .source-details { display: grid; gap: .45rem; padding: 0 .8rem .8rem; color: var(--muted); font-size: .8rem; line-height: 1.35; }
    .source-details a { color: var(--blue); word-break: break-word; }
    .source-actions { display: flex; gap: .42rem; margin-top: .65rem; overflow-x: auto; }
    .mini { min-height: 2.1rem; padding: .4rem .62rem; font-size: .76rem; }
    .status-badge { display: inline-flex; align-items: center; border-radius: 999px; padding: .22rem .5rem; font-size: .68rem; font-weight: 850; color: #0b0d12; }
    .status-working { background: #82ff9e; }
    .status-needs_feed_url { background: #ffd76e; }
    .status-failed { background: #ff8d8d; }
    .status-unsupported_v1 { background: #b6bbc8; }
    .status-untested { background: #8bd5ff; }
    .form { display: grid; gap: .62rem; padding: .85rem; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .62rem; }
    .checkbox { display: flex; align-items: center; gap: .55rem; color: var(--muted); }
    .checkbox input { width: auto; min-height: 0; }
    .empty { padding: 2.2rem 1rem; color: var(--muted); text-align: center; }
    .saved-blank, .settings-card { padding: 1rem; color: var(--muted); line-height: 1.5; }
    .bottom-nav {
      position: fixed;
      left: .75rem;
      right: .75rem;
      bottom: calc(.75rem + env(safe-area-inset-bottom));
      height: 4.45rem;
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: .25rem;
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 1.45rem;
      background: rgba(18, 20, 29, .88);
      box-shadow: 0 1.2rem 3rem rgba(0,0,0,.45);
      backdrop-filter: blur(22px);
      padding: .35rem;
      z-index: 10;
    }
    .nav-btn {
      border: 0;
      border-radius: 1.05rem;
      background: transparent;
      color: var(--muted);
      display: grid;
      place-items: center;
      gap: .1rem;
      font-size: .66rem;
      cursor: pointer;
    }
    .nav-btn strong { font-size: 1rem; line-height: 1; }
    .nav-btn.active { background: rgba(255,255,255,.1); color: var(--text); }
    @media (min-width: 860px) {
      body { padding-bottom: 1.5rem; }
      .shell { padding: 1.25rem; }
      .app-grid { display: grid; grid-template-columns: minmax(0, 1fr) 23rem; gap: 1rem; align-items: start; }
      .bottom-nav { left: 50%; right: auto; width: 30rem; transform: translateX(-50%); }
      .feed-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .feed-card { grid-template-columns: 7rem 1fr; }
      .sources-desktop.active { display: block !important; position: sticky; top: 1rem; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="hero">
      <div class="kicker">Personal intelligence feed</div>
      <h1>Recon Feed</h1>
      <p class="subhead">Mobile-first RSS/Atom cards across research, markets, culture, policy, and creative tools. Clean source control now; richer intelligence later.</p>
      <div class="hero-actions">
        <button class="btn primary" id="sync-top">Sync feeds</button>
        <button class="btn" data-screen-link="sources">Edit sources</button>
        <button class="btn" data-screen-link="categories">Browse categories</button>
      </div>
    </header>
    <section class="quick-cats" id="quick-cats"></section>
    <div class="app-grid">
      <main>
        <section class="screen active" id="screen-home">
          <div class="panel toolbar">
            <div class="search-row">
              <input id="search" placeholder="Search titles, sources, excerpts">
              <button class="btn" id="clear-filter">Clear</button>
            </div>
            <div class="status" id="status">Ready. Run sync to fetch enabled feeds.</div>
          </div>
          <section class="feed-list" id="cards"></section>
        </section>

        <section class="screen" id="screen-categories">
          <div class="section-title"><span>Category moodboard</span><span id="cat-count"></span></div>
          <div class="quick-cats" id="category-board"></div>
        </section>

        <section class="screen" id="screen-sources">
          <div class="section-title"><span>Source management</span><span id="source-count"></span></div>
          <div class="hero-actions">
            <button class="btn primary" id="validate-all" type="button">Validate all</button>
            <button class="btn" id="refresh-all" type="button">Refresh all placeholder</button>
          </div>
          <form class="panel form" id="source-form">
            <div class="form-grid">
              <input name="id" placeholder="source-id" required>
              <select name="category" id="source-category" required></select>
            </div>
            <input name="name" placeholder="Source name" required>
            <input name="homepage_url" placeholder="Homepage URL">
            <input name="feed_url" placeholder="RSS/Atom feed URL">
            <div class="form-grid">
              <select name="source_type">
                <option value="rss">RSS</option>
                <option value="atom">Atom</option>
                <option value="api">API later</option>
                <option value="manual">Manual</option>
                <option value="unsupported_v1">Unsupported v1</option>
              </select>
              <input name="pull_frequency_minutes" type="number" min="1" step="1" value="360" placeholder="Pull minutes">
            </div>
            <textarea name="notes" placeholder="Notes"></textarea>
            <label class="checkbox"><input name="enabled" type="checkbox" checked> enabled</label>
            <div class="form-grid">
              <button class="btn primary" type="submit">Save source</button>
              <button class="btn" type="button" id="reset-source">Clear</button>
            </div>
          </form>
          <div class="source-stack" id="manage-list"></div>
        </section>

        <section class="screen" id="screen-saved">
          <div class="panel saved-blank">
            <strong>Saved structure ready.</strong><br>
            V1 schema has per-user read/saved state. Save controls can be added next; this nav stays so the app shape is ready.
          </div>
        </section>

        <section class="screen" id="screen-settings">
          <div class="panel settings-card">
            <strong>Phase 2 source controls active.</strong><br>
            Structured source config, manual RSS/Atom validation, compact mobile source cards, and multi-user-ready source/item state.
          </div>
        </section>
      </main>

      <aside class="sources-desktop screen active" id="desktop-source-panel">
        <div class="section-title"><span>Sources</span><span id="side-source-count"></span></div>
        <div class="source-stack" id="sources"></div>
      </aside>
    </div>
  </div>

  <nav class="bottom-nav">
    <button class="nav-btn active" data-screen-link="home"><strong>H</strong><span>Home</span></button>
    <button class="nav-btn" data-screen-link="categories"><strong>C</strong><span>Categories</span></button>
    <button class="nav-btn" data-screen-link="sources"><strong>S</strong><span>Sources</span></button>
    <button class="nav-btn" data-screen-link="saved"><strong>V</strong><span>Saved</span></button>
    <button class="nav-btn" data-screen-link="settings"><strong>G</strong><span>Settings</span></button>
  </nav>

  <script>
    const state = { categories: [], sources: [], category: '', sourceId: '', q: '', screen: 'home' };
    const $ = (id) => document.getElementById(id);
    const META = {
      AI_TECH: ['AI Tech', '#66e5ff', '#6f7cff', 'AI'],
      CREATIVE_AI: ['Creative AI', '#ff8fce', '#8d7cff', 'ART'],
      PSYCHOLOGY: ['Psychology', '#ffd36a', '#ff7f7f', 'PSY'],
      HEALTH_LOOKS: ['Health Looks', '#70f0b8', '#64a7ff', 'HL'],
      FASHION: ['Fashion', '#f8f0d8', '#d79cff', 'FSH'],
      MARKETS: ['Markets', '#b6ff5f', '#4fc3ff', 'MKT'],
      CRYPTO: ['Crypto', '#f8d66d', '#ff8a45', 'CRY'],
      DATA_SCIENCE: ['Data Science', '#8bf7ff', '#75ff9c', 'DATA'],
      POLICY: ['Policy', '#d1d5ff', '#7aa8ff', 'POL'],
    };

    async function api(path, options = {}) {
      const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
      const token = localStorage.getItem('feedAppToken') || '';
      if (token) headers['X-Feed-App-Token'] = token;
      const res = await fetch(path, { ...options, headers });
      const data = await res.json();
      if (res.status === 401) {
        const nextToken = prompt('Access token');
        if (nextToken) {
          localStorage.setItem('feedAppToken', nextToken.trim());
          return api(path, options);
        }
      }
      if (!res.ok) throw new Error(data.error || data.detail || res.statusText);
      return data;
    }

    function escapeHtml(value) {
      return String(value || '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
    }

    async function loadBase() {
      const [catData, sourceData] = await Promise.all([api('/api/categories'), api('/api/sources')]);
      state.categories = catData.categories;
      state.sources = sourceData.sources;
      renderCategoryOptions();
      renderCategories();
      renderSources();
      await loadItems();
    }

    function categoryStyle(cat) {
      const meta = META[cat] || [cat, '#b6ff5f', '#80d7ff', cat.slice(0, 3)];
      return `--c1:${meta[1]};--c2:${meta[2]}`;
    }

    function categoryName(cat) { return (META[cat] || [cat])[0]; }
    function categoryMark(cat) { return (META[cat] || [cat, '', '', cat.slice(0, 3)])[3]; }

    function renderCategoryOptions() {
      $('source-category').innerHTML = state.categories.map((cat) => `<option>${cat}</option>`).join('');
    }

    function categoryCards(targetId) {
      const counts = Object.fromEntries(state.categories.map((cat) => [cat, state.sources.filter((source) => source.category === cat).length]));
      $(targetId).innerHTML = [`<button class="cat-card ${state.category ? '' : 'active'}" style="--c1:#2e3345;--c2:#131722" data-cat=""><div class="cat-code">ALL</div><div class="cat-name">All Feed</div><div class="cat-count">${state.sources.length} sources</div></button>`]
        .concat(state.categories.map((cat) => `<button class="cat-card ${state.category === cat ? 'active' : ''}" style="${categoryStyle(cat)}" data-cat="${cat}"><div class="cat-code">${cat}</div><div class="cat-name">${categoryName(cat)}</div><div class="cat-count">${counts[cat] || 0} sources</div></button>`))
        .join('');
      document.querySelectorAll(`#${targetId} [data-cat]`).forEach((button) => button.onclick = () => {
        state.category = button.dataset.cat;
        state.sourceId = '';
        setScreen('home');
        renderCategories();
        renderSources();
        loadItems();
      });
    }

    function renderCategories() {
      $('cat-count').textContent = state.categories.length;
      categoryCards('quick-cats');
      categoryCards('category-board');
    }

    function renderSources() {
      const shown = state.sources.filter((source) => !state.category || source.category === state.category);
      $('source-count').textContent = state.sources.length;
      $('side-source-count').textContent = shown.length;
      $('sources').innerHTML = shown.map((source) => sourceRow(source, true)).join('');
      $('manage-list').innerHTML = renderSourceBundles(state.sources);
      document.querySelectorAll('[data-source]').forEach((button) => button.onclick = () => {
        state.sourceId = button.dataset.source;
        renderSources();
        loadItems();
        setScreen('home');
      });
      document.querySelectorAll('[data-edit]').forEach((button) => button.onclick = () => editSource(button.dataset.edit));
      document.querySelectorAll('[data-toggle]').forEach((button) => button.onclick = () => toggleSource(button.dataset.toggle));
      document.querySelectorAll('[data-delete]').forEach((button) => button.onclick = () => deleteSource(button.dataset.delete));
      document.querySelectorAll('[data-validate]').forEach((button) => button.onclick = () => validateSource(button.dataset.validate));
      document.querySelectorAll('[data-refresh]').forEach((button) => button.onclick = () => refreshSource(button.dataset.refresh));
    }

    function sourceRow(source, compact) {
      const active = state.sourceId === source.id ? 'active' : '';
      const enabled = source.enabled ? 'enabled' : 'disabled';
      const validation = source.status || 'untested';
      const detail = `${source.category} / ${enabled} / ${source.source_type || 'rss'} / ${source.pull_frequency_minutes || 360}m`;
      const badge = `<span class="status-badge status-${validation}">${validation.replaceAll('_', ' ')}</span>`;
      if (compact) {
        return `<div class="source-row ${source.enabled ? '' : 'off'} ${active}" style="${categoryStyle(source.category)}"><div class="source-main"><button class="btn mini" data-source="${source.id}">${categoryMark(source.category)}</button><div><strong>${escapeHtml(source.name)}</strong><small>${escapeHtml(detail)}</small></div>${badge}</div></div>`;
      }
      const homepage = source.homepage_url ? `<a href="${escapeHtml(source.homepage_url)}" target="_blank" rel="noreferrer">${escapeHtml(source.homepage_url)}</a>` : 'missing';
      const feed = source.feed_url ? `<a href="${escapeHtml(source.feed_url)}" target="_blank" rel="noreferrer">${escapeHtml(source.feed_url)}</a>` : 'missing';
      const error = source.validation_error ? `<div><strong>Error:</strong> ${escapeHtml(source.validation_error)}</div>` : '';
      const notes = source.notes ? `<div><strong>Notes:</strong> ${escapeHtml(source.notes)}</div>` : '';
      return `<details class="source-row ${source.enabled ? '' : 'off'} ${active}" style="${categoryStyle(source.category)}"><summary class="source-summary"><div class="source-main"><button class="btn mini" data-source="${source.id}" type="button">${categoryMark(source.category)}</button><div><strong>${escapeHtml(source.name)}</strong><small>${escapeHtml(detail)}</small></div>${badge}</div></summary><div class="source-details"><div><strong>Homepage:</strong> ${homepage}</div><div><strong>Feed:</strong> ${feed}</div><div><strong>Last checked:</strong> ${escapeHtml(source.last_checked_at || 'never')}</div><div><strong>Detected:</strong> ${escapeHtml(source.detected_feed_type || 'unknown')} / ${source.example_item_count || 0} items / images ${source.has_images ? 'yes' : 'no'} / descriptions ${source.has_descriptions ? 'yes' : 'no'}</div>${error}${notes}<div class="source-actions"><button class="btn mini" data-edit="${source.id}" type="button">Edit</button><button class="btn mini" data-validate="${source.id}" type="button">Validate</button><button class="btn mini" data-refresh="${source.id}" type="button">Refresh</button><button class="btn mini" data-toggle="${source.id}" type="button">${source.enabled ? 'Disable' : 'Enable'}</button><button class="btn mini danger" data-delete="${source.id}" type="button">Delete</button></div></div></details>`;
    }

    function renderSourceBundles(sources) {
      const groups = new Map();
      const singles = [];
      for (const source of sources) {
        if (source.group) {
          if (!groups.has(source.group)) groups.set(source.group, []);
          groups.get(source.group).push(source);
        } else {
          singles.push(source);
        }
      }
      const bundled = [...groups.entries()].map(([group, groupSources]) => `<details class="source-bundle" open><summary>${escapeHtml(group)} bundle · ${groupSources.length}</summary><div class="source-bundle-list">${groupSources.map((source) => sourceRow(source, false)).join('')}</div></details>`);
      return bundled.concat(singles.map((source) => sourceRow(source, false))).join('');
    }

    async function loadItems() {
      const params = new URLSearchParams({ limit: '200' });
      if (state.category) params.set('category', state.category);
      if (state.sourceId) params.set('source_id', state.sourceId);
      if (state.q) params.set('q', state.q);
      const data = await api(`/api/items?${params}`);
      $('status').textContent = `${data.total} cards loaded${state.category ? ' / ' + categoryName(state.category) : ''}`;
      renderCards(data.items, data.placeholders || []);
    }

    function renderCards(items, placeholders = []) {
      if (!items.length && !placeholders.length) {
        $('cards').innerHTML = '<div class="panel empty">No cards yet. Sync feeds, pick another category, or clear filters.</div>';
        return;
      }
      const cards = items.map((item) => {
        const bg = item.image_url ? `background-image: linear-gradient(to top, rgba(0,0,0,.55), transparent), url('${escapeHtml(item.image_url)}')` : '';
        return `<article class="feed-card" style="${categoryStyle(item.category)}"><div class="thumb" style="${bg}">${item.image_url ? '' : categoryMark(item.category)}</div><div class="feed-body"><div class="meta"><span class="pill category">${categoryName(item.category)}</span><span class="pill">${escapeHtml(item.source_name)}</span></div><h2>${escapeHtml(item.title)}</h2>${item.excerpt ? `<p>${escapeHtml(item.excerpt)}</p>` : ''}${item.url ? `<a class="read" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">Open original</a>` : ''}</div></article>`;
      });
      const placeholderCards = placeholders.map((source) => `<article class="feed-card placeholder" style="${categoryStyle(source.category)}"><div class="thumb">${categoryMark(source.category)}</div><div class="feed-body"><div class="meta"><span class="pill category">${categoryName(source.category)}</span><span class="pill">${escapeHtml(source.status || 'inactive')}</span></div><h2>${escapeHtml(source.source_name)}</h2><p>${escapeHtml(source.message || source.notes || 'Inactive source placeholder.')}</p>${source.homepage_url ? `<a class="read" href="${escapeHtml(source.homepage_url)}" target="_blank" rel="noreferrer">Open homepage</a>` : ''}</div></article>`);
      $('cards').innerHTML = cards.concat(placeholderCards).join('');
    }

    function editSource(id) {
      const source = state.sources.find((item) => item.id === id);
      if (!source) return;
      setScreen('sources');
      const form = $('source-form');
      const fields = form.elements;
      form.dataset.editing = id;
      fields.id.value = source.id;
      fields.id.disabled = true;
      fields.name.value = source.name;
      fields.category.value = source.category;
      fields.homepage_url.value = source.homepage_url || source.site_url || '';
      fields.feed_url.value = source.feed_url || '';
      fields.source_type.value = source.source_type || 'rss';
      fields.pull_frequency_minutes.value = source.pull_frequency_minutes || 360;
      fields.notes.value = source.notes || '';
      fields.enabled.checked = Boolean(source.enabled);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function resetForm() {
      const form = $('source-form');
      const fields = form.elements;
      form.reset();
      fields.id.disabled = false;
      fields.enabled.checked = true;
      fields.pull_frequency_minutes.value = 360;
      delete form.dataset.editing;
    }

    async function saveSource(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const fields = form.elements;
      const payload = {
        id: fields.id.value,
        name: fields.name.value,
        category: fields.category.value,
        homepage_url: fields.homepage_url.value,
        feed_url: fields.feed_url.value,
        source_type: fields.source_type.value,
        pull_frequency_minutes: Number(fields.pull_frequency_minutes.value || 360),
        notes: fields.notes.value,
        enabled: fields.enabled.checked,
      };
      const editing = form.dataset.editing;
      const path = editing ? `/api/sources/${encodeURIComponent(editing)}` : '/api/sources';
      const method = editing ? 'PUT' : 'POST';
      try {
        await api(path, { method, body: JSON.stringify(payload) });
        $('status').textContent = `Saved source ${payload.id}`;
        resetForm();
        await loadBase();
      } catch (err) { $('status').textContent = err.message; }
    }

    async function toggleSource(id) {
      try { await api(`/api/sources/${encodeURIComponent(id)}/toggle`, { method: 'POST' }); await loadBase(); }
      catch (err) { $('status').textContent = err.message; }
    }

    async function validateSource(id) {
      $('status').textContent = `Validating ${id}...`;
      try {
        const data = await api(`/api/sources/${encodeURIComponent(id)}/validate`, { method: 'POST' });
        $('status').textContent = `${id}: ${data.result.status} / ${data.result.detected_feed_type || 'unknown'} / ${data.result.example_item_count} items`;
        await loadBase();
      } catch (err) { $('status').textContent = err.message; }
    }

    async function validateAllSources() {
      $('status').textContent = 'Validating all source feeds...';
      try {
        const data = await api('/api/validate-sources', { method: 'POST' });
        const counts = data.results.reduce((acc, result) => { acc[result.status] = (acc[result.status] || 0) + 1; return acc; }, {});
        $('status').textContent = Object.entries(counts).map(([key, value]) => `${key}: ${value}`).join(' / ');
        await loadBase();
      } catch (err) { $('status').textContent = err.message; }
    }

    async function refreshSource(id) {
      const data = await api(`/api/sources/${encodeURIComponent(id)}/refresh`, { method: 'POST' });
      $('status').textContent = data.message;
    }

    async function refreshAllSources() {
      const data = await api('/api/refresh', { method: 'POST' });
      $('status').textContent = data.message;
    }

    async function deleteSource(id) {
      if (!confirm(`Delete source ${id}?`)) return;
      try { await api(`/api/sources/${encodeURIComponent(id)}`, { method: 'DELETE' }); await loadBase(); }
      catch (err) { $('status').textContent = err.message; }
    }

    function setScreen(name) {
      state.screen = name;
      document.querySelectorAll('.screen').forEach((screen) => screen.classList.remove('active'));
      const active = $(`screen-${name}`);
      if (active) active.classList.add('active');
      document.querySelectorAll('.nav-btn').forEach((button) => button.classList.toggle('active', button.dataset.screenLink === name));
    }

    async function syncFeeds() {
      $('status').textContent = 'Syncing enabled feeds...';
      try {
        const data = await api('/api/sync', { method: 'POST' });
        const run = data.run;
        $('status').textContent = `Sync: ${run.total_written} new, ${run.total_skipped} skipped, ${run.error_count} errors`;
        await loadBase();
      } catch (err) { $('status').textContent = err.message; }
    }

    document.querySelectorAll('[data-screen-link]').forEach((button) => button.onclick = () => setScreen(button.dataset.screenLink));
    $('sync-top').onclick = syncFeeds;
    $('validate-all').onclick = validateAllSources;
    $('refresh-all').onclick = refreshAllSources;
    $('clear-filter').onclick = () => { state.category = ''; state.sourceId = ''; state.q = ''; $('search').value = ''; renderCategories(); renderSources(); loadItems(); };
    $('search').oninput = (event) => { state.q = event.target.value; loadItems(); };
    $('source-form').onsubmit = saveSource;
    $('reset-source').onclick = resetForm;
    loadBase().catch((err) => $('status').textContent = err.message);
  </script>
</body>
</html>
"""
