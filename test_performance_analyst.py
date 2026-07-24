"""
Mock-based unit tests for research_lab/performance_analyst.py. Confirms
the deterministic breakdowns are computed correctly, and that explain()'s
prompt is given the verdict as an already-fixed fact rather than
something it's asked to evaluate itself. No real Claude calls. Run with:

    python test_performance_analyst.py
"""

import unittest
from datetime import date

import pandas as pd

from research_lab.backtesting_engineer import Trade
from research_lab.performance_analyst import (
    build_narrative_prompt, compute_regime_breakdown, compute_sector_breakdown,
    compute_time_of_day_breakdown, explain, load_sector_map,
)


class TestSectorBreakdown(unittest.TestCase):
    def test_groups_pnl_by_sector(self):
        trades = [
            Trade("RELIANCE", date(2026, 1, 5), date(2026, 1, 5), 100, 110, 100, 1000.0, "target"),
            Trade("TCS", date(2026, 1, 6), date(2026, 1, 6), 100, 95, 100, -500.0, "stop_loss"),
        ]
        sector_map = {"RELIANCE": "Energy", "TCS": "IT"}
        breakdown = compute_sector_breakdown(trades, sector_map)
        self.assertEqual(breakdown["Energy"], 1000.0)
        self.assertEqual(breakdown["IT"], -500.0)

    def test_unknown_symbol_bucketed_as_unknown(self):
        trades = [Trade("MYSTERY", date(2026, 1, 5), date(2026, 1, 5), 100, 110, 100, 1000.0, "target")]
        breakdown = compute_sector_breakdown(trades, {})
        self.assertEqual(breakdown["Unknown"], 1000.0)

    def test_real_nifty500_csv_loads_and_has_reliance(self):
        sector_map = load_sector_map()
        self.assertIn("RELIANCE", sector_map)


class TestRegimeBreakdown(unittest.TestCase):
    """Regression tests for a real bug found 2026-07-24: is_bullish_on()'s
    regime_series is indexed by pd.Timestamp, but Trade.entry_date is a
    plain datetime.date -- `date not in DatetimeIndex` was always False for
    that mismatched type, so every trade in EXP-001/002/003 was silently
    misclassified as "bearish" regardless of the real regime. These tests
    use a DatetimeIndex-based series (matching fetch_nifty()'s real shape)
    specifically to catch that mismatch if it ever comes back."""

    def _regime_series(self):
        # 2026-01-05 = bullish, 2026-01-06 = bearish -- a DatetimeIndex,
        # exactly like build_regime_series() produces from real Nifty data.
        idx = pd.DatetimeIndex([pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")])
        return pd.Series([True, False], index=idx)

    def test_bullish_day_correctly_attributed_not_misclassified_as_bearish(self):
        regime_series = self._regime_series()
        trades = [Trade("A", date(2026, 1, 5), date(2026, 1, 5), 100, 110, 100, 1000.0, "target")]
        breakdown = compute_regime_breakdown(trades, regime_series)
        self.assertEqual(breakdown["bullish"], 1000.0)
        self.assertEqual(breakdown["bearish"], 0.0)

    def test_bearish_day_correctly_attributed(self):
        regime_series = self._regime_series()
        trades = [Trade("A", date(2026, 1, 6), date(2026, 1, 6), 100, 95, 100, -500.0, "stop_loss")]
        breakdown = compute_regime_breakdown(trades, regime_series)
        self.assertEqual(breakdown["bearish"], -500.0)
        self.assertEqual(breakdown["bullish"], 0.0)

    def test_mixed_regime_trades_split_correctly_not_all_bearish(self):
        # The exact failure signature of the real bug: every trade landing
        # in "bearish" regardless of the actual day.
        regime_series = self._regime_series()
        trades = [
            Trade("A", date(2026, 1, 5), date(2026, 1, 5), 100, 110, 100, 1000.0, "target"),
            Trade("B", date(2026, 1, 6), date(2026, 1, 6), 100, 95, 100, -500.0, "stop_loss"),
        ]
        breakdown = compute_regime_breakdown(trades, regime_series)
        self.assertEqual(breakdown["bullish"], 1000.0)
        self.assertEqual(breakdown["bearish"], -500.0)

    def test_date_genuinely_missing_from_series_uses_is_bullish_ons_safe_default(self):
        # is_bullish_on() itself documents "not in index -> False (bearish)"
        # as its safe default for a genuinely absent date -- distinct from
        # the bug this class guards against (a date that DOES exist in the
        # series but was never found due to a type mismatch).
        regime_series = self._regime_series()
        trades = [Trade("A", date(2099, 1, 1), date(2099, 1, 1), 100, 110, 100, 1000.0, "target")]
        breakdown = compute_regime_breakdown(trades, regime_series)
        self.assertEqual(breakdown["bearish"], 1000.0)


class TestTimeOfDayBreakdown(unittest.TestCase):
    def test_buckets_by_entry_hour(self):
        trades = [
            Trade("A", date(2026, 1, 5), date(2026, 1, 5), 100, 110, 100, 1000.0, "target", entry_hour=9.5),
            Trade("B", date(2026, 1, 6), date(2026, 1, 6), 100, 95, 100, -500.0, "stop_loss", entry_hour=14.2),
        ]
        breakdown = compute_time_of_day_breakdown(trades)
        self.assertEqual(breakdown[9.0], 1000.0)
        self.assertEqual(breakdown[14.0], -500.0)

    def test_skips_trades_with_no_entry_hour(self):
        trades = [Trade("A", date(2026, 1, 5), date(2026, 1, 5), 100, 110, 100, 1000.0, "target")]
        breakdown = compute_time_of_day_breakdown(trades)
        self.assertEqual(breakdown, {})


class TestExplain(unittest.TestCase):
    def test_verdict_is_passed_as_a_fixed_fact_not_evaluated(self):
        captured = {}

        def fake_call(prompt):
            captured["prompt"] = prompt
            return "narrative text"

        result = explain("Test Hyp", {"decision": "REJECT", "reasoning": "bad out of sample"},
                          {"win_rate": 0.3}, {}, {}, {}, call_fn=fake_call)
        self.assertEqual(result, "narrative text")
        self.assertIn("REJECT", captured["prompt"])
        self.assertIn("cannot change the verdict", captured["prompt"].lower())

    def test_prompt_includes_all_breakdowns(self):
        captured = {}
        explain("Test Hyp", {"decision": "PASS", "reasoning": "good"}, {"win_rate": 0.6},
                {"IT": 500.0}, {9.0: 200.0}, {"bullish": 700.0},
                call_fn=lambda p: captured.setdefault("prompt", p) or "narrative")
        self.assertIn("IT", captured["prompt"])
        self.assertIn("bullish", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
