"""
Tests for deployment/paper_trading_engine.py's execution-realism additions
(2026-08-17): configurable capital, ExecutionRealismConfig (next-day-open
fill queueing, cost/cap adjustment via
swing_research.execution_realism_engine.compute_single_fill_cost()), and
the min-position-value hook. Each test uses an isolated tmp state dir --
never touches the real deployment/state/paper_trading/ directory SW-003/
SW-008 actually run under.
"""

import shutil
import tempfile
import unittest
from datetime import date
from typing import Optional
from unittest.mock import patch

import pandas as pd

from deployment.paper_trading_engine import ExecutionRealismConfig, run_daily
from swing_research.base import OpenPosition, Signal, Strategy


class _AlwaysBuyStrategy(Strategy):
    """Buys on a specific trigger date if flat, exits via a fixed stop or
    a specific exit-trigger date -- deterministic, for exact-value tests."""
    name = "always_buy_test"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = 0

    def __init__(self, entry_trigger_date, exit_trigger_date=None, stop_loss_pct=0.08):
        self.entry_trigger_date = entry_trigger_date
        self.exit_trigger_date = exit_trigger_date
        self.stop_loss_pct = stop_loss_pct

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        df = price_history.copy()
        df["date"] = df.index.date
        return df

    def entry_signal_at(self, row) -> Optional[Signal]:
        if row.date != self.entry_trigger_date:
            return None
        entry_price = float(row.Close)
        return Signal(symbol="", direction="BUY", entry_price=entry_price,
                      stop_loss=entry_price * (1 - self.stop_loss_pct), strategy_name=self.name)

    def exit_signal_at(self, row, open_position: OpenPosition) -> Optional[float]:
        if self.exit_trigger_date is not None and row.date == self.exit_trigger_date:
            return float(row.Close)
        return None


def _make_data(n=60, start_price=100.0, drift=0.2, volume=100000):
    dates = pd.bdate_range("2024-01-01", periods=n)
    close = pd.Series([start_price + i * drift for i in range(n)], index=dates)
    df = pd.DataFrame({
        "Open": close.shift(1).fillna(start_price) * 1.001, "High": close * 1.01, "Low": close * 0.98,
        "Close": close, "Volume": volume,
    }, index=dates)
    return {"TEST.NS": df}, dates


class _IsolatedStateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.patcher = patch("deployment.paper_trading_engine.PAPER_TRADING_STATE_DIR", self.tmp_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


class TestBackwardCompatibleDefault(_IsolatedStateTestCase):
    def test_default_execution_config_matches_pre_change_behavior(self):
        """The exact regression test: calling run_daily() with NO
        execution_config (or an explicit default ExecutionRealismConfig())
        must produce byte-identical entry/exit prices to the original
        same-day-close, zero-cost logic."""
        data, dates = _make_data()
        entry_date = dates[10].date()
        exit_date = dates[20].date()
        strategy = _AlwaysBuyStrategy(entry_trigger_date=entry_date, exit_trigger_date=exit_date)

        results_by_date = {}
        for d in dates[:22]:
            results_by_date[d.date()] = run_daily("test_strategy", strategy, fetch_data_fn=lambda: data,
                                                    as_of_date=d.date())

        result = results_by_date[exit_date]
        self.assertEqual(len(result["new_exits"]), 1)
        exit_info = result["new_exits"][0]
        expected_close = float(data["TEST.NS"].loc[data["TEST.NS"].index.date == exit_date, "Close"].iloc[0])
        self.assertEqual(exit_info["exit_price"], expected_close)   # same-day-close, no cost adjustment
        self.assertNotIn("fill_timing", exit_info)   # only present when next_day_open is active

    def test_explicit_default_config_is_identical_to_none(self):
        data, dates = _make_data()
        strategy1 = _AlwaysBuyStrategy(entry_trigger_date=dates[5].date())
        strategy2 = _AlwaysBuyStrategy(entry_trigger_date=dates[5].date())

        for d in dates[:7]:
            r1 = run_daily("s1", strategy1, fetch_data_fn=lambda: data, as_of_date=d.date())
        for d in dates[:7]:
            r2 = run_daily("s2", strategy2, fetch_data_fn=lambda: data,
                            as_of_date=d.date(), execution_config=ExecutionRealismConfig())
        self.assertEqual(r1["new_entries"], r2["new_entries"])


class TestMinPositionValue(_IsolatedStateTestCase):
    def test_zero_threshold_disabled_takes_the_trade(self):
        data, dates = _make_data()
        entry_date = dates[5].date()
        strategy = _AlwaysBuyStrategy(entry_trigger_date=entry_date)
        results_by_date = {}
        for d in dates[:7]:
            results_by_date[d.date()] = run_daily("s", strategy, fetch_data_fn=lambda: data,
                                                    as_of_date=d.date(), min_position_value_rupees=0)
        self.assertEqual(len(results_by_date[entry_date]["new_entries"]), 1)

    def test_high_threshold_skips_the_trade(self):
        data, dates = _make_data()
        strategy = _AlwaysBuyStrategy(entry_trigger_date=dates[5].date())
        for d in dates[:7]:
            result = run_daily("s", strategy, fetch_data_fn=lambda: data, as_of_date=d.date(),
                                min_position_value_rupees=10_000_000)
        self.assertEqual(result["new_entries"], [])


class TestNextDayOpenFillTiming(_IsolatedStateTestCase):
    def test_entry_is_queued_not_filled_same_day(self):
        data, dates = _make_data()
        entry_date = dates[5].date()
        strategy = _AlwaysBuyStrategy(entry_trigger_date=entry_date)
        config = ExecutionRealismConfig(fill_timing="next_day_open")

        result = None
        for d in dates[:6]:
            result = run_daily("s", strategy, fetch_data_fn=lambda: data, as_of_date=d.date(),
                                execution_config=config)
        self.assertEqual(result["new_entries"], [])   # queued, not filled on the signal day itself

    def test_entry_fills_next_day_at_that_days_real_open(self):
        data, dates = _make_data()
        entry_date = dates[5].date()
        strategy = _AlwaysBuyStrategy(entry_trigger_date=entry_date)
        config = ExecutionRealismConfig(fill_timing="next_day_open")

        for d in dates[:7]:
            result = run_daily("s", strategy, fetch_data_fn=lambda: data, as_of_date=d.date(),
                                execution_config=config)
        self.assertEqual(len(result["new_entries"]), 1)
        entry = result["new_entries"][0]
        self.assertEqual(entry["fill_timing"], "next_day_open")
        expected_open = float(data["TEST.NS"].loc[data["TEST.NS"].index.date == dates[6].date(), "Open"].iloc[0])
        self.assertEqual(entry["entry_price"], expected_open)

    def test_exit_is_queued_then_filled_next_open(self):
        data, dates = _make_data()
        entry_date = dates[3].date()
        exit_date = dates[10].date()
        strategy = _AlwaysBuyStrategy(entry_trigger_date=entry_date, exit_trigger_date=exit_date)
        config = ExecutionRealismConfig(fill_timing="next_day_open")

        for d in dates[:12]:
            result = run_daily("s", strategy, fetch_data_fn=lambda: data, as_of_date=d.date(),
                                execution_config=config)
        self.assertEqual(len(result["new_exits"]), 1)
        exit_info = result["new_exits"][0]
        self.assertEqual(exit_info["fill_timing"], "next_day_open")
        expected_open = float(data["TEST.NS"].loc[data["TEST.NS"].index.date == dates[11].date(), "Open"].iloc[0])
        self.assertEqual(exit_info["exit_price"], expected_open)


class TestCostAndBrokerage(_IsolatedStateTestCase):
    def test_illiq_cost_widens_buy_fill_price(self):
        data, dates = _make_data(volume=500)   # thin -> nontrivial ILLIQ
        entry_date = dates[30].date()   # needs 20d ADV/ILLIQ lookback warm-up
        strategy = _AlwaysBuyStrategy(entry_trigger_date=entry_date)
        config = ExecutionRealismConfig(illiq_cost_k=1e9)

        results_by_date = {}
        for d in dates[:32]:
            results_by_date[d.date()] = run_daily("s", strategy, fetch_data_fn=lambda: data,
                                                    as_of_date=d.date(), execution_config=config)
        result = results_by_date[entry_date]
        self.assertEqual(len(result["new_entries"]), 1)
        raw_close = float(data["TEST.NS"].loc[data["TEST.NS"].index.date == entry_date, "Close"].iloc[0])
        self.assertGreater(result["new_entries"][0]["entry_price"], raw_close)

    def test_brokerage_deducted_from_cash_on_entry(self):
        data, dates = _make_data()
        entry_date = dates[5].date()
        strategy_a = _AlwaysBuyStrategy(entry_trigger_date=entry_date)
        strategy_b = _AlwaysBuyStrategy(entry_trigger_date=entry_date)

        for d in dates[:6]:
            r_no_fee = run_daily("no_fee", strategy_a, fetch_data_fn=lambda: data, as_of_date=d.date())
        for d in dates[:6]:
            r_fee = run_daily("fee", strategy_b, fetch_data_fn=lambda: data, as_of_date=d.date(),
                               execution_config=ExecutionRealismConfig(brokerage_flat_rs=20.0))
        self.assertAlmostEqual(r_no_fee["cash"] - r_fee["cash"], 20.0, places=2)


if __name__ == "__main__":
    unittest.main()
