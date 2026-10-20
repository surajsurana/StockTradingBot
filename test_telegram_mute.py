"""
The 2026-10-19 Telegram mute: only research outcomes (short) and the Portfolio B bot's replies to
Suraj's own commands reach Telegram; everything else is logged to logs/telegram_muted.log instead.
"""

import os
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import auto_retire_strategies as ars
import research_queue
import run_queued_backtest as rqb
from reporting import telegram_notifier as tn

REPO_DIR = os.path.dirname(os.path.abspath(__file__))


class _TempLog(unittest.TestCase):
    def setUp(self):
        self.log = os.path.join(tempfile.mkdtemp(), "logs", "telegram_muted.log")
        p = patch.object(tn, "MUTED_LOG_PATH", self.log)
        p.start()
        self.addCleanup(p.stop)


class TestTheGate(_TempLog):
    def test_general_is_muted_logged_and_makes_no_http_call(self):
        with patch("reporting.telegram_notifier.requests.post") as post:
            out = tn.send_telegram_message("*Daily summary*\nline two", "tok", "123")
        post.assert_not_called()
        self.assertEqual(out, {"status": "muted", "kind": "general"})
        with open(self.log, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("\tgeneral\t*Daily summary* line two", lines[0])   # one line, newlines flattened

    def test_the_log_line_is_capped(self):
        tn.send_telegram_message("x" * 5000, "tok", "123", kind="pool_summary")
        with open(self.log, encoding="utf-8") as f:
            self.assertLess(len(f.read()), 300)

    def test_a_logging_failure_never_raises(self):
        with patch("builtins.open", side_effect=OSError("disk full")):
            self.assertEqual(tn.send_telegram_message("hi", "tok", "123")["status"], "muted")

    def _sent(self, kind, **kw):
        fake = MagicMock()
        fake.json.return_value = {"ok": True}
        with patch("reporting.telegram_notifier.requests.post", return_value=fake) as post:
            tn.send_telegram_message("hello", "tok", "123", kind=kind, **kw)
        return post

    def test_research_outcome_goes_through(self):
        post = self._sent("research_outcome")
        post.assert_called_once()
        self.assertIn("sendMessage", post.call_args.args[0])
        self.assertFalse(os.path.exists(self.log))

    def test_bot_reply_goes_through(self):
        self._sent("bot_reply").assert_called_once()

    def test_plain_text_omits_parse_mode(self):
        post = self._sent("research_outcome", parse_mode=None)
        self.assertNotIn("parse_mode", post.call_args.kwargs["data"])
        post = self._sent("bot_reply")
        self.assertEqual(post.call_args.kwargs["data"]["parse_mode"], "Markdown")

    def test_the_setting_decides(self):
        with patch("deployment.settings.TELEGRAM_ALLOWED_KINDS", frozenset({"general"})):
            self._sent("general").assert_called_once()
            self.assertEqual(self._sent("research_outcome").call_count, 0)

    def test_the_default_setting_is_research_outcome_bot_reply_and_kite_renewal(self):
        from deployment import settings
        if "TELEGRAM_ALLOWED_KINDS" not in os.environ:
            self.assertEqual(set(settings.TELEGRAM_ALLOWED_KINDS),
                             {"research_outcome", "bot_reply", "kite_renewal"})


class TestResearchOutcomeMessage(unittest.TestCase):
    def _short(self, msg):
        self.assertLessEqual(len(msg.splitlines()), 2)
        self.assertLessEqual(len(msg), 200)
        self.assertNotIn("*", msg)

    def test_promoted(self):
        msg = rqb.outcome_message("us", "US High-Volume Return Premium", "PASS", "EXP-101",
                                  {"promoted": True, "runner": "run_pool_i.py (Pool I, US)",
                                   "reason": "run_pool_i.py (Pool I, US) will seed its book"})
        self._short(msg)
        self.assertIn("US High-Volume Return Premium (US): PASS", msg)
        self.assertIn("promoted to paper trading (Pool I)", msg)

    def test_rejected(self):
        msg = rqb.outcome_message("crypto", "Crypto X", "REJECT", "EXP-102",
                                  {"promoted": False, "reason": "REJECT -- only PASS and INCONCLUSIVE are promoted"})
        self._short(msg)
        self.assertTrue(msg.endswith("Crypto X (crypto): REJECT → not promoted"))

    def test_inconclusive_promoted_says_so(self):
        msg = rqb.outcome_message("india", "Max Effect", "INCONCLUSIVE", "EXP-103",
                                  {"promoted": True, "runner": "run_paper_trading.py (Pool A)",
                                   "reason": "run_paper_trading.py (Pool A) will seed its book"})
        self.assertIn("INCONCLUSIVE → promoted to paper trading (Pool A)", msg)

    def test_passed_but_no_runner_is_not_claimed_as_promoted(self):
        msg = rqb.outcome_message("india", "Y", "PASS", "EXP-104",
                                  {"promoted": False, "reason": "no paper-trading runner has an entry for it yet, so ..."})
        self._short(msg)
        self.assertIn("PASS → not promoted", msg)
        self.assertNotIn("promoted to paper", msg)

    def test_a_failed_promotion_is_honest(self):
        msg = rqb.outcome_message("india", "Y", "PASS", "EXP-105",
                                  {"promoted": False, "reason": "could not promote (OSError: x)"})
        self.assertIn("NOT promoted", msg)

    def test_no_verdict_points_at_the_experiment(self):
        msg = rqb.outcome_message("us", "Z", "", "EXP-106", {"promoted": False, "reason": "no verdict"})
        self._short(msg)
        self.assertIn("EXP-106", msg)

    def test_the_name_comes_from_the_queue_row(self):
        d = tempfile.mkdtemp()
        research_queue._save(d, {"current": None, "history": [{"key": "us_hv", "name": "US High-Volume"}]}, "us")
        self.assertEqual(rqb.strategy_name(d, "us", "us_hv"), "US High-Volume")

    def test_run_lane_sends_exactly_one_research_outcome(self):
        d = tempfile.mkdtemp()
        data = {"current": {"key": "alpha", "started": "2026-10-05", "started_by": "auto",
                            "in_progress": True, "mode": "backtest"},
                "history": [{"key": "alpha", "name": "Alpha Strategy", "queued": "2026-10-05",
                             "started": "2026-10-05", "started_by": "auto", "mode": "backtest",
                             "resolved": None, "outcome": None, "experiment_id": None, "branch": None}]}
        research_queue._save(d, data, "india")
        with patch.object(rqb, "implemented_keys", return_value={"alpha"}), \
             patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Saved as EXP-321\n", stderr="")), \
             patch.object(rqb, "read_verdict", return_value="REJECT"), \
             patch("reporting.telegram_notifier.send_telegram_message") as send:
            rqb.run_lane("india", d, windows=3, send=True, token="t", chat_id="c")
        send.assert_called_once()
        msg = send.call_args.args[0]
        self.assertEqual(send.call_args.kwargs["kind"], "research_outcome")
        self.assertIn("Alpha Strategy (India): REJECT", msg)
        self.assertLessEqual(len(msg.splitlines()), 2)
        self.assertLessEqual(len(msg), 200)


class TestRetirementMessage(unittest.TestCase):
    RULE_A = {"key": "max_effect", "sid": "SW-005", "name": "Max Effect", "rule": ars.RULE_A,
              "days": 124, "trades": 31, "pnl": -4210.5, "retired": True}

    def test_rule_a_is_short_and_says_why(self):
        with patch.object(ars, "_pool_of", return_value="Pool A"):
            msg = ars.retirement_message(self.RULE_A)
        self.assertLessEqual(len(msg.splitlines()), 2)
        self.assertLessEqual(len(msg), 200)
        self.assertIn("Retired from paper trading — Max Effect (Pool A):", msg)
        self.assertIn("124 days", msg)
        self.assertIn("-₹4,210", msg)

    def test_main_announces_rule_a_and_mutes_rule_b(self):
        rule_b = {"key": "old_reject", "sid": "SW-002", "name": "Old Reject",
                  "rule": "not trading and the research verdict is REJECT", "days": None,
                  "trades": None, "pnl": None, "reason": "x"}
        rule_a = {**self.RULE_A, "reason": "y"}
        rule_a.pop("retired")
        with patch("sys.argv", ["auto_retire_strategies.py", "--send"]), \
             patch("deployment.retirement.candidates", return_value=[rule_a, rule_b]), \
             patch("deployment.retirement.retire", side_effect=lambda c: {**c, "retired": True, "error": ""}), \
             patch.object(ars, "_pool_of", return_value="Pool A"), \
             patch("reporting.telegram_notifier.send_telegram_message") as send:
            ars.main()
        kinds = [c.kwargs["kind"] for c in send.call_args_list]
        self.assertEqual(kinds, ["research_outcome", "general"])
        self.assertIn("Max Effect", send.call_args_list[0].args[0])


class TestNothingGoesAroundTheGate(unittest.TestCase):
    # Files allowed to name Telegram's API host, and why:
    #   reporting/telegram_notifier.py -- the gate itself (and setMyCommands/deleteMyCommands/getUpdates,
    #       which configure or read the bot and push no message).
    #   portfolio_b/telegram_bot.py -- getUpdates (reading Suraj's commands) and answerCallbackQuery,
    #       which is guarded by kind_allowed("bot_reply").
    ALLOWED = {os.path.join("reporting", "telegram_notifier.py"),
               os.path.join("portfolio_b", "telegram_bot.py")}
    SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__"}

    def test_no_other_source_file_calls_the_telegram_api(self):
        offenders = []
        for root, dirs, files in os.walk(REPO_DIR):
            dirs[:] = [x for x in dirs if x not in self.SKIP_DIRS]
            for name in files:
                if not name.endswith((".py", ".sh", ".js")) or name.startswith("test_"):
                    continue
                path = os.path.join(root, name)
                rel = os.path.relpath(path, REPO_DIR)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except OSError:
                    continue
                if "api.telegram.org" in text and rel not in self.ALLOWED:
                    offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_the_bot_answer_call_is_gated(self):
        with open(os.path.join(REPO_DIR, "portfolio_b", "telegram_bot.py"), encoding="utf-8") as f:
            text = f.read()
        body = text[text.index("def _answer_callback_query"):text.index("def poll_and_process_commands")]
        self.assertLess(body.index('kind_allowed("bot_reply")'), body.index("api.telegram.org"))

    def test_the_notifier_only_sends_after_the_gate(self):
        with open(tn.__file__, encoding="utf-8") as f:
            text = f.read()
        body = text[text.index("def send_telegram_message"):text.index("def set_bot_commands")]
        self.assertLess(body.index("if not kind_allowed(kind)"), body.index("requests.post"))

    def test_bot_replies_are_tagged(self):
        with open(os.path.join(REPO_DIR, "portfolio_b", "telegram_bot.py"), encoding="utf-8") as f:
            text = f.read()
        calls = re.findall(r"send_telegram_message\((.*?)\)\n", text, flags=re.S)
        self.assertTrue(calls)
        for c in calls:
            self.assertIn('kind="bot_reply"', c)


class TestKiteRenewalStillReachesTelegram(unittest.TestCase):
    """Suraj, 2026-10-20: "the kite-renewal message should come on telegram"."""

    def test_kite_renewal_is_an_allowed_kind_by_default(self):
        from deployment import settings as deployment_settings
        from reporting.telegram_notifier import DEFAULT_ALLOWED_KINDS
        self.assertIn("kite_renewal", deployment_settings.TELEGRAM_ALLOWED_KINDS)
        self.assertIn("kite_renewal", DEFAULT_ALLOWED_KINDS)

    def test_check_kite_subscription_sends_as_kite_renewal(self):
        import inspect
        import check_kite_subscription
        self.assertIn('kind="kite_renewal"', inspect.getsource(check_kite_subscription.main))


if __name__ == "__main__":
    unittest.main()
