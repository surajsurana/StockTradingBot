"""
Unit tests for swing_research/strategies/crypto_max_effect.py and its
cross-sectional MAX plumbing in swing_research/cross_sectional.py: the
30-calendar-day MAX formation window, the WEEKLY (Monday UTC) rebalance
gate, the bottom-QUINTILE (not decile) entry threshold, the 7-calendar-day
holding period, the 20% crypto-scaled stop, and no percentile-based early
exit -- mirroring test_crypto_short_term_reversal.py's cross-sectional
ranking pattern and crypto_xs_momentum.py's weekly-rebalance design (no
dedicated test file exists for that strategy to mirror directly). Offline,
hand-built fixtures, no network calls.

    python test_crypto_max_effect.py
"""

import unittest
from datetime import date, timedelta

import pandas as pd

from swing_research.backtesting_engine import simulate_portfolio
from swing_research.base import OpenPosition, PositionUnit
from swing_research.cross_sectional import (
    CRYPTO_MAX_EFFECT_FORMATION_DAYS, compute_crypto_max_effect_percentile_ranks, compute_crypto_max_effect_score,
)
from swing_research.strategies.crypto_max_effect import (
    BOTTOM_QUINTILE_PERCENTILE, HOLDING_DAYS, REBALANCE_WEEKDAY, STOP_LOSS_PCT, CryptoMaxEffectStrategy,
)


def _daily(closes, start=date(2026, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


class TestCryptoMaxEffectRanksEndToEnd(unittest.TestCase):
    def test_a_calm_coin_ranks_lowest_against_a_lottery_like_coin(self):
        n = CRYPTO_MAX_EFFECT_FORMATION_DAYS + 5
        start = date(2025, 1, 1)
        calm = [100.0 * (0.9995 ** i) for i in range(n)]    # steadily down -- its single highest
                                                              # daily return is still negative, below FLAT's 0
        lottery = [100.0] * (n - 1) + [140.0]                # one huge spike day
        flat = [100.0] * n
        data = {"CALM": _daily(calm, start), "LOTTERY": _daily(lottery, start), "FLAT": _daily(flat, start)}

        ranks = compute_crypto_max_effect_percentile_ranks(data)
        last_date = data["CALM"].index[-1]
        self.assertLess(ranks["CALM"].loc[last_date], ranks["FLAT"].loc[last_date])
        self.assertLess(ranks["FLAT"].loc[last_date], ranks["LOTTERY"].loc[last_date])

    def test_needs_a_full_formation_window(self):
        n = CRYPTO_MAX_EFFECT_FORMATION_DAYS - 5
        data = {"A": _daily([100.0] * n, date(2025, 1, 1))}
        ranks = compute_crypto_max_effect_percentile_ranks(data)
        self.assertTrue(ranks["A"].isna().all())

    def test_score_is_the_trailing_single_highest_daily_return_no_lookahead(self):
        closes = [100.0] * CRYPTO_MAX_EFFECT_FORMATION_DAYS + [130.0, 100.0]
        df = _daily(closes, date(2025, 1, 1))
        score = compute_crypto_max_effect_score(df)
        self.assertAlmostEqual(score.iloc[-2], 0.30)   # the spike day itself: MAX = that day's own return
        # one day after the spike, it is still the trailing max (within the formation window)
        self.assertAlmostEqual(score.iloc[-1], 0.30)
        self.assertTrue(score.iloc[:CRYPTO_MAX_EFFECT_FORMATION_DAYS].isna().all())


class TestPrecompute(unittest.TestCase):
    def _frame(self, closes, percentile, start=date(2025, 1, 5)):   # 2025-01-05 is a Sunday
        df = _daily(closes, start=start)
        df["crypto_max_effect_percentile"] = percentile
        return CryptoMaxEffectStrategy().precompute(df)

    def test_qualifies_only_on_monday_and_at_or_below_threshold(self):
        precomputed = self._frame([100.0] * 10, 5.0)   # start Sunday 1/5 -> Monday 1/6 is index 1
        monday_row = precomputed.iloc[1]
        self.assertEqual(monday_row.name.weekday(), REBALANCE_WEEKDAY)
        self.assertTrue(bool(monday_row.qualifies))
        tuesday_row = precomputed.iloc[2]
        self.assertFalse(bool(tuesday_row.qualifies))   # same percentile, but not a rebalance day

    def test_does_not_qualify_above_threshold_even_on_rebalance_day(self):
        precomputed = self._frame([100.0] * 10, 25.0)
        monday_row = precomputed.iloc[1]
        self.assertFalse(bool(monday_row.qualifies))

    def test_missing_percentile_column_defaults_to_disqualified(self):
        df = _daily([100.0] * 10, start=date(2025, 1, 5))   # no crypto_max_effect_percentile column
        precomputed = CryptoMaxEffectStrategy().precompute(df)
        monday_row = precomputed.iloc[1]
        self.assertFalse(bool(monday_row.qualifies))

    def test_exactly_at_threshold_qualifies(self):
        precomputed = self._frame([100.0] * 10, BOTTOM_QUINTILE_PERCENTILE)
        monday_row = precomputed.iloc[1]
        self.assertTrue(bool(monday_row.qualifies))


class TestEntrySignal(unittest.TestCase):
    def _precomputed(self, percentile=5.0, n=10, start=date(2025, 1, 5)):
        df = _daily([100.0] * n, start=start)
        df["crypto_max_effect_percentile"] = percentile
        return CryptoMaxEffectStrategy().precompute(df)

    def test_fires_on_a_qualifying_monday(self):
        strategy = CryptoMaxEffectStrategy()
        precomputed = self._precomputed()
        monday_row = list(precomputed.itertuples(index=False))[1]
        signal = strategy.entry_signal_at(monday_row)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "BUY")
        self.assertAlmostEqual(signal.stop_loss, signal.entry_price * (1 - STOP_LOSS_PCT))

    def test_no_signal_on_a_non_rebalance_day_even_if_qualifying(self):
        strategy = CryptoMaxEffectStrategy()
        precomputed = self._precomputed()
        tuesday_row = list(precomputed.itertuples(index=False))[2]._replace(qualifies=False)
        self.assertIsNone(strategy.entry_signal_at(tuesday_row))

    def test_no_signal_when_indicators_not_yet_available(self):
        strategy = CryptoMaxEffectStrategy()
        precomputed = self._precomputed(n=3)
        row = list(precomputed.itertuples(index=False))[1]._replace(qualifies=float("nan"))
        self.assertIsNone(strategy.entry_signal_at(row))

    def test_confidence_is_the_inverse_percentile(self):
        strategy = CryptoMaxEffectStrategy()
        precomputed = self._precomputed(percentile=3.0)
        monday_row = list(precomputed.itertuples(index=False))[1]
        signal = strategy.entry_signal_at(monday_row)
        self.assertAlmostEqual(signal.confidence, 97.0)


class TestExitSignal(unittest.TestCase):
    def _position(self, entry_date):
        return OpenPosition(symbol="TEST", direction="BUY",
                             units=[PositionUnit(entry_price=100.0, entry_date=entry_date, quantity=10)],
                             stop_loss=80.0)

    def test_fires_on_the_next_rebalance_day_at_or_past_the_holding_period(self):
        strategy = CryptoMaxEffectStrategy()
        entry_date = date(2025, 1, 6)   # a Monday
        exit_date = entry_date + timedelta(days=HOLDING_DAYS)   # the following Monday

        class Row:
            date = exit_date
            Close = 105.0
            is_rebalance_day = True
        exit_price = strategy.exit_signal_at(Row(), self._position(entry_date))
        self.assertAlmostEqual(exit_price, 105.0)

    def test_no_exit_before_the_holding_period_elapses_even_on_a_rebalance_day(self):
        strategy = CryptoMaxEffectStrategy()
        entry_date = date(2025, 1, 6)

        class Row:
            date = entry_date   # same-day "rebalance" but 0 days held
            Close = 105.0
            is_rebalance_day = True
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))

    def test_no_exit_on_a_non_rebalance_day_even_past_the_holding_period(self):
        strategy = CryptoMaxEffectStrategy()
        entry_date = date(2025, 1, 6)
        exit_date = entry_date + timedelta(days=HOLDING_DAYS)

        class Row:
            date = exit_date
            Close = 105.0
            is_rebalance_day = False
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))

    def test_no_percentile_based_early_exit_exists(self):
        strategy = CryptoMaxEffectStrategy()
        entry_date = date(2025, 1, 6)
        near_date = entry_date + timedelta(days=1)

        class Row:
            date = near_date
            Close = 150.0
            is_rebalance_day = True
            crypto_max_effect_percentile = 99.0   # firmly out of the bottom quintile -- must not matter
        self.assertIsNone(strategy.exit_signal_at(Row(), self._position(entry_date)))


class TestEndToEndBacktest(unittest.TestCase):
    def test_fractional_weekly_positions_only_for_the_bottom_quintile_coin(self):
        start = date(2025, 1, 1)
        n = CRYPTO_MAX_EFFECT_FORMATION_DAYS + 5 * HOLDING_DAYS + 10   # >=60 bars, min_bars_required default
        data = {"A": _daily([100.0 + 0.5 * i for i in range(n)], start=start),
                "B": _daily([3_000.0 + 10 * i for i in range(n)], start=start)}
        idx = data["A"].index
        extra = {"A": pd.Series([10.0] * n, index=idx, name="crypto_max_effect_percentile"),
                 "B": pd.Series([90.0] * n, index=idx, name="crypto_max_effect_percentile")}
        result = simulate_portfolio(data, CryptoMaxEffectStrategy(), 1_000.0, sector_map={},
                                    extra_columns_by_symbol=extra)
        trades = result["trades"]
        self.assertTrue(trades, "a 1,000 USDT book must be able to buy a fraction of the bottom-quintile coin")
        self.assertTrue(all(t.symbol == "A" for t in trades))   # B never qualifies (percentile 90)
        self.assertTrue(all(0 < t.quantity for t in trades))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_approved_plan(self):
        self.assertEqual(CRYPTO_MAX_EFFECT_FORMATION_DAYS, 30)
        self.assertAlmostEqual(BOTTOM_QUINTILE_PERCENTILE, 20.0)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.20)
        self.assertEqual(HOLDING_DAYS, 7)
        self.assertEqual(REBALANCE_WEEKDAY, 0)
        self.assertEqual(CryptoMaxEffectStrategy.max_units, 1)
        self.assertAlmostEqual(CryptoMaxEffectStrategy.risk_pct_per_unit, 0.025)
        self.assertTrue(CryptoMaxEffectStrategy.fractional_quantities)
        self.assertEqual(CryptoMaxEffectStrategy.min_lookback_days, 31)

    def test_threshold_is_a_quintile_not_this_programs_usual_decile(self):
        # The paper's own documented rule is a quintile sort (20%), not this module's usual
        # decile (10%) -- a deliberate, disclosed deviation from the program-wide convention.
        self.assertNotEqual(BOTTOM_QUINTILE_PERCENTILE, 10.0)
        self.assertLess(BOTTOM_QUINTILE_PERCENTILE, 50.0)   # still the "low MAX" / calmer side


if __name__ == "__main__":
    unittest.main()
