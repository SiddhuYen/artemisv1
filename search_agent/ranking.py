from __future__ import annotations

from .models import PathResult, RelationshipEdge


EDGE_RELEVANCE = {
    "cofounder": 1.0,
    "advisor_or_board": 0.95,
    "investor": 0.9,
    "same_company": 0.85,
    "employee_or_ex_employee": 0.8,
    "coauthor": 0.78,
    "podcast_or_interview": 0.72,
    "same_event": 0.65,
    "github_collaboration": 0.75,
    "same_school": 0.55,
    "public_social_connection": 0.45,
}


def edge_strength(edge: RelationshipEdge) -> float:
    citation_bonus = min(len(edge.evidence), 3) * 0.05
    confirmed_bonus = 0.08 if edge.confirmed else 0
    relevance = EDGE_RELEVANCE.get(edge.edge_type, 0.5)
    return min(1.0, edge.confidence * 0.55 + relevance * 0.25 + edge.public_professional_relevance * 0.12 + citation_bonus + confirmed_bonus)


def rank_paths(graph, raw_paths) -> list[PathResult]:
    ranked: list[PathResult] = []
    for node_ids, edges in raw_paths:
        nodes = [graph.nodes[node_id] for node_id in node_ids]
        if not edges:
            continue
        avg_strength = sum(edge_strength(edge) for edge in edges) / len(edges)
        path_len_score = 1 / len(edges)
        intermediate_ids = node_ids[1:-1]
        low_fame_score = 0.5
        if intermediate_ids:
            degrees = [graph.degree(node_id) for node_id in intermediate_ids]
            low_fame_score = sum(1 / (1 + degree) for degree in degrees) / len(degrees)
        strong_citations = sum(1 for edge in edges if edge.confidence >= 0.75 and edge.evidence)
        score = path_len_score * 0.42 + avg_strength * 0.36 + low_fame_score * 0.12 + min(strong_citations, 3) * 0.03
        explanation = "; ".join(
            f"{edge.source_name} -> {edge.target_name} via {edge.edge_type} ({edge.confidence:.2f})"
            for edge in edges
        )
        ranked.append(PathResult(nodes=nodes, edges=edges, score=round(score, 4), explanation=explanation))
    return sorted(ranked, key=lambda path: path.score, reverse=True)
