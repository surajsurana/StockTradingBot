"""
Unit tests for swing_research/strategies/crypto_long_horizon_reversal.py
and its cross-sectional plumbing in swing_research/cross_sectional.py: the
60-calendar-day formation return, monthly month-end-only decisions,
bottom-decile (past LOSERS) entry, the 20% stop, and the 55-calendar-day
minimum that always resolves to the SECOND month-end after entry -- the
same conventions already exercised for crypto_illiquidity_premium.py /
crypto_idiosyncratic_volatility.py, just with the reversal strategy's own
bottom-decile polarity and longer (two-month-end) holding period.
Offline, hand-built fixtures, no network calls.

    python test_crypto_long_horizon_reversal.py
"""

import unittest
from datetime import date, timedelta

import pandas as pd

from swing_research.backtesting_engine import simulate_portfolio
from swing_research.base import OpenPosition, PositionUnit
from swing_research.cross_sectional import (
    CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS, compute_crypto_long_horizon_reversal_percentile_ranks,
    compute_crypto_long_horizon_reversal_score,
)
from swing_research.strategies.crypto_long_horizon_reversal import (
    BOTTOM_DECILE_PERCENTILE_THRESHOLD, HOLDING_PERIOD_DAYS, STOP_LOSS_PCT, CryptoLongHorizonReversalStrategy,
)


def _daily(closes, start=date(2026, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


class TestCryptoReversalRanksEndToEnd(unittest.TestCase):
    def test_a_past_loser_ranks_lowest_against_winners(self):
        n = CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS + 5
        start = date(2025, 1, 1)
        loser = [100.0 * (0.995 ** i) for i in range(n)]    # steadily down over the formation window
        winner = [100.0 * (1.005 ** i) for i in range(n)]   # steadily up over the formation window
        flat = [100.0] * n
        data = {"LOSER": _daily(loser, start), "WINNER": _daily(winner, start), "FLAT": _daily(flat, start)}

        ranks = compute_crypto_long_horizon_reversal_percentile_ranks(data)
        last_date = data["LOSER"].index[-1]
        self.assertLess(ranks["LOSER"].loc[last_date], ranks["FLAT"].loc[last_date])
        self.assertLess(ranks["FLAT"].loc[last_date], ranks["WINNER"].loc[last_date])

    def test_needs_a_full_formation_window(self):
        n = CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS - 5
        data = {"A": _daily([100.0] * n, date(2025, 1, 1))}
        ranks = compute_crypto_long_horizon_reversal_percentile_ranks(data)
        self.assertTrue(ranks["A"].isna().all())

    def test_score_is_the_plain_trailing_return_no_lookahead(self):
        closes = [100.0] * CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS + [120.0]
        df = _daily(closes, date(2025, 1, 1))
        score = compute_crypto_long_horizon_reversal_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.20)
        self.assertTrue(score.iloc[:CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS].isna().all())


class TestStrategyPrecomputeAndEntry(unittest.TestCase):
    def _frame(self, start, closes, percentiles):
        df = _daily(closes, start=start)
        df["crypto_reversal_percentile"] = percentiles
        return CryptoLongHorizonReversalStrategy().precompute(df)

    def test_enters_only_at_month_end_in_the_bottom_decile(self):
        strategy = CryptoLongHorizonReversalStrategy()
        start, n = date(2025, 1, 25), 10   # crosses the 31 Jan month-end
        df = self._frame(start, [100.0] * n, [5.0] * n)
        rows = list(df.itertuples())
        self.assertTrue(any(r.is_month_end for r in rows))
        for r in rows:
            sig = strategy.entry_signal_at(r)
            self.assertIsNotNone(sig) if r.is_month_end else self.assertIsNone(sig)
        high = self._frame(start, [100.0] * n, [10.1] * n)
        self.assertTrue(all(strategy.entry_signal_at(r) is None for r in high.itertuples() if r.is_month_end))

    def test_missing_percentile_column_defaults_to_disqualified(self):
        strategy = CryptoLongHorizonReversalStrategy()
        df = _daily([100.0] * 10, start=date(2025, 1, 25))   # no crypto_reversal_percentile column at all
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))

    def test_stop_is_20_pct_below_entry_and_confidence_is_percentile(self):
        strategy = CryptoLongHorizonReversalStrategy()
        df = self._frame(date(2025, 1, 31), [200.0], [3.0])
        sig = strategy.entry_signal_at(list(df.itertuples())[0])
        self.assertAlmostEqual(sig.stop_loss, 200.0 * (1 - STOP_LOSS_PCT))
        self.assertAlmostEqual(sig.confidence, 3.0)
        self.assertEqual(sig.direction, "BUY")

    def test_exactly_at_threshold_qualifies(self):
        strategy = CryptoLongHorizonReversalStrategy()
        df = self._frame(date(2025, 1, 31), [100.0], [BOTTOM_DECILE_PERCENTILE_THRESHOLD])
        self.assertTrue(bool(df["qualifies"].iloc[-1]))


class TestExitSignal(unittest.TestCase):
    def _frame(self, start, closes, percentiles):
        df = _daily(closes, start=start)
        df["crypto_reversal_percentile"] = percentiles
        return CryptoLongHorizonReversalStrategy().precompute(df)

    def test_exits_at_the_second_month_end_after_the_holding_period(self):
        strategy = CryptoLongHorizonReversalStrategy()
        start, n = date(2025, 1, 1), 95   # through early April: Jan 31, Feb 28, Mar 31 month-ends
        df = self._frame(start, [100.0] * n, [50.0] * n)   # never qualifies, only exit logic is exercised
        rows = list(df.itertuples())
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=100.0, entry_date=date(2025, 1, 31), quantity=1.0)])
        jan_end = next(r for r in rows if r.is_month_end and r.date.month == 1)
        feb_end = next(r for r in rows if r.is_month_end and r.date.month == 2)
        mar_end = next(r for r in rows if r.is_month_end and r.date.month == 3)
        # Jan 31 is entry day itself; Feb 28 is only 28 days later, below the 55-day minimum.
        self.assertIsNone(strategy.exit_signal_at(feb_end, pos))
        # Mar 31 is 59 days after entry -- the second month-end, >= the 55-day minimum.
        self.assertEqual(strategy.exit_signal_at(mar_end, pos), 100.0)
        self.assertIsNone(strategy.exit_signal_at(jan_end, pos))

    def test_no_percentile_based_early_exit_exists(self):
        strategy = CryptoLongHorizonReversalStrategy()
        start, n = date(2025, 1, 1), 40
        df = self._frame(start, [100.0] * n, [1.0] * n)   # firmly in the bottom decile throughout
        rows = list(df.itertuples())
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=100.0, entry_date=date(2025, 1, 31), quantity=1.0)])
        jan_end = next(r for r in rows if r.is_month_end and r.date.month == 1)
        # Even at the very next month-end, a still-low percentile must not trigger an exit --
        # only the holding-period/month-end rule governs exits.
        self.assertIsNone(strategy.exit_signal_at(jan_end, pos))


class TestEndToEndBacktest(unittest.TestCase):
    def test_fractional_monthly_positions_only_for_the_bottom_decile_coin(self):
        start, n = date(2025, 1, 1), 95   # three month-ends: Jan 31, Feb 28, Mar 31
        data = {"A": _daily([100.0 + 0.5 * i for i in range(n)], start=start),
                "B": _daily([3_000.0 + 10 * i for i in range(n)], start=start)}
        idx = data["A"].index
        extra = {"A": pd.Series([5.0] * n, index=idx, name="crypto_reversal_percentile"),
                 "B": pd.Series([90.0] * n, index=idx, name="crypto_reversal_percentile")}
        result = simulate_portfolio(data, CryptoLongHorizonReversalStrategy(), 1_000.0, sector_map={},
                                    extra_columns_by_symbol=extra)
        trades = result["trades"]
        self.assertTrue(trades, "a 1,000 USDT book must be able to buy a fraction of the bottom-decile coin")
        self.assertTrue(all(t.symbol == "A" for t in trades))   # B never qualifies (percentile 90)
        self.assertTrue(all(0 < t.quantity for t in trades))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_approved_plan(self):
        self.assertEqual(CRYPTO_LONG_HORIZON_REVERSAL_FORMATION_DAYS, 60)
        self.assertAlmostEqual(BOTTOM_DECILE_PERCENTILE_THRESHOLD, 10.0)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.20)
        self.assertEqual(HOLDING_PERIOD_DAYS, 55)
        self.assertEqual(CryptoLongHorizonReversalStrategy.max_units, 1)
        self.assertAlmostEqual(CryptoLongHorizonReversalStrategy.risk_pct_per_unit, 0.025)
        self.assertTrue(CryptoLongHorizonReversalStrategy.fractional_quantities)

    def test_threshold_is_a_bottom_decile_the_reversal_polarity(self):
        # Past LOSERS over the formation window -- the opposite polarity from every momentum
        # strategy's top-decile (>=90) threshold in this lane.
        self.assertLess(BOTTOM_DECILE_PERCENTILE_THRESHOLD, 50.0)


if __name__ == "__main__":
    unittest.main()
