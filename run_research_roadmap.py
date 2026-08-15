"""
CLI entry point for the Swing Research Program's Head of Research roadmap
-- regenerates swing_research/RESEARCH_ROADMAP.md from
swing_research/research_roadmap.py's candidate database and the LIVE
deployment registry, so the roadmap's portfolio-awareness and
diversification scoring always reflect the platform's actual current
state, not a stale snapshot.

    python run_research_roadmap.py [--top=20]

Read-only against the registry (uses deployment.deployment_manager.list_strategies()
only, via swing_research.research_roadmap.load_portfolio()) -- never
writes to it, never touches acceptance_criteria.py, evidence_quality.py,
cross_strategy_review.py, or anything else under deployment/. Writes only
swing_research/RESEARCH_ROADMAP.md.
"""

import argparse

from swing_research.research_roadmap import ROADMAP_PATH, build_roadmap, render_roadmap_markdown


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=20, help="How many researchable-now candidates to rank")
    args = parser.parse_args()

    roadmap = build_roadmap()
    markdown = render_roadmap_markdown(roadmap, top_n=args.top)
    with open(ROADMAP_PATH, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"Roadmap regenerated: {ROADMAP_PATH}")
    researchable = roadmap["researchable_now"][:args.top]
    print(f"\nTop {len(researchable)} researchable-now candidates:")
    for i, s in enumerate(researchable, 1):
        print(f"  {i}. {s.candidate.name} ({s.candidate.year}) -- score {s.total_score}/10, "
              f"diversification {s.diversification_score}/10")
    print(f"\n{len(roadmap['deferred_pending_data'])} candidate(s) deferred pending better data.")


if __name__ == "__main__":
    main()
