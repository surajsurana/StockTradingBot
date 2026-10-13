"""check_research_queue.py -- when the stuck-research-queue watchdog fires (pure planning logic)."""
import unittest
from datetime import datetime

import check_research_queue as crq

QUEUED = {
    "current": {"key": "size_premium_banz", "started": "2026-09-22", "started_by": "auto", "in_progress": False},
    "history": [
        {"key": "size_premium_banz", "name": "Size Premium (Small-Cap Effect)", "queued": "2026-09-22",
         "started": "2026-09-22", "started_by": "auto", "resolved": None, "outcome": None, "experiment_id": None, "branch": None},
    ],
}
IN_PROGRESS = {**QUEUED, "current": {**QUEUED["current"], "in_progress": True}}
EMPTY = {"current": None, "history": [{"key": "x", "name": "x", "started": "2026-09-10", "resolved": "2026-09-10", "outcome": "researched"}]}


class TestPlan(unittest.TestCase):
    def test_quiet_right_after_queuing(self):
        due, _ = crq.plan(datetime(2026, 9, 23, 10, 0), QUEUED, {})
        self.assertEqual(due, [])

    def test_quiet_at_a_normal_weekly_cycle(self):
        due, _ = crq.plan(datetime(2026, 9, 27, 19, 0), QUEUED, {})   # 5 days -- the routine's due to run tonight
        self.assertEqual(due, [])

    def test_fires_once_a_normal_weekly_cycle_has_passed_untouched(self):
        due, _ = crq.plan(datetime(2026, 9, 30, 10, 0), QUEUED, {})   # 8 days, still not started
        self.assertEqual(len(due), 1)
        self.assertIn("Size Premium (Small-Cap Effect)", due[0][1])
        self.assertIn("hasn't run", due[0][1])

    def test_repeats_daily_until_resolved_and_never_twice_the_same_day(self):
        due1, sent = crq.plan(datetime(2026, 9, 30, 10, 0), QUEUED, {})
        again_same_day, _ = crq.plan(datetime(2026, 9, 30, 18, 0), QUEUED, dict(sent) | {due1[0][0]: "x"})
        self.assertEqual(again_same_day, [])
        due2, _ = crq.plan(datetime(2026, 10, 1, 10, 0), QUEUED, dict(sent) | {due1[0][0]: "x"})
        self.assertEqual(len(due2), 1)
        self.assertNotEqual(due1[0][0], due2[0][0])

    def test_in_progress_is_not_stuck_for_a_normal_run_duration(self):
        due, _ = crq.plan(datetime(2026, 9, 22, 10, 5), IN_PROGRESS, {})   # 5 minutes in
        self.assertEqual(due, [])

    def test_in_progress_for_a_full_day_is_flagged_as_stuck(self):
        due, _ = crq.plan(datetime(2026, 9, 23, 10, 0), IN_PROGRESS, {})   # 24h in
        self.assertEqual(len(due), 1)
        self.assertIn("stuck", due[0][1])
        self.assertIn("crashed", due[0][1])

    def test_empty_queue_is_quiet_soon_after_the_last_resolution(self):
        due, _ = crq.plan(datetime(2026, 9, 10, 12, 0), EMPTY, {})
        self.assertEqual(due, [])

    def test_empty_queue_flagged_once_the_6_hourly_refill_should_have_run(self):
        due, _ = crq.plan(datetime(2026, 9, 11, 12, 0), EMPTY, {})   # a full day later, still nothing queued
        self.assertEqual(len(due), 1)
        self.assertIn("empty", due[0][1])

    def test_no_current_and_no_history_at_all_never_crashes(self):
        due, _ = crq.plan(datetime(2026, 9, 10, 12, 0), {"current": None, "history": []}, {})
        self.assertEqual(due, [])



class TestAClaimWaitingOnAMergeIsNotStuck(unittest.TestCase):
    """The routine's job ENDS at pushing its branch -- the backtest only runs on code already
    merged to main. Telling you it "likely crashed" sent you to a cloud run log when the thing to
    do was merge a pull request (2026-10-13, nifty_momentum_30_style: implemented 10-11, branch
    pushed, never merged, and the clock threw the week away)."""

    QUEUE = {"current": {"key": "alpha", "started": "2026-10-11", "in_progress": True},
             "history": [{"key": "alpha", "name": "Alpha", "started": "2026-10-11", "resolved": None}]}
    NOW = datetime(2026, 10, 13, 9, 15)

    def test_a_delivered_claim_asks_for_a_merge(self):
        due, _ = crq.plan(self.NOW, self.QUEUE, {}, delivered=True)
        self.assertEqual(len(due), 1)
        alert_id, text = due[0]
        self.assertIn("waiting for your merge", text)
        self.assertNotIn("crashed", text)
        self.assertTrue(alert_id.startswith("awaiting-merge-"))

    def test_an_undelivered_one_still_reads_as_stuck(self):
        due, _ = crq.plan(self.NOW, self.QUEUE, {}, delivered=False)
        self.assertEqual(len(due), 1)
        alert_id, text = due[0]
        self.assertIn("likely crashed", text)
        self.assertIn("no branch for it has appeared", text)
        self.assertTrue(alert_id.startswith("stuck-progress-"))

    def test_delivered_defaults_to_false_so_old_callers_are_unchanged(self):
        due, _ = crq.plan(self.NOW, self.QUEUE, {})
        self.assertIn("likely crashed", due[0][1])

if __name__ == "__main__":
    unittest.main()
