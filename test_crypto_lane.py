"""
Unit tests for the crypto lane (Pool F, 2026-09-13): Binance kline parsing,
the cost + India VDA tax model, the 3-week cross-sectional momentum ranks,
the strategy's Monday-only entries and 7-day exits, fractional sizing in
the backtest / benchmark / paper engines, and the post-tax walk-forward
returning both pre- and post-tax metrics. Offline, hand-built fixtures.

    python test_crypto_lane.py
"""

import json
import os
import tempfile
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from data.fetch_crypto import klines_to_dataframe
from swing_research.backtesting_engine import Trade, simulate_portfolio, size_quantity
from swing_research.base import Strategy
from swing_research.benchmarks import simulate_buy_and_hold
from swing_research.cross_sectional import compute_crypto_momentum_percentile_ranks, compute_crypto_momentum_score
from swing_research.crypto_costs import (
    INDIA_VDA_TAX_RATE, CryptoCostModel, apply_crypto_costs, apply_india_tax, tax_summary, trade_cost, trade_tax,
)
from swing_research.crypto_lane import run_walk_forward_crypto
from swing_research.strategies.crypto_xs_momentum import (
    HOLDING_DAYS, STOP_LOSS_PCT, CryptoCrossSectionalMomentumStrategy,
)


def _trade(entry, exit_, qty, symbol="BTC"):
    return Trade(symbol=symbol, entry_date=date(2026, 1, 5), exit_date=date(2026, 1, 12), entry_price=entry,
                 exit_price=exit_, quantity=qty, pnl=(exit_ - entry) * qty, exit_reason="time")


def _daily(closes, start=date(2026, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


class TestKlinesParsing(unittest.TestCase):
    def test_rows_become_daily_ohlcv_indexed_by_utc_date(self):
        t0 = int(pd.Timestamp("2026-03-01", tz="UTC").timestamp() * 1000)
        rows = [[t0 + i * 86_400_000, "100", "110", "90", str(100 + i), "5", 0, "0", 1, "0", "0", "0"] for i in range(3)]
        df = klines_to_dataframe(rows)
        self.assertEqual(list(df.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(df.index[0], pd.Timestamp("2026-03-01"))
        self.assertIsNone(df.index.tz)
        self.assertEqual(df["Close"].tolist(), [100.0, 101.0, 102.0])

    def test_empty_rows(self):
        self.assertTrue(klines_to_dataframe([]).empty)


class TestCostsAndTax(unittest.TestCase):
    def setUp(self):
        self.model = CryptoCostModel(fee_pct_per_side=0.003, spread_bps_per_side=10)

    def test_round_trip_cost_is_fee_plus_spread_on_both_sides(self):
        t = _trade(100.0, 110.0, 2.0)          # buy 200, sell 220 -> turnover 420
        self.assertAlmostEqual(trade_cost(t, self.model), 420 * 0.003 + 420 * 0.001, places=6)

    def test_tax_only_on_winners_no_loss_offset(self):
        self.assertAlmostEqual(trade_tax(_trade(100.0, 110.0, 1.0)), 10 * INDIA_VDA_TAX_RATE)
        self.assertEqual(trade_tax(_trade(100.0, 90.0, 1.0)), 0.0)

    def test_pre_and_post_tax_pnl(self):
        winner, loser = _trade(100.0, 110.0, 1.0), _trade(100.0, 90.0, 1.0)
        pre = apply_crypto_costs([winner, loser], self.model)
        post = apply_india_tax([winner, loser], self.model)
        self.assertAlmostEqual(pre[0].pnl, 10 - 210 * 0.004, places=6)
        self.assertAlmostEqual(post[0].pnl, 10 - 210 * 0.004 - 10 * 0.312, places=6)
        self.assertAlmostEqual(pre[1].pnl, -10 - 190 * 0.004, places=6)
        self.assertAlmostEqual(post[1].pnl, pre[1].pnl, places=6)   # a loser pays no tax and gets no relief
        self.assertEqual(winner.pnl, 10.0)                            # inputs untouched

    def test_ledger_adds_up_and_reports_refundable_tds(self):
        trades = [_trade(100.0, 110.0, 1.0), _trade(100.0, 90.0, 1.0)]
        s = tax_summary(trades, self.model)
        self.assertEqual(s["raw_pnl"], 0.0)
        self.assertAlmostEqual(s["pre_tax_pnl"], s["raw_pnl"] - s["fees_and_spread"], places=2)
        self.assertAlmostEqual(s["post_tax_pnl"], s["pre_tax_pnl"] - s["tax"], places=2)
        self.assertAlmostEqual(s["tds_withheld_refundable"], (110 + 90) * 0.01, places=2)
        self.assertEqual((s["winning_trades"], s["losing_trades"]), (1, 1))


class TestMomentumRanks(unittest.TestCase):
    def test_score_is_21_day_return(self):
        df = _daily([100.0] * 21 + [121.0])
        score = compute_crypto_momentum_score(df)
        self.assertTrue(np.isnan(score.iloc[20]))
        self.assertAlmostEqual(score.iloc[21], 0.21)

    def test_percentile_ranks_across_coins(self):
        data = {"A": _daily([100.0] * 21 + [130.0]), "B": _daily([100.0] * 21 + [110.0]),
                "C": _daily([100.0] * 21 + [90.0])}
        ranks = compute_crypto_momentum_percentile_ranks(data)
        last = {k: v.iloc[-1] for k, v in ranks.items()}
        self.assertAlmostEqual(last["A"], 100.0)
        self.assertAlmostEqual(last["C"], 100 / 3)
        self.assertTrue(last["C"] < last["B"] < last["A"])


class TestStrategyRules(unittest.TestCase):
    def _frame(self, start, closes, percentiles):
        df = _daily(closes, start=start)
        df["crypto_momentum_percentile"] = percentiles
        return CryptoCrossSectionalMomentumStrategy().precompute(df)

    def test_enters_only_on_monday_in_top_quintile(self):
        strategy = CryptoCrossSectionalMomentumStrategy()
        start = date(2026, 1, 5)   # a Monday
        df = self._frame(start, [100.0] * 7, [90.0] * 7)
        rows = list(df.itertuples())
        self.assertIsNotNone(strategy.entry_signal_at(rows[0]))          # Monday, percentile 90
        for r in rows[1:]:
            self.assertIsNone(strategy.entry_signal_at(r))              # Tue..Sun never enter
        low = self._frame(start, [100.0] * 7, [79.9] * 7)
        self.assertIsNone(strategy.entry_signal_at(list(low.itertuples())[0]))

    def test_stop_is_20_pct_below_entry(self):
        strategy = CryptoCrossSectionalMomentumStrategy()
        df = self._frame(date(2026, 1, 5), [200.0], [95.0])
        sig = strategy.entry_signal_at(list(df.itertuples())[0])
        self.assertAlmostEqual(sig.stop_loss, 200.0 * (1 - STOP_LOSS_PCT))
        self.assertAlmostEqual(sig.confidence, 95.0)

    def test_exits_on_the_next_monday_after_seven_days(self):
        from swing_research.base import OpenPosition, PositionUnit
        strategy = CryptoCrossSectionalMomentumStrategy()
        df = self._frame(date(2026, 1, 5), [100.0] * 8, [50.0] * 8)
        rows = list(df.itertuples())
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=100.0, entry_date=date(2026, 1, 5), quantity=1.0)])
        for r in rows[:7]:
            self.assertIsNone(strategy.exit_signal_at(r, pos))
        self.assertEqual(strategy.exit_signal_at(rows[7], pos), 100.0)   # Monday 12 Jan, 7 days later
        self.assertEqual(HOLDING_DAYS, 7)

    def test_end_to_end_fractional_positions_in_backtest(self):
        start = date(2026, 1, 5)
        n = 60
        closes = [50_000.0 + 100 * i for i in range(n)]
        data = {"BTC": _daily(closes, start=start), "ETH": _daily([3_000.0 + 10 * i for i in range(n)], start=start)}
        extra = {"BTC": pd.Series([95.0] * n, index=data["BTC"].index, name="crypto_momentum_percentile"),
                 "ETH": pd.Series([10.0] * n, index=data["ETH"].index, name="crypto_momentum_percentile")}
        result = simulate_portfolio(data, CryptoCrossSectionalMomentumStrategy(), 1_000.0, sector_map={},
                                    extra_columns_by_symbol=extra)
        trades = result["trades"]
        self.assertTrue(trades, "a 1,000 USDT book must be able to buy a fraction of BTC")
        self.assertTrue(all(t.symbol == "BTC" for t in trades))
        self.assertTrue(all(0 < t.quantity < 1 for t in trades))
        self.assertTrue(all((t.exit_date - t.entry_date).days == 7 for t in trades if t.exit_reason != "end_of_backtest"))


class TestFractionalSizing(unittest.TestCase):
    def test_size_quantity_int_by_default_fraction_when_declared(self):
        class Whole(Strategy):
            pass

        class Frac(Strategy):
            fractional_quantities = True

        self.assertEqual(size_quantity(2.9, Whole()), 2)
        self.assertEqual(size_quantity(0.4, Whole()), 0)
        self.assertAlmostEqual(size_quantity(0.4, Frac()), 0.4)
        self.assertAlmostEqual(size_quantity(1 / 3, Frac()), 0.333333)

    def test_buy_and_hold_fractional(self):
        data = {"BTC": _daily([80_000.0, 88_000.0])}
        whole = simulate_buy_and_hold(data, 1_000.0)
        frac = simulate_buy_and_hold(data, 1_000.0, fractional_quantities=True)
        self.assertEqual(whole["trades"], [])
        self.assertEqual(len(frac["trades"]), 1)
        self.assertAlmostEqual(frac["trades"][0].quantity, 0.0125)
        self.assertAlmostEqual(frac["trades"][0].pnl, 100.0)

    def test_paper_engine_sizes_fractions_for_crypto_strategy(self):
        from deployment import paper_trading_engine as pte
        tmp = tempfile.mkdtemp()
        original = pte.PAPER_TRADING_STATE_DIR
        pte.PAPER_TRADING_STATE_DIR = tmp
        try:
            start = date(2026, 1, 5)
            n = 30
            data = {"BTC": _daily([80_000.0] * n, start=start)}
            extra = {"BTC": pd.Series([95.0] * n, index=data["BTC"].index, name="crypto_momentum_percentile")}
            os.makedirs(os.path.join(tmp, "crypto_book"))
            with open(os.path.join(tmp, "crypto_book", "portfolio.json"), "w") as f:
                json.dump({"cash": 1_000.0, "starting_capital": 1_000.0, "positions": {},
                           "last_processed_date": None, "pending_entries": {}, "pending_exits": {}}, f)
            result = pte.run_daily("crypto_book", CryptoCrossSectionalMomentumStrategy(), lambda: data,
                                   compute_extra_columns_fn=lambda d: extra, as_of_date=start, force=True,
                                   min_position_value_rupees=10, sizing_capital_cap=1_000.0)
            self.assertEqual(len(result["new_entries"]), 1)
            qty = result["new_entries"][0]["quantity"]
            self.assertAlmostEqual(qty, round(1_000 * 0.025 / (80_000 * STOP_LOSS_PCT), 6))
            self.assertTrue(0 < qty < 1)
        finally:
            pte.PAPER_TRADING_STATE_DIR = original


class TestWalkForwardCrypto(unittest.TestCase):
    def test_returns_post_tax_verdict_inputs_and_pre_tax_metrics(self):
        start = date(2025, 1, 6)
        n = 300
        rng = np.random.default_rng(7)
        data, extra = {}, {}
        for i, sym in enumerate(["BTC", "ETH", "SOL", "XRP", "ADA"]):
            closes = 100.0 * np.cumprod(1 + rng.normal(0.002, 0.03, n))
            data[sym] = _daily(list(closes), start=start)
        ranks = compute_crypto_momentum_percentile_ranks(data)
        extra = {s: r.rename("crypto_momentum_percentile") for s, r in ranks.items()}
        result = run_walk_forward_crypto(CryptoCrossSectionalMomentumStrategy(), data, 1_000.0, {},
                                         start, start + timedelta(days=n - 1), n_walk_forward_windows=3,
                                         extra_columns_by_symbol=extra)
        for key in ("verdict", "walk_forward_metrics", "out_of_sample_metrics", "all_trades",
                    "pre_tax_walk_forward_metrics", "pre_tax_out_of_sample_metrics"):
            self.assertIn(key, result)
        self.assertEqual(len(result["walk_forward_metrics"]), 2)
        self.assertEqual(len(result["pre_tax_walk_forward_metrics"]), 2)
        self.assertTrue(result["all_trades"])
        # post-tax can never beat pre-tax, window by window
        for pre, post in zip(result["pre_tax_walk_forward_metrics"], result["walk_forward_metrics"]):
            self.assertLessEqual(post["total_pnl"], pre["total_pnl"] + 1e-9)
            self.assertEqual(post["total_trades"], pre["total_trades"])


class TestTrendTimingRules(unittest.TestCase):
    """Faber's 10-month SMA rule: month-end-only decisions, SMA of month-end
    closes, entry above / exit below, 20% stop, 20% sleeve sizing."""

    def _year(self, monthly_closes, start=date(2025, 1, 1)):
        # daily data whose month-end closes are exactly monthly_closes (flat within the month)
        closes, d = [], start
        i = 0
        while i < len(monthly_closes):
            closes.append(monthly_closes[i])
            nxt = d + timedelta(days=1)
            if nxt.month != d.month:
                i += 1
            d = nxt
        return _daily(closes, start=start)

    def test_sma_is_of_month_end_closes_and_flags_month_ends(self):
        from swing_research.strategies.crypto_trend_timing import compute_month_end_sma
        df = compute_month_end_sma(self._year([float(m) for m in range(1, 13)]))
        me = df[df["is_month_end"]]
        self.assertEqual(len(me), 12)
        self.assertEqual([d.day for d in me.index[:3]], [31, 28, 31])
        self.assertTrue(np.isnan(me["sma_month_end"].iloc[8]))          # only 9 month-ends so far
        self.assertAlmostEqual(me["sma_month_end"].iloc[9], 5.5)         # mean of 1..10
        self.assertAlmostEqual(me["sma_month_end"].iloc[11], 7.5)        # mean of 3..12
        self.assertTrue(df.loc[~df["is_month_end"], "sma_month_end"].isna().all())

    def test_trailing_partial_month_is_not_a_month_end_and_warm_up_column_is_kept(self):
        from swing_research.strategies.crypto_trend_timing import CryptoTrendTimingStrategy, compute_month_end_sma
        full = self._year([float(m) for m in range(1, 13)])
        self.assertFalse(compute_month_end_sma(full.iloc[:-5])["is_month_end"].iloc[-1])   # 26 Dec: not flagged
        warm = compute_month_end_sma(full)[["sma_month_end"]]
        window = full.loc["2025-11-01":].join(warm.loc["2025-11-01":])    # a window with only 2 month-ends
        pre = CryptoTrendTimingStrategy().precompute(window)
        me = pre[pre["is_month_end"]]
        self.assertAlmostEqual(me["sma_month_end"].iloc[0], 6.5)          # mean of 2..11 from full history
        self.assertAlmostEqual(me["sma_month_end"].iloc[1], 7.5)
        cold = CryptoTrendTimingStrategy().precompute(full.loc["2025-11-01":])
        self.assertTrue(cold.loc[cold["is_month_end"], "sma_month_end"].isna().all())   # without warm-up: blind

    def test_entry_above_sma_exit_below_only_at_month_end(self):
        from swing_research.base import OpenPosition, PositionUnit
        from swing_research.strategies.crypto_trend_timing import STOP_LOSS_PCT, CryptoTrendTimingStrategy
        strategy = CryptoTrendTimingStrategy()
        closes = [100.0] * 10 + [120.0, 80.0]     # Nov above the SMA (~102), Dec below (~100)
        df = strategy.precompute(self._year(closes))
        rows = list(df.itertuples())
        month_end_rows = [r for r in rows if r.is_month_end]
        nov, dec = month_end_rows[10], month_end_rows[11]
        sig = strategy.entry_signal_at(nov)
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.stop_loss, 120.0 * (1 - STOP_LOSS_PCT))
        self.assertIsNone(strategy.entry_signal_at(dec))
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=120.0, entry_date=nov.Index.date(), quantity=0.1)])
        self.assertIsNone(strategy.exit_signal_at(nov, pos))
        self.assertEqual(strategy.exit_signal_at(dec, pos), 80.0)
        for r in rows:
            if not r.is_month_end:
                self.assertIsNone(strategy.entry_signal_at(r))
                self.assertIsNone(strategy.exit_signal_at(r, pos))

    def test_sizing_is_a_twenty_percent_sleeve(self):
        from swing_research.strategies.crypto_trend_timing import CryptoTrendTimingStrategy
        strategy = CryptoTrendTimingStrategy()
        closes = [100.0] * 10 + [120.0] * 3
        data = {"BTC": self._year(closes)}
        result = simulate_portfolio(data, strategy, 1_000.0, sector_map={})
        self.assertEqual(len(result["trades"]), 1)
        t = result["trades"][0]
        self.assertAlmostEqual(t.entry_price * t.quantity, 200.0, places=2)   # 4% risk / 20% stop = 20% of book
        self.assertTrue(strategy.fractional_quantities)


class TestTimeSeriesMomentumRules(unittest.TestCase):
    def test_signal_is_365_day_return_and_read_at_month_end_only(self):
        from swing_research.strategies.crypto_tsmom import CryptoTimeSeriesMomentumStrategy, compute_tsmom_signal
        n = 400
        closes = [100.0 + i * 0.5 for i in range(n)]           # rising: 365-day return positive
        df = compute_tsmom_signal(_daily(closes, start=date(2025, 1, 1)))
        self.assertTrue(df["tsmom_return"].iloc[:365].isna().all())
        self.assertAlmostEqual(df["tsmom_return"].iloc[365], (100 + 365 * 0.5) / 100 - 1)
        s = CryptoTimeSeriesMomentumStrategy()
        pre = s.precompute(df)
        rows = list(pre.itertuples())
        month_ends = [r for r in rows if r.is_month_end and not pd.isna(r.tsmom_return)]
        self.assertTrue(month_ends)
        self.assertIsNotNone(s.entry_signal_at(month_ends[0]))
        self.assertTrue(all(s.entry_signal_at(r) is None for r in rows if not r.is_month_end))

    def test_negative_return_exits_and_never_enters(self):
        from swing_research.base import OpenPosition, PositionUnit
        from swing_research.strategies.crypto_tsmom import CryptoTimeSeriesMomentumStrategy
        n = 400
        closes = [200.0 - i * 0.2 for i in range(n)]           # falling
        s = CryptoTimeSeriesMomentumStrategy()
        pre = s.precompute(_daily(closes, start=date(2025, 1, 1)))
        rows = [r for r in pre.itertuples() if r.is_month_end and not pd.isna(r.tsmom_return)]
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=150.0, entry_date=date(2025, 6, 30), quantity=1.0)])
        self.assertIsNone(s.entry_signal_at(rows[0]))
        self.assertEqual(s.exit_signal_at(rows[0], pos), float(rows[0].Close))

    def test_warm_up_column_is_kept(self):
        from swing_research.strategies.crypto_tsmom import compute_tsmom_signal
        full = _daily([100.0 + i for i in range(400)], start=date(2025, 1, 1))
        warm = compute_tsmom_signal(full)[["tsmom_return"]]
        window = full.iloc[-30:].join(warm.iloc[-30:])
        self.assertFalse(compute_tsmom_signal(window)["tsmom_return"].isna().all())
        self.assertTrue(compute_tsmom_signal(full.iloc[-30:])["tsmom_return"].isna().all())


if __name__ == "__main__":
    unittest.main()
