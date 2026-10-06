"""
data/usdinr_history.py -- the rate on a given day.

The property that matters: never look forward. A rate published after a trade closed was not
available to that trade, and using it would quietly flatter (or punish) a finished result.
"""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from data import usdinr_history as uh

HISTORY = {"2026-09-28": 88.10, "2026-09-29": 88.40, "2026-10-01": 88.90, "2026-10-02": 89.20}


def _cache(rates, state_dir=None):
    d = state_dir or tempfile.mkdtemp()
    with open(uh.cache_path(d), "w", encoding="utf-8") as f:
        json.dump({"symbol": uh.SYMBOL, "rates": rates}, f)
    return d


class TestRateOn(unittest.TestCase):
    def test_an_exact_date_returns_that_days_rate(self):
        self.assertEqual(uh.rate_on(HISTORY, "2026-10-01"), 88.90)
        self.assertEqual(uh.rate_on(HISTORY, date(2026, 9, 29)), 88.40)

    def test_a_day_with_no_quote_takes_the_most_recent_EARLIER_rate(self):
        # 2026-09-30 is missing; the answer must be the 29th, never the 1st
        self.assertEqual(uh.rate_on(HISTORY, "2026-09-30"), 88.40)

    def test_it_never_looks_forward(self):
        # a date before the series has no earlier rate -- falling back is right, borrowing the first
        # later rate would be hindsight
        self.assertIsNone(uh.rate_on(HISTORY, "2026-01-01"))
        self.assertEqual(uh.rate_on(HISTORY, "2026-01-01", fallback=96.0), 96.0)

    def test_a_date_after_the_series_takes_the_last_known_rate(self):
        self.assertEqual(uh.rate_on(HISTORY, "2026-12-25"), 89.20)

    def test_an_empty_history_or_junk_date_falls_back_rather_than_guessing(self):
        self.assertEqual(uh.rate_on({}, "2026-10-01", fallback=96.0), 96.0)
        for junk in ("", "not-a-date", None, 7, "2026-13-45"):
            self.assertEqual(uh.rate_on(HISTORY, junk, fallback=96.0), 96.0, junk)


class TestTheCache(unittest.TestCase):
    def test_a_written_cache_reads_back(self):
        self.assertEqual(uh.load_history(_cache(HISTORY)), HISTORY)

    def test_a_missing_or_corrupt_cache_is_an_empty_history_not_an_error(self):
        self.assertEqual(uh.load_history(tempfile.mkdtemp()), {})
        for junk in ("{ truncated", "[]", '{"rates": "nope"}', '{"rates": {"x": "abc"}}'):
            d = tempfile.mkdtemp()
            with open(uh.cache_path(d), "w", encoding="utf-8") as f:
                f.write(junk)
            self.assertEqual(uh.load_history(d), {} if junk != '{"rates": {"x": "abc"}}' else {})

    def test_staleness_is_measured_from_the_files_own_mtime(self):
        d = _cache(HISTORY)
        self.assertFalse(uh.cache_is_stale(d))
        self.assertTrue(uh.cache_is_stale(d, now=datetime.now() + timedelta(hours=48)))
        self.assertTrue(uh.cache_is_stale(tempfile.mkdtemp()))     # no cache at all is stale

    def test_reading_never_fetches_when_told_not_to(self):
        d = _cache(HISTORY)
        with patch.object(uh, "refresh", side_effect=AssertionError("must not fetch")) as fetch:
            self.assertEqual(uh.history_for(d, refresh_if_stale=False), HISTORY)
        fetch.assert_not_called()


class TestRefresh(unittest.TestCase):
    def test_a_failed_download_leaves_the_existing_cache_untouched(self):
        d = _cache(HISTORY)
        with patch.dict("sys.modules", {"yfinance": None}):       # import blows up
            self.assertEqual(uh.refresh(d), HISTORY)
        self.assertEqual(uh.load_history(d), HISTORY)             # still serving the old rates

    def test_a_refresh_merges_rather_than_replaces(self):
        # old dates must survive a shorter download window, or past trades lose their rate
        d = _cache({"2020-01-02": 71.0, **HISTORY})

        class FakeFrame:
            def iterrows(self):
                return iter([(datetime(2026, 10, 3), {"Close": 89.5})])

        fake = type("T", (), {"history": lambda self, **k: FakeFrame()})
        with patch.dict("sys.modules", {"yfinance": type("M", (), {"Ticker": lambda self_, s: fake()})()}):
            merged = uh.refresh(d)
        self.assertEqual(merged["2020-01-02"], 71.0)              # kept
        self.assertEqual(merged["2026-10-03"], 89.5)              # added

    def test_history_for_never_raises_even_when_everything_fails(self):
        with patch.object(uh, "refresh", side_effect=RuntimeError("boom")):
            self.assertEqual(uh.history_for(tempfile.mkdtemp()), {})


if __name__ == "__main__":
    unittest.main()
