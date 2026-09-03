"""Tests for run_pool_a1_legacy.py -- the fully-silent, exit-only daily
pass for the 3 strategies set aside into Pool A1 on 2026-09-03 (Minervini,
Cross-Sectional Momentum, Short-Term Reversal). Mirrors the mocking style
already used in test_run_paper_trading.py (patch each collaborator at its
run_pool_a1_legacy import site, never the real deployment/paper_trading_engine
implementation)."""

import unittest
from unittest.mock import call, patch

import run_pool_a1_legacy as pool_a1


class TestRunOneSkipsFetchWhenFullyWoundDown(unittest.TestCase):
    """A strategy with zero open positions and zero pending exits has
    nothing left to do -- must not fetch data or call run_daily at all."""

    @patch("run_pool_a1_legacy.pte.run_daily")
    @patch("run_pool_a1_legacy.fetch_all")
    @patch("run_pool_a1_legacy.pte.load_portfolio", return_value={"positions": {}, "pending_exits": {}})
    def test_no_fetch_no_run_daily_when_nothing_held(self, mock_load, mock_fetch, mock_run_daily):
        pool_a1._run_one("short_term_reversal")
        mock_fetch.assert_not_called()
        mock_run_daily.assert_not_called()


class TestRunOneFetchesOnlyHeldSymbols(unittest.TestCase):
    """Pool A1 must never fetch the full universe -- only symbols actually
    still open (positions) or awaiting a queued exit fill (pending_exits)."""

    @patch("run_pool_a1_legacy.pte.run_daily", return_value={"new_entries": [], "new_exits": []})
    @patch("run_pool_a1_legacy.fetch_all", return_value={})
    @patch("run_pool_a1_legacy.pte.load_portfolio",
           return_value={"positions": {"TCS": {}}, "pending_exits": {"INFY": {}}})
    def test_fetch_all_called_with_sorted_union_of_held_symbols(self, mock_load, mock_fetch, mock_run_daily):
        pool_a1._run_one("minervini_trend_template_filter")
        mock_fetch.assert_called_once_with(["INFY", "TCS"], period="3y")


class TestRunOneAlwaysCallsRunDailyWithEntriesDisabled(unittest.TestCase):
    """The whole point of Pool A1: entries_enabled=False must be passed on
    every single call, unconditionally -- this is the one line that turns
    a normal daily pass into a wind-down-only pass."""

    @patch("run_pool_a1_legacy.pte.run_daily", return_value={"new_entries": [], "new_exits": []})
    @patch("run_pool_a1_legacy.fetch_all", return_value={})
    @patch("run_pool_a1_legacy.pte.load_portfolio", return_value={"positions": {"TCS": {}}, "pending_exits": {}})
    def test_entries_enabled_false_passed_to_run_daily(self, mock_load, mock_fetch, mock_run_daily):
        pool_a1._run_one("cross_sectional_momentum")
        self.assertEqual(mock_run_daily.call_args.kwargs["entries_enabled"], False)

    @patch("run_pool_a1_legacy.pte.run_daily", return_value={"new_entries": [], "new_exits": []})
    @patch("run_pool_a1_legacy.fetch_all", return_value={})
    @patch("run_pool_a1_legacy.pte.load_portfolio", return_value={"positions": {"TCS": {}}, "pending_exits": {}})
    def test_no_compute_extra_columns_fn_passed(self, mock_load, mock_fetch, mock_run_daily):
        """Confirms the deliberate lean-fetch design: no cross-sectional
        percentile computation is wired in for the exit-only pass."""
        pool_a1._run_one("cross_sectional_momentum")
        self.assertNotIn("compute_extra_columns_fn", mock_run_daily.call_args.kwargs)


class TestMainIsolatesStateDirAndContinuesPastFailure(unittest.TestCase):
    """main() must (a) point the real engine module at Pool A1's own,
    separate state directory before running anything, and (b) keep going
    if one strategy's pass raises, matching run_paper_trading.py's own
    --all-due exception-isolation discipline (TestAllDueLoopContinuesPastOneFailure)."""

    def setUp(self):
        self._original_state_dir = pool_a1.pte.PAPER_TRADING_STATE_DIR

    def tearDown(self):
        pool_a1.pte.PAPER_TRADING_STATE_DIR = self._original_state_dir

    @patch("run_pool_a1_legacy._run_one")
    def test_state_dir_set_to_pool_a1_before_any_run(self, mock_run_one):
        def _assert_isolated(strategy_key):
            self.assertEqual(pool_a1.pte.PAPER_TRADING_STATE_DIR, pool_a1.POOL_A1_STATE_DIR)
        mock_run_one.side_effect = _assert_isolated
        pool_a1.main()
        self.assertEqual(mock_run_one.call_count, len(pool_a1.POOL_A1_STRATEGY_KEYS))

    @patch("run_pool_a1_legacy._run_one")
    def test_one_strategy_failure_does_not_stop_the_others(self, mock_run_one):
        mock_run_one.side_effect = [RuntimeError("data provider outage")] + [None] * (
            len(pool_a1.POOL_A1_STRATEGY_KEYS) - 1
        )
        pool_a1.main()   # must not raise
        self.assertEqual(
            mock_run_one.call_args_list,
            [call(k) for k in pool_a1.POOL_A1_STRATEGY_KEYS],
        )


class TestNeverSendsTelegram(unittest.TestCase):
    """Fully silent per explicit direction ("summary only shows me Pool A,
    B and C and not Pool A1") -- this module must not even import a
    Telegram send function, so no code path could ever accidentally send one."""

    def test_module_has_no_telegram_send_capability(self):
        module_attrs = " ".join(dir(pool_a1)).lower()
        self.assertNotIn("telegram", module_attrs)


class TestStrategyKeysMatchApprovedScope(unittest.TestCase):
    """Exactly the 4 strategies still carrying Rs.10,00,000-scale capital
    (verified against live VPS state 2026-09-03) are classified into Pool
    A1 -- PEAD (already wound to the Rs.1,00,000 floor), MAX Effect, and
    Turn-of-Month (already seeded at Rs.1,00,000) must stay in Pool A
    untouched, per explicit scope."""

    def test_exactly_four_approved_keys(self):
        self.assertEqual(
            set(pool_a1.POOL_A1_STRATEGY_KEYS),
            {
                "fifty_two_week_high_momentum",
                "minervini_trend_template_filter",
                "cross_sectional_momentum",
                "short_term_reversal",
            },
        )


if __name__ == "__main__":
    unittest.main()
