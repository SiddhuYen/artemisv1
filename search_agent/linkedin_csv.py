from __future__ import annotations

import csv
import re

from .models import PersonNode


def node_id(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return cleaned or "unknown"


def first_value(row: dict[str, str], names: list[str]) -> str:
    normalized = {key.lower().strip(): value for key, value in row.items()}
    for name in names:
        value = normalized.get(name.lower())
        if value:
            return " ".join(value.split())
    return ""


def load_linkedin_connections(csv_path: str) -> list[PersonNode]:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as file:
        sample = file.read(4096)
        file.seek(0)
        start_offset = 0
        for line_number, line in enumerate(sample.splitlines()):
            lowered = line.lower()
            if "first name" in lowered or "name" in lowered:
                start_offset = line_number
                break
        for _ in range(start_offset):
            next(file, None)
        rows = list(csv.DictReader(file))

    nodes: list[PersonNode] = []
    seen = set()
    for row in rows:
        name = first_value(row, ["name", "full name"])
        if not name:
            first = first_value(row, ["first name"])
            last = first_value(row, ["last name"])
            name = " ".join(part for part in [first, last] if part).strip()
        if not name:
            continue
        person_id = f"connection:{node_id(name)}"
        if person_id in seen:
            continue
        seen.add(person_id)
        nodes.append(
            PersonNode(
                id=person_id,
                name=name,
                kind="person",
                profile_url=first_value(row, ["url", "profile url", "linkedin url"]),
                company=first_value(row, ["company", "current company"]),
                title=first_value(row, ["position", "title", "job title"]),
                source="linkedin_connections_csv",
                fame_score=0.1,
                is_user_connection=True,
            )
        )
    return nodes
