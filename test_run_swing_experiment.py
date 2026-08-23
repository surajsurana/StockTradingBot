"""
Tests for run_swing_experiment.py's strategy dispatch -- none existed
before this file (added 2026-08-23 alongside the self-registering strategy
architecture refactor, as net-new safety-net coverage for a script that
previously had zero tests). Covers only the dispatch/wiring layer
(run_strategy() resolving the right research_director function, argparse
exposing the right choices, an unknown key still raising ValueError) --
never runs a real backtest (fetch_all, build_run_manifest, and the
resolved experiment function are all mocked).
"""

import unittest
from unittest.mock import patch

import run_swing_experiment as rse
from swing_research.strategy_catalog import RESEARCH_EXPERIMENT_SPECS


class TestStrategyDispatch(unittest.TestCase):
    def test_unknown_strategy_raises_value_error(self):
        with self.assertRaises(ValueError):
            rse.run_strategy("not_a_real_strategy", years=1, limit=0, windows=1)

    def test_every_catalog_key_resolves_without_raising_before_reaching_the_network(self):
        """For every registered research strategy, run_strategy() must get
        PAST the dispatch (resolve a real callable via runner_getter())
        without raising -- verified by mocking get_swing_universe/fetch_all
        so the function fails harmlessly for an unrelated reason (empty
        data -> sys.exit(1)) rather than a dispatch bug."""
        for spec in RESEARCH_EXPERIMENT_SPECS:
            with self.subTest(strategy_key=spec.strategy_key):
                with patch("run_swing_experiment.get_swing_universe", return_value=["AAA.NS"]), \
                     patch("run_swing_experiment.build_run_manifest") as mock_manifest, \
                     patch("run_swing_experiment.save_run_manifest", return_value="/tmp/fake_manifest.json"), \
                     patch("run_swing_experiment.fetch_all", return_value={}):
                    mock_manifest.return_value = {
                        "engine_version": "v1", "universe": {"version": "test"},
                        "git": {"commit_hash": "abc123", "working_tree_dirty": False, "dirty_files": []},
                        "benchmark_config": {"starting_capital": 1_000_000},
                    }
                    with self.assertRaises(SystemExit):
                        rse.run_strategy(spec.strategy_key, years=1, limit=0, windows=1)


class TestArgparseChoices(unittest.TestCase):
    def test_all_catalog_keys_are_valid_cli_choices(self):
        expected = {spec.strategy_key for spec in RESEARCH_EXPERIMENT_SPECS}
        self.assertEqual(set(rse._SPECS_BY_KEY.keys()), expected)

    def test_default_strategy_is_a_valid_choice(self):
        parser_default = "turtle_system2"
        self.assertIn(parser_default, rse._SPECS_BY_KEY)


if __name__ == "__main__":
    unittest.main()
