"""
Unit tests for the Price Delay Factor strategy (Hou & Moskowitz 2005): the
rolling multi-lag regression estimator on hand-built series, the
cross-sectional ranks, the strategy's transition-day entry / 21-day exit,
the research wrapper's long warm-up and S&P 500 market-series
substitution, the published-strategy record, the roadmap candidate
profile, and the paper-trading catalog wiring. No network calls -- data
is always a hand-built fixture, or the wrapper's fetch is mocked. Run
with:

    python test_price_delay_factor.py
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
    PRICE_DELAY_LOOKBACK_WEEKS, PRICE_DELAY_NUM_LAGS,
    compute_price_delay_percentile_ranks, compute_price_delay_score,
)
from swing_research.strategies.price_delay_factor import (
    HOLDING_PERIOD_CALENDAR_DAYS, PRICE_DELAY_PERCENTILE_THRESHOLD, STOP_LOSS_PCT, PriceDelayFactorStrategy,
)


def _daily_from_weekly_returns(weekly_returns, start_price=100.0, start=date(2010, 1, 1)):
    """Spreads each weekly log return evenly over 5 business days, so the weekly resample inside
    compute_price_delay_score() recovers (close to) the same return per week."""
    n_days = len(weekly_returns) * 5
    idx = pd.date_range(start, periods=n_days, freq="B")
    price = start_price
    closes = []
    for wr in weekly_returns:
        step = wr / 5.0
        for _ in range(5):
            price *= np.exp(step)
            closes.append(price)
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


def _market_returns(n_weeks=PRICE_DELAY_LOOKBACK_WEEKS + 60, seed=1):
    return np.random.default_rng(seed).normal(0.0, 0.02, n_weeks)


def _stock_from_market(r_m, lag_fraction=0.0, seed=1):
    """A stock whose return is a mix of `lag_fraction` worth of LAST week's market return and
    (1-lag_fraction) worth of THIS week's -- so lag_fraction=0 is an instant reactor (delay ~0)
    and lag_fraction>0 has real, known delay, against the SAME market series `r_m`."""
    rng = np.random.default_rng(seed)
    lagged_m = np.concatenate([[0.0], r_m[:-1]])
    r_i = (1 - lag_fraction) * r_m + lag_fraction * lagged_m + rng.normal(0.0, 0.0005, len(r_m))
    return _daily_from_weekly_returns(r_i, start_price=100.0)


def _market_and_stock(n_weeks=PRICE_DELAY_LOOKBACK_WEEKS + 60, lag_fraction=0.0, seed=1):
    r_m = _market_returns(n_weeks, seed)
    market = _daily_from_weekly_returns(r_m, start_price=1000.0)
    stock = _stock_from_market(r_m, lag_fraction, seed + 1000)
    return market, stock


class TestPriceDelayScore(unittest.TestCase):
    def test_instant_reactor_has_near_zero_delay(self):
        market, stock = _market_and_stock(lag_fraction=0.0, seed=1)
        delay = compute_price_delay_score(stock, market["Close"])
        self.assertFalse(delay.dropna().empty)
        self.assertLess(delay.dropna().iloc[-1], 0.05)

    def test_lagged_reactor_has_materially_higher_delay(self):
        market, instant = _market_and_stock(lag_fraction=0.0, seed=2)
        _, delayed = _market_and_stock(lag_fraction=0.5, seed=2)
        d_instant = compute_price_delay_score(instant, market["Close"]).dropna().iloc[-1]
        d_delayed = compute_price_delay_score(delayed, market["Close"]).dropna().iloc[-1]
        self.assertGreater(d_delayed, d_instant)
        self.assertGreater(d_delayed, 0.15)

    def test_short_history_is_all_nan(self):
        market, stock = _market_and_stock(n_weeks=PRICE_DELAY_LOOKBACK_WEEKS - 10, seed=1)
        self.assertTrue(compute_price_delay_score(stock, market["Close"]).isna().all())

    def test_delay_is_clipped_to_unit_interval(self):
        market, stock = _market_and_stock(lag_fraction=1.0, seed=5)
        delay = compute_price_delay_score(stock, market["Close"]).dropna()
        self.assertTrue((delay >= 0.0).all())
        self.assertTrue((delay <= 1.0).all())

    def test_ranks_put_the_high_delay_stock_on_top(self):
        r_m = _market_returns(seed=6)
        market = _daily_from_weekly_returns(r_m, start_price=1000.0)
        hi = _stock_from_market(r_m, lag_fraction=0.6, seed=7)
        lo = _stock_from_market(r_m, lag_fraction=0.0, seed=8)
        mid = _stock_from_market(r_m, lag_fraction=0.3, seed=9)
        ranks = compute_price_delay_percentile_ranks({"HI": hi, "LO": lo, "MID": mid}, market["Close"])
        last = {k: v.dropna().iloc[-1] for k, v in ranks.items()}
        self.assertAlmostEqual(last["HI"], 100.0)
        self.assertAlmostEqual(last["LO"], 100 / 3)
        self.assertTrue(last["LO"] < last["MID"] < last["HI"])


class TestStrategyRules(unittest.TestCase):
    def _pre(self, percentiles):
        idx = pd.to_datetime([date(2024, 1, 1) + timedelta(days=i) for i in range(len(percentiles))])
        c = pd.Series([100.0] * len(percentiles), index=idx, dtype=float)
        df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})
        df["price_delay_percentile"] = percentiles
        return PriceDelayFactorStrategy().precompute(df)

    def test_top_decile_transition_day_only(self):
        s = PriceDelayFactorStrategy()
        rows = list(self._pre([50.0, 89.9, 90.0, 95.0, 80.0, 92.0]).itertuples())
        self.assertIsNone(s.entry_signal_at(rows[1]))          # below the decile
        sig = s.entry_signal_at(rows[2])                        # enters the top decile today
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.stop_loss, 100.0 * (1 - STOP_LOSS_PCT))
        self.assertAlmostEqual(sig.confidence, 90.0)
        self.assertIsNone(s.entry_signal_at(rows[3]))          # already qualified yesterday
        self.assertIsNotNone(s.entry_signal_at(rows[5]))       # re-entered after dropping out
        self.assertEqual(PRICE_DELAY_PERCENTILE_THRESHOLD, 90.0)

    def test_missing_percentile_never_enters(self):
        df = PriceDelayFactorStrategy().precompute(self._pre([float("nan")] * 3))
        self.assertTrue(all(PriceDelayFactorStrategy().entry_signal_at(r) is None for r in df.itertuples()))

    def test_exit_after_the_holding_period(self):
        s = PriceDelayFactorStrategy()
        rows = list(self._pre([95.0] * 40).itertuples())
        pos = OpenPosition(symbol="X", direction="BUY",
                           units=[PositionUnit(entry_price=100.0, entry_date=date(2024, 1, 1), quantity=10)])
        self.assertIsNone(s.exit_signal_at(rows[HOLDING_PERIOD_CALENDAR_DAYS - 1], pos))
        self.assertEqual(s.exit_signal_at(rows[HOLDING_PERIOD_CALENDAR_DAYS], pos), 100.0)


class TestRunnerUsesSp500AsMarketAndLongWarmUp(unittest.TestCase):
    def test_runner_judges_from_three_years_after_the_data_start(self):
        from swing_research.research_director import run_price_delay_factor_us_experiment
        n_days = (PRICE_DELAY_LOOKBACK_WEEKS + PRICE_DELAY_NUM_LAGS + 70) * 5
        market, hi = _market_and_stock(n_weeks=n_days // 5, lag_fraction=0.6, seed=10)
        _, lo = _market_and_stock(n_weeks=n_days // 5, lag_fraction=0.0, seed=11)
        data = {"HI": hi, "LO": lo}
        start_date = date(2010, 1, 1)
        end_date = start_date + timedelta(days=n_days * 7 // 5 - 1)
        tmp = tempfile.mkdtemp()
        exp = run_price_delay_factor_us_experiment(
            data, start_date, end_date,
            starting_capital=100_000, narrative_call_fn=lambda p: "stub",
            experiments_dir=os.path.join(tmp, "e"),
            knowledge_base_path=os.path.join(tmp, "kb.jsonl"),
            skip_regime_breakdown=True, market_close=market["Close"],
        )
        params = json.load(open(os.path.join(tmp, "e", exp, "parameters.json")))
        self.assertEqual(params["warm_up_days"], 1095)
        self.assertEqual(params["data_start_with_warm_up"], start_date.isoformat())
        self.assertEqual(params["walk_forward_judged_from"], (start_date + timedelta(days=1095)).isoformat())
        self.assertIn("TOP decile", params["price_delay_side"])
        self.assertIn("S&P 500", params["price_delay_market"])
        self.assertEqual(params["price_delay_lookback_weeks"], PRICE_DELAY_LOOKBACK_WEEKS)
        self.assertEqual(params["price_delay_num_lags"], PRICE_DELAY_NUM_LAGS)

    def test_default_market_close_is_fetched_from_sp500(self):
        """When market_close is not passed, the wrapper must reach for the S&P 500 index fetcher."""
        import unittest.mock as mock
        from swing_research import research_director

        market, stock = _market_and_stock(n_weeks=200, seed=12)
        with mock.patch("data.fetch_historical.fetch_sp500_index", return_value=market) as fetch_mock:
            tmp = tempfile.mkdtemp()
            research_director.run_price_delay_factor_us_experiment(
                {"X": stock}, date(2010, 1, 1), date(2010, 1, 1) + timedelta(days=1399),
                starting_capital=100_000, narrative_call_fn=lambda p: "stub",
                experiments_dir=os.path.join(tmp, "e"),
                knowledge_base_path=os.path.join(tmp, "kb.jsonl"),
                skip_regime_breakdown=True,
            )
            fetch_mock.assert_called_once()


class TestPublishedStrategyRecord(unittest.TestCase):
    def test_price_delay_factor_us_record_names_its_source_and_market(self):
        from swing_research.published_research_analyst import PRICE_DELAY_FACTOR_US
        self.assertIn("Hou", PRICE_DELAY_FACTOR_US.source_citation)
        self.assertIn("Moskowitz", PRICE_DELAY_FACTOR_US.source_citation)
        self.assertIn("lag", PRICE_DELAY_FACTOR_US.mechanism.lower())
        self.assertIn("genuinely new", PRICE_DELAY_FACTOR_US.distinctiveness.lower())


class TestRoadmapCandidateProfile(unittest.TestCase):
    def test_us_price_delay_factor_profile_matches_the_queue_key(self):
        from swing_research.research_roadmap import CANDIDATES
        profile = next(c for c in CANDIDATES if c.key == "us_price_delay_factor")
        self.assertEqual(profile.market, "US")
        self.assertEqual(profile.horizon_lane, "swing")
        self.assertIn("top-decile", profile.direction)
        self.assertIn("daily_ohlcv_history", profile.data_requirements)


class TestPaperTradingCatalogWiring(unittest.TestCase):
    def test_us_price_delay_factor_is_wired_for_paper_without_touching_the_network(self):
        from swing_research.base import Strategy
        from swing_research.strategy_catalog import PAPER_US_STRATEGIES
        self.assertIn("us_price_delay_factor", PAPER_US_STRATEGIES)
        factory, extra_fn = PAPER_US_STRATEGIES["us_price_delay_factor"]
        self.assertIsInstance(factory(), Strategy)
        self.assertEqual(factory().name, "price_delay_factor")
        self.assertTrue(callable(extra_fn))

    def test_us_price_delay_factor_reaches_pool_i(self):
        import run_pool_i
        self.assertIn("us_price_delay_factor", run_pool_i.POOL_I_STRATEGIES)

    def test_us_price_delay_factor_not_in_research_only_legacy(self):
        from swing_research.strategy_catalog import RESEARCH_ONLY_LEGACY
        self.assertNotIn("us_price_delay_factor", RESEARCH_ONLY_LEGACY)

    def test_us_price_delay_factor_registered_in_the_us_cli(self):
        import run_us_experiment
        self.assertIn("us_price_delay_factor", run_us_experiment.RUNNERS)


if __name__ == "__main__":
    unittest.main()
