"""
Mock-based unit tests for execution/execution_engine.py's GTT stop-loss/target
logic (_place_gtt_exit, cancel_gtt, and the BUY -> GTT wiring in
_place_live_order). Run with:

    python test_execution_engine_gtt.py
"""

import json
import unittest
from unittest.mock import patch, MagicMock, ANY

from execution.execution_engine import ExecutionEngine, fetch_gtt_trigger
from risk.risk_manager import ApprovedTrade
from strategies.base import Signal


def _resp(status_code=200, json_data=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data or {}
    return m


def _buy_trade():
    signal = Signal(symbol="INFY.NS", direction="BUY", entry_price=1500.0, stop_loss=1470.0,
                     target=1560.0, confidence=0.8, strategy_name="test", reason="test")
    return ApprovedTrade(signal=signal, quantity=10, capital_deployed=15000.0)


class TestPlaceGttExit(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(live_trading=True, api_key="api_key", access_token="token")

    @patch("execution.execution_engine.requests.post")
    def test_two_leg_payload_shape(self, mock_post):
        mock_post.return_value = _resp(200, {"data": {"trigger_id": 4242}})

        gtt_id = self.engine._place_gtt_exit(_buy_trade())

        self.assertEqual(gtt_id, 4242)
        url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        self.assertEqual(url, "https://api.kite.trade/gtt/triggers")
        self.assertEqual(kwargs["data"]["type"], "two-leg")

        condition = json.loads(kwargs["data"]["condition"])
        self.assertEqual(condition["tradingsymbol"], "INFY")
        self.assertEqual(condition["trigger_values"], [1470.0, 1560.0])

        orders = json.loads(kwargs["data"]["orders"])
        self.assertEqual(len(orders), 2)
        self.assertTrue(all(o["transaction_type"] == "SELL" for o in orders))
        self.assertTrue(all(o["quantity"] == 10 for o in orders))

    @patch("execution.execution_engine.get_tick_size", return_value=0.05)
    @patch("execution.execution_engine.requests.post")
    def test_trigger_values_are_tick_rounded_not_raw_floats(self, mock_post, mock_tick):
        # Regression test: a real production trade had stop_loss/target as
        # raw, non-tick-aligned floats (ATR/swing-low math never lands on a
        # clean multiple of the tick size). The GTT's two SELL order prices
        # were tick-rounded, but condition["trigger_values"] used the raw
        # signal values directly -- Kite rejected it: "Stoploss trigger
        # price should be a multiple of tick size 0.05." The BUY had already
        # filled by the time this failed, leaving a real position with zero
        # stop-loss/target protection.
        mock_post.return_value = _resp(200, {"data": {"trigger_id": 4242}})
        signal = Signal(symbol="NTPC.NS", direction="BUY", entry_price=344.0,
                         stop_loss=333.9709881591797, target=364.05741464355,
                         confidence=0.68, strategy_name="test", reason="test")
        trade = ApprovedTrade(signal=signal, quantity=15, capital_deployed=5160.0)

        self.engine._place_gtt_exit(trade)

        kwargs = mock_post.call_args[1]
        condition = json.loads(kwargs["data"]["condition"])
        self.assertEqual(condition["trigger_values"], [333.95, 364.05])

    @patch("execution.execution_engine.requests.post")
    def test_placement_failure_raises(self, mock_post):
        mock_post.return_value = _resp(400, {"error_type": "InputException", "message": "bad request"})
        with self.assertRaises(RuntimeError):
            self.engine._place_gtt_exit(_buy_trade())


class TestPlaceLiveOrderWiresGtt(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(live_trading=True, api_key="api_key", access_token="token")

    @patch.object(ExecutionEngine, "_fetch_average_fill_price", return_value=None)
    @patch.object(ExecutionEngine, "_place_gtt_exit", return_value=999)
    @patch("execution.execution_engine.requests.post")
    def test_successful_buy_places_gtt(self, mock_post, mock_gtt, mock_fill):
        mock_post.return_value = _resp(200, {"status": "success", "data": {"order_id": "abc"}})

        result = self.engine._place_live_order(_buy_trade())

        mock_gtt.assert_called_once()
        self.assertEqual(result["gtt_id"], 999)

    @patch.object(ExecutionEngine, "_fetch_average_fill_price", return_value=None)
    @patch("execution.execution_engine.get_tick_size", return_value=0.50)
    @patch.object(ExecutionEngine, "_place_gtt_exit", return_value=999)
    @patch("execution.execution_engine.requests.post")
    def test_falls_back_to_limit_price_when_fill_price_unavailable(self, mock_post, mock_gtt, mock_tick, mock_fill):
        # entry 1500.0 * 1.015 buffer = 1522.5, rounded to the nearest 0.50 tick
        mock_post.return_value = _resp(200, {"status": "success", "data": {"order_id": "abc"}})

        result = self.engine._place_live_order(_buy_trade())

        self.assertEqual(result["price"], 1522.5)

    @patch("execution.execution_engine.get_tick_size", return_value=0.50)
    @patch("execution.execution_engine.requests.post")
    def test_failed_order_still_reports_the_attempted_price(self, mock_post, mock_tick):
        mock_post.return_value = _resp(400, {"status": "error", "message": "Tick size for this "
                                              "script is 0.50", "data": None})

        result = self.engine._place_live_order(_buy_trade())

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["price"], 1522.5)

    @patch.object(ExecutionEngine, "_fetch_average_fill_price")
    @patch.object(ExecutionEngine, "_place_gtt_exit", return_value=999)
    @patch("execution.execution_engine.requests.post")
    def test_real_fill_price_overrides_the_limit_price_estimate(self, mock_post, mock_gtt, mock_fill):
        # Regression test: a real production trade had a LIMIT price of
        # 349.15, but the actual fill (a LIMIT buy can fill at a BETTER
        # price than the limit) was 344.10 -- Telegram reported 349.15 as
        # "the price", which never matched what Kite actually shows.
        mock_post.return_value = _resp(200, {"status": "success", "data": {"order_id": "abc"}})
        mock_fill.return_value = 344.10

        result = self.engine._place_live_order(_buy_trade())

        self.assertEqual(result["price"], 344.10)

    @patch.object(ExecutionEngine, "_fetch_average_fill_price", return_value=None)
    @patch.object(ExecutionEngine, "_place_gtt_exit", side_effect=RuntimeError("GTT endpoint down"))
    @patch("execution.execution_engine.requests.post")
    def test_gtt_failure_does_not_fail_the_buy(self, mock_post, mock_gtt, mock_fill):
        """The BUY already filled -- a GTT placement failure shouldn't make
        place_order look like the whole trade failed. It should be surfaced
        (gtt_id is None) so the position is known to be missing its safety net."""
        mock_post.return_value = _resp(200, {"status": "success", "data": {"order_id": "abc"}})

        result = self.engine._place_live_order(_buy_trade())

        self.assertEqual(result["status"], "success")
        self.assertIsNone(result["gtt_id"])

    @patch.object(ExecutionEngine, "_fetch_average_fill_price", return_value=None)
    @patch.object(ExecutionEngine, "_place_gtt_exit")
    @patch("execution.execution_engine.requests.post")
    def test_sell_order_never_triggers_gtt_placement(self, mock_post, mock_gtt, mock_fill):
        mock_post.return_value = _resp(200, {"status": "success", "data": {"order_id": "abc"}})
        sell_signal = Signal(symbol="INFY.NS", direction="SELL", entry_price=1500.0, stop_loss=1500.0,
                              target=1500.0, confidence=0.8, strategy_name="test", reason="exit")
        trade = ApprovedTrade(signal=sell_signal, quantity=10, capital_deployed=15000.0)

        self.engine._place_live_order(trade)

        mock_gtt.assert_not_called()


class TestFetchAverageFillPrice(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(live_trading=True, api_key="api_key", access_token="token")

    @patch("execution.execution_engine.requests.get")
    def test_returns_average_price_from_latest_status_update(self, mock_get):
        mock_get.return_value = _resp(200, {"data": [
            {"status": "OPEN", "average_price": 0},
            {"status": "COMPLETE", "average_price": 344.10},
        ]})

        price = self.engine._fetch_average_fill_price("order123")

        self.assertEqual(price, 344.10)

    @patch("execution.execution_engine.requests.get")
    def test_still_pending_returns_none(self, mock_get):
        mock_get.return_value = _resp(200, {"data": [
            {"status": "OPEN", "average_price": 0},
        ]})

        self.assertIsNone(self.engine._fetch_average_fill_price("order123"))

    @patch("execution.execution_engine.requests.get")
    def test_lookup_failure_returns_none_not_an_exception(self, mock_get):
        mock_get.return_value = _resp(500, {"error_type": "GeneralException"})

        self.assertIsNone(self.engine._fetch_average_fill_price("order123"))


class TestIsOrderComplete(unittest.TestCase):
    """
    Regression tests for a real incident: monitor_positions.py treated
    Kite's order-PLACEMENT response ("status": "success", meaning only
    "accepted") as proof an early-exit SELL had actually filled, cancelled
    the position's GTT immediately, and the LIMIT order (mispriced off a
    stale entry_price -- a separate bug) then sat open/unfilled for hours
    with the position completely unprotected. is_order_complete() checks
    the order's REAL status via Kite's own order-status endpoint.
    """

    def setUp(self):
        self.engine = ExecutionEngine(live_trading=True, api_key="api_key", access_token="token")

    @patch("execution.execution_engine.requests.get")
    def test_complete_order_returns_true(self, mock_get):
        mock_get.return_value = _resp(200, {"data": [
            {"status": "OPEN"}, {"status": "COMPLETE"},
        ]})
        self.assertTrue(self.engine.is_order_complete("order123"))

    @patch("execution.execution_engine.requests.get")
    def test_still_open_returns_false(self, mock_get):
        # The exact real-incident case: a LIMIT order accepted by Kite but
        # not yet (or never) filled.
        mock_get.return_value = _resp(200, {"data": [
            {"status": "OPEN"},
        ]})
        self.assertFalse(self.engine.is_order_complete("order123"))

    @patch("execution.execution_engine.requests.get")
    def test_rejected_order_returns_false(self, mock_get):
        mock_get.return_value = _resp(200, {"data": [
            {"status": "REJECTED"},
        ]})
        self.assertFalse(self.engine.is_order_complete("order123"))

    @patch("execution.execution_engine.requests.get")
    def test_lookup_failure_returns_false_not_an_exception(self, mock_get):
        mock_get.return_value = _resp(500, {"error_type": "GeneralException"})
        self.assertFalse(self.engine.is_order_complete("order123"))


class TestCancelGtt(unittest.TestCase):
    @patch("execution.execution_engine.requests.delete")
    def test_cancel_sends_delete_to_correct_url(self, mock_delete):
        mock_delete.return_value = _resp(200, {"status": "success"})
        engine = ExecutionEngine(live_trading=True, api_key="api_key", access_token="token")

        engine.cancel_gtt(4242)

        called_url = mock_delete.call_args[0][0]
        self.assertEqual(called_url, "https://api.kite.trade/gtt/triggers/4242")


class TestReplaceGtt(unittest.TestCase):
    """Used by the trailing stop (risk/trailing_stop.py, wired in via
    monitor_positions.py) to move a position's stop-loss up -- Kite has no
    "modify trigger price" endpoint, only cancel + create. Places the NEW
    GTT first and only cancels the OLD one on success -- see
    test_new_placement_failure_leaves_old_gtt_untouched for the real
    incident (2026-07-20) this ordering fixes."""

    def setUp(self):
        self.engine = ExecutionEngine(live_trading=True, api_key="api_key", access_token="token")

    @patch("execution.execution_engine.requests.post")
    @patch("execution.execution_engine.requests.delete")
    def test_places_new_then_cancels_old_gtt(self, mock_delete, mock_post):
        mock_delete.return_value = _resp(200, {"status": "success"})
        mock_post.return_value = _resp(200, {"data": {"trigger_id": 5555}})
        # a trailing-stop-ratcheted trade: stop raised to 1515 (locking in a gain), target unchanged
        signal = Signal(symbol="INFY.NS", direction="BUY", entry_price=1500.0, stop_loss=1515.0,
                         target=1650.0, confidence=0.7, strategy_name="trailing_stop", reason="ratchet")
        trade = ApprovedTrade(signal=signal, quantity=10, capital_deployed=15000.0)

        new_gtt_id = self.engine.replace_gtt(4242, trade)

        mock_delete.assert_called_once_with("https://api.kite.trade/gtt/triggers/4242", headers=ANY)
        self.assertEqual(new_gtt_id, 5555)
        condition = json.loads(mock_post.call_args[1]["data"]["condition"])
        self.assertEqual(condition["trigger_values"], [1515.0, 1650.0])

    @patch("execution.execution_engine.requests.post")
    @patch("execution.execution_engine.requests.delete")
    def test_new_placement_failure_leaves_old_gtt_untouched(self, mock_delete, mock_post):
        # Regression test for a real incident: 3 live positions
        # (GESHIP.NS, KPIL.NS, NTPC.NS) lost ALL stop-loss/target protection
        # when a cancel-then-place ordering cancelled the old GTT
        # successfully, then the new placement failed (Kite rejected it --
        # "Trigger prices must bracket current price", since price had
        # moved since the new stop was computed from a past peak). The
        # position was left with NO active GTT until manually restored.
        # Placing first means this failure must never reach the delete call
        # at all.
        mock_post.return_value = _resp(400, {"error_type": "InputException",
                                              "message": "Trigger prices must bracket current price"})

        with self.assertRaises(RuntimeError):
            self.engine.replace_gtt(4242, _buy_trade())

        mock_delete.assert_not_called()


class TestFetchGttTrigger(unittest.TestCase):
    @patch("execution.execution_engine.requests.get")
    def test_returns_stop_loss_and_target_from_trigger_values(self, mock_get):
        mock_get.return_value = _resp(200, {"data": {"condition": {"trigger_values": [339.35, 410.70]}}})

        result = fetch_gtt_trigger(327629126, "api_key", "token")

        self.assertEqual(result, {"stop_loss": 339.35, "target": 410.70})

    @patch("execution.execution_engine.requests.get")
    def test_missing_trigger_returns_none(self, mock_get):
        mock_get.return_value = _resp(404, {"error_type": "DataException"})

        self.assertIsNone(fetch_gtt_trigger(999, "api_key", "token"))

    @patch("execution.execution_engine.requests.get")
    def test_malformed_condition_returns_none(self, mock_get):
        mock_get.return_value = _resp(200, {"data": {"condition": {}}})

        self.assertIsNone(fetch_gtt_trigger(999, "api_key", "token"))


if __name__ == "__main__":
    unittest.main()
