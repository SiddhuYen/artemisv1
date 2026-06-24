import argparse
import os
import re
from types import SimpleNamespace

import dossier_network_matcher
import person_deep_research


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "person"


def prompt_if_missing(value, label, required=False):
    if value:
        return value
    prompt = f"{label}: "
    while True:
        response = input(prompt).strip()
        if response or not required:
            return response


def build_dossier(args, person, context, dossier_path):
    research_args = SimpleNamespace(
        person=person,
        context=context,
        provider=args.provider,
        model=args.model,
        env=args.env,
        profiles_csv=args.profiles_csv,
        include_local_profiles=False,
        local_matches=0,
        max_results=args.max_results,
        max_pages=args.max_pages,
        search_provider=args.search_provider,
        use_apify_instagram=args.use_apify_instagram,
        adjacent_pass=not args.no_adjacent_pass,
        max_adjacent_queries=args.max_adjacent_queries,
        institution_pass=not args.no_institution_pass,
        max_institution_queries=args.max_institution_queries,
        max_institution_followups=args.max_institution_followups,
        max_institution_pages=args.max_institution_pages,
        output=dossier_path,
        allow_insecure_ssl=args.allow_insecure_ssl,
    )
    report = person_deep_research.research_person(research_args)
    with open(dossier_path, "w", encoding="utf-8") as dossier_file:
        dossier_file.write(report)


def build_network_matches(args, dossier_path, matches_path):
    dossier_text = dossier_network_matcher.read_text(dossier_path)
    rows = dossier_network_matcher.load_profiles(args.profiles_csv)
    artemis_map = dossier_network_matcher.artemis_map_from_dossier(dossier_text)
    fallback_terms = dossier_network_matcher.unique(
        dossier_network_matcher.extract_terms(dossier_text) + args.extra_term
    )
    if artemis_map.get("closest_people"):
        bridge_matches, clue_matches, _, bridge_terms, clue_terms = dossier_network_matcher.score_artemis_profiles(
            rows,
            artemis_map,
            fallback_terms,
            args.min_score,
        )
        verified_paths = []
        rejected_candidates = []
        expansion_nodes = []
        if args.verify_hops:
            verify_options = dossier_network_matcher.prepare_verification_options(args)
            verified_paths, rejected_candidates = dossier_network_matcher.verify_candidate_matches(
                bridge_matches,
                artemis_map,
                verify_options,
            )
            if not verified_paths and args.layer_expansion and args.expansion_layers > 0:
                expanded = dossier_network_matcher.expand_until_verified(
                    rows,
                    artemis_map,
                    fallback_terms,
                    verify_options,
                )
                if expanded["new_nodes"]:
                    artemis_map = expanded["artemis_map"]
                    expansion_nodes = expanded["new_nodes"]
                    bridge_matches = expanded["bridge_matches"]
                    clue_matches = expanded["clue_matches"]
                    bridge_terms = expanded["bridge_terms"]
                    clue_terms = expanded["clue_terms"]
                    verified_paths = expanded["verified_paths"]
                    rejected_candidates.extend(expanded["rejected_candidates"])
        dossier_network_matcher.write_artemis_report(
            bridge_matches,
            clue_matches,
            artemis_map,
            bridge_terms,
            clue_terms,
            matches_path,
            args.match_limit,
            verified_paths=verified_paths,
            rejected_candidates=rejected_candidates,
            verification_enabled=args.verify_hops,
            expansion_nodes=expansion_nodes,
        )
    else:
        matches = dossier_network_matcher.score_profiles(rows, fallback_terms, args.min_score)
        dossier_network_matcher.write_report(matches, fallback_terms, matches_path, args.match_limit)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prompt for a person, create a dossier, then match it against linkedin_network_profiles.csv."
    )
    parser.add_argument("person", nargs="?", help="Person name. If omitted, you will be prompted.")
    parser.add_argument("--context", default="", help="Optional disambiguator: company, school, city, firm, etc.")
    parser.add_argument("--output-dir", default="research_outputs", help="Directory for generated reports.")
    parser.add_argument("--profiles-csv", default="linkedin_network_profiles.csv", help="Network profile CSV.")
    parser.add_argument("--env", default=".env", help="Path to .env file.")
    parser.add_argument("--provider", choices=["gemini", "ollama"], default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--search-provider",
        choices=["all", "brave", "apify", "google", "duckduckgo", "bing"],
        default="brave",
    )
    parser.add_argument("--use-apify-instagram", action="store_true")
    parser.add_argument("--max-results", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--max-adjacent-queries", type=int, default=20)
    parser.add_argument("--no-adjacent-pass", action="store_true")
    parser.add_argument("--no-institution-pass", action="store_true", help="Skip target-created institution/community searches.")
    parser.add_argument("--max-institution-queries", type=int, default=10)
    parser.add_argument("--max-institution-followups", type=int, default=8)
    parser.add_argument("--max-institution-pages", type=int, default=6)
    parser.add_argument("--min-score", type=int, default=1, help="Minimum dossier term matches in a profile.")
    parser.add_argument("--match-limit", type=int, default=50, help="Max network matches to report.")
    parser.add_argument("--extra-term", action="append", default=[], help="Additional network match term; can repeat.")
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


def main():
    args = parse_args()
    person = prompt_if_missing(args.person, "Person name", required=True)
    context = prompt_if_missing(args.context, "Optional context/company/school")

    os.makedirs(args.output_dir, exist_ok=True)
    base = slugify(" ".join(part for part in [person, context] if part))
    dossier_path = os.path.join(args.output_dir, f"{base}_dossier.md")
    matches_path = os.path.join(args.output_dir, f"{base}_network_matches.md")

    print(f"\nBuilding dossier for {person}...", flush=True)
    build_dossier(args, person, context, dossier_path)
    print(f"Saved dossier: {dossier_path}", flush=True)

    print("\nChecking network matches...", flush=True)
    build_network_matches(args, dossier_path, matches_path)
    print(f"Saved network matches: {matches_path}", flush=True)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
