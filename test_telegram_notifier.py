"""
Tests for reporting/telegram_notifier.py's reply_markup and
set_bot_commands additions (added 2026-09-01 for Portfolio B's
interactive commands). The pre-existing send/print fallback behavior
isn't retested here -- these only cover the new surface.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from reporting.telegram_notifier import delete_bot_commands, send_telegram_message, set_bot_commands


class TestSendTelegramMessageReplyMarkup(unittest.TestCase):
    def test_reply_markup_omitted_by_default(self):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"ok": True}
        with patch("reporting.telegram_notifier.requests.post", return_value=fake_resp) as mock_post:
            send_telegram_message("hi", "tok", "123")
        self.assertNotIn("reply_markup", mock_post.call_args.kwargs["data"])

    def test_reply_markup_included_and_json_encoded_when_provided(self):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"ok": True}
        keyboard = {"keyboard": [[{"text": "/watchlist"}]]}
        with patch("reporting.telegram_notifier.requests.post", return_value=fake_resp) as mock_post:
            send_telegram_message("hi", "tok", "123", reply_markup=keyboard)
        sent = mock_post.call_args.kwargs["data"]
        self.assertEqual(json.loads(sent["reply_markup"]), keyboard)

    def test_not_configured_still_prints_and_ignores_reply_markup(self):
        result = send_telegram_message("hi", "", "", reply_markup={"keyboard": []})
        self.assertEqual(result, {"status": "not_configured"})


class TestSetBotCommands(unittest.TestCase):
    def test_not_configured_is_a_no_op(self):
        result = set_bot_commands("", commands=[{"command": "help", "description": "x"}])
        self.assertEqual(result, {"status": "not_configured"})

    def test_sends_commands_list_to_the_right_endpoint(self):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"ok": True}
        commands = [{"command": "watchlist", "description": "Show the list"}]
        with patch("reporting.telegram_notifier.requests.post", return_value=fake_resp) as mock_post:
            set_bot_commands("tok", commands)
        self.assertIn("setMyCommands", mock_post.call_args.args[0])
        self.assertEqual(mock_post.call_args.kwargs["json"]["commands"], commands)


class TestDeleteBotCommands(unittest.TestCase):
    def test_not_configured_is_a_no_op(self):
        result = delete_bot_commands("")
        self.assertEqual(result, {"status": "not_configured"})

    def test_calls_the_right_endpoint(self):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"ok": True}
        with patch("reporting.telegram_notifier.requests.post", return_value=fake_resp) as mock_post:
            delete_bot_commands("tok")
        self.assertIn("deleteMyCommands", mock_post.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
