"""Tests for research_queue.py -- the "one strategy at a time, weekly, or start one by hand" queue."""
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime

from research_queue import advance, load, resolve, start_now


@dataclass
class _FakeCandidate:
    key: str
    name: str


@dataclass
class _FakeScored:
    candidate: _FakeCandidate


def _roadmap(keys):
    scored = [_FakeScored(_FakeCandidate(k, k.upper())) for k in keys]
    return {"researchable_now": scored, "all_scored": scored}


class TestAdvance(unittest.TestCase):
    def test_picks_the_top_ranked_candidate_never_attempted_before(self):
        d = tempfile.mkdtemp()
        entry = advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        self.assertEqual((entry["key"], entry["started"], entry["started_by"]), ("alpha", "2026-09-22", "auto"))
        self.assertEqual(load(d)["current"]["key"], "alpha")
        self.assertEqual(len(load(d)["history"]), 1)

    def test_does_nothing_while_something_is_already_current(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        self.assertIsNone(advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 23)))
        self.assertEqual(load(d)["current"]["key"], "alpha")   # unchanged

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


class TestStartNow(unittest.TestCase):
    def test_jumps_the_queue_to_a_specific_candidate(self):
        d = tempfile.mkdtemp()
        entry = start_now(d, "beta", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        self.assertEqual((entry["key"], entry["started_by"]), ("beta", "manual"))

    def test_refuses_an_unknown_key(self):
        d = tempfile.mkdtemp()
        with self.assertRaises(ValueError):
            start_now(d, "nope", _roadmap(["alpha"]))

    def test_refuses_while_something_is_already_current(self):
        d = tempfile.mkdtemp()
        start_now(d, "alpha", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        with self.assertRaises(ValueError):
            start_now(d, "beta", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 23))

    def test_refuses_a_key_already_resolved(self):
        d = tempfile.mkdtemp()
        start_now(d, "alpha", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        resolve(d, "alpha", "researched", now=datetime(2026, 9, 23))
        with self.assertRaises(ValueError):
            start_now(d, "alpha", _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 29))


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
