from __future__ import annotations

import json
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import SearchResult


DEFAULT_GOOGLE_SERP_ACTOR = "apify/google-search-scraper"
DEFAULT_INSTAGRAM_SCRAPER_ACTOR = "apify/instagram-scraper"
DEFAULT_INSTAGRAM_PROFILE_ACTOR = "apify/instagram-profile-scraper"


class ApifyClient:
    def __init__(
        self,
        token: str,
        google_serp_actor: str = DEFAULT_GOOGLE_SERP_ACTOR,
        instagram_scraper_actor: str = DEFAULT_INSTAGRAM_SCRAPER_ACTOR,
        instagram_profile_actor: str = DEFAULT_INSTAGRAM_PROFILE_ACTOR,
    ):
        self.token = token
        self.google_serp_actor = google_serp_actor
        self.instagram_scraper_actor = instagram_scraper_actor
        self.instagram_profile_actor = instagram_profile_actor

    def enabled(self) -> bool:
        return bool(self.token and self.token != "YOUR_APIFY_API_TOKEN_HERE")

    def google_search(self, query: str, count: int = 10) -> list[SearchResult]:
        if not self.enabled():
            return []
        data = self.run_actor(
            self.google_serp_actor,
            {
                "queries": query,
                "resultsPerPage": min(max(count, 1), 20),
                "maxPagesPerQuery": 1,
                "languageCode": "en",
            },
        )
        results = []
        for item in flatten_items(data):
            title = first_value(item, ["title", "name", "organicTitle"])
            url = first_value(item, ["url", "link", "organicUrl"])
            snippet = first_value(item, ["description", "snippet", "text"])
            if title and url:
                results.append(SearchResult(title=title, url=url, snippet=snippet))
        return dedupe_results(results)[:count]

    def instagram_public_evidence(self, subject: str, candidate_urls: list[str], max_items: int = 8) -> list[SearchResult]:
        if not self.enabled():
            return []
        usernames = extract_instagram_usernames(candidate_urls)
        results: list[SearchResult] = []
        for username in usernames[:3]:
            profile_items = self.run_actor(
                self.instagram_profile_actor,
                {
                    "usernames": [username],
                    "resultsLimit": 1,
                },
            )
            results.extend(instagram_items_to_results(profile_items, subject, "profile"))

            public_url = f"https://www.instagram.com/{username}/"
            post_items = self.run_actor(
                self.instagram_scraper_actor,
                {
                    "directUrls": [public_url],
                    "resultsLimit": max_items,
                    "searchLimit": max_items,
                },
            )
            results.extend(instagram_items_to_results(post_items, subject, "public posts"))
        return dedupe_results(results)[:max_items]

    def run_actor(self, actor_id: str, payload: dict) -> list[dict]:
        actor = quote(actor_id, safe="")
        run_url = f"https://api.apify.com/v2/acts/{actor}/runs?token={quote(self.token)}"
        request = Request(
            run_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            run = json.loads(response.read().decode("utf-8")).get("data", {})
        run_id = run.get("id")
        if not run_id:
            return []

        for _ in range(90):
            status_url = f"https://api.apify.com/v2/actor-runs/{quote(run_id)}?token={quote(self.token)}"
            with urlopen(status_url, timeout=30) as response:
                status_data = json.loads(response.read().decode("utf-8")).get("data", {})
            status = status_data.get("status")
            if status == "SUCCEEDED":
                dataset_id = status_data.get("defaultDatasetId")
                return self.dataset_items(dataset_id)
            if status in {"FAILED", "ABORTED", "TIMED-OUT"}:
                return []
            time.sleep(2)
        return []

    def dataset_items(self, dataset_id: str) -> list[dict]:
        if not dataset_id:
            return []
        url = f"https://api.apify.com/v2/datasets/{quote(dataset_id)}/items?clean=true&token={quote(self.token)}"
        with urlopen(url, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data if isinstance(data, list) else []


def flatten_items(items) -> list[dict]:
    output = []
    if isinstance(items, dict):
        items = [items]
    for item in items or []:
        if not isinstance(item, dict):
            continue
        output.append(item)
        for key in ("organicResults", "results", "searchResults"):
            nested = item.get(key)
            if isinstance(nested, list):
                output.extend(value for value in nested if isinstance(value, dict))
    return output


def first_value(item: dict, keys: list[str]) -> str:
    for key in keys:
        value = item.get(key)
        if value:
            return " ".join(str(value).split())
    return ""


def extract_instagram_usernames(urls: list[str]) -> list[str]:
    usernames = []
    blocked = {"p", "reel", "tv", "stories", "explore", "accounts"}
    for url in urls:
        match = re.search(r"instagram\.com/([A-Za-z0-9_.]+)/?", url)
        if not match:
            continue
        username = match.group(1).strip(".")
        if username.lower() not in blocked and username not in usernames:
            usernames.append(username)
    return usernames


def instagram_items_to_results(items: list[dict], subject: str, source_type: str) -> list[SearchResult]:
    results = []
    for item in flatten_items(items):
        url = first_value(item, ["url", "postUrl", "profileUrl", "inputUrl"])
        username = first_value(item, ["username", "ownerUsername", "fullName"])
        caption = first_value(item, ["caption", "biography", "description", "text"])
        if not url or "instagram.com" not in url:
            continue
        title = f"Instagram {source_type}: {username or subject}"
        results.append(SearchResult(title=title, url=url, snippet=caption[:1000], page_text=caption[:5000]))
    return results


def dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    seen = set()
    output = []
    for result in results:
        key = result.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        output.append(result)
    return output

