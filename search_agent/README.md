# Search Agent

Public-source relationship path-finding agent.

Given a target person, a LinkedIn connections CSV, and optional seed people or organizations, this builds a local JSON graph of cited public relationship edges and ranks possible paths from the target to one of your LinkedIn connections.

## Safety Rules

- Uses public web search and public pages only.
- Rejects edges without citation URLs.
- Rejects private addresses, leaked info, minors, family inference, hidden accounts, and weak unsupported speculation.
- Treats output as possible connection paths unless the cited source directly confirms an edge.
- Stores graph data locally as JSON.

## Setup

Put your keys in the repo `.env` file:

```bash
BRAVE_SEARCH_API_KEY=...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
APIFY_API_TOKEN=...
```

Optional Apify actor overrides:

```bash
APIFY_GOOGLE_SERP_ACTOR=apify/google-search-scraper
APIFY_INSTAGRAM_SCRAPER_ACTOR=apify/instagram-scraper
APIFY_INSTAGRAM_PROFILE_ACTOR=apify/instagram-profile-scraper
```

## Run

From the repo root:

```bash
python3 -m search_agent.cli \
  --target "Enrique Linares" \
  --connections Connections.csv \
  --seed "Plus Partners" \
  --graph-output search_agent_graph.json \
  --paths-output search_agent_paths.md
```

Use Apify Google SERP instead of Brave:

```bash
python3 -m search_agent.cli \
  --target "Enrique Linares" \
  --connections Connections.csv \
  --use-apify-serp
```

Add public Instagram evidence when Instagram profile URLs appear in search results:

```bash
python3 -m search_agent.cli \
  --target "Enrique Linares" \
  --connections Connections.csv \
  --use-apify-instagram
```

Or run interactively:

```bash
python3 -m search_agent.cli
```

## Output

- `search_agent_graph.json`: local graph with nodes and cited edges.
- `search_agent_paths.md`: ranked possible paths with citations and explanations.

Supported edge types:

- `same_company`
- `cofounder`
- `employee_or_ex_employee`
- `advisor_or_board`
- `investor`
- `coauthor`
- `same_school`
- `same_event`
- `podcast_or_interview`
- `github_collaboration`
- `public_social_connection`
