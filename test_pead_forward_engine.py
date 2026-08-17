"""
Tests for deployment/pead_forward_engine.py and deployment/pead_signal.py
-- uses synthetic, mocked earnings events (never a real yfinance call --
real-data verification is done separately, see the audit report) so these
tests are fast, deterministic, and isolated.
"""

import shutil
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

from data.fetch_earnings_calendar import EarningsEvent, fetch_recent_earnings_events_chunked
from deployment.pead_signal import compute_sue, evaluate_pead_signal, PEAD_SUE_THRESHOLD


def _strong_positive_eps_history():
    """12 quarters, most recent first, engineered for a clearly positive SUE."""
    return [10.0, 6.0, 5.8, 5.5, 5.2, 5.0, 4.9, 4.8, 4.7, 4.6, 4.5, 4.4]


def _flat_eps_history():
    """No surprise -- this quarter matches last year exactly."""
    return [5.0, 6.0, 5.8, 5.5, 5.2, 5.0, 4.9, 4.8, 4.7, 4.6, 4.5, 4.4]


class TestSUEComputation(unittest.TestCase):
    def test_sufficient_history_computes_sue(self):
        r = compute_sue(_strong_positive_eps_history())
        self.assertTrue(r.sufficient_history)
        self.assertIsNotNone(r.sue)

    def test_insufficient_history_returns_none_not_a_guess(self):
        r = compute_sue(_strong_positive_eps_history()[:8])
        self.assertFalse(r.sufficient_history)
        self.assertIsNone(r.sue)

    def test_strong_surprise_exceeds_threshold(self):
        r = compute_sue(_strong_positive_eps_history())
        signal, _ = evaluate_pead_signal(r)
        self.assertTrue(signal)

    def test_no_surprise_does_not_signal(self):
        r = compute_sue(_flat_eps_history())
        signal, _ = evaluate_pead_signal(r)
        self.assertFalse(signal)


class TestChunkedEarningsScanDelegation(unittest.TestCase):
    """The multiprocessing/subprocess-isolated path (used for symbol lists
    above chunk_size, to bound memory across a large universe -- see
    data/fetch_earnings_calendar.py's DEFAULT_SCAN_CHUNK_SIZE docstring) is
    verified separately against the real production universe, not here --
    unittest.mock patches applied in this process do not propagate into
    the subprocess workers. What IS verified here, deterministically: a
    symbol list at or below chunk_size is delegated directly, in-process,
    with no subprocess overhead and no behavior change from before this
    fix existed."""

    def test_small_list_delegates_directly_without_subprocess(self):
        fake_event = EarningsEvent(symbol="TEST.NS", announcement_date=date(2026, 1, 1),
                                    reported_eps=10.0, eps_estimate=5.0, surprise_pct=100.0,
                                    trailing_actual_eps=_strong_positive_eps_history())
        with patch("data.fetch_earnings_calendar.fetch_recent_earnings_events",
                   return_value=[fake_event]) as mocked:
            result = fetch_recent_earnings_events_chunked(["TEST.NS"], date(2026, 1, 2), lookback_days=10,
                                                            chunk_size=25)
        mocked.assert_called_once_with(["TEST.NS"], date(2026, 1, 2), lookback_days=10)
        self.assertEqual(result, [fake_event])


def _make_price_data(symbols, n=80, start_price=100.0):
    dates = pd.bdate_range("2024-01-01", periods=n)
    data = {}
    for sym in symbols:
        close = pd.Series([start_price] * n, index=dates)
        data[sym] = pd.DataFrame({
            "Open": close, "High": close * 1.01, "Low": close * 0.98, "Close": close, "Volume": 100000,
        }, index=dates)
    return data, dates


class TestForwardEngineLookaheadGuard(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.patcher = patch("deployment.paper_trading_engine.PAPER_TRADING_STATE_DIR", self.tmp_dir)
        self.patcher.start()
        self.patcher2 = patch("deployment.pead_forward_engine.PAPER_TRADING_STATE_DIR", self.tmp_dir)
        self.patcher2.start()

    def tearDown(self):
        self.patcher.stop()
        self.patcher2.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_same_day_announcement_is_not_acted_on(self):
        from deployment.pead_forward_engine import run_pead_daily, load_events

        data, dates = _make_price_data(["TEST.NS"])
        today = dates[10].date()
        fake_event = EarningsEvent(symbol="TEST.NS", announcement_date=today, reported_eps=10.0,
                                    eps_estimate=5.0, surprise_pct=100.0,
                                    trailing_actual_eps=_strong_positive_eps_history())

        with patch("deployment.pead_forward_engine.fetch_recent_earnings_events_chunked", return_value=[fake_event]):
            result = run_pead_daily(["TEST.NS"], as_of_date=today, fetch_ohlcv_fn=lambda syms: data)

        self.assertEqual(result["new_entries"], [])   # same-day -- must not act
        events = load_events()
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["eligible"])
        self.assertIn("same-day", events[0]["eligibility_reason"])

    def test_prior_day_announcement_with_strong_sue_generates_entry(self):
        from deployment.pead_forward_engine import run_pead_daily, load_events

        data, dates = _make_price_data(["TEST.NS"])
        announce_date = dates[9].date()
        run_date = dates[10].date()
        fake_event = EarningsEvent(symbol="TEST.NS", announcement_date=announce_date, reported_eps=10.0,
                                    eps_estimate=5.0, surprise_pct=100.0,
                                    trailing_actual_eps=_strong_positive_eps_history())

        with patch("deployment.pead_forward_engine.fetch_recent_earnings_events_chunked", return_value=[fake_event]):
            result = run_pead_daily(["TEST.NS"], as_of_date=run_date, fetch_ohlcv_fn=lambda syms: data)

        self.assertEqual(len(result["new_entries"]), 1)
        self.assertEqual(result["new_entries"][0]["symbol"], "TEST.NS")
        events = load_events()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["eligible"])
        self.assertTrue(events[0]["signal_generated"])
        self.assertTrue(events[0]["trade_taken"])

    def test_same_event_not_double_counted_across_two_daily_runs(self):
        from deployment.pead_forward_engine import run_pead_daily, load_events

        data, dates = _make_price_data(["TEST.NS"])
        announce_date = dates[5].date()
        fake_event = EarningsEvent(symbol="TEST.NS", announcement_date=announce_date, reported_eps=10.0,
                                    eps_estimate=5.0, surprise_pct=100.0,
                                    trailing_actual_eps=_flat_eps_history())   # no signal, just testing dedup

        with patch("deployment.pead_forward_engine.fetch_recent_earnings_events_chunked", return_value=[fake_event]):
            run_pead_daily(["TEST.NS"], as_of_date=dates[6].date(), fetch_ohlcv_fn=lambda syms: data)
            run_pead_daily(["TEST.NS"], as_of_date=dates[7].date(), fetch_ohlcv_fn=lambda syms: data, force=True)

        events = load_events()
        self.assertEqual(len(events), 1, "The same real-world event must only be logged once across two daily runs")

    def test_insufficient_history_is_logged_as_ineligible_not_silently_dropped(self):
        from deployment.pead_forward_engine import run_pead_daily, load_events

        data, dates = _make_price_data(["TEST.NS"])
        announce_date = dates[5].date()
        fake_event = EarningsEvent(symbol="TEST.NS", announcement_date=announce_date, reported_eps=10.0,
                                    eps_estimate=5.0, surprise_pct=100.0,
                                    trailing_actual_eps=_strong_positive_eps_history()[:6])   # too short

        with patch("deployment.pead_forward_engine.fetch_recent_earnings_events_chunked", return_value=[fake_event]):
            result = run_pead_daily(["TEST.NS"], as_of_date=dates[6].date(), fetch_ohlcv_fn=lambda syms: data)

        self.assertEqual(result["new_entries"], [])
        events = load_events()
        self.assertFalse(events[0]["eligible"])
        self.assertFalse(events[0]["trade_taken"])


class TestExitSideReusesExistingMachinery(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.patcher = patch("deployment.paper_trading_engine.PAPER_TRADING_STATE_DIR", self.tmp_dir)
        self.patcher.start()
        self.patcher2 = patch("deployment.pead_forward_engine.PAPER_TRADING_STATE_DIR", self.tmp_dir)
        self.patcher2.start()

    def tearDown(self):
        self.patcher.stop()
        self.patcher2.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_holding_period_exit_fires_via_the_shared_run_daily(self):
        from deployment.pead_forward_engine import run_pead_daily
        from deployment.pead_signal import PEAD_HOLDING_PERIOD_TRADING_DAYS

        data, dates = _make_price_data(["TEST.NS"], n=150)
        announce_date = dates[1].date()
        entry_run_date = dates[2].date()
        fake_event = EarningsEvent(symbol="TEST.NS", announcement_date=announce_date, reported_eps=10.0,
                                    eps_estimate=5.0, surprise_pct=100.0,
                                    trailing_actual_eps=_strong_positive_eps_history())

        with patch("deployment.pead_forward_engine.fetch_recent_earnings_events_chunked", return_value=[fake_event]):
            entry_result = run_pead_daily(["TEST.NS"], as_of_date=entry_run_date, fetch_ohlcv_fn=lambda syms: data)
        self.assertEqual(len(entry_result["new_entries"]), 1)

        # Walk forward past the holding period with no further events --
        # the exit must come from PEADStrategy.exit_signal_at() via the
        # SHARED run_daily(), not from any PEAD-specific exit code.
        exit_seen = False
        with patch("deployment.pead_forward_engine.fetch_recent_earnings_events_chunked", return_value=[]):
            for i in range(3, 3 + PEAD_HOLDING_PERIOD_TRADING_DAYS + 10):
                if i >= len(dates):
                    break
                result = run_pead_daily(["TEST.NS"], as_of_date=dates[i].date(), fetch_ohlcv_fn=lambda syms: data)
                if result.get("new_exits"):
                    exit_seen = True
                    self.assertEqual(result["new_exits"][0]["reason"], "signal_exit")
                    break
        self.assertTrue(exit_seen, "Holding-period exit never fired")


if __name__ == "__main__":
    unittest.main()
