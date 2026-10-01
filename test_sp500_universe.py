"""Tests for data/sp500_universe.py and swing_research/universe_us.py -- the US-equity
counterparts to data/nifty500_universe.py / swing_research/universe.py."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from data.sp500_universe import get_sp500_symbols


class TestGetSp500Symbols(unittest.TestCase):
    def _write_csv(self, rows):
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write("Symbol,Security,GICS Sector,GICS Sub-Industry,Headquarters Location,Date added,CIK,Founded\n")
            for row in rows:
                f.write(row + "\n")
        return path

    def test_missing_file_raises_a_clear_error(self):
        with patch("data.sp500_universe.CSV_PATH", "/nonexistent/path.csv"):
            with self.assertRaises(FileNotFoundError):
                get_sp500_symbols()

    def test_dot_class_shares_converted_to_dash_for_yfinance(self):
        path = self._write_csv(["BRK.B,Berkshire Hathaway,,,,,,", "BF.B,Brown-Forman,,,,,,"])
        try:
            with patch("data.sp500_universe.CSV_PATH", path):
                self.assertEqual(get_sp500_symbols(), ["BF-B", "BRK-B"])
        finally:
            os.remove(path)

    def test_plain_symbols_sorted_and_deduplicated(self):
        path = self._write_csv(["MSFT,Microsoft,,,,,,", "AAPL,Apple,,,,,,", "AAPL,Apple,,,,,,"])
        try:
            with patch("data.sp500_universe.CSV_PATH", path):
                self.assertEqual(get_sp500_symbols(), ["AAPL", "MSFT"])
        finally:
            os.remove(path)

    def test_blank_symbol_rows_skipped(self):
        path = self._write_csv(["MSFT,Microsoft,,,,,,", ",Blank Row,,,,,,"])
        try:
            with patch("data.sp500_universe.CSV_PATH", path):
                self.assertEqual(get_sp500_symbols(), ["MSFT"])
        finally:
            os.remove(path)


class TestSwingUniverseUsFreeze(unittest.TestCase):
    def setUp(self):
        fd, self.snapshot_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.snapshot_path)

    def tearDown(self):
        if os.path.exists(self.snapshot_path):
            os.remove(self.snapshot_path)

    def test_first_call_freezes_from_the_live_csv_then_reuses_the_snapshot(self):
        import swing_research.universe_us as uus
        with patch.object(uus, "SNAPSHOT_PATH", self.snapshot_path), \
             patch.object(uus, "get_sp500_symbols", return_value=["MSFT", "AAPL"]) as mock_get:
            first = uus.get_swing_universe_us()
            second = uus.get_swing_universe_us()
        self.assertEqual(first, ["AAPL", "MSFT"])
        self.assertEqual(second, first)
        mock_get.assert_called_once()   # second call read the frozen file, not the live source again

    def test_metadata_round_trips_version_and_count(self):
        import swing_research.universe_us as uus
        with patch.object(uus, "SNAPSHOT_PATH", self.snapshot_path), \
             patch.object(uus, "get_sp500_symbols", return_value=["MSFT", "AAPL", "GOOGL"]):
            meta = uus.get_universe_us_metadata()
        self.assertEqual((meta["version"], meta["symbol_count"]), (uus.SWING_UNIVERSE_US_VERSION, 3))
        with open(self.snapshot_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), meta)

    def test_a_version_bump_with_no_matching_snapshot_re_freezes(self):
        import swing_research.universe_us as uus
        with open(self.snapshot_path, "w", encoding="utf-8") as f:
            json.dump({"version": "v0-stale", "symbols": ["OLD"]}, f)
        with patch.object(uus, "SNAPSHOT_PATH", self.snapshot_path), \
             patch.object(uus, "get_sp500_symbols", return_value=["NEW"]):
            self.assertEqual(uus.get_swing_universe_us(), ["NEW"])


if __name__ == "__main__":
    unittest.main()
