"""
Mock-based unit tests for research_lab/performance_analyst.py. Confirms
the deterministic breakdowns are computed correctly, and that explain()'s
prompt is given the verdict as an already-fixed fact rather than
something it's asked to evaluate itself. No real Claude calls. Run with:

    python test_performance_analyst.py
"""

import unittest
from datetime import date

from research_lab.backtesting_engineer import Trade
from research_lab.performance_analyst import (
    build_narrative_prompt, compute_sector_breakdown, compute_time_of_day_breakdown, explain,
    load_sector_map,
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
