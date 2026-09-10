"""
Unit tests for data/fetch_kite_intraday.py's rate limiter and concurrent
fetch path (no network: the per-symbol fetch and the session/token
loading are patched). Run with:

    python test_fetch_kite_intraday.py
"""

import threading
import time
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import data.fetch_kite_intraday as fki


class TestRateLimiter(unittest.TestCase):
    def test_spaces_request_starts_across_threads(self):
        limiter = fki._RateLimiter(min_interval=0.05)
        starts = []
        lock = threading.Lock()

        def worker():
            limiter.wait()
            with lock:
                starts.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        starts.sort()
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(g >= 0.045 for g in gaps), gaps)   # small tolerance for timer resolution


class TestConcurrentFetch(unittest.TestCase):
    def _df(self, value):
        return pd.DataFrame({"Open": [value], "High": [value], "Low": [value], "Close": [value], "Volume": [1]},
                            index=pd.DatetimeIndex([pd.Timestamp("2026-09-10 09:15")]))

    @patch.object(fki, "get_market_data_session", return_value={})
    @patch.object(fki, "_load_instrument_tokens", return_value={})
    def test_max_workers_collects_every_symbol_and_skips_failures(self, _tokens, _session):
        def fake_fetch(symbol, interval, from_date, to_date, headers, rate_limit_delay):
            return None if symbol == "BAD" else self._df(len(symbol))

        with patch.object(fki, "_fetch_symbol", side_effect=fake_fetch):
            data = fki.fetch_all_intraday(["A", "BB", "BAD", "CCC"], "5minute", date(2026, 9, 10), date(2026, 9, 10),
                                          settings=None, max_workers=3)
        self.assertEqual(set(data), {"A", "BB", "CCC"})
        self.assertEqual(float(data["CCC"]["Close"].iloc[0]), 3.0)

    @patch.object(fki, "get_market_data_session", return_value={})
    @patch.object(fki, "_load_instrument_tokens", return_value={})
    def test_on_symbol_reduces_and_drops_frames(self, _tokens, _session):
        seen = {}

        def fake_fetch(symbol, interval, from_date, to_date, headers, rate_limit_delay):
            return self._df(1.0)

        with patch.object(fki, "_fetch_symbol", side_effect=fake_fetch):
            data = fki.fetch_all_intraday(["A", "B"], "5minute", date(2026, 9, 10), date(2026, 9, 10), settings=None,
                                          max_workers=2, on_symbol=lambda s, df: seen.__setitem__(s, len(df)))
        self.assertEqual(data, {})            # nothing retained ...
        self.assertEqual(seen, {"A": 1, "B": 1})   # ... everything reduced


class TestPoolDUniverse(unittest.TestCase):
    def test_pool_d_trades_the_nifty_500_as_bare_kite_symbols(self):
        from run_pool_d import POOL_D_SYMBOLS
        self.assertGreaterEqual(len(POOL_D_SYMBOLS), 400)
        self.assertFalse(any(s.endswith(".NS") for s in POOL_D_SYMBOLS))
        self.assertIn("RELIANCE", POOL_D_SYMBOLS)


if __name__ == "__main__":
    unittest.main()
