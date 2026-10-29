"""
Tests for research_health.py and the nightly runner's handling of a broken test (2026-10-29): a test
that broke is re-run, never acted on; a second break leaves the verdict standing and asks for a look.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import research_health as rh
import research_queue


def _exp(root, exp_id, windows, oos, strategy="alpha", title=None):
    folder = os.path.join(root, exp_id)
    os.makedirs(folder)
    with open(os.path.join(folder, "metrics.json"), "w") as f:
        json.dump({"walk_forward_metrics": windows, "out_of_sample_metrics": oos}, f)
    if strategy:
        with open(os.path.join(folder, "run_manifest.json"), "w") as f:
            json.dump({"strategy_name": strategy}, f)
    with open(os.path.join(folder, "hypothesis.md"), "w") as f:
        f.write(f"# {title or strategy}\n")


def W(trades, pnl=1000.0, **extra):
    return {"total_trades": trades, "total_pnl": pnl, **extra}


class TestProblems(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def p(self, exp_id):
        return rh.problems(exp_id, roots=[self.root])

    def test_a_sound_test_has_no_problems(self):
        _exp(self.root, "EXP-1", [W(120), W(95)], W(140))
        self.assertEqual(self.p("EXP-1"), [])

    def test_exp_102s_collapsed_window_is_caught(self):
        _exp(self.root, "EXP-102", [W(24, -4425.16), W(8, -8333.8)], W(107, 153460.81), "industry_momentum")
        found = self.p("EXP-102")
        self.assertEqual(len(found), 1)
        self.assertIn("second test period had only 8 trades", found[0])

    def test_a_stalled_window_is_caught_even_without_a_collapse(self):
        _exp(self.root, "EXP-1", [W(60), W(55, stalled=True)], W(70))
        self.assertIn("stopped trading partway", self.p("EXP-1")[0])

    def test_a_window_with_no_trades_at_all_is_left_to_the_auditor(self):
        _exp(self.root, "EXP-1", [W(0), W(103)], W(104))       # indicator warm-up, as EXP-066
        self.assertEqual(self.p("EXP-1"), [])

    def test_a_window_identical_to_another_strategys_is_caught(self):
        _exp(self.root, "EXP-1", [W(30, 5000.0), W(31, -8335.74)], W(29), "us_hvrp")
        _exp(self.root, "EXP-2", [W(33, 7000.0), W(31, -8334.9)], W(35), "industry_momentum")
        self.assertIn("identical to one in EXP-2", self.p("EXP-1")[0])

    def test_two_runs_of_one_strategy_may_match_even_when_one_lacks_its_manifest(self):
        _exp(self.root, "EXP-36", [W(13, 31930.0), W(36, 128939.0)], W(43), strategy=None,
             title="Cross-Sectional Momentum")
        _exp(self.root, "EXP-37", [W(13, 31930.0), W(36, 128939.0)], W(42), "cross_sectional_momentum",
             title="Cross-Sectional Momentum")
        self.assertEqual(self.p("EXP-37"), [])

    def test_a_missing_experiment_is_not_a_problem(self):
        self.assertEqual(self.p("EXP-404"), [])


class TestRetestList(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_add_once_take_in_order_and_remove(self):
        rh.add_retest(self.dir, "india", "a", "EXP-1", ["x"])
        rh.add_retest(self.dir, "us", "b", "EXP-2", ["y"])
        rh.add_retest(self.dir, "india", "a", "EXP-3", ["z"])         # replaces, not duplicates
        self.assertEqual([r["key"] for r in rh.load_retests(self.dir)], ["b", "a"])
        self.assertEqual(rh.next_retest(self.dir)["key"], "b")
        rh.remove_retest(self.dir, "us", "b")
        self.assertEqual(rh.next_retest(self.dir)["key"], "a")
        self.assertEqual(rh.attempts_so_far(self.dir, "india", "a"), 1)


class _Runner(unittest.TestCase):
    """run_queued_backtest with the backtest, promotion and Telegram faked."""

    def setUp(self):
        import run_queued_backtest as rqb
        self.rqb = rqb
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.sent, self.promoted = [], []
        self.patches = [
            patch.object(rqb, "implemented_keys", return_value={"alpha"}),
            patch.object(rqb, "run_backtest", return_value=("EXP-9", "REJECT", "")),
            patch("deployment.auto_paper_promotion.promote",
                  side_effect=lambda key, verdict, **k: self.promoted.append((key, verdict)) or
                  {"promoted": False, "verdict": verdict, "key": key, "reason": "REJECT"}),
            patch("reporting.telegram_notifier.send_telegram_message",
                  side_effect=lambda m, *a, **k: self.sent.append((m, k.get("kind")))),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def claim(self):
        research_queue._set_current(self.dir, research_queue.load(self.dir, "india"), "alpha", "Alpha",
                                    "routine", "backtest", __import__("datetime").datetime(2026, 10, 29))
        research_queue.mark_in_progress(self.dir, "alpha")


class TestBrokenTestIsNotActedOn(_Runner):
    def test_a_broken_first_run_is_queued_for_a_retest_and_not_promoted(self):
        self.claim()
        with patch.object(rh, "problems", return_value=["the second test period had only 8 trades"]):
            self.assertTrue(self.rqb.run_lane("india", self.dir, 3, True, "t", "c"))
        self.assertEqual(self.promoted, [])
        row = research_queue.load(self.dir, "india")["history"][-1]
        self.assertEqual(row["outcome"], "retest_needed")
        self.assertIn("only 8 trades", row["note"])
        self.assertIsNone(research_queue.load(self.dir, "india")["current"])
        self.assertEqual(rh.next_retest(self.dir)["key"], "alpha")
        self.assertIn("looked broken", self.sent[0][0])
        self.assertEqual(self.sent[0][1], "research_outcome")

    def test_a_sound_run_goes_the_normal_way(self):
        self.claim()
        with patch.object(rh, "problems", return_value=[]):
            self.rqb.run_lane("india", self.dir, 3, False, "t", "c")
        self.assertEqual(self.promoted, [("alpha", "REJECT")])
        self.assertEqual(research_queue.load(self.dir, "india")["history"][-1]["outcome"], "researched")
        self.assertIsNone(rh.next_retest(self.dir))

    def test_the_nightly_run_takes_the_retest_before_new_research(self):
        state = os.path.join(self.dir, "deployment", "state")       # where main() keeps its state
        os.makedirs(state)
        rh.add_retest(state, "india", "alpha", "EXP-8", ["x"])
        with patch.object(rh, "problems", return_value=[]), \
             patch.object(self.rqb, "run_lane") as lane, \
             patch.object(self.rqb, "REPO_DIR", self.dir), \
             patch.object(self.rqb, "LOCK_PATH", os.path.join(self.dir, "lock")), \
             patch("sys.argv", ["run_queued_backtest.py"]):
            self.rqb.main()
        lane.assert_not_called()
        self.assertIsNone(rh.next_retest(state))
        self.assertEqual(research_queue.load(state, "india")["history"][-1]["experiment_id"], "EXP-9")


class TestSecondBreak(_Runner):
    def test_a_retest_that_breaks_again_keeps_its_verdict_and_asks_for_a_look(self):
        rh.add_retest(self.dir, "india", "alpha", "EXP-8", ["x"])
        with patch.object(rh, "problems", return_value=["the second test period had only 7 trades"]):
            self.rqb.retest("india", "alpha", self.dir, 3, True, "t", "c")
        self.assertIsNone(rh.next_retest(self.dir))                       # no loop
        self.assertEqual(self.promoted, [("alpha", "REJECT")])            # verdict stands
        kinds = [k for _, k in self.sent]
        self.assertIn("research_alert", kinds)
        self.assertIn("still looks broken", next(m for m, k in self.sent if k == "research_alert"))
        self.assertIn("still looked broken", research_queue.load(self.dir, "india")["history"][-1]["note"])

    def test_a_retest_that_never_finishes_is_dropped_with_an_alert(self):
        rh.add_retest(self.dir, "india", "alpha", "EXP-8", ["x"])
        with patch.object(self.rqb, "run_backtest", return_value=("", "", "boom")):
            self.assertFalse(self.rqb.retest("india", "alpha", self.dir, 3, True, "t", "c"))
        self.assertIsNone(rh.next_retest(self.dir))
        self.assertEqual(self.sent[0][1], "research_alert")


class TestNotificationsRow(unittest.TestCase):
    def test_a_retest_needed_row_says_so_with_the_reason(self):
        from reporting.lifecycle import timeline
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        q = research_queue.load(d, "india")
        q["history"] = [{"key": "alpha", "name": "Alpha", "resolved": "2026-10-30", "outcome": "retest_needed",
                         "experiment_id": "EXP-9", "note": "test looked broken: the second test period had only 8 trades"}]
        research_queue._save(d, q, "india")
        rows = timeline(d, records=[])
        self.assertEqual(rows[0]["what"], "Test looked broken: re-testing")
        self.assertIn("only 8 trades", rows[0]["detail"])


if __name__ == "__main__":
    unittest.main()
