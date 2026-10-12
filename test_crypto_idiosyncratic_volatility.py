"""
Unit tests for swing_research/strategies/crypto_idiosyncratic_volatility.py
and its cross-sectional IVOL plumbing in swing_research/cross_sectional.py:
the 30-calendar-day crypto IVOL formation, the equal-weighted
universe-built market factor, monthly month-end-only decisions, top-decile
(highest IVOL) entry, the 20% stop, and the 25-calendar-day minimum that
always resolves to the next month-end -- the same conventions already
exercised for crypto_illiquidity_premium.py in test_crypto_lane.py. Offline,
hand-built fixtures, no network calls.

    python test_crypto_idiosyncratic_volatility.py
"""

import unittest
from datetime import date, timedelta

import pandas as pd

from swing_research.backtesting_engine import simulate_portfolio
from swing_research.base import OpenPosition, PositionUnit
from swing_research.cross_sectional import (
    CRYPTO_IVOL_FORMATION_DAYS, compute_crypto_idiosyncratic_volatility_percentile_ranks,
    compute_crypto_market_factor_close,
)
from swing_research.strategies.crypto_idiosyncratic_volatility import (
    HOLDING_PERIOD_DAYS, IVOL_PERCENTILE_THRESHOLD, STOP_LOSS_PCT, CryptoIdiosyncraticVolatilityStrategy,
)


def _daily(closes, start=date(2026, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


class TestCryptoIvolRanksEndToEnd(unittest.TestCase):
    def test_noisy_coin_ranks_highest_against_an_equal_weighted_universe_factor(self):
        n = CRYPTO_IVOL_FORMATION_DAYS + 5
        start = date(2025, 1, 1)
        base = [100.0 * (1.001 ** i) for i in range(n)]
        noisy = [v * (1 + (0.05 if i % 2 == 0 else -0.05)) for i, v in enumerate(base)]
        calm = [v * (1 + (0.001 if i % 2 == 0 else -0.001)) for i, v in enumerate(base)]
        data = {"NOISY": _daily(noisy, start), "CALM": _daily(calm, start), "C": _daily(base, start)}

        market_close = compute_crypto_market_factor_close(data)
        ranks = compute_crypto_idiosyncratic_volatility_percentile_ranks(data, market_close)
        last_date = data["NOISY"].index[-1]
        self.assertGreater(ranks["NOISY"].loc[last_date], ranks["CALM"].loc[last_date])

    def test_needs_a_full_formation_window(self):
        n = CRYPTO_IVOL_FORMATION_DAYS - 5
        data = {"A": _daily([100.0] * n, date(2025, 1, 1))}
        market_close = compute_crypto_market_factor_close(data)
        ranks = compute_crypto_idiosyncratic_volatility_percentile_ranks(data, market_close)
        self.assertTrue(ranks["A"].isna().all())


class TestStrategyPrecomputeAndEntry(unittest.TestCase):
    def _frame(self, start, closes, percentiles):
        df = _daily(closes, start=start)
        df["crypto_ivol_percentile"] = percentiles
        return CryptoIdiosyncraticVolatilityStrategy().precompute(df)

    def test_enters_only_at_month_end_in_the_top_decile(self):
        strategy = CryptoIdiosyncraticVolatilityStrategy()
        start, n = date(2025, 1, 25), 10   # crosses the 31 Jan month-end
        df = self._frame(start, [100.0] * n, [95.0] * n)
        rows = list(df.itertuples())
        self.assertTrue(any(r.is_month_end for r in rows))
        for r in rows:
            sig = strategy.entry_signal_at(r)
            self.assertIsNotNone(sig) if r.is_month_end else self.assertIsNone(sig)
        low = self._frame(start, [100.0] * n, [89.9] * n)
        self.assertTrue(all(strategy.entry_signal_at(r) is None for r in low.itertuples() if r.is_month_end))

    def test_missing_percentile_column_defaults_to_disqualified(self):
        strategy = CryptoIdiosyncraticVolatilityStrategy()
        df = _daily([100.0] * 10, start=date(2025, 1, 25))   # no crypto_ivol_percentile column at all
        precomputed = strategy.precompute(df)
        self.assertFalse(bool(precomputed["qualifies"].iloc[-1]))

    def test_stop_is_20_pct_below_entry_and_confidence_is_percentile(self):
        strategy = CryptoIdiosyncraticVolatilityStrategy()
        df = self._frame(date(2025, 1, 31), [200.0], [97.0])
        sig = strategy.entry_signal_at(list(df.itertuples())[0])
        self.assertAlmostEqual(sig.stop_loss, 200.0 * (1 - STOP_LOSS_PCT))
        self.assertAlmostEqual(sig.confidence, 97.0)
        self.assertEqual(sig.direction, "BUY")

    def test_exactly_at_threshold_qualifies(self):
        strategy = CryptoIdiosyncraticVolatilityStrategy()
        df = self._frame(date(2025, 1, 31), [100.0], [IVOL_PERCENTILE_THRESHOLD])
        self.assertTrue(bool(df["qualifies"].iloc[-1]))


class TestExitSignal(unittest.TestCase):
    def _frame(self, start, closes, percentiles):
        df = _daily(closes, start=start)
        df["crypto_ivol_percentile"] = percentiles
        return CryptoIdiosyncraticVolatilityStrategy().precompute(df)

    def test_exits_at_the_next_month_end_after_the_holding_period(self):
        strategy = CryptoIdiosyncraticVolatilityStrategy()
        start, n = date(2025, 1, 1), 65   # through early March
        df = self._frame(start, [100.0] * n, [50.0] * n)   # never qualifies, only exit logic is exercised
        rows = list(df.itertuples())
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=100.0, entry_date=date(2025, 1, 31), quantity=1.0)])
        feb_end = next(r for r in rows if r.is_month_end and r.date.month == 2)
        for r in rows:
            if r.date < feb_end.date:
                self.assertIsNone(strategy.exit_signal_at(r, pos))
        self.assertEqual(strategy.exit_signal_at(feb_end, pos), 100.0)   # 28 days later, >= the 25-day minimum

    def test_no_percentile_based_early_exit_exists(self):
        strategy = CryptoIdiosyncraticVolatilityStrategy()
        start, n = date(2025, 1, 1), 40
        df = self._frame(start, [100.0] * n, [99.0] * n)   # firmly in the top decile throughout
        rows = list(df.itertuples())
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=100.0, entry_date=date(2025, 1, 31), quantity=1.0)])
        jan_end = next(r for r in rows if r.is_month_end and r.date.month == 1)
        # Even at the very next month-end, a still-high percentile must not trigger an exit
        # -- only the holding-period/month-end rule governs exits.
        self.assertIsNone(strategy.exit_signal_at(jan_end, pos))


class TestEndToEndBacktest(unittest.TestCase):
    def test_fractional_monthly_positions_only_for_the_top_decile_coin(self):
        start, n = date(2025, 1, 1), 95   # three month-ends: Jan 31, Feb 28, Mar 31
        data = {"A": _daily([100.0 + 0.5 * i for i in range(n)], start=start),
                "B": _daily([3_000.0 + 10 * i for i in range(n)], start=start)}
        idx = data["A"].index
        extra = {"A": pd.Series([95.0] * n, index=idx, name="crypto_ivol_percentile"),
                 "B": pd.Series([10.0] * n, index=idx, name="crypto_ivol_percentile")}
        result = simulate_portfolio(data, CryptoIdiosyncraticVolatilityStrategy(), 1_000.0, sector_map={},
                                    extra_columns_by_symbol=extra)
        trades = result["trades"]
        self.assertTrue(trades, "a 1,000 USDT book must be able to buy a fraction of the top-decile coin")
        self.assertTrue(all(t.symbol == "A" for t in trades))   # B never qualifies (percentile 10)
        self.assertTrue(all(0 < t.quantity for t in trades))


class TestConstants(unittest.TestCase):
    def test_documented_parameters_match_the_approved_plan(self):
        self.assertEqual(CRYPTO_IVOL_FORMATION_DAYS, 30)
        self.assertAlmostEqual(IVOL_PERCENTILE_THRESHOLD, 90.0)
        self.assertAlmostEqual(STOP_LOSS_PCT, 0.20)
        self.assertEqual(HOLDING_PERIOD_DAYS, 25)
        self.assertEqual(CryptoIdiosyncraticVolatilityStrategy.max_units, 1)
        self.assertAlmostEqual(CryptoIdiosyncraticVolatilityStrategy.risk_pct_per_unit, 0.025)
        self.assertTrue(CryptoIdiosyncraticVolatilityStrategy.fractional_quantities)

    def test_threshold_is_a_top_decile_unlike_the_equity_sibling(self):
        # The paper's documented sign for crypto is the OPPOSITE of the equity-market IVOL
        # puzzle (idiosyncratic_volatility.py longs the bottom decile) -- this strategy longs
        # the TOP decile of idiosyncratic volatility.
        self.assertGreater(IVOL_PERCENTILE_THRESHOLD, 50.0)


if __name__ == "__main__":
    unittest.main()
