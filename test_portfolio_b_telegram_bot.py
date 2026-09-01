"""
Tests for portfolio_b/telegram_bot.py -- command parsing, watchlist
mutation, and the getUpdates polling/security/idempotency logic.
requests.get/send_telegram_message are always mocked -- these tests
never make a real network call.
"""

import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import portfolio_b.state as pbs
from portfolio_b.telegram_bot import (
    _handle_command,
    _normalize_symbol,
    poll_and_process_commands,
    validate_symbol_is_tradeable,
)


class _PortfolioBBotTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = patch.object(pbs, "PORTFOLIO_B_STATE_DIR", self.tmpdir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)


class TestNormalizeSymbol(unittest.TestCase):
    def test_appends_ns_suffix_if_missing(self):
        self.assertEqual(_normalize_symbol("tatasteel"), "TATASTEEL.NS")

    def test_leaves_existing_ns_suffix_alone(self):
        self.assertEqual(_normalize_symbol("tatasteel.ns"), "TATASTEEL.NS")

    def test_strips_whitespace(self):
        self.assertEqual(_normalize_symbol("  rvnl  "), "RVNL.NS")


class TestValidateSymbolIsTradeable(unittest.TestCase):
    def test_true_when_fetch_returns_data(self):
        import pandas as pd
        fake_fetch = MagicMock(return_value=pd.DataFrame({"Close": [100.0]}))
        self.assertTrue(validate_symbol_is_tradeable("AAA.NS", fetch_fn=fake_fetch))

    def test_false_when_fetch_returns_empty(self):
        import pandas as pd
        fake_fetch = MagicMock(return_value=pd.DataFrame())
        self.assertFalse(validate_symbol_is_tradeable("AAA.NS", fetch_fn=fake_fetch))

    def test_false_on_fetch_exception_never_raises(self):
        fake_fetch = MagicMock(side_effect=RuntimeError("network down"))
        self.assertFalse(validate_symbol_is_tradeable("AAA.NS", fetch_fn=fake_fetch))


class TestHandleCommand(_PortfolioBBotTestBase):
    def test_watchlist_command_lists_current_symbols(self):
        pbs.save_watchlist(["AAA.NS", "BBB.NS"])
        reply = _handle_command("/watchlist")
        self.assertIn("AAA.NS", reply)
        self.assertIn("BBB.NS", reply)

    def test_help_command_lists_commands(self):
        reply = _handle_command("/help")
        self.assertIn("/addstock", reply)
        self.assertIn("/removestock", reply)

    def test_addstock_appends_valid_new_symbol(self):
        pbs.save_watchlist(["AAA.NS"])
        reply = _handle_command("/addstock TATASTEEL", validate_fn=lambda s: True)
        self.assertIn("Added TATASTEEL.NS", reply)
        self.assertIn("TATASTEEL.NS", pbs.load_watchlist(default=[]))

    def test_addstock_rejects_symbol_that_fails_validation(self):
        pbs.save_watchlist(["AAA.NS"])
        reply = _handle_command("/addstock FAKESYMBOL", validate_fn=lambda s: False)
        self.assertIn("Could not find recent trading data", reply)
        self.assertNotIn("FAKESYMBOL.NS", pbs.load_watchlist(default=[]))

    def test_addstock_rejects_malformed_symbol_without_calling_validate_fn(self):
        calls = []
        reply = _handle_command("/addstock ../../etc", validate_fn=lambda s: calls.append(s) or True)
        self.assertIn("doesn't look like a valid NSE ticker", reply)
        self.assertEqual(calls, [], "must never even attempt to validate an obviously malformed symbol")

    def test_addstock_is_a_no_op_if_symbol_already_present(self):
        pbs.save_watchlist(["TATASTEEL.NS"])
        reply = _handle_command("/addstock tatasteel", validate_fn=lambda s: True)
        self.assertIn("already on the watchlist", reply)
        self.assertEqual(pbs.load_watchlist(default=[]).count("TATASTEEL.NS"), 1)

    def test_removestock_removes_present_symbol(self):
        pbs.save_watchlist(["AAA.NS", "BBB.NS"])
        reply = _handle_command("/removestock AAA")
        self.assertIn("Removed AAA.NS", reply)
        self.assertEqual(pbs.load_watchlist(default=[]), ["BBB.NS"])

    def test_removestock_on_absent_symbol_is_a_no_op(self):
        pbs.save_watchlist(["BBB.NS"])
        reply = _handle_command("/removestock AAA")
        self.assertIn("isn't on the watchlist", reply)
        self.assertEqual(pbs.load_watchlist(default=[]), ["BBB.NS"])

    def test_unrecognized_text_returns_none_no_reply(self):
        self.assertIsNone(_handle_command("just a regular chat message"))


class TestPollAndProcessCommands(_PortfolioBBotTestBase):
    def _mock_response(self, updates: list):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"ok": True, "result": updates}
        return resp

    def test_message_from_configured_chat_is_processed_and_replied_to(self):
        pbs.save_watchlist(["AAA.NS"])
        updates = [{"update_id": 1, "message": {"chat": {"id": 999}, "text": "/watchlist"}}]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message") as mock_send:
            processed = poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(len(processed), 1)
        mock_send.assert_called_once()

    def test_message_from_a_different_chat_is_silently_ignored(self):
        updates = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "/addstock EVIL"}}]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message") as mock_send:
            processed = poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(processed, [])
        mock_send.assert_not_called()
        self.assertNotIn("EVIL.NS", pbs.load_watchlist(default=[]))

    def test_offset_advances_so_the_same_update_is_never_reprocessed(self):
        updates = [{"update_id": 5, "message": {"chat": {"id": 999}, "text": "/watchlist"}}]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message"):
            poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(pbs.load_telegram_offset(), 5)

    def test_next_poll_requests_updates_after_the_saved_offset(self):
        pbs.save_telegram_offset(10)
        captured = {}

        def fake_get(url, params, timeout):
            captured["offset"] = params["offset"]
            return self._mock_response([])

        with patch("portfolio_b.telegram_bot.requests.get", side_effect=fake_get):
            poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(captured["offset"], 11)

    def test_one_bad_update_does_not_block_the_rest_of_the_batch(self):
        updates = [
            {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/help"}},
            {"update_id": 2},   # malformed -- no "message" key at all
            {"update_id": 3, "message": {"chat": {"id": 999}, "text": "/watchlist"}},
        ]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message"):
            processed = poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(len(processed), 2)
        self.assertEqual(pbs.load_telegram_offset(), 3)

    def test_no_updates_is_a_clean_no_op(self):
        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response([])), \
             patch("portfolio_b.telegram_bot.send_telegram_message") as mock_send:
            processed = poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(processed, [])
        mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
