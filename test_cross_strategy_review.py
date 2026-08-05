"""
Unit tests for swing_research/cross_strategy_review.py -- the review-
cadence logic (deterministic, no Claude calls) and the mocked generation
path (using a fake call_fn, same convention as
research_lab.performance_analyst.explain()'s tests). Run with:

    python test_cross_strategy_review.py
"""

import json
import os
import shutil
import tempfile
import unittest

from swing_research.cross_strategy_review import (
    REVIEW_CADENCE, build_review_prompt, generate_cross_strategy_review, is_review_due,
    strategies_completed_count,
)


def _write_conclusions(path, n, prefix="STRATEGY"):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({
                "timestamp": 1234567890.0 + i, "based_on_exp_ids": [f"EXP-{i:03d}"],
                "conclusion_text": f"{prefix}_{i}: some conclusion text.",
            }) + "\n")


class TestStrategiesCompletedCount(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "conclusions.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_zero_for_missing_file(self):
        self.assertEqual(strategies_completed_count(os.path.join(self.tmpdir, "nope.jsonl")), 0)

    def test_counts_one_line_per_strategy(self):
        _write_conclusions(self.path, 5)
        self.assertEqual(strategies_completed_count(self.path), 5)


class TestIsReviewDue(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "conclusions.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_not_due_below_cadence(self):
        _write_conclusions(self.path, REVIEW_CADENCE - 1)
        self.assertFalse(is_review_due(self.path))

    def test_due_at_exact_cadence(self):
        _write_conclusions(self.path, REVIEW_CADENCE)
        self.assertTrue(is_review_due(self.path))

    def test_not_due_between_cadence_multiples(self):
        _write_conclusions(self.path, REVIEW_CADENCE + 1)
        self.assertFalse(is_review_due(self.path))

    def test_due_at_second_cadence_multiple(self):
        _write_conclusions(self.path, REVIEW_CADENCE * 2)
        self.assertTrue(is_review_due(self.path))

    def test_respects_already_reviewed_count(self):
        # 3 completed, already reviewed after strategy 3 -- 0 pending, not due.
        _write_conclusions(self.path, REVIEW_CADENCE)
        self.assertFalse(is_review_due(self.path, already_reviewed_count=REVIEW_CADENCE))
        # 6 completed, 3 already reviewed -- 3 pending, due.
        _write_conclusions(self.path, REVIEW_CADENCE * 2)
        self.assertTrue(is_review_due(self.path, already_reviewed_count=REVIEW_CADENCE))


class TestBuildReviewPrompt(unittest.TestCase):
    def test_includes_every_conclusions_text_and_exp_ids(self):
        conclusions = [
            {"based_on_exp_ids": ["EXP-001", "EXP-002"], "conclusion_text": "TURTLE: rejected."},
            {"based_on_exp_ids": ["EXP-008"], "conclusion_text": "MINERVINI: inconclusive."},
        ]
        prompt = build_review_prompt(conclusions)
        self.assertIn("TURTLE: rejected.", prompt)
        self.assertIn("MINERVINI: inconclusive.", prompt)
        self.assertIn("EXP-001", prompt)
        self.assertIn("EXP-008", prompt)

    def test_instructs_not_to_re_judge_individual_verdicts(self):
        prompt = build_review_prompt([{"based_on_exp_ids": [], "conclusion_text": "X: y."}])
        self.assertIn("not to re-judge", prompt.lower())


class TestGenerateCrossStrategyReview(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.conclusions_path = os.path.join(self.tmpdir, "conclusions.jsonl")
        self.output_dir = os.path.join(self.tmpdir, "reviews")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_raises_when_not_due(self):
        _write_conclusions(self.conclusions_path, 1)
        with self.assertRaises(ValueError):
            generate_cross_strategy_review(self.conclusions_path, output_dir=self.output_dir,
                                            call_fn=lambda p: "unused")

    def test_saves_a_markdown_file_when_due(self):
        _write_conclusions(self.conclusions_path, REVIEW_CADENCE)
        mock_review_text = "## Common Patterns\nEvery strategy so far has X."
        path = generate_cross_strategy_review(
            self.conclusions_path, output_dir=self.output_dir, call_fn=lambda p: mock_review_text,
        )
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(mock_review_text, content)
        self.assertIn("Cross-Strategy Research Review", content)

    def test_only_covers_conclusions_since_already_reviewed_count(self):
        _write_conclusions(self.conclusions_path, REVIEW_CADENCE * 2, prefix="S")
        captured_prompt = {}

        def capture_call_fn(prompt):
            captured_prompt["value"] = prompt
            return "review text"

        generate_cross_strategy_review(
            self.conclusions_path, output_dir=self.output_dir,
            already_reviewed_count=REVIEW_CADENCE, call_fn=capture_call_fn,
        )
        # Only strategies 3, 4, 5 (indices REVIEW_CADENCE..2*REVIEW_CADENCE-1) should appear.
        self.assertNotIn("S_0:", captured_prompt["value"])
        self.assertIn("S_3:", captured_prompt["value"])
        self.assertIn("S_5:", captured_prompt["value"])


if __name__ == "__main__":
    unittest.main()
