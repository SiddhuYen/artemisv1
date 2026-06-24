from __future__ import annotations

from .models import ALLOWED_EDGE_TYPES, SearchResult


EXTRACTION_SYSTEM_PROMPT = f"""
You extract public relationship graph edges from web search evidence.

Return only JSON. No markdown.

Allowed edge_type values:
{", ".join(sorted(ALLOWED_EDGE_TYPES))}

Rules:
- Extract only edges supported by the provided search result snippet or page text.
- Every edge must include at least one citation_url from the evidence.
- Prefer professional, academic, creative, investor, event, podcast, GitHub, or public social links.
- Do not infer family relationships.
- Do not use private addresses, location tracking, leaked data, minors, hidden accounts, or sensitive personal data.
- If a relationship is weak or speculative, either lower confidence or reject it.
- Treat edges as possible connections unless the source directly proves the relationship.
- Lower-fame intermediaries are useful if they are publicly connected to the subject.

Schema:
{{
  "edges": [
    {{
      "source_name": "Person or organization A",
      "target_name": "Person or organization B",
      "edge_type": "same_company",
      "confidence": 0.0,
      "confirmed": false,
      "public_professional_relevance": 0.0,
      "citation_url": "https://...",
      "evidence_quote": "short quote or snippet",
      "explanation": "why the source supports this edge"
    }}
  ],
  "rejections": [
    {{
      "reason": "why evidence was rejected"
    }}
  ]
}}
""".strip()


def extraction_prompt(subject: str, results: list[SearchResult]) -> str:
    blocks = []
    for index, result in enumerate(results, 1):
        page_text = result.page_text[:5000] if result.page_text else ""
        blocks.append(
            "\n".join(
                [
                    f"Evidence {index}",
                    f"Title: {result.title}",
                    f"URL: {result.url}",
                    f"Snippet: {result.snippet}",
                    f"Page text: {page_text}",
                ]
            )
        )
    return (
        f"Subject to expand: {subject}\n\n"
        "Extract candidate relationship edges involving the subject or public people/organizations "
        "that could create a relationship path toward a LinkedIn connection.\n\n"
        + "\n\n".join(blocks)
    )
