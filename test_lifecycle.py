"""
Tests for reporting/lifecycle.py -- what has happened to the strategies lately.

    python test_lifecycle.py
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from reporting.lifecycle import timeline


class _Rec:
    def __init__(self, key, sid, name):
        self.strategy_key, self.strategy_id, self.display_name = key, sid, name


def _overlay(root, data):
    with open(os.path.join(root, "deployment_status.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)


def _entry(status, *history):
    return {"status": status, "history": list(history)}


def _h(to, ts, reason="", frm="RESEARCH"):
    return {"from_status": frm, "to_status": to, "timestamp": ts, "reason": reason}


AUG1 = datetime(2026, 8, 1, 9, 30).timestamp()
SEP1 = datetime(2026, 9, 1, 14, 5).timestamp()
OCT1 = datetime(2026, 10, 1, 0, 10).timestamp()


class TestStatusChanges(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.recs = [_Rec("alpha", "SW-007", "Alpha Strategy")]

    def _rows(self):
        return timeline(self.root, records=self.recs)

    def test_each_promotion_and_retirement_is_a_line(self):
        _overlay(self.root, {"alpha": _entry("ARCHIVED",
                                             _h("PAPER_TRADING", AUG1),
                                             _h("PILOT_LIVE", SEP1, frm="PAPER_TRADING"),
                                             _h("ARCHIVED", OCT1, frm="PILOT_LIVE"))})
        rows = self._rows()
        self.assertEqual([r["what"] for r in rows],
                         ["Retired", "Promoted to live", "Promoted to paper trading"])

    def test_the_three_stage_changes_are_named_the_same_way(self):
        """They are one kind of event. Calling one of them "Started ..." made it read as something
        else going on nearby, and a promotion to paper was looked for on a page showing it."""
        from reporting.lifecycle import STATUS_EVENTS
        for status in ("PAPER_TRADING", "PILOT_LIVE", "PRODUCTION"):
            self.assertTrue(STATUS_EVENTS[status][0].startswith("Promoted to"), status)

    def test_the_newest_is_first(self):
        """It is a timeline, and the question it answers is "what just happened"."""
        _overlay(self.root, {"alpha": _entry("PILOT_LIVE",
                                             _h("PAPER_TRADING", AUG1), _h("PILOT_LIVE", SEP1))})
        self.assertEqual([r["date"] for r in self._rows()], ["2026-09-01", "2026-08-01"])

    def test_a_status_change_carries_a_real_time(self):
        """A timestamp was recorded, so it is reported."""
        _overlay(self.root, {"alpha": _entry("PILOT_LIVE", _h("PILOT_LIVE", SEP1))})
        r = self._rows()[0]
        self.assertEqual(r["date"], "2026-09-01")
        self.assertEqual(r["time"], "14:05")

    def test_the_reason_written_at_the_time_comes_with_it(self):
        """A promotion that overrode the gates says so in its reason, and that is the single most
        important thing on the line."""
        _overlay(self.root, {"alpha": _entry("PILOT_LIVE",
                                             _h("PILOT_LIVE", SEP1, reason="[MANUAL OVERRIDE] Gates NOT met"))})
        self.assertIn("MANUAL OVERRIDE", self._rows()[0]["detail"])

    def test_it_says_which_strategy_in_the_words_a_person_uses(self):
        _overlay(self.root, {"alpha": _entry("PILOT_LIVE", _h("PILOT_LIVE", SEP1))})
        r = self._rows()[0]
        self.assertEqual((r["sid"], r["name"], r["key"]), ("SW-007", "Alpha Strategy", "alpha"))

    def test_it_says_where_the_strategy_runs(self):
        """From the same helper the Strategies tab uses, so a row cannot say Pool A here and Pool E
        there."""
        _overlay(self.root, {"alpha": _entry("PILOT_LIVE", _h("PILOT_LIVE", SEP1))})
        self.assertTrue(self._rows()[0]["pool"].startswith("Pool"))

    def test_an_unknown_status_is_shown_not_dropped(self):
        """A status this file has not been taught about is still news."""
        _overlay(self.root, {"alpha": _entry("SOMETHING_NEW", _h("SOMETHING_NEW", SEP1))})
        self.assertEqual(self._rows()[0]["what"], "Something New")

    def test_a_strategy_with_no_registry_record_still_appears(self):
        """Its key is all we have, and a line naming the key beats no line at all."""
        _overlay(self.root, {"ghost": _entry("ARCHIVED", _h("ARCHIVED", SEP1))})
        r = self._rows()[0]
        self.assertEqual((r["key"], r["name"]), ("ghost", "ghost"))


class TestResearchResults(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def _with_queue(self, history, verdict=""):
        q = {"current": {}, "history": history}
        with patch("research_queue.load", return_value=q), \
             patch("research_queue.LANES", ("india",)), \
             patch("reporting.lifecycle._verdict_of", return_value=verdict):
            return timeline(self.root, records=[])

    def test_a_finished_backtest_reports_its_verdict(self):
        rows = self._with_queue([{"key": "k", "name": "Some Factor", "resolved": "2026-10-10",
                                  "outcome": "researched", "experiment_id": "EXP-096"}], verdict="PASS")
        self.assertEqual(rows[0]["what"], "Research result: PASS")
        self.assertEqual(rows[0]["experiment"], "EXP-096")
        self.assertEqual(rows[0]["name"], "Some Factor")

    def test_the_verdict_comes_from_the_experiments_own_file(self):
        """The same file the Research tab reads, so the two cannot claim different results."""
        import inspect

        import reporting.lifecycle as mod
        self.assertIn("verdict.md", inspect.getsource(mod._verdict_of))

    def test_a_superseded_candidate_is_not_on_the_timeline(self):
        """Swapped out of the queue before any research began -- housekeeping, hidden 2026-10-22."""
        rows = self._with_queue([{"key": "k", "name": "Some Factor", "resolved": "2026-10-22",
                                  "outcome": "superseded", "experiment_id": None},
                                 {"key": "j", "name": "Other", "resolved": "2026-10-13",
                                  "outcome": "abandoned", "experiment_id": None}])
        self.assertEqual([r["key"] for r in rows], ["j"])

    def test_a_candidate_that_produced_no_backtest_reads_differently(self):
        """Abandoned is not a result. Calling it one would put a verdict on something never run."""
        rows = self._with_queue([{"key": "k", "name": "Some Factor", "resolved": "2026-10-07",
                                  "outcome": "abandoned", "experiment_id": None}])
        self.assertEqual(rows[0]["what"], "Research abandoned")
        self.assertEqual(rows[0]["kind"], "research")

    def test_a_result_with_no_name_of_its_own_borrows_the_registrys(self):
        """A candidate that got as far as a verdict has usually been registered by now, and its key
        is not what anyone calls it."""
        q = {"current": {}, "history": [{"key": "alpha", "resolved": "2026-10-10",
                                         "outcome": "researched", "experiment_id": "E"}]}
        with patch("research_queue.load", return_value=q),              patch("research_queue.LANES", ("india",)),              patch("reporting.lifecycle._verdict_of", return_value="PASS"):
            rows = timeline(self.root, records=[_Rec("alpha", "SW-007", "Alpha Strategy")])
        self.assertEqual((rows[0]["name"], rows[0]["sid"]), ("Alpha Strategy", "SW-007"))

    def test_the_queues_own_name_still_wins_when_it_has_one(self):
        """It is the name the candidate was proposed under, before anything was registered."""
        q = {"current": {}, "history": [{"key": "alpha", "name": "As Proposed",
                                         "resolved": "2026-10-10", "outcome": "researched",
                                         "experiment_id": "E"}]}
        with patch("research_queue.load", return_value=q),              patch("research_queue.LANES", ("india",)),              patch("reporting.lifecycle._verdict_of", return_value="PASS"):
            rows = timeline(self.root, records=[_Rec("alpha", "SW-007", "Alpha Strategy")])
        self.assertEqual(rows[0]["name"], "As Proposed")

    def test_an_unresolved_candidate_is_not_on_the_timeline(self):
        """It has not happened yet."""
        self.assertEqual(self._with_queue([{"key": "k", "resolved": None, "outcome": None}]), [])

    def test_a_date_only_event_leaves_the_time_blank(self):
        """Nobody recorded when in the day it finished, and a plausible-looking 00:00 would be a
        timestamp this program invented."""
        rows = self._with_queue([{"key": "k", "name": "X", "resolved": "2026-10-10",
                                  "outcome": "researched", "experiment_id": "E"}], verdict="REJECT")
        self.assertEqual(rows[0]["date"], "2026-10-10")
        self.assertEqual(rows[0]["time"], "")


class TestTheTwoSourcesShareOneClock(unittest.TestCase):
    def test_promotions_and_research_results_interleave_by_date(self):
        root = tempfile.mkdtemp()
        _overlay(root, {"alpha": _entry("PAPER_TRADING",
                                        _h("PAPER_TRADING", datetime(2026, 10, 9, 8, 0).timestamp()))})
        q = {"current": {}, "history": [{"key": "k", "name": "X", "resolved": "2026-10-10",
                                         "outcome": "researched", "experiment_id": "E"}]}
        with patch("research_queue.load", return_value=q), \
             patch("research_queue.LANES", ("india",)), \
             patch("reporting.lifecycle._verdict_of", return_value="PASS"):
            rows = timeline(root, records=[])
        self.assertEqual([r["date"] for r in rows], ["2026-10-10", "2026-10-09"])


class TestItNeverTakesThePageDown(unittest.TestCase):
    """It is a view over other people's files. A missing or broken one is an ordinary Tuesday."""

    def test_no_sources_at_all_is_an_empty_timeline_not_an_error(self):
        self.assertEqual(timeline(tempfile.mkdtemp(), records=[]), [])

    def test_a_corrupt_overlay_does_not_stop_the_research_half(self):
        root = tempfile.mkdtemp()
        with open(os.path.join(root, "deployment_status.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        q = {"current": {}, "history": [{"key": "k", "name": "X", "resolved": "2026-10-10",
                                         "outcome": "researched", "experiment_id": "E"}]}
        with patch("research_queue.load", return_value=q), \
             patch("research_queue.LANES", ("india",)), \
             patch("reporting.lifecycle._verdict_of", return_value="PASS"):
            self.assertEqual(len(timeline(root, records=[])), 1)

    def test_a_broken_queue_does_not_stop_the_status_half(self):
        root = tempfile.mkdtemp()
        _overlay(root, {"alpha": _entry("PILOT_LIVE", _h("PILOT_LIVE", SEP1))})
        with patch("research_queue.load", side_effect=RuntimeError("boom")):
            self.assertEqual(len(timeline(root, records=[])), 1)

    def test_an_unreadable_timestamp_does_not_lose_the_line(self):
        root = tempfile.mkdtemp()
        _overlay(root, {"alpha": _entry("ARCHIVED", _h("ARCHIVED", "not a number"))})
        rows = timeline(root, records=[])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["what"], "Retired")

    def test_it_records_nothing(self):
        """A second log would be a second thing to keep in step, and the first time the two
        disagreed neither would be trustworthy. Checked against the CODE, not the file: the module's
        own prose uses several of these words."""
        import ast

        import reporting.lifecycle as mod
        with open(mod.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                called.add(fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", ""))
        for forbidden in ("write_json", "record_status", "set_deployment_status", "resolve",
                          "register_strategy", "open", "replace_file", "remove", "rename"):
            self.assertNotIn(forbidden, called, forbidden)


class TestHowMuchItShows(unittest.TestCase):
    def test_the_list_is_capped(self):
        root = tempfile.mkdtemp()
        _overlay(root, {"alpha": _entry("ARCHIVED", *[_h("ARCHIVED", AUG1 + i) for i in range(400)])})
        self.assertEqual(len(timeline(root, records=[], limit=50)), 50)

    def test_the_cap_keeps_the_newest(self):
        root = tempfile.mkdtemp()
        _overlay(root, {"alpha": _entry("ARCHIVED",
                                        _h("ARCHIVED", AUG1), _h("ARCHIVED", OCT1))})
        self.assertEqual(timeline(root, records=[], limit=1)[0]["date"], "2026-10-01")

class TestResearchSummary(unittest.TestCase):
    """2026-10-26: a research result shows its backtest in one line and the reason in plain words."""

    def _exp(self, metrics, verdict_text):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "EXP-1"))
        with open(os.path.join(root, "EXP-1", "metrics.json"), "w") as f:
            json.dump(metrics, f)
        with open(os.path.join(root, "EXP-1", "verdict.md"), "w") as f:
            f.write(verdict_text)
        return root

    def test_a_reject_reads_in_plain_words(self):
        from reporting.lifecycle import research_summary
        root = self._exp({"total_trades": 158, "win_rate": 0.3418, "avg_holding_period_days": 36.0,
                          "expectancy": -5.99, "cagr": -44.24, "max_drawdown_pct": 32.0},
                         "# Verdict: REJECT\n\nOnly 0/2 walk-forward windows (0%) showed positive expectancy -- "
                         "below the 50% needed. Out-of-sample expectancy is -6.99 per trade -- not positive.")
        s = research_summary("EXP-1", "crypto", "REJECT", roots=[root])
        self.assertEqual(s["stats"], "158 trades (54 won, 104 lost) · held ~36 days · avg −$6 per trade · "
                                     "−44% a year · worst drop −32%")
        self.assertEqual(s["why"], "Why rejected: made money in only 0 of 2 test periods, and lost money in "
                                   "the most recent period it had never seen.")

    def test_too_few_trades_is_named(self):
        from reporting.lifecycle import research_summary
        root = self._exp({"total_trades": 12}, "# Verdict: REJECT\n\nOnly 12 total trades across all windows -- "
                                               "below the minimum of 30 needed.")
        self.assertIn("too few trades (12; needs 30)", research_summary("EXP-1", "india", "REJECT", roots=[root])["why"])

    def test_a_wiped_out_book_is_said_in_words(self):
        from reporting.lifecycle import research_summary
        root = self._exp({"total_trades": 5, "cagr": -100.0, "max_drawdown_pct": 139.0}, "# Verdict: REJECT")
        stats = research_summary("EXP-1", "crypto", "REJECT", roots=[root])["stats"]
        self.assertIn("lost the whole book", stats)
        self.assertIn("worst drop: more than the whole book", stats)

    def test_rupees_for_india(self):
        from reporting.lifecycle import research_summary
        root = self._exp({"expectancy": 1573.2}, "# Verdict: PASS\n\nPassed all checks")
        s = research_summary("EXP-1", "india", "PASS", roots=[root])
        self.assertIn("avg +₹1,573 per trade", s["stats"])
        self.assertTrue(s["why"].startswith("Passed"))

    def test_money_made_over_the_test_period_in_indian_rupees(self):
        # EXP-102's real figures (Industry Momentum, India, 2026-10-29).
        from reporting.lifecycle import research_summary
        root = self._exp({"total_pnl": 291708.22, "return_on_capital_pct": 291.71,
                          "monthly_returns_pct": {"2017-05": 1.0, "2026-10": 0.5},
                          "out_of_sample_metrics": {"total_pnl": 153460.81}}, "# Verdict: PASS")
        self.assertEqual(research_summary("EXP-1", "india", "PASS", roots=[root])["money"],
                         "Made ₹2,91,708 on ₹1,00,000 (May 2017 – Oct 2026) before charges · most recent unseen period: +₹1,53,461")

    def test_money_lost_past_the_whole_book(self):
        # EXP-100's real figures (crypto, a book that kept trading after it was gone).
        from reporting.lifecycle import research_summary
        root = self._exp({"total_pnl": -1500.97, "return_on_capital_pct": -150.1,
                          "monthly_returns_pct": {"2022-11": -1.0, "2026-10": -2.0}, "diagnostic_zero_cost_baseline": {},
                          "out_of_sample_metrics": {"total_pnl": -611.91}}, "# Verdict: REJECT")
        self.assertEqual(research_summary("EXP-1", "crypto", "REJECT", roots=[root])["money"],
                         "Lost $1,501 on $1,000 (more than the whole book) (Nov 2022 – Oct 2026) · "
                         "most recent unseen period: −$612")

    def test_year_by_year_amounts_add_up_to_the_total(self):
        from reporting.lifecycle import research_summary
        root = self._exp({"total_pnl": 30000.0, "return_on_capital_pct": 30.0,
                          "annual_returns_pct": {"2024": -10.0, "2023": 40.0}}, "# Verdict: REJECT")
        years = research_summary("EXP-1", "india", "REJECT", roots=[root])["years"]
        self.assertEqual(years, [
            {"year": "2023", "amount": "+₹40,000", "pct": "+40.0%", "gain": True},
            {"year": "2024", "amount": "−₹10,000", "pct": "−10.0%", "gain": False}])

    def test_no_pnl_recorded_means_no_money_line(self):
        from reporting.lifecycle import research_summary
        root = self._exp({"total_trades": 3}, "# Verdict: REJECT")
        self.assertEqual(research_summary("EXP-1", "us", "REJECT", roots=[root])["money"], "")

    def test_a_missing_experiment_gives_nothing_and_never_raises(self):
        from reporting.lifecycle import research_summary
        self.assertEqual(research_summary("EXP-404", "us", "REJECT", roots=[tempfile.mkdtemp()]), {})
        self.assertEqual(research_summary("", "us", "REJECT"), {})


if __name__ == "__main__":
    unittest.main()
