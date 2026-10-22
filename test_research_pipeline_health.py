"""research_pipeline_health.py -- every stage's green/amber/red, the two real failure shapes of 2026-10-22,
the once-only Telegram, and a self-test that always cleans up. Everything runs in temp dirs: nothing here
may touch deployment/state/."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import research_pipeline_health as rph

NOW = datetime(2026, 10, 22, 9, 20)        # a Thursday; India fired Sun 18 Oct, crypto Tue 20 Oct

INTRADAY_MISLABELLED = SimpleNamespace(
    key="intraday_momentum_half_hour", horizon_lane="swing", asset_class="Broad market index/ETF, intraday",
    typical_holding_period="Same trading session only", holding_days_max=1,
    data_requirements=["daily_ohlcv_history", "intraday_bar_history"])
SWING = SimpleNamespace(key="size_premium_banz", horizon_lane="swing", asset_class="Indian equities",
                        typical_holding_period="1 month", holding_days_max=30, data_requirements=["daily_ohlcv_history"])

MERGE_REFUSED_TWICE = """[india] nothing waiting to merge.
[crypto] nothing waiting to merge.
*Research needs you* (us)
us_high_volume_return_premium
It was NOT merged: the test suite fails with it merged -- a person should look
FAILED (errors=121)

Everything else is automatic -- this is the one case worth a person's eyes.
    [ok ] has changes: 3 file(s)
[india] nothing waiting to merge.
[crypto] nothing waiting to merge.
*Research needs you* (us)
us_high_volume_return_premium
It was NOT merged: the test suite fails with it merged -- a person should look
FAILED (errors=121)

Everything else is automatic -- this is the one case worth a person's eyes.
"""

BACKTEST_OK = """[india] nothing to back-test: intraday_momentum_half_hour is queued but not claimed yet
[crypto] nothing to back-test: x is claimed but its implementation is not merged to main yet
[us] nothing to back-test: us_high_volume_return_premium is claimed but its implementation is not merged to main yet
"""


def _queue(key, started="2026-10-14", in_progress=False, history=None, since=None):
    current = {"key": key, "started": started, "started_by": "auto", "in_progress": in_progress, "mode": "backtest"}
    if since:
        current["in_progress_since"] = since
    return {"current": current,
            "history": (history or []) + [{"key": key, "name": key, "queued": started, "started": started,
                                           "resolved": None, "outcome": None}]}


RESULT_RECENT = [{"key": "old", "queued": "2026-10-05", "started": "2026-10-05", "resolved": "2026-10-19",
                  "outcome": "researched", "experiment_id": "EXP-100"}]


class TmpDirs(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rph-test-")
        self.state = os.path.join(self.root, "state")
        self.logs = os.path.join(self.root, "logs")
        os.makedirs(self.state)
        os.makedirs(self.logs)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def log(self, name, text, at=NOW):
        path = os.path.join(self.logs, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        ts = at.timestamp()
        os.utime(path, (ts, ts))
        return path

    def evaluate(self, queues, find=lambda key: SWING, implemented=lambda lane: set(), self_test=None, now=NOW):
        full = {lane: queues.get(lane, {"current": None, "history": RESULT_RECENT}) for lane in ("india", "crypto", "us")}
        return rph.evaluate(self.state, self.logs, now=now, implemented=implemented, self_test=self_test,
                            find=find, queues=full)


class TestRealFailureShapes(TmpDirs):
    """The two ways research actually stalled in October 2026."""

    def test_india_mislabelled_intraday_candidate_is_red_with_that_reason(self):
        self.log("queued_backtest.log", BACKTEST_OK)
        self.log("research_merge.log", "[india] nothing waiting to merge.\n")
        ev = self.evaluate({"india": _queue("intraday_momentum_half_hour", "2026-10-14", history=RESULT_RECENT)},
                           find=lambda key: INTRADAY_MISLABELLED)
        india = ev["lanes"]["india"]
        self.assertEqual(india["status"], "red")
        self.assertEqual(india["stages"]["queue"]["status"], "red")
        self.assertIn("intraday", india["stages"]["queue"]["reason"])
        self.assertIn("labelled swing", india["stages"]["queue"]["reason"])
        self.assertEqual(india["stages"]["queue"]["days"], 8)
        self.assertEqual(india["stages"]["routine"]["status"], "amber")
        self.assertIn("didn't claim", india["stages"]["routine"]["reason"])
        msg = rph.red_message("india", india)
        self.assertLessEqual(len(msg), rph.MAX_MESSAGE)
        self.assertNotIn("\n", msg)
        self.assertIn("India lane", msg)
        self.assertIn("8 days", msg)

    def test_the_real_roadmap_entry_is_caught_whatever_its_label(self):
        c = rph.find_candidate("intraday_momentum_half_hour")
        if c is None:
            self.skipTest("candidate no longer in the roadmap")
        self.assertIsNotNone(rph.routine_blocker(c))

    def test_us_branch_refused_two_runs_running_is_red(self):
        self.log("queued_backtest.log", BACKTEST_OK)
        self.log("research_merge.log", MERGE_REFUSED_TWICE)
        ev = self.evaluate({"us": _queue("us_high_volume_return_premium", "2026-10-15", in_progress=True,
                                         since="2026-10-16T06:00:00", history=RESULT_RECENT)})
        merge = ev["lanes"]["us"]["stages"]["merge"]
        self.assertEqual(merge["status"], "red")
        self.assertIn("2 runs in a row", merge["reason"])
        self.assertIn("test suite fails", merge["reason"])
        self.assertEqual(ev["lanes"]["us"]["stages"]["routine"]["status"], "green")   # it delivered
        self.assertEqual(ev["merge_history"]["us"][-1]["event"], "refused")

    def test_broken_gate_self_test_turns_every_lanes_merge_red(self):
        self.log("queued_backtest.log", BACKTEST_OK)
        self.log("research_merge.log", "")
        st = {"ran_at": "2026-10-22T03:30:00", "ok": False, "summary": "FAILED (errors=121)"}
        ev = self.evaluate({}, self_test=st)
        for lane in ("india", "crypto", "us"):
            self.assertEqual(ev["lanes"][lane]["stages"]["merge"]["status"], "red")
            self.assertIn("own test setup is broken", ev["lanes"][lane]["stages"]["merge"]["reason"])

    def test_an_old_failed_self_test_is_ignored(self):
        st = {"ran_at": "2026-10-01T03:30:00", "ok": False, "summary": "x"}
        ev = self.evaluate({}, self_test=st)
        self.assertEqual(ev["lanes"]["us"]["stages"]["merge"]["status"], "green")


class TestQueueStage(unittest.TestCase):
    def test_green_amber_red_by_unclaimed_age(self):
        for started, want in (("2026-10-20", "green"), ("2026-10-13", "amber"), ("2026-10-05", "red")):
            got = rph.queue_stage("india", _queue("k", started), NOW, SWING)
            self.assertEqual(got["status"], want, started)

    def test_claimed_is_green(self):
        self.assertEqual(rph.queue_stage("india", _queue("k", "2026-10-01", True), NOW, SWING)["status"], "green")

    def test_nothing_queued_long_is_amber_briefly_is_green(self):
        recent = {"current": None, "history": [{"key": "x", "started": "2026-10-20", "resolved": "2026-10-22"}]}
        old = {"current": None, "history": [{"key": "x", "started": "2026-10-10", "resolved": "2026-10-12"}]}
        self.assertEqual(rph.queue_stage("india", recent, NOW)["status"], "green")
        self.assertEqual(rph.queue_stage("india", old, NOW)["status"], "amber")

    def test_true_intraday_lane_is_blocked_and_daily_swing_is_not(self):
        self.assertIsNotNone(rph.routine_blocker(SimpleNamespace(key="o", horizon_lane="intraday", data_requirements=[])))
        self.assertIsNone(rph.routine_blocker(SWING))
        self.assertIsNone(rph.routine_blocker(None))


class TestRoutineStage(unittest.TestCase):
    def test_before_the_fire_it_is_waiting(self):
        # queued Monday 19 Oct, India's last fire was Sun 18 Oct -- it hasn't had its chance yet
        got = rph.routine_stage("india", _queue("k", "2026-10-19"), NOW, set(), set())
        self.assertEqual(got["status"], "green")
        self.assertIn("waiting", got["reason"])

    def test_within_a_day_of_the_fire_is_not_judged_yet(self):
        got = rph.routine_stage("crypto", _queue("k", "2026-10-15"), datetime(2026, 10, 20, 12, 0), set(), set())
        self.assertEqual(got["status"], "green")

    def test_stale_claim_with_nothing_delivered_is_amber(self):
        got = rph.routine_stage("us", _queue("k", "2026-10-10", True, since="2026-10-15T06:00:00"), NOW, set(), set())
        self.assertEqual(got["status"], "amber")

    def test_fire_weekdays_match_the_dashboard(self):
        from dashboard.state_view import RESEARCH_RUN_WEEKDAY
        self.assertEqual(rph.ROUTINE_FIRE_WEEKDAY, RESEARCH_RUN_WEEKDAY)


class TestMergeStage(unittest.TestCase):
    Q = _queue("k", "2026-10-15", True, since="2026-10-16T06:00:00")

    def _events(self, *kinds):
        return [{"event": e, "key": "k", "reason": "touches dashboard/x.py"} for e in kinds]

    def test_merged_and_nothing_waiting_are_green(self):
        self.assertEqual(rph.merge_stage("us", self.Q, NOW, {"k"}, [], NOW, None)["status"], "green")
        self.assertEqual(rph.merge_stage("us", {"current": None, "history": []}, NOW, set(), [], NOW, None)["status"], "green")

    def test_refused_once_amber_twice_red_and_a_merge_resets_it(self):
        self.assertEqual(rph.merge_stage("us", self.Q, NOW, set(), self._events("refused"), NOW, None)["status"], "amber")
        self.assertEqual(rph.merge_stage("us", self.Q, NOW, set(), self._events("refused", "refused"), NOW, None)["status"], "red")
        self.assertEqual(rph.refusal_streak(self._events("refused", "refused", "merged", "refused"), "k")[0], 1)

    def test_missing_or_stale_gate_log_is_amber_while_work_waits(self):
        self.assertEqual(rph.merge_stage("us", self.Q, NOW, set(), [], None, None)["status"], "amber")
        self.assertEqual(rph.merge_stage("us", self.Q, NOW, set(), [], datetime(2026, 10, 18), None)["status"], "amber")


class TestBacktestStage(TmpDirs):
    def test_fresh_clean_run_is_green(self):
        self.assertEqual(rph.backtest_stage("us", rph.parse_backtest_log(BACKTEST_OK.splitlines()), NOW, NOW)["status"], "green")

    def test_stale_log_is_amber_then_red(self):
        p = rph.parse_backtest_log(BACKTEST_OK.splitlines())
        self.assertEqual(rph.backtest_stage("us", p, datetime(2026, 10, 21, 2, 30), NOW)["status"], "amber")
        self.assertEqual(rph.backtest_stage("us", p, datetime(2026, 10, 19, 2, 30), NOW)["status"], "red")
        self.assertEqual(rph.backtest_stage("us", p, None, NOW)["status"], "amber")

    def test_a_crash_in_the_latest_run_is_red_but_one_before_it_is_not(self):
        crashed = BACKTEST_OK + "Traceback (most recent call last):\n  File \"x\", line 1\nImportError: no config\n"
        p = rph.parse_backtest_log(crashed.splitlines())
        got = rph.backtest_stage("india", p, NOW, NOW)
        self.assertEqual(got["status"], "red")
        self.assertIn("ImportError", got["reason"])
        recovered = crashed + BACKTEST_OK
        self.assertEqual(rph.backtest_stage("india", rph.parse_backtest_log(recovered.splitlines()), NOW, NOW)["status"], "green")

    def test_experiment_cli_failure_is_amber_for_its_lane_only(self):
        text = ("[india] nothing to back-test: q\n[crypto] nothing to back-test: q\n[us] running run_us_experiment.py\n"
                "[us] k: the experiment CLI exited 1\nTraceback (most recent call last):\nValueError: x\n")
        p = rph.parse_backtest_log(text.splitlines())
        self.assertEqual(rph.backtest_stage("us", p, NOW, NOW)["status"], "amber")
        self.assertEqual(rph.backtest_stage("india", p, NOW, NOW)["status"], "green")


class TestOutcomeStage(unittest.TestCase):
    def _q(self, resolved):
        return {"current": None, "history": [{"key": "a", "queued": "2026-09-01", "resolved": resolved, "outcome": "researched"}]}

    def test_thresholds(self):
        self.assertEqual(rph.outcome_stage("india", self._q("2026-10-19"), NOW)["status"], "green")
        self.assertEqual(rph.outcome_stage("india", self._q("2026-10-10"), NOW)["status"], "amber")
        self.assertEqual(rph.outcome_stage("india", self._q("2026-10-01"), NOW)["status"], "red")

    def test_skips_and_supersedes_are_not_results(self):
        q = {"current": None, "history": [{"key": "a", "queued": "2026-09-01", "resolved": "2026-10-21", "outcome": "superseded"}]}
        self.assertEqual(rph.outcome_stage("india", q, NOW)["status"], "red")


class TestAlerts(TmpDirs):
    def _red(self):
        return {"lanes": {"india": {"status": "red", "stages": {s: {"status": "red" if s == "queue" else "green",
                                                                     "reason": "queued candidate is intraday", "days": 8}
                                                                 for s in rph.STAGES}}}}

    def _green(self):
        return {"lanes": {"india": {"status": "green", "stages": {s: {"status": "green", "reason": "ok"} for s in rph.STAGES}}}}

    def test_turning_red_sends_once_then_recovery_once(self):
        alerts, due = rph.plan_alerts(self._red(), {}, NOW, send=True)
        self.assertEqual(len(due), 1)
        self.assertIn("Research stuck", due[0][1])
        self.assertEqual(alerts["india"]["red_since"], "2026-10-22")
        alerts, due = rph.plan_alerts(self._red(), alerts, NOW, send=True)
        self.assertEqual(due, [])
        self.assertEqual(alerts["india"]["red_since"], "2026-10-22")
        alerts, due = rph.plan_alerts(self._green(), alerts, NOW, send=True)
        self.assertEqual([m for _, m in due], [rph.recovered_message("india")])
        alerts, due = rph.plan_alerts(self._green(), alerts, NOW, send=True)
        self.assertEqual(due, [])

    def test_a_run_without_send_never_swallows_the_alert(self):
        alerts, _ = rph.plan_alerts(self._red(), {}, NOW, send=False)
        self.assertFalse(alerts["india"]["notified"])
        _, due = rph.plan_alerts(self._red(), alerts, NOW, send=True)
        self.assertEqual(len(due), 1)

    def test_message_is_one_short_line_even_for_a_long_reason(self):
        ev = self._red()
        ev["lanes"]["india"]["stages"]["queue"]["reason"] = "x" * 400
        msg = rph.red_message("india", ev["lanes"]["india"])
        self.assertLessEqual(len(msg), 160)
        self.assertNotIn("\n", msg)
        self.assertLessEqual(len(rph.recovered_message("crypto")), 160)

    def test_run_sends_once_and_persists_to_the_given_state_dir_only(self):
        self.log("queued_backtest.log", BACKTEST_OK)
        self.log("research_merge.log", "")
        queues = {"india": _queue("intraday_momentum_half_hour", "2026-10-14", history=RESULT_RECENT)}
        full = {lane: queues.get(lane, {"current": None, "history": RESULT_RECENT}) for lane in ("india", "crypto", "us")}
        sent = []
        kw = dict(implemented=lambda lane: set(), find=lambda key: INTRADAY_MISLABELLED if key.startswith("intraday") else SWING,
                  queues=full)
        data = rph.run(self.state, self.logs, send=True, now=NOW, sender=sent.append, **kw)
        self.assertEqual(len(sent), 1)
        self.assertEqual(data["lanes"]["india"]["red_since"], "2026-10-22")
        with open(os.path.join(self.state, rph.HEALTH_FILE), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["lanes"]["india"]["status"], "red")
        rph.run(self.state, self.logs, send=True, now=NOW, sender=sent.append, **kw)
        self.assertEqual(len(sent), 1)       # no repeat while it stays red

    def test_telegram_kind_is_research_alert_and_it_is_allowed_by_default(self):
        from reporting import telegram_notifier
        self.assertIn("research_alert", telegram_notifier.DEFAULT_ALLOWED_KINDS)
        with patch("reporting.telegram_notifier.send_telegram_message") as send, \
             patch.dict("sys.modules", {"config.settings": SimpleNamespace(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c")}):
            rph._telegram_sender()("hello")
        self.assertEqual(send.call_args.kwargs["kind"], "research_alert")
        self.assertIsNone(send.call_args.kwargs["parse_mode"])


class TestSelfTest(unittest.TestCase):
    def _git(self, add_rc=0):
        calls = []

        def git(args, cwd=None, timeout=600):
            calls.append(list(args))
            if args[:2] == ["worktree", "add"]:
                os.makedirs(os.path.join(args[3], "config"), exist_ok=True)
                return subprocess.CompletedProcess(args, add_rc, "", "fatal: nope" if add_rc else "")
            return subprocess.CompletedProcess(args, 0, "", "")
        return git, calls

    def _cleaned(self, calls):
        self.assertTrue(any(c[:2] == ["worktree", "remove"] for c in calls))
        self.assertEqual(calls[-1], ["worktree", "prune"])

    def test_green_clean_main(self):
        git, calls = self._git()
        r = rph.run_self_test(NOW, git=git, run_suite=lambda tree: (True, "OK"), prepare=lambda tree: None)
        self.assertTrue(r["ok"])
        self._cleaned(calls)

    def test_failing_clean_main_reports_false(self):
        git, calls = self._git()
        r = rph.run_self_test(NOW, git=git, run_suite=lambda tree: (False, "FAILED (errors=121)"), prepare=lambda tree: None)
        self.assertIs(r["ok"], False)
        self.assertIn("errors=121", r["summary"])
        self._cleaned(calls)

    def test_worktree_is_removed_even_when_the_suite_blows_up(self):
        git, calls = self._git()

        def boom(tree):
            raise RuntimeError("out of memory")
        r = rph.run_self_test(NOW, git=git, run_suite=boom, prepare=lambda tree: None)
        self.assertIsNone(r["ok"])
        self._cleaned(calls)
        tree = next(c for c in calls if c[:2] == ["worktree", "add"])[3]
        self.assertFalse(os.path.exists(os.path.dirname(tree)))

    def test_worktree_add_failure_still_cleans_up(self):
        git, calls = self._git(add_rc=128)
        r = rph.run_self_test(NOW, git=git, run_suite=lambda tree: (True, ""), prepare=lambda tree: None)
        self.assertIsNone(r["ok"])
        self._cleaned(calls)

    def test_skipped_while_the_backtest_runs(self):
        git, calls = self._git()
        r = rph.run_self_test(NOW, git=git, run_suite=lambda tree: (True, ""), busy=lambda: True)
        self.assertIsNone(r["ok"])
        self.assertEqual(calls, [])

    def test_prepare_copies_the_example_settings(self):
        tree = tempfile.mkdtemp(prefix="rph-tree-")
        try:
            os.makedirs(os.path.join(tree, "config"))
            with open(os.path.join(tree, "config", "settings.example.py"), "w") as f:
                f.write("X = 1\n")
            with patch.dict("sys.modules", {"research_merge_gate": SimpleNamespace()}):
                rph.prepare_worktree(tree)
            self.assertTrue(os.path.exists(os.path.join(tree, "config", "settings.py")))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    def test_prepare_defers_to_the_gates_own_step_when_it_has_one(self):
        own = MagicMock()
        with patch.dict("sys.modules", {"research_merge_gate": SimpleNamespace(prepare_worktree=own)}):
            rph.prepare_worktree("/nonexistent")
        own.assert_called_once_with("/nonexistent")

    def test_a_skipped_self_test_keeps_the_last_real_answer(self):
        root = tempfile.mkdtemp(prefix="rph-run-")
        try:
            os.makedirs(os.path.join(root, "logs"))
            rph.save(root, {"self_test": {"ran_at": "2026-10-22T03:30:00", "ok": False, "summary": "FAILED"}})
            data = rph.run(root, os.path.join(root, "logs"), self_test=True, now=NOW,
                           tester=lambda now, busy: {"ran_at": "x", "ok": None, "summary": "skipped"},
                           implemented=lambda lane: set(), find=lambda key: SWING,
                           queues={l: {"current": None, "history": RESULT_RECENT} for l in ("india", "crypto", "us")})
            self.assertIs(data["self_test"]["ok"], False)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestDashboardView(unittest.TestCase):
    def test_missing_and_malformed_files_never_throw(self):
        from dashboard.state_view import pipeline_health_view
        for bad in (None, {}, {"lanes": None}, {"lanes": {"india": "x"}}, {"lanes": {"india": {"stages": None}}}, "junk"):
            v = pipeline_health_view(bad)
            self.assertIn("checked", v)
            self.assertEqual(len(v["stages"]), 5)

    def test_shapes_a_real_evaluation(self):
        from dashboard.state_view import pipeline_health_view
        ev = {"checked_at": "2026-10-22T09:20:00", "self_test": {"ran_at": "2026-10-22T03:30:00", "ok": True},
              "lanes": {"india": {"status": "red", "red_since": "2026-10-22", "candidate": "k",
                                  "stages": {s: {"status": "green", "reason": "ok"} for s in rph.STAGES}}}}
        v = pipeline_health_view(ev)
        self.assertTrue(v["checked"])
        self.assertEqual(v["lanes"][0]["red_since"], "2026-10-22")
        self.assertEqual([s["id"] for s in v["lanes"][0]["stages"]], list(rph.STAGES))

    def test_load_missing_file_is_none(self):
        d = tempfile.mkdtemp(prefix="rph-load-")
        try:
            self.assertIsNone(rph.load(d))
            with open(os.path.join(d, rph.HEALTH_FILE), "w") as f:
                f.write("{not json")
            self.assertIsNone(rph.load(d))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
