"""
Unit tests for the Realized Low Volatility strategy (Nifty100 Low Vol 30
methodology): the 1-year volatility score and ranks, bottom-decile
transition-day entry, 126-trading-day exit, and the runner's warm-up.

    python test_realized_low_volatility.py
"""

import json
import os
import tempfile
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from swing_research.base import OpenPosition, PositionUnit
from swing_research.cross_sectional import (
    REALIZED_VOL_LOOKBACK_DAYS, compute_realized_volatility_percentile_ranks, compute_realized_volatility_score,
)
from swing_research.strategies.realized_low_volatility import (
    HOLDING_PERIOD_CALENDAR_DAYS, LOW_VOL_PERCENTILE_THRESHOLD, STOP_LOSS_PCT, RealizedLowVolatilityStrategy,
)


def _frame(closes, start=date(2024, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(closes))])
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


def _walk(n, daily_sigma, seed):
    rng = np.random.default_rng(seed)
    return _frame(list(100 * np.exp(np.cumsum(rng.normal(0, daily_sigma, n)))))


class TestScoreAndRanks(unittest.TestCase):
    def test_score_is_one_year_std_of_log_returns(self):
        df = _walk(400, 0.02, 1)
        score = compute_realized_volatility_score(df)
        self.assertTrue(score.iloc[:REALIZED_VOL_LOOKBACK_DAYS].isna().all())
        expected = np.log(df["Close"]).diff().iloc[-252:].std()
        self.assertAlmostEqual(score.iloc[-1], expected, places=10)

    def test_calm_stock_ranks_lowest(self):
        data = {"CALM": _walk(400, 0.005, 2), "MID": _walk(400, 0.02, 3), "WILD": _walk(400, 0.05, 4)}
        ranks = compute_realized_volatility_percentile_ranks(data)
        last = {k: v.iloc[-1] for k, v in ranks.items()}
        self.assertAlmostEqual(last["CALM"], 100 / 3)
        self.assertAlmostEqual(last["WILD"], 100.0)


class TestRules(unittest.TestCase):
    def _pre(self, percentiles):
        df = _frame([100.0] * len(percentiles))
        df["realized_vol_percentile"] = percentiles
        return RealizedLowVolatilityStrategy().precompute(df)

    def test_bottom_decile_transition_day_only(self):
        s = RealizedLowVolatilityStrategy()
        rows = list(self._pre([50.0, 10.1, 10.0, 5.0, 30.0, 8.0]).itertuples())
        self.assertIsNone(s.entry_signal_at(rows[1]))
        sig = s.entry_signal_at(rows[2])
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.confidence, 90.0)                      # lowest vol ranks first
        self.assertAlmostEqual(sig.stop_loss, 100.0 * (1 - STOP_LOSS_PCT))
        self.assertIsNone(s.entry_signal_at(rows[3]))                     # already in the decile
        self.assertIsNotNone(s.entry_signal_at(rows[5]))                  # re-entered
        self.assertEqual(LOW_VOL_PERCENTILE_THRESHOLD, 10.0)

    def test_exit_after_six_months(self):
        s = RealizedLowVolatilityStrategy()
        rows = list(self._pre([5.0] * 200).itertuples())
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=100.0, entry_date=date(2024, 1, 1), quantity=10)])
        self.assertIsNone(s.exit_signal_at(rows[HOLDING_PERIOD_CALENDAR_DAYS - 1], pos))
        self.assertEqual(s.exit_signal_at(rows[HOLDING_PERIOD_CALENDAR_DAYS], pos), 100.0)
        self.assertEqual(HOLDING_PERIOD_CALENDAR_DAYS, 183)


class TestRunner(unittest.TestCase):
    def test_runner_judges_from_one_year_after_the_data_start(self):
        from swing_research.research_director import run_realized_low_volatility_experiment
        data = {"CALM": _walk(800, 0.005, 5), "MID": _walk(800, 0.02, 6), "WILD": _walk(800, 0.05, 7)}
        tmp = tempfile.mkdtemp()
        exp = run_realized_low_volatility_experiment(
            data, date(2024, 1, 1), date(2024, 1, 1) + timedelta(days=799), narrative_call_fn=lambda p: "stub",
            experiments_dir=os.path.join(tmp, "e"), knowledge_base_path=os.path.join(tmp, "kb.jsonl"),
            skip_regime_breakdown=True)
        with open(os.path.join(tmp, "e", exp, "parameters.json"), encoding="utf-8") as f:
            params = json.load(f)
        self.assertEqual(params["walk_forward_judged_from"], "2024-12-31")
        self.assertIn("BOTTOM decile", params["low_vol_side"])


if __name__ == "__main__":
    unittest.main()
