from __future__ import annotations

import argparse
import json
import math
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from reddit_scraper.config import load_dotenv
from reddit_scraper.models import RedditPost, utc_now_iso


@dataclass(frozen=True)
class BenchmarkCategory:
    name: str
    weight: int
    terms: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkResult:
    post: RedditPost
    score: int
    priority: str
    signals: tuple[str, ...]
    matched_terms: tuple[str, ...]
    critic: CriticReview | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "score": self.score,
            "priority": self.priority,
            "signals": list(self.signals),
            "matched_terms": list(self.matched_terms),
            "post": self.post.to_dict(),
        }
        if self.critic:
            data["critic"] = self.critic.to_dict()
        return data


@dataclass(frozen=True)
class CriticReview:
    usefulness_score: int
    verdict: str
    confidence: str
    rationale: str
    suggested_action: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CriticReview:
        return cls(
            usefulness_score=clamp_score(data.get("usefulness_score", data.get("score", 0))),
            verdict=choice(data.get("verdict"), choices=("keep", "watch", "skip"), default="watch"),
            confidence=choice(
                data.get("confidence"), choices=("high", "medium", "low"), default="medium"
            ),
            rationale=clean_text(data.get("rationale", ""), limit=240),
            suggested_action=clean_text(data.get("suggested_action", ""), limit=180),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "usefulness_score": self.usefulness_score,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "suggested_action": self.suggested_action,
        }


class CriticConfigError(ValueError):
    """Raised when the OpenCode critic cannot be configured or parsed."""


class CriticModel(Protocol):
    def invoke(self, messages: list[dict[str, str]]) -> Any:
        ...


DEFAULT_CRITIC_MODEL = "kimi-k2.6"
DEFAULT_OPENCODE_BASE_URL = "https://opencode.ai/zen/go/v1"

CRITIC_SYSTEM_PROMPT = """You are a skeptical AI technology recon critic.
Judge whether a scraped Reddit listing is likely to contain actionable technical intelligence.
Prefer concrete findings, benchmarks, setup/debugging guidance, tools, repos, workflows,
and implementation examples.
Penalize memes, hype, hiring, drama, vague speculation, and generic discussions.
Return strict JSON only with keys: usefulness_score, verdict, confidence, rationale,
suggested_action.
usefulness_score must be an integer 0-100.
verdict must be one of: keep, watch, skip.
confidence must be one of: high, medium, low.
Keep rationale and suggested_action concise.
"""


BENCHMARK_CATEGORIES = (
    BenchmarkCategory(
        name="technical finding",
        weight=24,
        terms=(
            "architecture",
            "benchmark",
            "cuda",
            "embedding",
            "eval",
            "evaluation",
            "fine-tune",
            "gpu",
            "inference",
            "latency",
            "memory",
            "mcp",
            "paper",
            "quantization",
            "rag",
            "retrieval",
            "throughput",
            "tool calling",
            "vram",
        ),
    ),
    BenchmarkCategory(
        name="tool or library",
        weight=22,
        terms=(
            "api",
            "claude code",
            "cli",
            "cursor",
            "extension",
            "framework",
            "github",
            "langchain",
            "library",
            "litellm",
            "llama.cpp",
            "n8n",
            "ollama",
            "open source",
            "opencode",
            "package",
            "playwright",
            "plugin",
            "repo",
            "sdk",
            "tool",
            "vllm",
        ),
    ),
    BenchmarkCategory(
        name="setup tip or trick",
        weight=28,
        terms=(
            "configure",
            "deploy",
            "debug",
            "docker",
            "dockerfile",
            "error",
            "fix",
            "guide",
            "how to",
            "install",
            "run locally",
            "self-host",
            "setup",
            "step-by-step",
            "tip",
            "trick",
            "tutorial",
            "workflow",
        ),
    ),
    BenchmarkCategory(
        name="implementation example",
        weight=14,
        terms=(
            "automation",
            "built",
            "example",
            "launched",
            "released",
            "script",
            "showcase",
            "template",
        ),
    ),
)

LOW_VALUE_TERMS = (
    "art",
    "drama",
    "funny",
    "hiring",
    "hot take",
    "image",
    "job",
    "joke",
    "meme",
    "poll",
    "wallpaper",
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_dotenv(args.env_file)

    posts = load_posts_jsonl(args.input)
    results = benchmark_posts(posts)
    if args.critic:
        if args.critic_limit <= 0:
            parser.error("--critic-limit must be greater than zero.")
        try:
            llm = build_opencode_critic(
                model=args.critic_model,
                base_url=args.critic_base_url,
            )
            results = review_with_critic(results, llm, limit=args.critic_limit)
        except CriticConfigError as exc:
            parser.error(str(exc))
    write_markdown_report(results, args.output, top=args.top)
    if args.jsonl_output:
        write_jsonl(results, args.jsonl_output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rank scraped Reddit posts by recon value.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to .env file.")
    parser.add_argument("--input", type=Path, default=Path("data/reddit_posts.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/reddit_post_benchmark.md"))
    parser.add_argument(
        "--jsonl-output", type=Path, default=Path("data/reddit_post_benchmark.jsonl")
    )
    parser.add_argument("--top", type=int, default=50, help="Number of ranked posts in Markdown.")
    parser.add_argument(
        "--critic",
        action="store_true",
        help="Review top heuristic results with an OpenCode-backed LangChain critic.",
    )
    parser.add_argument(
        "--critic-limit",
        type=int,
        default=20,
        help="Number of top heuristic posts to send to the critic.",
    )
    parser.add_argument(
        "--critic-model",
        help=(
            "OpenCode model for critic reviews. Defaults to OPENCODE_MODEL or "
            f"{DEFAULT_CRITIC_MODEL}."
        ),
    )
    parser.add_argument(
        "--critic-base-url",
        help=(
            "OpenCode API base URL. Defaults to OPENCODE_BASE_URL or "
            "the public OpenCode endpoint."
        ),
    )
    return parser


def load_posts_jsonl(path: Path) -> list[RedditPost]:
    if not path.exists():
        return []

    allowed_fields = set(RedditPost.__dataclass_fields__)
    posts: list[RedditPost] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        post_data = {key: value for key, value in data.items() if key in allowed_fields}
        try:
            posts.append(RedditPost.from_dict(post_data))
        except TypeError:
            continue
    return posts


def benchmark_posts(posts: list[RedditPost]) -> list[BenchmarkResult]:
    return sort_results(benchmark_post(post) for post in posts)


def benchmark_post(post: RedditPost) -> BenchmarkResult:
    text = searchable_text(post)
    score = 5
    signals: list[str] = []
    matched_terms: list[str] = []

    for category in BENCHMARK_CATEGORIES:
        matches = tuple(term for term in category.terms if term in text)
        if not matches:
            continue
        score += category.weight + min(len(matches) * 3, 12)
        signals.append(category.name)
        matched_terms.extend(matches)

    low_value_matches = tuple(term for term in LOW_VALUE_TERMS if term in text)
    if low_value_matches:
        score -= 18 + min(len(low_value_matches) * 3, 12)
        matched_terms.extend(low_value_matches)

    score += engagement_bonus(post.score, limit=10)
    score += engagement_bonus(post.comment_count, limit=12)
    score = max(0, min(100, score))

    return BenchmarkResult(
        post=post,
        score=score,
        priority=priority_for_score(score),
        signals=tuple(signals),
        matched_terms=tuple(dict.fromkeys(matched_terms)),
    )


def build_opencode_critic(
    *, model: str | None = None, base_url: str | None = None, env: Mapping[str, str] | None = None
) -> CriticModel:
    values = env or os.environ
    api_key = values.get("OPENCODE_API_KEY", "").strip()
    if not api_key:
        raise CriticConfigError("Missing OPENCODE_API_KEY for --critic.")

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise CriticConfigError("Missing langchain-openai dependency for --critic.") from exc

    return ChatOpenAI(
        model=(model or values.get("OPENCODE_MODEL") or DEFAULT_CRITIC_MODEL),
        api_key=api_key,
        base_url=(base_url or values.get("OPENCODE_BASE_URL") or DEFAULT_OPENCODE_BASE_URL),
        temperature=0,
    )


def review_with_critic(
    results: list[BenchmarkResult], llm: CriticModel, *, limit: int
) -> list[BenchmarkResult]:
    reviewed: list[BenchmarkResult] = []
    for index, result in enumerate(results):
        if index >= limit:
            reviewed.append(result)
            continue
        reviewed.append(replace(result, critic=critic_review(result, llm)))
    return sort_results(reviewed)


def critic_review(result: BenchmarkResult, llm: CriticModel) -> CriticReview:
    try:
        response = llm.invoke(build_critic_messages(result))
    except Exception as exc:  # noqa: BLE001 - keep CLI error focused on critic failure.
        raise CriticConfigError(f"Critic call failed: {exc}") from exc

    content = getattr(response, "content", response)
    try:
        return CriticReview.from_dict(parse_json_object(message_content_to_text(content)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        text = message_content_to_text(content)
        raise CriticConfigError(f"Critic returned invalid JSON: {text!r}") from exc


def build_critic_messages(result: BenchmarkResult) -> list[dict[str, str]]:
    post = result.post
    payload = {
        "post": {
            "subreddit": post.subreddit,
            "title": post.title,
            "url": post.url,
            "permalink": post.permalink,
            "score": post.score,
            "comment_count": post.comment_count,
        },
        "heuristic": {
            "score": result.score,
            "priority": result.priority,
            "signals": list(result.signals),
            "matched_terms": list(result.matched_terms),
        },
    }
    return [
        {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = strip_code_fence(text.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise TypeError("Critic JSON must be an object.")
    return data


def strip_code_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def sort_results(results: Iterable[BenchmarkResult]) -> list[BenchmarkResult]:
    return sorted(
        results,
        key=lambda result: (
            1 if result.critic else 0,
            result.critic.usefulness_score if result.critic else result.score,
            result.score,
            result.post.comment_count or 0,
            result.post.score or 0,
        ),
        reverse=True,
    )


def searchable_text(post: RedditPost) -> str:
    return " ".join(
        value.lower()
        for value in (post.title, post.url, post.permalink, post.subreddit)
        if value
    )


def engagement_bonus(value: int | None, *, limit: int) -> int:
    if not value or value <= 0:
        return 0
    return min(limit, round(math.log10(value + 1) * 4))


def priority_for_score(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def clamp_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def choice(value: Any, *, choices: tuple[str, ...], default: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in choices else default


def clean_text(value: Any, *, limit: int) -> str:
    cleaned = " ".join(str(value or "").split())
    return cleaned[:limit]


def write_markdown_report(results: list[BenchmarkResult], path: Path, *, top: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    high_count = sum(1 for result in results if result.priority == "high")
    medium_count = sum(1 for result in results if result.priority == "medium")
    low_count = sum(1 for result in results if result.priority == "low")
    has_critic = any(result.critic for result in results)
    headers = ["Rank", "Priority", "Score", "Subreddit", "Title", "Signals"]
    if has_critic:
        headers.append("Critic")
    headers.append("Link")

    lines = [
        "# Reddit Post Importance Benchmark",
        "",
        f"Generated: {utc_now_iso()}",
        f"Posts scored: {len(results)}",
        f"Priority counts: high={high_count}, medium={medium_count}, low={low_count}",
        "",
        "## Scoring Focus",
        "",
        (
            "High score means likely useful for technical recon: findings, tools, setup tips, "
            "fixes, tutorials, workflows, or implementation examples."
        ),
        "",
        f"| {' | '.join(headers)} |",
        f"| {' | '.join(markdown_separator(header) for header in headers)} |",
    ]

    for rank, result in enumerate(results[:top], start=1):
        post = result.post
        link = post.permalink or post.url or ""
        values = [
            str(rank),
            result.priority,
            str(result.score),
            f"r/{post.subreddit}",
            post.title,
            ", ".join(result.signals) or "none",
        ]
        if has_critic:
            values.append(format_critic(result.critic))
        values.append(link)
        lines.append(f"| {' | '.join(escape_table(value) for value in values)} |")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_jsonl(results: list[BenchmarkResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
    return path


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def markdown_separator(header: str) -> str:
    return "---:" if header in {"Rank", "Score"} else "---"


def format_critic(critic: CriticReview | None) -> str:
    if not critic:
        return "not reviewed"
    return (
        f"{critic.usefulness_score} {critic.verdict} ({critic.confidence}): "
        f"{critic.rationale} Action: {critic.suggested_action}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
