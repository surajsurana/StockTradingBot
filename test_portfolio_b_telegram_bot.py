"""
Tests for portfolio_b/telegram_bot.py -- command parsing, the search-and-
confirm /addstock flow (inline buttons, nothing added until tapped),
/removestock's tap-to-remove picker, and the getUpdates polling/security/
idempotency logic (both message and callback_query updates).
requests.get/send_telegram_message are always mocked -- these tests
never make a real network call.
"""

import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import portfolio_b.state as pbs
from portfolio_b.telegram_bot import (
    MAIN_MENU_KEYBOARD,
    CommandReply,
    _handle_callback_query,
    _handle_command,
    _normalize_symbol,
    fetch_company_name_if_tradeable,
    poll_and_process_commands,
    search_nse_symbol_candidates,
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
        import pandas as pd
        fake_price_fn = MagicMock(return_value=pd.DataFrame({"Close": [100.0]}))
        fake_info_fn = MagicMock(side_effect=RuntimeError("info lookup failed"))
        result = fetch_company_name_if_tradeable("AAA.NS", fetch_price_fn=fake_price_fn, fetch_info_fn=fake_info_fn)
        self.assertEqual(result, "")


class TestSearchNseSymbolCandidates(unittest.TestCase):
    def test_filters_to_nse_exchange_only(self):
        fake_search = MagicMock(return_value=[
            {"symbol": "HWHG.F", "shortname": "Tata Steel Ltd.", "exchange": "FRA"},
            {"symbol": "TATASTEEL.NS", "shortname": "TATA STEEL LIMITED", "exchange": "NSI"},
        ])
        result = search_nse_symbol_candidates("tata steel", search_fn=fake_search)
        self.assertEqual(result, [{"symbol": "TATASTEEL.NS", "name": "TATA STEEL LIMITED"}])

    def test_prefers_longname_over_shortname(self):
        fake_search = MagicMock(return_value=[
            {"symbol": "AAA.NS", "shortname": "Short", "longname": "Long Name Ltd", "exchange": "NSI"},
        ])
        result = search_nse_symbol_candidates("x", search_fn=fake_search)
        self.assertEqual(result[0]["name"], "Long Name Ltd")

    def test_deduplicates_repeated_symbols(self):
        fake_search = MagicMock(return_value=[
            {"symbol": "AAA.NS", "shortname": "A", "exchange": "NSI"},
            {"symbol": "AAA.NS", "shortname": "A", "exchange": "NSI"},
        ])
        result = search_nse_symbol_candidates("x", search_fn=fake_search)
        self.assertEqual(len(result), 1)

    def test_caps_at_max_results(self):
        fake_search = MagicMock(return_value=[
            {"symbol": f"S{i}.NS", "shortname": f"Co {i}", "exchange": "NSI"} for i in range(10)
        ])
        result = search_nse_symbol_candidates("x", search_fn=fake_search, max_results=3)
        self.assertEqual(len(result), 3)

    def test_empty_when_no_nse_matches(self):
        fake_search = MagicMock(return_value=[{"symbol": "GOLD", "shortname": "Gold Inc", "exchange": "NYQ"}])
        self.assertEqual(search_nse_symbol_candidates("gold", search_fn=fake_search), [])

    def test_never_raises_on_search_failure(self):
        fake_search = MagicMock(side_effect=RuntimeError("network down"))
        self.assertEqual(search_nse_symbol_candidates("x", search_fn=fake_search), [])


class TestHandleCommand(_PortfolioBBotTestBase):
    def test_watchlist_command_lists_names_and_symbols(self):
        pbs.save_watchlist({"AAA.NS": "Company A", "BBB.NS": "Company B"})
        reply = _handle_command("/watchlist")
        self.assertIn("Company A (AAA.NS)", reply.text)
        self.assertIn("Company B (BBB.NS)", reply.text)

    def test_watchlist_command_falls_back_to_bare_symbol_when_name_unknown(self):
        pbs.save_watchlist({"AAA.NS": ""})
        reply = _handle_command("/watchlist")
        self.assertIn("- AAA.NS", reply.text)
        self.assertNotIn("()", reply.text)

    def test_help_command_lists_commands(self):
        reply = _handle_command("/help")
        self.assertIn("/addstock", reply.text)
        self.assertIn("/removestock", reply.text)

    def test_start_command_also_shows_help(self):
        reply = _handle_command("/start")
        self.assertIn("/addstock", reply.text)

    def test_bare_addstock_asks_what_to_add_instead_of_silence(self):
        """Regression test for a real reported bug: '/addstock' sent
        alone (e.g. tapped from Telegram's '/' menu and sent as-is)
        used to fall through the argument-capturing pattern entirely
        and return None -- no reply, no log entry, indistinguishable
        from the message never having arrived at all. Now asks what to
        add (ask-then-reply flow) rather than requiring the name in the
        same message."""
        reply = _handle_command("/addstock")
        self.assertIsNotNone(reply)
        self.assertIn("What stock", reply.text)

    def test_bare_addstock_sets_pending_action(self):
        _handle_command("/addstock")
        self.assertEqual(pbs.load_pending_action(), "addstock")

    def test_add_stock_button_also_asks_and_sets_pending_action(self):
        reply = _handle_command("➕ Add Stock")
        self.assertIsNotNone(reply)
        self.assertIn("What stock", reply.text)
        self.assertEqual(pbs.load_pending_action(), "addstock")

    def test_follow_up_reply_after_pending_addstock_is_treated_as_the_query(self):
        pbs.save_watchlist({})
        pbs.save_pending_action("addstock")
        fake_search = MagicMock(return_value=[
            {"symbol": "TATASTEEL.NS", "shortname": "TATA STEEL LIMITED", "exchange": "NSI"},
        ])
        reply = _handle_command("Tata Steel", search_fn=fake_search)
        self.assertIn("Found 1 match", reply.text)
        self.assertIsNone(pbs.load_pending_action(), "must be cleared once consumed")

    def test_sending_a_command_while_pending_clears_it_instead_of_misreading_it(self):
        pbs.save_watchlist({"AAA.NS": "Company A"})
        pbs.save_pending_action("addstock")
        reply = _handle_command("/watchlist")
        self.assertIn("Company A", reply.text)
        self.assertIsNone(pbs.load_pending_action())

    def test_sending_watchlist_button_while_pending_clears_it_instead_of_misreading_it(self):
        pbs.save_watchlist({"AAA.NS": "Company A"})
        pbs.save_pending_action("addstock")
        reply = _handle_command("📋 Watchlist")
        self.assertIn("Company A", reply.text)
        self.assertIsNone(pbs.load_pending_action())

    def test_watchlist_button_is_equivalent_to_slash_command(self):
        pbs.save_watchlist({"AAA.NS": "Company A"})
        reply = _handle_command("📋 Watchlist")
        self.assertIn("Company A (AAA.NS)", reply.text)

    def test_help_button_is_equivalent_to_slash_command(self):
        reply = _handle_command("❓ Help")
        self.assertIn("Add Stock", reply.text)

    def test_remove_stock_button_shows_picker(self):
        pbs.save_watchlist({"AAA.NS": "Company A"})
        reply = _handle_command("➖ Remove Stock")
        self.assertIn("Tap a symbol", reply.text)
        self.assertIsNotNone(reply.reply_markup)

    def test_addstock_by_company_name_offers_matches_and_adds_nothing_yet(self):
        pbs.save_watchlist({})
        fake_search = MagicMock(return_value=[
            {"symbol": "TATASTEEL.NS", "shortname": "TATA STEEL LIMITED", "exchange": "NSI"},
        ])
        reply = _handle_command("/addstock tata steel", search_fn=fake_search)
        self.assertIn("Found 1 match", reply.text)
        self.assertIsNotNone(reply.reply_markup)
        self.assertIn("inline_keyboard", reply.reply_markup)
        # Nothing was actually added -- confirmation is a separate, later step.
        self.assertEqual(pbs.load_watchlist(default={}), {})

    def test_addstock_multiple_matches_offers_one_button_each_plus_cancel(self):
        pbs.save_watchlist({})
        fake_search = MagicMock(return_value=[
            {"symbol": "AAA.NS", "shortname": "Company A", "exchange": "NSI"},
            {"symbol": "BBB.NS", "shortname": "Company B", "exchange": "NSI"},
        ])
        reply = _handle_command("/addstock company", search_fn=fake_search)
        self.assertIn("Found 2 matches", reply.text)
        rows = reply.reply_markup["inline_keyboard"]
        self.assertEqual(len(rows), 3)   # 2 candidates + Cancel

    def test_addstock_falls_back_to_literal_ticker_when_search_finds_nothing(self):
        pbs.save_watchlist({})
        fake_search = MagicMock(return_value=[])
        reply = _handle_command("/addstock TATASTEEL", search_fn=fake_search,
                                 name_fn=lambda s: "Tata Steel Limited")
        self.assertIn("Found 1 match", reply.text)
        self.assertIn("Tata Steel Limited (TATASTEEL.NS)", reply.reply_markup["inline_keyboard"][0][0]["text"])

    def test_addstock_no_search_results_and_no_valid_ticker_fallback_says_so(self):
        pbs.save_watchlist({})
        fake_search = MagicMock(return_value=[])
        reply = _handle_command("/addstock ../../etc", search_fn=fake_search,
                                 name_fn=lambda s: "irrelevant")
        self.assertIn("Couldn't find any NSE-listed match", reply.text)

    def test_addstock_omits_candidates_already_on_the_watchlist(self):
        pbs.save_watchlist({"TATASTEEL.NS": "Tata Steel Limited"})
        fake_search = MagicMock(return_value=[
            {"symbol": "TATASTEEL.NS", "shortname": "TATA STEEL LIMITED", "exchange": "NSI"},
        ])
        reply = _handle_command("/addstock tata steel", search_fn=fake_search)
        self.assertIn("already on the watchlist", reply.text)

    def test_removestock_alone_shows_a_tap_to_remove_picker(self):
        pbs.save_watchlist({"AAA.NS": "Company A", "BBB.NS": "Company B"})
        reply = _handle_command("/removestock")
        self.assertIn("Tap a symbol to remove", reply.text)
        rows = reply.reply_markup["inline_keyboard"]
        self.assertEqual(len(rows), 3)   # 2 symbols + Cancel

    def test_removestock_alone_on_empty_watchlist_says_so(self):
        pbs.save_watchlist({})
        reply = _handle_command("/removestock")
        self.assertIn("currently empty", reply.text)

    def test_removestock_with_symbol_removes_directly_no_confirm_needed(self):
        pbs.save_watchlist({"AAA.NS": "Company A", "BBB.NS": "Company B"})
        reply = _handle_command("/removestock AAA")
        self.assertIn("Removed AAA.NS", reply.text)
        self.assertEqual(pbs.load_watchlist(default={}), {"BBB.NS": "Company B"})

    def test_removestock_with_absent_symbol_is_a_no_op(self):
        pbs.save_watchlist({"BBB.NS": "Company B"})
        reply = _handle_command("/removestock AAA")
        self.assertIn("isn't on the watchlist", reply.text)

    def test_unrecognized_text_returns_none_no_reply(self):
        self.assertIsNone(_handle_command("just a regular chat message"))


class TestHandleCallbackQuery(_PortfolioBBotTestBase):
    def test_add_callback_adds_the_symbol(self):
        pbs.save_watchlist({})
        reply = _handle_callback_query("pbadd:TATASTEEL.NS", name_fn=lambda s: "Tata Steel Limited")
        self.assertIn("Added Tata Steel Limited (TATASTEEL.NS)", reply)
        self.assertEqual(pbs.load_watchlist(default={})["TATASTEEL.NS"], "Tata Steel Limited")

    def test_add_callback_no_longer_tradeable_is_not_added(self):
        pbs.save_watchlist({})
        reply = _handle_callback_query("pbadd:DELISTED.NS", name_fn=lambda s: None)
        self.assertIn("Could not find recent trading data", reply)
        self.assertEqual(pbs.load_watchlist(default={}), {})

    def test_add_callback_already_present_is_a_no_op(self):
        pbs.save_watchlist({"TATASTEEL.NS": "Tata Steel Limited"})
        reply = _handle_callback_query("pbadd:TATASTEEL.NS", name_fn=lambda s: "Tata Steel Limited")
        self.assertIn("already on the watchlist", reply)

    def test_remove_callback_removes_the_symbol(self):
        pbs.save_watchlist({"AAA.NS": "Company A"})
        reply = _handle_callback_query("pbrm:AAA.NS")
        self.assertIn("Removed AAA.NS", reply)
        self.assertEqual(pbs.load_watchlist(default={}), {})

    def test_cancel_callback_changes_nothing(self):
        pbs.save_watchlist({"AAA.NS": "Company A"})
        reply = _handle_callback_query("pbcancel")
        self.assertEqual(reply, "Cancelled.")
        self.assertEqual(pbs.load_watchlist(default={}), {"AAA.NS": "Company A"})

    def test_unrecognized_callback_data_does_not_raise(self):
        reply = _handle_callback_query("something_unexpected")
        self.assertEqual(reply, "Unrecognized action.")


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
        self.assertEqual(mock_send.call_args.kwargs.get("reply_markup"), MAIN_MENU_KEYBOARD)

    def test_message_from_a_different_chat_is_silently_ignored(self):
        updates = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "/addstock evil corp"}}]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message") as mock_send:
            processed = poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(processed, [])
        mock_send.assert_not_called()

    def test_callback_query_from_configured_chat_is_processed(self):
        pbs.save_watchlist({})
        updates = [{"update_id": 1, "callback_query": {
            "id": "cbq1", "data": "pbadd:AAA.NS", "message": {"chat": {"id": 999}},
        }}]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message") as mock_send, \
             patch("portfolio_b.telegram_bot._answer_callback_query") as mock_answer:
            processed = poll_and_process_commands("fake-token", chat_id="999",
                                                    name_fn=lambda s: "Company A")

        self.assertEqual(len(processed), 1)
        mock_send.assert_called_once()
        mock_answer.assert_called_once_with("fake-token", "cbq1")
        self.assertIn("AAA.NS", pbs.load_watchlist(default={}))

    def test_callback_query_from_a_different_chat_is_silently_ignored(self):
        updates = [{"update_id": 1, "callback_query": {
            "id": "cbq1", "data": "pbadd:EVIL.NS", "message": {"chat": {"id": 111}},
        }}]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message") as mock_send, \
             patch("portfolio_b.telegram_bot._answer_callback_query") as mock_answer:
            processed = poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(processed, [])
        mock_send.assert_not_called()
        mock_answer.assert_not_called()
        self.assertNotIn("EVIL.NS", pbs.load_watchlist(default={}))

    def test_offset_advances_for_callback_queries_too(self):
        updates = [{"update_id": 7, "callback_query": {
            "id": "cbq1", "data": "pbcancel", "message": {"chat": {"id": 999}},
        }}]

        with patch("portfolio_b.telegram_bot.requests.get", return_value=self._mock_response(updates)), \
             patch("portfolio_b.telegram_bot.send_telegram_message"), \
             patch("portfolio_b.telegram_bot._answer_callback_query"):
            poll_and_process_commands("fake-token", chat_id="999")

        self.assertEqual(pbs.load_telegram_offset(), 7)

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
        self.assertGreater(captured["requests_timeout"], 30,
                            "the client timeout must exceed Telegram's own hold-open window")

    def test_one_bad_update_does_not_block_the_rest_of_the_batch(self):
        updates = [
            {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/help"}},
            {"update_id": 2},   # malformed -- no "message" or "callback_query" key at all
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
