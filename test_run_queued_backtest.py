"""run_queued_backtest.py -- the VPS-side backtest step for an already-implemented research candidate."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import research_queue
import run_queued_backtest as rqb


def _queue(state_dir, lane, key, in_progress=True, mode="backtest", experiment_id=None):
    """Writes a queue file directly -- the state shapes here are the ones the real routine produces."""
    data = {"current": {"key": key, "started": "2026-10-05", "started_by": "auto",
                        "in_progress": in_progress, "mode": mode},
            "history": [{"key": key, "name": key, "queued": "2026-10-05", "started": "2026-10-05",
                         "started_by": "auto", "mode": mode, "resolved": None, "outcome": None,
                         "experiment_id": experiment_id, "branch": None}]}
    research_queue._save(state_dir, data, lane)
    return data


class TestPendingCandidate(unittest.TestCase):
    def test_nothing_queued(self):
        self.assertEqual(rqb.pending_candidate(tempfile.mkdtemp(), "india")[0], None)

    def test_a_queued_but_unclaimed_candidate_is_not_ready(self):
        d = tempfile.mkdtemp()
        _queue(d, "india", "alpha", in_progress=False)
        key, reason = rqb.pending_candidate(d, "india")
        self.assertIsNone(key)
        self.assertIn("not claimed", reason)

    def test_a_paper_direct_candidate_is_never_backtested(self):
        # the whole point of paper_direct is that no backtest is possible -- that path stays with the
        # routine, which proposes a paper-trading pool instead.
        d = tempfile.mkdtemp()
        _queue(d, "india", "alpha", mode="paper_direct")
        key, reason = rqb.pending_candidate(d, "india")
        self.assertIsNone(key)
        self.assertIn("no backtest", reason)

    def test_an_unmerged_implementation_is_not_run(self):
        # the merge gate: claimed, but its key isn't in the lane's experiment catalog yet, so no human
        # has reviewed the code and this machine must not execute it.
        d = tempfile.mkdtemp()
        _queue(d, "india", "not_a_real_strategy_key")
        key, reason = rqb.pending_candidate(d, "india")
        self.assertIsNone(key)
        self.assertIn("not merged", reason)

    def test_a_merged_implementation_is_ready(self):
        d = tempfile.mkdtemp()
        with patch.object(rqb, "implemented_keys", return_value={"alpha"}):
            _queue(d, "india", "alpha")
            self.assertEqual(rqb.pending_candidate(d, "india")[0], "alpha")

    def test_a_candidate_that_already_has_an_experiment_is_not_rerun(self):
        d = tempfile.mkdtemp()
        with patch.object(rqb, "implemented_keys", return_value={"alpha"}):
            _queue(d, "india", "alpha", experiment_id="EXP-099")
            key, reason = rqb.pending_candidate(d, "india")
            self.assertIsNone(key)
            self.assertIn("EXP-099", reason)


class TestImplementedKeys(unittest.TestCase):
    def test_the_real_india_catalog_is_readable_and_non_empty(self):
        keys = rqb.implemented_keys("india")
        self.assertIn("turtle_system2", keys)       # a strategy that has been in the catalog from the start

    def test_the_real_crypto_catalog_is_readable_and_non_empty(self):
        self.assertIn("crypto_tsmom", rqb.implemented_keys("crypto"))

    def test_the_us_lane_has_no_runner_yet_and_says_so_instead_of_raising(self):
        # us has only hand-written Pool I wrappers, no CLI -- must degrade, not crash the cron.
        self.assertEqual(rqb.implemented_keys("us"), set())

    def test_a_broken_catalog_is_reported_not_raised(self):
        with patch.dict(rqb.LANE_RUNNERS, {"india": ("x.py", MagicMock(side_effect=ImportError("boom")))}):
            self.assertEqual(rqb.implemented_keys("india"), set())


class TestReadVerdict(unittest.TestCase):
    def test_reads_the_verdict_word_from_the_experiment_folder(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "EXP-500"))
        with open(os.path.join(d, "EXP-500", "verdict.md"), "w", encoding="utf-8") as f:
            f.write("# Verdict: REJECT\n\nFailed the out-of-sample holdout.")
        with patch.object(rqb, "EXPERIMENTS_DIR", d):
            self.assertEqual(rqb.read_verdict("EXP-500"), "REJECT")

    def test_a_missing_experiment_folder_is_empty_not_an_exception(self):
        with patch.object(rqb, "EXPERIMENTS_DIR", tempfile.mkdtemp()):
            self.assertEqual(rqb.read_verdict("EXP-404"), "")


class TestRunLane(unittest.TestCase):
    def _run(self, state_dir, lane="india", returncode=0, stdout="Saved as EXP-321\n", verdict="PASS"):
        with patch.object(rqb, "implemented_keys", return_value={"alpha"}), \
             patch("subprocess.run", return_value=MagicMock(returncode=returncode, stdout=stdout, stderr="")), \
             patch.object(rqb, "read_verdict", return_value=verdict):
            return rqb.run_lane(lane, state_dir, windows=3, send=False, token="", chat_id="")

    def test_a_successful_run_resolves_the_queue_with_the_real_experiment_id(self):
        d = tempfile.mkdtemp()
        _queue(d, "india", "alpha")
        self.assertTrue(self._run(d))
        data = research_queue.load(d, "india")
        self.assertIsNone(data["current"])                      # unlocked -- the queue can move on
        row = data["history"][-1]
        self.assertEqual((row["outcome"], row["experiment_id"]), ("researched", "EXP-321"))
        self.assertEqual(row["branch"], "research/alpha")

    def test_a_crashed_experiment_leaves_the_candidate_claimed_so_it_retries(self):
        # resolving on failure would silently burn a candidate that never actually got researched.
        d = tempfile.mkdtemp()
        _queue(d, "india", "alpha")
        self.assertFalse(self._run(d, returncode=1, stdout="Traceback..."))
        data = research_queue.load(d, "india")
        self.assertEqual(data["current"]["key"], "alpha")
        self.assertTrue(data["current"]["in_progress"])
        self.assertIsNone(data["history"][-1]["outcome"])

    def test_a_run_that_prints_no_experiment_id_is_treated_as_a_failure(self):
        d = tempfile.mkdtemp()
        _queue(d, "india", "alpha")
        self.assertFalse(self._run(d, stdout="fetched 457 symbols\n...no id here"))
        self.assertIsNotNone(research_queue.load(d, "india")["current"])

    def test_a_reject_verdict_resolves_exactly_like_a_pass(self):
        # "researched" records that research HAPPENED; the verdict lives in the experiment.
        d = tempfile.mkdtemp()
        _queue(d, "india", "alpha")
        self.assertTrue(self._run(d, verdict="REJECT"))
        self.assertEqual(research_queue.load(d, "india")["history"][-1]["outcome"], "researched")

    def test_the_crypto_lane_records_its_own_branch_prefix(self):
        d = tempfile.mkdtemp()
        _queue(d, "crypto", "alpha")
        self.assertTrue(self._run(d, lane="crypto"))
        self.assertEqual(research_queue.load(d, "crypto")["history"][-1]["branch"], "research-crypto/alpha")

    def test_lanes_do_not_interfere(self):
        d = tempfile.mkdtemp()
        _queue(d, "india", "alpha")
        _queue(d, "crypto", "beta")
        self._run(d, lane="india")
        self.assertIsNone(research_queue.load(d, "india")["current"])
        self.assertEqual(research_queue.load(d, "crypto")["current"]["key"], "beta")   # untouched


if __name__ == "__main__":
    unittest.main()
