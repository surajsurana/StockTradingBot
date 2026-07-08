"""
Mock-based unit tests for cio/chief_investment_ai.py's risk_per_trade_pct
handling in parse_plan_response() -- the newest lever Chief Investment AI
controls monthly, alongside capital/target/strategies. Covers the same
"never a runaway swing" guardrails already proven for capital: a monthly
relative-change cap plus an absolute safety band. Run with:

    python test_chief_investment_ai_risk_clamp.py
"""

import unittest

from cio.chief_investment_ai import (
    MonthlyPlan, parse_plan_response,
    MIN_RISK_PER_TRADE_PCT, MAX_RISK_PER_TRADE_PCT, MAX_MONTHLY_RISK_PER_TRADE_CHANGE_PCT,
)


def _previous_plan(risk_per_trade_pct=0.01):
    return MonthlyPlan(
        month_label="June 2026", capital_allocated=100000, target_return_pct=3.0,
        active_strategies=["ma_crossover", "mean_reversion"], risk_per_trade_pct=risk_per_trade_pct,
    )


def _response(risk_pct: float) -> str:
    return f"""CAPITAL: 100000
TARGET_RETURN_PCT: 3.0
RISK_PER_TRADE_PCT: {risk_pct}
ACTIVE_STRATEGIES: ma_crossover, mean_reversion
NOTES: test plan"""


class TestRiskPerTradeClamp(unittest.TestCase):
    def test_small_increase_within_band_and_monthly_cap_is_accepted(self):
        # 1.0% -> 1.1% is a 10% relative increase, within the 15% monthly cap
        plan = parse_plan_response("July 2026", _response(1.1), _previous_plan(0.01))
        self.assertAlmostEqual(plan.risk_per_trade_pct, 0.011)

    def test_large_increase_gets_capped_to_monthly_change_limit(self):
        # Claude asks for 2.0% (double) -- monthly cap only allows +15% relative from 1.0%
        plan = parse_plan_response("July 2026", _response(2.0), _previous_plan(0.01))
        expected = 0.01 * (1 + MAX_MONTHLY_RISK_PER_TRADE_CHANGE_PCT)
        self.assertAlmostEqual(plan.risk_per_trade_pct, expected)

    def test_large_decrease_gets_capped_to_monthly_change_limit(self):
        plan = parse_plan_response("July 2026", _response(0.1), _previous_plan(0.01))
        expected = 0.01 * (1 - MAX_MONTHLY_RISK_PER_TRADE_CHANGE_PCT)
        self.assertAlmostEqual(plan.risk_per_trade_pct, expected)

    def test_absolute_band_wins_even_if_monthly_change_would_allow_more(self):
        # Starting already near the top of the band -- a further "allowed" relative
        # increase would breach MAX_RISK_PER_TRADE_PCT, so the absolute cap wins.
        near_max = MAX_RISK_PER_TRADE_PCT * 0.98
        plan = parse_plan_response("July 2026", _response(5.0), _previous_plan(near_max))
        self.assertLessEqual(plan.risk_per_trade_pct, MAX_RISK_PER_TRADE_PCT)

    def test_absolute_band_floor_enforced(self):
        near_min = MIN_RISK_PER_TRADE_PCT * 1.02
        plan = parse_plan_response("July 2026", _response(0.01), _previous_plan(near_min))
        self.assertGreaterEqual(plan.risk_per_trade_pct, MIN_RISK_PER_TRADE_PCT)

    def test_missing_field_falls_back_to_previous_value(self):
        response_without_risk = """CAPITAL: 100000
TARGET_RETURN_PCT: 3.0
ACTIVE_STRATEGIES: ma_crossover, mean_reversion
NOTES: test plan"""

        plan = parse_plan_response("July 2026", response_without_risk, _previous_plan(0.012))

        self.assertEqual(plan.risk_per_trade_pct, 0.012)
        self.assertIn("Could not parse", plan.notes)


if __name__ == "__main__":
    unittest.main()
