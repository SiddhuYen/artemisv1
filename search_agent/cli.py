from __future__ import annotations

import argparse
from pathlib import Path

from .agent import RelationshipPathAgent
from .config import AgentConfig


def format_path(path) -> str:
    names = " -> ".join(node.name for node in path.nodes)
    lines = [f"## {names}", f"Score: {path.score:.3f}", "", path.explanation, ""]
    for edge in path.edges:
        evidence = edge.evidence[0]
        lines.extend(
            [
                f"- {edge.source_name} -> {edge.target_name}",
                f"  - Type: {edge.edge_type}",
                f"  - Confidence: {edge.confidence:.2f}",
                f"  - Why it may work: {edge.explanation}",
                f"  - Citation: {evidence.url}",
            ]
        )
    return "\n".join(lines)


def write_markdown(paths, output_path: str, target: str) -> None:
    lines = [
        f"# Possible Public Connection Paths To {target}",
        "",
        "These are possible paths built only from cited public-source edges. They are not confirmed relationships unless the cited source directly proves the edge.",
        "",
    ]
    if not paths:
        lines.append("No cited path to a LinkedIn connection was found within the configured depth.")
    for path in paths:
        lines.append(format_path(path))
        lines.append("")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Public-source relationship path-finding agent")
    parser.add_argument("--target", help="Target person name")
    parser.add_argument("--connections", default="Connections.csv", help="LinkedIn connections CSV")
    parser.add_argument("--seed", action="append", default=[], help="Optional seed person or organization; repeatable")
    parser.add_argument("--graph-output", default="search_agent_graph.json", help="Graph JSON output path")
    parser.add_argument("--paths-output", default="search_agent_paths.md", help="Ranked path report output path")
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--max-search-results", type=int, default=5)
    parser.add_argument("--max-pages-per-query", type=int, default=3)
    parser.add_argument("--max-queries-per-node", type=int, default=6)
    parser.add_argument("--min-confidence", type=float, default=0.45)
    parser.add_argument("--allow-insecure-ssl", action="store_true")
    parser.add_argument("--use-apify-serp", action="store_true", help="Use Apify Google SERP actor instead of Brave.")
    parser.add_argument("--use-apify-instagram", action="store_true", help="Add public Instagram evidence through Apify actors.")
    args = parser.parse_args()

    target = args.target or input("Target person: ").strip()
    if not target:
        raise SystemExit("Target person is required.")
    if not args.seed:
        seed_text = input("Optional seeds, comma-separated: ").strip()
        args.seed = [item.strip() for item in seed_text.split(",") if item.strip()]

    config = AgentConfig.from_env(
        max_depth=args.max_depth,
        max_search_results=args.max_search_results,
        max_pages_per_query=args.max_pages_per_query,
        max_queries_per_node=args.max_queries_per_node,
        min_confidence=args.min_confidence,
        graph_path=args.graph_output,
        allow_insecure_ssl=args.allow_insecure_ssl,
        use_apify_serp=args.use_apify_serp,
        use_apify_instagram=args.use_apify_instagram,
    )
    agent = RelationshipPathAgent(config)
    _, paths = agent.run(target, args.connections, args.seed, args.graph_output)
    write_markdown(paths, args.paths_output, target)
    print(f"Saved graph: {args.graph_output}")
    print(f"Saved paths: {args.paths_output}")
    print(f"Found {len(paths)} possible path(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
