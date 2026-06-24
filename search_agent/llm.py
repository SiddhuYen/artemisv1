from __future__ import annotations

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import Evidence, RelationshipEdge
from .prompts import EXTRACTION_SYSTEM_PROMPT, extraction_prompt


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


class GeminiExtractor:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        self.model = model

    def extract_edges(self, subject: str, results) -> list[RelationshipEdge]:
        prompt = extraction_prompt(subject, results)
        response = self._generate(prompt)
        data = parse_json_object(response)
        edges: list[RelationshipEdge] = []
        for item in data.get("edges", []):
            citation_url = str(item.get("citation_url", "")).strip()
            evidence = [
                Evidence(
                    url=citation_url,
                    title="",
                    snippet=str(item.get("evidence_quote", "")).strip(),
                    quote=str(item.get("evidence_quote", "")).strip(),
                )
            ]
            edge = RelationshipEdge(
                source_id="",
                target_id="",
                source_name=str(item.get("source_name", "")).strip(),
                target_name=str(item.get("target_name", "")).strip(),
                edge_type=str(item.get("edge_type", "")).strip(),
                confidence=float(item.get("confidence", 0) or 0),
                confirmed=bool(item.get("confirmed", False)),
                public_professional_relevance=float(item.get("public_professional_relevance", 0.5) or 0.5),
                evidence=evidence,
                explanation=str(item.get("explanation", "")).strip(),
            )
            edges.append(edge)
        return edges

    def _generate(self, prompt: str, retries: int = 3) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(self.model)}:generateContent?key={quote(self.api_key)}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": EXTRACTION_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }
        body = json.dumps(payload).encode("utf-8")
        last_error = None
        for attempt in range(retries):
            request = Request(url, data=body, headers={"Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=60) as response:
                    data = json.loads(response.read().decode("utf-8"))
                parts = data["candidates"][0]["content"].get("parts", [])
                return "\n".join(part.get("text", "") for part in parts).strip()
            except (HTTPError, URLError, TimeoutError, KeyError, json.JSONDecodeError) as error:
                last_error = error
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Gemini extraction failed: {last_error}")
