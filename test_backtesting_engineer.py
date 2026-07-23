"""
Mock-based unit tests for research_lab/backtesting_engineer.py. Metrics
are checked against hand-calculated expected values on small synthetic
trade sequences -- no real data or API calls involved. Run with:

    python test_backtesting_engineer.py
"""

import unittest
from datetime import date

import pandas as pd

from research_lab.backtesting_engineer import (
    Trade, _compute_day_context, compute_metrics, simulate_symbol, walk_forward_split,
)
from research_lab.base import Signal, Strategy
from research_lab.risk_manager_research import RiskParameters


class TestComputeMetrics(unittest.TestCase):
    def test_win_rate_profit_factor_expectancy_hand_calculated(self):
        trades = [
            Trade("A", date(2026, 1, 5), date(2026, 1, 5), 100, 110, 100, 1000.0, "target"),
            Trade("B", date(2026, 1, 6), date(2026, 1, 6), 100, 95, 100, -500.0, "stop_loss"),
            Trade("A", date(2026, 1, 7), date(2026, 1, 7), 100, 110, 100, 1000.0, "target"),
            Trade("B", date(2026, 1, 8), date(2026, 1, 8), 100, 95, 100, -500.0, "stop_loss"),
        ]
        calendar = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)]
        m = compute_metrics(trades, starting_capital=100000, trading_calendar=calendar)

        self.assertEqual(m["win_rate"], 0.5)
        self.assertEqual(m["profit_factor"], 2.0)          # 2000 gross profit / 1000 gross loss
        self.assertEqual(m["expectancy"], 250.0)            # (1000-500+1000-500)/4
        self.assertEqual(m["total_pnl"], 1000.0)
        self.assertEqual(m["total_trades"], 4)
        self.assertEqual(m["return_on_capital_pct"], 1.0)   # 1000/100000*100

    def test_no_trades_returns_zeroed_result_not_a_crash(self):
        m = compute_metrics([], starting_capital=100000, trading_calendar=[])
        self.assertEqual(m["total_trades"], 0)
        self.assertEqual(m["win_rate"], 0.0)
        self.assertIsNone(m["profit_factor"])

    def test_all_losses_gives_zero_profit_factor_not_a_crash(self):
        trades = [Trade("A", date(2026, 1, 5), date(2026, 1, 5), 100, 95, 100, -500.0, "stop_loss")]
        m = compute_metrics(trades, 100000, [date(2026, 1, 5)])
        self.assertEqual(m["win_rate"], 0.0)
        self.assertEqual(m["profit_factor"], 0.0)  # 0 gross profit / abs(gross loss) = 0.0, not a crash

    def test_max_drawdown_hand_calculated(self):
        # Equity path: 100000 -> 101000 (peak) -> 99500 -> 100500
        # Drawdown from peak 101000 to trough 99500 = 1500/101000 = 1.485%
        trades = [
            Trade("A", date(2026, 1, 5), date(2026, 1, 5), 100, 110, 100, 1000.0, "target"),
            Trade("B", date(2026, 1, 6), date(2026, 1, 6), 100, 95, 100, -1500.0, "stop_loss"),
            Trade("C", date(2026, 1, 7), date(2026, 1, 7), 100, 110, 100, 1000.0, "target"),
        ]
        calendar = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
        m = compute_metrics(trades, 100000, calendar)
        expected_dd = (1500 / 101000) * 100
        self.assertAlmostEqual(m["max_drawdown_pct"], expected_dd, places=2)

    def test_monthly_returns_bucketed_correctly(self):
        trades = [
            Trade("A", date(2026, 1, 15), date(2026, 1, 15), 100, 110, 100, 1000.0, "target"),
            Trade("A", date(2026, 2, 15), date(2026, 2, 15), 100, 95, 100, -500.0, "stop_loss"),
        ]
        m = compute_metrics(trades, 100000, [date(2026, 1, 15), date(2026, 2, 15)])
        self.assertEqual(m["monthly_returns_pct"]["2026-01"], 1.0)
        self.assertEqual(m["monthly_returns_pct"]["2026-02"], -0.5)


class _RangeBreakoutOnceADayStrategy(Strategy):
    """Fires exactly once on the 4th bar of any day it's called on, for
    simulate_symbol()'s one-trade-per-day and risk_params tests."""
    name = "test_strategy"

    def generate_signal(self, todays_bars_so_far, context=None):
        if len(todays_bars_so_far) != 4:
            return None
        entry = float(todays_bars_so_far.iloc[-1]["Close"])
        return Signal(symbol="TEST", direction="BUY", entry_price=entry,
                       stop_loss=entry - 1, target=entry + 2, confidence=0.5,
                       strategy_name=self.name)


class TestSimulateSymbol(unittest.TestCase):
    def _synthetic_day(self, base_price=100.0, n_bars=8):
        """A day where price rises steadily -- the test strategy's stop is
        never hit, target (entry+2) gets hit a few bars after entry."""
        idx = pd.date_range("2026-01-05 09:15", periods=n_bars, freq="5min")
        prices = [base_price + i * 0.5 for i in range(n_bars)]
        return pd.DataFrame({
            "Open": prices, "High": [p + 0.3 for p in prices], "Low": [p - 0.3 for p in prices],
            "Close": prices, "Volume": [1000] * n_bars,
        }, index=idx)

    def test_default_caps_at_one_trade_per_symbol_per_day(self):
        # Regression test: the earlier ad hoc script's code had a comment
        # claiming "one trade per symbol per day" but never actually
        # enforced it -- fixed while building this component.
        df = self._synthetic_day(n_bars=12)
        trades = simulate_symbol(df, _RangeBreakoutOnceADayStrategy(), capital=100000,
                                  risk_per_trade_pct=0.01)
        trade_dates = [t.entry_date for t in trades]
        self.assertEqual(len(trade_dates), len(set(trade_dates)),
                          "more than one trade was taken for the same symbol on the same day")

    def test_mandatory_eod_square_off_when_neither_stop_nor_target_hit(self):
        idx = pd.date_range("2026-01-05 09:15", periods=6, freq="5min")
        flat_prices = [100.0] * 6  # never moves -- stop/target never reached
        df = pd.DataFrame({
            "Open": flat_prices, "High": flat_prices, "Low": flat_prices,
            "Close": flat_prices, "Volume": [1000] * 6,
        }, index=idx)
        trades = simulate_symbol(df, _RangeBreakoutOnceADayStrategy(), capital=100000,
                                  risk_per_trade_pct=0.01)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "eod_square_off")

    def test_risk_params_allows_multiple_trades_per_day_up_to_limit(self):
        class _AlwaysFiresStrategy(Strategy):
            name = "always_fires"

            def generate_signal(self, todays_bars_so_far, context=None):
                if len(todays_bars_so_far) < 2:
                    return None
                entry = float(todays_bars_so_far.iloc[-1]["Close"])
                return Signal(symbol="TEST", direction="BUY", entry_price=entry,
                               stop_loss=entry - 0.1, target=entry + 0.05, confidence=0.5,
                               strategy_name=self.name)

        df = self._synthetic_day(n_bars=10)
        params = RiskParameters(max_trades_per_day=2, daily_loss_limit_pct=1.0)  # loss limit effectively off
        trades = simulate_symbol(df, _AlwaysFiresStrategy(), capital=100000,
                                  risk_per_trade_pct=0.01, risk_params=params)
        same_day_trades = [t for t in trades if t.entry_date == date(2026, 1, 5)]
        self.assertLessEqual(len(same_day_trades), 2)

    def test_entry_hour_recorded_on_trade(self):
        df = self._synthetic_day(n_bars=8)
        trades = simulate_symbol(df, _RangeBreakoutOnceADayStrategy(), capital=100000,
                                  risk_per_trade_pct=0.01)
        self.assertEqual(len(trades), 1)
        self.assertIsNotNone(trades[0].entry_hour)
        self.assertAlmostEqual(trades[0].entry_hour, 9 + 30 / 60, places=2)  # 4th bar = 09:30

    def test_symbol_parameter_recorded_on_trade_not_unknown(self):
        # Regression test: strategies following this project's convention
        # (leave Signal.symbol="", let the caller fill it in) meant every
        # trade used to be recorded as "UNKNOWN" regardless of which real
        # symbol was actually traded -- silently breaking any per-symbol/
        # sector analysis. Fixed by having simulate_symbol() trust its own
        # `symbol` parameter (the caller always knows it) instead of
        # reading (an intentionally blank) Signal.symbol.
        df = self._synthetic_day(n_bars=8)
        trades = simulate_symbol(df, _RangeBreakoutOnceADayStrategy(), capital=100000,
                                  risk_per_trade_pct=0.01, symbol="RELIANCE")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].symbol, "RELIANCE")

    def test_symbol_defaults_to_unknown_if_not_given(self):
        df = self._synthetic_day(n_bars=8)
        trades = simulate_symbol(df, _RangeBreakoutOnceADayStrategy(), capital=100000,
                                  risk_per_trade_pct=0.01)
        self.assertEqual(trades[0].symbol, "UNKNOWN")


class TestComputeDayContext(unittest.TestCase):
    def _multi_day_df(self, n_days=25, first_15min_volume_per_bar=500, last_day_close=100.0):
        dfs = []
        for day in range(1, n_days + 1):
            idx = pd.date_range(f"2026-01-{day:02d} 09:15", periods=10, freq="5min")
            dfs.append(pd.DataFrame({
                "Open": last_day_close, "High": last_day_close + 0.2, "Low": last_day_close - 0.2,
                "Close": last_day_close, "Volume": first_15min_volume_per_bar,
            }, index=idx))
        return pd.concat(dfs)

    def test_prior_close_is_previous_days_last_close(self):
        df = self._multi_day_df(n_days=10, last_day_close=123.0)
        ctx = _compute_day_context(df, date(2026, 1, 11))
        self.assertEqual(ctx["prior_close"], 123.0)

    def test_avg_first_15min_volume_hand_calculated(self):
        # 3 bars/15min at 500 volume each = 1500 per day, constant across all days
        df = self._multi_day_df(n_days=25, first_15min_volume_per_bar=500)
        ctx = _compute_day_context(df, date(2026, 1, 26))
        self.assertEqual(ctx["avg_first_15min_volume_20d"], 1500.0)

    def test_no_prior_data_returns_none_prior_close(self):
        df = self._multi_day_df(n_days=5)
        ctx = _compute_day_context(df, date(2026, 1, 1))  # before any data exists
        self.assertIsNone(ctx["prior_close"])
        self.assertIsNone(ctx["avg_first_15min_volume_20d"])

    def test_fewer_than_5_prior_days_gives_none_avg_volume_but_real_prior_close(self):
        df = self._multi_day_df(n_days=3)
        ctx = _compute_day_context(df, date(2026, 1, 4))
        self.assertIsNotNone(ctx["prior_close"])
        self.assertIsNone(ctx["avg_first_15min_volume_20d"])

    def test_only_uses_data_strictly_before_trade_date_no_lookahead(self):
        df = self._multi_day_df(n_days=10, last_day_close=100.0)
        # Manually make day 11 (the day itself) have a very different close --
        # context computed for day 11 must NOT see day 11's own data.
        future_idx = pd.date_range("2026-01-11 09:15", periods=10, freq="5min")
        future_day = pd.DataFrame({
            "Open": 999.0, "High": 999.0, "Low": 999.0, "Close": 999.0, "Volume": 999,
        }, index=future_idx)
        full_df = pd.concat([df, future_day])
        ctx = _compute_day_context(full_df, date(2026, 1, 11))
        self.assertEqual(ctx["prior_close"], 100.0)  # not 999.0


class TestWalkForwardSplit(unittest.TestCase):
    def test_splits_into_requested_number_of_windows(self):
        windows = walk_forward_split(date(2026, 1, 1), date(2026, 4, 1), 3)
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0][0], date(2026, 1, 1))
        self.assertEqual(windows[-1][1], date(2026, 4, 1))

    def test_windows_are_contiguous(self):
        windows = walk_forward_split(date(2026, 1, 1), date(2026, 4, 1), 4)
        for i in range(len(windows) - 1):
            self.assertEqual(windows[i][1], windows[i + 1][0])


if __name__ == "__main__":
    unittest.main()
