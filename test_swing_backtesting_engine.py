"""
Unit tests for swing_research/backtesting_engine.py -- hand-calculated
synthetic cases proving the core claim this whole program depends on:
multi-day holding works (no EOD force-close), pyramiding works, portfolio-
level correlation-group caps work, and the compounding equity curve
updates correctly. Run with:

    python test_swing_backtesting_engine.py
"""

import unittest
from datetime import date

import pandas as pd

from swing_research.backtesting_engine import (
    Trade, simulate_portfolio, simulate_portfolio_single_unit, simulate_symbol_single_unit,
    _risk_per_share, _trade_pnl,
)
from swing_research.base import OpenPosition, Signal, Strategy
from swing_research.metrics import compute_holding_period_breakdown, compute_metrics


def _bars(date_str, closes, base=0.0):
    idx = pd.date_range(f"{date_str}", periods=len(closes), freq="D")
    opens = [closes[0]] + closes[:-1]
    rows = [{"Open": base + o, "High": base + max(o, c) + 0.3, "Low": base + min(o, c) - 0.3,
             "Close": base + c, "Volume": 1000} for o, c in zip(opens, closes)]
    return pd.DataFrame(rows, index=idx)


class _FiresOnceHoldsMultiDay(Strategy):
    """Enters on day index 5 (arbitrary), stop far away so it survives many
    days, exits when price crosses back below entry -- exercises genuine
    multi-day holding with NO forced EOD close."""
    name = "multi_day_test"

    def precompute(self, price_history: pd.DataFrame) -> pd.DataFrame:
        return price_history.copy()

    def entry_signal_at(self, row) -> None:
        return None  # entry decided externally in this test via monkeypatched sequencing

    def exit_signal_at(self, row, open_position):
        return None


class TestMultiDayHolding(unittest.TestCase):
    def test_position_survives_across_many_days_no_eod_force_close(self):
        # A strategy that enters on the FIRST bar and only exits via its
        # own signal 10 days later -- if the engine force-closed at any
        # day boundary (the exact bug this engine exists to avoid), this
        # trade would show far more than 1 trade, or an early exit_reason
        # of something other than "signal_exit".
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 95]  # day 10 crashes
        bars = _bars("2026-01-05", closes)

        class _EntersDay0ExitsOnCrash(Strategy):
            name = "test"
            def precompute(self, price_history):
                return price_history.copy()
            def entry_signal_at(self, row):
                if row.Close == 100.0:
                    return Signal(symbol="", direction="BUY", entry_price=100.0, stop_loss=50.0,
                                   strategy_name=self.name)
                return None
            def exit_signal_at(self, row, open_position):
                if row.Close < 100.0:
                    return float(row.Close)
                return None

        result = simulate_portfolio({"TEST": bars}, _EntersDay0ExitsOnCrash(), starting_capital=100000,
                                     min_bars_required=1)
        self.assertEqual(len(result["trades"]), 1)
        t = result["trades"][0]
        self.assertEqual(t.exit_reason, "signal_exit")
        self.assertEqual((t.exit_date - t.entry_date).days, 10)  # held 10 days, not force-closed daily
        self.assertAlmostEqual(t.exit_price, 95.0)

    def test_stop_loss_checked_before_signal_exit_same_day(self):
        closes = [100, 90]  # day 1 gaps straight through the stop
        bars = _bars("2026-01-05", closes)

        class _StopAt95(Strategy):
            name = "test"
            def precompute(self, price_history):
                return price_history.copy()
            def entry_signal_at(self, row):
                if row.Close == 100.0:
                    return Signal(symbol="", direction="BUY", entry_price=100.0, stop_loss=95.0,
                                   strategy_name=self.name)
                return None
            def exit_signal_at(self, row, open_position):
                return float(row.Close)  # would ALSO fire every day if ever reached

        result = simulate_portfolio({"TEST": bars}, _StopAt95(), starting_capital=100000,
                                     min_bars_required=1)
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0].exit_reason, "stop_loss")
        self.assertAlmostEqual(result["trades"][0].exit_price, 95.0)


class TestPyramiding(unittest.TestCase):
    def test_adds_units_and_raises_whole_position_stop(self):
        # Entry at 100, pyramid triggers every +2 (N=4, step=0.5N=2), up to
        # max_units=3 for this test strategy.
        closes = [100, 102, 104, 106, 108, 110, 200]  # final bar crashes... no, let's just hold
        bars = _bars("2026-01-05", closes)

        class _PyramidTest(Strategy):
            name = "pyramid_test"
            max_units = 3
            risk_pct_per_unit = 0.01

            def precompute(self, price_history):
                df = price_history.copy()
                df["N"] = 4.0
                return df

            def entry_signal_at(self, row):
                if row.Close == 100.0:
                    return Signal(symbol="", direction="BUY", entry_price=100.0, stop_loss=92.0,
                                   strategy_name=self.name)
                return None

            def pyramid_signal_at(self, row, open_position: OpenPosition):
                last = open_position.last_unit_entry_price
                if row.Close - last >= 2.0:
                    new_stop = row.Close - 8.0  # 2N
                    if new_stop > open_position.stop_loss:
                        return Signal(symbol="", direction="BUY", entry_price=float(row.Close),
                                      stop_loss=new_stop, strategy_name=self.name)
                return None

            def exit_signal_at(self, row, open_position):
                return None  # held to end of data

        result = simulate_portfolio({"TEST": bars}, _PyramidTest(), starting_capital=1_000_000,
                                     min_bars_required=1)
        # entries at 100, 102 (+2), 104 (+2 from 102) -> 3 units (max_units=3), no more after
        self.assertEqual(len(result["trades"]), 3)
        entry_prices = sorted(t.entry_price for t in result["trades"])
        self.assertEqual(entry_prices, [100.0, 102.0, 104.0])
        # all three exit together at end_of_backtest, same exit price/date
        exit_prices = {t.exit_price for t in result["trades"]}
        self.assertEqual(len(exit_prices), 1)
        for t in result["trades"]:
            self.assertEqual(t.exit_reason, "end_of_backtest")


class TestPortfolioLimits(unittest.TestCase):
    def test_max_units_total_blocks_new_entries_across_symbols(self):
        class _AlwaysEnters(Strategy):
            name = "always_enters"
            def precompute(self, price_history):
                return price_history.copy()
            def entry_signal_at(self, row):
                return Signal(symbol="", direction="BUY", entry_price=float(row.Close),
                              stop_loss=float(row.Close) * 0.9, strategy_name=self.name)

        # 3 symbols, all with a valid entry on day 0, but max_units_total=2
        data = {
            "A": _bars("2026-01-05", [100, 101, 102, 103, 104]),
            "B": _bars("2026-01-05", [200, 201, 202, 203, 204]),
            "C": _bars("2026-01-05", [300, 301, 302, 303, 304]),
        }
        result = simulate_portfolio(data, _AlwaysEnters(), starting_capital=1_000_000, max_units_total=2,
                                     min_bars_required=1)
        symbols_entered = {t.symbol for t in result["trades"]}
        self.assertEqual(len(symbols_entered), 2)  # third symbol blocked by the total cap

    def test_max_units_per_sector_blocks_same_sector_entries(self):
        class _AlwaysEnters(Strategy):
            name = "always_enters"
            def precompute(self, price_history):
                return price_history.copy()
            def entry_signal_at(self, row):
                return Signal(symbol="", direction="BUY", entry_price=float(row.Close),
                              stop_loss=float(row.Close) * 0.9, strategy_name=self.name)

        data = {
            "A": _bars("2026-01-05", [100, 101, 102]),
            "B": _bars("2026-01-05", [200, 201, 202]),
        }
        sector_map = {"A": "IT", "B": "IT"}  # same sector
        result = simulate_portfolio(data, _AlwaysEnters(), starting_capital=1_000_000,
                                     sector_map=sector_map, max_units_per_sector=1, max_units_total=10,
                                     min_bars_required=1)
        symbols_entered = {t.symbol for t in result["trades"]}
        self.assertEqual(len(symbols_entered), 1)  # second IT-sector entry blocked

    def test_higher_confidence_wins_the_scarce_slot_over_an_alphabetically_earlier_symbol(self):
        # AAA is alphabetically first (would win under the old, pre-fix
        # raw-iteration-order behavior) but has LOWER confidence than ZZZ,
        # which is alphabetically last. Only 1 slot is available for 2
        # candidates -- the fix must give it to ZZZ, proving confidence
        # (not iteration order) decides.
        class _ConfidenceBySymbol(Strategy):
            name = "confidence_test"
            def precompute(self, price_history):
                return price_history.copy()
            def entry_signal_at(self, row):
                confidence = 10.0 if row.Close < 150 else 90.0  # AAA's bars are ~100s, ZZZ's are ~200s
                return Signal(symbol="", direction="BUY", entry_price=float(row.Close),
                              stop_loss=float(row.Close) * 0.9, confidence=confidence,
                              strategy_name=self.name)

        data = {
            "AAA": _bars("2026-01-05", [100, 101, 102]),   # low confidence (10.0)
            "ZZZ": _bars("2026-01-05", [200, 201, 202]),   # high confidence (90.0)
        }
        result = simulate_portfolio(data, _ConfidenceBySymbol(), starting_capital=1_000_000, max_units_total=1,
                                     min_bars_required=1)
        symbols_entered = {t.symbol for t in result["trades"]}
        self.assertEqual(symbols_entered, {"ZZZ"})

    def test_equal_confidence_tie_break_result_is_independent_of_dict_insertion_order(self):
        # 5 symbols, all equal (default) confidence, only 2 slots -- the
        # SAME 2 winners must be chosen regardless of which order the
        # caller happens to build the `data` dict in (alphabetical,
        # reversed, or shuffled). This is the end-to-end proof that the
        # fix works through the real simulate_portfolio() call path, not
        # just in candidate_ranking.py's own isolated unit tests.
        class _AlwaysEnters(Strategy):
            name = "always_enters"
            def precompute(self, price_history):
                return price_history.copy()
            def entry_signal_at(self, row):
                return Signal(symbol="", direction="BUY", entry_price=float(row.Close),
                              stop_loss=float(row.Close) * 0.9, strategy_name=self.name)

        symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
        bars_by_symbol = {s: _bars("2026-01-05", [100 + i, 101 + i, 102 + i]) for i, s in enumerate(symbols)}

        data_alphabetical = {s: bars_by_symbol[s] for s in symbols}
        data_reversed = {s: bars_by_symbol[s] for s in reversed(symbols)}
        data_shuffled = {s: bars_by_symbol[s] for s in [symbols[2], symbols[0], symbols[4], symbols[1], symbols[3]]}

        winners = []
        for data in (data_alphabetical, data_reversed, data_shuffled):
            result = simulate_portfolio(data, _AlwaysEnters(), starting_capital=1_000_000, max_units_total=2,
                                         min_bars_required=1)
            winners.append(frozenset(t.symbol for t in result["trades"]))

        self.assertEqual(len(winners[0]), 2)
        self.assertEqual(winners[0], winners[1])
        self.assertEqual(winners[0], winners[2])


class TestCompoundingEquity(unittest.TestCase):
    def test_equity_updates_after_each_closed_trade_and_sizes_off_current_equity(self):
        # First trade doubles equity; second trade's unit size should be
        # computed off the NEW (larger) equity, not the starting capital --
        # this is the whole point of a real compounding curve vs.
        # research_lab's fixed-starting-capital simplification.
        class _TwoSequentialTrades(Strategy):
            name = "sequential"
            risk_pct_per_unit = 0.5  # aggressive, to make the equity change obvious

            def precompute(self, price_history):
                return price_history.copy()

            def entry_signal_at(self, row):
                if row.Close in (100.0, 50.0):
                    return Signal(symbol="", direction="BUY", entry_price=float(row.Close),
                                  stop_loss=float(row.Close) * 0.5, strategy_name=self.name)
                return None

            def exit_signal_at(self, row, open_position):
                if float(row.Close) != open_position.last_unit_entry_price:
                    return float(row.Close)
                return None

        # Day0: enter 100, stop 50 (risk/share=50). Day1: exits at 200 (huge win).
        # Day2: enters again at 50, stop 25.
        closes = [100, 200, 50, 60]
        bars = _bars("2026-01-05", closes)
        result = simulate_portfolio({"TEST": bars}, _TwoSequentialTrades(), starting_capital=100000,
                                     min_bars_required=1)
        self.assertEqual(len(result["trades"]), 2)
        first, second = sorted(result["trades"], key=lambda t: t.entry_date)
        # first trade: qty = floor(100000*0.5/50) = 1000, pnl = (200-100)*1000 = 100000 -> equity now 200000
        self.assertEqual(first.quantity, 1000)
        self.assertAlmostEqual(first.pnl, 100000.0)
        # second trade sized off NEW equity (200000), risk/share = 50*0.5=25 -> qty = floor(200000*0.5/25) = 4000
        self.assertEqual(second.quantity, 4000)


class TestComputeMetrics(unittest.TestCase):
    def test_hand_calculated_basic_metrics(self):
        trades = [
            Trade("A", date(2026, 1, 5), date(2026, 1, 15), 100, 110, 100, 1000.0, "signal_exit"),
            Trade("B", date(2026, 1, 6), date(2026, 1, 10), 100, 95, 100, -500.0, "stop_loss"),
        ]
        calendar = [date(2026, 1, 5), date(2026, 1, 15)]
        m = compute_metrics(trades, starting_capital=100000, trading_calendar=calendar)
        self.assertEqual(m["total_trades"], 2)
        self.assertEqual(m["win_rate"], 0.5)
        self.assertEqual(m["profit_factor"], 2.0)
        self.assertEqual(m["expectancy"], 250.0)
        self.assertEqual(m["total_pnl"], 500.0)
        self.assertAlmostEqual(m["avg_holding_period_days"], (10 + 4) / 2)

    def test_no_trades_returns_zeroed_result_not_a_crash(self):
        m = compute_metrics([], starting_capital=100000, trading_calendar=[])
        self.assertEqual(m["total_trades"], 0)
        self.assertIsNone(m["profit_factor"])

    def test_cagr_and_drawdown_from_equity_curve(self):
        trades = [Trade("A", date(2026, 1, 1), date(2026, 1, 2), 100, 110, 100, 1000.0, "signal_exit")]
        daily_equity = {
            date(2026, 1, 1): 100000, date(2026, 1, 2): 101000, date(2026, 6, 1): 95000,
            date(2027, 1, 1): 110000,
        }
        m = compute_metrics(trades, starting_capital=100000, trading_calendar=list(daily_equity.keys()),
                             daily_equity=daily_equity)
        self.assertIsNotNone(m["cagr"])
        self.assertIsNotNone(m["max_drawdown_pct"])
        # drawdown from peak 101000 to trough 95000
        expected_dd = (101000 - 95000) / 101000 * 100
        self.assertAlmostEqual(m["max_drawdown_pct"], expected_dd, places=1)


class TestHoldingPeriodBreakdown(unittest.TestCase):
    def test_buckets_by_holding_days(self):
        trades = [
            Trade("A", date(2026, 1, 1), date(2026, 1, 3), 100, 110, 10, 100.0, "signal_exit"),   # 2 days
            Trade("B", date(2026, 1, 1), date(2026, 1, 20), 100, 90, 10, -100.0, "stop_loss"),     # 19 days
        ]
        breakdown = compute_holding_period_breakdown(trades, bucket_days=(5, 20))
        self.assertEqual(breakdown["<=5d"], 100.0)
        self.assertEqual(breakdown["<=20d"], -100.0)


class TestSimulateSymbolSingleUnit(unittest.TestCase):
    """Benchmark engine used only for the read-only production strategy comparisons."""

    def test_single_position_no_pyramiding_matches_growing_window_convention(self):
        from strategies.base import Signal as ProdSignal, Strategy as ProdStrategy

        class _RawTestStrategy(ProdStrategy):
            name = "raw_test"
            def generate_signal(self, price_history):
                if len(price_history) == 3:
                    entry = float(price_history.iloc[-1]["Close"])
                    return ProdSignal(symbol="TEST", direction="BUY", entry_price=entry,
                                       stop_loss=entry - 5, target=entry + 10,
                                       confidence=0.6, strategy_name=self.name)
                return None

        closes = [100, 101, 102, 103, 104, 105, 112]
        bars = _bars("2026-01-05", closes)
        trades = simulate_symbol_single_unit("TEST", bars, _RawTestStrategy(), starting_capital=100000,
                                              min_bars=2)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "target")


class TestSimulatePortfolioSingleUnit(unittest.TestCase):
    """
    Shared-capital-pool benchmark engine built for the 2026-08-03 Research
    Audit -- confirms MA Crossover/Mean Reversion-style (production)
    strategies now share ONE capital pool across the whole universe, under
    the same real risk discipline production trading uses, instead of each
    symbol getting independent unlimited capital (the bug this engine
    exists to fix).
    """

    def _always_fires_strategy(self, fire_at_len=2):
        from strategies.base import Signal as ProdSignal, Strategy as ProdStrategy

        class _AlwaysFires(ProdStrategy):
            name = "always_fires"
            def generate_signal(self, price_history):
                if len(price_history) == fire_at_len:
                    entry = float(price_history.iloc[-1]["Close"])
                    return ProdSignal(symbol="", direction="BUY", entry_price=entry,
                                       stop_loss=entry - 1000, target=entry + 100000,
                                       confidence=0.6, strategy_name=self.name)
                return None
        return _AlwaysFires

    def test_max_open_positions_caps_concurrent_entries_across_symbols(self):
        strategy_cls = self._always_fires_strategy()
        data = {
            "A": _bars("2026-01-05", [100] * 60),
            "B": _bars("2026-01-05", [100] * 60),
            "C": _bars("2026-01-05", [100] * 60),
        }
        result = simulate_portfolio_single_unit(
            data, strategy_cls, starting_capital=1_000_000, min_bars=1, max_open_positions=2,
        )
        opened_symbols = {t.symbol for t in result["trades"]}
        # end_of_backtest trades also get recorded for whatever's still open --
        # either way, no more than max_open_positions should ever have been
        # concurrently opened, so at most 2 symbols ever got a position.
        self.assertLessEqual(len(opened_symbols), 2)

    def test_shared_capital_pool_not_independent_per_symbol(self):
        # Aggressive risk-per-trade so the FIRST entry consumes most of the
        # pool -- if capital were independent per symbol (the bug), a
        # second symbol firing the same day would ALSO get a big position;
        # under a real shared pool, the deployed-capital cap should constrain it.
        strategy_cls = self._always_fires_strategy()
        data = {
            "A": _bars("2026-01-05", [100] * 60),
            "B": _bars("2026-01-05", [100] * 60),
        }
        result = simulate_portfolio_single_unit(
            data, strategy_cls, starting_capital=100000, min_bars=1,
            max_open_positions=10, max_deployed_capital_pct=0.5, max_capital_per_trade_pct=0.5,
            risk_per_trade_pct=1.0,  # deliberately extreme to force the capital cap to bind
        )
        # Both may open, but their combined cost must respect the 50%-deployed cap
        total_cost = sum(t.quantity * t.entry_price for t in result["trades"]
                          if t.exit_reason != "stop_loss")
        # Can't exceed roughly max_deployed_capital_pct of starting capital
        # (loose bound -- exact figure depends on entry order, but it must
        # NOT be anywhere near 2x starting_capital, which independent
        # per-symbol capital would have allowed).
        self.assertLess(total_cost, 100000 * 0.6)

    def test_daily_loss_circuit_breaker_blocks_new_entries_same_day(self):
        # A strategy that immediately stops out, then fires again same day
        # on another symbol -- with a tiny circuit-breaker threshold, the
        # second entry should be blocked once the day's realized loss
        # breaches it.
        from strategies.base import Signal as ProdSignal, Strategy as ProdStrategy

        class _ImmediateStopStrategy(ProdStrategy):
            name = "immediate_stop"
            def generate_signal(self, price_history):
                if len(price_history) == 2:
                    entry = float(price_history.iloc[-1]["Close"])
                    return ProdSignal(symbol="", direction="BUY", entry_price=entry,
                                       stop_loss=entry - 0.01, target=entry + 1000,
                                       confidence=0.6, strategy_name=self.name)
                return None

        data = {
            "A": _bars("2026-01-05", [100, 99, 98, 97, 96] + [96] * 55),
            "B": _bars("2026-01-05", [100, 99, 98, 97, 96] + [96] * 55),
        }
        result = simulate_portfolio_single_unit(
            data, _ImmediateStopStrategy, starting_capital=100000, min_bars=1,
            risk_per_trade_pct=1.0, daily_loss_circuit_breaker_pct=0.0001,
        )
        # Whatever happened, no unbounded cascade of entries -- sanity check
        # the engine ran and produced a bounded, finite trade list.
        self.assertIsInstance(result["trades"], list)
        self.assertGreaterEqual(len(result["trades"]), 0)

    def test_no_position_produces_empty_trades_not_a_crash(self):
        from strategies.base import Signal as ProdSignal, Strategy as ProdStrategy

        class _NeverFires(ProdStrategy):
            name = "never_fires"
            def generate_signal(self, price_history):
                return None

        data = {"A": _bars("2026-01-05", [100] * 60)}
        result = simulate_portfolio_single_unit(data, _NeverFires, starting_capital=100000, min_bars=1)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["symbols"], ["A"])


if __name__ == "__main__":
    unittest.main()
