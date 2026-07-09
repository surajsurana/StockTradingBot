"""
Mock-based unit tests for execution/tick_size.py -- fixes a real production
bug where every live order was rounded to a hardcoded 0.05 tick regardless
of the instrument's actual tick size, causing Kite to reject orders for any
symbol with a different tick size (KEI.NS uses 0.50; the order that
surfaced this was rejected with "Tick size for this script is 0.50").

Run with:
    python test_tick_size.py
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from execution.tick_size import get_tick_size, fetch_nse_tick_sizes, DEFAULT_TICK_SIZE


class TestFetchNseTickSizes(unittest.TestCase):
    @patch("requests.get")
    def test_parses_csv_into_symbol_to_tick_dict(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = (
            "instrument_token,tradingsymbol,tick_size\n"
            "12345,RELIANCE,0.05\n"
            "67890,KEI,0.50\n"
        )
        mock_get.return_value = mock_resp

        result = fetch_nse_tick_sizes(api_key="key", access_token="token")

        self.assertEqual(result["RELIANCE"], 0.05)
        self.assertEqual(result["KEI"], 0.50)


class TestGetTickSize(unittest.TestCase):
    def setUp(self):
        fd, self.cache_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.cache_path)  # start as if the cache doesn't exist yet

    def tearDown(self):
        if os.path.exists(self.cache_path):
            os.unlink(self.cache_path)

    @patch("execution.tick_size.fetch_nse_tick_sizes")
    def test_fetches_and_caches_on_first_call(self, mock_fetch):
        mock_fetch.return_value = {"KEI": 0.50, "RELIANCE": 0.05}

        tick = get_tick_size("KEI.NS", api_key="key", access_token="token", cache_path=self.cache_path)

        self.assertEqual(tick, 0.50)
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertTrue(os.path.exists(self.cache_path))

    @patch("execution.tick_size.fetch_nse_tick_sizes")
    def test_second_call_uses_cache_not_a_fresh_fetch(self, mock_fetch):
        mock_fetch.return_value = {"KEI": 0.50}

        get_tick_size("KEI.NS", api_key="key", access_token="token", cache_path=self.cache_path)
        get_tick_size("KEI.NS", api_key="key", access_token="token", cache_path=self.cache_path)

        self.assertEqual(mock_fetch.call_count, 1)

    @patch("execution.tick_size.fetch_nse_tick_sizes")
    def test_symbol_not_in_dump_falls_back_to_default(self, mock_fetch):
        mock_fetch.return_value = {"RELIANCE": 0.05}

        tick = get_tick_size("SOMENEWSTOCK.NS", api_key="key", access_token="token",
                              cache_path=self.cache_path)

        self.assertEqual(tick, DEFAULT_TICK_SIZE)

    @patch("execution.tick_size.fetch_nse_tick_sizes")
    def test_fetch_failure_with_no_cache_falls_back_to_default(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("network error")

        tick = get_tick_size("KEI.NS", api_key="key", access_token="token", cache_path=self.cache_path)

        self.assertEqual(tick, DEFAULT_TICK_SIZE)

    @patch("execution.tick_size.fetch_nse_tick_sizes")
    def test_stale_cache_triggers_refresh(self, mock_fetch):
        from datetime import datetime, timedelta
        stale_cache = {
            "fetched_at": (datetime.now() - timedelta(days=30)).isoformat(),
            "tick_sizes": {"KEI": 0.05},  # deliberately wrong/stale value
        }
        with open(self.cache_path, "w") as f:
            json.dump(stale_cache, f)

        mock_fetch.return_value = {"KEI": 0.50}  # fresh, corrected value

        tick = get_tick_size("KEI.NS", api_key="key", access_token="token",
                              cache_path=self.cache_path, max_age_days=7)

        self.assertEqual(tick, 0.50)
        self.assertEqual(mock_fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
