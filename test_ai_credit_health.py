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


def _log(logs_dir, name, text):
    path = os.path.join(logs_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _state(state_dir, rel):
    path = os.path.join(state_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{}")
    return path


class TestNoticingTheOutage(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.logs = tempfile.mkdtemp()

    def _log(self, name, text, when=None):
        path = os.path.join(self.logs, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if when is not None:
            os.utime(path, (when, when))
        return path

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
        self._log("pool_g.log", "starting" + chr(10) + ERROR_LINE,
                  when=time.mktime((2026, 10, 13, 8, 30, 0, 0, 0, -1)))
        self._state("pool_g/portfolio.json", when=time.mktime((2026, 10, 11, 8, 30, 0, 0, 0, -1)))
        r = check(self.state, self.logs)
        self.assertTrue(r["blocked"])
        self.assertTrue(r["since"].startswith("2026-10-13"), r["since"])
        self.assertTrue(r["books"][0]["last_ran"].startswith("2026-10-11"))
        self.assertIn("Pool G", r["books"][0]["name"])
        self.assertIn("real positions", r["message"])

    def test_a_book_that_has_run_since_is_not_blocked(self):
        """Whatever that was, it is over -- and a warning that stays up after the thing is fixed is
        a warning people learn to ignore."""
        self._log("pool_g.log", "starting" + chr(10) + ERROR_LINE,
                  when=time.mktime((2026, 10, 11, 8, 30, 0, 0, 0, -1)))
        self._state("pool_g/portfolio.json", when=time.mktime((2026, 10, 13, 8, 30, 0, 0, 0, -1)))
        self.assertFalse(check(self.state, self.logs)["blocked"])

    def test_a_log_with_no_dates_in_it_is_still_noticed(self):
        """THE BUG THIS REPLACED. The first version read the newest date stamp ABOVE the error, and
        Pool G prints none near its traceback -- so it matched a stamp from two runs earlier, decided
        the book had run since, and the live book read as healthy while it was down."""
        self._log("pool_g.log", "Traceback" + chr(10) + ERROR_LINE,
                  when=time.mktime((2026, 10, 13, 8, 30, 0, 0, 0, -1)))
        self._state("pool_g/portfolio.json", when=time.mktime((2026, 10, 11, 8, 30, 0, 0, 0, -1)))
        self.assertTrue(check(self.state, self.logs)["blocked"])

    def test_a_book_with_no_state_file_at_all_is_blocked(self):
        self._log("pool_g.log", ERROR_LINE, when=time.mktime((2026, 10, 13, 8, 30, 0, 0, 0, -1)))
        self.assertTrue(check(self.state, self.logs)["blocked"])

    def test_it_clears_itself_when_a_run_finally_works(self):
        """THE QUESTION THIS HAS TO ANSWER: how does the bar come down after you top up? A run that
        works writes its state file and its log seconds apart. A failed one leaves the state a whole
        cron interval behind. Requiring a GAP is what tells them apart -- a success writes the log
        LAST, so plain "log is newer" is true even then and the warning would never come down."""
        ok = time.mktime((2026, 10, 14, 8, 30, 0, 0, 0, -1))
        self._log("pool_g.log", "starting" + chr(10) + ERROR_LINE + chr(10) + "then it worked",
                  when=ok + 20)                      # log finishes 20s after the state is written
        self._state("pool_g/portfolio.json", when=ok)
        self.assertFalse(check(self.state, self.logs)["blocked"])

    def test_a_run_a_whole_cycle_behind_is_still_blocked(self):
        failed = time.mktime((2026, 10, 14, 8, 30, 0, 0, 0, -1))
        self._log("pool_g.log", ERROR_LINE, when=failed)
        self._state("pool_g/portfolio.json", when=failed - 12 * 3600)
        self.assertTrue(check(self.state, self.logs)["blocked"])

    def test_one_book_working_clears_it_for_all_of_them(self):
        """CREDIT IS ONE ACCOUNT-WIDE THING. Pool G recovered at 20:30 on 2026-10-13 and proved the
        balance was back; Portfolio B and C had not had a turn yet -- they run on weekday mornings,
        fourteen hours away -- and the bar stayed up telling the user credit had run out when a
        successful call had just disproved it."""
        ok = time.mktime((2026, 10, 13, 20, 30, 0, 0, 0, -1))
        earlier = time.mktime((2026, 10, 13, 15, 50, 0, 0, 0, -1))
        # Pool G: worked at 20:30 (state and log seconds apart)
        self._log("pool_g.log", ERROR_LINE + chr(10) + "then it worked", when=ok + 15)
        self._state("pool_g/portfolio.json", when=ok)
        # Portfolio B: refused at 15:50 and has not run since 2026-10-09
        self._log("portfolio_b.log", ERROR_LINE, when=earlier)
        self._state("portfolio_b/portfolio.json",
                    when=time.mktime((2026, 10, 9, 15, 51, 0, 0, 0, -1)))
        self.assertFalse(check(self.state, self.logs)["blocked"])

    def test_a_refusal_newer_than_every_success_still_blocks(self):
        """The other direction: if the most recent thing that happened anywhere was a refusal, the
        account really is short and the warning belongs on screen."""
        worked = time.mktime((2026, 10, 13, 8, 30, 0, 0, 0, -1))
        refused = time.mktime((2026, 10, 13, 20, 30, 0, 0, 0, -1))
        self._log("pool_g.log", ERROR_LINE, when=refused)
        self._state("pool_g/portfolio.json", when=worked)
        self._log("portfolio_b.log", "all fine" + chr(10), when=worked + 15)
        self._state("portfolio_b/portfolio.json", when=worked)
        self.assertTrue(check(self.state, self.logs)["blocked"])

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


class TestTheCheckIsAccountWideNotPerView(unittest.TestCase):
    """WHAT WENT WRONG ON 2026-10-14. The dashboard passed the VIEW's state root, so in Live mode the
    check looked for the AI books under deployment/state/live/. Portfolio B and C are paper books and
    have never existed there, so their state files could not be found, their successful runs were
    invisible, and an old refusal still sitting in their never-rotated logs was read as current --
    for ever. Live showed "the Anthropic account is out of credit" permanently while Paper, reading
    the same logs from the right root, said everything was fine. The account had credit throughout.

    Whether the account has credit is one account-wide fact. It has nothing to do with which mode
    somebody is looking at."""

    def test_the_dashboard_passes_the_canonical_state_dir(self):
        import dashboard.state_view as mod
        with open(mod.__file__, encoding="utf-8") as f:
            code = f.read()
        self.assertIn('"ai_credits": _ai_credit_health(STATE_DIR_CANONICAL, logs_dir)', code)
        self.assertNotIn('"ai_credits": _ai_credit_health(state_dir, logs_dir)', code)

    def test_a_book_whose_state_file_is_missing_entirely(self):
        """What the live root looked like: the log is there with an old refusal in it, the state
        file is not. Reported blocked, with a run time it could not name -- which is the shape the
        bug showed up in (last_ran: "")."""
        logs, state = tempfile.mkdtemp(), tempfile.mkdtemp()
        _log(logs, "portfolio_b.log", "boom: credit balance is too low")
        out = check(state, logs)
        self.assertTrue(out["blocked"])
        self.assertEqual([b["last_ran"] for b in out["books"]], [""])

    def test_the_same_logs_read_from_the_right_root_are_not_an_outage(self):
        """The fix, end to end: give it the root where those books actually live and the recent run
        beside the old refusal settles it."""
        logs, state = tempfile.mkdtemp(), tempfile.mkdtemp()
        _log(logs, "portfolio_b.log", "boom: credit balance is too low")
        _state(state, os.path.join("portfolio_b", "portfolio.json"))
        self.assertFalse(check(state, logs)["blocked"])
