from __future__ import annotations

import json
import re
from pathlib import Path

from .guardrails import passes_guardrails
from .models import Evidence, PersonNode, RelationshipEdge


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-") or "unknown"


class RelationshipGraph:
    def __init__(self):
        self.nodes: dict[str, PersonNode] = {}
        self.edges: list[RelationshipEdge] = []
        self.edge_keys: set[tuple[str, str, str, str]] = set()

    def add_node(self, node: PersonNode) -> PersonNode:
        existing = self.nodes.get(node.id)
        if existing:
            if node.is_user_connection:
                existing.is_user_connection = True
            return existing
        self.nodes[node.id] = node
        return node

    def get_or_create_person(self, name: str, *, source: str = "public_web") -> PersonNode:
        person_id = f"person:{slug(name)}"
        node = self.nodes.get(person_id)
        if node:
            return node
        node = PersonNode(id=person_id, name=name, source=source)
        self.add_node(node)
        return node

    def add_edge(self, edge: RelationshipEdge, min_confidence: float = 0.45) -> bool:
        if not passes_guardrails(edge, min_confidence):
            return False
        source = self.get_or_create_person(edge.source_name)
        target = self.get_or_create_person(edge.target_name)
        edge.source_id = source.id
        edge.target_id = target.id
        key = edge.key()
        if key in self.edge_keys:
            return False
        self.edge_keys.add(key)
        self.edges.append(edge)
        return True

    def neighbors(self, node_id: str) -> list[tuple[str, RelationshipEdge]]:
        output = []
        for edge in self.edges:
            if edge.source_id == node_id:
                output.append((edge.target_id, edge))
            elif edge.target_id == node_id:
                output.append((edge.source_id, edge))
        return output

    def degree(self, node_id: str) -> int:
        return len(self.neighbors(node_id))

    def to_dict(self) -> dict:
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str):
        graph = cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in data.get("nodes", []):
            graph.add_node(PersonNode(**item))
        for item in data.get("edges", []):
            item["evidence"] = [Evidence(**evidence) for evidence in item.get("evidence", [])]
            edge = RelationshipEdge(**item)
            graph.edges.append(edge)
            graph.edge_keys.add(edge.key())
        return graph
