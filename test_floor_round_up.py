"""
Tests for the 2026-10-29 minimum-trade-size changes: a near miss is bought at the floor
(broker_costs.lift_to_floor), a test window that froze under the floor is "not enough evidence"
rather than a loss, US research pays IBKR's fees with no Indian floor, and India research starts at
Rs2,00,000. Pool F keeps the plain skip.
"""

import inspect
import unittest
from datetime import date

import pandas as pd

from swing_research.base import Signal, Strategy
from swing_research.broker_costs import FLOOR_ROUND_UP_TOLERANCE, lift_to_floor, min_viable_notional

FLOOR = 12469.0


class TestLiftToFloor(unittest.TestCase):
    def test_the_tolerance_is_25_percent(self):
        self.assertEqual(FLOOR_ROUND_UP_TOLERANCE, 0.25)

    def test_a_near_miss_is_raised_to_the_smallest_quantity_that_clears_the_floor(self):
        q = lift_to_floor(118, 100.0, FLOOR)            # Rs11,800 -- 5% short
        self.assertEqual(q, 125)
        self.assertGreaterEqual(q * 100.0, FLOOR)
        self.assertLess((q - 1) * 100.0, FLOOR)

    def test_exactly_25_percent_short_still_lifts_and_further_short_does_not(self):
        self.assertEqual(lift_to_floor(93.52, 100.0, 12469.0, whole_units=False) * 100 >= FLOOR, True)
        self.assertEqual(lift_to_floor(93, 100.0, FLOOR), 93)              # Rs9,300 < Rs9,351.75

    def test_a_trade_already_over_the_floor_is_untouched(self):
        self.assertEqual(lift_to_floor(130, 100.0, FLOOR), 130)

    def test_nothing_is_lifted_from_zero_or_without_a_floor_or_tolerance(self):
        self.assertEqual(lift_to_floor(0, 100.0, FLOOR), 0)
        self.assertEqual(lift_to_floor(118, 100.0, 0.0), 118)
        self.assertEqual(lift_to_floor(118, 100.0, FLOOR, tolerance=0.0), 118)

    def test_fractional_quantities_are_lifted_to_six_decimals(self):
        q = lift_to_floor(0.11, 100000.0, FLOOR, whole_units=False)
        self.assertGreaterEqual(q * 100000.0, FLOOR)
        self.assertEqual(q, round(q, 6))


class _BuyAndHold(Strategy):
    """Buys the one symbol whenever flat, 8% stop, never exits on its own."""
    name = "buy_and_hold_test"

    def precompute(self, price_history):
        return price_history

    def entry_signal_at(self, row):
        price = float(row.Close)
        return Signal(symbol="AAA.NS", direction="BUY", entry_price=price, stop_loss=price * 0.92)


def _flat_prices(n=80):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return {"AAA.NS": pd.DataFrame({"Open": 100.0, "High": 100.5, "Low": 99.5, "Close": 100.0,
                                    "Volume": 100000}, index=idx)}


class TestBacktestEngine(unittest.TestCase):
    def _run(self, capital, round_up):
        from swing_research.backtesting_engine import simulate_portfolio
        return simulate_portfolio(_flat_prices(), _BuyAndHold(), capital,
                                  min_trade_notional=FLOOR, floor_round_up=round_up)

    def test_the_book_that_used_to_freeze_now_trades(self):
        # Rs95,000 at 1% risk and an 8% stop sizes Rs11,875 -- under the floor.
        frozen = self._run(95_000, 0.0)
        self.assertEqual(frozen["trades"], [])
        self.assertGreater(frozen["floor_skips"], 0)
        lifted = self._run(95_000, FLOOR_ROUND_UP_TOLERANCE)
        self.assertEqual(len(lifted["trades"]), 1)
        self.assertEqual(lifted["trades"][0].quantity, 125)

    def test_a_book_far_too_small_is_still_skipped(self):
        r = self._run(70_000, FLOOR_ROUND_UP_TOLERANCE)        # Rs8,750 -- 30% short
        self.assertEqual(r["trades"], [])


class TestStalledWindows(unittest.TestCase):
    def test_a_window_with_floor_skips_and_no_late_entries_is_stalled(self):
        from swing_research.research_director import _stalled_on_floor

        class T:
            def __init__(self, d):
                self.entry_date = d
        early = {"floor_skips": 40, "trades": [T(date(2020, 3, 1))]}
        self.assertTrue(_stalled_on_floor(early, date(2020, 2, 23), date(2023, 6, 1)))
        late = {"floor_skips": 40, "trades": [T(date(2022, 1, 1))]}
        self.assertFalse(_stalled_on_floor(late, date(2020, 2, 23), date(2023, 6, 1)))
        quiet = {"floor_skips": 0, "trades": [T(date(2020, 3, 1))]}
        self.assertFalse(_stalled_on_floor(quiet, date(2020, 2, 23), date(2023, 6, 1)))

    def test_the_auditor_leaves_a_stalled_window_out_instead_of_counting_a_loss(self):
        from research_lab.statistical_auditor import audit
        good = {"total_trades": 200, "expectancy": 300.0}
        frozen = {"total_trades": 8, "expectancy": -1041.7, "stalled": True}
        oos = {"total_trades": 107, "expectancy": 1434.2}
        v = audit([good, frozen], oos, min_trades_total=15, min_out_of_sample_trades=3,
                  min_consistent_window_fraction=0.5)
        self.assertEqual(v.decision, "PASS")
        self.assertEqual(v.checks["stalled_windows"], 1)
        # Before the fix the same numbers failed: 1/2 is fine, but 0/1 with the loss counted is not.
        v_old = audit([{"total_trades": 8, "expectancy": -1041.7}], oos, min_trades_total=15,
                      min_out_of_sample_trades=3, min_consistent_window_fraction=0.5)
        self.assertEqual(v_old.decision, "REJECT")

    def test_a_stalled_holdout_is_not_enough_evidence(self):
        from research_lab.statistical_auditor import audit
        from reporting.lifecycle import _plain_reasons
        v = audit([{"total_trades": 248, "expectancy": 221.6}],
                  {"total_trades": 25, "expectancy": -110.7, "stalled": True},
                  min_trades_total=15, min_out_of_sample_trades=3, min_consistent_window_fraction=0.5)
        self.assertEqual(v.decision, "REJECT")
        self.assertIn("Not enough evidence", v.reasoning)
        self.assertNotIn("Out-of-sample expectancy", v.reasoning)
        self.assertIn("too little evidence", " ".join(_plain_reasons(v.reasoning)))


class TestUSResearchCosts(unittest.TestCase):
    def test_market_is_read_from_the_symbols(self):
        from swing_research.research_director import _market_of
        from swing_research.universe_us import get_swing_universe_us
        us = get_swing_universe_us()[:3]
        self.assertEqual(_market_of({s: None for s in us}), "us")
        self.assertEqual(_market_of({"RELIANCE.NS": None}), "india")
        self.assertEqual(_market_of({}), "india")

    def test_us_windows_pay_ibkr_fees_not_indian_charges(self):
        from swing_research.backtesting_engine import Trade
        from swing_research.us_equity_costs import IBKRUSTieredCostModel, apply_us_broker_costs
        fields = {f: None for f in Trade.__dataclass_fields__}
        fields.update(symbol="AAPL", direction="BUY", entry_date=date(2024, 1, 2), exit_date=date(2024, 2, 1),
                      entry_price=100.0, exit_price=110.0, quantity=100, pnl=1000.0)
        trade = Trade(**{k: v for k, v in fields.items()})
        out = apply_us_broker_costs({"trades": [trade], "daily_equity": None})
        fee = IBKRUSTieredCostModel().round_trip_cost(100, 100.0, 110.0)
        self.assertAlmostEqual(out["trades"][0].pnl, 1000.0 - fee)
        self.assertLess(fee, 5.0)          # Indian charges on $10,000 would be far more

    def test_the_walk_forward_takes_the_round_up_and_market_by_default(self):
        from swing_research.research_director import run_walk_forward_generic
        params = inspect.signature(run_walk_forward_generic).parameters
        self.assertEqual(params["floor_round_up"].default, FLOOR_ROUND_UP_TOLERANCE)
        self.assertIsNone(params["market"].default)


class TestWhereItApplies(unittest.TestCase):
    def test_india_research_starts_at_two_lakh(self):
        from swing_research.research_director import SWING_RESEARCH_STARTING_CAPITAL
        self.assertEqual(SWING_RESEARCH_STARTING_CAPITAL, 200_000)
        import run_swing_experiment
        self.assertIn("SWING_RESEARCH_STARTING_CAPITAL", inspect.getsource(run_swing_experiment))

    def test_paper_books_round_up_and_pool_f_does_not(self):
        import run_paper_trading
        import run_pool_f
        self.assertEqual(inspect.getsource(run_paper_trading).count("floor_round_up=FLOOR_ROUND_UP_TOLERANCE"), 2)
        self.assertNotIn("floor_round_up", inspect.getsource(run_pool_f))

    def test_live_pool_a_rounds_up_and_live_pool_f_does_not(self):
        from run_pool_live import _engine_plan
        from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS
        key = PAPER_TRADING_STRATEGY_SPECS[0].strategy_key
        self.assertEqual(_engine_plan(key, "A")["round_up"], FLOOR_ROUND_UP_TOLERANCE)
        self.assertEqual(_engine_plan(key, "F")["round_up"], 0.0)
        self.assertEqual(_engine_plan(key)["round_up"], FLOOR_ROUND_UP_TOLERANCE)

    def test_the_paper_engine_defaults_to_the_plain_skip(self):
        from deployment.paper_trading_engine import run_daily
        self.assertEqual(inspect.signature(run_daily).parameters["floor_round_up"].default, 0.0)


if __name__ == "__main__":
    unittest.main()


class TestRetest(unittest.TestCase):
    def test_a_retest_promotes_reports_and_leaves_the_queue_alone(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        import research_queue
        import run_queued_backtest as rqb
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        before = research_queue.load(tmp, "india")
        sent = []
        with patch.object(rqb, "implemented_keys", return_value={"industry_momentum"}), \
             patch.object(rqb, "run_backtest", return_value=("EXP-200", "PASS", "")), \
             patch("deployment.auto_paper_promotion.promote",
                   return_value={"promoted": True, "verdict": "PASS", "key": "industry_momentum", "reason": "",
                                 "runner": "run_paper_trading.py (Pool A, India)"}), \
             patch("reporting.telegram_notifier.send_telegram_message", side_effect=lambda m, *a, **k: sent.append(m)):
            self.assertTrue(rqb.retest("india", "industry_momentum", tmp, 3, True, "t", "c"))
        after = research_queue.load(tmp, "india")
        self.assertEqual(after["current"], before["current"])
        row = after["history"][-1]
        self.assertEqual((row["key"], row["experiment_id"], row["outcome"]), ("industry_momentum", "EXP-200", "researched"))
        self.assertEqual(len(sent), 1)
        self.assertIn("Re-test done", sent[0])
        self.assertIn("promoted to paper trading (Pool A)", sent[0])

    def test_an_unknown_key_runs_nothing(self):
        from unittest.mock import patch
        import run_queued_backtest as rqb
        with patch.object(rqb, "implemented_keys", return_value=set()), \
             patch.object(rqb, "run_backtest") as run:
            self.assertFalse(rqb.retest("india", "nope", "/tmp", 3, False, "", ""))
        run.assert_not_called()
