# LinkedIn Network Research

Tools for public-source person research and LinkedIn-network overlap matching.

## Main Workflow

Run the prompt-driven flow:

```bash
python3 research_person_and_network.py
```

Or pass a person directly:

```bash
python3 research_person_and_network.py "Enrique Linares" --context "Plus Partners"
```

This does two things:

1. Builds a public web dossier with `person_deep_research.py`.
2. Scans your local LinkedIn profile CSV with `dossier_network_matcher.py`.

Outputs are written to `research_outputs/`.

## Web UI

```bash
python3 research_web_app.py
```

Then open:

```text
http://127.0.0.1:8765
```

If that port is busy, choose another:

```bash
python3 research_web_app.py --port 8766
```

The web UI has three pages:

1. **Path Search**: enter a target name. Artemis builds a public dossier, creates an endpoint-side map, then searches your uploaded network graph for bridge candidates.
2. **Build Network**: upload your LinkedIn CSV as your first-degree graph. Click any connection, then manually add their connections or upload their CSV to extend the graph.
3. **Graph**: browse the full local graph of your known connections.

The graph is stored locally in:

```text
user_network_graph.json
user_network_profiles.csv
```

`user_network_profiles.csv` is the flattened graph file used by the backend path search. If it does not exist, the app falls back to `linkedin_network_profiles.csv`.

For deployment, set `ARTEMIS_DATA_DIR` to control where writable data goes:

```bash
ARTEMIS_DATA_DIR=/var/data python3 research_web_app.py --host 0.0.0.0 --port "$PORT"
```

When `ARTEMIS_DATA_DIR` is set, the app writes these files inside that directory:

```text
user_network_graph.json
user_network_profiles.csv
research_outputs/
```

On Render, point `ARTEMIS_DATA_DIR` at your persistent disk mount path. If you do not attach a disk, files written there can disappear on redeploy.

The website is split into backend and frontend files:

```text
research_web_app.py       # compatibility launcher
backend/server.py         # local backend/API server
frontend/index.html       # page markup
frontend/styles.css       # visual design
frontend/app.js           # browser behavior and graph rendering
```

For a step-by-step walkthrough, see [docs/WEBSITE_TUTORIAL.md](docs/WEBSITE_TUTORIAL.md).

## Verification Loop

Artemis now separates overlap from verified relationship paths.

The run order is:

```text
target research
-> Artemis Target Map
-> endpoint-side second layer
-> local graph bridge candidates
-> seed-side relationship map
-> exact-name bridging search
-> verified paths / rejected candidates / research clues
```

A candidate bridge only becomes a verified path if the verifier finds a cited, named professional relationship between the local-network person and the endpoint-side node. Shared employer, shared broad field, same large school, or generic title overlap is rejected.

Useful switches:

```bash
--no-verify-hops       # cheaper/faster, but returns candidates instead of verified paths
--verify-limit 8       # max bridge candidates to verify
--no-seed-map          # skip seed-side relationship-map synthesis
```

## Required Environment

Create a `.env` file:

```text
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE
GEMINI_MODEL=gemini-2.5-flash
LLM_PROVIDER=gemini
BRAVE_SEARCH_API_KEY=YOUR_BRAVE_SEARCH_API_KEY_HERE
```

Optional Apify settings:

```text
APIFY_API_TOKEN=YOUR_APIFY_API_TOKEN_HERE
APIFY_GOOGLE_SERP_ACTOR=apify/google-search-scraper
APIFY_INSTAGRAM_SCRAPER_ACTOR=apify/instagram-scraper
APIFY_INSTAGRAM_PROFILE_ACTOR=apify/instagram-profile-scraper
```

Use Apify search from the main workflow:

```bash
python3 research_person_and_network.py "John Giannandrea" \
  --context "Apple" \
  --search-provider apify
```

Add public Instagram evidence when Instagram URLs appear in search results:

```bash
python3 research_person_and_network.py "Some Person" \
  --use-apify-instagram
```

## Separate Path-Finding Agent

The `search_agent/` package is a separate graph-based agent. It builds cited public relationship edges, stores them as JSON, and ranks possible paths to your LinkedIn connections.

```bash
python3 -m search_agent.cli \
  --target "Enrique Linares" \
  --connections Connections.csv \
  --seed "Plus Partners"
```

See `search_agent/README.md` for details.

## Important Safety Constraints

- Uses public web sources only.
- Rejects uncited relationship edges.
- Does not use private addresses, leaked data, minors, family inference, or hidden accounts.
- Treats matches as possible public overlaps, not confirmed relationships.
