"""
Unit tests for the Downside Beta strategy (Ang, Chen & Xing 2006): the
conditional-beta estimator on hand-built series, the cross-sectional
ranks, the strategy's transition-day entry / 21-day exit, the runner's
warm-up start, and the roadmap's direction deferral. Run with:

    python test_downside_beta.py
"""

import datetime
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from swing_research.base import OpenPosition, PositionUnit
from swing_research.cross_sectional import (
    DOWNSIDE_BETA_LOOKBACK_DAYS, compute_downside_beta_percentile_ranks, compute_downside_beta_score,
)
from swing_research.strategies.downside_beta import (
    DOWNSIDE_BETA_PERCENTILE_THRESHOLD, HOLDING_PERIOD_CALENDAR_DAYS, STOP_LOSS_PCT, DownsideBetaStrategy,
)


def _frame(closes, start=date(2024, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


def _market_and_stock(n=400, down_mult=2.0, up_mult=0.5, seed=1):
    """A market with symmetric noise and a stock that moves down_mult x the
    market on down days and up_mult x on up days -> downside beta ~ down_mult."""
    rng = np.random.default_rng(seed)
    r_m = rng.normal(0.0, 0.01, n)
    r_i = np.where(r_m < 0, down_mult * r_m, up_mult * r_m)
    m = 1000 * np.exp(np.cumsum(r_m))
    s = 100 * np.exp(np.cumsum(r_i))
    return _frame(list(m)), _frame(list(s))


class TestDownsideBetaScore(unittest.TestCase):
    def test_recovers_the_down_day_slope(self):
        market, stock = _market_and_stock()
        beta = compute_downside_beta_score(stock, market["Close"])
        self.assertTrue(beta.iloc[:DOWNSIDE_BETA_LOOKBACK_DAYS].isna().all())
        self.assertFalse(beta.iloc[DOWNSIDE_BETA_LOOKBACK_DAYS:].isna().any())
        # the down-day slope is exactly 2.0 apart from the few days between the window mean and zero
        self.assertAlmostEqual(beta.iloc[-1], 2.0, delta=0.15)

    def test_symmetric_stock_has_downside_beta_equal_to_its_beta(self):
        market, stock = _market_and_stock(down_mult=1.5, up_mult=1.5)
        beta = compute_downside_beta_score(stock, market["Close"])
        self.assertAlmostEqual(beta.iloc[-1], 1.5, places=6)

    def test_short_history_is_all_nan(self):
        market, stock = _market_and_stock(n=100)
        self.assertTrue(compute_downside_beta_score(stock, market["Close"]).isna().all())

    def test_ranks_put_the_high_downside_beta_stock_on_top(self):
        market, hi = _market_and_stock(down_mult=2.0, up_mult=1.0)
        _, lo = _market_and_stock(down_mult=0.5, up_mult=1.0)
        _, mid = _market_and_stock(down_mult=1.0, up_mult=1.0)
        ranks = compute_downside_beta_percentile_ranks({"HI": hi, "LO": lo, "MID": mid}, market["Close"])
        last = {k: v.iloc[-1] for k, v in ranks.items()}
        self.assertAlmostEqual(last["HI"], 100.0)
        self.assertAlmostEqual(last["LO"], 100 / 3)
        self.assertTrue(last["LO"] < last["MID"] < last["HI"])


class TestStrategyRules(unittest.TestCase):
    def _pre(self, percentiles):
        df = _frame([100.0] * len(percentiles))
        df["downside_beta_percentile"] = percentiles
        return DownsideBetaStrategy().precompute(df)

    def test_top_quintile_transition_day_only(self):
        s = DownsideBetaStrategy()
        rows = list(self._pre([50.0, 79.9, 80.0, 85.0, 70.0, 90.0]).itertuples())
        self.assertIsNone(s.entry_signal_at(rows[1]))          # below the quintile
        sig = s.entry_signal_at(rows[2])                        # enters the top quintile today
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.stop_loss, 100.0 * (1 - STOP_LOSS_PCT))
        self.assertAlmostEqual(sig.confidence, 80.0)
        self.assertIsNone(s.entry_signal_at(rows[3]))          # already qualified yesterday
        self.assertIsNotNone(s.entry_signal_at(rows[5]))       # re-entered after dropping out
        self.assertEqual(DOWNSIDE_BETA_PERCENTILE_THRESHOLD, 80.0)

    def test_missing_percentile_never_enters(self):
        df = DownsideBetaStrategy().precompute(_frame([100.0] * 3))
        self.assertTrue(all(DownsideBetaStrategy().entry_signal_at(r) is None for r in df.itertuples()))

    def test_exit_after_the_holding_period(self):
        s = DownsideBetaStrategy()
        rows = list(self._pre([90.0] * 40).itertuples())
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=100.0, entry_date=date(2024, 1, 1), quantity=10)])
        self.assertIsNone(s.exit_signal_at(rows[HOLDING_PERIOD_CALENDAR_DAYS - 1], pos))
        self.assertEqual(s.exit_signal_at(rows[HOLDING_PERIOD_CALENDAR_DAYS], pos), 100.0)


class TestRunnerAndRoadmap(unittest.TestCase):
    def test_runner_judges_from_one_year_after_the_data_start(self):
        import json
        import os
        import tempfile
        from swing_research.research_director import run_downside_beta_experiment
        market, hi = _market_and_stock(n=800, down_mult=2.0, up_mult=1.0, seed=3)
        _, lo = _market_and_stock(n=800, down_mult=0.5, up_mult=1.0, seed=4)
        data = {"HI": hi, "LO": lo}
        tmp = tempfile.mkdtemp()
        exp = run_downside_beta_experiment(data, date(2024, 1, 1), date(2024, 1, 1) + timedelta(days=799),
                                           starting_capital=1_000_000, narrative_call_fn=lambda p: "stub",
                                           experiments_dir=os.path.join(tmp, "e"),
                                           knowledge_base_path=os.path.join(tmp, "kb.jsonl"),
                                           skip_regime_breakdown=True, market_close=market["Close"])
        params = json.load(open(os.path.join(tmp, "e", exp, "parameters.json")))
        self.assertEqual(params["walk_forward_judged_from"], "2024-12-31")
        self.assertEqual(params["data_start_with_warm_up"], "2024-01-01")
        self.assertIn("TOP quintile", params["downside_beta_side"])

    def test_roadmap_defers_multi_year_candidates_by_direction(self):
        from swing_research.research_roadmap import DEFERRED_BY_DIRECTION, build_roadmap
        r = build_roadmap()
        ready = [s.candidate.key for s in r["researchable_now"]]
        for key in DEFERRED_BY_DIRECTION:
            self.assertNotIn(key, ready)
        self.assertIn("long_term_reversal", [s.candidate.key for s in r["deferred_by_direction"]])
        profile = next(s.candidate for s in r["all_scored"] if s.candidate.key == "downside_beta")
        self.assertIn("TOP quintile", profile.direction)


if __name__ == "__main__":
    unittest.main()
