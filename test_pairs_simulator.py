"""
Unit tests for research_lab/pairs_simulator.py -- the pairs engine. Small
synthetic two-leg data, hand-calculated expected trades and P&L, no real
data or API calls. Run with:

    python test_pairs_simulator.py
"""

import statistics
import unittest
from datetime import date

import pandas as pd

from research_lab.backtesting_engineer import compute_metrics
from research_lab.pairs_base import PairSignal, PairStrategy
from research_lab.pairs_simulator import (
    _check_spread_exit, _compute_pair_day_context, _daily_close_correlation, _pooled_ratio_baseline,
    _size_pair_legs, simulate_pair, simulate_pairs,
)
from research_lab.risk_manager_research import RiskParameters
from research_lab.statistical_auditor import audit


def _bars(date_str, closes, times=None):
    """Flat bars (Open=High=Low=Close) -- only Close matters to the pairs
    engine. `times` overrides the default 5-min grid from 09:15, so a test
    can leave a bar out of one leg."""
    if times is None:
        idx = pd.date_range(f"{date_str} 09:15", periods=len(closes), freq="5min")
    else:
        idx = pd.DatetimeIndex([pd.Timestamp(f"{date_str} {t}") for t in times])
    rows = [{"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1000} for c in closes]
    return pd.DataFrame(rows, index=idx)


def _multiday(closes_by_date):
    return pd.concat([_bars(d, closes) for d, closes in closes_by_date.items()])


BASELINE_DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-05", periods=20)]
TEST_DAY = "2026-02-03"   # the business day after the 20 baseline days


def _baseline_pair():
    """20 prior days where A oscillates 200/210 and B sits at 100 -> ratio
    pooled over 80 bars is half 2.0, half 2.1: mean 2.05, sample std as
    statistics.stdev() computes it."""
    a = {d: [200, 210, 200, 210] for d in BASELINE_DATES}
    b = {d: [100, 100, 100, 100] for d in BASELINE_DATES}
    return a, b


BASELINE_MEAN = 2.05
BASELINE_STD = statistics.stdev([2.0, 2.1] * 40)


def _with_test_day(a_closes, b_closes, a_times=None, b_times=None):
    a, b = _baseline_pair()
    df_a = pd.concat([_multiday(a), _bars(TEST_DAY, a_closes, a_times)])
    df_b = pd.concat([_multiday(b), _bars(TEST_DAY, b_closes, b_times)])
    return df_a, df_b


def _daily(start, closes):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                         "Volume": [1000] * len(closes)}, index=idx)


def _geometric(n, first_return, alternate=False, start=100.0):
    closes = [start]
    for i in range(n - 1):
        r = first_return if (not alternate or i % 2 == 0) else -first_return
        closes.append(closes[-1] * (1 + r))
    return closes


class _FiresWhenAbsZAbove(PairStrategy):
    """Engine test double: same z-score arithmetic as the real strategy
    but WITHOUT the correlation gate, so the engine's own mechanics can be
    exercised with no daily data at all."""
    name = "fires_when_abs_z_above"

    def __init__(self, threshold=2.0, stop=3.5, target=0.5):
        self.threshold, self.stop, self.target = threshold, stop, target

    def generate_pair_signal(self, bars_a_so_far, bars_b_so_far, spread_context=None):
        mean, std = spread_context.get("baseline_mean"), spread_context.get("baseline_std")
        if mean is None or std is None:
            return None
        z = (float(bars_a_so_far.iloc[-1]["Close"]) / float(bars_b_so_far.iloc[-1]["Close"]) - mean) / std
        if abs(z) < self.threshold:
            return None
        return PairSignal(long_leg="b" if z > 0 else "a", entry_zscore=z, stop_zscore=self.stop,
                          target_zscore=self.target, confidence=0.5, strategy_name=self.name)


class TestDailyCloseCorrelation(unittest.TestCase):
    def test_identical_returns_give_correlation_near_one(self):
        a = _daily("2026-01-01", _geometric(65, 0.01, alternate=True))
        b = _daily("2026-01-01", _geometric(65, 0.01, alternate=True, start=250.0))
        corr = _daily_close_correlation(a, b, date(2026, 6, 1))
        self.assertGreater(corr, 0.99)

    def test_opposite_returns_give_correlation_below_threshold(self):
        a = _daily("2026-01-01", _geometric(65, 0.01, alternate=True))
        b_closes = _geometric(65, -0.01, alternate=True, start=250.0)
        b = _daily("2026-01-01", b_closes)
        corr = _daily_close_correlation(a, b, date(2026, 6, 1))
        self.assertLess(corr, 0.8)

    def test_none_when_too_few_prior_rows(self):
        a = _daily("2026-01-01", _geometric(30, 0.01, alternate=True))
        b = _daily("2026-01-01", _geometric(30, 0.01, alternate=True))
        self.assertIsNone(_daily_close_correlation(a, b, date(2026, 6, 1)))

    def test_none_when_a_series_is_missing(self):
        a = _daily("2026-01-01", _geometric(65, 0.01, alternate=True))
        self.assertIsNone(_daily_close_correlation(a, None, date(2026, 6, 1)))
        self.assertIsNone(_daily_close_correlation(None, a, date(2026, 6, 1)))

    def test_rows_on_or_after_as_of_are_never_used(self):
        # 65 identical-return rows, then 10 OPPOSITE-return rows dated on/after as_of.
        prior_a = _geometric(65, 0.01, alternate=True)
        prior_b = _geometric(65, 0.01, alternate=True, start=250.0)
        later_a = _geometric(11, 0.01, alternate=True, start=prior_a[-1])[1:]
        later_b = _geometric(11, -0.01, alternate=True, start=prior_b[-1])[1:]
        a = _daily("2026-01-01", prior_a + later_a)
        b = _daily("2026-01-01", prior_b + later_b)
        as_of = a.index[65].date()
        corr = _daily_close_correlation(a, b, as_of)
        self.assertGreater(corr, 0.99)


class TestPooledRatioBaseline(unittest.TestCase):
    def test_hand_calculated_mean_and_std(self):
        df_a, df_b = _with_test_day([200, 200, 200, 200], [100, 100, 100, 100])
        mean, std = _pooled_ratio_baseline(df_a, df_b, date(2026, 2, 3), lookback_days=20)
        self.assertAlmostEqual(mean, BASELINE_MEAN)
        self.assertAlmostEqual(std, BASELINE_STD)

    def test_todays_own_bars_are_excluded(self):
        # A ratio of 5.0 all day today must not move the baseline at all.
        df_a, df_b = _with_test_day([500, 500, 500, 500], [100, 100, 100, 100])
        mean, std = _pooled_ratio_baseline(df_a, df_b, date(2026, 2, 3), lookback_days=20)
        self.assertAlmostEqual(mean, BASELINE_MEAN)
        self.assertAlmostEqual(std, BASELINE_STD)

    def test_none_until_full_lookback_of_prior_days(self):
        a = {d: [200, 210, 200, 210] for d in BASELINE_DATES[:5]}
        b = {d: [100, 100, 100, 100] for d in BASELINE_DATES[:5]}
        mean, std = _pooled_ratio_baseline(_multiday(a), _multiday(b), date(2026, 2, 3), lookback_days=20)
        self.assertIsNone(mean)
        self.assertIsNone(std)

    def test_std_is_none_when_ratio_is_constant(self):
        a = {d: [200, 200, 200, 200] for d in BASELINE_DATES}
        b = {d: [100, 100, 100, 100] for d in BASELINE_DATES}
        mean, std = _pooled_ratio_baseline(_multiday(a), _multiday(b), date(2026, 2, 3), lookback_days=20)
        self.assertAlmostEqual(mean, 2.0)
        self.assertIsNone(std)


class TestComputePairDayContext(unittest.TestCase):
    def test_correlation_ok_fails_closed_without_daily_data(self):
        df_a, df_b = _with_test_day([200] * 4, [100] * 4)
        ctx = _compute_pair_day_context(df_a, df_b, "A", "B", date(2026, 2, 3))
        self.assertEqual(ctx["symbol_a"], "A")
        self.assertEqual(ctx["symbol_b"], "B")
        self.assertAlmostEqual(ctx["baseline_mean"], BASELINE_MEAN)
        self.assertIsNone(ctx["correlation"])
        self.assertFalse(ctx["correlation_ok"])

    def test_correlation_ok_true_above_threshold(self):
        df_a, df_b = _with_test_day([200] * 4, [100] * 4)
        daily_a = _daily("2025-10-01", _geometric(70, 0.01, alternate=True))
        daily_b = _daily("2025-10-01", _geometric(70, 0.01, alternate=True, start=50.0))
        ctx = _compute_pair_day_context(df_a, df_b, "A", "B", date(2026, 2, 3), daily_a=daily_a, daily_b=daily_b)
        self.assertGreater(ctx["correlation"], 0.99)
        self.assertTrue(ctx["correlation_ok"])

    def test_correlation_ok_false_below_threshold(self):
        df_a, df_b = _with_test_day([200] * 4, [100] * 4)
        daily_a = _daily("2025-10-01", _geometric(70, 0.01, alternate=True))
        daily_b = _daily("2025-10-01", _geometric(70, -0.01, alternate=True, start=50.0))
        ctx = _compute_pair_day_context(df_a, df_b, "A", "B", date(2026, 2, 3), daily_a=daily_a, daily_b=daily_b)
        self.assertFalse(ctx["correlation_ok"])


class TestCheckSpreadExit(unittest.TestCase):
    def test_stop_when_spread_widens_further_on_entry_side(self):
        self.assertEqual(_check_spread_exit(3.6, 2.2, 3.5, 0.5), (True, False))
        self.assertEqual(_check_spread_exit(-3.6, -2.2, 3.5, 0.5), (True, False))

    def test_target_when_spread_reverts_toward_zero(self):
        self.assertEqual(_check_spread_exit(0.4, 2.2, 3.5, 0.5), (False, True))
        self.assertEqual(_check_spread_exit(-0.4, -2.2, 3.5, 0.5), (False, True))

    def test_swing_to_opposite_extreme_is_target_not_stop(self):
        self.assertEqual(_check_spread_exit(-3.6, 2.2, 3.5, 0.5), (False, True))
        self.assertEqual(_check_spread_exit(3.6, -2.2, 3.5, 0.5), (False, True))

    def test_neither_while_spread_is_in_between(self):
        self.assertEqual(_check_spread_exit(2.9, 2.2, 3.5, 0.5), (False, False))
        self.assertEqual(_check_spread_exit(1.0, 2.2, 3.5, 0.5), (False, False))


class TestSizePairLegs(unittest.TestCase):
    def test_equal_notional_split_rounded_down_per_leg(self):
        self.assertEqual(_size_pair_legs(100000.0, 250.0, 80.0), (200, 625))

    def test_zero_when_a_price_is_invalid(self):
        self.assertEqual(_size_pair_legs(100000.0, 0.0, 80.0), (0, 0))


class TestSimulatePair(unittest.TestCase):
    """Entry fires on the test day's second bar (A=220 -> ratio 2.20,
    z ~ +2.98 -> short A / long B). capital 100000 -> 50000 notional per
    leg -> 227 A shares, 500 B shares. B is flat at 100 all day so the long
    leg contributes 0; every expected P&L below is the A short alone."""

    def _run(self, a_closes, b_closes, risk_params=None, **kw):
        df_a, df_b = _with_test_day(a_closes, b_closes, **kw)
        return simulate_pair(df_a, df_b, "A", "B", _FiresWhenAbsZAbove(), 100000.0, risk_params=risk_params)

    def test_target_exit_with_hand_calculated_pnl(self):
        trades = self._run([200, 220, 215, 206], [100] * 4)
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t.symbol, "A/B")
        self.assertEqual(t.exit_reason, "target")
        self.assertEqual(t.direction, "SELL")       # short the ratio: short A, long B
        self.assertEqual(t.quantity, 227)
        self.assertAlmostEqual(t.entry_price, 2.20)
        self.assertAlmostEqual(t.exit_price, 2.06)
        self.assertAlmostEqual(t.pnl, (220 - 206) * 227)   # 3178
        self.assertAlmostEqual(t.entry_hour, 9 + 20 / 60)
        self.assertEqual(t.entry_date, date(2026, 2, 3))

    def test_stop_exit_when_spread_widens_further(self):
        trades = self._run([200, 220, 230, 200], [100] * 4)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "stop_loss")
        self.assertAlmostEqual(trades[0].pnl, (220 - 230) * 227)   # -2270

    def test_eod_square_off_closes_both_legs_on_the_last_common_bar(self):
        trades = self._run([200, 220, 218, 217], [100] * 4)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "eod_square_off")
        self.assertAlmostEqual(trades[0].pnl, (220 - 217) * 227)   # 681

    def test_negative_z_goes_long_a_short_b(self):
        # A=190 -> ratio 1.90, z ~ -2.98 -> long A (50000//190 = 263 shares), short B.
        trades = self._run([200, 190, 195, 204], [100] * 4)
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t.direction, "BUY")
        self.assertEqual(t.exit_reason, "target")
        self.assertEqual(t.quantity, 263)
        self.assertAlmostEqual(t.pnl, (204 - 190) * 263)   # 3682

    def test_bar_missing_on_one_leg_is_never_seen(self):
        # A's 220 print is at 09:20, a bar B does not have -> never a common
        # timestamp -> the engine never sees the divergence -> no trade.
        trades = self._run([200, 220, 200, 200, 200], [100, 100, 100, 100],
                           a_times=["09:15", "09:20", "09:25", "09:30", "09:35"],
                           b_times=["09:15", "09:25", "09:30", "09:35"])
        self.assertEqual(trades, [])

    def test_no_trade_without_a_full_baseline(self):
        a = {d: [200, 210, 200, 210] for d in BASELINE_DATES[:5]}
        b = {d: [100] * 4 for d in BASELINE_DATES[:5]}
        df_a = pd.concat([_multiday(a), _bars(TEST_DAY, [200, 300, 300, 300])])
        df_b = pd.concat([_multiday(b), _bars(TEST_DAY, [100] * 4)])
        self.assertEqual(simulate_pair(df_a, df_b, "A", "B", _FiresWhenAbsZAbove(), 100000.0), [])

    def test_one_round_trip_per_day_by_default(self):
        # entry (220) -> target (206) -> a second divergence (220) is ignored.
        trades = self._run([200, 220, 206, 220, 206], [100] * 5)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "target")

    def test_risk_params_allow_a_second_round_trip(self):
        trades = self._run([200, 220, 206, 220, 206], [100] * 5, risk_params=RiskParameters())
        self.assertEqual([t.exit_reason for t in trades], ["target", "target"])
        self.assertAlmostEqual(trades[1].pnl, (220 - 206) * 227)

    def test_partial_day_with_under_four_common_bars_is_skipped(self):
        trades = self._run([200, 220, 206], [100] * 3)
        self.assertEqual(trades, [])


class TestSimulatePairs(unittest.TestCase):
    def test_output_shape_and_downstream_compatibility(self):
        df_a, df_b = _with_test_day([200, 220, 215, 206], [100] * 4)
        data = {"A": df_a, "B": df_b, "C": df_a.copy()}
        pairs = [("A", "B"), ("A", "MISSING"), ("C", "B")]
        result = simulate_pairs(data, pairs, _FiresWhenAbsZAbove(), 100000.0)

        self.assertEqual(set(result.keys()), {"trades", "trading_calendar", "symbols", "capital_per_symbol"})
        self.assertEqual(result["symbols"], ["A/B", "C/B"])   # the pair with a missing leg is skipped
        self.assertEqual(result["capital_per_symbol"], 100000.0)
        self.assertEqual(len(result["trades"]), 2)
        self.assertIn(date(2026, 2, 3), result["trading_calendar"])

        metrics = compute_metrics(result["trades"], 100000.0 * 2, result["trading_calendar"])
        self.assertEqual(metrics["total_trades"], 2)
        verdict = audit([metrics], metrics)
        self.assertIn(verdict.decision, ("PASS", "REJECT"))


if __name__ == "__main__":
    unittest.main()
