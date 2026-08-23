"""
Unit tests for the deployment/ package (Research Deployment system) --
deployment_manager's registry/state-machine, paper_trading_engine's
idempotency and entry/exit logic, certification's evidence-quality wiring,
drift_report's threshold logic, and pilot_live's eligibility gate. All
tests use temporary paths -- never touch the real deployment/state/ or
deployment/certification_experiments/ directories. Run with:

    python test_deployment.py
"""

import datetime
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord, is_valid_transition
from deployment import deployment_manager
from deployment import paper_trading_engine as pte
from deployment.drift_report import _flag_drift, compute_drift
from deployment.pilot_live import check_pilot_eligibility
from deployment.settings import PAPER_TRADING_WINDDOWN_TARGET_CAPITAL
from swing_research.base import OpenPosition, Signal, Strategy


class TestBaseTransitions(unittest.TestCase):
    def test_research_to_paper_trading_is_valid(self):
        self.assertTrue(is_valid_transition(DeploymentStatus.RESEARCH, DeploymentStatus.PAPER_TRADING))

    def test_research_to_production_is_invalid(self):
        self.assertFalse(is_valid_transition(DeploymentStatus.RESEARCH, DeploymentStatus.PRODUCTION))

    def test_archived_is_reachable_from_any_state(self):
        for status in DeploymentStatus:
            if status == DeploymentStatus.ARCHIVED:
                continue
            self.assertTrue(is_valid_transition(status, DeploymentStatus.ARCHIVED))

    def test_archived_has_no_outgoing_transitions(self):
        for status in DeploymentStatus:
            if status == DeploymentStatus.ARCHIVED:
                continue
            self.assertFalse(is_valid_transition(DeploymentStatus.ARCHIVED, status))

    def test_same_status_is_always_a_valid_noop_transition(self):
        self.assertTrue(is_valid_transition(DeploymentStatus.PAPER_TRADING, DeploymentStatus.PAPER_TRADING))


class TestDeploymentManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "registry.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_register_and_get(self):
        deployment_manager.register_strategy("test_strategy", "Test Strategy", "test family",
                                              registry_path=self.registry_path)
        record = deployment_manager.get_strategy("test_strategy", registry_path=self.registry_path)
        self.assertEqual(record.display_name, "Test Strategy")
        self.assertEqual(record.research_verdict, ResearchVerdict.NOT_YET_EVALUATED)
        self.assertEqual(record.deployment_status, DeploymentStatus.RESEARCH)

    def test_register_is_idempotent_does_not_reset_existing_record(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        deployment_manager.set_research_verdict("s1", ResearchVerdict.PASS, "EXP-999",
                                                 registry_path=self.registry_path)
        deployment_manager.register_strategy("s1", "S1 renamed", "fam", registry_path=self.registry_path)
        record = deployment_manager.get_strategy("s1", registry_path=self.registry_path)
        self.assertEqual(record.research_verdict, ResearchVerdict.PASS)   # not reset

    def test_set_research_verdict_never_touches_deployment_status(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        deployment_manager.set_deployment_status("s1", DeploymentStatus.PAPER_TRADING, "test",
                                                  registry_path=self.registry_path)
        deployment_manager.set_research_verdict("s1", ResearchVerdict.REJECT, "EXP-1",
                                                 registry_path=self.registry_path)
        record = deployment_manager.get_strategy("s1", registry_path=self.registry_path)
        self.assertEqual(record.research_verdict, ResearchVerdict.REJECT)
        self.assertEqual(record.deployment_status, DeploymentStatus.PAPER_TRADING)   # untouched

    def test_set_deployment_status_never_touches_research_verdict(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        deployment_manager.set_research_verdict("s1", ResearchVerdict.PASS, "EXP-1",
                                                 registry_path=self.registry_path)
        deployment_manager.set_deployment_status("s1", DeploymentStatus.PAPER_TRADING, "test",
                                                  registry_path=self.registry_path)
        record = deployment_manager.get_strategy("s1", registry_path=self.registry_path)
        self.assertEqual(record.research_verdict, ResearchVerdict.PASS)   # untouched
        self.assertEqual(record.deployment_status, DeploymentStatus.PAPER_TRADING)

    def test_invalid_transition_raises_without_force(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        with self.assertRaises(ValueError):
            deployment_manager.set_deployment_status("s1", DeploymentStatus.PRODUCTION, "test",
                                                       registry_path=self.registry_path)

    def test_invalid_transition_allowed_with_force(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        record = deployment_manager.set_deployment_status("s1", DeploymentStatus.PRODUCTION, "test",
                                                            force=True, registry_path=self.registry_path)
        self.assertEqual(record.deployment_status, DeploymentStatus.PRODUCTION)

    def test_deployment_status_history_is_recorded(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        deployment_manager.set_deployment_status("s1", DeploymentStatus.PAPER_TRADING, "reason A",
                                                  registry_path=self.registry_path)
        record = deployment_manager.get_strategy("s1", registry_path=self.registry_path)
        self.assertEqual(len(record.deployment_status_history), 1)
        self.assertEqual(record.deployment_status_history[0]["to_status"], "PAPER_TRADING")
        self.assertEqual(record.deployment_status_history[0]["reason"], "reason A")

    def test_operating_on_unregistered_strategy_raises(self):
        with self.assertRaises(KeyError):
            deployment_manager.set_research_verdict("nonexistent", ResearchVerdict.PASS, "",
                                                      registry_path=self.registry_path)

    def test_list_strategies(self):
        deployment_manager.register_strategy("s1", "S1", "fam", registry_path=self.registry_path)
        deployment_manager.register_strategy("s2", "S2", "fam", registry_path=self.registry_path)
        self.assertEqual(len(deployment_manager.list_strategies(registry_path=self.registry_path)), 2)


class _AlwaysQualifiesStrategy(Strategy):
    """Minimal test double: qualifies (and thus fires an entry signal) on
    the FIRST bar of whatever data it's given, exits after Close < entry
    x 0.5 (never, in these small fixtures) -- lets tests control entry/exit
    purely via the OHLC values in the synthetic data itself."""
    name = "always_qualifies_test_strategy"
    max_units = 1
    risk_pct_per_unit = 0.01   # realistic -- see backtesting_engine.py's own sizing convention
    min_lookback_days = 0

    def precompute(self, price_history):
        df = price_history.copy()
        df["signal_day"] = False
        if len(df) > 0:
            df.iloc[-1, df.columns.get_loc("signal_day")] = True
        return df

    def entry_signal_at(self, row):
        if not bool(row.signal_day):
            return None
        entry_price = float(row.Close)
        return Signal(symbol="", direction="BUY", entry_price=entry_price,
                      stop_loss=entry_price * 0.9, strategy_name=self.name)

    def exit_signal_at(self, row, open_position):
        if float(row.Close) >= open_position.units[0].entry_price * 1.5:
            return float(row.Close)
        return None


def _one_day_df(date_str, open_, high, low, close):
    idx = pd.DatetimeIndex([pd.Timestamp(date_str)])
    return pd.DataFrame({"Open": [open_], "High": [high], "Low": [low], "Close": [close],
                          "Volume": [1000]}, index=idx)


class TestPaperTradingEngine(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher_state = patch.object(pte, "PAPER_TRADING_STATE_DIR", self.tmpdir)
        self.patcher_state.start()
        self.strategy_key = "test_strategy"

    def tearDown(self):
        self.patcher_state.stop()
        shutil.rmtree(self.tmpdir)

    def test_entry_fires_and_persists_position(self):
        strategy = _AlwaysQualifiesStrategy()
        data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data,
                                as_of_date=datetime.date(2024, 1, 1))
        self.assertEqual(result["status"], "processed")
        self.assertEqual(len(result["new_entries"]), 1)
        self.assertEqual(result["new_entries"][0]["symbol"], "SYM")

        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertIn("SYM", portfolio["positions"])

    def test_second_call_for_same_date_is_a_noop(self):
        strategy = _AlwaysQualifiesStrategy()
        data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data,
                      as_of_date=datetime.date(2024, 1, 1))
        result2 = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data,
                                 as_of_date=datetime.date(2024, 1, 1))
        self.assertEqual(result2["status"], "skipped_already_processed")

    def test_earlier_date_after_later_date_is_also_a_noop(self):
        strategy = _AlwaysQualifiesStrategy()
        data_day2 = {"SYM": _one_day_df("2024-01-02", 100, 101, 99, 100)}
        data_day1 = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data_day2,
                      as_of_date=datetime.date(2024, 1, 2))
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data_day1,
                                as_of_date=datetime.date(2024, 1, 1))
        self.assertEqual(result["status"], "skipped_already_processed")

    def test_force_overrides_idempotency_guard(self):
        strategy = _AlwaysQualifiesStrategy()
        data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data,
                      as_of_date=datetime.date(2024, 1, 1))
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data,
                                as_of_date=datetime.date(2024, 1, 1), force=True)
        self.assertEqual(result["status"], "processed")

    def test_stop_loss_hit_closes_position(self):
        strategy = _AlwaysQualifiesStrategy()
        entry_data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: entry_data,
                      as_of_date=datetime.date(2024, 1, 1))
        # entry stop_loss = 100 * 0.9 = 90 -- a day whose Low undercuts it should close the position
        stop_hit_data = {"SYM": _one_day_df("2024-01-02", 95, 96, 85, 92)}
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: stop_hit_data,
                                as_of_date=datetime.date(2024, 1, 2))
        self.assertEqual(len(result["new_exits"]), 1)
        self.assertEqual(result["new_exits"][0]["reason"], "stop_loss")
        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertNotIn("SYM", portfolio["positions"])

    def test_cash_decreases_by_cost_on_entry(self):
        strategy = _AlwaysQualifiesStrategy()
        data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data,
                      as_of_date=datetime.date(2024, 1, 1))
        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertLess(portfolio["cash"], portfolio["starting_capital"])

    def test_mark_to_market_equity_unchanged_by_a_same_day_entry(self):
        # Regression test for a real bug found 2026-08-18: entering a
        # position used to ADD its cost to mark_to_market_equity on top
        # of a baseline that already equalled the pre-trade cash --
        # double-counting the entry (equity looked like it jumped by the
        # trade's own size). Moving cash into a position of equal value
        # changes nothing on entry day (no price move has happened yet).
        # Expected baseline is PAPER_TRADING_WINDDOWN_TARGET_CAPITAL, not a
        # hardcoded literal -- see _load_portfolio()'s fresh-strategy
        # capital policy (fixed 2026-08-23): a brand-new strategy now
        # starts directly at the target capital, so this test's baseline
        # tracks that setting rather than drifting out of sync with it.
        strategy = _AlwaysQualifiesStrategy()
        data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data,
                                as_of_date=datetime.date(2024, 1, 1))
        self.assertEqual(result["mark_to_market_equity"], PAPER_TRADING_WINDDOWN_TARGET_CAPITAL)

    def test_mark_to_market_equity_reflects_same_day_exit_proceeds(self):
        # Regression test for the other half of the same bug: an exit's
        # proceeds were never added anywhere (position removed from
        # `positions`, but cash's increase wasn't reflected in equity
        # either) -- equity silently understated by the exited position's
        # value on any day a position closes (default same_day_close
        # fill timing, same setup as test_stop_loss_hit_closes_position).
        strategy = _AlwaysQualifiesStrategy()
        entry_data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: entry_data,
                      as_of_date=datetime.date(2024, 1, 1))
        stop_hit_data = {"SYM": _one_day_df("2024-01-02", 95, 96, 85, 92)}
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: stop_hit_data,
                                as_of_date=datetime.date(2024, 1, 2))
        self.assertEqual(len(result["new_exits"]), 1)
        self.assertEqual(result["open_positions"], 0)
        # No open positions left -- equity must equal cash exactly (the
        # bug would have left equity stuck at a stale, lower snapshot
        # that never picked up the exit proceeds).
        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertEqual(result["mark_to_market_equity"], round(portfolio["cash"], 2))

    def test_compute_live_metrics_reflects_a_closed_trade(self):
        strategy = _AlwaysQualifiesStrategy()
        entry_data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: entry_data,
                      as_of_date=datetime.date(2024, 1, 1))
        exit_data = {"SYM": _one_day_df("2024-01-02", 95, 96, 85, 92)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: exit_data,
                      as_of_date=datetime.date(2024, 1, 2))
        metrics = pte.compute_live_metrics(self.strategy_key)
        self.assertEqual(metrics["total_trades"], 1)

    # -- daily_pnl / open_positions_detail (added 2026-08-18, per direct
    # user feedback: "Daily P&L" showed 0 whenever nothing closed that
    # day, even though open positions' value had genuinely moved; open
    # positions showed only entry price/stop-loss, never current value or
    # unrealized P&L) --

    def test_daily_pnl_is_none_on_the_first_ever_recorded_day(self):
        strategy = _AlwaysQualifiesStrategy()
        data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data,
                                as_of_date=datetime.date(2024, 1, 1))
        self.assertIsNone(result["daily_pnl"])

    def test_daily_pnl_reflects_unrealized_movement_with_no_exit(self):
        strategy = _AlwaysQualifiesStrategy()
        entry_data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: entry_data,
                      as_of_date=datetime.date(2024, 1, 1))
        # Position held open, price moved up, no exit signal fires --
        # under the OLD computation (sum of today's booked exits only)
        # this showed 0.0; it must now reflect the real equity change.
        moved_data = {"SYM": _one_day_df("2024-01-02", 105, 106, 104, 105)}
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: moved_data,
                                as_of_date=datetime.date(2024, 1, 2))
        self.assertEqual(len(result["new_exits"]), 0)
        self.assertIsNotNone(result["daily_pnl"])
        self.assertGreater(result["daily_pnl"], 0)

    def test_open_positions_detail_has_current_value_and_unrealized_pnl(self):
        strategy = _AlwaysQualifiesStrategy()
        entry_data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        entry_result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: entry_data,
                                     as_of_date=datetime.date(2024, 1, 1))
        quantity = entry_result["new_entries"][0]["quantity"]

        moved_data = {"SYM": _one_day_df("2024-01-02", 105, 106, 104, 105)}
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: moved_data,
                                as_of_date=datetime.date(2024, 1, 2))
        detail = result["open_positions_detail"]
        self.assertEqual(len(detail), 1)
        d = detail[0]
        self.assertEqual(d["symbol"], "SYM")
        self.assertEqual(d["current_price"], 105.0)
        self.assertEqual(d["current_value"], round(105.0 * quantity, 2))
        self.assertEqual(d["unrealized_pnl"], round((105.0 - 100.0) * quantity, 2))
        self.assertAlmostEqual(d["unrealized_pnl_pct"], 5.0, places=1)

    def test_next_day_open_signal_appears_in_new_pending_entries(self):
        strategy = _AlwaysQualifiesStrategy()
        data = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: data,
                                as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)
        self.assertEqual(result["new_entries"], [])   # not filled yet
        self.assertEqual(len(result["new_pending_entries"]), 1)
        self.assertEqual(result["new_pending_entries"][0]["symbol"], "SYM")
        self.assertEqual(result["new_pending_entries"][0]["signal_date"], "2024-01-01")
        self.assertEqual(result["new_pending_entries"][0]["signal_price"], 100.0)

    def test_signal_price_flows_through_to_the_resolved_fill(self):
        # Per direct user feedback 2026-08-19: the morning execution
        # message should show both the signal price (the close the day
        # the signal fired) and the actual fill price -- confirms
        # signal_price survives the queue -> resolve round trip.
        strategy = _AlwaysQualifiesStrategy()
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")
        day1 = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)
        day2 = {"SYM": _one_day_df("2024-01-02", 105, 106, 104, 107)}
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day2,
                                as_of_date=datetime.date(2024, 1, 2), execution_config=cfg)
        self.assertEqual(len(result["new_entries"]), 1)
        self.assertEqual(result["new_entries"][0]["signal_price"], 100.0)   # day 1's close
        self.assertEqual(result["new_entries"][0]["entry_price"], 105.0)   # day 2's real Open


class TestResolvePendingFillsAtOpen(unittest.TestCase):
    """resolve_pending_fills_at_open() -- the near-market-open runner
    added 2026-08-18 per direct user feedback ("I should be getting a
    Telegram message at the live time when something is bought or
    sold"). Verifies it resolves queued fills using the SAME logic as
    run_daily()'s own STEP 0, without disturbing that same day's later
    EOD run_daily() call."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher_state = patch.object(pte, "PAPER_TRADING_STATE_DIR", self.tmpdir)
        self.patcher_state.start()
        self.strategy_key = "test_strategy"

    def tearDown(self):
        self.patcher_state.stop()
        shutil.rmtree(self.tmpdir)

    def test_no_pending_items_skips_the_data_fetch_entirely(self):
        def _should_not_be_called():
            raise AssertionError("fetch_open_data_fn must not be called when nothing is pending")

        strategy = _AlwaysQualifiesStrategy()
        result = pte.resolve_pending_fills_at_open(
            self.strategy_key, strategy, fetch_open_data_fn=_should_not_be_called,
            as_of_date=datetime.date(2024, 1, 2),
        )
        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["new_entries"], [])
        self.assertEqual(result["new_exits"], [])

    def test_resolves_a_queued_entry_at_the_real_open(self):
        strategy = _AlwaysQualifiesStrategy()
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")
        day1 = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)
        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertIn("SYM", portfolio["pending_entries"])

        open_only_data = {"SYM": _one_day_df("2024-01-02", 105, 106, 104, 999)}   # Close irrelevant here
        result = pte.resolve_pending_fills_at_open(
            self.strategy_key, strategy, fetch_open_data_fn=lambda: open_only_data,
            as_of_date=datetime.date(2024, 1, 2), execution_config=cfg,
        )
        self.assertEqual(len(result["new_entries"]), 1)
        self.assertEqual(result["new_entries"][0]["entry_price"], 105.0)   # today's real Open, not Close
        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertIn("SYM", portfolio["positions"])
        self.assertNotIn("SYM", portfolio["pending_entries"])

    def test_does_not_touch_last_processed_date(self):
        # Equity/daily_pnl are end-of-day concepts -- this morning-only
        # call must never mark the day as "processed" (that stays
        # run_daily()'s sole responsibility), or the later EOD call would
        # be skipped by the idempotency guard.
        strategy = _AlwaysQualifiesStrategy()
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")
        day1 = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)

        open_only_data = {"SYM": _one_day_df("2024-01-02", 105, 106, 104, 999)}
        pte.resolve_pending_fills_at_open(
            self.strategy_key, strategy, fetch_open_data_fn=lambda: open_only_data,
            as_of_date=datetime.date(2024, 1, 2), execution_config=cfg,
        )
        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertEqual(portfolio["last_processed_date"], "2024-01-01")   # unchanged

    def test_later_eod_run_daily_call_same_day_still_works_normally(self):
        # The morning call and the EOD call must compose cleanly: the EOD
        # call's own STEP 0 finds nothing left (already resolved that
        # morning) and proceeds to detect NEW signals as usual.
        strategy = _AlwaysQualifiesStrategy()
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")
        day1 = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)

        open_only_data = {"SYM": _one_day_df("2024-01-02", 105, 106, 104, 999)}
        morning_result = pte.resolve_pending_fills_at_open(
            self.strategy_key, strategy, fetch_open_data_fn=lambda: open_only_data,
            as_of_date=datetime.date(2024, 1, 2), execution_config=cfg,
        )
        self.assertEqual(len(morning_result["new_entries"]), 1)

        # EOD call, same day, full data -- must not re-fill SYM (already
        # filled this morning) and must complete as "processed".
        eod_data = {"SYM": _one_day_df("2024-01-02", 105, 106, 104, 108)}
        eod_result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: eod_data,
                                    as_of_date=datetime.date(2024, 1, 2), execution_config=cfg)
        self.assertEqual(eod_result["status"], "processed")
        self.assertEqual(eod_result["new_entries"], [])   # not re-filled
        self.assertEqual(len(eod_result["open_positions_detail"]), 1)   # SYM correctly still held

    def test_no_open_price_available_leaves_it_pending_not_an_error(self):
        strategy = _AlwaysQualifiesStrategy()
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")
        day1 = {"SYM": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)

        result = pte.resolve_pending_fills_at_open(
            self.strategy_key, strategy, fetch_open_data_fn=lambda: {},   # no bar available yet
            as_of_date=datetime.date(2024, 1, 2), execution_config=cfg,
        )
        self.assertEqual(result["new_entries"], [])
        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertIn("SYM", portfolio["pending_entries"])   # still queued, not lost


class TestDriftReport(unittest.TestCase):
    def test_flag_drift_relative_within_threshold_is_none(self):
        self.assertIsNone(_flag_drift("cagr", 20.0, 22.0))   # 10% relative diff

    def test_flag_drift_relative_beyond_threshold_flags(self):
        result = _flag_drift("cagr", 20.0, 5.0)   # 75% relative diff
        self.assertIsNotNone(result)
        self.assertIn("MATERIAL", result)

    def test_flag_drift_absolute_metric_within_threshold_is_none(self):
        self.assertIsNone(_flag_drift("win_rate", 0.40, 0.45))   # 5pp diff, threshold 20pp

    def test_flag_drift_absolute_metric_beyond_threshold_flags(self):
        result = _flag_drift("win_rate", 0.40, 0.05)   # 35pp diff
        self.assertIsNotNone(result)

    def test_flag_drift_none_values_are_not_flagged(self):
        self.assertIsNone(_flag_drift("sharpe_ratio", None, 1.0))
        self.assertIsNone(_flag_drift("sharpe_ratio", 1.0, None))

    def test_compute_drift_with_mocked_dependencies(self):
        with patch("deployment.drift_report.load_experiment") as mock_load, \
             patch("deployment.drift_report.compute_live_metrics") as mock_live:
            mock_load.return_value = {"metrics": {"win_rate": 0.5, "expectancy": 100.0, "cagr": 20.0,
                                                    "sharpe_ratio": 1.0, "max_drawdown_pct": 10.0}}
            mock_live.return_value = {"win_rate": 0.1, "expectancy": 100.0, "cagr": 20.0,
                                       "sharpe_ratio": 1.0, "max_drawdown_pct": 10.0, "total_trades": 5}
            drift = compute_drift("strategy_key", "EXP-999", "/fake/dir")
            self.assertIn("win_rate", drift["flags"])   # 0.5 -> 0.1 is a 40pp swing, beyond 20pp
            self.assertNotIn("cagr", drift["flags"])    # identical


class TestPilotEligibility(unittest.TestCase):
    def _record(self, verdict=ResearchVerdict.PASS, status=DeploymentStatus.PAPER_TRADING):
        return StrategyRecord(strategy_key="s1", display_name="S1", strategy_family="fam",
                               research_verdict=verdict, deployment_status=status)

    def test_eligible_when_all_conditions_met(self):
        result = check_pilot_eligibility(self._record(), paper_trading_days_elapsed=90,
                                          paper_trading_trade_count=30)
        self.assertTrue(result.eligible)
        self.assertGreater(result.recommended_allocation_pct, 0)

    def test_not_eligible_without_pass_verdict(self):
        result = check_pilot_eligibility(self._record(verdict=ResearchVerdict.INCONCLUSIVE),
                                          paper_trading_days_elapsed=90, paper_trading_trade_count=30)
        self.assertFalse(result.eligible)
        self.assertTrue(any("Research Verdict" in r for r in result.reasons))

    def test_not_eligible_without_enough_paper_trading_days(self):
        result = check_pilot_eligibility(self._record(), paper_trading_days_elapsed=10,
                                          paper_trading_trade_count=30)
        self.assertFalse(result.eligible)

    def test_not_eligible_without_enough_trades(self):
        result = check_pilot_eligibility(self._record(), paper_trading_days_elapsed=90,
                                          paper_trading_trade_count=2)
        self.assertFalse(result.eligible)

    def test_not_eligible_if_not_currently_paper_trading(self):
        result = check_pilot_eligibility(self._record(status=DeploymentStatus.RESEARCH),
                                          paper_trading_days_elapsed=90, paper_trading_trade_count=30)
        self.assertFalse(result.eligible)

    def test_eligibility_alone_never_sets_deployment_status(self):
        # Structural guarantee: check_pilot_eligibility has no side effects
        # and no access to deployment_manager at all.
        import deployment.pilot_live as pl
        self.assertNotIn("deployment_manager", dir(pl))


if __name__ == "__main__":
    unittest.main()
