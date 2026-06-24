from __future__ import annotations

from collections import deque

from .graph_store import RelationshipGraph


def bfs_paths(
    graph: RelationshipGraph,
    start_id: str,
    target_ids: set[str],
    max_depth: int = 3,
    max_paths: int = 25,
) -> list[tuple[list[str], list]]:
    queue = deque([(start_id, [start_id], [])])
    paths = []
    while queue and len(paths) < max_paths:
        node_id, node_path, edge_path = queue.popleft()
        depth = len(edge_path)
        if depth > max_depth:
            continue
        if node_id in target_ids and node_id != start_id:
            paths.append((node_path, edge_path))
            continue
        if depth == max_depth:
            continue
        for next_id, edge in graph.neighbors(node_id):
            if next_id in node_path:
                continue
            queue.append((next_id, node_path + [next_id], edge_path + [edge]))
    return paths
