"""
Unit tests for pool_d/engine.py's process_tick() -- the core live-tick
logic for Pool D, the intraday paper-trading pool. Uses a fully
controllable test-double strategy (not the real
VwapExtensionExhaustionFadeStrategy) so entry timing is deterministic,
covering sizing, stop/target exits, force-square-off, and the
missed-poll robustness (a stop hit on an EARLIER bar than the latest
must still be caught, not missed). Run with:

    python test_pool_d.py
"""

import datetime
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import pool_d.state as pool_d_state
from research_lab.base import Signal, Strategy
from pool_d.engine import RISK_PER_TRADE_PCT, process_tick
from run_pool_d import _is_market_hours


class _IsolatedStateTestCase(unittest.TestCase):
    """process_tick() calls pool_d.state.append_trade() on every exit --
    unpatched, that writes real trades.jsonl entries into the actual
    deployment/state/pool_d/ directory (confirmed: running this suite
    once polluted that file with fake "SYM" trades). Every test class
    that exercises an exit path inherits this to redirect append_trade()
    at its two module-level path constants, same pattern as
    test_deployment.py's own patch.object(pte, "PAPER_TRADING_STATE_DIR", ...)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher_dir = patch.object(pool_d_state, "POOL_D_STATE_DIR", self.tmpdir)
        self.patcher_trades = patch.object(
            pool_d_state, "POOL_D_TRADES_PATH", f"{self.tmpdir}/trades.jsonl")
        self.patcher_dir.start()
        self.patcher_trades.start()

    def tearDown(self):
        self.patcher_dir.stop()
        self.patcher_trades.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class _FixedEntryStrategy(Strategy):
    """Fires exactly ONE fixed BUY signal the first time it's called with
    at least `fire_after_bars` bars, never again after that (mimics a
    real strategy's own state-transition-only firing, so process_tick()'s
    own "position already open -> skip generate_signal" gating is what's
    actually under test, not this double re-firing every tick)."""
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


class _NeverFiresStrategy(Strategy):
    name = "never_fires_test_strategy"

    def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
        return None


def _bars(timestamps_and_ohlc):
    """timestamps_and_ohlc: list of (hh, mm, open, high, low, close) tuples,
    all on 2026-09-07. Volume is a constant placeholder (unused by these tests)."""
    idx = [pd.Timestamp(2026, 9, 7, hh, mm) for hh, mm, *_ in timestamps_and_ohlc]
    rows = [{"Open": o, "High": h, "Low": lo, "Close": c, "Volume": 1000}
            for _, _, o, h, lo, c in timestamps_and_ohlc]
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def _base_state():
    return {"context_by_symbol": {}, "cash_by_symbol": {}, "positions": {},
            "trades_today_by_symbol": {}, "realized_pnl_today_by_symbol": {}}


class TestEntry(_IsolatedStateTestCase):
    def test_entry_opens_a_correctly_sized_position(self):
        state = _base_state()
        bars = _bars([(9, 15, 100, 101, 99, 100)])
        strategy = _FixedEntryStrategy(entry_price=100.0, stop_loss=95.0, target=110.0)
        now = datetime.datetime(2026, 9, 7, 9, 20)
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                              now=now, strategy=strategy)
        self.assertEqual(len(result["new_entries"]), 1)
        pos = state["positions"]["SYM"]
        self.assertAlmostEqual(pos["entry_price"], 100.0)
        # risk_per_share = 100-95 = 5; quantity = (100000*0.01)/5 = 200
        expected_qty = int((100000.0 * RISK_PER_TRADE_PCT) / 5.0)
        self.assertEqual(pos["quantity"], expected_qty)

    def test_no_entry_when_strategy_returns_none(self):
        state = _base_state()
        bars = _bars([(9, 15, 100, 101, 99, 100)])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                              now=datetime.datetime(2026, 9, 7, 9, 20), strategy=_NeverFiresStrategy())
        self.assertEqual(result["new_entries"], [])
        self.assertNotIn("SYM", state["positions"])

    def test_no_new_entries_once_in_the_force_square_off_window(self):
        state = _base_state()
        bars = _bars([(15, 20, 100, 101, 99, 100)])
        strategy = _FixedEntryStrategy()
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                              now=datetime.datetime(2026, 9, 7, 15, 26), strategy=strategy)
        self.assertEqual(result["new_entries"], [])
        self.assertNotIn("SYM", state["positions"])

    def test_no_entry_when_max_trades_per_day_already_reached(self):
        state = _base_state()
        state["trades_today_by_symbol"]["SYM"] = 3   # RiskParameters default max_trades_per_day=3
        state["realized_pnl_today_by_symbol"]["SYM"] = 0.0
        bars = _bars([(9, 15, 100, 101, 99, 100)])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                              now=datetime.datetime(2026, 9, 7, 9, 20), strategy=_FixedEntryStrategy())
        self.assertEqual(result["new_entries"], [])


class TestExit(_IsolatedStateTestCase):
    def _open_position(self, state, entry_hh_mm=(9, 20), entry_price=100.0, stop_loss=95.0, target=110.0):
        entry_ts = pd.Timestamp(2026, 9, 7, *entry_hh_mm)
        state["positions"]["SYM"] = {
            "entry_price": entry_price, "stop_loss": stop_loss, "target": target, "quantity": 100,
            "direction": "BUY", "entry_hour": entry_ts.hour + entry_ts.minute / 60,
            "entry_timestamp": entry_ts.isoformat(),
        }
        state["cash_by_symbol"]["SYM"] = 100000.0
        state["realized_pnl_today_by_symbol"]["SYM"] = 0.0
        state["trades_today_by_symbol"]["SYM"] = 1

    def test_stop_loss_exit(self):
        state = _base_state()
        self._open_position(state)
        bars = _bars([(9, 20, 100, 101, 99, 100), (9, 25, 99, 99.5, 94, 94.5)])  # Low breaches stop(95)
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                              now=datetime.datetime(2026, 9, 7, 9, 30), strategy=_NeverFiresStrategy())
        self.assertEqual(len(result["new_exits"]), 1)
        self.assertEqual(result["new_exits"][0]["reason"], "stop_loss")
        self.assertAlmostEqual(result["new_exits"][0]["exit_price"], 95.0)
        self.assertNotIn("SYM", state["positions"])

    def test_target_exit(self):
        state = _base_state()
        self._open_position(state)
        bars = _bars([(9, 20, 100, 101, 99, 100), (9, 25, 105, 111, 104, 110.5)])  # High reaches target(110)
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                              now=datetime.datetime(2026, 9, 7, 9, 30), strategy=_NeverFiresStrategy())
        self.assertEqual(len(result["new_exits"]), 1)
        self.assertEqual(result["new_exits"][0]["reason"], "target")
        self.assertAlmostEqual(result["new_exits"][0]["exit_price"], 110.0)

    def test_no_exit_when_neither_stop_nor_target_hit_and_not_yet_square_off_time(self):
        state = _base_state()
        self._open_position(state)
        bars = _bars([(9, 20, 100, 101, 99, 100), (9, 25, 100, 102, 98, 101)])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                              now=datetime.datetime(2026, 9, 7, 9, 30), strategy=_NeverFiresStrategy())
        self.assertEqual(result["new_exits"], [])
        self.assertIn("SYM", state["positions"])

    def test_force_square_off_closes_an_otherwise_untouched_position(self):
        state = _base_state()
        self._open_position(state)
        bars = _bars([(9, 20, 100, 101, 99, 100), (15, 25, 102, 103, 101, 102.5)])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                              now=datetime.datetime(2026, 9, 7, 15, 26), strategy=_NeverFiresStrategy())
        self.assertEqual(len(result["new_exits"]), 1)
        self.assertEqual(result["new_exits"][0]["reason"], "eod_square_off")
        self.assertAlmostEqual(result["new_exits"][0]["exit_price"], 102.5)

    def test_missed_poll_still_catches_an_earlier_stop_hit_not_the_latest_bar(self):
        """The core robustness guarantee: if a poll was missed and the
        stop was actually hit TWO bars ago (not reflected in the latest
        bar, which has since recovered), the scan-from-entry logic must
        still catch and exit at the ORIGINAL stop price, not silently
        skip past it because the CURRENT bar looks fine."""
        state = _base_state()
        self._open_position(state, stop_loss=95.0)
        bars = _bars([
            (9, 20, 100, 101, 99, 100),     # entry bar
            (9, 25, 99, 99.5, 94, 94.5),    # stop breached here (missed poll)
            (9, 30, 96, 105, 96, 104),      # price has since recovered -- latest bar looks fine
        ])
        result = process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                              now=datetime.datetime(2026, 9, 7, 9, 35), strategy=_NeverFiresStrategy())
        self.assertEqual(len(result["new_exits"]), 1)
        self.assertEqual(result["new_exits"][0]["reason"], "stop_loss")
        self.assertAlmostEqual(result["new_exits"][0]["exit_price"], 95.0)

    def test_pnl_and_cash_update_correctly_on_exit(self):
        state = _base_state()
        self._open_position(state, entry_price=100.0, stop_loss=95.0)
        bars = _bars([(9, 20, 100, 101, 99, 100), (9, 25, 99, 99.5, 94, 94.5)])
        process_tick(state, {"SYM": bars}, {"SYM": {}}, capital_per_symbol=100000.0,
                    now=datetime.datetime(2026, 9, 7, 9, 30), strategy=_NeverFiresStrategy())
        # 100 qty * (95 - 100) = -500
        self.assertAlmostEqual(state["realized_pnl_today_by_symbol"]["SYM"], -500.0)
        self.assertAlmostEqual(state["cash_by_symbol"]["SYM"], 100000.0 - 500.0)
        self.assertEqual(state["trades_today_by_symbol"]["SYM"], 2)


class TestMarketHoursGate(unittest.TestCase):
    """The one safety-critical check run_pool_d.py's own module owns --
    every wasted Kite call (and every spurious trade) outside real market
    hours is avoided here, before any data is ever fetched."""

    def test_true_during_market_hours_on_a_weekday(self):
        self.assertTrue(_is_market_hours(datetime.datetime(2026, 9, 7, 11, 0)))   # Monday, 11am

    def test_true_at_the_open_boundary(self):
        self.assertTrue(_is_market_hours(datetime.datetime(2026, 9, 7, 9, 15)))

    def test_true_at_the_close_boundary(self):
        self.assertTrue(_is_market_hours(datetime.datetime(2026, 9, 7, 15, 30)))

    def test_false_before_market_open(self):
        self.assertFalse(_is_market_hours(datetime.datetime(2026, 9, 7, 9, 0)))

    def test_false_after_market_close(self):
        self.assertFalse(_is_market_hours(datetime.datetime(2026, 9, 7, 15, 45)))

    def test_false_on_saturday(self):
        self.assertFalse(_is_market_hours(datetime.datetime(2026, 9, 12, 11, 0)))   # a Saturday

    def test_false_on_sunday(self):
        self.assertFalse(_is_market_hours(datetime.datetime(2026, 9, 13, 11, 0)))   # a Sunday


if __name__ == "__main__":
    unittest.main()
