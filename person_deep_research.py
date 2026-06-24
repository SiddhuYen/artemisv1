import argparse
import csv
import html
import json
import os
import re
import ssl
import subprocess
import textwrap
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from search_agent.apify_client import ApifyClient


DEFAULT_PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_PROFILES_CSV = "linkedin_network_profiles.csv"


class SearchResultParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._in_link = False
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        if tag == "a" and ({"result-link", "result__a"} & classes):
            self._in_link = True
            self._href = attrs.get("href")
            self._text = []

    def handle_data(self, data):
        if self._in_link:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._in_link:
            title = html.unescape(" ".join("".join(self._text).split()))
            if title and self._href:
                self.results.append({"title": title, "url": clean_search_url(html.unescape(self._href))})
            self._in_link = False
            self._href = None
            self._text = []


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            cleaned = " ".join(data.split())
            if cleaned:
                self.parts.append(cleaned)

    def text(self):
        return " ".join(self.parts)


def ssl_context(allow_insecure_ssl=False):
    if allow_insecure_ssl:
        return ssl._create_unverified_context()
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def clean_search_url(url):
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        target = parse_qs(parsed.query).get("q", [""])[0]
        if target:
            return unquote(target)
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/a"):
        target = parse_qs(parsed.query).get("u", [""])[0]
        if target:
            return unquote(target)
    return url


def fetch_url(url, timeout=15, allow_insecure_ssl=False):
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=timeout, context=ssl_context(allow_insecure_ssl)) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def search_web(query, limit=8, pause=0.35, allow_insecure_ssl=False, provider="brave"):
    providers = ["brave", "google", "duckduckgo", "bing"] if provider == "all" else [provider]
    all_results = []
    errors = []
    for search_provider in providers:
        try:
            if search_provider == "brave":
                results = brave_search_results(query, limit, allow_insecure_ssl=allow_insecure_ssl)
            elif search_provider == "apify":
                results = apify_search_results(query, limit)
            else:
                search_html = fetch_url(
                    search_url(query, search_provider),
                    allow_insecure_ssl=allow_insecure_ssl,
                )
                results = parse_search_results(search_html, search_provider)
            results = [result for result in results if is_real_result_url(result.get("url", ""))]
            all_results.extend(results)
            if results:
                break
        except Exception as error:
            errors.append(f"{search_provider}: {error}")
            continue

    if not all_results and errors:
        raise RuntimeError("; ".join(errors))
    if pause:
        time.sleep(pause)
    return dedupe_results(all_results)[:limit]


def apify_client_from_env():
    return ApifyClient(
        os.environ.get("APIFY_API_TOKEN", "").strip(),
        os.environ.get("APIFY_GOOGLE_SERP_ACTOR", "apify/google-search-scraper").strip(),
        os.environ.get("APIFY_INSTAGRAM_SCRAPER_ACTOR", "apify/instagram-scraper").strip(),
        os.environ.get("APIFY_INSTAGRAM_PROFILE_ACTOR", "apify/instagram-profile-scraper").strip(),
    )


def apify_search_results(query, limit=8):
    client = apify_client_from_env()
    if not client.enabled():
        raise RuntimeError("Set APIFY_API_TOKEN in .env to use Apify search.")
    return [
        {"title": result.title, "url": result.url, "snippet": result.snippet}
        for result in client.google_search(query, limit)
    ]


def apify_instagram_evidence(person, urls, max_items=5):
    client = apify_client_from_env()
    if not client.enabled():
        return []
    results = client.instagram_public_evidence(person, urls, max_items=max_items)
    evidence = []
    for result in results:
        text = "\n".join(part for part in [result.snippet, result.page_text] if part)
        if not result.url or not text:
            continue
        evidence.append(
            {
                "query": f'Apify Instagram public scrape for "{person}"',
                "title": result.title,
                "url": result.url,
                "text": text,
            }
        )
    return evidence


def brave_search_results(query, limit=8, allow_insecure_ssl=False):
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key or api_key == "YOUR_BRAVE_SEARCH_API_KEY_HERE":
        raise RuntimeError("Set BRAVE_SEARCH_API_KEY in .env to use Brave Search.")

    url = (
        "https://api.search.brave.com/res/v1/web/search"
        f"?q={quote_plus(query)}&count={min(max(limit, 1), 20)}&text_decorations=false"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "X-Subscription-Token": api_key,
        },
    )
    with urlopen(request, timeout=30, context=ssl_context(allow_insecure_ssl)) as response:
        data = json.loads(response.read().decode("utf-8"))

    results = []
    for item in data.get("web", {}).get("results", []):
        title = " ".join(html.unescape(item.get("title", "")).split())
        result_url = item.get("url", "")
        description = " ".join(html.unescape(item.get("description", "")).split())
        if title and result_url:
            results.append({"title": title, "url": result_url, "snippet": description})
    return results


def search_url(query, provider):
    encoded = quote_plus(query)
    if provider == "google":
        return f"https://www.google.com/search?q={encoded}&num=10&hl=en"
    if provider == "duckduckgo":
        return f"https://html.duckduckgo.com/html/?q={encoded}"
    if provider == "bing":
        return f"https://www.bing.com/search?q={encoded}"
    raise ValueError(f"Unknown search provider: {provider}")


def parse_search_results(search_html, provider):
    parser = SearchResultParser()
    parser.feed(search_html)
    results = parser.results
    if not results:
        results = regex_search_results(search_html)
    if provider == "google":
        results.extend(google_regex_results(search_html))
    return results


def google_regex_results(search_html):
    results = []
    for href, title in re.findall(r'<a href="(/url\?q=[^"]+)"[^>]*>(.*?)</a>', search_html, re.I | re.S):
        raw_title = re.sub(r"<[^>]+>", " ", title)
        title = " ".join(html.unescape(raw_title).split())
        url = clean_search_url(f"https://www.google.com{html.unescape(href)}")
        if title and is_real_result_url(url):
            results.append({"title": title, "url": url})
    return results


def regex_search_results(search_html):
    results = []
    for match in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', search_html, re.I | re.S):
        href = html.unescape(match.group(1))
        raw_title = re.sub(r"<[^>]+>", " ", match.group(2))
        title = " ".join(html.unescape(raw_title).split())
        url = clean_search_url(href)
        if not title or not url:
            continue
        if not is_real_result_url(url):
            continue
        results.append({"title": title, "url": url})
    return results


def is_real_result_url(url):
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        return False
    blocked_domains = (
        "google.com",
        "duckduckgo.com",
        "bing.com",
        "microsoft.com",
        "gstatic.com",
        "googleusercontent.com",
    )
    host = parsed.netloc.lower()
    if any(host == domain or host.endswith(f".{domain}") for domain in blocked_domains):
        return False
    blocked_paths = ("/search", "/preferences", "/setprefs")
    return not any(parsed.path.startswith(path) for path in blocked_paths)


def dedupe_results(results):
    seen = set()
    unique = []
    for result in results:
        parsed = urlparse(result.get("url", ""))
        key = (parsed.netloc, parsed.path)
        if not result.get("url") or key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


def page_text(url, max_chars=10000, allow_insecure_ssl=False):
    extractor = TextExtractor()
    extractor.feed(fetch_url(url, allow_insecure_ssl=allow_insecure_ssl))
    return extractor.text()[:max_chars]


def strip_terminal_control_codes(text):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def gemini_generate(model, prompt, allow_insecure_ssl=False, retries=4):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "YOUR_GEMINI_API_KEY_HERE":
        raise SystemExit("Set GEMINI_API_KEY in .env before using the Gemini provider.")

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    last_error = None
    retry_statuses = {408, 429, 500, 502, 503, 504}

    for attempt in range(1, retries + 1):
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=600, context=ssl_context(allow_insecure_ssl)) as response:
                data = json.loads(response.read().decode("utf-8"))
                break
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {error.code}: {body or error.reason}"
            if error.code not in retry_statuses or attempt == retries:
                raise SystemExit(f"Gemini API call failed after {attempt} attempt(s): {last_error}") from error
        except URLError as error:
            last_error = str(error)
            if attempt == retries:
                raise SystemExit(f"Gemini API call failed after {attempt} attempt(s): {last_error}") from error

        sleep_seconds = min(45, 2 ** attempt)
        print(f"Gemini call failed ({last_error}); retrying in {sleep_seconds}s...", flush=True)
        time.sleep(sleep_seconds)
    else:
        raise SystemExit(f"Gemini API call failed: {last_error}")

    candidates = data.get("candidates", [])
    if not candidates:
        raise SystemExit(f"Gemini returned no candidates: {json.dumps(data)[:1000]}")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts)
    return strip_terminal_control_codes(text.strip())


def ollama_generate(model, prompt):
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    try:
        request = Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=600) as response:
            data = json.loads(response.read().decode("utf-8"))
            return strip_terminal_control_codes(data.get("response", "").strip())
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            text=True,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as error:
        raise SystemExit("Could not find `ollama`. Install Ollama and make sure the `ollama` command is on PATH.") from error
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr.strip() or error.stdout.strip() or "Ollama failed.") from error
    return strip_terminal_control_codes(result.stdout.strip())


def generate_text(provider, model, prompt, allow_insecure_ssl=False):
    if provider == "gemini":
        return gemini_generate(model, prompt, allow_insecure_ssl=allow_insecure_ssl)
    if provider == "ollama":
        return ollama_generate(model, prompt)
    raise SystemExit(f"Unsupported provider: {provider}")


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def unique(values):
    seen = set()
    output = []
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        key = normalize(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def load_local_profile_matches(path, name, max_matches=8):
    if not os.path.exists(path):
        return []

    name_terms = [term for term in normalize(name).split() if len(term) > 2]
    matches = []
    with open(path, "r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            haystack = normalize(
                " ".join(
                    [
                        row.get("name", ""),
                        row.get("company", ""),
                        row.get("position", ""),
                        row.get("profile_url", ""),
                        row.get("extracted_companies", ""),
                        row.get("extracted_schools", ""),
                        row.get("extracted_roles", ""),
                        row.get("profile_text", "")[:5000],
                    ]
                )
            )
            score = sum(1 for term in name_terms if term in haystack)
            if score:
                matches.append((score, row))

    matches.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in matches[:max_matches]]


def build_evidence(
    queries,
    max_results,
    max_pages,
    allow_insecure_ssl=False,
    search_provider="all",
    instagram_person="",
    use_apify_instagram=False,
):
    evidence = []
    seen_urls = set()
    for query in queries:
        print(f"Searching: {query}", flush=True)
        try:
            results = search_web(
                query,
                limit=max_results,
                allow_insecure_ssl=allow_insecure_ssl,
                provider=search_provider,
            )
        except Exception as error:
            print(f"Search failed: {error}", flush=True)
            evidence.append({"query": query, "title": "Search failed", "url": "", "text": f"[Search failed: {error}]"})
            continue

        print(f"Found {len(results)} result(s)", flush=True)
        for result in results:
            if result["url"] in seen_urls:
                continue
            seen_urls.add(result["url"])
            item = {"query": query, "title": result["title"], "url": result["url"], "text": result.get("snippet", "")}
            if len([e for e in evidence if e.get("text")]) < max_pages:
                try:
                    print(f"Reading: {result['url']}", flush=True)
                    page = page_text(result["url"], allow_insecure_ssl=allow_insecure_ssl)
                    item["text"] = "\n".join(part for part in [result.get("snippet", ""), page] if part)
                except Exception as error:
                    print(f"Read failed: {error}", flush=True)
                    if item["text"]:
                        item["text"] = f"{item['text']}\n[Could not read page: {error}]"
                    else:
                        item["text"] = f"[Could not read page: {error}]"
            evidence.append(item)
        if use_apify_instagram:
            instagram_urls = [result["url"] for result in results if "instagram.com" in result.get("url", "")]
            if instagram_urls:
                try:
                    print("Reading public Instagram evidence through Apify", flush=True)
                    for item in apify_instagram_evidence(instagram_person or query, instagram_urls, max_pages):
                        if item["url"] not in seen_urls:
                            seen_urls.add(item["url"])
                            evidence.append(item)
                except Exception as error:
                    print(f"Apify Instagram scrape failed: {error}", flush=True)
    return evidence


def usable_evidence(evidence):
    unusable_prefixes = ("[Search failed:", "[Could not read page:")
    return [
        item
        for item in evidence
        if item.get("url") and item.get("text") and not item["text"].startswith(unusable_prefixes)
    ]


def evidence_block(evidence):
    blocks = []
    for index, item in enumerate(evidence, 1):
        blocks.append(
            textwrap.dedent(
                f"""
                SOURCE {index}
                Query: {item['query']}
                Title: {item['title']}
                URL: {item['url']}
                Text: {item['text'][:5000]}
                """
            ).strip()
        )
    return "\n\n".join(blocks)


def local_profiles_block(matches, enabled=True):
    if not enabled:
        return "Local LinkedIn CSV evidence was not included for this run."
    blocks = []
    for index, row in enumerate(matches, 1):
        blocks.append(
            textwrap.dedent(
                f"""
                LOCAL PROFILE {index}
                Name: {row.get('name', '')}
                URL: {row.get('profile_url', '')}
                Current company: {row.get('company', '')}
                Current position: {row.get('position', '')}
                Extracted companies: {row.get('extracted_companies', '')}
                Extracted schools: {row.get('extracted_schools', '')}
                Extracted roles: {row.get('extracted_roles', '')}
                Hook keywords: {row.get('hook_keywords', '')}
                Profile text excerpt: {row.get('profile_text', '')[:7000]}
                """
            ).strip()
        )
    return "\n\n".join(blocks) if blocks else "No local CSV matches."


def person_queries(name, context):
    context_part = f' "{context}"' if context else ""
    queries = [f'"{name}"{context_part}']
    if context:
        queries.append(f'"{name}"')
    return queries


def created_institution_queries(name, context="", limit=18):
    context_terms = [context] if context else []
    institution_terms = [
        "school",
        "academy",
        "university",
        "fellowship",
        "foundation",
        "nonprofit",
        "lab",
        "institute",
        "program",
        "community",
        "competition",
        "accelerator",
        "grant",
        "scholarship",
    ]
    relationship_terms = [
        "founded",
        "cofounded",
        "created",
        "started",
        "backed",
        "launched",
    ]
    queries = []
    for relationship in relationship_terms[:4]:
        for institution in institution_terms[:6]:
            queries.append(f'"{name}" {relationship} {institution}')
    for institution in institution_terms:
        queries.append(f'"{name}" "{institution}" alumni staff founders')
    for term in context_terms:
        queries.append(f'"{name}" "{term}" school foundation program alumni')
    return unique(queries)[:limit]


def institution_followup_queries(evidence, target_name, limit=12):
    text = "\n".join(
        " ".join([item.get("title", ""), item.get("text", "")[:2000]])
        for item in evidence
        if item.get("text")
    )
    candidates = []
    patterns = [
        r"\b[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,4}\s+(?:School|Academy|Foundation|Institute|Lab|Labs|Fellowship|Program|Community|Competition|Accelerator|Scholarship)\b",
        r"\b(?:Ad Astra|Astra Nova|Synthesis(?: School)?)\b",
    ]
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text))
    queries = []
    for org in unique(candidates):
        if normalize(target_name) in normalize(org):
            continue
        queries.extend(
            [
                f'"{org}" "{target_name}"',
                f'"{org}" alumni',
                f'"{org}" staff founders',
                f'"{org}" leadership team',
            ]
        )
    return unique(queries)[:limit]


def dossier_prompt(name, context, evidence, local_matches, include_local_profiles=False):
    return f"""
You are compiling notes from search results about a person.

Person: {name}
Optional context/disambiguator: {context or "none"}
Generated at: {datetime.now(timezone.utc).isoformat()}

Use only the evidence below. Do not invent facts. If a source appears to be about a different person with the
same name, mark it as "likely different person" and do not merge its details into the main identity.

Do not include private contact info, home addresses, private family details, minors, or speculative personal-life claims.
Focus on public/professional and self-published information.

Output Markdown with these exact sections:

## Identity Check
- Who the first search results appear to refer to.
- Whether the sources seem to describe one person or multiple people with the same name.

## Source-by-Source Notes
For every source, write:
- Source: [title](url)
- Relevance: [about target / maybe target / likely different person / unreadable]
- Details found:
  - [specific fact from this page]
  - [specific fact from this page]

## Consolidated Details
- Merge only facts that appear to refer to the same target identity.
- Include roles, companies, schools, projects, locations at broad level, articles, talks, awards, clubs, and any other useful public hooks.

## Adjacent People To Investigate
- List less-famous / closer-to-target people found in the sources.
- Include cofounders, early employees, coworkers, investors, board members, article authors, podcast hosts, interviewers, school/community peers, event organizers, target-created institution staff/alumni, and named collaborators.
- For each person: name, connection to target, organization/context, source URL/title, and why they may be closer to the target.
- Do not include random famous people unless the source directly connects them to the target.

## Artemis Target Map
Create an endpoint-first map for warm-intro pathfinding.

First write this exact fenced JSON block:

```json
{{
  "target": "{name}",
  "closest_people": [
    {{
      "name": "Full name",
      "relationship_to_target": "direct report / coworker / cofounder / board / investor / advisor / collaborator / interviewer / author / event peer",
      "organization": "Company, school, fund, publication, project, or event",
      "source_url": "https://...",
      "source_title": "Source title",
      "proof": "One sentence explaining why this source proves a meaningful professional relationship.",
      "strength": "strong / moderate / weak",
      "freshness": "current / recent / old but relevant / stale / unknown",
      "bridge_terms": ["Exact person name", "Exact organization", "Exact project or event"]
    }}
  ],
  "second_layer_nodes": [
    {{
      "name": "Full name",
      "relationship_to_closest_person": "cofounder / coworker / board / investor / collaborator / interviewer / coauthor / direct report",
      "closest_person_name": "Name from closest_people",
      "organization": "Company, school, fund, publication, project, or event",
      "source_url": "https://...",
      "source_title": "Source title",
      "proof": "One sentence proving this person has a meaningful professional relationship with the closest_person_name.",
      "strength": "strong / moderate / weak",
      "freshness": "current / recent / old but relevant / stale / unknown",
      "bridge_terms": ["Exact person name", "Exact organization", "Exact project or event"]
    }}
  ],
  "second_layer_queries": [
    "\\"Close Person Name\\" \\"Organization\\"",
    "\\"Close Person Name\\" collaborator"
  ],
  "rejected_clues": [
    {{
      "term": "Broad clue",
      "reason": "same industry / same large institution / too vague / no named relationship"
    }}
  ]
}}
```

Then summarize the same map in bullets.

Rules for the map:
- Only include people with source-backed, named professional relationships to the target.
- Prefer close operational relationships over famous-but-distant people.
- Treat target-created schools, academies, fellowships, labs, foundations, nonprofits, competitions, and communities as useful bridge institutions when source-backed.
- For those institutions, include public founders, staff, public alumni, board members, advisors, or program leaders as closest_people or second_layer_nodes when their connection is source-backed.
- Do not include same city, same broad industry, same large school, or vague ecosystem overlap as closest_people.
- Every closest_people item must have a source_url and a one-sentence proof.
- second_layer_nodes are people connected to closest_people, not random people connected only to the target's broad field.
- Every second_layer_nodes item must have a source_url and one-sentence proof connecting it to the closest_person_name.
- Prefer second_layer_nodes that a local network might realistically reach: coauthors, project collaborators, operators, board colleagues, interviewers, funders, direct coworkers.
- Put weak broad terms in rejected_clues, not closest_people.

## Best Facts To Use
- The most useful, concrete facts found, each with source title or URL.

## Unverified / Ambiguous
- Facts or identities that need verification before trusting them.

## Sources
- List source URLs used.

Local LinkedIn CSV evidence:
{local_profiles_block(local_matches, enabled=include_local_profiles)}

Web evidence:
{evidence_block(evidence)}
""".strip()


def adjacent_people_prompt(name, context, evidence):
    return f"""
Extract less-famous adjacent people and organizations connected to this target from the evidence.

Target: {name}
Context: {context or "none"}

Return JSON only:
{{
  "people": [
    {{
      "name": "Full name",
      "relationship_to_target": "cofounder / coworker / investor / author / interviewer / school peer / board / collaborator",
      "organization": "Company, school, publication, fund, or event",
      "why_close": "short source-backed reason this person may be closer to the target",
      "source": "URL"
    }}
  ],
  "organizations": [
    {{
      "name": "Organization",
      "relationship_to_target": "company / school / employer / investor / event / publication",
      "source": "URL"
    }}
  ],
  "followup_queries": [
    "\\"Close Person Name\\" \\"Target or Organization\\" collaborator",
    "\\"Close Person Name\\" board advisor cofounder",
    "\\"Organization\\" leadership team founders board"
  ]
}}

Rules:
- Prefer less-famous, operationally close people over celebrities or famous investors.
- Include cofounders, early employees, coworkers, investors, board members, authors, podcast hosts, interviewers, event organizers, professors, school/community peers, and named collaborators.
- Use only source-backed items.
- If a source is likely about a different person with the same name, exclude those people/orgs unless clearly labeled for that different identity.
- Add followup_queries that are likely to reveal the closest professional connections of those adjacent people, not just broad pages about the target.

Evidence:
{evidence_block(evidence)}
""".strip()


def parse_adjacent_json(text):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {"people": [], "organizations": []}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"people": [], "organizations": []}
    people = data.get("people", [])
    organizations = data.get("organizations", [])
    followup_queries = data.get("followup_queries", [])
    return {
        "people": people if isinstance(people, list) else [],
        "organizations": organizations if isinstance(organizations, list) else [],
        "followup_queries": followup_queries if isinstance(followup_queries, list) else [],
    }


def adjacent_queries(adjacent, limit=20):
    queries = []
    for query in adjacent.get("followup_queries", [])[:limit]:
        if query:
            queries.append(str(query))
    for person in adjacent.get("people", [])[:limit]:
        name = person.get("name", "")
        org = person.get("organization", "")
        if name and org:
            queries.append(f'"{name}" "{org}"')
            queries.append(f'"{name}" "{org}" collaborator')
            queries.append(f'"{name}" "{org}" board')
        elif name:
            queries.append(f'"{name}"')
            queries.append(f'"{name}" collaborator board coauthor')
    for org in adjacent.get("organizations", [])[:limit]:
        name = org.get("name", "")
        if name:
            queries.append(f'"{name}" founders team')
            queries.append(f'"{name}" leadership board')
    return unique(queries)[:limit]


def research_person(args):
    load_dotenv(args.env)
    args.provider = args.provider or os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER)
    if not args.model:
        if args.provider == "gemini":
            args.model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        else:
            args.model = os.environ.get("OLLAMA_MODEL", "llama3.1")
    local_matches = []
    if args.include_local_profiles:
        local_matches = load_local_profile_matches(args.profiles_csv, args.person, args.local_matches)
    evidence = build_evidence(
        person_queries(args.person, args.context),
        args.max_results,
        args.max_pages,
        args.allow_insecure_ssl,
        args.search_provider,
        args.person,
        args.use_apify_instagram,
    )
    if not usable_evidence(evidence):
        raise SystemExit(
            "No usable web evidence was read. The search provider returned no parsable results or every page failed. "
            "Try again, reduce --max-results, or check your network/search access."
        )
    if args.adjacent_pass:
        adjacent_text = generate_text(
            args.provider,
            args.model,
            adjacent_people_prompt(args.person, args.context, evidence),
            allow_insecure_ssl=args.allow_insecure_ssl,
        )
        adjacent = parse_adjacent_json(adjacent_text)
        followup_queries = adjacent_queries(adjacent, args.max_adjacent_queries)
        if followup_queries:
            evidence.extend(
                build_evidence(
                    followup_queries,
                    max(2, min(args.max_results, 4)),
                    args.max_pages + args.max_adjacent_queries,
                    args.allow_insecure_ssl,
                    args.search_provider,
                    args.person,
                    args.use_apify_instagram,
                )
            )
            if not usable_evidence(evidence):
                raise SystemExit("No usable web evidence remained after adjacent searches.")
    if args.institution_pass:
        institution_evidence = build_evidence(
            created_institution_queries(args.person, args.context, args.max_institution_queries),
            max(2, min(args.max_results, 4)),
            args.max_pages + args.max_adjacent_queries + args.max_institution_pages,
            args.allow_insecure_ssl,
            args.search_provider,
            args.person,
            args.use_apify_instagram,
        )
        evidence.extend(institution_evidence)
        followup_queries = institution_followup_queries(institution_evidence, args.person, args.max_institution_followups)
        if followup_queries:
            evidence.extend(
                build_evidence(
                    followup_queries,
                    max(2, min(args.max_results, 4)),
                    args.max_pages + args.max_adjacent_queries + args.max_institution_pages,
                    args.allow_insecure_ssl,
                    args.search_provider,
                    args.person,
                    args.use_apify_instagram,
                )
            )
    return generate_text(
        args.provider,
        args.model,
        dossier_prompt(args.person, args.context, evidence, local_matches, args.include_local_profiles),
        allow_insecure_ssl=args.allow_insecure_ssl,
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Build a public/professional dossier for a person by name.")
    parser.add_argument("person", help="Person name to research.")
    parser.add_argument("--context", default="", help="Optional disambiguator: company, school, city, firm, etc.")
    parser.add_argument("--provider", choices=["gemini", "ollama"], default=None)
    parser.add_argument("--model", default=None, help="Model name for the selected provider.")
    parser.add_argument("--env", default=".env", help="Path to .env file containing GEMINI_API_KEY.")
    parser.add_argument("--profiles-csv", default=DEFAULT_PROFILES_CSV, help="Local LinkedIn profile CSV to search.")
    parser.add_argument(
        "--include-local-profiles",
        action="store_true",
        help="Include local linkedin_network_profiles.csv evidence. Off by default for clean web-only dossiers.",
    )
    parser.add_argument("--local-matches", type=int, default=8, help="Max local profile CSV matches to include.")
    parser.add_argument("--max-results", type=int, default=8, help="Search results to inspect from the exact-name query.")
    parser.add_argument("--max-pages", type=int, default=8, help="Total pages to fetch and read.")
    parser.add_argument(
        "--search-provider",
        choices=["all", "brave", "apify", "google", "duckduckgo", "bing"],
        default="brave",
        help="Search provider to use. 'apify' uses the configured Apify Google SERP actor.",
    )
    parser.add_argument(
        "--use-apify-instagram",
        action="store_true",
        help="When search results include Instagram profile URLs, add public Instagram evidence via Apify actors.",
    )
    parser.add_argument(
        "--no-adjacent-pass",
        dest="adjacent_pass",
        action="store_false",
        help="Skip the second pass that searches adjacent people/orgs.",
    )
    parser.set_defaults(adjacent_pass=True)
    parser.add_argument("--max-adjacent-queries", type=int, default=20, help="Max adjacent people/org follow-up searches.")
    parser.add_argument(
        "--no-institution-pass",
        dest="institution_pass",
        action="store_false",
        help="Skip target-created institution/community searches.",
    )
    parser.set_defaults(institution_pass=True)
    parser.add_argument("--max-institution-queries", type=int, default=10, help="Max searches for schools/foundations/programs created or backed by target.")
    parser.add_argument("--max-institution-followups", type=int, default=8, help="Max follow-up searches for found institutions and alumni/staff.")
    parser.add_argument("--max-institution-pages", type=int, default=6, help="Extra page-read budget for institution searches.")
    parser.add_argument("--output", default="", help="Write Markdown dossier to this file.")
    parser.add_argument(
        "--allow-insecure-ssl",
        action="store_true",
        help="Disable HTTPS certificate verification for networks with self-signed proxy certificates.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    report = research_person(args)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as output_file:
            output_file.write(report)
        print(f"\nSaved report to {args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
