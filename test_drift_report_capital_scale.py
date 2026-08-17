"""
Tests for deployment/drift_report.py's capital-scale fix (2026-08-17):
expectancy is compared as a percentage of each side's OWN starting
capital, not raw rupees -- a strategy behaving identically at two
different capital levels must not falsely flag MATERIAL drift purely
from position-size scaling. This is the exact bug found during the
paper-trading audit: research runs on Rs.1,00,000, paper trading (now
configurable) can run at a different capital entirely.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from deployment.drift_report import compute_drift, _EXPECTANCY_NORMALIZED_KEY


def _write_fake_experiment(experiments_dir: str, exp_id: str, starting_capital: float, metrics: dict):
    exp_dir = os.path.join(experiments_dir, exp_id)
    os.makedirs(exp_dir, exist_ok=True)
    with open(os.path.join(exp_dir, "hypothesis.md"), "w") as f:
        f.write("# Fake\n\n## Mechanism\ntest\n")
    with open(os.path.join(exp_dir, "parameters.json"), "w") as f:
        json.dump({"starting_capital": starting_capital}, f)
    with open(os.path.join(exp_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f)
    with open(os.path.join(exp_dir, "observations.md"), "w") as f:
        f.write("test")
    with open(os.path.join(exp_dir, "verdict.md"), "w") as f:
        f.write("# Verdict: PASS\n\ntest")


class TestExpectancyCapitalScaleFix(unittest.TestCase):
    def setUp(self):
        self.exp_dir = tempfile.mkdtemp()
        self.state_patcher = patch("deployment.paper_trading_engine.PAPER_TRADING_STATE_DIR", tempfile.mkdtemp())
        self.state_patcher.start()

    def tearDown(self):
        self.state_patcher.stop()
        shutil.rmtree(self.exp_dir, ignore_errors=True)

    def _seed_live_portfolio(self, strategy_key: str, starting_capital: float, cash: float):
        from deployment.paper_trading_engine import _save_portfolio
        _save_portfolio(strategy_key, {
            "cash": cash, "starting_capital": starting_capital, "positions": {},
            "last_processed_date": "2026-08-10", "pending_entries": {}, "pending_exits": {},
        })

    def _seed_live_trades(self, strategy_key: str, trades: list):
        from deployment.paper_trading_engine import _append_trade
        from swing_research.backtesting_engine import Trade
        from datetime import date
        for t in trades:
            _append_trade(strategy_key, Trade(
                symbol="TEST.NS", entry_date=date(2026, 8, 1), exit_date=date(2026, 8, 5),
                entry_price=100.0, exit_price=100.0 + t["pnl"] / t["qty"], quantity=t["qty"],
                pnl=t["pnl"], exit_reason="signal_exit", direction="BUY",
            ))

    def _seed_live_equity(self, strategy_key: str, starting_capital: float, days_and_cash: list):
        from deployment.paper_trading_engine import _append_daily_equity
        from datetime import date
        for i, cash in enumerate(days_and_cash):
            _append_daily_equity(strategy_key, date(2026, 8, 1 + i), cash, cash)

    def test_identical_relative_performance_at_different_capital_does_not_flag(self):
        """The core regression: historical at Rs.1L with expectancy
        Rs.1,000/trade (1% of capital) vs. live at Rs.10L with expectancy
        Rs.10,000/trade (ALSO exactly 1% of capital, i.e. genuinely
        identical relative performance) must NOT be flagged as drift."""
        _write_fake_experiment(self.exp_dir, "EXP-FAKE", starting_capital=100_000,
                                metrics={"win_rate": 0.5, "expectancy": 1000.0, "cagr": 10.0,
                                         "sharpe_ratio": 1.0, "max_drawdown_pct": 10.0,
                                         "avg_holding_period_days": 20.0})
        self._seed_live_portfolio("fake_strategy", starting_capital=1_000_000, cash=1_000_000)
        self._seed_live_trades("fake_strategy", [{"pnl": 10_000, "qty": 10}])
        self._seed_live_equity("fake_strategy", 1_000_000, [1_000_000, 1_010_000])

        drift = compute_drift("fake_strategy", "EXP-FAKE", self.exp_dir)
        self.assertNotIn("expectancy", drift["flags"],
                          f"False drift flag despite identical relative performance: {drift['flags']}")
        # Confirm the normalized figures are actually equal (both 1% of their own capital).
        self.assertAlmostEqual(drift["historical"][_EXPECTANCY_NORMALIZED_KEY], 1.0)
        self.assertAlmostEqual(drift["live"][_EXPECTANCY_NORMALIZED_KEY], 1.0)

    def test_genuinely_different_relative_performance_still_flags(self):
        """A real behavioral difference (not just capital scale) must
        still be caught -- this fix must not weaken drift detection."""
        _write_fake_experiment(self.exp_dir, "EXP-FAKE2", starting_capital=100_000,
                                metrics={"win_rate": 0.5, "expectancy": 1000.0, "cagr": 10.0,
                                         "sharpe_ratio": 1.0, "max_drawdown_pct": 10.0,
                                         "avg_holding_period_days": 20.0})
        self._seed_live_portfolio("fake_strategy2", starting_capital=1_000_000, cash=1_000_000)
        # Live expectancy is only Rs.1,000/trade on a 10x-larger capital
        # base -- 0.1% of capital vs. historical's 1%, a genuine 10x
        # relative underperformance, not a scale artifact.
        self._seed_live_trades("fake_strategy2", [{"pnl": 1_000, "qty": 10}])
        self._seed_live_equity("fake_strategy2", 1_000_000, [1_000_000, 1_001_000])

        drift = compute_drift("fake_strategy2", "EXP-FAKE2", self.exp_dir)
        self.assertIn("expectancy", drift["flags"])


if __name__ == "__main__":
    unittest.main()
