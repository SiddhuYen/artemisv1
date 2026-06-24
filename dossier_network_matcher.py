import argparse
import csv
import json
import os
import re


STOP_TERMS = {
    "source",
    "details",
    "found",
    "name",
    "title",
    "education",
    "location",
    "previous",
    "notes",
    "goal",
    "started",
    "achieved",
    "headquarters",
    "spanish",
    "consultant",
    "person",
    "target",
    "relevance",
    "about",
    "likely",
    "different",
    "unreadable",
    "linkedin",
    "wikipedia",
    "facebook",
    "twitter",
    "instagram",
    "youtube",
    "spotify",
    "google",
}

ORG_HINTS = {
    "school",
    "university",
    "college",
    "business",
    "group",
    "bcg",
    "accenture",
    "tiendeo",
    "letgo",
    "partners",
    "ventures",
    "capital",
    "startup",
    "plus",
    "esade",
    "iese",
    "universitat",
    "politècnica",
    "politecnica",
    "catalunya",
    "barcelona",
    "catalonia",
    "enisa",
}

GENERIC_CLUE_TERMS = {
    "venture capital",
    "capital",
    "startup",
    "startups",
    "technology",
    "ai",
    "artificial intelligence",
    "software",
    "university",
    "business school",
    "founder",
    "co founder",
    "cofounder",
    "investor",
    "advisor",
    "board",
    "board of directors",
    "board member",
    "ceo",
    "chief executive officer",
    "founding team",
    "business development",
    "business",
    "president",
    "chair",
    "chairman",
    "president ceo",
    "founder president",
    "founder president ceo",
    "founder president ceo cto",
    "director",
    "intern",
    "engineer",
    "student",
    "manager",
    "consultant",
    "partner",
    "vp",
    "vice president",
}

GENERIC_TITLE_WORDS = {
    "advisor",
    "analyst",
    "assistant",
    "board",
    "business",
    "chair",
    "chairman",
    "chief",
    "cofounder",
    "commissioner",
    "consultant",
    "cto",
    "ceo",
    "director",
    "employee",
    "engineer",
    "executive",
    "founder",
    "head",
    "intern",
    "lead",
    "leader",
    "manager",
    "member",
    "officer",
    "owner",
    "partner",
    "president",
    "principal",
    "secretary",
    "student",
    "supervisor",
    "trustee",
    "vice",
    "vp",
}

STRONG_RELATIONSHIPS = {
    "direct report",
    "coworker",
    "cofounder",
    "board",
    "investor",
    "advisor",
    "collaborator",
    "interviewer",
    "author",
    "event peer",
}

PRIVATE_RELATIONSHIP_TERMS = {
    "child",
    "children",
    "co parent",
    "coparent",
    "cousin",
    "daughter",
    "ex spouse",
    "family",
    "father",
    "former spouse",
    "husband",
    "minor",
    "mother",
    "parent",
    "sibling",
    "son",
    "spouse",
    "wife",
}

PROFESSIONAL_RELATIONSHIP_TERMS = {
    "advisor",
    "board",
    "collaborator",
    "cofounder",
    "coworker",
    "employee",
    "executive",
    "founder",
    "investor",
    "manager",
    "peer",
}


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def unique(values):
    seen = set()
    output = []
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip(" -:.,;()[]")
        key = normalize(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def parse_json_block(text, heading):
    match = re.search(rf"## {re.escape(heading)}(?P<body>.*?)(?:\n## |\Z)", text, re.S)
    body = match.group("body") if match else ""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", body, re.S) if body else None
    if not fenced and heading == "Artemis Target Map":
        leading = re.match(r"\s*```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        fenced = leading
    raw = fenced.group(1) if fenced else ""
    if not raw:
        object_match = re.search(r"\{.*\}", body, re.S)
        raw = object_match.group(0) if object_match else ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def artemis_map_from_dossier(dossier_text):
    data = parse_json_block(dossier_text, "Artemis Target Map")
    for key in ("closest_people", "second_layer_nodes"):
        nodes = data.get(key, [])
        if not isinstance(nodes, list):
            nodes = []
        clean_nodes = []
        for person in nodes:
            if not isinstance(person, dict):
                continue
            if not person.get("name") or not person.get("source_url") or not person.get("proof"):
                continue
            relationship_text = normalize(
                " ".join(
                    [
                        person.get("relationship_to_target", ""),
                        person.get("relationship_to_closest_person", ""),
                        person.get("proof", ""),
                        person.get("source_title", ""),
                    ]
                )
            )
            has_private_relation = any(term in relationship_text for term in PRIVATE_RELATIONSHIP_TERMS)
            has_professional_relation = any(term in relationship_text for term in PROFESSIONAL_RELATIONSHIP_TERMS)
            if has_private_relation and not has_professional_relation:
                continue
            person = dict(person)
            person["node_type"] = "closest" if key == "closest_people" else "second_layer"
            clean_nodes.append(person)
        data[key] = clean_nodes
    return data


def artemis_nodes(artemis_map):
    return list(artemis_map.get("second_layer_nodes", [])) + list(artemis_map.get("closest_people", []))


def useful_bridge_term(term):
    key = normalize(term)
    if not key or key in GENERIC_CLUE_TERMS:
        return False
    if len(key) < 4:
        return False
    words = key.split()
    if words and set(words).issubset(GENERIC_TITLE_WORDS):
        return False
    if len(words) == 1 and key not in ORG_HINTS:
        return False
    return True


def clean_bridge_terms(values):
    return unique(term for term in values if useful_bridge_term(term))


def artemis_bridge_terms(artemis_map):
    terms = []
    for person in artemis_nodes(artemis_map):
        terms.append(person.get("name", ""))
        organization = person.get("organization", "")
        if organization and not is_generic_term(organization):
            terms.append(organization)
        for term in person.get("bridge_terms", []) or []:
            terms.append(term)
    return clean_bridge_terms(terms)


def artemis_clue_terms(artemis_map):
    terms = []
    for person in artemis_nodes(artemis_map):
        organization = person.get("organization", "")
        if organization:
            terms.append(organization)
        for term in person.get("bridge_terms", []) or []:
            terms.append(term)
    return unique(terms)


def _legacy_artemis_map_from_dossier(dossier_text):
    data = parse_json_block(dossier_text, "Artemis Target Map")
    people = data.get("closest_people", [])
    if not isinstance(people, list):
        people = []
    clean_people = []
    for person in people:
        if not isinstance(person, dict):
            continue
        if not person.get("name") or not person.get("source_url") or not person.get("proof"):
            continue
        clean_people.append(person)
    data["closest_people"] = clean_people
    return data


def clue_terms_from_rejections(artemis_map):
    return unique(
        item.get("term", "")
        for item in artemis_map.get("rejected_clues", [])
        if isinstance(item, dict)
    )


def is_generic_term(term):
    key = normalize(term)
    if key in GENERIC_CLUE_TERMS:
        return True
    words = key.split()
    return len(words) == 1 and key in GENERIC_CLUE_TERMS


def read_text(path):
    with open(path, "r", encoding="utf-8") as file:
        return file.read()


def about_target_blocks(dossier_text):
    blocks = []
    chunks = re.split(r"\n- Source: ", dossier_text)
    for chunk in chunks:
        if "Relevance: about target" in chunk:
            blocks.append(chunk)
    return blocks or [dossier_text]


def generic_noise_terms(dossier_text):
    terms = set()
    identity_match = re.search(r"## Identity Check(?P<body>.*?)(?:\n## |\Z)", dossier_text, re.S)
    if not identity_match:
        return terms
    for match in re.findall(r"different (?:person|individual).*?named\s+([A-Z][A-Za-zÀ-ÿ' -]{3,80})", identity_match.group("body")):
        terms.add(normalize(match))
    return terms


def extract_terms(dossier_text):
    text = "\n".join(about_target_blocks(dossier_text))
    noise_terms = generic_noise_terms(dossier_text)
    filtered_lines = []
    for line in text.splitlines():
        normalized_line = normalize(line)
        if any(noise and noise in normalized_line for noise in noise_terms):
            continue
        filtered_lines.append(line)
    text = "\n".join(filtered_lines)
    terms = []

    # Markdown links/titles often contain exact names of organizations/pages.
    terms.extend(re.findall(r"\[([^\]]{3,100})\]\(", text))

    # Pull named entities-ish spans without needing an NLP dependency.
    terms.extend(
        re.findall(
            r"\b(?:[A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ&.\-']+)(?:\s+(?:[A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ&.\-']+|&|of|de|del|la|the)){0,5}",
            text,
        )
    )

    # Pull quoted/profile-worthy company and school phrases from detail bullets.
    for label in ("Education", "Previous role", "Previous experience", "Location", "Founded", "Headquarters"):
        terms.extend(re.findall(rf"{label}:\s*([^\n]+)", text, flags=re.I))

    cleaned = []
    for term in unique(terms):
        key = normalize(term)
        words = key.split()
        if key in STOP_TERMS:
            continue
        if len(key) < 4:
            continue
        if len(words) == 1 and key not in ORG_HINTS:
            continue
        if not (set(words) & ORG_HINTS):
            continue
        cleaned.append(term)
    return cleaned


def snippets_for_terms(text, terms, chars=220):
    normalized_text = normalize(text)
    snippets = []
    for term in terms:
        normalized_term = normalize(term)
        if not normalized_term:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(normalized_term) + r"(?![a-z0-9])"
        match = re.search(pattern, normalized_text)
        if not match:
            continue
        raw_index = match.start()
        ratio = raw_index / max(len(normalized_text), 1)
        approx_index = int(ratio * len(text))
        start = max(0, approx_index - chars // 2)
        end = min(len(text), approx_index + chars // 2)
        snippets.append({"term": term, "snippet": " ".join(text[start:end].split())})
    return snippets


def load_profiles(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as csv_file:
        header_offset = 0
        for line_number, line in enumerate(csv_file):
            lowered = line.lower()
            if ("first name" in lowered and "last name" in lowered) or "profile_text" in lowered or "profile_url" in lowered:
                header_offset = line_number
                break
        csv_file.seek(0)
        for _ in range(header_offset):
            next(csv_file, None)
        return list(csv.DictReader(csv_file))


def profile_text(row):
    full_name = " ".join(part for part in [row.get("First Name", ""), row.get("Last Name", "")] if part)
    return " ".join(
        [
            row.get("name", ""),
            full_name,
            row.get("company", ""),
            row.get("Company", ""),
            row.get("position", ""),
            row.get("Position", ""),
            row.get("URL", ""),
            row.get("extracted_companies", ""),
            row.get("extracted_schools", ""),
            row.get("extracted_roles", ""),
            row.get("extracted_sections", ""),
            row.get("hook_keywords", ""),
            row.get("profile_text", ""),
        ]
    )


def parse_paths(row):
    try:
        return json.loads(row.get("graph_paths", "[]") or "[]")
    except json.JSONDecodeError:
        return []


def format_person(person):
    full_name = " ".join(part for part in [person.get("First Name", ""), person.get("Last Name", "")] if part).strip()
    parts = [person.get("name") or full_name or "Unknown"]
    company = person.get("company") or person.get("Company")
    position = person.get("position") or person.get("Position")
    profile_url = person.get("profile_url") or person.get("URL")
    if company:
        parts.append(company)
    if position:
        parts.append(position)
    if profile_url:
        parts.append(profile_url)
    return " | ".join(parts)


def score_profiles(rows, terms, min_score):
    scored = []
    for row in rows:
        text = profile_text(row)
        snippets = snippets_for_terms(text, terms)
        if len(snippets) < min_score:
            continue
        scored.append(
            {
                "score": len(snippets),
                "row": row,
                "snippets": snippets,
                "paths": parse_paths(row),
            }
        )
    scored.sort(key=lambda item: (item["score"], len(item["paths"])), reverse=True)
    return scored


def score_artemis_profiles(rows, artemis_map, fallback_terms, min_score):
    bridge_terms = artemis_bridge_terms(artemis_map)
    clue_terms = unique(clue_terms_from_rejections(artemis_map) + artemis_clue_terms(artemis_map) + fallback_terms)
    bridge_matches = []
    clue_matches = []
    rejected = []

    for row in rows:
        text = profile_text(row)
        bridge_snippets = snippets_for_terms(text, bridge_terms)
        clue_snippets = snippets_for_terms(text, clue_terms)
        if bridge_snippets:
            matched_people = matched_target_nodes(bridge_snippets, artemis_map)
            bridge_matches.append(
                {
                    "score": len(bridge_snippets) * 3 + len(clue_snippets),
                    "row": row,
                    "snippets": bridge_snippets + clue_snippets[:5],
                    "bridge_snippets": bridge_snippets,
                    "clue_snippets": clue_snippets,
                    "target_people": matched_people,
                    "target_nodes": matched_people,
                    "paths": parse_paths(row),
                }
            )
        elif len(clue_snippets) >= min_score:
            clue_matches.append(
                {
                    "score": len(clue_snippets),
                    "row": row,
                    "snippets": clue_snippets,
                    "paths": parse_paths(row),
                }
            )
        else:
            rejected.append({"row": row, "reason": "No named target-side bridge term matched."})

    bridge_matches.sort(key=lambda item: (item["score"], len(item["target_people"])), reverse=True)
    clue_matches.sort(key=lambda item: item["score"], reverse=True)
    return bridge_matches, clue_matches, rejected, bridge_terms, clue_terms


def matched_target_people(snippets, artemis_map):
    return matched_target_nodes(snippets, artemis_map)


def matched_target_nodes(snippets, artemis_map):
    matched = []
    snippet_terms = {normalize(snippet["term"]) for snippet in snippets}
    for person in artemis_nodes(artemis_map):
        terms = clean_bridge_terms([person.get("name", ""), person.get("organization", "")] + (person.get("bridge_terms", []) or []))
        if any(normalize(term) in snippet_terms for term in terms):
            matched.append(person)
    return matched


def profile_payload(row):
    full_name = " ".join(part for part in [row.get("First Name", ""), row.get("Last Name", "")] if part).strip()
    return {
        "name": row.get("name") or full_name or "Unknown",
        "company": row.get("company") or row.get("Company", ""),
        "position": row.get("position") or row.get("Position", ""),
        "profile_url": row.get("profile_url") or row.get("URL", ""),
    }


def extract_json_object(text):
    match = re.search(r"\{.*\}", str(text or ""), re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def evidence_items_for_query(query, max_results, allow_insecure_ssl, search_provider):
    import person_deep_research

    try:
        results = person_deep_research.search_web(
            query,
            limit=max_results,
            allow_insecure_ssl=allow_insecure_ssl,
            provider=search_provider,
        )
    except Exception as error:
        return [{"query": query, "title": "Search failed", "url": "", "text": f"[Search failed: {error}]"}]
    return [
        {
            "query": query,
            "title": result.get("title", ""),
            "url": result.get("url", ""),
            "text": result.get("snippet", ""),
        }
        for result in results
    ]


def evidence_block(items):
    blocks = []
    for index, item in enumerate(items, 1):
        blocks.append(
            "\n".join(
                [
                    f"SOURCE {index}",
                    f"Query: {item.get('query', '')}",
                    f"Title: {item.get('title', '')}",
                    f"URL: {item.get('url', '')}",
                    f"Text: {item.get('text', '')[:2500]}",
                ]
            )
        )
    return "\n\n".join(blocks)


def build_seed_map(row, options):
    import person_deep_research

    person = profile_payload(row)
    queries = unique(
        [
            f'"{person["name"]}" "{person["company"]}"',
            f'"{person["name"]}" "{person["position"]}"',
            f'"{person["name"]}" professional relationships',
        ]
    )
    evidence = []
    for query in queries[:2]:
        evidence.extend(
            evidence_items_for_query(
                query,
                options.seed_search_results,
                options.allow_insecure_ssl,
                options.search_provider,
            )
        )
    prompt = f"""
Build a seed-side relationship map for this LinkedIn connection.

Person: {person["name"]}
Company: {person["company"] or "unknown"}
Title: {person["position"] or "unknown"}

Use only the evidence below. List only named, documented professional relationships.
Do not include vague affiliations, broad industries, same school, or same employer without a named interaction.
Do not include private personal details.

Return JSON only:
{{
  "relationships": [
    {{
      "name_or_org": "Named person, project, event, board, company, publication, or organization",
      "relationship_type": "coauthor / coworker / board / event / project / investor / advisor / employer / unknown",
      "evidence": "short source-backed reason",
      "source_url": "https://..."
    }}
  ],
  "rejected": [
    {{
      "term": "broad clue",
      "reason": "why it is not relationship evidence"
    }}
  ]
}}

Evidence:
{evidence_block(evidence)}
""".strip()
    try:
        text = person_deep_research.generate_text(
            options.provider,
            options.model,
            prompt,
            allow_insecure_ssl=options.allow_insecure_ssl,
        )
    except Exception as error:
        return {"relationships": [], "rejected": [{"term": person["name"], "reason": f"seed map failed: {error}"}]}
    data = extract_json_object(text)
    relationships = data.get("relationships", [])
    data["relationships"] = relationships if isinstance(relationships, list) else []
    return data


def target_chain_for_node(node, artemis_map):
    target = artemis_map.get("target", "Target")
    if node.get("node_type") == "expanded":
        closest_name = node.get("closest_person_name") or "closest target-side person"
        closest_node = next(
            (
                item
                for item in artemis_nodes(artemis_map)
                if normalize(item.get("name", "")) == normalize(closest_name)
                and item is not node
            ),
            None,
        )
        if closest_node:
            return [node.get("name", "")] + target_chain_for_node(closest_node, artemis_map)
        return [node.get("name", ""), closest_name, target]
    if node.get("node_type") == "second_layer":
        closest = node.get("closest_person_name") or "closest target-side person"
        return [node.get("name", ""), closest, target]
    return [node.get("name", ""), target]


def verify_hop(row, target_node, seed_map, artemis_map, options):
    import person_deep_research

    person = profile_payload(row)
    target_name = target_node.get("name", "")
    queries = unique(
        [
            f'"{person["name"]}" "{target_name}"',
            f'"{person["name"]}" "{target_name}" "{target_node.get("organization", "")}"',
        ]
    )
    evidence = []
    for query in queries[:2]:
        evidence.extend(
            evidence_items_for_query(
                query,
                options.verify_search_results,
                options.allow_insecure_ssl,
                options.search_provider,
            )
        )
    prompt = f"""
You are a strict relationship-hop verifier.

Question: Is there a named, documented professional relationship between:
Person A: {person["name"]} ({person["position"] or "unknown"} at {person["company"] or "unknown"})
Person B: {target_name} ({target_node.get("relationship_to_target") or target_node.get("relationship_to_closest_person") or "target-side node"} at {target_node.get("organization", "unknown")})

Evidence must be specific: named co-event/session, co-publication, direct reporting, named appointment, named project, board overlap, funding/advising relationship, interview, or documented collaboration.
First verify identity: the evidence must refer to the same Person B described by this target-side proof, not merely someone with the same name.
Target-side proof for Person B: {target_node.get("proof", "")}
Target-side source title: {target_node.get("source_title", "")}
Target-side source URL: {target_node.get("source_url", "")}

Invalid evidence:
- same broad field
- same city
- same large institution
- same employer without evidence they interacted
- same conference unless both are named in the same specific session/project
- shared title words like founder/advisor/CEO
- same name but mismatched identity, employer, age, school, or career context
- speculation that they probably know each other

Also consider this seed-side relationship map for Person A, but do not treat it as proof unless it names Person B or a specific shared project/org:
{json.dumps(seed_map, indent=2)[:5000]}

Return JSON only:
{{
  "verified": true,
  "relationship_type": "coevent / copublication / coworker / board / project / investor / interview / other",
  "evidence": "one sentence under 25 words proving the hop",
  "source_url": "https://...",
  "freshness": "current / recent / old but relevant / stale / unknown",
  "friction": "none / low / medium / high / unknown",
  "rejection_reason": ""
}}

If not verified, return:
{{
  "verified": false,
  "relationship_type": "",
  "evidence": "",
  "source_url": "",
  "freshness": "unknown",
  "friction": "unknown",
  "rejection_reason": "specific reason the evidence is not enough"
}}

Search evidence:
{evidence_block(evidence)}
""".strip()
    try:
        text = person_deep_research.generate_text(
            options.provider,
            options.model,
            prompt,
            allow_insecure_ssl=options.allow_insecure_ssl,
        )
    except Exception as error:
        return {
            "verified": False,
            "relationship_type": "",
            "evidence": "",
            "source_url": "",
            "freshness": "unknown",
            "friction": "unknown",
            "rejection_reason": f"verification failed: {error}",
        }
    data = extract_json_object(text)
    data["verified"] = bool(data.get("verified"))
    if data["verified"] and not data.get("source_url"):
        data["verified"] = False
        data["rejection_reason"] = "LLM marked verified but provided no source URL."
    return data


def verify_candidate_matches(matches, artemis_map, options):
    verified_paths = []
    rejected_candidates = []
    for match in matches[: options.verify_limit]:
        row = match["row"]
        seed_map = build_seed_map(row, options) if options.seed_map else {"relationships": []}
        target_nodes = match.get("target_nodes") or match.get("target_people") or []
        if not target_nodes:
            rejected_candidates.append(
                {
                    "match": match,
                    "target_node": {},
                    "verification": {"verified": False, "rejection_reason": "No specific target-side node matched."},
                    "seed_map": seed_map,
                }
            )
            continue
        any_verified = False
        for target_node in target_nodes[: options.verify_targets_per_match]:
            verification = verify_hop(row, target_node, seed_map, artemis_map, options)
            item = {
                "match": match,
                "target_node": target_node,
                "verification": verification,
                "seed_map": seed_map,
                "target_chain": target_chain_for_node(target_node, artemis_map),
            }
            if verification.get("verified"):
                any_verified = True
                verified_paths.append(item)
            else:
                rejected_candidates.append(item)
        if any_verified and len(verified_paths) >= options.verified_path_limit:
            break
    return verified_paths, rejected_candidates


def expansion_frontier_nodes(artemis_map):
    nodes = artemis_nodes(artemis_map)

    def strength_rank(node):
        strength = normalize(node.get("strength", ""))
        if strength == "strong":
            return 3
        if strength == "moderate":
            return 2
        return 1

    return sorted(nodes, key=strength_rank, reverse=True)


def expand_target_node(node, artemis_map, options):
    import person_deep_research

    name = node.get("name", "")
    organization = node.get("organization", "")
    if not name:
        return []
    queries = unique(
        [
            f'"{name}" "{organization}" board leadership team',
            f'"{name}" "{organization}" collaborators partners',
            f'"{name}" public professional relationships',
        ]
    )
    evidence = []
    for query in queries[: options.expansion_queries_per_node]:
        evidence.extend(
            evidence_items_for_query(
                query,
                options.expansion_search_results,
                options.allow_insecure_ssl,
                options.search_provider,
            )
        )
    prompt = f"""
Find the next public relationship layer around this target-side person.

Original target: {artemis_map.get("target", "Target")}
Frontier person: {name}
Organization/context: {organization or "unknown"}
Known relationship to target side: {node_relationship_label(node)}

Use only the search evidence below. Extract named people or specific organizations that have a documented professional, academic, creative, board, investment, event, interview, or collaboration relationship with the frontier person.

Rules:
- Return only public professional/civic/academic/creative relationships.
- No family, private address, minors, private accounts, leaked data, or speculation.
- Exclude generic roles/titles as bridge terms.
- Exclude the original target and the frontier person.
- Every returned node must have a source_url.
- Prefer lower-fame, closer operators over famous names.
- Keep it tight: max {options.expansion_nodes_per_frontier} nodes.

Return JSON only:
{{
  "nodes": [
    {{
      "name": "Person or specific organization",
      "relationship_to_frontier": "coworker / board peer / collaborator / investor / advisor / coauthor / same event / interview / school / other",
      "organization": "Specific org/project/event",
      "proof": "short source-backed reason",
      "source_url": "https://...",
      "strength": "strong / moderate / weak",
      "freshness": "current / recent / old but relevant / stale / unknown",
      "bridge_terms": ["exact non-generic names/orgs/projects/events"]
    }}
  ]
}}

Evidence:
{evidence_block(evidence)}
""".strip()
    try:
        text = person_deep_research.generate_text(
            options.provider,
            options.model,
            prompt,
            allow_insecure_ssl=options.allow_insecure_ssl,
        )
    except Exception:
        return []
    data = extract_json_object(text)
    nodes = data.get("nodes", [])
    if not isinstance(nodes, list):
        return []
    expanded = []
    blocked_names = {normalize(artemis_map.get("target", "")), normalize(name)}
    for item in nodes[: options.expansion_nodes_per_frontier]:
        if not isinstance(item, dict):
            continue
        item_name = item.get("name", "")
        if not item_name or normalize(item_name) in blocked_names:
            continue
        if not item.get("source_url") or not item.get("proof"):
            continue
        item = dict(item)
        item["node_type"] = "expanded"
        item["closest_person_name"] = name
        item["relationship_to_closest_person"] = item.get("relationship_to_frontier", "")
        item["expansion_parent"] = name
        item["expansion_depth"] = int(node.get("expansion_depth", 1) or 1) + 1
        expanded.append(item)
    return expanded


def expand_target_map_once(artemis_map, options):
    existing_keys = {
        normalize(node.get("name", ""))
        for node in artemis_nodes(artemis_map)
        if node.get("name")
    }
    frontier = expansion_frontier_nodes(artemis_map)[: options.expansion_frontier_limit]
    new_nodes = []
    for node in frontier:
        for expanded in expand_target_node(node, artemis_map, options):
            key = normalize(expanded.get("name", ""))
            if not key or key in existing_keys:
                continue
            existing_keys.add(key)
            new_nodes.append(expanded)
            if len(new_nodes) >= options.expansion_total_node_limit:
                break
        if len(new_nodes) >= options.expansion_total_node_limit:
            break
    if new_nodes:
        artemis_map = dict(artemis_map)
        artemis_map["second_layer_nodes"] = list(artemis_map.get("second_layer_nodes", [])) + new_nodes
    return artemis_map, new_nodes


def expand_until_verified(rows, artemis_map, fallback_terms, options):
    all_new_nodes = []
    all_rejected = []
    bridge_matches = []
    clue_matches = []
    bridge_terms = artemis_bridge_terms(artemis_map)
    clue_terms = unique(clue_terms_from_rejections(artemis_map) + artemis_clue_terms(artemis_map) + fallback_terms)
    verified_paths = []
    current_map = artemis_map
    for depth in range(options.expansion_layers):
        current_map, new_nodes = expand_target_map_once(current_map, options)
        if not new_nodes:
            break
        all_new_nodes.extend(new_nodes)
        bridge_matches, clue_matches, _, bridge_terms, clue_terms = score_artemis_profiles(
            rows,
            current_map,
            fallback_terms,
            options.min_score,
        )
        if not bridge_matches:
            continue
        verified_paths, rejected = verify_candidate_matches(bridge_matches, current_map, options)
        all_rejected.extend(rejected)
        if verified_paths:
            break
    return {
        "artemis_map": current_map,
        "new_nodes": all_new_nodes,
        "bridge_matches": bridge_matches,
        "clue_matches": clue_matches,
        "bridge_terms": bridge_terms,
        "clue_terms": clue_terms,
        "verified_paths": verified_paths,
        "rejected_candidates": all_rejected,
    }


def write_report(matches, terms, output, limit):
    lines = [
        "# Dossier Network Matches",
        "",
        "## Terms Checked",
        ", ".join(terms),
        "",
        "## Possible Network Connections",
    ]
    if not matches:
        lines.append("No matches found in the profile CSV.")

    for index, match in enumerate(matches[:limit], 1):
        row = match["row"]
        lines.append(f"\n### {index}. {format_person(row)}")
        lines.append(f"- Score: {match['score']}")
        lines.append(f"- Profile: {row.get('profile_url', '') or row.get('URL', '')}")
        if match["paths"]:
            lines.append("- Saved graph paths:")
            for path in match["paths"][:3]:
                hops = " -> ".join(format_person(person) for person in path)
                lines.append(f"  - {hops}")
        lines.append("- Matching evidence:")
        for snippet in match["snippets"][:12]:
            lines.append(f"  - `{snippet['term']}`: {snippet['snippet']}")

    report = "\n".join(lines)
    if output:
        with open(output, "w", encoding="utf-8") as output_file:
            output_file.write(report)
        print(f"Saved report to {output}")
    else:
        print(report)


def format_graph_path(row):
    paths = parse_paths(row)
    if not paths:
        return ["You", profile_payload(row).get("name", "Unknown")]
    path = paths[0]
    return [profile_payload(person).get("name", "Unknown") for person in path]


def node_relationship_label(node):
    return node.get("relationship_to_target") or node.get("relationship_to_closest_person") or "documented relationship"


def near_miss_path_items(bridge_matches, clue_matches, artemis_map, rejected_candidates=None, limit=5):
    rejected_candidates = rejected_candidates or []
    items = []
    seen = set()

    for rejected in rejected_candidates:
        match = rejected.get("match", {})
        row = match.get("row", {})
        target_node = rejected.get("target_node", {})
        if not row or not target_node:
            continue
        key = (profile_payload(row).get("name", ""), target_node.get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        verification = rejected.get("verification", {})
        items.append(
            {
                "kind": "rejected_specific",
                "confidence": "near miss - specific but unverified",
                "score": match.get("score", 0),
                "row": row,
                "target_node": target_node,
                "target_chain": target_chain_for_node(target_node, artemis_map),
                "snippets": match.get("bridge_snippets", []) + match.get("clue_snippets", [])[:3],
                "why": verification.get("rejection_reason", "Exact bridge search did not prove the hop."),
            }
        )

    for match in bridge_matches:
        row = match.get("row", {})
        target_nodes = match.get("target_nodes") or match.get("target_people") or []
        target_node = target_nodes[0] if target_nodes else {}
        key = (profile_payload(row).get("name", ""), target_node.get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "kind": "raw_bridge",
                "confidence": "possible - named overlap, unverified",
                "score": match.get("score", 0),
                "row": row,
                "target_node": target_node,
                "target_chain": target_chain_for_node(target_node, artemis_map) if target_node else [artemis_map.get("target", "Target")],
                "snippets": match.get("bridge_snippets", []) + match.get("clue_snippets", [])[:3],
                "why": "The local profile matched a named target-side person, organization, project, or event, but the exact relationship hop is not proven yet.",
            }
        )

    if not items:
        target = artemis_map.get("target", "Target")
        for match in clue_matches:
            row = match.get("row", {})
            key = (profile_payload(row).get("name", ""), "clue")
            if key in seen:
                continue
            seen.add(key)
            snippets = match.get("snippets", [])
            terms = unique(snippet.get("term", "") for snippet in snippets)
            specific_terms = [term for term in terms if normalize(term) not in GENERIC_CLUE_TERMS]
            bridge_label = (specific_terms or terms or ["broad clue"])[0]
            if normalize(bridge_label) in GENERIC_CLUE_TERMS:
                confidence = "very weak - generic clue only"
                why = (
                    "No named target-side bridge matched. This is only the strongest generic overlap, "
                    "so use it as a direction for more research, not as an intro path."
                )
                target_chain = [f"{bridge_label} theme", target]
            else:
                confidence = "weak - broad clue only"
                why = "No named target-side bridge matched, but this is the strongest specific overlap found in the network."
                target_chain = [f"{bridge_label} clue", target]
            items.append(
                {
                    "kind": "broad_clue",
                    "confidence": confidence,
                    "score": match.get("score", 0),
                    "row": row,
                    "target_node": {"name": bridge_label, "source_url": ""},
                    "target_chain": target_chain,
                    "snippets": snippets,
                    "why": why,
                }
            )
            if len(items) >= limit:
                break

    items.sort(key=lambda item: (item["kind"] == "rejected_specific", item["kind"] == "raw_bridge", item["score"]), reverse=True)
    return items[:limit]


def write_artemis_report(
    bridge_matches,
    clue_matches,
    artemis_map,
    bridge_terms,
    clue_terms,
    output,
    limit,
    verified_paths=None,
    rejected_candidates=None,
    verification_enabled=False,
    expansion_nodes=None,
):
    verified_paths = verified_paths or []
    rejected_candidates = rejected_candidates or []
    expansion_nodes = expansion_nodes or []
    lines = [
        "# Artemis Network Matches",
        "",
        "This report prioritizes endpoint-side bridge terms from the dossier's Artemis Target Map.",
        "A CSV match is not a verified path unless an exact bridging search proves each hop.",
        "",
        "## Endpoint-Side People Checked",
    ]
    if not artemis_map.get("closest_people"):
        lines.append("No Artemis Target Map people were found in the dossier.")
    for person in artemis_map.get("closest_people", []):
        lines.append(
            f"- {person.get('name', '')} | {person.get('relationship_to_target', '')} | "
            f"{person.get('organization', '')} | {person.get('strength', '')} | {person.get('freshness', '')}"
        )
        lines.append(f"  - Proof: {person.get('proof', '')}")
        lines.append(f"  - Source: {person.get('source_url', '')}")

    if artemis_map.get("second_layer_nodes"):
        lines.extend(["", "## Endpoint-Side Second Layer Checked"])
        for person in artemis_map.get("second_layer_nodes", []):
            lines.append(
                f"- {person.get('name', '')} -> {person.get('closest_person_name', '')} | "
                f"{person.get('relationship_to_closest_person', '')} | {person.get('organization', '')} | "
                f"{person.get('strength', '')} | {person.get('freshness', '')}"
            )
            lines.append(f"  - Proof: {person.get('proof', '')}")
            lines.append(f"  - Source: {person.get('source_url', '')}")

    if expansion_nodes:
        lines.extend(["", "## Extra Target-Side Layers Added"])
        lines.append(
            "Normal matching found no verified path, so Artemis expanded outward from the closest target-side people."
        )
        for person in expansion_nodes:
            lines.append(
                f"- {person.get('name', '')} -> {person.get('closest_person_name', '')} | "
                f"{person.get('relationship_to_closest_person', '')} | {person.get('organization', '')} | "
                f"{person.get('strength', '')} | {person.get('freshness', '')}"
            )
            lines.append(f"  - Proof: {person.get('proof', '')}")
            lines.append(f"  - Source: {person.get('source_url', '')}")

    lines.extend(["", "## Bridge Terms Checked", ", ".join(bridge_terms) or "None", ""])

    lines.append("## Verified Paths")
    if verification_enabled and not verified_paths:
        lines.append("No verified paths passed the exact-name bridging search.")
    elif not verification_enabled:
        lines.append("Hop verification was disabled for this run.")
    for index, item in enumerate(verified_paths[:limit], 1):
        row = item["match"]["row"]
        target_node = item["target_node"]
        verification = item["verification"]
        local_path = format_graph_path(row)
        target_chain = [name for name in item.get("target_chain", []) if name]
        full_path = local_path + target_chain
        lines.append(f"\n### {index}. {' -> '.join(full_path)}")
        lines.append(f"- Strength: Verified")
        lines.append(f"- Bridge hop: {profile_payload(row)['name']} -> {target_node.get('name', '')}")
        lines.append(f"- Evidence: {verification.get('evidence', '')}")
        lines.append(f"- Source: {verification.get('source_url', '')}")
        lines.append(f"- Freshness: {verification.get('freshness', 'unknown')}")
        lines.append(f"- Friction: {verification.get('friction', 'unknown')}")
        lines.append(f"- Target-side proof: {target_node.get('proof', '')}")
        lines.append(f"- Target-side source: {target_node.get('source_url', '')}")
        lines.append("- First activation ask:")
        lines.append(
            f"  - Ask {profile_payload(row)['name']} whether they can make or validate the connection to "
            f"{target_node.get('name', '')}; do not mention downstream names until they confirm."
        )

    if not verified_paths:
        lines.extend(["", "## Best Near-Miss Paths"])
        lines.append(
            "These are the best routes found even though at least one hop is iffy or unverified. Treat them as research leads, not confirmed paths."
        )
        near_misses = near_miss_path_items(
            bridge_matches,
            clue_matches,
            artemis_map,
            rejected_candidates=rejected_candidates,
            limit=min(limit, 5),
        )
        if not near_misses:
            lines.append("No near-miss path could be constructed from the current network scan.")
        for index, item in enumerate(near_misses, 1):
            row = item["row"]
            local_path = format_graph_path(row)
            target_chain = [name for name in item.get("target_chain", []) if name]
            full_path = local_path + target_chain
            target_node = item.get("target_node", {})
            lines.append(f"\n### {index}. {' -> '.join(full_path)}")
            lines.append(f"- Confidence: {item['confidence']}")
            lines.append(f"- Near-miss score: {item['score']}")
            lines.append(f"- Iffy hop: {profile_payload(row)['name']} -> {target_node.get('name', 'target-side clue')}")
            lines.append(f"- Why it is iffy: {item['why']}")
            if target_node.get("source_url"):
                lines.append(f"- Target-side source: {target_node.get('source_url')}")
            lines.append("- Evidence from your network row:")
            for snippet in item.get("snippets", [])[:6]:
                lines.append(f"  - `{snippet.get('term', '')}`: {snippet.get('snippet', '')}")
            lines.append("- Best next ask:")
            if item["kind"] == "broad_clue":
                lines.append(
                    f"  - Ask {profile_payload(row)['name']} if they know anyone closer to "
                    f"{artemis_map.get('target', 'the target')}'s world through this theme; do not treat this as an intro request yet."
                )
            else:
                lines.append(
                    f"  - Ask {profile_payload(row)['name']} if they personally know or can validate the link to "
                    f"{target_node.get('name', 'this target-side clue')} before using the downstream path."
                )

    lines.extend(["", "## Rejected Candidates"])
    if verification_enabled and not rejected_candidates:
        lines.append("No rejected candidates.")
    elif not verification_enabled:
        lines.append("Not run because hop verification was disabled.")
    for index, item in enumerate(rejected_candidates[:limit], 1):
        row = item["match"]["row"]
        target_node = item.get("target_node", {})
        verification = item.get("verification", {})
        lines.append(f"\n### {index}. {profile_payload(row)['name']} -> {target_node.get('name', 'target-side node')}")
        lines.append(f"- Reason rejected: {verification.get('rejection_reason', 'No verified relationship evidence found.')}")
        if target_node:
            lines.append(f"- Target-side node: {target_node.get('name', '')} | {target_node.get('organization', '')}")
            lines.append(f"- Target-side source: {target_node.get('source_url', '')}")

    lines.extend(["", "## Candidate Bridges Into Target-Side Map"])
    if verification_enabled:
        lines.append("These are raw overlap candidates. They are not paths unless they appear under Verified Paths.")
    if not bridge_matches:
        lines.append("No named target-side bridge matches found in the profile CSV.")

    for index, match in enumerate(bridge_matches[:limit], 1):
        row = match["row"]
        lines.append(f"\n### {index}. {format_person(row)}")
        lines.append(f"- Activation score: {match['score']}")
        lines.append(f"- Profile: {row.get('profile_url', '') or row.get('URL', '')}")
        lines.append("- Why this is a candidate:")
        if match["target_people"]:
            for person in match["target_people"][:5]:
                lines.append(
                    f"  - Matches target-side node `{person.get('name', '')}` "
                    f"({node_relationship_label(person)} at {person.get('organization', '')})."
                )
                lines.append(f"    Source: {person.get('source_url', '')}")
        else:
            lines.append("  - Matches a named organization/project from the endpoint-side map.")
        lines.append("- Matching evidence from CSV:")
        for snippet in match["bridge_snippets"][:12]:
            lines.append(f"  - `{snippet['term']}`: {snippet['snippet']}")
        if match["clue_snippets"]:
            lines.append("- Additional clue-only overlaps:")
            for snippet in match["clue_snippets"][:5]:
                lines.append(f"  - `{snippet['term']}`: {snippet['snippet']}")
        lines.append("- Status: Needs hop verification before this becomes a usable path.")

    lines.extend(["", "## Research Clues Only"])
    if not clue_matches:
        lines.append("No clue-only matches found.")
    for index, match in enumerate(clue_matches[:limit], 1):
        row = match["row"]
        lines.append(f"\n### {index}. {format_person(row)}")
        lines.append(f"- Clue score: {match['score']}")
        lines.append("- Why not a path yet: matched broad dossier terms but no named endpoint-side bridge.")
        for snippet in match["snippets"][:8]:
            lines.append(f"  - `{snippet['term']}`: {snippet['snippet']}")

    lines.extend(["", "## Rejected Clues From Dossier"])
    rejected = artemis_map.get("rejected_clues", [])
    if not rejected:
        lines.append("None listed.")
    for item in rejected:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('term', '')}`: {item.get('reason', '')}")

    report = "\n".join(lines)
    if output:
        with open(output, "w", encoding="utf-8") as output_file:
            output_file.write(report)
        print(f"Saved report to {output}")
    else:
        print(report)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Use a person dossier to search linkedin_network_profiles.csv for possible network overlap."
    )
    parser.add_argument("--dossier", required=True, help="Markdown dossier file.")
    parser.add_argument("--profiles-csv", default="linkedin_network_profiles.csv", help="Network profile CSV.")
    parser.add_argument("--extra-term", action="append", default=[], help="Additional term to match; can repeat.")
    parser.add_argument("--min-score", type=int, default=1, help="Minimum number of matched dossier terms.")
    parser.add_argument("--limit", type=int, default=50, help="Max matches in report.")
    parser.add_argument("--output", default="dossier_network_matches.md", help="Markdown report output.")
    parser.add_argument("--env", default=".env", help="Path to .env file for LLM/search keys.")
    parser.add_argument("--provider", choices=["gemini", "ollama"], default=None, help="LLM provider for hop verification.")
    parser.add_argument("--model", default=None, help="Model for hop verification.")
    parser.add_argument(
        "--search-provider",
        choices=["all", "brave", "apify", "google", "duckduckgo", "bing"],
        default="brave",
        help="Search provider for exact bridging searches.",
    )
    parser.add_argument("--allow-insecure-ssl", action="store_true")
    parser.add_argument("--no-verify-hops", dest="verify_hops", action="store_false", help="Skip exact-name hop verification.")
    parser.set_defaults(verify_hops=True)
    parser.add_argument("--verify-limit", type=int, default=8, help="Max bridge candidates to verify.")
    parser.add_argument("--verified-path-limit", type=int, default=3, help="Stop after this many verified paths.")
    parser.add_argument("--verify-targets-per-match", type=int, default=2, help="Max target-side nodes to test per candidate.")
    parser.add_argument("--verify-search-results", type=int, default=5, help="Search results per exact-name bridging query.")
    parser.add_argument("--seed-search-results", type=int, default=4, help="Search results for seed-side relationship mapping.")
    parser.add_argument("--no-seed-map", dest="seed_map", action="store_false", help="Skip seed-side relationship-map synthesis.")
    parser.set_defaults(seed_map=True)
    parser.add_argument("--no-layer-expansion", dest="layer_expansion", action="store_false", help="Do not expand target-side layers when no verified paths are found.")
    parser.set_defaults(layer_expansion=True)
    parser.add_argument("--expansion-layers", type=int, default=1, help="Extra target-side layers to add only after zero verified paths.")
    parser.add_argument("--expansion-frontier-limit", type=int, default=4, help="Max existing target-side nodes to expand per layer.")
    parser.add_argument("--expansion-nodes-per-frontier", type=int, default=3, help="Max new nodes extracted from each expanded target-side node.")
    parser.add_argument("--expansion-total-node-limit", type=int, default=10, help="Max new target-side nodes per expansion layer.")
    parser.add_argument("--expansion-queries-per-node", type=int, default=2, help="Search queries per target-side node expansion.")
    parser.add_argument("--expansion-search-results", type=int, default=4, help="Search results per expansion query.")
    return parser.parse_args()


def prepare_verification_options(args):
    import person_deep_research

    person_deep_research.load_dotenv(args.env)
    args.provider = args.provider or os.environ.get("LLM_PROVIDER", person_deep_research.DEFAULT_PROVIDER)
    if not args.model:
        if args.provider == "gemini":
            args.model = os.environ.get("GEMINI_MODEL", person_deep_research.DEFAULT_MODEL)
        else:
            args.model = os.environ.get("OLLAMA_MODEL", "llama3.1")
    return args


def main():
    args = parse_args()
    dossier_text = read_text(args.dossier)
    rows = load_profiles(args.profiles_csv)
    artemis_map = artemis_map_from_dossier(dossier_text)
    fallback_terms = unique(extract_terms(dossier_text) + args.extra_term)
    if artemis_map.get("closest_people"):
        bridge_matches, clue_matches, _, bridge_terms, clue_terms = score_artemis_profiles(
            rows,
            artemis_map,
            fallback_terms,
            args.min_score,
        )
        verified_paths = []
        rejected_candidates = []
        expansion_nodes = []
        if args.verify_hops:
            verify_options = prepare_verification_options(args)
            verified_paths, rejected_candidates = verify_candidate_matches(bridge_matches, artemis_map, verify_options)
            if not verified_paths and args.layer_expansion and args.expansion_layers > 0:
                expanded = expand_until_verified(rows, artemis_map, fallback_terms, verify_options)
                if expanded["new_nodes"]:
                    artemis_map = expanded["artemis_map"]
                    expansion_nodes = expanded["new_nodes"]
                    bridge_matches = expanded["bridge_matches"]
                    clue_matches = expanded["clue_matches"]
                    bridge_terms = expanded["bridge_terms"]
                    clue_terms = expanded["clue_terms"]
                    verified_paths = expanded["verified_paths"]
                    rejected_candidates.extend(expanded["rejected_candidates"])
        write_artemis_report(
            bridge_matches,
            clue_matches,
            artemis_map,
            bridge_terms,
            clue_terms,
            args.output,
            args.limit,
            verified_paths=verified_paths,
            rejected_candidates=rejected_candidates,
            verification_enabled=args.verify_hops,
            expansion_nodes=expansion_nodes,
        )
    else:
        matches = score_profiles(rows, fallback_terms, args.min_score)
        write_report(matches, fallback_terms, args.output, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
