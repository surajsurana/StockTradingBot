"""
Tests for research_merge_gate.py -- the four mechanical facts that replaced a person reading a diff.

    python test_research_merge_gate.py
"""

import subprocess
import unittest
from unittest.mock import patch

import research_merge_gate as gate


def _diff(files, removed_outside_tests=0, added_lines=()):
    """Fake the three git reads review() makes, in the order it makes them."""
    name_only = subprocess.CompletedProcess([], 0, stdout="\n".join(files), stderr="")
    non_test = subprocess.CompletedProcess(
        [], 0, stdout="\n".join(["+++ b/x"] + ["-gone" for _ in range(removed_outside_tests)]), stderr="")
    whole = subprocess.CompletedProcess(
        [], 0, stdout="\n".join(["+++ b/x"] + [f"+{l}" for l in added_lines]), stderr="")
    return [name_only, non_test, whole]


class TestTheFourChecks(unittest.TestCase):
    CLEAN = ["swing_research/strategies/alpha.py", "swing_research/strategy_catalog.py",
             "test_alpha.py"]

    def _review(self, **kw):
        with patch.object(gate, "_git", side_effect=_diff(**kw)):
            return gate.review("origin/research/alpha")

    def test_an_ordinary_research_branch_passes(self):
        r = self._review(files=self.CLEAN)
        self.assertTrue(r["ok"], r["reasons"])
        self.assertEqual([c["check"] for c in r["checks"]],
                         ["has changes", "nowhere near money", "additions only", "no reach"])

    def test_a_branch_that_touches_deployment_is_refused(self):
        r = self._review(files=self.CLEAN + ["deployment/live_guard.py"])
        self.assertFalse(r["ok"])
        self.assertIn("deployment/live_guard.py", " ".join(r["reasons"]))

    def test_a_branch_that_touches_a_live_runner_is_refused(self):
        for f in ("run_live.py", "run_pool_live.py", "run_paper_trading.py", "run_pool_g.py"):
            with self.subTest(f=f):
                r = self._review(files=self.CLEAN + [f])
                self.assertFalse(r["ok"], f)

    def test_a_branch_that_touches_reporting_or_the_dashboard_is_refused(self):
        for f in ("reporting/pool_g.py", "dashboard/state_view.py", "execution/coindcx_client.py"):
            with self.subTest(f=f):
                self.assertFalse(self._review(files=self.CLEAN + [f])["ok"], f)

    def test_rewriting_existing_code_outside_tests_is_refused(self):
        """A research branch ADDS a strategy. Rewriting something that is already there is a change
        to behaviour nobody asked it to make."""
        r = self._review(files=self.CLEAN, removed_outside_tests=3)
        self.assertFalse(r["ok"])
        self.assertIn("3 line(s) removed", " ".join(r["reasons"]))

    def test_reaching_for_a_broker_or_a_credential_is_refused(self):
        for token in ("from execution.coindcx_client import Client", "import kiteconnect",
                      "client.place_order(x)", "API_SECRET = os.environ"):
            with self.subTest(token=token):
                r = self._review(files=self.CLEAN, added_lines=[token])
                self.assertFalse(r["ok"], token)
                self.assertIn("no reach", " ".join(r["reasons"]))

    def test_a_branch_that_changes_nothing_is_refused(self):
        self.assertFalse(self._review(files=[])["ok"])

    def test_a_refusal_always_names_the_fact_that_failed(self):
        r = self._review(files=self.CLEAN + ["deployment/x.py"], removed_outside_tests=1)
        self.assertEqual(len(r["reasons"]), 2)
        self.assertTrue(all(":" in reason for reason in r["reasons"]))


class TestItNeverMergesIntoTheLiveCheckout(unittest.TestCase):
    """A conflict or a red suite must not be able to leave the box that trades your money in a
    half-merged state, so everything happens in a throwaway worktree built from origin/main."""

    def test_the_merge_and_the_suite_both_run_somewhere_else(self):
        with open(gate.__file__, encoding="utf-8") as f:
            code = f.read()
        body = code[code.index("def merge("):code.index("def _message(")]
        self.assertIn('_git(["worktree", "add", "--detach", tree, "origin/main"]', body)
        self.assertIn("cwd=tree", body)
        self.assertIn("_run_suite(tree)", body)

    def test_the_worktree_is_always_removed(self):
        with open(gate.__file__, encoding="utf-8") as f:
            code = f.read()
        body = code[code.index("def merge("):code.index("def _message(")]
        self.assertIn("finally:", body)
        after = body[body.index("finally:"):]
        self.assertIn('"worktree", "remove", "--force"', after)
        self.assertIn("shutil.rmtree(tmp, ignore_errors=True)", after)

    def test_a_red_suite_is_never_pushed(self):
        calls = []

        def fake_git(args, cwd=None, timeout=180):
            calls.append(args[0])
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with patch.object(gate, "_git", side_effect=fake_git), \
             patch.object(gate, "_run_suite", return_value=(False, "FAILED (failures=1)")), \
             patch("tempfile.mkdtemp", return_value="/tmp/x"), \
             patch("shutil.rmtree"):
            r = gate.merge("research/alpha")
        self.assertFalse(r["merged"])
        self.assertIn("test suite fails", r["reason"])
        self.assertNotIn("push", calls)

    def test_a_conflict_is_reported_and_not_forced(self):
        def fake_git(args, cwd=None, timeout=180):
            code = 1 if args[0] == "merge" else 0
            return subprocess.CompletedProcess([], code, stdout="", stderr="")

        with patch.object(gate, "_git", side_effect=fake_git), \
             patch("tempfile.mkdtemp", return_value="/tmp/x"), patch("shutil.rmtree"):
            r = gate.merge("research/alpha")
        self.assertFalse(r["merged"])
        self.assertIn("needs a person", r["reason"])

    def test_a_green_suite_is_pushed(self):
        pushed = []

        def fake_git(args, cwd=None, timeout=180):
            if args[0] == "push":
                pushed.append(args)
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with patch.object(gate, "_git", side_effect=fake_git), \
             patch.object(gate, "_run_suite", return_value=(True, "OK")), \
             patch("tempfile.mkdtemp", return_value="/tmp/x"), patch("shutil.rmtree"):
            r = gate.merge("research/alpha")
        self.assertTrue(r["merged"])
        self.assertEqual(pushed, [["push", "origin", "HEAD:main"]])

    def test_a_dry_run_checks_everything_and_pushes_nothing(self):
        calls = []

        def fake_git(args, cwd=None, timeout=180):
            calls.append(args[0])
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with patch.object(gate, "_git", side_effect=fake_git), \
             patch.object(gate, "_run_suite", return_value=(True, "OK")), \
             patch("tempfile.mkdtemp", return_value="/tmp/x"), patch("shutil.rmtree"):
            r = gate.merge("research/alpha", push=False)
        self.assertTrue(r["merged"])
        self.assertNotIn("push", calls)


class TestItOnlyActsOnWorkThatIsWaiting(unittest.TestCase):
    def _consider(self, current, implemented=(), branch_exists=True):
        with patch("research_queue.load", return_value={"current": current, "history": []}), \
             patch("run_queued_backtest.implemented_keys", return_value=set(implemented)), \
             patch.object(gate, "_git",
                          return_value=subprocess.CompletedProcess([], 0 if branch_exists else 1, "", "")), \
             patch.object(gate, "review", return_value={"ok": True, "reasons": [], "checks": []}), \
             patch.object(gate, "merge", return_value={"merged": True, "reason": "merged"}):
            return gate.consider("x", "india")

    def test_nothing_claimed_means_nothing_to_do(self):
        self.assertIsNone(self._consider(None))
        self.assertIsNone(self._consider({"key": "alpha", "in_progress": False}))

    def test_something_already_merged_is_left_alone(self):
        self.assertIsNone(self._consider({"key": "alpha", "in_progress": True}, implemented=["alpha"]))

    def test_a_claim_with_no_branch_yet_is_left_alone(self):
        self.assertIsNone(self._consider({"key": "alpha", "in_progress": True}, branch_exists=False))

    def test_delivered_and_unmerged_is_what_it_acts_on(self):
        r = self._consider({"key": "alpha", "in_progress": True})
        self.assertEqual((r["key"], r["merged"]), ("alpha", True))

    def test_a_refusal_reads_as_asking_for_a_person(self):
        said = gate.line({"lane": "india", "key": "alpha", "merged": False, "reason": "touches deployment/x.py"})
        self.assertIn("needs you", said)
        self.assertIn("touches deployment/x.py", said)

    def test_a_merge_reads_as_done(self):
        said = gate.line({"lane": "india", "key": "alpha", "merged": True, "reason": "merged to main"})
        self.assertIn("merged automatically", said)


class TestItChangesNoMoney(unittest.TestCase):
    def test_it_cannot_promote_allocate_or_trade(self):
        """It decides what code may be BACKTESTED. Nothing here moves money, and paper -> live keeps
        every gate it has. (The names it refuses are in FORBIDDEN_TOKENS, which is the point of the
        module -- that literal is cut out before scanning, or the check would trip on itself.)"""
        with open(gate.__file__, encoding="utf-8") as f:
            code = f.read()
        head = code.index("FORBIDDEN_TOKENS")
        code = code[:head] + code[code.index(")", code.index("access_token", head)):]
        code = " ".join(l.split("#")[0] for l in code.splitlines())
        for forbidden in ("set_deployment_status", "set_allocation", "PILOT_LIVE", "promote(",
                          "place_order("):
            self.assertNotIn(forbidden, code, forbidden)

class TestTheWorktreeHasItsSettings(unittest.TestCase):
    """2026-10-22: a fresh worktree has no config/settings.py (git-ignored), so ~120 tests failed on
    import even on clean main and the gate refused every research branch for a week."""

    def _tree(self):
        import os
        import tempfile
        tree = tempfile.mkdtemp()
        os.makedirs(os.path.join(tree, "config"))
        with open(os.path.join(tree, "config", "settings.example.py"), "w", encoding="utf-8") as f:
            f.write("TELEGRAM_BOT_TOKEN = ''\n")
        return tree

    def test_the_template_is_copied_in(self):
        import os
        tree = self._tree()
        gate.prepare_worktree(tree)
        with open(os.path.join(tree, "config", "settings.py"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "TELEGRAM_BOT_TOKEN = ''\n")

    def test_an_existing_settings_file_is_never_overwritten(self):
        import os
        tree = self._tree()
        with open(os.path.join(tree, "config", "settings.py"), "w", encoding="utf-8") as f:
            f.write("KEEP = 1\n")
        gate.prepare_worktree(tree)
        with open(os.path.join(tree, "config", "settings.py"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "KEEP = 1\n")

    def test_it_runs_after_the_merge_and_before_the_suite(self):
        with open(gate.__file__, encoding="utf-8") as f:
            code = f.read()
        body = code[code.index("def merge("):code.index("def _message(")]
        self.assertLess(body.index('_git(["merge"'), body.index("prepare_worktree(tree)"))
        self.assertLess(body.index("prepare_worktree(tree)"), body.index("_run_suite(tree)"))
        # The template, never the real credentials file.
        prep = code[code.index("def prepare_worktree("):code.index("def merge(")]
        self.assertIn('"settings.example.py"', prep)

class TestTheSuiteRunner(unittest.TestCase):
    """2026-10-22: with the settings fixed, the gate still refused both branches on the VPS -- once
    for a test that needs node (not on cron's PATH) and once for a timing test that flakes under load."""

    FAILED = ("FAIL: test_spaces (test_fetch_kite_intraday.TestRateLimiter.test_spaces)\n"
              "Ran 10 tests\nFAILED (failures=1)")

    def _proc(self, code, out=""):
        return subprocess.CompletedProcess([], code, stdout=out, stderr="")

    def test_a_test_that_passes_on_one_rerun_does_not_block(self):
        with patch("subprocess.run", side_effect=[self._proc(1, self.FAILED), self._proc(0, "OK")]) as run:
            passed, tail = gate._run_suite("/tmp/x")
        self.assertTrue(passed)
        self.assertIn("test_fetch_kite_intraday.TestRateLimiter.test_spaces", run.call_args_list[1][0][0])
        self.assertIn("re-run", tail)

    def test_a_test_that_fails_twice_blocks(self):
        with patch("subprocess.run", side_effect=[self._proc(1, self.FAILED), self._proc(1, self.FAILED)]):
            passed, _ = gate._run_suite("/tmp/x")
        self.assertFalse(passed)

    def test_many_failures_are_never_retried(self):
        many = "\n".join(f"ERROR: test_{i} (test_mod.T.test_{i})" for i in range(gate.MAX_RETRIED_TESTS + 1))
        with patch("subprocess.run", side_effect=[self._proc(1, many)]) as run:
            passed, _ = gate._run_suite("/tmp/x")
        self.assertFalse(passed)
        self.assertEqual(run.call_count, 1)

    def test_the_suite_knows_it_is_running_under_the_gate(self):
        self.assertEqual(gate._suite_env()["STB_RESEARCH_MERGE_GATE"], "1")

    def test_the_gate_refuses_dashboard_changes_which_is_why_the_node_check_may_skip(self):
        r = TestTheFourChecks()._review.__func__(TestTheFourChecks(), files=["dashboard/index.html", "test_a.py"])
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
