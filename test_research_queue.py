"""Tests for research_queue.py -- the "one strategy at a time, weekly, or start one by hand" queue."""
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime

from research_queue import advance, load, mark_in_progress, resolve, start_now


@dataclass
class _FakeCandidate:
    key: str
    name: str


@dataclass
class _FakeScored:
    candidate: _FakeCandidate
    total_score: float = 5.0
    feasibility_classification: str = "IMPLEMENTABLE"


def _roadmap(keys, paper_direct_keys=()):
    """`keys` rank in the given order (first = highest score) as normal backtestable candidates;
    `paper_direct_keys` are blocked-but-good-enough candidates, ranked below every backtestable one
    unless given an explicit score via _scored()."""
    n = len(keys)
    backtestable = [_FakeScored(_FakeCandidate(k, k.upper()), total_score=n - i) for i, k in enumerate(keys)]
    blocked = [_FakeScored(_FakeCandidate(k, k.upper()), total_score=0.5, feasibility_classification="NOT_CURRENTLY_IMPLEMENTABLE")
               for k in paper_direct_keys]
    return {"researchable_now": backtestable, "paper_direct_eligible": blocked, "all_scored": backtestable + blocked}


class TestAdvance(unittest.TestCase):
    def test_picks_the_top_ranked_candidate_never_attempted_before(self):
        d = tempfile.mkdtemp()
        entry = advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        self.assertEqual((entry["key"], entry["started"], entry["started_by"]), ("alpha", "2026-09-22", "auto"))
        self.assertEqual(load(d)["current"]["key"], "alpha")
        self.assertEqual(len(load(d)["history"]), 1)

    def test_does_nothing_when_the_current_pick_is_still_the_best_available(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        self.assertIsNone(advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 23)))
        self.assertEqual(load(d)["current"]["key"], "alpha")   # unchanged -- still the top pick
        self.assertEqual(len(load(d)["history"]), 1)           # no superseded row added for a no-op

    def test_swaps_an_auto_pick_for_a_better_ranked_candidate_before_research_starts(self):
        # a new candidate (e.g. from the monthly discovery routine) now ranks above what's queued --
        # "interrupting and changing mid week or anytime is ok" as long as nothing has started (2026-09-22).
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))   # picks alpha (top of the list)
        entry = advance(d, _roadmap(["beta", "alpha"]), now=datetime(2026, 9, 24))   # beta now ranks first
        self.assertEqual(entry["key"], "beta")
        data = load(d)
        self.assertEqual(data["current"]["key"], "beta")
        alpha_row = next(r for r in data["history"] if r["key"] == "alpha")
        self.assertEqual((alpha_row["resolved"], alpha_row["outcome"]), ("2026-09-24", "superseded"))
        self.assertEqual(len(data["history"]), 2)               # alpha's row closed, beta's row opened

    def test_never_swaps_a_pick_a_human_made_by_hand(self):
        d = tempfile.mkdtemp()
        start_now(d, "alpha", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        self.assertIsNone(advance(d, _roadmap(["beta", "alpha"]), now=datetime(2026, 9, 24)))
        self.assertEqual(load(d)["current"]["key"], "alpha")   # a manual pick is only ever moved by a human

    def test_locked_once_research_is_in_progress(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        mark_in_progress(d, "alpha", now=datetime(2026, 9, 23))
        self.assertTrue(load(d)["current"]["in_progress"])
        self.assertIsNone(advance(d, _roadmap(["beta", "alpha"]), now=datetime(2026, 9, 24)))
        self.assertEqual(load(d)["current"]["key"], "alpha")   # locked -- research is already ongoing

    def test_skips_candidates_already_in_history_even_once_resolved(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        resolve(d, "alpha", "researched", experiment_id="EXP-050", now=datetime(2026, 9, 23))
        entry = advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 29))
        self.assertEqual(entry["key"], "beta")   # alpha is done, never re-picked

    def test_returns_none_when_every_candidate_has_been_attempted(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 9, 22))
        resolve(d, "alpha", "researched", now=datetime(2026, 9, 23))
        self.assertIsNone(advance(d, _roadmap(["alpha"]), now=datetime(2026, 9, 29)))


class TestPaperDirectMode(unittest.TestCase):
    def test_advance_picks_mode_backtest_for_a_normal_candidate(self):
        d = tempfile.mkdtemp()
        entry = advance(d, _roadmap(["alpha"]), now=datetime(2026, 9, 22))
        self.assertEqual(entry["mode"], "backtest")
        self.assertEqual(load(d)["history"][0]["mode"], "backtest")

    def test_advance_picks_a_paper_direct_candidate_when_it_outranks_every_backtestable_one(self):
        d = tempfile.mkdtemp()
        roadmap = _roadmap(["alpha"], paper_direct_keys=["blocked_good"])
        # give the blocked candidate the highest score in the whole pool
        roadmap["paper_direct_eligible"][0].total_score = 99.0
        entry = advance(d, roadmap, now=datetime(2026, 9, 22))
        self.assertEqual((entry["key"], entry["mode"]), ("blocked_good", "paper_direct"))

    def test_advance_falls_back_to_paper_direct_when_researchable_now_is_exhausted(self):
        d = tempfile.mkdtemp()
        roadmap = _roadmap(["alpha"], paper_direct_keys=["blocked_good"])
        advance(d, roadmap, now=datetime(2026, 9, 22))
        resolve(d, "alpha", "researched", now=datetime(2026, 9, 23))
        entry = advance(d, roadmap, now=datetime(2026, 9, 24))
        self.assertEqual((entry["key"], entry["mode"]), ("blocked_good", "paper_direct"))

    def test_start_now_sets_paper_direct_mode_for_a_blocked_candidate_chosen_by_hand(self):
        d = tempfile.mkdtemp()
        roadmap = _roadmap(["alpha"], paper_direct_keys=["blocked_good"])
        entry = start_now(d, "blocked_good", roadmap, now=datetime(2026, 9, 22))
        self.assertEqual(entry["mode"], "paper_direct")

    def test_start_now_sets_backtest_mode_for_an_implementable_candidate_chosen_by_hand(self):
        d = tempfile.mkdtemp()
        entry = start_now(d, "alpha", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        self.assertEqual(entry["mode"], "backtest")

    def test_resolve_accepts_paper_trading_proposed_as_an_outcome(self):
        d = tempfile.mkdtemp()
        roadmap = _roadmap([], paper_direct_keys=["blocked_good"])
        advance(d, roadmap, now=datetime(2026, 9, 22))
        resolve(d, "blocked_good", "paper_trading_proposed", branch="research/blocked_good", now=datetime(2026, 9, 23))
        row = load(d)["history"][0]
        self.assertEqual((row["outcome"], row["branch"]), ("paper_trading_proposed", "research/blocked_good"))


class TestStartNow(unittest.TestCase):
    def test_jumps_the_queue_to_a_specific_candidate(self):
        d = tempfile.mkdtemp()
        entry = start_now(d, "beta", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        self.assertEqual((entry["key"], entry["started_by"]), ("beta", "manual"))

    def test_refuses_an_unknown_key(self):
        d = tempfile.mkdtemp()
        with self.assertRaises(ValueError):
            start_now(d, "nope", _roadmap(["alpha"]))

    def test_jumps_to_a_different_candidate_even_while_one_is_already_queued(self):
        d = tempfile.mkdtemp()
        start_now(d, "alpha", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        entry = start_now(d, "beta", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 23))
        self.assertEqual(entry["key"], "beta")
        data = load(d)
        self.assertEqual(data["current"]["key"], "beta")
        alpha_row = next(r for r in data["history"] if r["key"] == "alpha")
        self.assertEqual((alpha_row["resolved"], alpha_row["outcome"]), ("2026-09-23", "superseded"))

    def test_refuses_once_research_is_in_progress(self):
        d = tempfile.mkdtemp()
        start_now(d, "alpha", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        mark_in_progress(d, "alpha", now=datetime(2026, 9, 23))
        with self.assertRaises(ValueError):
            start_now(d, "beta", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 24))

    def test_refuses_a_key_already_resolved(self):
        d = tempfile.mkdtemp()
        start_now(d, "alpha", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        resolve(d, "alpha", "researched", now=datetime(2026, 9, 23))
        with self.assertRaises(ValueError):
            start_now(d, "alpha", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 29))


class TestMarkInProgress(unittest.TestCase):
    def test_locks_the_current_pick(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 9, 22))
        mark_in_progress(d, "alpha", now=datetime(2026, 9, 23))
        self.assertTrue(load(d)["current"]["in_progress"])

    def test_refuses_a_key_that_is_not_current(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 9, 22))
        with self.assertRaises(ValueError):
            mark_in_progress(d, "beta")

    def test_refuses_when_nothing_is_queued(self):
        with self.assertRaises(ValueError):
            mark_in_progress(tempfile.mkdtemp(), "alpha")


class TestResolve(unittest.TestCase):
    def test_clears_current_and_fills_in_the_history_row(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 9, 22))
        resolve(d, "alpha", "researched", experiment_id="EXP-050", branch="research/alpha", now=datetime(2026, 9, 23))
        data = load(d)
        self.assertIsNone(data["current"])
        row = data["history"][0]
        self.assertEqual((row["resolved"], row["outcome"], row["experiment_id"], row["branch"]),
                         ("2026-09-23", "researched", "EXP-050", "research/alpha"))

    def test_refuses_to_resolve_a_key_that_is_not_current(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 9, 22))
        with self.assertRaises(ValueError):
            resolve(d, "beta", "researched")

    def test_refuses_a_bad_outcome(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 9, 22))
        with self.assertRaises(ValueError):
            resolve(d, "alpha", "maybe")


class TestLoad(unittest.TestCase):
    def test_missing_or_corrupt_file_loads_as_empty_not_an_error(self):
        self.assertEqual(load(tempfile.mkdtemp()), {"current": None, "history": []})
        self.assertEqual(load(None), {"current": None, "history": []})


if __name__ == "__main__":
    unittest.main()
