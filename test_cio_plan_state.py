"""
Mock-based unit tests for cio/plan_state.py -- persistence of Chief
Investment AI's monthly plan and the two resolvers that let run_daily.py /
monitor_positions.py actually use it. Run with:

    python test_cio_plan_state.py
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

from cio.chief_investment_ai import MonthlyPlan
from cio.plan_state import (
    load_monthly_plan, save_monthly_plan,
    effective_active_strategies, effective_capital_cap, effective_risk_per_trade_pct,
)


def _settings(active_strategies=("ma_crossover", "mean_reversion"), risk_per_trade_pct=0.01):
    return SimpleNamespace(ACTIVE_STRATEGIES=list(active_strategies), RISK_PER_TRADE_PCT=risk_per_trade_pct)


class TestPlanPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)
        self.path = self.tmp.name

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_load_missing_file_returns_none(self):
        self.assertIsNone(load_monthly_plan(self.path))

    def test_save_then_load_round_trips(self):
        plan = MonthlyPlan(
            month_label="July 2026", capital_allocated=5470.30, target_return_pct=3.0,
            active_strategies=["ma_crossover"], risk_per_trade_pct=0.012, notes="test plan",
        )
        save_monthly_plan(plan, self.path)

        loaded = load_monthly_plan(self.path)

        self.assertEqual(loaded.month_label, "July 2026")
        self.assertEqual(loaded.capital_allocated, 5470.30)
        self.assertEqual(loaded.target_return_pct, 3.0)
        self.assertEqual(loaded.active_strategies, ["ma_crossover"])
        self.assertEqual(loaded.risk_per_trade_pct, 0.012)
        self.assertEqual(loaded.notes, "test plan")

    def test_saving_a_new_plan_overwrites_the_old_one(self):
        first = MonthlyPlan(month_label="July 2026", capital_allocated=5000,
                             target_return_pct=3.0, active_strategies=["ma_crossover"])
        second = MonthlyPlan(month_label="August 2026", capital_allocated=5500,
                              target_return_pct=4.0, active_strategies=["mean_reversion"])
        save_monthly_plan(first, self.path)
        save_monthly_plan(second, self.path)

        loaded = load_monthly_plan(self.path)

        self.assertEqual(loaded.month_label, "August 2026")
        self.assertEqual(loaded.active_strategies, ["mean_reversion"])


class TestEffectiveActiveStrategies(unittest.TestCase):
    def test_no_plan_falls_back_to_settings(self):
        settings = _settings(active_strategies=["ma_crossover", "mean_reversion"])
        self.assertEqual(effective_active_strategies(None, settings), ["ma_crossover", "mean_reversion"])

    def test_plan_overrides_settings(self):
        settings = _settings(active_strategies=["ma_crossover", "mean_reversion"])
        plan = MonthlyPlan(month_label="July 2026", capital_allocated=5000,
                            target_return_pct=3.0, active_strategies=["mean_reversion"])
        self.assertEqual(effective_active_strategies(plan, settings), ["mean_reversion"])


class TestEffectiveCapitalCap(unittest.TestCase):
    def test_no_plan_uses_real_capital_uncapped(self):
        self.assertEqual(effective_capital_cap(None, 8000.0), 8000.0)

    def test_plan_caps_below_real_capital(self):
        plan = MonthlyPlan(month_label="July 2026", capital_allocated=5000,
                            target_return_pct=3.0, active_strategies=["ma_crossover"])
        self.assertEqual(effective_capital_cap(plan, 8000.0), 5000.0)

    def test_real_capital_caps_below_plan_when_account_shrank(self):
        """CIO can't authorize sizing against money that isn't actually there."""
        plan = MonthlyPlan(month_label="July 2026", capital_allocated=8000,
                            target_return_pct=3.0, active_strategies=["ma_crossover"])
        self.assertEqual(effective_capital_cap(plan, 5000.0), 5000.0)


class TestEffectiveRiskPerTradePct(unittest.TestCase):
    def test_no_plan_falls_back_to_settings(self):
        settings = _settings(risk_per_trade_pct=0.01)
        self.assertEqual(effective_risk_per_trade_pct(None, settings), 0.01)

    def test_plan_overrides_settings(self):
        settings = _settings(risk_per_trade_pct=0.01)
        plan = MonthlyPlan(month_label="July 2026", capital_allocated=5000, target_return_pct=3.0,
                            active_strategies=["ma_crossover"], risk_per_trade_pct=0.014)
        self.assertEqual(effective_risk_per_trade_pct(plan, settings), 0.014)


if __name__ == "__main__":
    unittest.main()
