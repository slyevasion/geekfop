from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reddit_scraper.benchmark import (  # noqa: E402
    CriticConfigError,
    benchmark_post,
    benchmark_posts,
    build_opencode_critic,
    parse_json_object,
    review_with_critic,
)
from reddit_scraper.models import RedditPost  # noqa: E402


class FakeCriticResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeCritic:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    def invoke(self, messages: list[dict[str, str]]) -> Any:
        self.calls.append(messages)
        return FakeCriticResponse(self.content)


class BenchmarkTests(unittest.TestCase):
    def test_scores_setup_tools_and_technical_findings_high(self) -> None:
        post = RedditPost(
            subreddit="LocalLLaMA",
            title="Guide: setup vLLM with CUDA Docker and benchmark inference latency",
            url="https://github.com/example/tool",
            score=120,
            comment_count=42,
        )

        result = benchmark_post(post)

        self.assertEqual(result.priority, "high")
        self.assertIn("technical finding", result.signals)
        self.assertIn("tool or library", result.signals)
        self.assertIn("setup tip or trick", result.signals)

    def test_scores_low_value_posts_low(self) -> None:
        post = RedditPost(
            subreddit="LocalLLaMA",
            title="Friday meme thread with funny AI image",
            score=1,
            comment_count=0,
        )

        result = benchmark_post(post)

        self.assertEqual(result.priority, "low")
        self.assertLess(result.score, 25)

    def test_benchmark_posts_sorts_by_score(self) -> None:
        low = RedditPost(subreddit="automation", title="General discussion")
        high = RedditPost(
            subreddit="AI_Agents",
            title="How to debug MCP tool calling workflow with OpenCode CLI",
        )

        results = benchmark_posts([low, high])

        self.assertEqual(results[0].post, high)

    def test_critic_reviews_top_results(self) -> None:
        low = RedditPost(subreddit="automation", title="General discussion")
        high = RedditPost(
            subreddit="LocalLLaMA",
            title="Benchmark: vLLM CUDA deployment guide with latency fixes",
        )
        fake = FakeCritic(
            """
            ```json
            {
              "usefulness_score": 91,
              "verdict": "keep",
              "confidence": "high",
              "rationale": "Likely actionable benchmark and setup guidance.",
              "suggested_action": "Open and extract deployment steps."
            }
            ```
            """
        )

        results = review_with_critic(benchmark_posts([low, high]), fake, limit=1)

        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(results[0].post, high)
        self.assertIsNotNone(results[0].critic)
        self.assertEqual(results[0].critic.usefulness_score, 91)
        self.assertEqual(results[0].critic.verdict, "keep")
        self.assertIn("critic", results[0].to_dict())
        self.assertIn("heuristic", fake.calls[0][1]["content"])

    def test_parse_json_object_finds_json_inside_text(self) -> None:
        data = parse_json_object('preface {"usefulness_score": 55, "verdict": "watch"} suffix')

        self.assertEqual(data["usefulness_score"], 55)
        self.assertEqual(data["verdict"], "watch")

    def test_opencode_critic_requires_api_key(self) -> None:
        with self.assertRaises(CriticConfigError):
            build_opencode_critic(env={})


if __name__ == "__main__":
    unittest.main()
