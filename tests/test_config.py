from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reddit_scraper.config import (  # noqa: E402
    ConfigError,
    OxylabsProxyConfig,
    ScraperSettings,
    load_dotenv,
    normalize_subreddits,
)


class ConfigTests(unittest.TestCase):
    def test_proxy_url_without_scheme(self) -> None:
        config = OxylabsProxyConfig.from_proxy_url("customer-user:pass@pr.oxylabs.io:7777")
        self.assertEqual(config.server, "http://pr.oxylabs.io:7777")
        self.assertEqual(config.username, "customer-user")
        self.assertEqual(config.password, "pass")

    def test_explicit_proxy_env(self) -> None:
        config = OxylabsProxyConfig.from_env(
            {
                "OXYLABS_PROXY_SERVER": "pr.oxylabs.io:7777",
                "OXYLABS_PROXY_USERNAME": "customer-user",
                "OXYLABS_PROXY_PASSWORD": "pass",
            }
        )
        self.assertEqual(config.server, "http://pr.oxylabs.io:7777")
        self.assertEqual(config.to_playwright_proxy()["username"], "customer-user")

    def test_missing_proxy_credentials(self) -> None:
        with self.assertRaises(ConfigError):
            OxylabsProxyConfig.from_env({})

    def test_normalize_subreddits(self) -> None:
        self.assertEqual(
            normalize_subreddits(["r/LocalLLaMA", "LocalLLaMA", "AI_Agents"]),
            ("LocalLLaMA", "AI_Agents"),
        )

    def test_limit_override_must_be_positive(self) -> None:
        with self.assertRaises(ConfigError):
            ScraperSettings().with_overrides(limit=0)

    def test_load_dotenv_no_override(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("EXISTING=from-file\nNEW_VALUE='hello'\n", encoding="utf-8")
            old_existing = os.environ.get("EXISTING")
            os.environ["EXISTING"] = "from-env"
            try:
                loaded = load_dotenv(env_path)
                self.assertEqual(os.environ["EXISTING"], "from-env")
                self.assertEqual(os.environ["NEW_VALUE"], "hello")
                self.assertEqual(loaded, ["NEW_VALUE"])
            finally:
                if old_existing is None:
                    os.environ.pop("EXISTING", None)
                else:
                    os.environ["EXISTING"] = old_existing
                os.environ.pop("NEW_VALUE", None)


if __name__ == "__main__":
    unittest.main()
