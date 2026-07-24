"""
Mock-based unit tests for research_lab/knowledge_base.py. Uses a temp file
throughout -- never touches the real research_lab/knowledge_base.jsonl.
Run with:

    python test_knowledge_base.py
"""

import os
import tempfile
import unittest

from research_lab import knowledge_base as kb


class TestKnowledgeBase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(self.path)  # record() should create it fresh

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_record_then_load_round_trips(self):
        kb.record("EXP-001", "Test Hyp", "mechanism text", "PASS", "reason text", path=self.path)
        entries = kb.load_entries(self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].exp_id, "EXP-001")
        self.assertEqual(entries[0].verdict, "PASS")

    def test_append_only_never_overwrites_a_prior_line(self):
        kb.record("EXP-001", "First", "mech1", "PASS", "reason1", path=self.path)
        kb.record("EXP-002", "Second", "mech2", "REJECT", "reason2", path=self.path)
        entries = kb.load_entries(self.path)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].exp_id, "EXP-001")  # still there, unmodified
        self.assertEqual(entries[1].exp_id, "EXP-002")

    def test_record_raises_on_duplicate_exp_id(self):
        kb.record("EXP-001", "First", "mech1", "PASS", "reason1", path=self.path)
        with self.assertRaises(ValueError):
            kb.record("EXP-001", "Different name", "different mech", "REJECT", "r", path=self.path)

    def test_render_for_prompt_includes_every_entry(self):
        kb.record("EXP-001", "First Hyp", "mechanism one", "PASS", "reason one", path=self.path)
        kb.record("EXP-002", "Second Hyp", "mechanism two", "REJECT", "reason two", path=self.path)
        rendered = kb.render_for_prompt(self.path)
        self.assertIn("First Hyp", rendered)
        self.assertIn("Second Hyp", rendered)
        self.assertIn("reason two", rendered)

    def test_render_for_prompt_empty_when_no_history(self):
        self.assertEqual(kb.render_for_prompt(self.path), "")

    def test_rejected_mechanisms_only_includes_reject_and_seeded(self):
        kb.record("EXP-001", "Passed one", "mech-pass", "PASS", "worked", path=self.path)
        kb.record("EXP-002", "Failed one", "mech-fail", "REJECT", "failed", path=self.path)
        kb.record("SEED-1", "Seeded failure", "mech-seed", "SEEDED", "seeded failure", path=self.path)
        mechs = kb.rejected_mechanisms(self.path)
        self.assertIn("mech-fail", mechs)
        self.assertIn("mech-seed", mechs)
        self.assertNotIn("mech-pass", mechs)

    def test_seed_orb_history_is_idempotent(self):
        kb.seed_orb_history(self.path)
        first_count = len(kb.load_entries(self.path))
        kb.seed_orb_history(self.path)  # calling again should not duplicate
        second_count = len(kb.load_entries(self.path))
        self.assertEqual(first_count, second_count)
        self.assertGreater(first_count, 0)


class TestResearchConclusions(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_record_then_load_round_trips(self):
        kb.record_conclusion("Single-regime dependency keeps failing.", ["EXP-001", "EXP-002"],
                              path=self.path)
        conclusions = kb.load_conclusions(self.path)
        self.assertEqual(len(conclusions), 1)
        self.assertEqual(conclusions[0].based_on_exp_ids, ["EXP-001", "EXP-002"])
        self.assertIn("Single-regime", conclusions[0].conclusion_text)

    def test_append_only_never_overwrites_a_prior_conclusion(self):
        kb.record_conclusion("First conclusion.", ["EXP-001"], path=self.path)
        kb.record_conclusion("Second, updated conclusion.", ["EXP-001", "EXP-002"], path=self.path)
        conclusions = kb.load_conclusions(self.path)
        self.assertEqual(len(conclusions), 2)
        self.assertEqual(conclusions[0].conclusion_text, "First conclusion.")
        self.assertEqual(conclusions[1].conclusion_text, "Second, updated conclusion.")

    def test_render_for_prompt_shows_only_the_latest(self):
        kb.record_conclusion("Old, superseded conclusion.", ["EXP-001"], path=self.path)
        kb.record_conclusion("Latest conclusion.", ["EXP-001", "EXP-002"], path=self.path)
        rendered = kb.render_conclusions_for_prompt(self.path)
        self.assertIn("Latest conclusion.", rendered)
        self.assertNotIn("Old, superseded", rendered)

    def test_render_for_prompt_empty_when_no_review_yet(self):
        self.assertEqual(kb.render_conclusions_for_prompt(self.path), "")


if __name__ == "__main__":
    unittest.main()
