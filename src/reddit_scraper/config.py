from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

DEFAULT_SUBREDDITS = (
    "LocalLLaMA",
    "LocalLLM",
    "AI_Agents",
    "PromptEngineering",
    "automation",
    "AI_developers",
)

DEFAULT_BLOCKED_RESOURCE_TYPES = ("image", "stylesheet", "media", "font")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]+$")


class ConfigError(ValueError):
    """Raised when required scraper config is missing or invalid."""


@dataclass(frozen=True, repr=False)
class OxylabsProxyConfig:
    server: str
    username: str
    password: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OxylabsProxyConfig:
        values = env or os.environ
        proxy_url = values.get("OXYLABS_PROXY_URL", "").strip()
        if proxy_url:
            return cls.from_proxy_url(proxy_url)

        server = values.get("OXYLABS_PROXY_SERVER", "http://pr.oxylabs.io:7777").strip()
        username = values.get("OXYLABS_PROXY_USERNAME", "").strip()
        password = values.get("OXYLABS_PROXY_PASSWORD", "").strip()
        if not username or not password:
            raise ConfigError(
                "Missing Oxylabs credentials. Set OXYLABS_PROXY_USERNAME and "
                "OXYLABS_PROXY_PASSWORD, or set OXYLABS_PROXY_URL."
            )
        return cls(server=normalize_proxy_server(server), username=username, password=password)

    @classmethod
    def from_proxy_url(cls, raw_url: str) -> OxylabsProxyConfig:
        url = raw_url.strip()
        if not url:
            raise ConfigError("OXYLABS_PROXY_URL is empty.")
        if not _SCHEME_RE.match(url):
            url = f"http://{url}"

        parsed = urlsplit(url)
        if not parsed.hostname:
            raise ConfigError("OXYLABS_PROXY_URL must include a proxy host.")
        if not parsed.username or parsed.password is None:
            raise ConfigError("OXYLABS_PROXY_URL must include username and password.")

        server = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            server = f"{server}:{parsed.port}"

        return cls(
            server=normalize_proxy_server(server),
            username=unquote(parsed.username),
            password=unquote(parsed.password),
        )

    def to_playwright_proxy(self) -> dict[str, str]:
        return {"server": self.server, "username": self.username, "password": self.password}

    def safe_label(self) -> str:
        return f"{self.server} as {redact(self.username)}"


@dataclass(frozen=True)
class ScraperSettings:
    output_path: Path = Path("data/reddit_posts.jsonl")
    run_dir: Path = Path("data/runs")
    base_url: str = "https://old.reddit.com"
    max_posts_per_subreddit: int = 50
    pages_per_subreddit: int = 2
    nav_timeout_ms: int = 45_000
    headless: bool = True
    block_resource_types: tuple[str, ...] = DEFAULT_BLOCKED_RESOURCE_TYPES
    subreddits: tuple[str, ...] = DEFAULT_SUBREDDITS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ScraperSettings:
        values = env or os.environ
        return cls(
            output_path=Path(values.get("REDDIT_SCRAPER_OUTPUT", "data/reddit_posts.jsonl")),
            run_dir=Path(values.get("REDDIT_SCRAPER_RUN_DIR", "data/runs")),
            base_url=values.get("REDDIT_SCRAPER_BASE_URL", "https://old.reddit.com").rstrip("/"),
            max_posts_per_subreddit=parse_positive_int(values, "REDDIT_SCRAPER_MAX_POSTS", 50),
            pages_per_subreddit=parse_positive_int(values, "REDDIT_SCRAPER_PAGES", 2),
            nav_timeout_ms=parse_positive_int(values, "REDDIT_SCRAPER_NAV_TIMEOUT_MS", 45_000),
        )

    def with_overrides(
        self,
        *,
        subreddits: tuple[str, ...] | None = None,
        limit: int | None = None,
        pages: int | None = None,
        output_path: Path | None = None,
        run_dir: Path | None = None,
        headless: bool | None = None,
        base_url: str | None = None,
    ) -> ScraperSettings:
        resolved_limit = (
            self.max_posts_per_subreddit if limit is None else require_positive("limit", limit)
        )
        resolved_pages = (
            self.pages_per_subreddit if pages is None else require_positive("pages", pages)
        )
        return ScraperSettings(
            output_path=self.output_path if output_path is None else output_path,
            run_dir=self.run_dir if run_dir is None else run_dir,
            base_url=(base_url or self.base_url).rstrip("/"),
            max_posts_per_subreddit=resolved_limit,
            pages_per_subreddit=resolved_pages,
            nav_timeout_ms=self.nav_timeout_ms,
            headless=self.headless if headless is None else headless,
            block_resource_types=self.block_resource_types,
            subreddits=normalize_subreddits(subreddits or self.subreddits),
        )


def load_dotenv(path: Path = Path(".env"), *, override: bool = False) -> list[str]:
    if not path.exists():
        return []

    loaded: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = strip_env_value(value.strip())
        if not key or (not override and key in os.environ):
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def normalize_proxy_server(server: str) -> str:
    cleaned = server.strip()
    if not cleaned:
        raise ConfigError("Proxy server cannot be empty.")
    if not _SCHEME_RE.match(cleaned):
        cleaned = f"http://{cleaned}"
    return cleaned.rstrip("/")


def normalize_subreddit(name: str) -> str:
    cleaned = name.strip().strip("/")
    if cleaned.lower().startswith("r/"):
        cleaned = cleaned[2:].strip("/")
    if not cleaned or not _SUBREDDIT_RE.fullmatch(cleaned):
        raise ConfigError(f"Invalid subreddit name: {name!r}")
    return cleaned


def normalize_subreddits(names: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for name in names:
        subreddit = normalize_subreddit(name)
        key = subreddit.lower()
        if key not in seen:
            seen.add(key)
            normalized.append(subreddit)
    return tuple(normalized)


def parse_positive_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw_value = values.get(key)
    if raw_value is None or raw_value == "":
        return default
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer.") from exc
    if parsed <= 0:
        raise ConfigError(f"{key} must be greater than zero.")
    return parsed


def require_positive(name: str, value: int) -> int:
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero.")
    return value


def redact(value: str) -> str:
    if not value:
        return "***"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
