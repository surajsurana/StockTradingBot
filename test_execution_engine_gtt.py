"""
Mock-based unit tests for execution/execution_engine.py's GTT stop-loss/target
logic (_place_gtt_exit, cancel_gtt, and the BUY -> GTT wiring in
_place_live_order). Run with:

    python test_execution_engine_gtt.py
"""

import json
import unittest
from unittest.mock import patch, MagicMock

from execution.execution_engine import ExecutionEngine
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

    @patch("execution.execution_engine.requests.post")
    def test_placement_failure_raises(self, mock_post):
        mock_post.return_value = _resp(400, {"error_type": "InputException", "message": "bad request"})
        with self.assertRaises(RuntimeError):
            self.engine._place_gtt_exit(_buy_trade())


class TestPlaceLiveOrderWiresGtt(unittest.TestCase):
    def setUp(self):
        self.engine = ExecutionEngine(live_trading=True, api_key="api_key", access_token="token")

    @patch.object(ExecutionEngine, "_place_gtt_exit", return_value=999)
    @patch("execution.execution_engine.requests.post")
    def test_successful_buy_places_gtt(self, mock_post, mock_gtt):
        mock_post.return_value = _resp(200, {"status": "success", "data": {"order_id": "abc"}})

        result = self.engine._place_live_order(_buy_trade())

        mock_gtt.assert_called_once()
        self.assertEqual(result["gtt_id"], 999)

    @patch.object(ExecutionEngine, "_place_gtt_exit", side_effect=RuntimeError("GTT endpoint down"))
    @patch("execution.execution_engine.requests.post")
    def test_gtt_failure_does_not_fail_the_buy(self, mock_post, mock_gtt):
        """The BUY already filled -- a GTT placement failure shouldn't make
        place_order look like the whole trade failed. It should be surfaced
        (gtt_id is None) so the position is known to be missing its safety net."""
        mock_post.return_value = _resp(200, {"status": "success", "data": {"order_id": "abc"}})

        result = self.engine._place_live_order(_buy_trade())

        self.assertEqual(result["status"], "success")
        self.assertIsNone(result["gtt_id"])

    @patch.object(ExecutionEngine, "_place_gtt_exit")
    @patch("execution.execution_engine.requests.post")
    def test_sell_order_never_triggers_gtt_placement(self, mock_post, mock_gtt):
        mock_post.return_value = _resp(200, {"status": "success", "data": {"order_id": "abc"}})
        sell_signal = Signal(symbol="INFY.NS", direction="SELL", entry_price=1500.0, stop_loss=1500.0,
                              target=1500.0, confidence=0.8, strategy_name="test", reason="exit")
        trade = ApprovedTrade(signal=sell_signal, quantity=10, capital_deployed=15000.0)

        self.engine._place_live_order(trade)

        mock_gtt.assert_not_called()


class TestCancelGtt(unittest.TestCase):
    @patch("execution.execution_engine.requests.delete")
    def test_cancel_sends_delete_to_correct_url(self, mock_delete):
        mock_delete.return_value = _resp(200, {"status": "success"})
        engine = ExecutionEngine(live_trading=True, api_key="api_key", access_token="token")

        engine.cancel_gtt(4242)

        called_url = mock_delete.call_args[0][0]
        self.assertEqual(called_url, "https://api.kite.trade/gtt/triggers/4242")


if __name__ == "__main__":
    unittest.main()
