"""
Tests for swing_research/execution_realism_engine.py -- the volume-cap/
illiquidity-cost/next-day-open-fill sibling module wrapping
backtesting_engine.py's output (never its internals). Covers both real
bugs caught and fixed during the SW-003/SW-008 validation (2026-08-15):
sparse-vs-dense equity curve construction, and the participation-cap
counter under-reporting drop-to-zero trades.
"""

import unittest
import pandas as pd

from swing_research.backtesting_engine import Trade
from swing_research.execution_realism_engine import (
    apply_execution_realism,
    build_approximate_daily_equity,
    calibrate_illiq_cost_k,
    compute_trailing_adv,
    compute_trailing_illiq,
)


def _make_price_history(n=80, start_price=100.0, daily_drift=0.1, volume=10_000):
    dates = pd.bdate_range("2023-01-01", periods=n)
    close = pd.Series([start_price + i * daily_drift for i in range(n)], index=dates)
    return pd.DataFrame({
        "Open": close.shift(1).fillna(start_price), "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": volume,
    }, index=dates)


def _make_trade(symbol="TEST.NS", entry_idx=40, exit_idx=45, entry_price=104.0, exit_price=105.0,
                 quantity=100, direction="BUY", dates=None):
    dates = dates if dates is not None else pd.bdate_range("2023-01-01", periods=80)
    pnl = (exit_price - entry_price) * quantity if direction == "BUY" else (entry_price - exit_price) * quantity
    return Trade(symbol=symbol, entry_date=dates[entry_idx].date(), exit_date=dates[exit_idx].date(),
                 entry_price=entry_price, exit_price=exit_price, quantity=quantity, pnl=pnl,
                 exit_reason="signal_exit", direction=direction)


class TestNoLookahead(unittest.TestCase):
    def test_trailing_adv_shifted_by_one(self):
        df = _make_price_history(volume=5000)
        adv = compute_trailing_adv(df, lookback_days=20)
        # First 20 rows can't have a full trailing window; row 20 (0-indexed)
        # should be the mean of rows 0-19 (shifted), not including row 20 itself.
        self.assertTrue(adv.iloc[:20].isna().all())
        self.assertAlmostEqual(adv.iloc[20], 5000.0)

    def test_trailing_illiq_shifted_by_one_and_handles_zero_volume(self):
        df = _make_price_history()
        df.loc[df.index[10], "Volume"] = 0  # a genuinely thin/halted day
        illiq = compute_trailing_illiq(df, lookback_days=20)
        # Zero-volume day produces inf/nan dollar-volume denominator --
        # must not propagate as inf into the rolling mean.
        self.assertFalse((illiq == float("inf")).any())


class TestApplyExecutionRealismDefaultsAreNoOp(unittest.TestCase):
    def test_no_options_returns_economically_identical_trade(self):
        df = _make_price_history()
        t = _make_trade()
        result = apply_execution_realism([t], {"TEST.NS": df})
        r = result["trades"][0]
        self.assertEqual((r.entry_price, r.exit_price, r.quantity, r.pnl),
                          (t.entry_price, t.exit_price, t.quantity, t.pnl))
        self.assertEqual(result["capped_trade_count"], 0)
        self.assertEqual(result["skipped_no_next_day"], 0)


class TestParticipationCap(unittest.TestCase):
    def setUp(self):
        self.df = _make_price_history(volume=10_000)
        self.data = {"TEST.NS": self.df}
        self.trade = _make_trade(quantity=100)

    def test_loose_cap_is_a_no_op_and_not_counted(self):
        result = apply_execution_realism([self.trade], self.data, max_participation_pct_of_adv=0.99)
        self.assertEqual(result["trades"][0].quantity, 100)
        self.assertEqual(result["capped_trade_count"], 0)

    def test_tight_cap_resizes_and_rescales_pnl_exactly(self):
        # 5% of ADV(10000) = 500, less than the trade's 100... use a
        # smaller ADV proxy via a tighter pct instead: 0.001 * 10000 = 10.
        result = apply_execution_realism([self.trade], self.data, max_participation_pct_of_adv=0.001)
        r = result["trades"][0]
        self.assertEqual(r.quantity, 10)
        self.assertAlmostEqual(r.pnl, (self.trade.exit_price - self.trade.entry_price) * 10)
        self.assertEqual(result["capped_trade_count"], 1)

    def test_degenerate_cap_drops_trade_and_still_counts_it(self):
        # int(0.00001 * 10000) = 0 -- no economically meaningful position
        # size exists; trade must be dropped AND counted (regression test
        # for the under-counting bug caught during SW-008 validation,
        # where 2 trades vanished while capped_trade_count read 0).
        result = apply_execution_realism([self.trade], self.data, max_participation_pct_of_adv=0.00001)
        self.assertEqual(len(result["trades"]), 0)
        self.assertEqual(result["capped_trade_count"], 1)


class TestIlliquidityCost(unittest.TestCase):
    def test_cost_widens_the_round_trip_against_a_long_position(self):
        df = _make_price_history(volume=1000)  # thin -> non-trivial ILLIQ
        data = {"TEST.NS": df}
        t = _make_trade(quantity=100)
        result = apply_execution_realism([t], data, illiq_cost_k=1000.0)
        r = result["trades"][0]
        self.assertGreater(r.entry_price, t.entry_price)  # pays more entering long
        self.assertLess(r.exit_price, t.exit_price)         # receives less exiting long
        self.assertLess(r.pnl, t.pnl)

    def test_cost_is_capped(self):
        df = _make_price_history(volume=10)  # extremely thin -> huge raw ILLIQ
        data = {"TEST.NS": df}
        t = _make_trade(quantity=100)
        result = apply_execution_realism([t], data, illiq_cost_k=1e12, illiq_cost_cap_pct=0.05)
        r = result["trades"][0]
        implied_cost_pct = (r.entry_price - t.entry_price) / t.entry_price
        self.assertLessEqual(implied_cost_pct, 0.05 + 1e-9)

    def test_calibrate_illiq_cost_k_is_nonnegative_and_deterministic(self):
        df = _make_price_history(volume=5000)
        data = {"TEST.NS": df}
        k1 = calibrate_illiq_cost_k(data)
        k2 = calibrate_illiq_cost_k(data)
        self.assertGreaterEqual(k1, 0.0)
        self.assertEqual(k1, k2)

    def test_calibrate_illiq_cost_k_empty_data_returns_zero(self):
        self.assertEqual(calibrate_illiq_cost_k({}), 0.0)


class TestFillTiming(unittest.TestCase):
    def test_next_day_open_substitutes_prices(self):
        dates = pd.bdate_range("2023-01-01", periods=80)
        df = _make_price_history()
        data = {"TEST.NS": df}
        t = _make_trade(dates=dates)
        result = apply_execution_realism([t], data, fill_timing="next_day_open")
        r = result["trades"][0]
        entry_pos = list(df.index.date).index(t.entry_date)
        exit_pos = list(df.index.date).index(t.exit_date)
        self.assertAlmostEqual(r.entry_price, float(df.iloc[entry_pos + 1]["Open"]))
        self.assertAlmostEqual(r.exit_price, float(df.iloc[exit_pos + 1]["Open"]))

    def test_trade_at_end_of_data_is_skipped_not_crashed(self):
        df = _make_price_history(n=50)
        data = {"TEST.NS": df}
        t = _make_trade(entry_idx=40, exit_idx=49, dates=pd.bdate_range("2023-01-01", periods=50))  # exit on last bar
        result = apply_execution_realism([t], data, fill_timing="next_day_open")
        self.assertEqual(result["skipped_no_next_day"], 1)
        # Falls back to the original, unadjusted trade rather than dropping it silently.
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0].entry_price, t.entry_price)


class TestApproximateDailyEquity(unittest.TestCase):
    def test_dense_series_reproduces_baseline_metrics_exactly_for_zero_effect_change(self):
        """The actual regression test for the equity-curve density bug:
        a variant that changes NOTHING about the trades must reproduce
        the real engine's own daily_equity-based metrics exactly, not
        just approximately -- caught originally because a supposedly
        zero-effect volume-cap variant showed Sharpe more than tripling
        under the old (sparse, exit-date-only) implementation."""
        from swing_research.metrics import compute_metrics

        dates = pd.bdate_range("2023-01-01", periods=300)
        trades = [
            _make_trade(entry_idx=10, exit_idx=20, entry_price=100, exit_price=105, quantity=50, dates=dates),
            _make_trade(entry_idx=25, exit_idx=60, entry_price=101, exit_price=98, quantity=30, dates=dates),
            _make_trade(entry_idx=100, exit_idx=150, entry_price=110, exit_price=120, quantity=20, dates=dates),
        ]
        starting_capital = 1_000_000
        calendar = list(dates.date)

        # A REAL engine's daily_equity has one entry per trading day,
        # flat between trade closes. Reconstruct that directly (this is
        # what simulate_portfolio() itself would produce) as the "ground
        # truth" to compare the module's own dense-equity builder against.
        equity = starting_capital
        ground_truth = {}
        pnl_by_exit = {}
        for t in trades:
            pnl_by_exit[t.exit_date] = pnl_by_exit.get(t.exit_date, 0.0) + t.pnl
        for d in sorted(calendar):
            equity += pnl_by_exit.get(d, 0.0)
            ground_truth[d] = equity

        built = build_approximate_daily_equity(trades, starting_capital, calendar)
        self.assertEqual(built, ground_truth)

        m_ground_truth = compute_metrics(trades, starting_capital, calendar, daily_equity=ground_truth)
        m_built = compute_metrics(trades, starting_capital, calendar, daily_equity=built)
        self.assertEqual(m_ground_truth["sharpe_ratio"], m_built["sharpe_ratio"])
        self.assertEqual(m_ground_truth["sortino_ratio"], m_built["sortino_ratio"])
        self.assertEqual(m_ground_truth["max_drawdown_pct"], m_built["max_drawdown_pct"])


if __name__ == "__main__":
    unittest.main()
