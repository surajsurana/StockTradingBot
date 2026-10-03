"""Tests for research_queue_github_sync.py -- the GitHub-polling replacement for the research
routine's old (broken, confirmed TCP-unreachable 2026-09-27/28) direct-HTTP-to-the-VPS design."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import research_queue
from research_queue_github_sync import _outcome_from_pr, publish_snapshot, sync_from_github


class TestOutcomeFromPr(unittest.TestCase):
    def test_pass_title(self):
        self.assertEqual(_outcome_from_pr({"title": "Research: Some Strategy (PASS)", "body": "Experiment-ID: EXP-099\nmore text"}),
                         ("researched", "EXP-099"))

    def test_reject_title(self):
        outcome, exp_id = _outcome_from_pr({"title": "Research: Some Strategy (REJECT)", "body": "Experiment-ID: EXP-050"})
        self.assertEqual((outcome, exp_id), ("researched", "EXP-050"))

    def test_inconclusive_title(self):
        outcome, _ = _outcome_from_pr({"title": "Research: Some Strategy (INCONCLUSIVE)", "body": ""})
        self.assertEqual(outcome, "researched")

    def test_paper_direct_title(self):
        self.assertEqual(_outcome_from_pr({"title": "Research: Some Strategy (proposed for paper trading)", "body": ""}),
                         ("paper_trading_proposed", None))

    def test_missing_experiment_id_in_body_gives_none(self):
        outcome, exp_id = _outcome_from_pr({"title": "Research: Some Strategy (PASS)", "body": "no id here"})
        self.assertEqual((outcome, exp_id), ("researched", None))

    def test_unrecognized_title_returns_none_none(self):
        self.assertEqual(_outcome_from_pr({"title": "Some unrelated PR", "body": ""}), (None, None))


class TestSyncFromGithub(unittest.TestCase):
    def test_nothing_queued_is_a_noop(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(sync_from_github(d))

    @patch("research_queue_github_sync._branch_exists", return_value=False)
    def test_not_yet_claimed_on_github_is_a_noop(self, _mock):
        d = tempfile.mkdtemp()
        research_queue._set_current(d, research_queue.load(d), "alpha", "ALPHA", "auto", "backtest", datetime(2026, 10, 1))
        self.assertIsNone(sync_from_github(d))
        self.assertFalse(research_queue.load(d)["current"]["in_progress"])

    @patch("research_queue_github_sync._branch_exists", return_value=True)
    def test_branch_found_locks_in_progress(self, _mock):
        d = tempfile.mkdtemp()
        research_queue._set_current(d, research_queue.load(d), "alpha", "ALPHA", "auto", "backtest", datetime(2026, 10, 1))
        result = sync_from_github(d)
        self.assertIn("locked in", result)
        self.assertTrue(research_queue.load(d)["current"]["in_progress"])

    @patch("research_queue_github_sync._find_pr_for_branch", return_value=None)
    def test_in_progress_but_no_pr_yet_is_a_noop(self, _mock):
        d = tempfile.mkdtemp()
        research_queue._set_current(d, research_queue.load(d), "alpha", "ALPHA", "auto", "backtest", datetime(2026, 10, 1))
        research_queue.mark_in_progress(d, "alpha")
        self.assertIsNone(sync_from_github(d))
        self.assertIsNotNone(research_queue.load(d)["current"])

    @patch("research_queue_github_sync._find_pr_for_branch",
          return_value={"number": 42, "title": "Research: Alpha Strategy (PASS)", "body": "Experiment-ID: EXP-012"})
    def test_pr_found_resolves_the_candidate(self, _mock):
        d = tempfile.mkdtemp()
        research_queue._set_current(d, research_queue.load(d), "alpha", "ALPHA", "auto", "backtest", datetime(2026, 10, 1))
        research_queue.mark_in_progress(d, "alpha")
        result = sync_from_github(d)
        self.assertIn("resolved", result)
        data = research_queue.load(d)
        self.assertIsNone(data["current"])
        row = data["history"][-1]
        self.assertEqual((row["outcome"], row["experiment_id"], row["branch"]), ("researched", "EXP-012", "research/alpha"))

    @patch("research_queue_github_sync._find_pr_for_branch", return_value={"number": 1, "title": "unrelated", "body": ""})
    def test_pr_with_unrecognized_title_does_not_resolve(self, _mock):
        d = tempfile.mkdtemp()
        research_queue._set_current(d, research_queue.load(d), "alpha", "ALPHA", "auto", "backtest", datetime(2026, 10, 1))
        research_queue.mark_in_progress(d, "alpha")
        self.assertIsNone(sync_from_github(d))
        self.assertIsNotNone(research_queue.load(d)["current"])


class TestPublishSnapshot(unittest.TestCase):
    def _fake_candidate_roadmap(self):
        from dataclasses import dataclass

        @dataclass
        class C:
            key: str
            name: str
            horizon_lane: str = "swing"

        @dataclass
        class S:
            candidate: C
            total_score: float = 1.0
            feasibility_classification: str = "IMPLEMENTABLE"

        return {"all_scored": [S(C("alpha", "ALPHA"))]}

    @staticmethod
    def _fake_run(has_diff):
        def run(cmd, **kwargs):
            if cmd[:3] == ["git", "diff", "--cached"]:
                return MagicMock(returncode=1 if has_diff else 0)
            return MagicMock(returncode=0)
        return run

    @patch("subprocess.run")
    def test_writes_commits_and_pushes_when_content_changes(self, mock_run):
        mock_run.side_effect = self._fake_run(has_diff=True)
        repo_dir = tempfile.mkdtemp()
        state_dir = tempfile.mkdtemp()
        research_queue._set_current(state_dir, research_queue.load(state_dir), "alpha", "ALPHA", "auto", "backtest", datetime(2026, 10, 1))
        changed = publish_snapshot(repo_dir, state_dir, self._fake_candidate_roadmap())
        self.assertTrue(changed)
        self.assertEqual(mock_run.call_count, 5)   # pull, add, diff-check, commit, push
        with open(os.path.join(repo_dir, "swing_research", "research_queue_snapshot.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["current"]["key"], "alpha")

    @patch("subprocess.run")
    def test_no_change_vs_head_skips_the_commit(self, mock_run):
        # Regression: must check against git's HEAD (via `git diff --cached`), not the working
        # tree -- a prior run that wrote the file but failed to commit (e.g. no git identity
        # configured) must NOT look "already published" forever.
        mock_run.side_effect = self._fake_run(has_diff=False)
        repo_dir = tempfile.mkdtemp()
        state_dir = tempfile.mkdtemp()
        changed = publish_snapshot(repo_dir, state_dir, {"all_scored": []})
        self.assertFalse(changed)
        self.assertEqual(mock_run.call_count, 3)   # pull, add, diff-check -- no commit/push
        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertNotIn(["git", "commit", "-m", "Research queue snapshot: automated update"], calls)
        self.assertNotIn(["git", "push", "origin", "main"], calls)

    @patch("subprocess.run")
    def test_a_prior_failed_commit_is_retried_not_silently_skipped(self, mock_run):
        # Simulates exactly what happened in production: the file got written and `git add`ed by
        # an earlier run, but `git commit` failed (no user.name/user.email configured) -- the next
        # run must still see a real diff against HEAD and complete the commit+push.
        repo_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(repo_dir, "swing_research"))
        with open(os.path.join(repo_dir, "swing_research", "research_queue_snapshot.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"current": {"key": "alpha"}}, indent=2) + "\n")   # left behind, never committed
        state_dir = tempfile.mkdtemp()
        research_queue._set_current(state_dir, research_queue.load(state_dir), "alpha", "ALPHA", "auto", "backtest", datetime(2026, 10, 1))
        mock_run.side_effect = self._fake_run(has_diff=True)
        changed = publish_snapshot(repo_dir, state_dir, self._fake_candidate_roadmap())
        self.assertTrue(changed)
        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertIn(["git", "commit", "-m", "Research queue snapshot: automated update"], calls)
        self.assertIn(["git", "push", "origin", "main"], calls)

    @patch("subprocess.run", side_effect=OSError("git not found"))
    def test_git_failure_is_swallowed_not_raised(self, _mock):
        repo_dir = tempfile.mkdtemp()
        state_dir = tempfile.mkdtemp()
        research_queue._set_current(state_dir, research_queue.load(state_dir), "alpha", "ALPHA", "auto", "backtest", datetime(2026, 10, 1))
        changed = publish_snapshot(repo_dir, state_dir, self._fake_candidate_roadmap())
        self.assertFalse(changed)


if __name__ == "__main__":
    unittest.main()
