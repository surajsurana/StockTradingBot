"""
Mock-based unit tests for research_lab/research_director.py -- the
pipeline's governance guarantees are the most important thing to verify
here: the Auditor's verdict must be locked in before the narrative is
generated, and nothing the narrative says can change it. No real Claude
calls, no real data. Run with:

    python test_research_director.py
"""

import os
import shutil
import tempfile
import unittest
from datetime import date

import pandas as pd

from research_lab.base import Signal, Strategy
from research_lab.knowledge_base import seed_orb_history
from research_lab.quant_researcher import Hypothesis
from research_lab.research_director import (
    build_ranking_prompt, hard_filter, parse_ranking_response, rank_and_select,
    run_experiment_phase2,
)


def _orb_like_hypothesis():
    return Hypothesis(
        name="ORB Variant", mechanism="15-min opening range breakout, stop at range low, target 1.5x range",
        rationale="breakouts of the opening range indicate momentum",
        rules="enter on range breakout, stop at range low, target multiple",
        distinctiveness="uses a slightly different target multiple",
    )


def _gap_hypothesis():
    return Hypothesis(
        name="Gap Continuation",
        mechanism="stocks gapping up with strong relative volume and relative strength vs nifty continue",
        rationale="overnight information asymmetry resolves via institutional order flow the next session",
        rules="enter on break of first 15-min high after a qualifying gap, stop at gap-fill, target 2x risk",
        distinctiveness="requires an overnight gap as trigger, not a computed intraday range",
    )


def _options_hypothesis():
    return Hypothesis(
        name="Options Skew Reversal", mechanism="uses put-call ratio and options open interest to time reversals",
        rationale="options positioning reveals informed flow", rules="enter when put-call ratio extreme reverts",
        distinctiveness="uses derivatives data",
    )


class TestHardFilter(unittest.TestCase):
    def setUp(self):
        fd, self.kb_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(self.kb_path)
        seed_orb_history(self.kb_path)

    def tearDown(self):
        if os.path.exists(self.kb_path):
            os.remove(self.kb_path)

    def test_rejects_infeasible_data_hypothesis(self):
        survivors, rejected = hard_filter([_gap_hypothesis(), _options_hypothesis()], self.kb_path)
        survivor_names = [h.name for h in survivors]
        self.assertIn("Gap Continuation", survivor_names)
        self.assertNotIn("Options Skew Reversal", survivor_names)

    def test_reports_rejection_reason(self):
        _, rejected = hard_filter([_options_hypothesis()], self.kb_path)
        self.assertEqual(len(rejected), 1)
        self.assertIn("data", rejected[0][1].lower())

    def test_distinct_hypothesis_survives(self):
        survivors, _ = hard_filter([_gap_hypothesis()], self.kb_path)
        self.assertEqual(len(survivors), 1)


class TestRankingParsing(unittest.TestCase):
    def test_parse_ranking_selects_correct_hypothesis(self):
        hyps = [_orb_like_hypothesis(), _gap_hypothesis()]
        fake_response = """RANKING:
1. Gap Continuation -- clear informational rationale
2. ORB Variant -- overlaps prior failures

SELECTED: Gap Continuation
SELECTION_REASONING: Strongest theoretical grounding, distinct from ORB-style mechanisms."""
        result = parse_ranking_response(fake_response, hyps)
        self.assertEqual(result["winner"].name, "Gap Continuation")

    def test_raises_on_missing_selected_line(self):
        with self.assertRaises(RuntimeError):
            parse_ranking_response("no selected line here", [_gap_hypothesis()])

    def test_raises_on_unmatched_selected_name(self):
        with self.assertRaises(RuntimeError):
            parse_ranking_response("SELECTED: Nonexistent Hypothesis Name XYZ", [_gap_hypothesis()])

    def test_ranking_prompt_includes_knowledge_base_summary_when_given(self):
        prompt = build_ranking_prompt([_gap_hypothesis()], knowledge_base_summary="- [SEED-ORB-1] ORB -- REJECT")
        self.assertIn("SEED-ORB-1", prompt)
        self.assertIn("Penalize", prompt)


class TestRankAndSelect(unittest.TestCase):
    def setUp(self):
        fd, self.kb_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(self.kb_path)
        seed_orb_history(self.kb_path)

    def tearDown(self):
        if os.path.exists(self.kb_path):
            os.remove(self.kb_path)

    def test_full_pipeline_with_mocked_ranking_call(self):
        fake_response = """RANKING:
1. Gap Continuation -- strong rationale

SELECTED: Gap Continuation
SELECTION_REASONING: Best mechanism."""
        result = rank_and_select([_gap_hypothesis(), _options_hypothesis()], self.kb_path,
                                  call_fn=lambda p: fake_response)
        self.assertEqual(result["winner"].name, "Gap Continuation")
        self.assertEqual(len(result["rejected_by_hard_filter"]), 1)

    def test_raises_if_hard_filter_rejects_everything(self):
        with self.assertRaises(RuntimeError):
            rank_and_select([_options_hypothesis()], self.kb_path, call_fn=lambda p: "unused")


class _NeverFiresStrategy(Strategy):
    name = "never_fires"

    def generate_signal(self, todays_bars_so_far):
        return None


class TestRunExperimentPhase2Governance(unittest.TestCase):
    """The critical governance test: confirms the Auditor's verdict is
    computed before the narrative, and that a REJECT verdict survives
    unchanged regardless of what a (mocked) Performance Analyst says."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        idx = pd.date_range("2026-01-05 09:15", periods=8, freq="5min")
        prices = [100.0] * 8
        self.data = {"TESTSYM": pd.DataFrame({
            "Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": [1000] * 8,
        }, index=idx)}

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_reject_verdict_preserved_regardless_of_narrative(self):
        import research_lab.experiment_manager as em

        exp_dir = os.path.join(self.tmp_dir, "experiments")
        kb_path = os.path.join(self.tmp_dir, "kb.jsonl")
        hyp = Hypothesis(name="Never Fires Test", mechanism="m", rationale="r", rules="rules",
                          distinctiveness="d")
        # A strategy that never trades -> zero trades -> the Auditor
        # MUST reject on insufficient sample size, no matter what a
        # (mocked, enthusiastic) narrative claims.
        exp_id = run_experiment_phase2(
            hypothesis=hyp, strategy=_NeverFiresStrategy(), data=self.data,
            capital_per_symbol=100000, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5),
            narrative_call_fn=lambda p: "This strategy is AMAZING and should definitely be approved!",
            experiments_dir=exp_dir, knowledge_base_path=kb_path, skip_regime_breakdown=True,
        )
        loaded = em.load_experiment(exp_id, exp_dir)
        self.assertIn("REJECT", loaded["verdict"])
        # the enthusiastic narrative made it into observations, but did NOT flip the verdict
        self.assertIn("AMAZING", loaded["observations"])


if __name__ == "__main__":
    unittest.main()
