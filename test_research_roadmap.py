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

    def test_paper_direct_eligible_is_a_blocked_candidate_that_scores_as_well_as_the_backtestable_floor(self):
        # 2026-09-22, per explicit direction: a candidate that can't get a real backtest but scores as
        # well as the worst candidate that CAN shouldn't just sit inert -- it's eligible for
        # research_queue.py to propose paper trading directly instead.
        from types import SimpleNamespace
        from unittest import mock

        def fake(key, score, available):
            return SimpleNamespace(key=key, name=key, factor_tags={key}, mechanism="m",
                                   data_requirements=(["daily_ohlcv_history"] if available else ["options_data"]),
                                   academic_evidence_score=score, expected_robustness_score=score,
                                   operational_simplicity_score=score, research_value_score=score,
                                   data_availability_score=score, implementation_feasibility_score=score,
                                   horizon_lane="swing", market="India")

        fakes = [fake("backtestable_weak", 6.0, True), fake("backtestable_strong", 9.0, True),
                fake("blocked_good", 7.0, False), fake("blocked_poor", 2.0, False)]
        with mock.patch("swing_research.research_roadmap.CANDIDATES", fakes):
            roadmap = build_roadmap(registry_path=self.registry_path)
        self.assertEqual({s.candidate.key for s in roadmap["researchable_now"]}, {"backtestable_weak", "backtestable_strong"})
        self.assertEqual({s.candidate.key for s in roadmap["paper_direct_eligible"]}, {"blocked_good"})
        self.assertIn("blocked_good", {s.candidate.key for s in roadmap["deferred_pending_data"]})   # still listed there too

    def test_paper_direct_eligible_is_empty_when_nothing_is_backtestable_at_all(self):
        from types import SimpleNamespace
        from unittest import mock
        fake = SimpleNamespace(key="only_one", name="only_one", factor_tags=set(), mechanism="m",
                               data_requirements=["options_data"], academic_evidence_score=9,
                               expected_robustness_score=9, operational_simplicity_score=9,
                               research_value_score=9, data_availability_score=9, implementation_feasibility_score=9,
                               horizon_lane="swing", market="India")
        with mock.patch("swing_research.research_roadmap.CANDIDATES", [fake]):
            roadmap = build_roadmap(registry_path=self.registry_path)
        self.assertEqual(roadmap["researchable_now"], [])
        self.assertEqual(roadmap["paper_direct_eligible"], [])   # no floor to clear -- nothing to compare against

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
        total = (len(roadmap["researchable_now"]) + len(roadmap["deferred_pending_data"])
                 + len(roadmap["deferred_by_direction"]))
        self.assertEqual(total, len(CANDIDATES))

    def test_render_roadmap_markdown_contains_key_sections(self):
        roadmap = build_roadmap(registry_path=self.registry_path)
        markdown = render_roadmap_markdown(roadmap, top_n=20)
        for heading in ["Ranked Research Roadmap", "Full Comparison Table", "Recommended Research Order",
                        "Deferred Pending Better Data", "Permanently Excluded", "Future Dataset Recommendations"]:
            self.assertIn(heading, markdown)

    # The 31 candidates that predate horizon_lane (2026-09-22) -- these are what the "still reads as
    # swing without being touched by hand" test below checks BY KEY, not "every candidate in the list":
    # the whole point of horizon_lane is that a later addition (Discovery Scout's monthly pass, or a
    # human) can genuinely set "intraday"/"medium"/"long_term"/"crypto" -- asserting the whole list
    # would silently forbid that.
    _PRE_HORIZON_LANE_KEYS = {
        "nifty_momentum_30_style", "nifty_alpha_jensens", "nifty_low_volatility_30", "nifty_alpha_low_volatility_30",
        "nifty_quality_30", "nifty_value_20", "sehgal_long_term_contrarian_india", "volume_weighted_momentum_india",
        "nifty_index_inclusion_effect", "promoter_pledge_governance_signal", "fii_dii_flow_market_timing",
        "bonus_issue_announcement_drift", "coffee_can_quality_growth", "india_vix_regime_overlay",
        "long_term_reversal", "turnover_liquidity", "downside_beta", "industry_momentum", "turn_of_year",
        "day_of_week", "value_earnings_yield", "quality_composite", "accruals_anomaly", "asset_growth_anomaly",
        "analyst_revision_momentum", "short_interest_anomaly", "net_issuance_buybacks", "insider_trading_anomaly",
        "pairs_trading_stat_arb", "post_ipo_underperformance", "options_volatility_premia",
    }

    def test_every_existing_candidate_defaults_to_the_swing_india_lane(self):
        # this module is now the shared candidate roadmap for every research lane (2026-09-22) --
        # every candidate written before that still needs to read as "swing" without being touched by hand.
        pre_existing = [c for c in CANDIDATES if c.key in self._PRE_HORIZON_LANE_KEYS]
        self.assertEqual(len(pre_existing), len(self._PRE_HORIZON_LANE_KEYS))   # none of them got renamed/removed
        self.assertTrue(all(c.horizon_lane == "swing" and c.market == "India" for c in pre_existing))

    def test_holding_days_range_is_hand_classified_and_internally_consistent(self):
        # holding_days_min/max are a numeric SUMMARY of typical_holding_period's free text, hand-classified
        # per candidate (2026-09-22) -- every candidate must have been looked at, not silently left at the
        # dataclass default, and whichever ones DO have a range must have it the right way round.
        untouched = [c.key for c in CANDIDATES if c.holding_days_min is None and c.holding_days_max is None
                     and "N/A" not in c.typical_holding_period and "Avoid/underweight" not in c.typical_holding_period]
        self.assertEqual(untouched, [])   # every non-N/A candidate has a real range classified
        for c in CANDIDATES:
            if c.holding_days_min is not None:
                self.assertGreater(c.holding_days_min, 0, c.key)
                if c.holding_days_max is not None:
                    self.assertGreaterEqual(c.holding_days_max, c.holding_days_min, c.key)
            else:
                self.assertIsNone(c.holding_days_max, c.key)   # a min without a max is fine (open-ended); the reverse never is


if __name__ == "__main__":
    unittest.main()
