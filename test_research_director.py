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
from research_lab.knowledge_base import load_conclusions, seed_orb_history
from research_lab.pairs_base import PairSignal, PairStrategy
from research_lab.quant_researcher import Hypothesis
from research_lab.research_director import (
    build_ranking_prompt, build_review_prompt, hard_filter, parse_ranking_response, rank_and_select,
    review_research_history, run_backtest_with_audit, run_experiment_phase2,
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


class TestReviewResearchHistory(unittest.TestCase):
    def setUp(self):
        fd, self.kb_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(self.kb_path)
        fd2, self.conclusions_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd2)
        os.remove(self.conclusions_path)
        seed_orb_history(self.kb_path)

    def tearDown(self):
        for p in (self.kb_path, self.conclusions_path):
            if os.path.exists(p):
                os.remove(p)

    def test_raises_with_no_history(self):
        empty_kb = self.kb_path + ".empty"
        with self.assertRaises(RuntimeError):
            review_research_history(call_fn=lambda p: "unused", knowledge_base_path=empty_kb,
                                     conclusions_path=self.conclusions_path)

    def test_records_conclusion_from_review(self):
        conclusion = review_research_history(
            call_fn=lambda p: "Range-breakout mechanisms keep failing regardless of filter added.",
            knowledge_base_path=self.kb_path, conclusions_path=self.conclusions_path,
        )
        self.assertIn("Range-breakout", conclusion)
        saved = load_conclusions(self.conclusions_path)
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(saved[0].based_on_exp_ids), 5)  # all 5 SEED-ORB entries

    def test_prior_conclusion_reaches_a_second_reviews_own_prompt(self):
        from research_lab.knowledge_base import record_conclusion

        record_conclusion("Regime breakdown was miscomputed due to a date-type bug -- disregard it.",
                           ["EXP-001"], path=self.conclusions_path)
        captured = {}

        def fake_call(prompt):
            captured["prompt"] = prompt
            return "A second, new conclusion."

        review_research_history(call_fn=fake_call, knowledge_base_path=self.kb_path,
                                 conclusions_path=self.conclusions_path)
        self.assertIn("date-type bug", captured["prompt"])
        self.assertIn("ESTABLISHED", captured["prompt"])

    def test_review_prompt_includes_full_history_not_summary(self):
        from research_lab.knowledge_base import load_entries
        entries = load_entries(self.kb_path)
        prompt = build_review_prompt(entries)
        self.assertIn("SEED-ORB-1", prompt)
        self.assertIn("SEED-ORB-5", prompt)
        self.assertIn("higher-level", prompt.lower())


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

    def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
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


def _flat_bars(date_str, closes):
    idx = pd.date_range(f"{date_str} 09:15", periods=len(closes), freq="5min")
    return pd.DataFrame([{"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1000} for c in closes], index=idx)


class _FiresOnDivergencePairStrategy(PairStrategy):
    """No correlation gate (the engine doesn't own that -- the real
    strategy does), so no daily data is needed to get one deterministic
    pair trade out of the fixture below."""
    name = "fires_on_divergence"

    def generate_pair_signal(self, bars_a_so_far, bars_b_so_far, spread_context=None):
        mean, std = spread_context.get("baseline_mean"), spread_context.get("baseline_std")
        if mean is None or std is None:
            return None
        z = (float(bars_a_so_far.iloc[-1]["Close"]) / float(bars_b_so_far.iloc[-1]["Close"]) - mean) / std
        if abs(z) < 2.0:
            return None
        return PairSignal(long_leg="b" if z > 0 else "a", entry_zscore=z, stop_zscore=3.5,
                          target_zscore=0.5, confidence=0.5, strategy_name=self.name)


class TestPairsGovernance(unittest.TestCase):
    """Same governance guarantee as above, through the pairs engine: the
    Auditor's verdict is decided before the narrative and survives it.
    Plus the one pairs-specific accounting rule -- capital is counted per
    PAIR, not per underlying symbol."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        baseline_days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-05", periods=20)]
        df_a = pd.concat([_flat_bars(d, [200, 210, 200, 210]) for d in baseline_days]
                         + [_flat_bars("2026-02-03", [200, 220, 215, 206])])
        df_b = pd.concat([_flat_bars(d, [100, 100, 100, 100]) for d in baseline_days]
                         + [_flat_bars("2026-02-03", [100, 100, 100, 100])])
        self.data = {"A": df_a, "B": df_b}
        self.pairs = [("A", "B")]

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_capital_is_counted_per_pair_not_per_leg(self):
        result = run_backtest_with_audit(
            _FiresOnDivergencePairStrategy(), self.data, 100000, None, date(2026, 1, 5), date(2026, 2, 3),
            n_walk_forward_windows=1, use_pairs=True, pairs=self.pairs,
        )
        # one pair trade: short 227 A @220 -> 206, long 500 B flat = +3178 on ONE
        # 100000 bucket (the pair), not two (its legs) -> 3.18%, not 1.59%.
        self.assertEqual(len(result["all_trades"]), 1)
        self.assertAlmostEqual(result["out_of_sample_metrics"]["return_on_capital_pct"], 3.18)

    def test_use_pairs_requires_pairs_and_excludes_cross_sectional(self):
        with self.assertRaises(ValueError):
            run_backtest_with_audit(_FiresOnDivergencePairStrategy(), self.data, 100000, None,
                                    date(2026, 1, 5), date(2026, 2, 3), use_pairs=True, pairs=[])
        with self.assertRaises(ValueError):
            run_backtest_with_audit(_FiresOnDivergencePairStrategy(), self.data, 100000, None,
                                    date(2026, 1, 5), date(2026, 2, 3), use_pairs=True, pairs=self.pairs,
                                    use_cross_sectional=True, sector_map={"A": "x", "B": "x"})

    def test_reject_verdict_preserved_and_pairs_recorded(self):
        import json

        exp_dir = os.path.join(self.tmp_dir, "experiments")
        kb_path = os.path.join(self.tmp_dir, "kb.jsonl")
        hyp = Hypothesis(name="Pairs Test", mechanism="m", rationale="r", rules="rules", distinctiveness="d")
        # One trade is far below the Auditor's minimum sample -> REJECT,
        # whatever the (mocked, enthusiastic) narrative says.
        exp_id = run_experiment_phase2(
            hypothesis=hyp, strategy=_FiresOnDivergencePairStrategy(), data=self.data,
            capital_per_symbol=100000, start_date=date(2026, 1, 5), end_date=date(2026, 2, 3),
            n_walk_forward_windows=1, use_pairs=True, pairs=self.pairs,
            narrative_call_fn=lambda p: "Market-neutral GENIUS, approve immediately!",
            experiments_dir=exp_dir, knowledge_base_path=kb_path, skip_regime_breakdown=True,
        )
        with open(os.path.join(exp_dir, exp_id, "verdict.md"), encoding="utf-8") as f:
            self.assertIn("REJECT", f.read())
        with open(os.path.join(exp_dir, exp_id, "parameters.json"), encoding="utf-8") as f:
            params = json.load(f)
        self.assertEqual(params["pairs"], ["A/B"])
        self.assertEqual(sorted(params["symbols"]), ["A", "B"])
        with open(os.path.join(exp_dir, exp_id, "metrics.json"), encoding="utf-8") as f:
            metrics = json.load(f)
        self.assertEqual(metrics["total_trades"], 1)
        self.assertAlmostEqual(metrics["return_on_capital_pct"], 3.18)


if __name__ == "__main__":
    unittest.main()
