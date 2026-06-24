from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ALLOWED_EDGE_TYPES = {
    "same_company",
    "cofounder",
    "employee_or_ex_employee",
    "advisor_or_board",
    "investor",
    "coauthor",
    "same_school",
    "same_event",
    "podcast_or_interview",
    "github_collaboration",
    "public_social_connection",
}


@dataclass
class PersonNode:
    id: str
    name: str
    kind: str = "person"
    profile_url: str = ""
    company: str = ""
    title: str = ""
    source: str = ""
    fame_score: float = 0.5
    is_user_connection: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Evidence:
    url: str
    title: str = ""
    snippet: str = ""
    quote: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RelationshipEdge:
    source_id: str
    target_id: str
    source_name: str
    target_name: str
    edge_type: str
    confidence: float
    evidence: list[Evidence] = field(default_factory=list)
    explanation: str = ""
    confirmed: bool = False
    public_professional_relevance: float = 0.5

    def key(self) -> tuple[str, str, str, str]:
        ordered = tuple(sorted([self.source_id, self.target_id]))
        return (ordered[0], ordered[1], self.edge_type, self.primary_url())

    def primary_url(self) -> str:
        return self.evidence[0].url if self.evidence else ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [item.to_dict() for item in self.evidence]
        return data


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    page_text: str = ""


@dataclass
class PathResult:
    nodes: list[PersonNode]
    edges: list[RelationshipEdge]
    score: float
    explanation: str
