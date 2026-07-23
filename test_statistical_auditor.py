"""
Mock-based unit tests for research_lab/statistical_auditor.py -- the final,
deterministic gate no experiment passes without. No Claude calls involved
(this module never makes any). Run with:

    python test_statistical_auditor.py
"""

import unittest

from research_lab.statistical_auditor import audit


class TestStatisticalAuditor(unittest.TestCase):
    def test_passes_with_consistent_positive_edge_and_good_oos(self):
        wf = [{"total_trades": 15, "expectancy": 100}, {"total_trades": 15, "expectancy": 80},
              {"total_trades": 15, "expectancy": 50}]
        oos = {"total_trades": 10, "expectancy": 60}
        v = audit(wf, oos)
        self.assertEqual(v.decision, "PASS")

    def test_rejects_on_inconsistent_walk_forward_windows(self):
        # Real trap this guards against: the earlier ORB target-multiple
        # tuning (SEED-ORB-2) looked good on one window and lost on another.
        wf = [{"total_trades": 15, "expectancy": 100}, {"total_trades": 15, "expectancy": -200},
              {"total_trades": 15, "expectancy": -150}]
        oos = {"total_trades": 10, "expectancy": 60}
        v = audit(wf, oos)
        self.assertEqual(v.decision, "REJECT")
        self.assertIn("walk-forward", v.reasoning.lower())

    def test_rejects_on_negative_out_of_sample_expectancy(self):
        wf = [{"total_trades": 15, "expectancy": 100}, {"total_trades": 15, "expectancy": 80}]
        oos = {"total_trades": 10, "expectancy": -50}
        v = audit(wf, oos)
        self.assertEqual(v.decision, "REJECT")
        self.assertIn("out-of-sample", v.reasoning.lower())

    def test_rejects_on_insufficient_total_sample_size(self):
        wf = [{"total_trades": 3, "expectancy": 100}]
        oos = {"total_trades": 2, "expectancy": 60}
        v = audit(wf, oos)
        self.assertEqual(v.decision, "REJECT")
        self.assertIn("total trades", v.reasoning.lower())

    def test_rejects_on_insufficient_out_of_sample_trades_even_if_totals_are_fine(self):
        wf = [{"total_trades": 30, "expectancy": 100}]
        oos = {"total_trades": 1, "expectancy": 500}  # one lucky trade shouldn't be enough
        v = audit(wf, oos)
        self.assertEqual(v.decision, "REJECT")

    def test_no_trades_in_any_window_does_not_crash(self):
        v = audit([{"total_trades": 0, "expectancy": 0}], {"total_trades": 0, "expectancy": 0})
        self.assertEqual(v.decision, "REJECT")

    def test_checks_dict_reports_actual_numbers_used(self):
        wf = [{"total_trades": 15, "expectancy": 100}, {"total_trades": 15, "expectancy": 80}]
        oos = {"total_trades": 10, "expectancy": 60}
        v = audit(wf, oos)
        self.assertEqual(v.checks["total_trades"], 40)
        self.assertEqual(v.checks["out_of_sample_trades"], 10)


if __name__ == "__main__":
    unittest.main()
