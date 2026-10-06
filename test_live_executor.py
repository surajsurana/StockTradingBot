"""
deployment/live_executor.py -- the only module that can move money.

The properties under test: the guard cannot be bypassed, nothing raises, and every order that
reaches the broker is recorded BEFORE it is sent. That last one matters more than it looks: a crash
between sending and recording leaves a real position this program has no memory of, which
reconciliation would later see as foreign and could close.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.live_executor import (OrderOutcome, order_log_path, orders_placed_today,
                                      place_live_order)


def _settings(**over):
    base = dict(LIVE_TRADING=True, KITE_API_KEY="key", KITE_ACCESS_TOKEN="token",
                LIVE_MAX_ORDER_VALUE_RUPEES=50_000.0, LIVE_MAX_EXPOSURE_RUPEES=200_000.0,
                LIVE_MAX_ORDERS_PER_DAY=20)
    base.update(over)
    return SimpleNamespace(**base)


def _record(status=DeploymentStatus.PILOT_LIVE):
    return StrategyRecord(strategy_key="alpha", display_name="Alpha", strategy_family="fam",
                          research_verdict=ResearchVerdict.PASS, deployment_status=status)


class _FakeEngine:
    """Stands in for ExecutionEngine. Records what it was asked to do; never touches a network."""
    def __init__(self, result=None, raises=None):
        self.result = result if result is not None else {"status": "success", "data": {"order_id": "250001"},
                                                          "price": 101.5}
        self.raises = raises
        self.calls = []

    def place_order(self, trade):
        self.calls.append(trade)
        if self.raises:
            raise self.raises
        return self.result


def _place(state_dir, engine=None, settings=None, record=None, **over):
    kwargs = dict(settings=settings or _settings(), record=record or _record(), state_dir=state_dir,
                  symbol="RELIANCE.NS", side="BUY", quantity=10, reference_price=100.0,
                  strategy_key="alpha", engine=engine if engine is not None else _FakeEngine())
    kwargs.update(over)
    return place_live_order(**kwargs)


def _log(state_dir):
    path = order_log_path(state_dir)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestTheGuardCannotBeBypassed(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_refused_order_never_reaches_the_broker(self):
        engine = _FakeEngine()
        out = _place(self.d, engine=engine, settings=_settings(LIVE_TRADING=False))
        self.assertFalse(out.placed)
        self.assertEqual(engine.calls, [])                    # nothing sent
        self.assertEqual([r["stage"] for r in _log(self.d)], ["refused"])

    def test_a_paper_strategy_cannot_place_an_order(self):
        engine = _FakeEngine()
        out = _place(self.d, engine=engine, record=_record(DeploymentStatus.PAPER_TRADING))
        self.assertFalse(out.placed)
        self.assertEqual(engine.calls, [])

    def test_the_kill_switch_stops_it(self):
        from deployment.live_guard import KILL_SWITCH_FILENAME
        open(os.path.join(self.d, KILL_SWITCH_FILENAME), "w").close()
        engine = _FakeEngine()
        self.assertFalse(_place(self.d, engine=engine).placed)
        self.assertEqual(engine.calls, [])

    def test_an_order_over_the_value_cap_is_refused(self):
        engine = _FakeEngine()
        out = _place(self.d, engine=engine, quantity=10_000, reference_price=100.0)   # Rs10,00,000
        self.assertFalse(out.placed)
        self.assertEqual(engine.calls, [])
        self.assertTrue(any("per-order cap" in r for r in out.reasons))

    def test_losing_pilot_eligibility_stops_further_orders(self):
        engine = _FakeEngine()
        stale = SimpleNamespace(eligible=False, reasons=["Only 3 paper trades recorded."])
        self.assertFalse(_place(self.d, engine=engine, eligibility=stale).placed)
        self.assertEqual(engine.calls, [])

    def test_the_daily_cap_counts_orders_already_sent_today(self):
        engine = _FakeEngine()
        for _ in range(20):
            _place(self.d, engine=engine)
        self.assertEqual(len(engine.calls), 20)
        blocked = _place(self.d, engine=engine)
        self.assertFalse(blocked.placed)
        self.assertEqual(len(engine.calls), 20)               # the 21st never went
        self.assertTrue(any("at the cap" in r for r in blocked.reasons))


class TestTheHappyPath(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_successful_order_returns_its_id_and_real_fill_price(self):
        out = _place(self.d)
        self.assertTrue(out.placed, out.reasons)
        self.assertEqual(out.order_id, "250001")
        self.assertEqual(out.fill_price, 101.5)               # the ACTUAL fill, not the reference
        self.assertTrue(bool(out))

    def test_the_broker_is_given_the_right_side_quantity_and_symbol(self):
        engine = _FakeEngine()
        _place(self.d, engine=engine, side="SELL", quantity=7, symbol="TCS.NS", reference_price=3000.0)
        trade = engine.calls[0]
        self.assertEqual((trade.signal.symbol, trade.signal.direction, trade.quantity), ("TCS.NS", "SELL", 7))
        self.assertEqual(trade.capital_deployed, 21_000.0)


class TestTheRecordIsWrittenBeforeTheOrderIsSent(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_sent_is_logged_before_the_broker_call(self):
        seen = {}

        class Watcher(_FakeEngine):
            def place_order(self, trade):
                seen["log_at_send_time"] = [r["stage"] for r in _log(self.state_dir)]
                return super().place_order(trade)

        engine = Watcher()
        engine.state_dir = self.d
        _place(self.d, engine=engine)
        self.assertIn("sent", seen["log_at_send_time"])        # already recorded when the broker was called

    def test_a_successful_order_logs_both_sent_and_accepted(self):
        _place(self.d)
        self.assertEqual([r["stage"] for r in _log(self.d)], ["sent", "accepted"])
        self.assertEqual(_log(self.d)[-1]["order_id"], "250001")

    def test_if_the_log_cannot_be_written_no_order_is_sent(self):
        engine = _FakeEngine()
        with patch("deployment.live_executor._append_log", side_effect=OSError("read-only disk")):
            out = _place(self.d, engine=engine)
        self.assertFalse(out.placed)
        self.assertEqual(engine.calls, [])                     # refused rather than sent unrecorded
        self.assertTrue(any("order log" in r for r in out.reasons))


class TestNothingRaises(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_broker_exception_becomes_a_refusal(self):
        out = _place(self.d, engine=_FakeEngine(raises=ConnectionError("network down")))
        self.assertFalse(out.placed)
        self.assertTrue(any("network down" in r for r in out.reasons))
        self.assertEqual([r["stage"] for r in _log(self.d)], ["sent", "failed"])

    def test_a_broker_rejection_is_reported_with_its_message(self):
        rejected = {"status": "error", "message": "Insufficient funds"}
        out = _place(self.d, engine=_FakeEngine(result=rejected))
        self.assertFalse(out.placed)
        self.assertTrue(any("Insufficient funds" in r for r in out.reasons))
        self.assertEqual([r["stage"] for r in _log(self.d)], ["sent", "rejected"])

    def test_a_malformed_broker_response_does_not_raise(self):
        for junk in (None, {}, [], "not a dict", 7, {"status": "success", "price": "n/a"}):
            out = _place(tempfile.mkdtemp(), engine=_FakeEngine(result=junk))
            self.assertIsInstance(out, OrderOutcome)

    def test_an_unparseable_fill_price_does_not_undo_an_accepted_order(self):
        out = _place(self.d, engine=_FakeEngine(result={"status": "success", "price": "n/a",
                                                       "data": {"order_id": "9"}}))
        self.assertTrue(out.placed)                            # the order DID go
        self.assertIsNone(out.fill_price)

    def test_accepted_without_an_order_id_still_counts_as_placed(self):
        # claiming not-placed here would invite the caller to retry and double the real position
        out = _place(self.d, engine=_FakeEngine(result={"status": "success"}))
        self.assertTrue(out.placed)
        self.assertEqual(out.order_id, "")
        self.assertTrue(any("no order id" in r for r in out.reasons))


class TestOrdersPlacedToday(unittest.TestCase):
    def test_counts_only_todays_sent_rows(self):
        d = tempfile.mkdtemp()
        rows = [{"at": "2026-10-06T10:00:00", "stage": "sent"},
                {"at": "2026-10-06T10:01:00", "stage": "refused"},   # never reached the broker
                {"at": "2026-10-05T10:00:00", "stage": "sent"}]      # yesterday
        with open(order_log_path(d), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.assertEqual(orders_placed_today(d, "2026-10-06"), 1)

    def test_a_missing_or_unreadable_log_counts_zero(self):
        self.assertEqual(orders_placed_today(tempfile.mkdtemp()), 0)
        d = tempfile.mkdtemp()
        with open(order_log_path(d), "w", encoding="utf-8") as f:
            f.write("{not json\n")
        self.assertEqual(orders_placed_today(d), 0)


if __name__ == "__main__":
    unittest.main()
