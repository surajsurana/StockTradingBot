"""
Mock-based unit tests for execution/position_state.py -- the known_positions.json
persistence and the reconciliation/close-out logging that diffs it against
real Kite holdings. Uses temp files so nothing here touches the real
data/known_positions.json or closed_trades_log.csv. Run with:

    python test_position_state.py
"""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from execution.position_state import (
    record_new_position, load_known_positions, save_known_positions,
    reconcile_closed_positions, _log_closed_trade, KnownPosition,
)
from execution.positions import Holding


def _resp(status_code=200, json_data=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data or {}
    return m


class TestKnownPositionsRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_record_then_load_round_trips(self):
        record_new_position("INFY.NS", 10, 1500.0, gtt_id=42, path=self.path)

        positions = load_known_positions(self.path)

        self.assertIn("INFY.NS", positions)
        self.assertEqual(positions["INFY.NS"].quantity, 10)
        self.assertEqual(positions["INFY.NS"].gtt_id, 42)

    def test_load_missing_file_returns_empty_dict(self):
        os.unlink(self.path)
        self.assertEqual(load_known_positions(self.path), {})

    def test_recording_multiple_symbols_preserves_both(self):
        record_new_position("INFY.NS", 10, 1500.0, gtt_id=1, path=self.path)
        record_new_position("TCS.NS", 5, 3200.0, gtt_id=2, path=self.path)

        positions = load_known_positions(self.path)
        self.assertEqual(set(positions.keys()), {"INFY.NS", "TCS.NS"})


class TestLogClosedTrade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)  # let _log_closed_trade create it fresh, header included
        self.path = self.tmp.name

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_writes_header_and_computes_realized_pnl(self):
        position = KnownPosition("INFY.NS", 10, 1500.0, gtt_id=1, opened_at="2026-07-01T00:00:00")

        realized_pnl = _log_closed_trade(position, 1470.0, "GTT stop-loss/target triggered", path=self.path)

        self.assertAlmostEqual(realized_pnl, (1470.0 - 1500.0) * 10)
        with open(self.path) as f:
            contents = f.read()
        self.assertIn("INFY.NS", contents)
        self.assertIn("realized_pnl", contents)  # header present

    def test_unknown_exit_price_leaves_pnl_blank(self):
        position = KnownPosition("INFY.NS", 10, 1500.0, gtt_id=1, opened_at="2026-07-01T00:00:00")

        realized_pnl = _log_closed_trade(position, None, "GTT stop-loss/target triggered", path=self.path)

        self.assertIsNone(realized_pnl)
        with open(self.path) as f:
            contents = f.read()
        self.assertIn("INFY.NS", contents)


class TestReconcileClosedPositions(unittest.TestCase):
    """
    _log_closed_trade is mocked throughout -- it's covered in isolation above
    -- so these tests never touch the real project's closed_trades_log.csv
    and can focus on the diff/notify logic itself.
    """

    def setUp(self):
        self.known_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.known_tmp.close()
        self.known_path = self.known_tmp.name

    def tearDown(self):
        os.unlink(self.known_path)

    @patch("execution.position_state._log_closed_trade")
    @patch("execution.position_state.send_telegram_message")
    @patch("execution.position_state.requests.get")
    def test_position_still_held_is_left_untouched(self, mock_get, mock_telegram, mock_log):
        save_known_positions({
            "INFY.NS": KnownPosition("INFY.NS", 10, 1500.0, gtt_id=1, opened_at="2026-07-01T00:00:00"),
        }, self.known_path)

        current_holdings = [Holding(symbol="INFY.NS", quantity=10, average_price=1500.0)]
        closed = reconcile_closed_positions(current_holdings, "api_key", "token", "bot_token", "chat_id",
                                             path=self.known_path)

        self.assertEqual(closed, [])
        mock_telegram.assert_not_called()
        mock_log.assert_not_called()
        self.assertIn("INFY.NS", load_known_positions(self.known_path))

    @patch("execution.position_state._log_closed_trade", return_value=-300.0)
    @patch("execution.position_state.send_telegram_message")
    @patch("execution.position_state.requests.get")
    def test_disappeared_position_is_logged_and_notified(self, mock_get, mock_telegram, mock_log):
        mock_get.return_value = _resp(200, {"data": [
            {"tradingsymbol": "INFY", "transaction_type": "SELL", "status": "COMPLETE",
             "average_price": 1470.0, "order_timestamp": "2026-07-07 10:00:00"},
        ]})

        save_known_positions({
            "INFY.NS": KnownPosition("INFY.NS", 10, 1500.0, gtt_id=1, opened_at="2026-07-01T00:00:00"),
        }, self.known_path)

        closed = reconcile_closed_positions([], "api_key", "token", "bot_token", "chat_id",
                                             path=self.known_path)

        self.assertEqual(closed, ["INFY.NS"])
        mock_log.assert_called_once()
        mock_telegram.assert_called_once()
        self.assertNotIn("INFY.NS", load_known_positions(self.known_path))

    @patch("execution.position_state._log_closed_trade", return_value=None)
    @patch("execution.position_state.send_telegram_message")
    @patch("execution.position_state.requests.get")
    def test_no_matching_order_found_still_logs_with_unknown_exit(self, mock_get, mock_telegram, mock_log):
        mock_get.return_value = _resp(200, {"data": []})  # nothing found -- e.g. closed on an earlier day

        save_known_positions({
            "INFY.NS": KnownPosition("INFY.NS", 10, 1500.0, gtt_id=1, opened_at="2026-07-01T00:00:00"),
        }, self.known_path)

        closed = reconcile_closed_positions([], "api_key", "token", "bot_token", "chat_id",
                                             path=self.known_path)

        self.assertEqual(closed, ["INFY.NS"])
        message_sent = mock_telegram.call_args[0][0]
        self.assertIn("unknown", message_sent.lower())

    @patch("execution.position_state._log_closed_trade", return_value=None)
    @patch("execution.position_state.send_telegram_message")
    @patch("execution.position_state.requests.get")
    def test_custom_reason_is_used_for_monitor_triggered_exits(self, mock_get, mock_telegram, mock_log):
        mock_get.return_value = _resp(200, {"data": []})
        save_known_positions({
            "INFY.NS": KnownPosition("INFY.NS", 10, 1500.0, gtt_id=1, opened_at="2026-07-01T00:00:00"),
        }, self.known_path)

        reconcile_closed_positions([], "api_key", "token", "bot_token", "chat_id",
                                    path=self.known_path,
                                    reason="Exited early by monitor_positions.py (unfavorable verdict)")

        message_sent = mock_telegram.call_args[0][0]
        self.assertIn("monitor_positions.py", message_sent)


if __name__ == "__main__":
    unittest.main()
