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
    RESEARCH_LANES,
    build_roadmap,
    classify_data_feasibility,
    compute_diversification_score,
    lane_of,
    render_roadmap_markdown,
    score_candidate,
)
from dataclasses import dataclass, replace


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

    def _axis_value_for_total(self, total):
        # score_candidate()'s total is a DEFAULT_WEIGHTS-weighted sum where the diversification axis
        # is computed independently (fixed at 10.0 here -- a fresh, non-overlapping tag against an
        # empty registry). Invert that so a fake candidate's uniform axis score lands on an exact,
        # known total, instead of guessing values and hoping they land on the right side of the bar.
        from deployment.deployment_manager import list_strategies
        portfolio = list_strategies(self.registry_path)
        div_score, _ = compute_diversification_score({"unique_probe_tag_zzz"}, portfolio)
        other_weight = 1.0 - DEFAULT_WEIGHTS["diversification"]
        return (total - DEFAULT_WEIGHTS["diversification"] * div_score) / other_weight

    def test_paper_direct_eligible_is_a_blocked_candidate_scoring_above_the_fixed_threshold(self):
        # 2026-09-22, per explicit direction: a candidate that can't get a real backtest but scores
        # above PAPER_DIRECT_SCORE_THRESHOLD (a fixed absolute bar, not tied to whatever's currently
        # backtestable) shouldn't just sit inert -- it's eligible for research_queue.py to propose
        # paper trading directly instead. A candidate sitting exactly ON the threshold must NOT qualify
        # (strictly greater than, not "at least").
        from types import SimpleNamespace
        from unittest import mock
        from swing_research.research_roadmap import PAPER_DIRECT_SCORE_THRESHOLD

        def fake(key, axis_score, available):
            return SimpleNamespace(key=key, name=key, factor_tags={key}, mechanism="m",
                                   data_requirements=(["daily_ohlcv_history"] if available else ["options_data"]),
                                   academic_evidence_score=axis_score, expected_robustness_score=axis_score,
                                   operational_simplicity_score=axis_score, research_value_score=axis_score,
                                   data_availability_score=axis_score, implementation_feasibility_score=axis_score,
                                   horizon_lane="swing", market="India")

        above = self._axis_value_for_total(PAPER_DIRECT_SCORE_THRESHOLD + 1.0)
        at = self._axis_value_for_total(PAPER_DIRECT_SCORE_THRESHOLD)
        below = self._axis_value_for_total(PAPER_DIRECT_SCORE_THRESHOLD - 4.0)
        fakes = [fake("backtestable_weak", 6.0, True), fake("backtestable_strong", 9.0, True),
                fake("blocked_good", above, False),
                fake("blocked_at_threshold", at, False),
                fake("blocked_poor", below, False)]
        with mock.patch("swing_research.research_roadmap.CANDIDATES", fakes):
            roadmap = build_roadmap(registry_path=self.registry_path)
        self.assertEqual({s.candidate.key for s in roadmap["researchable_now"]}, {"backtestable_weak", "backtestable_strong"})
        self.assertEqual({s.candidate.key for s in roadmap["paper_direct_eligible"]}, {"blocked_good"})
        self.assertIn("blocked_good", {s.candidate.key for s in roadmap["deferred_pending_data"]})   # still listed there too

    def test_paper_direct_eligible_is_empty_when_no_blocked_candidate_clears_the_threshold(self):
        from types import SimpleNamespace
        from unittest import mock
        from swing_research.research_roadmap import PAPER_DIRECT_SCORE_THRESHOLD
        at = self._axis_value_for_total(PAPER_DIRECT_SCORE_THRESHOLD)
        fake = SimpleNamespace(key="only_one", name="only_one", factor_tags=set(), mechanism="m",
                               data_requirements=["options_data"], academic_evidence_score=at,
                               expected_robustness_score=at, operational_simplicity_score=at,
                               research_value_score=at, data_availability_score=at, implementation_feasibility_score=at,
                               horizon_lane="swing", market="India")
        with mock.patch("swing_research.research_roadmap.CANDIDATES", [fake]):
            roadmap = build_roadmap(registry_path=self.registry_path)
        self.assertEqual(roadmap["researchable_now"], [])
        self.assertEqual(roadmap["paper_direct_eligible"], [])   # blocked, but doesn't clear the fixed bar

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


# Every candidate the monthly Discovery Scout has added outside the India lane, newest run last.
# Listed by hand rather than derived from lane_of() so the lane tests below stay real assertions
# instead of tautologies: a newly discovered non-India candidate makes them fail until it is added
# here, which is the intended signal to go and look at what landed.
_CRYPTO_KEYS = {"crypto_size_factor", "crypto_long_horizon_reversal",                  # PR #2
                "crypto_idiosyncratic_volatility", "crypto_illiquidity_premium",       # PR #3
                "crypto_funding_rate_carry"}
_US_KEYS = {"dogs_of_the_dow",                                                         # PR #2
            "us_price_delay_factor",                                                   # PR #3
            # cross-market ports of India PASS strategies, added 2026-10-03
            "us_short_term_reversal", "us_max_effect", "us_turn_of_month",
            "us_overnight_return_anomaly", "us_high_volume_return_premium",
            "us_earnings_announcement_premium"}


class TestLaneOf(unittest.TestCase):
    """lane_of() -- which of the three independent research queues (research_queue.py, added
    2026-10-03) a candidate belongs to."""

    def test_default_candidate_is_india(self):
        self.assertEqual(lane_of(CANDIDATES[0]), "india")

    def test_crypto_horizon_lane_is_the_crypto_queue_regardless_of_market(self):
        c = replace(CANDIDATES[0], horizon_lane="crypto")
        self.assertEqual(lane_of(c), "crypto")
        c2 = replace(CANDIDATES[0], horizon_lane="crypto", market="US")
        self.assertEqual(lane_of(c2), "crypto")   # crypto takes priority over market

    def test_us_market_is_the_us_queue(self):
        c = replace(CANDIDATES[0], market="US")
        self.assertEqual(lane_of(c), "us")

    def test_every_pre_lane_candidate_is_the_india_lane(self):
        # Confirms the backward-compat promise: every candidate that predates lanes (2026-10-03) is
        # still india -- checked by the same fixed key set TestScoringAndRoadmap's own
        # _PRE_HORIZON_LANE_KEYS-style test uses, not "every candidate in CANDIDATES", since the
        # Discovery Scout has since legitimately added real crypto/US candidates -- lane_of()
        # correctly routing THOSE elsewhere is the point, not a regression.
        for c in CANDIDATES:
            if c.key not in _CRYPTO_KEYS | _US_KEYS:
                self.assertEqual(lane_of(c), "india", c.key)

    def test_the_real_discovered_crypto_and_us_candidates_route_correctly(self):
        # The genuinely real, non-India candidates the Discovery Scout has found since lanes were
        # split -- this is the regression check that they land in the right queue, not a synthetic
        # fixture. Every key here is a candidate a real monthly run proposed and a human merged.
        by_key = {c.key: c for c in CANDIDATES}
        for key in _CRYPTO_KEYS:
            self.assertEqual(lane_of(by_key[key]), "crypto", key)
        for key in _US_KEYS:
            self.assertEqual(lane_of(by_key[key]), "us", key)


class TestDiversificationIsLaneScoped(unittest.TestCase):
    """2026-10-03: overlap only counts within a lane. A factor already running on NSE is not evidence
    about the S&P 500 -- both strategies ported to Pool I so far PASSed in the US while their India
    originals are INCONCLUSIVE."""

    def setUp(self):
        self.registry_path = _fake_registry_path()
        # India's short_term_reversal, live in paper trading -- the heaviest overlap weight there is.
        register_strategy("short_term_reversal", "Short-Term Reversal",
                          "swing_research published strategy", registry_path=self.registry_path)
        set_research_verdict("short_term_reversal", ResearchVerdict.PASS, registry_path=self.registry_path)
        set_deployment_status("short_term_reversal", DeploymentStatus.PAPER_TRADING,
                              reason="test", registry_path=self.registry_path)

    def tearDown(self):
        os.remove(self.registry_path)

    def _portfolio(self):
        from deployment.deployment_manager import list_strategies
        return list_strategies(self.registry_path)

    def test_an_india_strategy_does_not_penalise_a_us_candidate(self):
        score, notes = compute_diversification_score({"reversal_short_horizon"}, self._portfolio(), lane="us")
        self.assertEqual(score, 10.0)
        self.assertEqual(notes, [])

    def test_the_same_india_strategy_still_penalises_an_india_candidate(self):
        score, notes = compute_diversification_score({"reversal_short_horizon"}, self._portfolio(), lane="india")
        self.assertEqual(score, 7.0)        # 10 - 3.0 for PASS/PAPER_TRADING
        self.assertEqual(len(notes), 1)

    def test_india_remains_the_default_so_existing_callers_are_unchanged(self):
        self.assertEqual(compute_diversification_score({"reversal_short_horizon"}, self._portfolio()),
                         compute_diversification_score({"reversal_short_horizon"}, self._portfolio(), "india"))

    def test_a_us_strategy_penalises_a_us_candidate_in_its_own_lane(self):
        # the other half of lane scoping: US-vs-US overlap must still be seen. Before this change the
        # two Pool I strategies weren't in EXISTING_STRATEGY_TAGS at all, so it never was.
        register_strategy("cross_sectional_momentum_us", "Cross-Sectional Momentum (US)",
                          "us_equity", registry_path=self.registry_path)
        set_research_verdict("cross_sectional_momentum_us", ResearchVerdict.PASS, registry_path=self.registry_path)
        set_deployment_status("cross_sectional_momentum_us", DeploymentStatus.PAPER_TRADING,
                              reason="test", registry_path=self.registry_path)
        us_score, us_notes = compute_diversification_score({"momentum_cross_sectional"}, self._portfolio(), "us")
        self.assertEqual(us_score, 7.0)
        self.assertEqual(len(us_notes), 1)
        # ...and it must NOT leak into the India lane
        self.assertEqual(compute_diversification_score({"momentum_cross_sectional"}, self._portfolio(), "india")[0],
                         10.0)

    def test_the_real_us_ports_are_not_penalised_by_their_india_twins(self):
        # end-to-end against the REAL registry: every cross-market port scores a clean 10 on
        # diversification even though its India original is registered and live.
        from deployment.deployment_manager import list_strategies
        portfolio = list_strategies()      # the real one on purpose -- that's the regression
        by_key = {c.key: c for c in CANDIDATES}
        for key in ("us_short_term_reversal", "us_max_effect", "us_turn_of_month",
                    "us_overnight_return_anomaly", "us_high_volume_return_premium",
                    "us_earnings_announcement_premium"):
            self.assertEqual(score_candidate(by_key[key], portfolio).axis_scores["diversification"], 10.0, key)


class TestCrossMarketPorts(unittest.TestCase):
    """The six India-PASS mechanisms proposed as US-lane candidates (2026-10-03)."""

    PORTS = {"us_short_term_reversal": "short_term_reversal",
             "us_max_effect": "max_effect",
             "us_turn_of_month": "turn_of_month",
             "us_overnight_return_anomaly": "overnight_return_anomaly",
             "us_high_volume_return_premium": "high_volume_return_premium",
             "us_earnings_announcement_premium": "earnings_announcement_premium"}

    def test_every_port_lands_in_the_us_lane_and_is_researchable(self):
        r_us = build_roadmap(registry_path=_fake_registry_path(), lane="us")
        researchable = {s.candidate.key for s in r_us["researchable_now"]}
        for key in self.PORTS:
            self.assertIn(key, researchable, key)

    def test_each_port_keeps_its_india_originals_factor_tags(self):
        # so that once a port is promoted, same-lane overlap scoring recognises a future duplicate.
        from swing_research.research_roadmap import EXISTING_STRATEGY_TAGS
        by_key = {c.key: c for c in CANDIDATES}
        for port, india in self.PORTS.items():
            self.assertEqual(by_key[port].factor_tags, EXISTING_STRATEGY_TAGS[india], port)

    def test_the_india_originals_really_are_registered_and_passed(self):
        # the premise of the whole exercise -- if one of these is ever rolled back, the matching port's
        # rationale ("already PASSed here, untested there") no longer holds and should be revisited.
        from deployment.deployment_manager import list_strategies
        by_key = {r.strategy_key: r for r in list_strategies()}
        for india in self.PORTS.values():
            self.assertEqual(by_key[india].research_verdict.value, "PASS", india)

    def test_ports_use_only_data_this_program_actually_has(self):
        by_key = {c.key: c for c in CANDIDATES}
        for key in self.PORTS:
            classification, reasons = classify_data_feasibility(by_key[key].data_requirements)
            self.assertEqual(classification, "IMPLEMENTABLE", f"{key}: {reasons}")


class TestBuildRoadmapLaneFilter(unittest.TestCase):
    def test_no_lane_argument_scores_every_candidate_unchanged(self):
        r = build_roadmap(registry_path=_fake_registry_path())
        self.assertEqual(len(r["all_scored"]), len(CANDIDATES))

    def test_india_lane_excludes_the_non_india_candidates(self):
        r_all = build_roadmap(registry_path=_fake_registry_path())
        r_india = build_roadmap(registry_path=_fake_registry_path(), lane="india")
        non_india_keys = _CRYPTO_KEYS | _US_KEYS
        self.assertEqual({s.candidate.key for s in r_all["all_scored"]} - non_india_keys,
                         {s.candidate.key for s in r_india["all_scored"]})
        self.assertTrue(non_india_keys.isdisjoint({s.candidate.key for s in r_india["all_scored"]}))

    def test_crypto_lane_holds_every_discovered_crypto_candidate(self):
        # Three are genuinely researchable from daily OHLCV alone; crypto_size_factor is blocked on
        # market-cap data and crypto_funding_rate_carry on perpetual-futures funding rates, neither of
        # which this program integrates.
        r_crypto = build_roadmap(registry_path=_fake_registry_path(), lane="crypto")
        self.assertEqual({s.candidate.key for s in r_crypto["all_scored"]}, _CRYPTO_KEYS)
        self.assertEqual({s.candidate.key for s in r_crypto["researchable_now"]},
                         {"crypto_long_horizon_reversal", "crypto_idiosyncratic_volatility",
                          "crypto_illiquidity_premium"})

    def test_us_lane_is_researchable_now_that_a_non_blocked_candidate_exists(self):
        # 2026-10-03 (PR #3): us_price_delay_factor (Hou & Moskowitz, RFS 2005) needs only daily OHLCV,
        # which Pool I already fetches for the S&P 500 -- so the US lane finally has something its
        # weekly routine can actually pick up. dogs_of_the_dow stays blocked on dividend-yield data.
        r_us = build_roadmap(registry_path=_fake_registry_path(), lane="us")
        self.assertEqual({s.candidate.key for s in r_us["all_scored"]}, _US_KEYS)
        # everything except Dogs of the Dow is researchable: us_price_delay_factor plus the six
        # cross-market ports added 2026-10-03, all of which need only data this program already has.
        self.assertEqual({s.candidate.key for s in r_us["researchable_now"]}, _US_KEYS - {"dogs_of_the_dow"})
        self.assertEqual([s.candidate.key for s in r_us["deferred_pending_data"]], ["dogs_of_the_dow"])


if __name__ == "__main__":
    unittest.main()
