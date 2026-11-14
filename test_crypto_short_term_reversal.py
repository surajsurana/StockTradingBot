"""
Unit tests for swing_research/strategies/crypto_short_term_reversal.py and
its cross-sectional plumbing in swing_research/cross_sectional.py: the
30-calendar-day formation return, the DAILY state-transition (single-
vintage, not month-end-gated) entry trigger, the 30-calendar-day time-stop
exit, the 20% crypto-scaled stop, and no percentile-based early exit --
mirroring test_short_term_reversal.py's (India) state-transition pattern
and test_crypto_long_horizon_reversal.py's cross-sectional ranking pattern.
Offline, hand-built fixtures, no network calls.

    python test_crypto_short_term_reversal.py
"""

import unittest
from datetime import date, timedelta

import pandas as pd

from swing_research.backtesting_engine import simulate_portfolio
from swing_research.base import OpenPosition, PositionUnit
from swing_research.cross_sectional import (
    CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS, compute_crypto_short_term_reversal_percentile_ranks,
    compute_crypto_short_term_reversal_score,
)
from swing_research.strategies.crypto_short_term_reversal import (
    HOLDING_PERIOD_DAYS, REVERSAL_PERCENTILE_THRESHOLD, STOP_LOSS_PCT, CryptoShortTermReversalStrategy,
)


def _daily(closes, start=date(2026, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


class TestCryptoShortReversalRanksEndToEnd(unittest.TestCase):
    def test_a_past_loser_ranks_lowest_against_winners(self):
        n = CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS + 5
        start = date(2025, 1, 1)
        loser = [100.0 * (0.995 ** i) for i in range(n)]    # steadily down over the formation window
        winner = [100.0 * (1.005 ** i) for i in range(n)]   # steadily up over the formation window
        flat = [100.0] * n
        data = {"LOSER": _daily(loser, start), "WINNER": _daily(winner, start), "FLAT": _daily(flat, start)}

        ranks = compute_crypto_short_term_reversal_percentile_ranks(data)
        last_date = data["LOSER"].index[-1]
        self.assertLess(ranks["LOSER"].loc[last_date], ranks["FLAT"].loc[last_date])
        self.assertLess(ranks["FLAT"].loc[last_date], ranks["WINNER"].loc[last_date])

    def test_needs_a_full_formation_window(self):
        n = CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS - 5
        data = {"A": _daily([100.0] * n, date(2025, 1, 1))}
        ranks = compute_crypto_short_term_reversal_percentile_ranks(data)
        self.assertTrue(ranks["A"].isna().all())

    def test_score_is_the_plain_trailing_return_no_lookahead(self):
        closes = [100.0] * CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS + [120.0]
        df = _daily(closes, date(2025, 1, 1))
        score = compute_crypto_short_term_reversal_score(df)
        self.assertAlmostEqual(score.iloc[-1], 0.20)
        self.assertTrue(score.iloc[:CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS].isna().all())


class TestPrecompute(unittest.TestCase):
    def _frame(self, closes, percentile, start=date(2025, 1, 1)):
        df = _daily(closes, start=start)
        df["crypto_short_reversal_percentile"] = percentile
        return CryptoShortTermReversalStrategy().precompute(df)

    def test_qualifies_when_percentile_at_or_below_threshold(self):
        precomputed = self._frame([100.0] * 40, 5.0)
        self.assertTrue(bool(precomputed["qualifies"].iloc[-1]))

    def test_does_not_qualify_above_threshold(self):
        precomputed = self._frame([100.0] * 40, 10.1)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))

    def test_missing_percentile_column_defaults_to_disqualified(self):
        df = _daily([100.0] * 40, start=date(2025, 1, 1))  # no crypto_short_reversal_percentile column at all
        precomputed = CryptoShortTermReversalStrategy().precompute(df)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))

    def test_exactly_at_threshold_qualifies(self):
        precomputed = self._frame([100.0] * 40, REVERSAL_PERCENTILE_THRESHOLD)
        self.assertTrue(bool(precomputed["qualifies"].iloc[-1]))


class TestEntrySignal(unittest.TestCase):
    def _precomputed(self, percentile=5.0, n=40):
        df = _daily([100.0] * n, start=date(2025, 1, 1))
        df["crypto_short_reversal_percentile"] = percentile
        return CryptoShortTermReversalStrategy().precompute(df)

    def test_fires_only_on_the_qualification_transition_day(self):
        strategy = CryptoShortTermReversalStrategy()
        precomputed = self._precomputed()
        rows = list(precomputed.itertuples(index=False))

        pre_row = rows[-2]._replace(qualifies=False, qualifies_prev=False)
        self.assertIsNone(strategy.entry_signal_at(pre_row))

        transition_row = rows[-1]._replace(qualifies=True, qualifies_prev=False)
        signal = strategy.entry_signal_at(transition_row)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "BUY")
        self.assertAlmostEqual(signal.stop_loss, signal.entry_price * (1 - STOP_LOSS_PCT))

    def test_no_signal_if_already_qualifying_yesterday(self):
        strategy = CryptoShortTermReversalStrategy()
        precomputed = self._precomputed()
        row = list(precomputed.itertuples(index=False))[-1]._replace(qualifies=True, qualifies_prev=True)
        self.assertIsNone(strategy.entry_signal_at(row))

    def test_no_signal_when_indicators_not_yet_available(self):
        strategy = CryptoShortTermReversalStrategy()
        precomputed = self._precomputed(n=10)  # far short of the 30-day formation window
        last_row = list(precomputed.itertuples(index=False))[-1]
        self.assertIsNone(strategy.entry_signal_at(last_row))

    def test_confidence_is_the_inverse_percentile(self):
        strategy = CryptoShortTermReversalStrategy()
        precomputed = self._precomputed(percentile=3.0)
        row = list(precomputed.itertuples(index=False))[-1]._replace(qualifies=True, qualifies_prev=False)
        signal = strategy.entry_signal_at(row)
        self.assertAlmostEqual(signal.confidence, 97.0)


class TestExitSignal(unittest.TestCase):
    def _position(self, entry_date):
        return OpenPosition(symbol="TEST", direction="BUY",
                             units=[PositionUnit(entry_price=100.0, entry_date=entry_date, quantity=10)],
                             stop_loss=80.0)

    def test_fires_at_or_past_the_holding_period_calendar_days(self):
        strategy = CryptoShortTermReversalStrategy()
        entry_date = date(2024, 1, 1)
        exit_date = entry_date + timedelta(days=HOLDING_PERIOD_DAYS)

        class Row:
            date = exit_date
            Close = 105.0
        exit_price = strategy.exit_signal_at(Row(), self._position(entry_date))
        self.assertAlmostEqual(exit_price, 105.0)

    def test_no_exit_before_the_holding_period_elapses(self):
        strategy = CryptoShortTermReversalStrategy()
        entry_date = date(2024, 1, 1)
        near_date = entry_date + timedelta(days=HOLDING_PERIOD_DAYS - 1)

        class Row:
            date = near_date
            Close = 105.0
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))

    def test_no_percentile_based_early_exit_exists(self):
        strategy = CryptoShortTermReversalStrategy()
        entry_date = date(2024, 1, 1)
        near_date = entry_date + timedelta(days=5)

        class Row:
            date = near_date
            Close = 150.0
            crypto_short_reversal_percentile = 99.0  # firmly in the top decile -- should NOT trigger an exit
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))


class TestEndToEndBacktest(unittest.TestCase):
    def test_fractional_positions_only_for_the_bottom_decile_coin(self):
        start = date(2025, 1, 1)
        n = CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS + HOLDING_PERIOD_DAYS + 10
        data = {"A": _daily([100.0 + 0.5 * i for i in range(n)], start=start),
                "B": _daily([3_000.0 + 10 * i for i in range(n)], start=start)}
        idx = data["A"].index
        # A: disqualified (above the decile cutoff) through warm-up, then transitions into the
        # bottom decile right after -- a clean state-transition for the single-vintage entry to
        # fire on, with enough remaining days for the 30-day holding-period exit to actually close
        # the trade before the data ends.
        a_percentile = [50.0] * CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS + [5.0] * (
            n - CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS)
        extra = {"A": pd.Series(a_percentile, index=idx, name="crypto_short_reversal_percentile"),
                 "B": pd.Series([90.0] * n, index=idx, name="crypto_short_reversal_percentile")}
        result = simulate_portfolio(data, CryptoShortTermReversalStrategy(), 1_000.0, sector_map={},
                                    extra_columns_by_symbol=extra)
        trades = result["trades"]
        self.assertTrue(trades, "a 1,000 USDT book must be able to buy a fraction of the bottom-decile coin")
        self.assertTrue(all(t.symbol == "A" for t in trades))   # B never qualifies (percentile 90)
        self.assertTrue(all(0 < t.quantity for t in trades))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_approved_plan(self):
        self.assertEqual(CRYPTO_SHORT_TERM_REVERSAL_FORMATION_DAYS, 30)
        self.assertAlmostEqual(REVERSAL_PERCENTILE_THRESHOLD, 10.0)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.20)
        self.assertEqual(HOLDING_PERIOD_DAYS, 30)
        self.assertEqual(CryptoShortTermReversalStrategy.max_units, 1)
        self.assertAlmostEqual(CryptoShortTermReversalStrategy.risk_pct_per_unit, 0.025)
        self.assertTrue(CryptoShortTermReversalStrategy.fractional_quantities)
        self.assertEqual(CryptoShortTermReversalStrategy.min_lookback_days, 30)

    def test_threshold_is_inverted_relative_to_momentum_strategies(self):
        self.assertLess(REVERSAL_PERCENTILE_THRESHOLD, 50.0)


if __name__ == "__main__":
    unittest.main()
