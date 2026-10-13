"""
Tests for reporting/ai_credit_health.py -- noticing that the AI books have stopped for want of
Anthropic credit.

    python test_ai_credit_health.py
"""

import os
import tempfile
import time
import unittest

from reporting.ai_credit_health import CREDIT_MESSAGE, check

ERROR_LINE = ("Traceback (most recent call last):\n"
              "anthropic.BadRequestError: Error code: 400 - {'message': 'Your "
              + CREDIT_MESSAGE + " to access the Anthropic API.'}\n")


class TestNoticingTheOutage(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.logs = tempfile.mkdtemp()

    def _log(self, name, text):
        with open(os.path.join(self.logs, name), "w", encoding="utf-8") as f:
            f.write(text)

    def _state(self, rel, when=None):
        path = os.path.join(self.state, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")
        if when is not None:
            os.utime(path, (when, when))
        return path

    def test_a_quiet_desk_reports_nothing(self):
        r = check(self.state, self.logs)
        self.assertFalse(r["blocked"])
        self.assertEqual(r["books"], [])
        self.assertEqual(r["message"], "")

    def test_a_credit_refusal_with_no_run_since_is_blocked(self):
        self._log("pool_g.log", "[2026-10-11] starting\n" + ERROR_LINE)
        self._state("pool_g/portfolio.json", when=time.mktime((2026, 10, 11, 8, 30, 0, 0, 0, -1)))
        r = check(self.state, self.logs)
        self.assertTrue(r["blocked"])
        self.assertEqual(r["since"], "2026-10-11")
        self.assertIn("Pool G", r["books"][0]["name"])
        self.assertIn("real positions", r["message"])

    def test_a_book_that_has_run_since_is_not_blocked(self):
        """Whatever that was, it is over -- and a warning that stays up after the thing is fixed is
        a warning people learn to ignore."""
        self._log("pool_g.log", "[2026-10-11] starting\n" + ERROR_LINE)
        self._state("pool_g/portfolio.json", when=time.mktime((2026, 10, 13, 8, 30, 0, 0, 0, -1)))
        self.assertFalse(check(self.state, self.logs)["blocked"])

    def test_an_ordinary_error_is_not_this_error(self):
        self._log("pool_g.log", "[2026-10-11] RuntimeError: data provider outage\n")
        self.assertFalse(check(self.state, self.logs)["blocked"])

    def test_every_ai_book_is_watched_not_just_pool_g(self):
        for log, state in (("pool_g.log", "pool_g/portfolio.json"),
                           ("portfolio_b.log", "portfolio_b/portfolio.json"),
                           ("portfolio_c.log", "portfolio_c/portfolio.json")):
            self._log(log, "[2026-10-11] starting\n" + ERROR_LINE)
            self._state(state, when=time.mktime((2026, 10, 11, 8, 30, 0, 0, 0, -1)))
        r = check(self.state, self.logs)
        self.assertEqual(len(r["books"]), 3)

    def test_it_reads_only_the_tail_of_a_big_log(self):
        """These logs grow for months; reading them whole on every 15-second refresh would not be
        free, and only the last run matters."""
        self._log("pool_g.log", ("noise\n" * 200000) + "[2026-10-11] starting\n" + ERROR_LINE)
        self._state("pool_g/portfolio.json", when=time.mktime((2026, 10, 11, 8, 30, 0, 0, 0, -1)))
        self.assertTrue(check(self.state, self.logs)["blocked"])

    def test_an_unreadable_log_is_not_an_outage(self):
        self.assertFalse(check(self.state, "/no/such/directory")["blocked"])


class TestItOnlyReads(unittest.TestCase):
    def test_it_writes_nothing(self):
        import reporting.ai_credit_health as mod
        with open(mod.__file__, encoding="utf-8") as f:
            code = " ".join(l.split("#")[0] for l in f.read().splitlines())
        for forbidden in ('"w"', "json.dump", "os.replace", "makedirs", "send_telegram"):
            self.assertNotIn(forbidden, code, forbidden)


if __name__ == "__main__":
    unittest.main()
