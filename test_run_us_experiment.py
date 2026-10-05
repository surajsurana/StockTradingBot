"""run_us_experiment.py -- the US equity research lane's CLI (Pool I), added 2026-10-05."""
import unittest
from unittest.mock import MagicMock, patch

import run_us_experiment as rue


class TestRunnersCatalog(unittest.TestCase):
    def test_both_existing_pool_i_strategies_are_registered(self):
        self.assertEqual(set(rue.RUNNERS),
                         {"minervini_trend_template_filter_us", "cross_sectional_momentum_us"})

    def test_every_entry_resolves_to_a_real_callable_in_research_director(self):
        # the getters are lazy (so importing this CLI stays cheap) -- prove they actually resolve.
        for key, (getter, variant) in rue.RUNNERS.items():
            fn = getter()
            self.assertTrue(callable(fn), key)
            self.assertTrue(fn.__name__.startswith("run_") and fn.__name__.endswith("_experiment"), key)
            self.assertTrue(variant.strip(), key)

    def test_keys_match_the_registered_us_strategy_keys(self):
        # run_queued_backtest decides "is this merged?" by looking the candidate's key up in here,
        # so these must be the registry's own strategy keys, not display names or variants.
        from deployment.deployment_manager import list_strategies
        registered = {r.strategy_key for r in list_strategies()}
        for key in rue.RUNNERS:
            self.assertIn(key, registered, key)


class TestRunStrategy(unittest.TestCase):
    def test_an_unknown_strategy_is_rejected_before_any_data_is_fetched(self):
        with patch("data.fetch_historical.fetch_all") as fetch:
            with self.assertRaises(ValueError):
                rue.run_strategy("not_a_us_strategy", years=1, limit=1, windows=1)
            fetch.assert_not_called()

    def test_it_uses_the_frozen_us_universe_and_honours_limit(self):
        with patch.object(rue, "get_swing_universe_us", return_value=["AAPL", "MSFT", "NVDA"]) as uni, \
             patch.object(rue, "build_run_manifest", return_value={"engine_version": "v1",
                          "git": {"commit_hash": "abc", "working_tree_dirty": False}}), \
             patch.object(rue, "save_run_manifest", return_value="/tmp/m.json"), \
             patch.object(rue, "fetch_all", return_value={}) as fetch:
            with self.assertRaises(SystemExit):           # no data -> aborts, which is what we want here
                rue.run_strategy("cross_sectional_momentum_us", years=1, limit=2, windows=1)
            uni.assert_called_once()
            self.assertEqual(fetch.call_args[0][0], ["AAPL", "MSFT"])   # limit applied

    def test_it_prints_the_saved_as_line_run_queued_backtest_parses(self):
        # run_queued_backtest.py greps stdout for "Saved as EXP-NNN" -- if this wording ever drifts,
        # every US backtest would silently look like a failed run.
        import pandas as pd
        idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
        df = pd.DataFrame({"Close": [1.0, 2.0]}, index=idx)
        with patch.object(rue, "get_swing_universe_us", return_value=["AAPL"]), \
             patch.object(rue, "build_run_manifest", return_value={"engine_version": "v1",
                          "git": {"commit_hash": "abc", "working_tree_dirty": False}}), \
             patch.object(rue, "save_run_manifest", return_value="/tmp/m.json"), \
             patch.object(rue, "fetch_all", return_value={"AAPL": df}), \
             patch.dict(rue.RUNNERS, {"cross_sectional_momentum_us": (lambda: (lambda **kw: "EXP-777"),
                                                                      "test variant")}), \
             patch("shutil.copy"), \
             patch("research_lab.experiment_manager.load_experiment",
                   return_value={"verdict": "# Verdict: PASS", "metrics": {"total_trades": 5}}), \
             patch("builtins.print") as pr:
            exp_id = rue.run_strategy("cross_sectional_momentum_us", years=1, limit=0, windows=1)
        self.assertEqual(exp_id, "EXP-777")
        printed = "\n".join(str(c.args[0]) for c in pr.call_args_list if c.args)
        self.assertIn("Saved as EXP-777", printed)

    def test_the_runner_is_called_with_the_fetched_data_and_window_count(self):
        import pandas as pd
        idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
        df = pd.DataFrame({"Close": [1.0, 2.0]}, index=idx)
        seen = {}

        def fake_runner(**kwargs):
            seen.update(kwargs)
            return "EXP-778"

        with patch.object(rue, "get_swing_universe_us", return_value=["AAPL"]), \
             patch.object(rue, "build_run_manifest", return_value={"engine_version": "v1",
                          "git": {"commit_hash": "abc", "working_tree_dirty": False}}), \
             patch.object(rue, "save_run_manifest", return_value="/tmp/m.json"), \
             patch.object(rue, "fetch_all", return_value={"AAPL": df}), \
             patch.dict(rue.RUNNERS, {"cross_sectional_momentum_us": (lambda: fake_runner, "v")}), \
             patch("shutil.copy"), \
             patch("research_lab.experiment_manager.load_experiment",
                   return_value={"verdict": "x", "metrics": {}}), \
             patch("builtins.print"):
            rue.run_strategy("cross_sectional_momentum_us", years=1, limit=0, windows=4)
        self.assertEqual(seen["n_walk_forward_windows"], 4)
        self.assertEqual(list(seen["data"]), ["AAPL"])
        self.assertEqual((str(seen["start_date"]), str(seen["end_date"])), ("2024-01-02", "2024-01-03"))


if __name__ == "__main__":
    unittest.main()
