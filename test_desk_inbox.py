"""desk_inbox.py -- questions sent to the Telegram bot in plain words, answered later from a Claude
Code session (2026-10-24). Every test works in a temp dir: nothing touches deployment/state/."""

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import desk_inbox


class TestTheInbox(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_question_is_saved_and_waits(self):
        qid = desk_inbox.add_question("  how did crypto do this week? ", self.d, now=datetime(2026, 10, 24, 9))
        self.assertEqual(qid, 1)
        [q] = desk_inbox.pending(self.d)
        self.assertEqual((q["text"], q["at"], q["answered_at"]), ("how did crypto do this week?",
                                                                  "2026-10-24T09:00:00", None))

    def test_ids_only_grow(self):
        a = desk_inbox.add_question("one", self.d)
        b = desk_inbox.add_question("two", self.d)
        self.assertEqual((a, b), (1, 2))

    def test_an_answer_is_sent_quoting_the_question_and_then_marked_done(self):
        qid = desk_inbox.add_question("why didn't SW-016 trade?", self.d)
        sent = []
        desk_inbox.answer(qid, "Its book is below the Rs12,469 minimum.", self.d,
                          send=lambda part: sent.append(part) or {"ok": True})
        self.assertEqual(len(sent), 1)
        self.assertIn("why didn't SW-016 trade?", sent[0])
        self.assertIn("Rs12,469", sent[0])
        self.assertEqual(desk_inbox.pending(self.d), [])

    def test_a_failed_send_leaves_the_question_waiting(self):
        qid = desk_inbox.add_question("q", self.d)
        with self.assertRaises(RuntimeError):
            desk_inbox.answer(qid, "a", self.d, send=lambda part: {"ok": False, "description": "nope"})
        self.assertEqual(len(desk_inbox.pending(self.d)), 1)

    def test_a_muted_or_unconfigured_send_is_not_a_success(self):
        qid = desk_inbox.add_question("q", self.d)
        for result in ({"status": "muted"}, {"status": "not_configured"}, None):
            with self.assertRaises(RuntimeError):
                desk_inbox.answer(qid, "a", self.d, send=lambda part, r=result: r)
        self.assertEqual(len(desk_inbox.pending(self.d)), 1)

    def test_a_question_cannot_be_answered_twice(self):
        qid = desk_inbox.add_question("q", self.d)
        desk_inbox.answer(qid, "a", self.d, send=lambda part: {"ok": True})
        with self.assertRaises(ValueError):
            desk_inbox.answer(qid, "b", self.d, send=lambda part: {"ok": True})

    def test_a_long_answer_arrives_in_pieces_under_telegrams_limit(self):
        qid = desk_inbox.add_question("q", self.d)
        sent = []
        long = "\n\n".join("para %d " % i + "x" * 900 for i in range(10))
        desk_inbox.answer(qid, long, self.d, send=lambda part: sent.append(part) or {"ok": True})
        self.assertGreater(len(sent), 1)
        self.assertTrue(all(len(p) <= 4096 for p in sent))

    def test_answers_go_out_as_the_allowed_desk_answer_kind(self):
        from reporting.telegram_notifier import DEFAULT_ALLOWED_KINDS
        self.assertIn("desk_answer", DEFAULT_ALLOWED_KINDS)
        qid = desk_inbox.add_question("q", self.d)
        with patch("reporting.telegram_notifier.send_telegram_message", return_value={"ok": True}) as s:
            desk_inbox.answer(qid, "a", self.d)
        self.assertEqual(s.call_args.kwargs["kind"], "desk_answer")
        self.assertIsNone(s.call_args.kwargs["parse_mode"])

    def test_a_missing_or_broken_inbox_reads_as_empty(self):
        self.assertEqual(desk_inbox.pending(self.d), [])
        with open(os.path.join(self.d, desk_inbox.INBOX_FILE), "w") as f:
            f.write("{broken")
        self.assertEqual(desk_inbox.load(self.d), [])


class TestTheBotSavesQuestions(unittest.TestCase):
    """The 24/7 bot saves anything that is not one of its commands and says so."""

    def _poll(self, text):
        from portfolio_b import telegram_bot as tb
        update = {"update_id": 5, "message": {"text": text, "chat": {"id": 42}}}

        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": True, "result": [update]}

        sent, saved = [], []
        with patch.object(tb.requests, "get", return_value=Resp()), \
             patch.object(tb.pbs, "load_telegram_offset", return_value=0), \
             patch.object(tb.pbs, "save_telegram_offset"), \
             patch.object(tb.pbs, "load_pending_action", return_value=None), \
             patch.object(tb.pbs, "save_pending_action"), \
             patch.object(tb, "send_telegram_message", side_effect=lambda m, *a, **k: sent.append((m, k))), \
             patch("desk_inbox.add_question", side_effect=lambda t: saved.append(t) or 1):
            tb.poll_and_process_commands("token", "42")
        return sent, saved

    def test_a_plain_question_is_saved_and_acknowledged(self):
        sent, saved = self._poll("how did crypto do this week?")
        self.assertEqual(saved, ["how did crypto do this week?"])
        self.assertEqual(sent[0][0], "📥 Got it — I'll answer when the desk is next open.")
        self.assertEqual(sent[0][1]["kind"], "bot_reply")

    def test_an_unknown_slash_command_is_not_saved_as_a_question(self):
        sent, saved = self._poll("/nonsense")
        self.assertEqual(saved, [])

class TestTheBotStaysLight(unittest.TestCase):
    """The bot is a 24/7 process on a 458 MB server: it must not load pandas or yfinance just to listen
    (2026-10-25 -- that cost ~45 MB and helped push a US backtest out of memory)."""

    def test_importing_the_bot_loads_neither_pandas_nor_yfinance(self):
        import subprocess
        import sys
        out = subprocess.run([sys.executable, "-c",
                              "import sys, portfolio_b.telegram_bot; "
                              "print('pandas' in sys.modules, 'yfinance' in sys.modules)"],
                             capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(out.stdout.strip(), "False False", out.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
