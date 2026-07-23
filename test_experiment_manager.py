"""
Mock-based unit tests for research_lab/experiment_manager.py. Uses a
temporary directory throughout -- never touches the real
research_lab/experiments/ or research_lab/knowledge_base.jsonl. Run with:

    python test_experiment_manager.py
"""

import json
import os
import shutil
import tempfile
import unittest

from research_lab import experiment_manager as em


class TestExperimentManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.experiments_dir = os.path.join(self.tmp_dir, "experiments")
        self.kb_path = os.path.join(self.tmp_dir, "kb.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_next_id_on_empty_dir_is_exp_001(self):
        self.assertEqual(em.next_experiment_id(self.experiments_dir), "EXP-001")

    def test_next_id_increments_past_highest_existing(self):
        os.makedirs(os.path.join(self.experiments_dir, "EXP-001"))
        os.makedirs(os.path.join(self.experiments_dir, "EXP-007"))
        self.assertEqual(em.next_experiment_id(self.experiments_dir), "EXP-008")

    def test_next_id_ignores_non_matching_names(self):
        os.makedirs(os.path.join(self.experiments_dir, "EXP-001"))
        os.makedirs(os.path.join(self.experiments_dir, "SEED-ORB-1"))
        os.makedirs(os.path.join(self.experiments_dir, "not_an_experiment"))
        self.assertEqual(em.next_experiment_id(self.experiments_dir), "EXP-002")

    def _save(self, exp_id="EXP-001", decision="PASS"):
        return em.save_experiment(
            exp_id=exp_id,
            hypothesis={"name": "Test Hyp", "mechanism": "m", "rationale": "r", "rules": "rules"},
            parameters={"foo": 1}, data_period="2026-01-01 to 2026-06-01",
            metrics={"win_rate": 0.5}, observations="obs text",
            verdict={"decision": decision, "reasoning": "why"},
            experiments_dir=self.experiments_dir, knowledge_base_path=self.kb_path,
        )

    def test_save_writes_all_five_files(self):
        exp_dir = self._save()
        for fname in ["hypothesis.md", "parameters.json", "metrics.json",
                      "observations.md", "verdict.md"]:
            self.assertTrue(os.path.exists(os.path.join(exp_dir, fname)), f"missing {fname}")

    def test_save_raises_on_duplicate_exp_id(self):
        self._save()
        with self.assertRaises(ValueError):
            self._save()

    def test_save_records_to_knowledge_base(self):
        self._save(decision="PASS")
        with open(self.kb_path, encoding="utf-8") as f:
            entries = [json.loads(line) for line in f]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["exp_id"], "EXP-001")
        self.assertEqual(entries[0]["verdict"], "PASS")

    def test_load_experiment_round_trips_metrics(self):
        self._save()
        loaded = em.load_experiment("EXP-001", self.experiments_dir)
        self.assertEqual(loaded["metrics"]["win_rate"], 0.5)
        self.assertEqual(loaded["parameters"]["foo"], 1)

    def test_list_experiments_sorted(self):
        self._save("EXP-002")
        self._save("EXP-001")
        self.assertEqual(em.list_experiments(self.experiments_dir), ["EXP-001", "EXP-002"])

    def test_load_missing_experiment_raises(self):
        with self.assertRaises(FileNotFoundError):
            em.load_experiment("EXP-999", self.experiments_dir)


if __name__ == "__main__":
    unittest.main()
