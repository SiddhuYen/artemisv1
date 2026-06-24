from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass
class AgentConfig:
    brave_api_key: str
    gemini_api_key: str
    apify_api_token: str = ""
    apify_google_serp_actor: str = "apify/google-search-scraper"
    apify_instagram_scraper_actor: str = "apify/instagram-scraper"
    apify_instagram_profile_actor: str = "apify/instagram-profile-scraper"
    use_apify_serp: bool = False
    use_apify_instagram: bool = False
    gemini_model: str = "gemini-2.5-flash"
    max_depth: int = 3
    max_search_results: int = 5
    max_pages_per_query: int = 3
    max_queries_per_node: int = 6
    min_confidence: float = 0.45
    graph_path: str = "search_agent_graph.json"
    allow_insecure_ssl: bool = False

    @classmethod
    def from_env(cls, **overrides):
        load_env()
        config = cls(
            brave_api_key=os.environ.get("BRAVE_SEARCH_API_KEY", "").strip(),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            apify_api_token=os.environ.get("APIFY_API_TOKEN", "").strip(),
            apify_google_serp_actor=os.environ.get("APIFY_GOOGLE_SERP_ACTOR", "apify/google-search-scraper").strip(),
            apify_instagram_scraper_actor=os.environ.get("APIFY_INSTAGRAM_SCRAPER_ACTOR", "apify/instagram-scraper").strip(),
            apify_instagram_profile_actor=os.environ.get(
                "APIFY_INSTAGRAM_PROFILE_ACTOR",
                "apify/instagram-profile-scraper",
            ).strip(),
            use_apify_serp=os.environ.get("USE_APIFY_SERP", "").strip().lower() in {"1", "true", "yes"},
            use_apify_instagram=os.environ.get("USE_APIFY_INSTAGRAM", "").strip().lower() in {"1", "true", "yes"},
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        )
        for key, value in overrides.items():
            if value is not None and hasattr(config, key):
                setattr(config, key, value)
        return config

    def validate(self) -> None:
        if not self.brave_api_key or self.brave_api_key == "YOUR_BRAVE_SEARCH_API_KEY_HERE":
            raise RuntimeError("Set BRAVE_SEARCH_API_KEY in .env.")
        if not self.gemini_api_key or self.gemini_api_key == "YOUR_GEMINI_API_KEY_HERE":
            raise RuntimeError("Set GEMINI_API_KEY in .env.")
        if (self.use_apify_serp or self.use_apify_instagram) and (
            not self.apify_api_token or self.apify_api_token == "YOUR_APIFY_API_TOKEN_HERE"
        ):
            raise RuntimeError("Set APIFY_API_TOKEN in .env before enabling Apify sources.")
