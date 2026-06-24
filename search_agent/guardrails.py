from __future__ import annotations

from urllib.parse import urlparse

from .models import ALLOWED_EDGE_TYPES, RelationshipEdge


SENSITIVE_TERMS = {
    "address",
    "home address",
    "private address",
    "phone number",
    "ssn",
    "social security",
    "leaked",
    "dox",
    "doxxed",
    "minor",
    "child",
    "children",
    "parent",
    "parents",
    "sibling",
    "spouse",
    "wife",
    "husband",
    "family",
    "hidden account",
    "private account",
}


def reject_reason(edge: RelationshipEdge, min_confidence: float = 0.45) -> str:
    if edge.edge_type not in ALLOWED_EDGE_TYPES:
        return f"unsupported edge type: {edge.edge_type}"
    if not edge.source_name or not edge.target_name:
        return "missing endpoint name"
    if edge.source_name.lower() == edge.target_name.lower():
        return "self-edge"
    if edge.confidence < min_confidence:
        return "confidence below threshold"
    if not edge.evidence or not edge.evidence[0].url:
        return "missing citation URL"
    parsed = urlparse(edge.evidence[0].url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "citation URL is not public HTTP(S)"
    combined = " ".join(
        [
            edge.source_name,
            edge.target_name,
            edge.edge_type,
            edge.explanation,
            edge.evidence[0].quote,
            edge.evidence[0].snippet,
        ]
    ).lower()
    if any(term in combined for term in SENSITIVE_TERMS):
        return "sensitive or private-data relationship"
    if edge.edge_type == "public_social_connection" and edge.confidence < 0.7:
        return "public social edge needs high confidence"
    return ""


def passes_guardrails(edge: RelationshipEdge, min_confidence: float = 0.45) -> bool:
    return reject_reason(edge, min_confidence) == ""
