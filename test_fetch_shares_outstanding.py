"""
Unit tests for data/fetch_shares_outstanding.py's on-disk cache -- no real
network calls (fetch_fn is always injected). Mirrors the cache-first /
missing-only-refetch / max-age-refresh contract already established by
data/fetch_earnings_calendar.py's cache. Run with:

    python test_fetch_shares_outstanding.py
"""

import datetime
import os
import tempfile
import unittest

from data.fetch_shares_outstanding import (
    get_shares_outstanding, load_shares_outstanding_cache, save_shares_outstanding_cache,
    shares_outstanding_cache_age_days,
)


class TestCacheRoundTrip(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "shares_outstanding_cache.json")

    def test_empty_cache_when_no_file(self):
        self.assertEqual(load_shares_outstanding_cache(self.path), {})
        self.assertIsNone(shares_outstanding_cache_age_days(self.path))

    def test_save_then_load_round_trips(self):
        save_shares_outstanding_cache({"RELIANCE.NS": 13_500_000_000.0, "TCS.NS": 3_600_000_000.0}, self.path)
        loaded = load_shares_outstanding_cache(self.path)
        self.assertEqual(loaded, {"RELIANCE.NS": 13_500_000_000.0, "TCS.NS": 3_600_000_000.0})

    def test_age_is_zero_the_day_it_was_written(self):
        save_shares_outstanding_cache({"TCS.NS": 3_600_000_000.0}, self.path)
        self.assertEqual(shares_outstanding_cache_age_days(self.path), 0)


class TestGetSharesOutstanding(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "shares_outstanding_cache.json")

    def test_fetches_only_missing_symbols(self):
        save_shares_outstanding_cache({"TCS.NS": 3_600_000_000.0}, self.path)
        fetched = {}

        def fake_fetch(symbols):
            fetched["called_with"] = list(symbols)
            return {s: 999.0 for s in symbols}

        result = get_shares_outstanding(["TCS.NS", "INFY.NS"], path=self.path, fetch_fn=fake_fetch)
        self.assertEqual(fetched["called_with"], ["INFY.NS"])   # TCS.NS already cached
        self.assertEqual(result, {"TCS.NS": 3_600_000_000.0, "INFY.NS": 999.0})

    def test_symbol_the_fetch_could_not_find_is_simply_absent(self):
        result = get_shares_outstanding(["GHOST.NS"], path=self.path, fetch_fn=lambda symbols: {})
        self.assertEqual(result, {})

    def test_stale_cache_triggers_a_full_refresh(self):
        old_day = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
        import json
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": old_day, "shares_by_symbol": {"TCS.NS": 1.0}}, f)
        fetched = {}

        def fake_fetch(symbols):
            fetched["called_with"] = list(symbols)
            return {s: 2.0 for s in symbols}

        result = get_shares_outstanding(["TCS.NS"], path=self.path, fetch_fn=fake_fetch, max_age_days=30)
        self.assertEqual(fetched["called_with"], ["TCS.NS"])   # re-fetched despite already being cached
        self.assertEqual(result, {"TCS.NS": 2.0})

    def test_fresh_cache_is_not_refreshed(self):
        save_shares_outstanding_cache({"TCS.NS": 3_600_000_000.0}, self.path)

        def fail_if_called(symbols):
            raise AssertionError("should not have been called -- cache is fresh")

        result = get_shares_outstanding(["TCS.NS"], path=self.path, fetch_fn=fail_if_called, max_age_days=30)
        self.assertEqual(result, {"TCS.NS": 3_600_000_000.0})


if __name__ == "__main__":
    unittest.main()
