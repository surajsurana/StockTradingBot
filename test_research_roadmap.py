"""
Tests for swing_research/research_roadmap.py -- the Head of Research
roadmap extension to the Published Research Analyst. Uses a temporary,
fake registry file (never the real deployment/state/strategy_registry.json)
so these tests are isolated from and don't depend on the platform's actual
current portfolio state.
"""

import json
import os
import tempfile
import unittest

from deployment.deployment_manager import register_strategy, set_deployment_status, set_research_verdict
from deployment.base import DeploymentStatus, ResearchVerdict

from swing_research.research_roadmap import (
    CANDIDATES,
    DATA_CAPABILITIES,
    DEFAULT_WEIGHTS,
    build_roadmap,
    classify_data_feasibility,
    compute_diversification_score,
    render_roadmap_markdown,
    score_candidate,
)


def _fake_registry_path():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({}, f)
    return path


class TestClassifyDataFeasibility(unittest.TestCase):
    def test_fully_available_requirements_are_implementable(self):
        classification, reasons = classify_data_feasibility(["daily_ohlcv_history", "volume"])
        self.assertEqual(classification, "IMPLEMENTABLE")
        self.assertEqual(len(reasons), 2)

    def test_any_missing_requirement_blocks_the_whole_candidate(self):
        classification, reasons = classify_data_feasibility(
            ["daily_ohlcv_history", "point_in_time_fundamentals_history"]
        )
        self.assertEqual(classification, "NOT_CURRENTLY_IMPLEMENTABLE")
        self.assertEqual(len(reasons), 1)
        self.assertIn("point_in_time_fundamentals_history", reasons[0])

    def test_unrecognized_tag_fails_closed(self):
        # A tag DATA_CAPABILITIES has never heard of must be treated as
        # unavailable, not silently ignored -- same conservative default
        # fundamentals/fundamental_agent.py already uses for missing metrics.
        self.assertNotIn("some_made_up_tag", DATA_CAPABILITIES)
        classification, _ = classify_data_feasibility(["some_made_up_tag"])
        self.assertEqual(classification, "NOT_CURRENTLY_IMPLEMENTABLE")


class TestDiversificationScoring(unittest.TestCase):
    def setUp(self):
        self.registry_path = _fake_registry_path()

    def tearDown(self):
        os.remove(self.registry_path)

    def test_no_overlap_scores_maximum(self):
        from deployment.deployment_manager import list_strategies
        portfolio = list_strategies(self.registry_path)
        score, notes = compute_diversification_score({"risk_based"}, portfolio)
        self.assertEqual(score, 10.0)
        self.assertEqual(notes, [])

    def test_live_paper_trading_overlap_costs_more_than_archived_reject(self):
        # cross_sectional_momentum is tagged "momentum_cross_sectional" in
        # EXISTING_STRATEGY_TAGS -- register it PASS/PAPER_TRADING (heavy
        # overlap) vs. REJECT/ARCHIVED (light overlap) and confirm the
        # heavier-occupied family costs strictly more diversification credit.
        register_strategy("cross_sectional_momentum", "Cross-Sectional Momentum",
                           "swing_research published strategy", registry_path=self.registry_path)
        set_research_verdict("cross_sectional_momentum", ResearchVerdict.PASS, registry_path=self.registry_path)
        set_deployment_status("cross_sectional_momentum", DeploymentStatus.PAPER_TRADING,
                               reason="test", registry_path=self.registry_path)
        from deployment.deployment_manager import list_strategies
        portfolio_paper = list_strategies(self.registry_path)
        score_paper, notes_paper = compute_diversification_score({"momentum_cross_sectional"}, portfolio_paper)
        self.assertLess(score_paper, 10.0)
        self.assertEqual(len(notes_paper), 1)

        set_deployment_status("cross_sectional_momentum", DeploymentStatus.RESEARCH,
                               reason="test rollback", registry_path=self.registry_path)
        set_research_verdict("cross_sectional_momentum", ResearchVerdict.REJECT, registry_path=self.registry_path)
        set_deployment_status("cross_sectional_momentum", DeploymentStatus.ARCHIVED,
                               reason="test", registry_path=self.registry_path)
        portfolio_archived = list_strategies(self.registry_path)
        score_archived, _ = compute_diversification_score({"momentum_cross_sectional"}, portfolio_archived)

        self.assertLess(score_paper, score_archived)

    def test_unrelated_tags_do_not_overlap(self):
        register_strategy("short_term_reversal", "Short-Term Reversal",
                           "swing_research published strategy", registry_path=self.registry_path)
        set_research_verdict("short_term_reversal", ResearchVerdict.PASS, registry_path=self.registry_path)
        set_deployment_status("short_term_reversal", DeploymentStatus.PAPER_TRADING,
                               reason="test", registry_path=self.registry_path)
        from deployment.deployment_manager import list_strategies
        portfolio = list_strategies(self.registry_path)
        score, notes = compute_diversification_score({"risk_based"}, portfolio)
        self.assertEqual(score, 10.0)
        self.assertEqual(notes, [])

    def test_score_never_goes_negative(self):
        # Register every existing strategy this module knows about as
        # PASS/PRODUCTION (max overlap weight) sharing the same tag, and
        # confirm the floor holds at 0, never negative.
        for key in ["fifty_two_week_high_momentum", "cross_sectional_momentum", "minervini_trend_template_filter"]:
            register_strategy(key, key, "swing_research published strategy", registry_path=self.registry_path)
            set_research_verdict(key, ResearchVerdict.PASS, registry_path=self.registry_path)
            set_deployment_status(key, DeploymentStatus.PAPER_TRADING, reason="test", registry_path=self.registry_path)
            set_deployment_status(key, DeploymentStatus.PILOT_LIVE, reason="test", registry_path=self.registry_path)
            set_deployment_status(key, DeploymentStatus.PRODUCTION, reason="test", registry_path=self.registry_path)
        from deployment.deployment_manager import list_strategies
        portfolio = list_strategies(self.registry_path)
        score, _ = compute_diversification_score({"momentum_cross_sectional"}, portfolio)
        self.assertGreaterEqual(score, 0.0)


class TestScoringAndRoadmap(unittest.TestCase):
    def setUp(self):
        self.registry_path = _fake_registry_path()

    def tearDown(self):
        os.remove(self.registry_path)

    def test_score_candidate_weights_sum_to_total(self):
        from deployment.deployment_manager import list_strategies
        portfolio = list_strategies(self.registry_path)
        candidate = CANDIDATES[0]
        scored = score_candidate(candidate, portfolio)
        expected_total = round(sum(scored.axis_scores[k] * DEFAULT_WEIGHTS[k] for k in DEFAULT_WEIGHTS), 2)
        self.assertEqual(scored.total_score, expected_total)

    def test_not_currently_implementable_candidates_exist_and_are_flagged(self):
        from deployment.deployment_manager import list_strategies
        portfolio = list_strategies(self.registry_path)
        scored = [score_candidate(c, portfolio) for c in CANDIDATES]
        blocked = [s for s in scored if s.feasibility_classification == "NOT_CURRENTLY_IMPLEMENTABLE"]
        self.assertGreater(len(blocked), 0)

    def test_build_roadmap_researchable_now_is_sorted_descending(self):
        roadmap = build_roadmap(registry_path=self.registry_path)
        scores = [s.total_score for s in roadmap["researchable_now"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_build_roadmap_excludes_blocked_candidates_from_researchable_now(self):
        roadmap = build_roadmap(registry_path=self.registry_path)
        researchable_keys = {s.candidate.key for s in roadmap["researchable_now"]}
        for s in roadmap["deferred_pending_data"]:
            self.assertNotIn(s.candidate.key, researchable_keys)

    def test_every_candidate_is_accounted_for_exactly_once(self):
        roadmap = build_roadmap(registry_path=self.registry_path)
        total = len(roadmap["researchable_now"]) + len(roadmap["deferred_pending_data"])
        self.assertEqual(total, len(CANDIDATES))

    def test_render_roadmap_markdown_contains_key_sections(self):
        roadmap = build_roadmap(registry_path=self.registry_path)
        markdown = render_roadmap_markdown(roadmap, top_n=20)
        for heading in ["Ranked Research Roadmap", "Full Comparison Table", "Recommended Research Order",
                        "Deferred Pending Better Data", "Permanently Excluded", "Future Dataset Recommendations"]:
            self.assertIn(heading, markdown)


if __name__ == "__main__":
    unittest.main()
