"""Tests for research_queue.py -- the "one strategy at a time, weekly, or start one by hand" queue."""
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta

import research_queue
from research_queue import advance, load, mark_in_progress, resolve, start_now


@dataclass
class _FakeCandidate:
    key: str
    name: str
    horizon_lane: str = "swing"
    market: str = "India"


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

    def test_skips_candidates_already_built_and_judged_in_the_registry(self):
        # 2026-10-05: the roadmap's CANDIDATES list lags promotions, so a strategy that has already
        # been built and given a verdict is still a "candidate". Without `exclude` the queue
        # re-proposes finished work -- it really picked turnover_liquidity, already REJECTed as
        # SW-031, the first time it advanced past a stuck candidate.
        d = tempfile.mkdtemp()
        entry = advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22), exclude={"alpha"})
        self.assertEqual(entry["key"], "beta")
        self.assertEqual(load(d)["current"]["key"], "beta")

    def test_an_excluded_key_that_is_already_queued_gets_superseded(self):
        # the live case: `alpha` was picked before the registry exclusion existed, so it has to be
        # moved off, not just skipped on the next fresh pick.
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        entry = advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 23), exclude={"alpha"})
        self.assertEqual(entry["key"], "beta")
        alpha_row = next(r for r in load(d)["history"] if r["key"] == "alpha")
        self.assertEqual(alpha_row["outcome"], "superseded")

    def test_exclude_does_not_override_an_in_progress_lock(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 22))
        mark_in_progress(d, "alpha", now=datetime(2026, 9, 23))
        self.assertIsNone(advance(d, _roadmap(["alpha", "beta"]), now=datetime(2026, 9, 24), exclude={"alpha"}))
        self.assertEqual(load(d)["current"]["key"], "alpha")   # research under way still wins


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


@dataclass
class _FakeCandidateWithLane:
    key: str
    name: str
    horizon_lane: str = "swing"


class TestBuildSnapshot(unittest.TestCase):
    def _roadmap_with_lane(self, key, name, horizon_lane):
        scored = _FakeScored(_FakeCandidateWithLane(key, name, horizon_lane))
        return {"all_scored": [scored]}

    def test_no_current_candidate_returns_null(self):
        from research_queue import build_snapshot
        d = tempfile.mkdtemp()
        self.assertEqual(build_snapshot(d, {"all_scored": []}), {"current": None})

    def test_current_candidate_includes_name_and_horizon_lane_from_roadmap(self):
        from research_queue import build_snapshot
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 10, 1))
        snap = build_snapshot(d, self._roadmap_with_lane("alpha", "ALPHA", "crypto"))
        self.assertEqual(snap, {"current": {"key": "alpha", "name": "ALPHA", "mode": "backtest",
                                            "in_progress": False, "horizon_lane": "crypto",
                                       # a lock that is not held is neither stale nor aged
                                       "stale": False, "claimed_days_ago": None}})

    def test_in_progress_flag_is_carried_through(self):
        from research_queue import build_snapshot
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 10, 1))
        mark_in_progress(d, "alpha")
        snap = build_snapshot(d, self._roadmap_with_lane("alpha", "ALPHA", "swing"))
        self.assertTrue(snap["current"]["in_progress"])

    def test_candidate_not_found_in_roadmap_falls_back_to_key_as_name(self):
        # Can happen if the roadmap changed shape between runs -- never crash the snapshot publish over it.
        from research_queue import build_snapshot
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 10, 1))
        snap = build_snapshot(d, {"all_scored": []})
        self.assertEqual(snap["current"]["name"], "alpha")
        self.assertIsNone(snap["current"]["horizon_lane"])


class TestLanes(unittest.TestCase):
    """Three independent queues (2026-10-03, per explicit direction) -- india/crypto/us -- each with
    its own state file, so a glut of India candidates can never starve crypto/US of cadence."""

    def test_india_lane_uses_the_original_unlabeled_filename(self):
        import os
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 10, 3), lane="india")
        self.assertTrue(os.path.exists(os.path.join(d, "research_queue.json")))
        self.assertFalse(os.path.exists(os.path.join(d, "research_queue_india.json")))

    def test_crypto_and_us_lanes_use_their_own_filenames(self):
        import os
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["btc_thing"]), now=datetime(2026, 10, 3), lane="crypto")
        advance(d, _roadmap(["us_thing"]), now=datetime(2026, 10, 3), lane="us")
        self.assertTrue(os.path.exists(os.path.join(d, "research_queue_crypto.json")))
        self.assertTrue(os.path.exists(os.path.join(d, "research_queue_us.json")))

    def test_the_three_lanes_are_fully_independent(self):
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["alpha"]), now=datetime(2026, 10, 3), lane="india")
        advance(d, _roadmap(["beta"]), now=datetime(2026, 10, 3), lane="crypto")
        advance(d, _roadmap(["gamma"]), now=datetime(2026, 10, 3), lane="us")
        mark_in_progress(d, "alpha", lane="india")   # locking india must not touch crypto/us
        self.assertTrue(load(d, lane="india")["current"]["in_progress"])
        self.assertFalse(load(d, lane="crypto")["current"]["in_progress"])
        self.assertFalse(load(d, lane="us")["current"]["in_progress"])
        self.assertEqual(load(d, lane="india")["current"]["key"], "alpha")
        self.assertEqual(load(d, lane="crypto")["current"]["key"], "beta")
        self.assertEqual(load(d, lane="us")["current"]["key"], "gamma")

    def test_rejects_an_unknown_lane(self):
        with self.assertRaises(ValueError):
            load(tempfile.mkdtemp(), lane="nasdaq")

    def test_start_now_auto_detects_the_lane_from_the_candidate(self):
        d = tempfile.mkdtemp()
        full_roadmap = {"all_scored": [
            _FakeScored(_FakeCandidate("india_thing", "INDIA_THING")),
            _FakeScored(_FakeCandidate("crypto_thing", "CRYPTO_THING", horizon_lane="crypto")),
            _FakeScored(_FakeCandidate("us_thing", "US_THING", market="US")),
        ]}
        start_now(d, "india_thing", full_roadmap, now=datetime(2026, 10, 3))
        start_now(d, "crypto_thing", full_roadmap, now=datetime(2026, 10, 3))
        start_now(d, "us_thing", full_roadmap, now=datetime(2026, 10, 3))
        self.assertEqual(load(d, lane="india")["current"]["key"], "india_thing")
        self.assertEqual(load(d, lane="crypto")["current"]["key"], "crypto_thing")
        self.assertEqual(load(d, lane="us")["current"]["key"], "us_thing")

    def test_start_now_in_one_lane_does_not_block_another_lane_being_in_progress(self):
        d = tempfile.mkdtemp()
        full_roadmap = {"all_scored": [
            _FakeScored(_FakeCandidate("crypto_thing", "CRYPTO_THING", horizon_lane="crypto")),
            _FakeScored(_FakeCandidate("india_thing", "INDIA_THING")),
        ]}
        start_now(d, "crypto_thing", full_roadmap, now=datetime(2026, 10, 3))
        mark_in_progress(d, "crypto_thing", lane="crypto")
        # India's own queue is untouched -- starting something there must not be refused by crypto
        # being locked.
        entry = start_now(d, "india_thing", full_roadmap, now=datetime(2026, 10, 3))
        self.assertEqual(entry["key"], "india_thing")

    def test_build_snapshot_respects_lane(self):
        from research_queue import build_snapshot
        d = tempfile.mkdtemp()
        advance(d, _roadmap(["beta"]), now=datetime(2026, 10, 3), lane="crypto")
        roadmap_crypto = {"all_scored": [_FakeScored(_FakeCandidate("beta", "BETA", horizon_lane="crypto"))]}
        snap = build_snapshot(d, roadmap_crypto, lane="crypto")
        self.assertEqual(snap["current"]["key"], "beta")
        # The india lane's own snapshot must stay empty -- nothing was ever queued there.
        snap_india = build_snapshot(d, {"all_scored": []}, lane="india")
        self.assertIsNone(snap_india["current"])


if __name__ == "__main__":
    unittest.main()


class TestAStaleLockDoesNotBlockTheLaneForever(unittest.TestCase):
    """This happened. The crypto lane sat locked on crypto_illiquidity_premium from 2026-10-04 to
    2026-10-07: a routine claimed it, died without calling resolve(), and the lock had no other way
    out. Every later fire saw in_progress and did nothing, while the dashboard said "Researching
    now" for three days about a run that was long gone."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _claimed(self, days_ago):
        when = datetime.now() - timedelta(days=days_ago)
        return {"key": "k", "started": when.date().isoformat(), "started_by": "auto",
                "in_progress": True, "in_progress_since": when.isoformat(timespec="seconds"),
                "mode": "backtest"}

    def test_a_fresh_claim_is_not_stale(self):
        self.assertFalse(research_queue.lock_is_stale(self._claimed(0.2)))

    def test_a_claim_older_than_the_limit_is_stale(self):
        self.assertTrue(research_queue.lock_is_stale(self._claimed(research_queue.STALE_LOCK_DAYS + 1)))

    def test_an_unclaimed_candidate_is_never_stale(self):
        self.assertFalse(research_queue.lock_is_stale({"key": "k", "in_progress": False}))
        self.assertFalse(research_queue.lock_is_stale(None))

    def test_age_falls_back_to_started_when_there_is_no_claim_stamp(self):
        # the entry that was actually stuck predates in_progress_since
        old = {"key": "k", "started": "2026-10-04", "in_progress": True}
        self.assertGreater(research_queue.lock_age_days(old, datetime(2026, 10, 7)), 2.5)

    def test_a_stale_lock_is_recorded_as_abandoned_not_silently_dropped(self):
        data = {"current": self._claimed(5), "history": []}
        research_queue._save(self.d, data, "crypto")
        research_queue._abandon(self.d, research_queue.load(self.d, "crypto"), "crypto")
        after = research_queue.load(self.d, "crypto")
        self.assertIsNone(after["current"])
        self.assertEqual(after["history"][-1]["outcome"], "abandoned")
        self.assertIn("never reported back", after["history"][-1]["note"])

    def test_start_now_is_blocked_by_a_live_claim_but_not_a_stale_one(self):
        self.assertTrue(research_queue.lock_is_stale(self._claimed(10)))
        self.assertFalse(research_queue.lock_is_stale(self._claimed(0.5)))


if __name__ == "__main__":
    unittest.main()
