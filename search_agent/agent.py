from __future__ import annotations

from .apify_client import ApifyClient
from .config import AgentConfig
from .graph_store import RelationshipGraph
from .linkedin_csv import load_linkedin_connections
from .llm import GeminiExtractor
from .models import PersonNode
from .pathfinder import bfs_paths
from .ranking import rank_paths
from .search import BraveSearchClient, fetch_page_text


def target_node_id(target_name: str) -> str:
    from .graph_store import slug

    return f"person:{slug(target_name)}"


def search_queries(name: str, seeds: list[str], limit: int) -> list[str]:
    base = [
        f'"{name}"',
        f'"{name}" founder cofounder',
        f'"{name}" investor advisor board',
        f'"{name}" interview podcast',
        f'"{name}" event speaker',
        f'"{name}" university school',
        f'"{name}" GitHub',
    ]
    for seed in seeds:
        base.append(f'"{name}" "{seed}"')
    seen = []
    for query in base:
        if query not in seen:
            seen.append(query)
    return seen[:limit]


class RelationshipPathAgent:
    def __init__(self, config: AgentConfig):
        config.validate()
        self.config = config
        self.search_client = BraveSearchClient(config.brave_api_key, config.allow_insecure_ssl)
        self.extractor = GeminiExtractor(config.gemini_api_key, config.gemini_model)
        self.apify = ApifyClient(
            config.apify_api_token,
            config.apify_google_serp_actor,
            config.apify_instagram_scraper_actor,
            config.apify_instagram_profile_actor,
        )

    def run(
        self,
        target_name: str,
        connections_csv: str,
        seeds: list[str] | None = None,
        graph_path: str | None = None,
    ):
        seeds = seeds or []
        graph = RelationshipGraph()
        for connection in load_linkedin_connections(connections_csv):
            graph.add_node(connection)

        target = graph.add_node(PersonNode(id=target_node_id(target_name), name=target_name, source="user_target"))
        connection_ids = {node.id for node in graph.nodes.values() if node.is_user_connection}
        frontier = [target.name] + seeds
        searched = set()

        for depth in range(self.config.max_depth):
            print(f"Expansion depth {depth + 1}/{self.config.max_depth}")
            next_frontier = []
            for subject in frontier:
                if subject.lower() in searched:
                    continue
                searched.add(subject.lower())
                for query in search_queries(subject, seeds, self.config.max_queries_per_node):
                    print(f"Searching: {query}")
                    try:
                        if self.config.use_apify_serp:
                            results = self.apify.google_search(query, self.config.max_search_results)
                        else:
                            results = self.search_client.search(query, self.config.max_search_results)
                    except Exception as error:
                        print(f"Search failed for {query}: {error}")
                        continue
                    if self.config.use_apify_instagram:
                        instagram_urls = [result.url for result in results if "instagram.com" in result.url]
                        if instagram_urls:
                            try:
                                results.extend(
                                    self.apify.instagram_public_evidence(
                                        subject,
                                        instagram_urls,
                                        self.config.max_pages_per_query,
                                    )
                                )
                            except Exception as error:
                                print(f"Apify Instagram scrape failed for {subject}: {error}")
                    enriched = []
                    for result in results[: self.config.max_pages_per_query]:
                        try:
                            result.page_text = fetch_page_text(result.url, self.config.allow_insecure_ssl)
                        except Exception:
                            result.page_text = ""
                        enriched.append(result)
                    if not enriched:
                        continue
                    try:
                        edges = self.extractor.extract_edges(subject, enriched)
                    except Exception as error:
                        print(f"LLM extraction failed for {query}: {error}")
                        continue
                    for edge in edges:
                        if graph.add_edge(edge, self.config.min_confidence):
                            if edge.source_name.lower() != subject.lower():
                                next_frontier.append(edge.source_name)
                            if edge.target_name.lower() != subject.lower():
                                next_frontier.append(edge.target_name)

            raw_paths = bfs_paths(graph, target.id, connection_ids, self.config.max_depth)
            if raw_paths:
                break
            frontier = list(dict.fromkeys(next_frontier))[:30]
            if not frontier:
                break

        output_graph = graph_path or self.config.graph_path
        graph.save(output_graph)
        raw_paths = bfs_paths(graph, target.id, connection_ids, self.config.max_depth)
        return graph, rank_paths(graph, raw_paths)
