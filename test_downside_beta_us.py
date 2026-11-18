"""
Unit tests for the US-market Downside Beta port (Ang, Chen & Xing 2006):
the research wrapper's warm-up start and market-series substitution
(S&P 500 index in place of Nifty 50), the published-strategy record, the
roadmap candidate profile, and the paper-trading catalog wiring. No
network calls -- market_close is always a hand-built fixture or the
wrapper's call is never actually invoked against real data. Run with:

    python test_downside_beta_us.py
"""

import json
import os
import tempfile
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd


def _frame(closes, start=date(2024, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


def _market_and_stock(n=800, down_mult=2.0, up_mult=0.5, seed=1):
    rng = np.random.default_rng(seed)
    r_m = rng.normal(0.0, 0.01, n)
    r_i = np.where(r_m < 0, down_mult * r_m, up_mult * r_m)
    m = 1000 * np.exp(np.cumsum(r_m))
    s = 100 * np.exp(np.cumsum(r_i))
    return _frame(list(m)), _frame(list(s))


class TestRunnerUsesSp500AsMarket(unittest.TestCase):
    def test_runner_judges_from_one_year_after_the_data_start(self):
        from swing_research.research_director import run_downside_beta_us_experiment
        market, hi = _market_and_stock(n=800, down_mult=2.0, up_mult=1.0, seed=3)
        _, lo = _market_and_stock(n=800, down_mult=0.5, up_mult=1.0, seed=4)
        data = {"HI": hi, "LO": lo}
        tmp = tempfile.mkdtemp()
        exp = run_downside_beta_us_experiment(
            data, date(2024, 1, 1), date(2024, 1, 1) + timedelta(days=799),
            starting_capital=100_000, narrative_call_fn=lambda p: "stub",
            experiments_dir=os.path.join(tmp, "e"),
            knowledge_base_path=os.path.join(tmp, "kb.jsonl"),
            skip_regime_breakdown=True, market_close=market["Close"],
        )
        params = json.load(open(os.path.join(tmp, "e", exp, "parameters.json")))
        self.assertEqual(params["walk_forward_judged_from"], "2024-12-31")
        self.assertEqual(params["data_start_with_warm_up"], "2024-01-01")
        self.assertIn("TOP quintile", params["downside_beta_side"])
        self.assertIn("S&P 500", params["downside_beta_market"])

    def test_default_market_close_is_fetched_from_sp500_not_nifty(self):
        """When market_close is not passed, the US wrapper must reach for the S&P 500 index
        fetcher, never Nifty -- the one input this port changes vs. the India wrapper."""
        import unittest.mock as mock
        from swing_research import research_director

        market, stock = _market_and_stock(n=800)
        with mock.patch("data.fetch_historical.fetch_sp500_index", return_value=market) as fetch_mock:
            tmp = tempfile.mkdtemp()
            research_director.run_downside_beta_us_experiment(
                {"X": stock}, date(2024, 1, 1), date(2024, 1, 1) + timedelta(days=799),
                starting_capital=100_000, narrative_call_fn=lambda p: "stub",
                experiments_dir=os.path.join(tmp, "e"),
                knowledge_base_path=os.path.join(tmp, "kb.jsonl"),
                skip_regime_breakdown=True,
            )
            fetch_mock.assert_called_once()


class TestPublishedStrategyRecord(unittest.TestCase):
    def test_downside_beta_us_shares_mechanism_with_india_but_names_sp500(self):
        from swing_research.published_research_analyst import DOWNSIDE_BETA, DOWNSIDE_BETA_US
        self.assertEqual(DOWNSIDE_BETA_US.mechanism, DOWNSIDE_BETA.mechanism)
        self.assertEqual(DOWNSIDE_BETA_US.rules, DOWNSIDE_BETA.rules)
        self.assertIn("^GSPC", DOWNSIDE_BETA_US.variant_chosen)
        self.assertIn("Ang, Chen & Xing", DOWNSIDE_BETA_US.distinctiveness)


class TestRoadmapCandidateProfile(unittest.TestCase):
    def test_us_downside_beta_profile_matches_the_queue_key(self):
        from swing_research.research_roadmap import CANDIDATES
        profile = next(c for c in CANDIDATES if c.key == "us_downside_beta")
        self.assertEqual(profile.market, "US")
        self.assertEqual(profile.horizon_lane, "swing")
        self.assertIn("TOP quintile", profile.direction)
        self.assertIn("daily_ohlcv_history", profile.data_requirements)


class TestPaperTradingCatalogWiring(unittest.TestCase):
    def test_us_downside_beta_is_wired_for_paper_without_touching_the_network(self):
        from swing_research.base import Strategy
        from swing_research.strategy_catalog import PAPER_US_STRATEGIES
        self.assertIn("us_downside_beta", PAPER_US_STRATEGIES)
        factory, extra_fn = PAPER_US_STRATEGIES["us_downside_beta"]
        self.assertIsInstance(factory(), Strategy)
        self.assertEqual(factory().name, "downside_beta")
        self.assertTrue(callable(extra_fn))

    def test_us_downside_beta_reaches_pool_i(self):
        import run_pool_i
        self.assertIn("us_downside_beta", run_pool_i.POOL_I_STRATEGIES)

    def test_us_downside_beta_not_in_research_only_legacy(self):
        from swing_research.strategy_catalog import RESEARCH_ONLY_LEGACY
        self.assertNotIn("us_downside_beta", RESEARCH_ONLY_LEGACY)

    def test_us_downside_beta_registered_in_the_us_cli(self):
        import run_us_experiment
        self.assertIn("us_downside_beta", run_us_experiment.RUNNERS)


if __name__ == "__main__":
    unittest.main()
