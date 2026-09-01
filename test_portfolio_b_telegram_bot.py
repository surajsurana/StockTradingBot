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
    QUICK_ACTIONS_KEYBOARD,
    _handle_command,
    _normalize_symbol,
    fetch_company_name_if_tradeable,
    poll_and_process_commands,
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


class TestFetchCompanyNameIfTradeable(unittest.TestCase):
    def test_returns_name_when_price_and_info_both_available(self):
        import pandas as pd
        fake_price_fn = MagicMock(return_value=pd.DataFrame({"Close": [100.0]}))
        fake_info_fn = MagicMock(return_value={"longName": "Test Company Ltd"})
        result = fetch_company_name_if_tradeable("AAA.NS", fetch_price_fn=fake_price_fn, fetch_info_fn=fake_info_fn)
        self.assertEqual(result, "Test Company Ltd")

    def test_falls_back_to_short_name(self):
        import pandas as pd
        fake_price_fn = MagicMock(return_value=pd.DataFrame({"Close": [100.0]}))
        fake_info_fn = MagicMock(return_value={"shortName": "Test Co"})
        result = fetch_company_name_if_tradeable("AAA.NS", fetch_price_fn=fake_price_fn, fetch_info_fn=fake_info_fn)
        self.assertEqual(result, "Test Co")

    def test_none_when_price_fetch_returns_empty(self):
        import pandas as pd
        fake_price_fn = MagicMock(return_value=pd.DataFrame())
        result = fetch_company_name_if_tradeable("AAA.NS", fetch_price_fn=fake_price_fn)
        self.assertIsNone(result)

    def test_none_on_price_fetch_exception_never_raises(self):
        fake_price_fn = MagicMock(side_effect=RuntimeError("network down"))
        result = fetch_company_name_if_tradeable("AAA.NS", fetch_price_fn=fake_price_fn)
        self.assertIsNone(result)

    def test_tradeable_but_no_name_returns_empty_string_not_none(self):
        """A symbol with real price data but no info returned is still
        ACCEPTED (empty name, falls back to showing the bare symbol) --
        the name is a display nicety, never a rejection reason."""
        import pandas as pd
        fake_price_fn = MagicMock(return_value=pd.DataFrame({"Close": [100.0]}))
        fake_info_fn = MagicMock(side_effect=RuntimeError("info lookup failed"))
        result = fetch_company_name_if_tradeable("AAA.NS", fetch_price_fn=fake_price_fn, fetch_info_fn=fake_info_fn)
        self.assertEqual(result, "")


class TestHandleCommand(_PortfolioBBotTestBase):
    def test_watchlist_command_lists_names_and_symbols(self):
        pbs.save_watchlist({"AAA.NS": "Company A", "BBB.NS": "Company B"})
        reply = _handle_command("/watchlist")
        self.assertIn("Company A (AAA.NS)", reply)
        self.assertIn("Company B (BBB.NS)", reply)

    def test_watchlist_command_falls_back_to_bare_symbol_when_name_unknown(self):
        pbs.save_watchlist({"AAA.NS": ""})
        reply = _handle_command("/watchlist")
        self.assertIn("- AAA.NS", reply)
        self.assertNotIn("()", reply)

    def test_help_command_lists_commands(self):
        reply = _handle_command("/help")
        self.assertIn("/addstock", reply)
        self.assertIn("/removestock", reply)

    def test_start_command_also_shows_help(self):
        reply = _handle_command("/start")
        self.assertIn("/addstock", reply)

    def test_addstock_appends_valid_new_symbol_with_its_name(self):
        pbs.save_watchlist({"AAA.NS": "Company A"})
        reply = _handle_command("/addstock TATASTEEL", name_fn=lambda s: "Tata Steel Limited")
        self.assertIn("Tata Steel Limited (TATASTEEL.NS)", reply)
        watchlist = pbs.load_watchlist(default={})
        self.assertEqual(watchlist["TATASTEEL.NS"], "Tata Steel Limited")

    def test_addstock_rejects_symbol_that_fails_validation(self):
        pbs.save_watchlist({"AAA.NS": "Company A"})
        reply = _handle_command("/addstock FAKESYMBOL", name_fn=lambda s: None)
        self.assertIn("Could not find recent trading data", reply)
        self.assertNotIn("FAKESYMBOL.NS", pbs.load_watchlist(default={}))

    def test_addstock_rejects_malformed_symbol_without_calling_name_fn(self):
        calls = []
        reply = _handle_command("/addstock ../../etc", name_fn=lambda s: calls.append(s) or "irrelevant")
        self.assertIn("doesn't look like a valid NSE ticker", reply)
        self.assertEqual(calls, [], "must never even attempt to resolve an obviously malformed symbol")

    def test_bare_addstock_with_no_symbol_gets_a_usage_reply_not_silence(self):
        """Regression test for a real reported bug: '/addstock' sent
        alone (e.g. tapped from Telegram's '/' menu and sent as-is, no
        symbol typed after it) previously fell through _ADD_PATTERN's
        match entirely and returned None -- no reply, no log entry,
        indistinguishable from the message never arriving at all."""
        reply = _handle_command("/addstock")
        self.assertIsNotNone(reply)
        self.assertIn("Usage: /addstock", reply)

    def test_bare_addstock_with_trailing_whitespace_only_gets_a_usage_reply(self):
        reply = _handle_command("/addstock   ")
        self.assertIsNotNone(reply)
        self.assertIn("Usage: /addstock", reply)

    def test_bare_removestock_with_no_symbol_gets_a_usage_reply_not_silence(self):
        reply = _handle_command("/removestock")
        self.assertIsNotNone(reply)
        self.assertIn("Usage: /removestock", reply)

    def test_addstock_is_a_no_op_if_symbol_already_present(self):
        pbs.save_watchlist({"TATASTEEL.NS": "Tata Steel Limited"})
        reply = _handle_command("/addstock tatasteel", name_fn=lambda s: "Tata Steel Limited")
        self.assertIn("already on the watchlist", reply)
        self.assertEqual(len(pbs.load_watchlist(default={})), 1)

    def test_removestock_removes_present_symbol(self):
        pbs.save_watchlist({"AAA.NS": "Company A", "BBB.NS": "Company B"})
        reply = _handle_command("/removestock AAA")
        self.assertIn("Removed AAA.NS", reply)
        self.assertEqual(pbs.load_watchlist(default={}), {"BBB.NS": "Company B"})

    def test_removestock_on_absent_symbol_is_a_no_op(self):
        pbs.save_watchlist({"BBB.NS": "Company B"})
        reply = _handle_command("/removestock AAA")
        self.assertIn("isn't on the watchlist", reply)
        self.assertEqual(pbs.load_watchlist(default={}), {"BBB.NS": "Company B"})

    def test_unrecognized_text_returns_none_no_reply(self):
        self.assertIsNone(_handle_command("just a regular chat message"))


class TestPollAndProcessCommands(_PortfolioBBotTestBase):
    def _mock_response(self, updates: list):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"ok": True, "result": updates}
        return resp

    def test_message_from_configured_chat_is_processed_and_replied_to_with_keyboard(self):
        pbs.save_watchlist({"AAA.NS": "Company A"})
        updates = [{"update_id": 1, "message": {"chat": {"id": 999}, "text": "/watchlist"}}]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message") as mock_send:
            processed = poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(len(processed), 1)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs.get("reply_markup"), QUICK_ACTIONS_KEYBOARD)

    def test_message_from_a_different_chat_is_silently_ignored(self):
        updates = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "/addstock EVIL"}}]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message") as mock_send:
            processed = poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(processed, [])
        mock_send.assert_not_called()
        self.assertNotIn("EVIL.NS", pbs.load_watchlist(default={}))

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

    def test_long_poll_timeout_is_passed_through_to_telegram_and_requests(self):
        captured = {}

        def fake_get(url, params, timeout):
            captured["telegram_timeout"] = params["timeout"]
            captured["requests_timeout"] = timeout
            return self._mock_response([])

        with patch("portfolio_b.telegram_bot.requests.get", side_effect=fake_get):
            poll_and_process_commands("fake-token", chat_id="999", long_poll_timeout=30)

        self.assertEqual(captured["telegram_timeout"], 30)
        self.assertGreater(captured["requests_timeout"], 30, "the client timeout must exceed Telegram's own hold-open window")

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
