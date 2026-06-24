from __future__ import annotations

import html
import json
import re
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

from .models import SearchResult


BLOCKED_HOST_HINTS = {"support.google.com", "policies.google.com", "accounts.google.com"}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self.parts.append(cleaned)

    def text(self) -> str:
        return " ".join(self.parts)


def ssl_context(allow_insecure_ssl: bool = False):
    if allow_insecure_ssl:
        return ssl._create_unverified_context()
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def is_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    if not host or any(hint in host for hint in BLOCKED_HOST_HINTS):
        return False
    return True


class BraveSearchClient:
    def __init__(self, api_key: str, allow_insecure_ssl: bool = False):
        self.api_key = api_key
        self.allow_insecure_ssl = allow_insecure_ssl

    def search(self, query: str, count: int = 5) -> list[SearchResult]:
        url = (
            "https://api.search.brave.com/res/v1/web/search"
            f"?q={quote_plus(query)}&count={min(max(count, 1), 20)}&text_decorations=false"
        )
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "X-Subscription-Token": self.api_key,
            },
        )
        with urlopen(request, timeout=30, context=ssl_context(self.allow_insecure_ssl)) as response:
            data = json.loads(response.read().decode("utf-8"))
        results: list[SearchResult] = []
        for item in data.get("web", {}).get("results", []):
            title = " ".join(html.unescape(item.get("title", "")).split())
            result_url = item.get("url", "")
            snippet = " ".join(html.unescape(item.get("description", "")).split())
            if title and is_public_url(result_url):
                results.append(SearchResult(title=title, url=result_url, snippet=snippet))
        return dedupe_results(results)


def dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    seen = set()
    output = []
    for result in results:
        key = result.url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        output.append(result)
    return output


def fetch_page_text(url: str, allow_insecure_ssl: bool = False, max_chars: int = 14000) -> str:
    if not is_public_url(url):
        return ""
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 public-source-relationship-research/1.0",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=20, context=ssl_context(allow_insecure_ssl)) as response:
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            return ""
        charset = response.headers.get_content_charset() or "utf-8"
        html_text = response.read(max_chars * 4).decode(charset, errors="replace")
    parser = TextExtractor()
    parser.feed(html_text)
    text = re.sub(r"\s+", " ", parser.text()).strip()
    time.sleep(0.15)
    return text[:max_chars]
