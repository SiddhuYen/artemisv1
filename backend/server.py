import argparse
import cgi
import csv
import io
import json
import os
import re
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import dossier_network_matcher
import research_person_and_network


JOBS = {}
JOBS_LOCK = threading.Lock()
DATA_DIR = os.path.abspath(os.environ.get("ARTEMIS_DATA_DIR", PROJECT_ROOT))
OUTPUT_DIR = os.path.join(DATA_DIR, "research_outputs")
BOARD_DIR = os.path.join(DATA_DIR, "boards")
NETWORK_GRAPH_PATH = os.path.join(DATA_DIR, "user_network_graph.json")
NETWORK_CSV_PATH = os.path.join(DATA_DIR, "user_network_profiles.csv")
FALLBACK_NETWORK_CSV_PATH = os.path.join(PROJECT_ROOT, "linkedin_network_profiles.csv")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


def slugify(value):
    return research_person_and_network.slugify(value)


def normalize_url(url):
    return str(url or "").strip().split("?", 1)[0].rstrip("/")


def person_id(person):
    url = normalize_url(person.get("profile_url") or person.get("URL"))
    if url:
        return f"url:{url.lower()}"
    name = person.get("name") or " ".join(
        part for part in [person.get("First Name", ""), person.get("Last Name", "")] if part
    )
    company = person.get("company") or person.get("Company", "")
    key = re.sub(r"[^a-z0-9]+", "-", f"{name}-{company}".lower()).strip("-")
    return f"person:{key or 'unknown'}"


def profile_payload(row):
    full_name = " ".join(part for part in [row.get("First Name", ""), row.get("Last Name", "")] if part).strip()
    return {
        "name": row.get("name") or full_name or "Unknown",
        "company": row.get("company") or row.get("Company", ""),
        "position": row.get("position") or row.get("Position", ""),
        "profile_url": normalize_url(row.get("profile_url") or row.get("URL", "")),
    }


def format_person(person):
    payload = profile_payload(person)
    return " | ".join(part for part in [payload["name"], payload["company"], payload["position"], payload["profile_url"]] if part)


def empty_graph():
    return {
        "nodes": {
            "me": {
                "id": "me",
                "name": "You",
                "company": "",
                "position": "",
                "profile_url": "",
                "depth": 0,
                "source": "root",
            }
        },
        "edges": [],
    }


def load_graph():
    if not os.path.exists(NETWORK_GRAPH_PATH):
        return empty_graph()
    with open(NETWORK_GRAPH_PATH, "r", encoding="utf-8") as file:
        graph = json.load(file)
    graph.setdefault("nodes", {})
    graph.setdefault("edges", [])
    graph["nodes"].setdefault("me", empty_graph()["nodes"]["me"])
    return graph


def save_graph(graph):
    with open(NETWORK_GRAPH_PATH, "w", encoding="utf-8") as file:
        json.dump(graph, file, indent=2)
    write_network_csv(graph)


def edge_key(source_id, target_id):
    return f"{source_id}->{target_id}"


def add_node(graph, person, source="manual", depth=1):
    payload = profile_payload(person)
    node_id = person.get("id") or person_id(payload)
    existing = graph["nodes"].get(node_id, {})
    node = {
        "id": node_id,
        "name": payload["name"] or existing.get("name", "Unknown"),
        "company": payload["company"] or existing.get("company", ""),
        "position": payload["position"] or existing.get("position", ""),
        "profile_url": payload["profile_url"] or existing.get("profile_url", ""),
        "depth": min(int(existing.get("depth", depth) or depth), depth),
        "source": source or existing.get("source", ""),
    }
    graph["nodes"][node_id] = node
    return node


def add_edge(graph, source_id, target_id, source="manual"):
    if source_id == target_id:
        return
    key = edge_key(source_id, target_id)
    if any(edge.get("key") == key for edge in graph["edges"]):
        return
    graph["edges"].append({"key": key, "source": source_id, "target": target_id, "source_type": source})


def parse_linkedin_csv_bytes(raw_bytes):
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_index = 0
    for index, line in enumerate(lines):
        lowered = line.lower()
        if ("first name" in lowered and "last name" in lowered) or "profile_url" in lowered or "profile url" in lowered:
            header_index = index
            break
    csv_text = "\n".join(lines[header_index:])
    return list(csv.DictReader(io.StringIO(csv_text)))


def import_connections(raw_bytes, parent_id="me", filename="upload.csv", replace_root=False):
    rows = parse_linkedin_csv_bytes(raw_bytes)
    graph = empty_graph() if replace_root else load_graph()
    parent_id = parent_id or "me"
    if parent_id not in graph["nodes"]:
        parent_id = "me"
    parent_depth = int(graph["nodes"].get(parent_id, {}).get("depth", 0) or 0)
    added = 0
    for row in rows:
        payload = profile_payload(row)
        if not payload["name"] or payload["name"] == "Unknown":
            continue
        node = add_node(graph, payload, source=filename, depth=parent_depth + 1)
        add_edge(graph, parent_id, node["id"], source=filename)
        added += 1
    save_graph(graph)
    return {"added": added, "total_nodes": len(graph["nodes"]), "total_edges": len(graph["edges"])}


def adjacency(graph):
    adj = {}
    for edge in graph["edges"]:
        adj.setdefault(edge["source"], []).append(edge["target"])
    return adj


def shortest_paths_from_me(graph):
    adj = adjacency(graph)
    paths = {"me": ["me"]}
    queue = deque(["me"])
    while queue:
        node_id = queue.popleft()
        for next_id in adj.get(node_id, []):
            if next_id in paths:
                continue
            paths[next_id] = paths[node_id] + [next_id]
            queue.append(next_id)
    return paths


def write_network_csv(graph):
    paths = shortest_paths_from_me(graph)
    with open(NETWORK_CSV_PATH, "w", encoding="utf-8", newline="") as file:
        fieldnames = ["name", "company", "position", "profile_url", "graph_paths", "source", "depth"]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for node_id, node in graph["nodes"].items():
            if node_id == "me":
                continue
            path_ids = paths.get(node_id, ["me", node_id])
            path_people = [
                {
                    "name": graph["nodes"].get(path_id, {}).get("name", ""),
                    "company": graph["nodes"].get(path_id, {}).get("company", ""),
                    "position": graph["nodes"].get(path_id, {}).get("position", ""),
                    "profile_url": graph["nodes"].get(path_id, {}).get("profile_url", ""),
                }
                for path_id in path_ids
            ]
            writer.writerow(
                {
                    "name": node.get("name", ""),
                    "company": node.get("company", ""),
                    "position": node.get("position", ""),
                    "profile_url": node.get("profile_url", ""),
                    "graph_paths": json.dumps([path_people]),
                    "source": node.get("source", ""),
                    "depth": node.get("depth", ""),
                }
            )


def graph_summary():
    graph = load_graph()
    if not os.path.exists(NETWORK_CSV_PATH):
        write_network_csv(graph)
    nodes = list(graph["nodes"].values())
    return {
        "nodes": nodes,
        "edges": graph["edges"],
        "stats": {
            "nodes": len(nodes),
            "edges": len(graph["edges"]),
            "first_degree": sum(1 for node in nodes if node.get("depth") == 1),
            "second_degree_plus": sum(1 for node in nodes if int(node.get("depth", 0) or 0) > 1),
            "csv_path": NETWORK_CSV_PATH,
            "data_dir": DATA_DIR,
        },
    }


def route_payload(dossier_path, profiles_csv, extra_terms, min_score, limit):
    dossier_text = dossier_network_matcher.read_text(dossier_path)
    rows = dossier_network_matcher.load_profiles(profiles_csv)
    artemis_map = dossier_network_matcher.artemis_map_from_dossier(dossier_text)
    fallback_terms = dossier_network_matcher.unique(dossier_network_matcher.extract_terms(dossier_text) + extra_terms)
    target_name = artemis_map.get("target") or "Target"
    bridge_terms = []
    clue_terms = []
    if artemis_map.get("closest_people"):
        matches, clue_matches, _, bridge_terms, clue_terms = dossier_network_matcher.score_artemis_profiles(
            rows, artemis_map, fallback_terms, min_score
        )
        terms = bridge_terms + clue_terms
    else:
        terms = fallback_terms
        matches = dossier_network_matcher.score_profiles(rows, terms, min_score)
        clue_matches = []
    routes = []

    def path_people_for_row(row, target_chain=None):
        paths = dossier_network_matcher.parse_paths(row)
        if paths:
            people = [profile_payload(person) for person in paths[0]]
        else:
            people = [{"name": "You"}, profile_payload(row)]
        for name in target_chain or []:
            if not name:
                continue
            if people and people[-1].get("name") == name:
                continue
            people.append({"name": name, "company": "", "position": "", "profile_url": ""})
        if target_name and (not people or people[-1].get("name") != target_name):
            people.append({"name": target_name, "company": "", "position": "", "profile_url": ""})
        return people

    def gateway_payload(match, index):
        row = match["row"]
        snippets = match.get("snippets", [])[:8]
        terms_hit = [snippet.get("term", "") for snippet in snippets if snippet.get("term")]
        person = profile_payload(row)
        score = min(0.82, 0.24 + (match.get("score", 0) * 0.07))
        return {
            "rank": index,
            "score": round(score, 2),
            "raw_score": match.get("score", 0),
            "type": "gateway",
            "confidence": "path creation lead",
            "profile": person,
            "path": path_people_for_row(row, ["Target ecosystem", target_name]),
            "terms": terms_hit,
            "snippets": snippets,
            "explanation": (
                f"{format_person(row)} does not prove an intro path yet, but overlaps with the target ecosystem "
                f"through {', '.join(terms_hit[:4]) or 'public target-side clues'}. Use them to create a bridge."
            ),
            "creation_strategy": (
                "Ask this person for the most specific operator, recruiter, investor, customer, supplier, "
                "event host, or community lead they know inside the target ecosystem."
            ),
        }

    for index, match in enumerate(matches[:limit], 1):
        row = match["row"]
        snippets = match.get("snippets", [])[:8]
        terms_hit = [snippet["term"] for snippet in snippets]
        has_paths = bool(match.get("paths"))
        score = min(0.95, 0.34 + (match["score"] * 0.09) + (0.05 if has_paths else 0))
        target_people = match.get("target_people") or []
        target_chain = []
        if target_people:
            target_chain = dossier_network_matcher.target_chain_for_node(target_people[0], artemis_map)
        if artemis_map.get("closest_people"):
            target_names = ", ".join(person.get("name", "") for person in target_people[:3]) or "the target-side map"
            explanation = (
                f"{format_person(row)} matched named endpoint-side bridge terms tied to {target_names}. "
                "This is a bridge candidate until each hop is source-checked."
            )
        else:
            explanation = f"Matched dossier terms: {', '.join(terms_hit[:5])}."
        routes.append(
            {
                "rank": index,
                "score": round(score, 2),
                "raw_score": match["score"],
                "type": "candidate",
                "confidence": "possible - unverified bridge",
                "profile": profile_payload(row),
                "path": path_people_for_row(row, target_chain),
                "terms": terms_hit,
                "snippets": snippets,
                "explanation": explanation,
                "iffy_hop": f"{profile_payload(row)['name']} -> {target_people[0].get('name', 'target-side bridge')}" if target_people else "",
            }
        )

    if not routes and artemis_map.get("closest_people"):
        near_misses = dossier_network_matcher.near_miss_path_items(
            [],
            clue_matches,
            artemis_map,
            rejected_candidates=[],
            limit=min(limit, 8),
        )
        for index, item in enumerate(near_misses, 1):
            row = item["row"]
            snippets = item.get("snippets", [])[:8]
            target_chain = [name for name in item.get("target_chain", []) if name]
            target_node = item.get("target_node", {})
            routes.append(
                {
                    "rank": index,
                    "score": round(min(0.72, 0.22 + (item.get("score", 0) * 0.08)), 2),
                    "raw_score": item.get("score", 0),
                    "type": "near_miss",
                    "confidence": item.get("confidence", "near miss"),
                    "profile": profile_payload(row),
                    "path": path_people_for_row(row, target_chain),
                    "terms": [snippet.get("term", "") for snippet in snippets],
                    "snippets": snippets,
                    "explanation": item.get("why", "This is the best route found so far, but one hop is not verified."),
                    "iffy_hop": f"{profile_payload(row)['name']} -> {target_node.get('name', 'target-side clue')}",
                }
            )
    cold_approaches = []
    if not routes and artemis_map.get("closest_people"):
        cold_approaches = dossier_network_matcher.best_cold_approach_candidates(artemis_map, limit=min(limit, 10))

    gateways = [gateway_payload(match, index) for index, match in enumerate(clue_matches[: min(limit, 12)], 1)]
    ecosystem_terms = dossier_network_matcher.unique(
        bridge_terms[:20]
        + clue_terms[:20]
        + [
            item.get("term", "")
            for item in artemis_map.get("rejected_clues", [])
            if isinstance(item, dict)
        ][:20]
    )
    return {
        "terms": terms,
        "routes": routes,
        "gateways": gateways,
        "cold_approaches": cold_approaches,
        "ecosystem_terms": ecosystem_terms,
    }


def board_node_id(person, index):
    raw = normalize_url(person.get("profile_url", "")) or person.get("name", "") or f"node-{index}"
    key = re.sub(r"[^a-z0-9]+", "-", str(raw).lower()).strip("-")
    return key or f"node-{index}"


def board_person_key(person):
    url = normalize_url(person.get("profile_url", ""))
    if url:
        return f"url:{url.lower()}"
    name = dossier_network_matcher.normalize(person.get("name", ""))
    company = dossier_network_matcher.normalize(person.get("company", ""))
    if name and company:
        return f"name_company:{name}:{company}"
    if name:
        return f"name:{name}"
    return ""


def board_from_routes(job_id, person, context, routes_payload):
    routes = (routes_payload or {}).get("routes", [])[:8]
    gateways = (routes_payload or {}).get("gateways", [])[:8]
    cold_approaches = (routes_payload or {}).get("cold_approaches", [])[:8]
    ecosystem_terms = (routes_payload or {}).get("ecosystem_terms", [])[:20]
    has_working_path = any(route.get("type") not in {"near_miss"} for route in routes)
    nodes = {}
    edges = []
    leads = []
    target_node = {
        "name": person,
        "company": context,
        "position": "Target",
        "profile_url": "",
    }
    target_id = board_node_id(target_node, 999)

    for route_index, route in enumerate(routes):
        path = route.get("path", [])
        route_type = route.get("type") or "candidate"
        highlighted = has_working_path and route_type != "near_miss"
        for depth, person_item in enumerate(path):
            node_id = board_node_id(person_item, depth)
            role = "target" if depth == len(path) - 1 else "lead" if depth > 0 else "me"
            existing = nodes.get(node_id, {})
            nodes[node_id] = {
                "id": node_id,
                "name": person_item.get("name", "Unknown"),
                "company": person_item.get("company", ""),
                "position": person_item.get("position", ""),
                "profile_url": person_item.get("profile_url", ""),
                "depth": depth,
                "role": existing.get("role") if existing.get("role") == "target" else role,
                "source": "path_search",
                "highlighted": bool(existing.get("highlighted") or highlighted),
                "route_count": int(existing.get("route_count", 0)) + 1,
            }
            if depth > 0:
                source = board_node_id(path[depth - 1], depth - 1)
                edge_key_value = f"{source}->{node_id}:{route_index}"
                edges.append(
                    {
                        "key": edge_key_value,
                        "source": source,
                        "target": node_id,
                        "route": route_index,
                        "type": route_type,
                        "highlighted": highlighted,
                    }
                )
        if path:
            lead_person = route.get("profile") or (path[1] if len(path) > 1 else path[0])
            leads.append(
                {
                    "rank": route.get("rank") or route_index + 1,
                    "name": lead_person.get("name", ""),
                    "company": lead_person.get("company", ""),
                    "position": lead_person.get("position", ""),
                    "profile_url": lead_person.get("profile_url", ""),
                    "score": route.get("score", ""),
                    "type": route_type,
                    "confidence": route.get("confidence", ""),
                    "ask": (
                        "Validate this warm path before outreach."
                        if highlighted
                        else "Use this lead to create a bridge into the target ecosystem."
                    ),
                    "explanation": route.get("explanation", ""),
                }
            )

    for gateway_index, gateway in enumerate(gateways):
        path = gateway.get("path", [])
        route_index = len(routes) + gateway_index
        for depth, person_item in enumerate(path):
            node_id = board_node_id(person_item, depth)
            role = "target" if depth == len(path) - 1 else "ecosystem" if depth > 1 else "lead" if depth > 0 else "me"
            existing = nodes.get(node_id, {})
            nodes[node_id] = {
                "id": node_id,
                "name": person_item.get("name", "Unknown"),
                "company": person_item.get("company", ""),
                "position": person_item.get("position", ""),
                "profile_url": person_item.get("profile_url", ""),
                "depth": depth,
                "role": existing.get("role") if existing.get("role") == "target" else role,
                "source": "path_creation",
                "highlighted": bool(existing.get("highlighted")),
                "route_count": int(existing.get("route_count", 0)) + 1,
            }
            if depth > 0:
                source = board_node_id(path[depth - 1], depth - 1)
                edges.append(
                    {
                        "key": f"{source}->{node_id}:gateway-{gateway_index}",
                        "source": source,
                        "target": node_id,
                        "route": route_index,
                        "type": "gateway",
                        "highlighted": False,
                    }
                )
        lead_person = gateway.get("profile") or (path[1] if len(path) > 1 else {})
        leads.append(
            {
                "rank": len(leads) + 1,
                "name": lead_person.get("name", ""),
                "company": lead_person.get("company", ""),
                "position": lead_person.get("position", ""),
                "profile_url": lead_person.get("profile_url", ""),
                "score": gateway.get("score", ""),
                "type": "gateway",
                "confidence": gateway.get("confidence", "path creation lead"),
                "ask": "Create the path: ask for the nearest specific person in this ecosystem.",
                "explanation": gateway.get("explanation", ""),
                "creation_strategy": gateway.get("creation_strategy", ""),
            }
        )

    for cold_index, candidate in enumerate(cold_approaches):
        if target_id not in nodes:
            nodes[target_id] = {
                "id": target_id,
                "name": person,
                "company": context,
                "position": "Target",
                "profile_url": "",
                "depth": 4,
                "role": "target",
                "source": "target",
                "highlighted": True,
                "route_count": 0,
            }
        node = {
            "name": candidate.get("name", ""),
            "company": candidate.get("company", ""),
            "position": candidate.get("relationship_to_target", ""),
            "profile_url": candidate.get("source_url", ""),
        }
        node_id = board_node_id(node, len(nodes) + cold_index + 1)
        cold_depth = 2 + (cold_index % 2)
        nodes[node_id] = {
            "id": node_id,
            "name": node["name"],
            "company": node["company"],
            "position": node["position"],
            "profile_url": node["profile_url"],
            "depth": cold_depth,
            "role": "cold_approach",
            "source": "cold_approach",
            "highlighted": False,
            "route_count": 0,
        }
        edges.append(
            {
                "key": f"{node_id}->{target_id}:cold-{cold_index}",
                "source": node_id,
                "target": target_id,
                "route": f"cold-{cold_index}",
                "type": "cold_approach",
                "highlighted": False,
            }
        )
        leads.append(
            {
                "rank": len(leads) + 1,
                "name": candidate.get("name", ""),
                "company": candidate.get("company", ""),
                "position": candidate.get("relationship_to_target", ""),
                "profile_url": candidate.get("source_url", ""),
                "score": candidate.get("warmth_score", ""),
                "type": "cold_approach",
                "confidence": "cold approach lead",
                "ask": candidate.get("first_ask", "Cold approach with a narrow, evidence-based ask."),
                "explanation": candidate.get("outreach_reason", ""),
                "creation_strategy": f"Reply score {candidate.get('reply_probability', '')}/100; intro score {candidate.get('intro_probability', '')}/100.",
            }
        )

    board = {
        "id": job_id,
        "target": person,
        "context": context,
        "summary": {
            "working_paths": sum(1 for route in routes if route.get("type") != "near_miss"),
            "near_misses": sum(1 for route in routes if route.get("type") == "near_miss"),
            "gateways": len(gateways),
            "cold_approaches": len(cold_approaches),
            "leads": len(leads),
        },
        "ecosystem_terms": ecosystem_terms,
        "nodes": list(nodes.values()),
        "edges": edges,
        "leads": leads,
    }
    save_board(board)
    return board


def new_board(name="Untitled Board"):
    board_id = f"{int(time.time() * 1000)}_{slugify(name)[:32]}"
    board = {
        "id": board_id,
        "name": name or "Untitled Board",
        "target": "",
        "context": "",
        "summary": {"working_paths": 0, "near_misses": 0, "gateways": 0, "cold_approaches": 0, "leads": 0},
        "ecosystem_terms": [],
        "nodes": [
            {
                "id": "me",
                "name": "You",
                "company": "",
                "position": "",
                "profile_url": "",
                "depth": 0,
                "role": "me",
                "source": "root",
                "highlighted": True,
                "route_count": 0,
            }
        ],
        "edges": [],
        "leads": [],
        "saved": False,
        "updated_at": time.time(),
    }
    save_board(board)
    return board


def board_summary_item(board):
    return {
        "id": board.get("id", ""),
        "name": board.get("name") or board.get("target") or "Untitled Board",
        "target": board.get("target", ""),
        "updated_at": board.get("updated_at", 0),
        "nodes": len(board.get("nodes", [])),
        "edges": len(board.get("edges", [])),
        "saved": bool(board.get("saved")),
    }


def list_boards():
    os.makedirs(BOARD_DIR, exist_ok=True)
    boards = []
    for filename in os.listdir(BOARD_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(BOARD_DIR, filename), "r", encoding="utf-8") as file:
                board = json.load(file)
                if not board.get("name"):
                    continue
                boards.append(board_summary_item(board))
        except Exception:
            continue
    boards.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
    return boards


def ensure_board(board_id=""):
    if board_id:
        try:
            return load_board(board_id)
        except ValueError:
            pass
    boards = list_boards()
    if boards:
        return load_board(boards[0]["id"])
    return new_board("Untitled Board")


def recompute_board_summary(board):
    routes = {}
    for edge in board.get("edges", []):
        route_key = edge.get("route")
        if route_key in {"manual", None, ""}:
            continue
        routes.setdefault(route_key, edge.get("type", "candidate"))
    board["summary"] = {
        "working_paths": sum(1 for value in routes.values() if value not in {"near_miss", "gateway", "manual"}),
        "near_misses": sum(1 for value in routes.values() if value == "near_miss"),
        "gateways": sum(1 for value in routes.values() if value == "gateway"),
        "cold_approaches": sum(1 for node in board.get("nodes", []) if node.get("role") == "cold_approach"),
        "leads": len(board.get("leads", [])),
    }


def merge_boards(base, incoming, run_person, run_context, dossier_path="", matches_path=""):
    base.setdefault("nodes", [])
    base.setdefault("edges", [])
    base.setdefault("leads", [])
    existing_nodes = {node.get("id"): node for node in base["nodes"]}
    canonical_nodes = {}
    for node in base["nodes"]:
        for key in (
            board_person_key(node),
            f"name:{dossier_network_matcher.normalize(node.get('name', ''))}",
        ):
            if key and key not in canonical_nodes:
                canonical_nodes[key] = node
    edge_keys = {edge.get("key") for edge in base["edges"]}
    run_prefix = f"run-{incoming.get('id', int(time.time() * 1000))}"
    id_map = {}

    if not base.get("target"):
        base["target"] = incoming.get("target", run_person)
        base["context"] = incoming.get("context", run_context)
    base["name"] = base.get("name") or base.get("target") or "Untitled Board"

    for node in incoming.get("nodes", []):
        node = dict(node)
        node["last_run_person"] = run_person
        node["dossier_path"] = dossier_path
        node["matches_path"] = matches_path
        node_name_key = f"name:{dossier_network_matcher.normalize(node.get('name', ''))}"
        existing = existing_nodes.get(node.get("id")) or canonical_nodes.get(board_person_key(node)) or canonical_nodes.get(node_name_key)
        if node.get("role") == "target" and dossier_network_matcher.normalize(node.get("name")) != dossier_network_matcher.normalize(base.get("target")):
            node["role"] = "sub_target"
        if existing:
            id_map[node.get("id")] = existing.get("id")
            if existing.get("role") in {"cold_approach", "lead", "gateway", "ecosystem"} and node.get("role") in {"target", "sub_target"}:
                node["role"] = existing.get("role")
                node["depth"] = existing.get("depth", node.get("depth"))
            existing.update({key: value for key, value in node.items() if value not in ("", None, [])})
            existing["id"] = id_map[node.get("id")]
            existing["route_count"] = int(existing.get("route_count", 0) or 0) + int(node.get("route_count", 0) or 0)
            existing["highlighted"] = bool(existing.get("highlighted") or node.get("highlighted"))
        else:
            id_map[node.get("id")] = node.get("id")
            base["nodes"].append(node)
            existing_nodes[node.get("id")] = node
            for key in (board_person_key(node), node_name_key):
                if key and key not in canonical_nodes:
                    canonical_nodes[key] = node

    for edge in incoming.get("edges", []):
        edge = dict(edge)
        edge["source"] = id_map.get(edge.get("source"), edge.get("source"))
        edge["target"] = id_map.get(edge.get("target"), edge.get("target"))
        if edge.get("source") == edge.get("target"):
            continue
        edge["route"] = f"{run_prefix}:{edge.get('route', '')}"
        edge["key"] = f"{edge.get('source')}->{edge.get('target')}:{edge.get('route')}"
        if edge["key"] in edge_keys:
            continue
        edge_keys.add(edge["key"])
        base["edges"].append(edge)

    for lead in incoming.get("leads", []):
        lead = dict(lead)
        lead["run_person"] = run_person
        lead["dossier_path"] = dossier_path
        lead["matches_path"] = matches_path
        lead["rank"] = len(base["leads"]) + 1
        base["leads"].append(lead)

    base["ecosystem_terms"] = dossier_network_matcher.unique(
        list(base.get("ecosystem_terms", [])) + list(incoming.get("ecosystem_terms", []))
    )[:40]
    base["updated_at"] = time.time()
    base["saved"] = False
    recompute_board_summary(base)
    save_board(base)
    return base


def board_path(board_id):
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(board_id or "")).strip("._")
    if not safe_id:
        raise ValueError("Board id is required.")
    return os.path.join(BOARD_DIR, f"{safe_id}.json")


def save_board(board):
    os.makedirs(BOARD_DIR, exist_ok=True)
    with open(board_path(board["id"]), "w", encoding="utf-8") as file:
        json.dump(board, file, indent=2)


def load_board(board_id):
    path = board_path(board_id)
    if not os.path.exists(path):
        raise ValueError("Board not found.")
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def add_board_node(board_id, payload):
    board = load_board(board_id)
    parent_id = payload.get("parent_id") or ""
    node = {
        "name": (payload.get("name") or "").strip(),
        "company": (payload.get("company") or "").strip(),
        "position": (payload.get("position") or "").strip(),
        "profile_url": normalize_url(payload.get("profile_url") or ""),
    }
    if not node["name"]:
        raise ValueError("Name is required.")
    node["id"] = f"manual-{board_node_id(node, len(board.get('nodes', [])))}"
    node["depth"] = int(payload.get("depth") or 1)
    node["role"] = "manual"
    node["source"] = "manual"
    node["highlighted"] = False
    node["route_count"] = 0
    existing_ids = {item.get("id") for item in board.get("nodes", [])}
    while node["id"] in existing_ids:
        node["id"] = f"{node['id']}-1"
    board.setdefault("nodes", []).append(node)
    if parent_id and parent_id in existing_ids:
        board.setdefault("edges", []).append(
            {
                "key": f"{parent_id}->{node['id']}:manual",
                "source": parent_id,
                "target": node["id"],
                "route": "manual",
                "type": "manual",
                "highlighted": False,
            }
        )
    board.setdefault("leads", []).append(
        {
            "rank": len(board.get("leads", [])) + 1,
            "name": node["name"],
            "company": node["company"],
            "position": node["position"],
            "profile_url": node["profile_url"],
            "score": "",
            "type": "manual",
            "confidence": "manually added",
            "ask": "Research or contact manually.",
            "explanation": "",
        }
    )
    board["summary"] = {
        **board.get("summary", {}),
        "leads": len(board.get("leads", [])),
    }
    save_board(board)
    return board


def board_csv(board):
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "board_id",
            "target",
            "name",
            "company",
            "position",
            "profile_url",
            "role",
            "depth",
            "source",
            "highlighted",
            "route_count",
        ],
    )
    writer.writeheader()
    for node in board.get("nodes", []):
        writer.writerow(
            {
                "board_id": board.get("id", ""),
                "target": board.get("target", ""),
                "name": node.get("name", ""),
                "company": node.get("company", ""),
                "position": node.get("position", ""),
                "profile_url": node.get("profile_url", ""),
                "role": node.get("role", ""),
                "depth": node.get("depth", ""),
                "source": node.get("source", ""),
                "highlighted": node.get("highlighted", False),
                "route_count": node.get("route_count", 0),
            }
        )
    return output.getvalue()


FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")


def read_static_file(relative_path):
    normalized = os.path.normpath(relative_path).lstrip(os.sep)
    path = os.path.join(FRONTEND_DIR, normalized)
    if not os.path.abspath(path).startswith(os.path.abspath(FRONTEND_DIR)):
        return None
    if not os.path.exists(path) or not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as file:
        return file.read()


def make_job(payload):
    person = (payload.get("person") or "").strip()
    if not person:
        raise ValueError("Person name is required.")
    context = (payload.get("context") or "").strip()
    job_id = f"{int(time.time() * 1000)}_{slugify(person)[:36]}"
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "status": "queued", "person": person, "context": context, "log": ["Queued."], "message": "Queued.", "files": {}}
    threading.Thread(target=run_job, args=(job_id, payload), daemon=True).start()
    return job_id


def update_job(job_id, **updates):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        for key, value in updates.items():
            if key == "log":
                job.setdefault("log", []).append(value)
            else:
                job[key] = value


def read_file(path):
    if not path or not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as file:
        return file.read()


def active_profiles_csv():
    if os.path.exists(NETWORK_CSV_PATH):
        return NETWORK_CSV_PATH
    if os.path.exists(FALLBACK_NETWORK_CSV_PATH):
        return FALLBACK_NETWORK_CSV_PATH
    save_graph(load_graph())
    return NETWORK_CSV_PATH


def run_job(job_id, payload):
    person = payload["person"].strip()
    context = (payload.get("context") or "").strip()
    output_dir = OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)
    base = slugify(" ".join(part for part in [person, context] if part))
    dossier_path = os.path.join(output_dir, f"{base}_dossier.md")
    matches_path = os.path.join(output_dir, f"{base}_network_matches.md")
    profiles_csv = active_profiles_csv()
    args = SimpleNamespace(
        provider=None,
        model=None,
        env=".env",
        profiles_csv=profiles_csv,
        max_results=int(payload.get("max_results") or 8),
        max_pages=int(payload.get("max_pages") or 8),
        max_adjacent_queries=int(payload.get("max_adjacent_queries") or 20),
        no_adjacent_pass=bool(payload.get("no_adjacent_pass")),
        no_institution_pass=bool(payload.get("no_institution_pass")),
        max_institution_queries=int(payload.get("max_institution_queries") or 10),
        max_institution_followups=int(payload.get("max_institution_followups") or 8),
        max_institution_pages=int(payload.get("max_institution_pages") or 6),
        search_provider=payload.get("search_provider") or "brave",
        use_apify_instagram=bool(payload.get("use_apify_instagram")),
        allow_insecure_ssl=bool(payload.get("allow_insecure_ssl")),
        cache_dir=os.path.join(DATA_DIR, ".cache", "dossiers"),
        cache_days=int(payload.get("cache_days") or 30),
        force_refresh=bool(payload.get("force_refresh")),
        extra_term=[],
        min_score=1,
        match_limit=int(payload.get("match_limit") or 50),
        verify_hops=not bool(payload.get("no_verify_hops")),
        verify_limit=int(payload.get("verify_limit") or 8),
        verified_path_limit=int(payload.get("verified_path_limit") or 3),
        verify_targets_per_match=int(payload.get("verify_targets_per_match") or 2),
        verify_search_results=int(payload.get("verify_search_results") or 5),
        seed_search_results=int(payload.get("seed_search_results") or 4),
        seed_map=not bool(payload.get("no_seed_map")),
        layer_expansion=not bool(payload.get("no_layer_expansion")),
        expansion_layers=int(payload.get("expansion_layers") or 1),
        expansion_frontier_limit=int(payload.get("expansion_frontier_limit") or 4),
        expansion_nodes_per_frontier=int(payload.get("expansion_nodes_per_frontier") or 3),
        expansion_total_node_limit=int(payload.get("expansion_total_node_limit") or 10),
        expansion_queries_per_node=int(payload.get("expansion_queries_per_node") or 2),
        expansion_search_results=int(payload.get("expansion_search_results") or 4),
    )
    board_id = payload.get("board_id") or ""
    try:
        cache_note = "fresh refresh requested" if args.force_refresh else f"cache up to {args.cache_days} day(s)"
        update_job(job_id, status="running", message=f"Building dossier using {profiles_csv}...", log=f"Building dossier for {person} ({cache_note}).")
        research_person_and_network.build_dossier(args, person, context, dossier_path)
        update_job(job_id, files={"dossier": dossier_path}, dossier=read_file(dossier_path), log=f"Saved dossier: {dossier_path}")
        update_job(job_id, message="Checking graph/network matches...", log=f"Checking network matches in {profiles_csv}.")
        research_person_and_network.build_network_matches(args, dossier_path, matches_path)
        routes = route_payload(dossier_path, profiles_csv, args.extra_term, args.min_score, args.match_limit)
        generated_board = board_from_routes(job_id, person, context, routes)
        board = generated_board
        if board_id:
            board = merge_boards(load_board(board_id), generated_board, person, context, dossier_path, matches_path)
        update_job(job_id, status="done", message="Complete.", files={"dossier": dossier_path, "matches": matches_path}, dossier=read_file(dossier_path), matches=read_file(matches_path), routes=routes, board=board, log=f"Saved network matches: {matches_path}")
    except Exception as error:
        update_job(job_id, status="error", message=str(error), log=traceback.format_exc(), dossier=read_file(dossier_path), matches=read_file(matches_path), files={"dossier": dossier_path, "matches": matches_path})


class ResearchHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, content_type="text/html; charset=utf-8", status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self.send_static(parsed.path.replace("/static/", "", 1))
            return
        if parsed.path == "/api/network":
            self.send_json(graph_summary())
            return
        if parsed.path == "/api/boards":
            board = ensure_board()
            self.send_json({"boards": list_boards(), "current": board})
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            self.send_json(job or {"error": "Job not found."}, status=200 if job else 404)
            return
        if parsed.path.startswith("/api/boards/") and parsed.path.endswith("/csv"):
            board_id = parsed.path.split("/")[-2]
            try:
                board = load_board(board_id)
            except ValueError as error:
                self.send_json({"error": str(error)}, status=404)
                return
            body = board_csv(board)
            filename = f"{slugify(board.get('target', 'board'))}_board.csv"
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        if parsed.path.startswith("/api/boards/") and parsed.path.endswith("/save"):
            board_id = parsed.path.split("/")[-2]
            try:
                board = load_board(board_id)
            except ValueError as error:
                self.send_json({"error": str(error)}, status=404)
                return
            board["saved"] = True
            board["updated_at"] = time.time()
            save_board(board)
            self.send_json(board)
            return
        if parsed.path.startswith("/api/boards/"):
            board_id = parsed.path.rsplit("/", 1)[-1]
            try:
                self.send_json(load_board(board_id))
            except ValueError as error:
                self.send_json({"error": str(error)}, status=404)
            return
        if parsed.path == "/download":
            requested = parse_qs(parsed.query).get("path", [""])[0]
            normalized = os.path.abspath(os.path.normpath(requested))
            output_root = os.path.abspath(OUTPUT_DIR)
            if not normalized.startswith(output_root + os.sep) or not os.path.exists(normalized):
                self.send_text("Not found.", content_type="text/plain; charset=utf-8", status=404)
                return
            self.send_text(read_file(normalized), content_type="text/markdown; charset=utf-8")
            return
        self.send_text("Not found.", content_type="text/plain; charset=utf-8", status=404)

    def send_static(self, relative_path):
        body = read_static_file(relative_path)
        if body is None:
            self.send_text("Not found.", content_type="text/plain; charset=utf-8", status=404)
            return
        ext = os.path.splitext(relative_path)[1]
        self.send_text(body, content_type=CONTENT_TYPES.get(ext, "text/plain; charset=utf-8"))

    def do_POST(self):
        try:
            if self.path == "/api/research":
                length = int(self.headers.get("Content-Length", "0") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_json({"job_id": make_job(payload)})
                return
            if self.path == "/api/boards":
                length = int(self.headers.get("Content-Length", "0") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                board = new_board((payload.get("name") or "Untitled Board").strip())
                self.send_json(board)
                return
            if self.path.startswith("/api/boards/") and self.path.endswith("/save"):
                board_id = self.path.split("/")[-2]
                length = int(self.headers.get("Content-Length", "0") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                board = load_board(board_id)
                if payload.get("name") is not None:
                    board["name"] = (payload.get("name") or "Untitled Board").strip()
                if payload.get("board"):
                    incoming = payload["board"]
                    for key in ("target", "context", "nodes", "edges", "leads", "ecosystem_terms", "summary"):
                        if key in incoming:
                            board[key] = incoming[key]
                board["saved"] = True
                board["updated_at"] = time.time()
                save_board(board)
                self.send_json(board)
                return
            if self.path == "/api/network/reset":
                save_graph(empty_graph())
                self.send_json(graph_summary())
                return
            if self.path == "/api/network/manual":
                length = int(self.headers.get("Content-Length", "0") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                graph = load_graph()
                parent_id = payload.get("parent_id") or "me"
                if parent_id not in graph["nodes"]:
                    raise ValueError("Selected parent is not in graph.")
                parent_depth = int(graph["nodes"][parent_id].get("depth", 0) or 0)
                node = add_node(graph, payload, source="manual", depth=parent_depth + 1)
                add_edge(graph, parent_id, node["id"], source="manual")
                save_graph(graph)
                self.send_json({"node": node, **graph_summary()})
                return
            if self.path == "/api/network/upload":
                form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type")})
                file_item = form["file"] if "file" in form else None
                if file_item is None or not getattr(file_item, "file", None):
                    raise ValueError("CSV file is required.")
                parent_id = form.getfirst("parent_id", "me")
                replace_root = form.getfirst("replace_root", "0") == "1"
                filename = os.path.basename(getattr(file_item, "filename", "") or "upload.csv")
                result = import_connections(file_item.file.read(), parent_id=parent_id, filename=filename, replace_root=replace_root)
                self.send_json(result)
                return
            if self.path.startswith("/api/boards/") and self.path.endswith("/nodes"):
                board_id = self.path.split("/")[-2]
                length = int(self.headers.get("Content-Length", "0") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_json(add_board_node(board_id, payload))
                return
            self.send_json({"error": "Not found."}, status=404)
        except Exception as error:
            self.send_json({"error": str(error)}, status=400)


def parse_args():
    parser = argparse.ArgumentParser(description="Run the local ARTEMIS web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main():
    args = parse_args()
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(BOARD_DIR, exist_ok=True)
    if not os.path.exists(NETWORK_GRAPH_PATH):
        save_graph(empty_graph())
    server = ThreadingHTTPServer((args.host, args.port), ResearchHandler)
    print(f"ARTEMIS: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
