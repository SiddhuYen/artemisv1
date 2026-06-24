# ARTEMIS Website Tutorial

This guide is for the local website served by `research_web_app.py`.

The code is split like this:

```text
research_web_app.py       # launcher so the old command still works
backend/server.py         # API server and research job runner
frontend/index.html       # page layout
frontend/styles.css       # visual design
frontend/app.js           # browser interactions and graph rendering
```

## 1. Start The Website

From the project folder:

```bash
python3 research_web_app.py
```

Open:

```text
http://127.0.0.1:8765
```

If the port is busy:

```bash
python3 research_web_app.py --port 8899
```

For deployment or persistent storage, set `ARTEMIS_DATA_DIR`:

```bash
ARTEMIS_DATA_DIR=/var/data python3 research_web_app.py --host 0.0.0.0 --port "$PORT"
```

The app will store `user_network_graph.json`, `user_network_profiles.csv`, and `research_outputs/` under that directory.

## 2. Add Your Network

Go to `Build Network`.

Use `Replace with my LinkedIn CSV` when uploading your own first-degree LinkedIn export. This creates:

```text
user_network_graph.json
user_network_profiles.csv
```

`user_network_graph.json` is the visual graph. `user_network_profiles.csv` is the flattened file the backend searches.

To add second-degree connections:

1. Click one of your connections in the list.
2. Change upload mode to `Add CSV under selected connection`.
3. Upload that person's `Connections.csv`.

Those people become second-degree nodes, and future target searches can route through them.

To add one person manually:

1. Click the parent person.
2. Fill in name, company, position, and optional LinkedIn URL.
3. Click `Add Connection`.

## 3. Search For A Target

Go to `Path Search`.

Fill in:

- `Target person`: the person you want to reach.
- `Context`: company, role, school, city, or why they matter.

Good examples:

```text
Gwynne Shotwell
SpaceX president COO
```

```text
Dr. Katie Jenner
Indiana Secretary of Education
```

Then click `Run Path Search`.

## 4. Recommended Settings

For normal runs:

```text
Results: 5
Pages: 5
Adjacent: 8
Matches: 50
Search provider: Brave API
```

Keep these checked:

```text
Allow local self-signed SSL chain
Verify hops with exact bridging search
Build seed-side relationship maps
```

Usually leave these unchecked:

```text
Skip adjacent-person expansion
Skip target-created institution search
Add public Instagram evidence through Apify
```

Use Apify Instagram only when public Instagram evidence is actually useful. It can slow down a run.

## 5. Read The Results Graph

After the run finishes, the `Routes` tab shows a graph.

Node colors:

- White/cyan: you or normal path nodes.
- Pink/red: the target.
- Amber: near-miss path nodes.

Line styles:

- Solid cyan/green: candidate or verified-style route.
- Dashed amber: near-miss route with at least one iffy hop.

Click any node or route line. The right panel will show:

- The full pathway.
- The confidence label.
- The score.
- The iffy hop, if there is one.
- The reasoning for why the path might work.
- Evidence snippets from your network row.

## 6. What The Tabs Mean

`Routes`: graph-first view of possible paths and near-misses.

`Dossier`: everything the target research step learned from public sources.

`Report`: the full network-matching report, including verified paths, rejected candidates, and clues.

`Log`: step-by-step run progress and errors.

## 7. What Counts As A Real Path

A path is strongest when every hop has a cited public relationship.

Examples of stronger links:

- Worked together at the same small company or team.
- Cofounded something.
- Board/advisor relationship.
- Investor/founder relationship.
- Coauthored work.
- Public event or interview relationship.

Examples of weak links:

- Same broad title, like engineer or student.
- Same large school with no overlap.
- Same huge company with no team evidence.
- Generic topic overlap.

When ARTEMIS cannot find a good verified path, it shows `Best Near-Miss Paths`. Use those as research leads, not as intro paths.

## 8. Output Files

Every run writes markdown files to:

```text
research_outputs/
```

Each run usually creates:

```text
TARGET_dossier.md
TARGET_network_matches.md
```

The website also gives download links after a run.

## 9. Fast Vs Thorough Runs

Fast and cheaper:

- Lower `Results`, `Pages`, and `Adjacent`.
- Uncheck `Verify hops with exact bridging search`.
- Uncheck `Build seed-side relationship maps`.

More thorough:

- Keep verification on.
- Keep adjacent and institution search on.
- Increase `Adjacent` to 15-20.
- Increase `Pages` to 8-10.

For famous targets, start small. Famous people create a lot of noisy public evidence.

## 10. Troubleshooting

If the website does not open, the port may be busy. Start it on another port:

```bash
python3 research_web_app.py --port 8899
```

If search returns nothing, check `.env` for:

```text
GEMINI_API_KEY
BRAVE_SEARCH_API_KEY
```

If a run is slow, reduce:

```text
Results
Pages
Adjacent
Matches
```

If the graph looks weak, open the `Report` tab and inspect whether the paths are verified, rejected, or near-miss clues.
