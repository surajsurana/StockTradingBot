"""
Unit tests for pool_d/engine.py's process_tick() -- the core live-tick
logic for Pool D, the intraday paper-trading pool, on its single shared
book. Uses a fully controllable test-double strategy (not the real
VwapExtensionExhaustionFadeStrategy) so entry timing is deterministic,
covering sizing against the shared book, cash reservation/release,
stop/target exits, force-square-off, the pool-level daily loss limit,
the missed-poll robustness (a stop hit on an EARLIER bar than the latest
must still be caught), and state migration from the old per-symbol
layout. Run with:

    python test_pool_d.py
"""

import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import pool_d.state as pool_d_state
from research_lab.base import Signal, Strategy
from pool_d.engine import RISK_PER_TRADE_PCT, process_tick
from run_pool_d import _is_market_hours

CAPITAL = 100000.0


class _IsolatedStateTestCase(unittest.TestCase):
    """process_tick() calls pool_d.state.append_trade() on every exit --
    redirected to a temp dir so the real deployment/state/pool_d/ is
    never touched (same pattern as test_deployment.py)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patchers = [
            patch.object(pool_d_state, "POOL_D_STATE_DIR", self.tmpdir),
            patch.object(pool_d_state, "POOL_D_TRADES_PATH", os.path.join(self.tmpdir, "trades.jsonl")),
            patch.object(pool_d_state, "POOL_D_PORTFOLIO_PATH", os.path.join(self.tmpdir, "portfolio.json")),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class _FixedEntryStrategy(Strategy):
    """Fires exactly ONE fixed signal the first time it's called with at
    least `fire_after_bars` bars, never again after that."""
    name = "fixed_entry_test_strategy"

    def __init__(self, fire_after_bars=1, entry_price=100.0, stop_loss=95.0, target=110.0, direction="BUY"):
        self.fire_after_bars = fire_after_bars
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.target = target
        self.direction = direction
        self.already_fired = False

    def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
        if self.already_fired or len(todays_bars_so_far) < self.fire_after_bars:
            return None
        self.already_fired = True
        return Signal(symbol="", direction=self.direction, entry_price=self.entry_price,
                      stop_loss=self.stop_loss, target=self.target, confidence=0.5, strategy_name=self.name)


class _AlwaysFiresStrategy(_FixedEntryStrategy):
    """Fires on EVERY symbol it is asked about (never marks itself fired)."""

    def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
        return Signal(symbol="", direction=self.direction, entry_price=self.entry_price,
                      stop_loss=self.stop_loss, target=self.target, confidence=0.5, strategy_name=self.name)


class _NeverFiresStrategy(Strategy):
    name = "never_fires_test_strategy"

    def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
        return None


def _bars(timestamps_and_ohlc):
    """timestamps_and_ohlc: list of (hh, mm, open, high, low, close) tuples, all on 2026-09-07."""
    idx = [pd.Timestamp(2026, 9, 7, hh, mm) for hh, mm, *_ in timestamps_and_ohlc]
    rows = [{"Open": o, "High": h, "Low": lo, "Close": c, "Volume": 1000}
            for _, _, o, h, lo, c in timestamps_and_ohlc]
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def _base_state(cash=CAPITAL):
    state = pool_d_state.new_state(CAPITAL)
    state["cash"] = cash
    return state


def _open_position(state, symbol="SYM", entry_hh_mm=(9, 20), entry_price=100.0, stop_loss=95.0, target=110.0,
                   quantity=100, direction="BUY"):
    entry_ts = pd.Timestamp(2026, 9, 7, *entry_hh_mm)
    state["positions"][symbol] = {
        "entry_price": entry_price, "stop_loss": stop_loss, "target": target, "quantity": quantity,
        "direction": direction, "entry_hour": entry_ts.hour + entry_ts.minute / 60,
        "entry_timestamp": entry_ts.isoformat(),
    }
    state["cash"] -= entry_price * quantity
    state["trades_today_by_symbol"][symbol] = 1


class TestEntry(_IsolatedStateTestCase):
    def test_entry_sizes_off_the_shared_book_and_reserves_cash(self):
        state = _base_state()
        bars = _bars([(9, 15, 100, 101, 99, 100)])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, now=datetime.datetime(2026, 9, 7, 9, 20),
                              strategy=_FixedEntryStrategy(entry_price=100.0, stop_loss=95.0, target=110.0))
        self.assertEqual(len(result["new_entries"]), 1)
        pos = state["positions"]["SYM"]
        expected_qty = int((CAPITAL * RISK_PER_TRADE_PCT) / 5.0)   # 1,000 risk / 5 per share = 200
        self.assertEqual(pos["quantity"], expected_qty)
        self.assertAlmostEqual(state["cash"], CAPITAL - 100.0 * expected_qty)

    def test_quantity_is_capped_by_free_cash(self):
        state = _base_state(cash=5000.0)   # only Rs.5,000 free
        bars = _bars([(9, 15, 100, 101, 99, 100)])
        process_tick(state, {"SYM": bars}, {"SYM": {}}, now=datetime.datetime(2026, 9, 7, 9, 20),
                     strategy=_FixedEntryStrategy(entry_price=100.0, stop_loss=95.0))
        self.assertEqual(state["positions"]["SYM"]["quantity"], 50)    # not the 200 the risk formula wants
        self.assertAlmostEqual(state["cash"], 0.0)

    def test_no_entry_when_no_cash_is_free(self):
        state = _base_state(cash=50.0)
        bars = _bars([(9, 15, 100, 101, 99, 100)])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, now=datetime.datetime(2026, 9, 7, 9, 20),
                              strategy=_FixedEntryStrategy(entry_price=100.0, stop_loss=95.0))
        self.assertEqual(result["new_entries"], [])

    def test_book_is_shared_across_symbols(self):
        state = _base_state(cash=30000.0)
        bars = {s: _bars([(9, 15, 100, 101, 99, 100)]) for s in ("A", "B", "C")}
        result = process_tick(state, bars, {s: {} for s in bars}, now=datetime.datetime(2026, 9, 7, 9, 20),
                              strategy=_AlwaysFiresStrategy(entry_price=100.0, stop_loss=95.0))
        # 200 x 100 = 20,000 for the first, then only 10,000 left -> 100 shares for the second, none for the third
        quantities = [e["quantity"] for e in result["new_entries"]]
        self.assertEqual(quantities, [200, 100])
        self.assertAlmostEqual(state["cash"], 0.0)

    def test_no_entry_when_strategy_returns_none(self):
        state = _base_state()
        result = process_tick(state, {"SYM": _bars([(9, 15, 100, 101, 99, 100)])}, {"SYM": {}},
                              now=datetime.datetime(2026, 9, 7, 9, 20), strategy=_NeverFiresStrategy())
        self.assertEqual(result["new_entries"], [])

    def test_no_new_entries_once_in_the_force_square_off_window(self):
        state = _base_state()
        result = process_tick(state, {"SYM": _bars([(15, 20, 100, 101, 99, 100)])}, {"SYM": {}},
                              now=datetime.datetime(2026, 9, 7, 15, 26), strategy=_FixedEntryStrategy())
        self.assertEqual(result["new_entries"], [])

    def test_no_entry_when_symbol_hit_max_trades_per_day(self):
        state = _base_state()
        state["trades_today_by_symbol"]["SYM"] = 3   # RiskParameters default max_trades_per_day=3
        result = process_tick(state, {"SYM": _bars([(9, 15, 100, 101, 99, 100)])}, {"SYM": {}},
                              now=datetime.datetime(2026, 9, 7, 9, 20), strategy=_FixedEntryStrategy())
        self.assertEqual(result["new_entries"], [])

    def test_pool_level_daily_loss_limit_blocks_every_symbol(self):
        state = _base_state()
        state["realized_pnl_today"] = -2500.0   # beyond 2% of the Rs.1,00,000 book
        bars = {s: _bars([(9, 15, 100, 101, 99, 100)]) for s in ("A", "B")}
        result = process_tick(state, bars, {s: {} for s in bars}, now=datetime.datetime(2026, 9, 7, 9, 20),
                              strategy=_AlwaysFiresStrategy())
        self.assertEqual(result["new_entries"], [])


class TestExit(_IsolatedStateTestCase):
    def test_stop_loss_exit_releases_cash_and_books_the_loss(self):
        state = _base_state()
        _open_position(state)   # 100 @ 100, cash now 90,000
        bars = _bars([(9, 20, 100, 101, 99, 100), (9, 25, 99, 99.5, 94, 94.5)])   # Low breaches stop(95)
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, now=datetime.datetime(2026, 9, 7, 9, 30),
                              strategy=_NeverFiresStrategy())
        self.assertEqual(result["new_exits"][0]["reason"], "stop_loss")
        self.assertAlmostEqual(result["new_exits"][0]["exit_price"], 95.0)
        self.assertAlmostEqual(state["cash"], CAPITAL - 500.0)        # 10,000 released, minus the 500 loss
        self.assertAlmostEqual(state["realized_pnl_today"], -500.0)
        self.assertEqual(state["trades_today_by_symbol"]["SYM"], 2)
        self.assertNotIn("SYM", state["positions"])

    def test_target_exit(self):
        state = _base_state()
        _open_position(state)
        bars = _bars([(9, 20, 100, 101, 99, 100), (9, 25, 105, 111, 104, 110.5)])   # High reaches target(110)
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, now=datetime.datetime(2026, 9, 7, 9, 30),
                              strategy=_NeverFiresStrategy())
        self.assertEqual(result["new_exits"][0]["reason"], "target")
        self.assertAlmostEqual(state["cash"], CAPITAL + 1000.0)

    def test_short_exit_pnl_sign(self):
        state = _base_state()
        _open_position(state, entry_price=100.0, stop_loss=105.0, target=90.0, direction="SELL")
        bars = _bars([(9, 20, 100, 101, 99, 100), (9, 25, 95, 96, 89, 89.5)])   # Low reaches target(90)
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, now=datetime.datetime(2026, 9, 7, 9, 30),
                              strategy=_NeverFiresStrategy())
        self.assertEqual(result["new_exits"][0]["reason"], "target")
        self.assertAlmostEqual(result["new_exits"][0]["pnl"], 1000.0)
        self.assertAlmostEqual(state["cash"], CAPITAL + 1000.0)

    def test_no_exit_when_neither_stop_nor_target_hit_and_not_yet_square_off_time(self):
        state = _base_state()
        _open_position(state)
        bars = _bars([(9, 20, 100, 101, 99, 100), (9, 25, 100, 102, 98, 101)])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, now=datetime.datetime(2026, 9, 7, 9, 30),
                              strategy=_NeverFiresStrategy())
        self.assertEqual(result["new_exits"], [])
        self.assertIn("SYM", state["positions"])

    def test_force_square_off_closes_an_otherwise_untouched_position(self):
        state = _base_state()
        _open_position(state)
        bars = _bars([(9, 20, 100, 101, 99, 100), (15, 25, 102, 103, 101, 102.5)])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, now=datetime.datetime(2026, 9, 7, 15, 26),
                              strategy=_NeverFiresStrategy())
        self.assertEqual(result["new_exits"][0]["reason"], "eod_square_off")
        self.assertAlmostEqual(result["new_exits"][0]["exit_price"], 102.5)

    def test_missed_poll_still_catches_an_earlier_stop_hit_not_the_latest_bar(self):
        state = _base_state()
        _open_position(state, stop_loss=95.0)
        bars = _bars([
            (9, 20, 100, 101, 99, 100),     # entry bar
            (9, 25, 99, 99.5, 94, 94.5),    # stop breached here (missed poll)
            (9, 30, 96, 105, 96, 104),      # price has since recovered -- latest bar looks fine
        ])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, now=datetime.datetime(2026, 9, 7, 9, 35),
                              strategy=_NeverFiresStrategy())
        self.assertEqual(result["new_exits"][0]["reason"], "stop_loss")
        self.assertAlmostEqual(result["new_exits"][0]["exit_price"], 95.0)

    def test_cash_released_by_an_exit_is_available_to_an_entry_in_the_same_tick(self):
        state = _base_state(cash=0.0)
        _open_position(state, symbol="OLD", entry_price=100.0, quantity=100)   # cash now -10,000 (was reserved earlier)
        state["cash"] = 0.0
        bars = {"OLD": _bars([(9, 20, 100, 101, 99, 100), (9, 25, 105, 111, 104, 110.5)]),
                "NEW": _bars([(9, 20, 50, 51, 49, 50), (9, 25, 50, 51, 49, 50)])}

        class _FiresOnlyForNew(Strategy):
            name = "fires_for_new"

            def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
                if float(todays_bars_so_far.iloc[-1]["Close"]) != 50.0:
                    return None
                return Signal(symbol="", direction="BUY", entry_price=50.0, stop_loss=45.0, target=60.0,
                              confidence=0.5, strategy_name=self.name)

        result = process_tick(state, bars, {"OLD": {}, "NEW": {}}, now=datetime.datetime(2026, 9, 7, 9, 30),
                              strategy=_FiresOnlyForNew())
        self.assertEqual([x["symbol"] for x in result["new_exits"]], ["OLD"])
        self.assertEqual([e["symbol"] for e in result["new_entries"]], ["NEW"])
        self.assertEqual(result["new_entries"][0]["quantity"], 200)   # 11,000 released covers 200 x 50
        self.assertAlmostEqual(state["cash"], 11000.0 - 10000.0)


class TestStateMigration(_IsolatedStateTestCase):
    def test_old_per_symbol_file_becomes_one_book_consistent_with_booked_trades(self):
        with open(pool_d_state.POOL_D_TRADES_PATH, "w", encoding="utf-8") as f:
            f.write(json.dumps({"symbol": "LT", "pnl": -987.99}) + "\n")
            f.write(json.dumps({"symbol": "SBIN", "pnl": 1096.25}) + "\n")
        with open(pool_d_state.POOL_D_PORTFOLIO_PATH, "w", encoding="utf-8") as f:
            json.dump({"last_processed_date": "2026-09-10", "cash_by_symbol": {"LT": 99012.01},
                       "positions": {}, "trades_today_by_symbol": {"LT": 1},
                       "realized_pnl_today_by_symbol": {"LT": -987.99}, "context_by_symbol": {}}, f)
        state = pool_d_state.load_state(CAPITAL)
        self.assertAlmostEqual(state["cash"], CAPITAL + 108.26, places=2)
        self.assertAlmostEqual(state["starting_capital"], CAPITAL)
        self.assertAlmostEqual(state["realized_pnl_today"], -987.99)
        self.assertEqual(state["trades_today_by_symbol"], {"LT": 1})
        self.assertNotIn("cash_by_symbol", state)

    def test_fresh_state_when_no_file(self):
        state = pool_d_state.load_state(CAPITAL)
        self.assertAlmostEqual(state["cash"], CAPITAL)
        self.assertIsNone(state["last_processed_date"])


class TestMarketHoursGate(unittest.TestCase):
    def test_true_during_market_hours_on_a_weekday(self):
        self.assertTrue(_is_market_hours(datetime.datetime(2026, 9, 7, 11, 0)))

    def test_boundaries(self):
        self.assertTrue(_is_market_hours(datetime.datetime(2026, 9, 7, 9, 15)))
        self.assertTrue(_is_market_hours(datetime.datetime(2026, 9, 7, 15, 30)))
        self.assertFalse(_is_market_hours(datetime.datetime(2026, 9, 7, 9, 0)))
        self.assertFalse(_is_market_hours(datetime.datetime(2026, 9, 7, 15, 45)))

    def test_false_on_weekends(self):
        self.assertFalse(_is_market_hours(datetime.datetime(2026, 9, 12, 11, 0)))
        self.assertFalse(_is_market_hours(datetime.datetime(2026, 9, 13, 11, 0)))


if __name__ == "__main__":
    unittest.main()
