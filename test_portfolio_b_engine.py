"""Tests for portfolio_b/engine.py -- the fixed watchlist and synthetic
signal builder."""

import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import portfolio_b.state as pbs
from portfolio_b.engine import (
    DEFAULT_WATCHLIST,
    PROTECTIVE_STOP_PCT,
    build_watchlist_signal,
    get_watchlist,
    get_watchlist_with_names,
)


def _price_history(closes):
    idx = pd.bdate_range(start="2024-01-01", periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                          "Volume": [1000] * len(closes)}, index=idx)


class TestDefaultWatchlist(unittest.TestCase):
    def test_default_watchlist_has_nine_symbols_no_duplicates(self):
        self.assertEqual(len(DEFAULT_WATCHLIST), 9)
        self.assertEqual(len(set(DEFAULT_WATCHLIST.keys())), 9)

    def test_default_watchlist_symbols_are_nse_tickers(self):
        for symbol in DEFAULT_WATCHLIST:
            self.assertTrue(symbol.endswith(".NS"), f"{symbol} should be an NSE ticker")

    def test_default_watchlist_every_symbol_has_a_real_company_name(self):
        for symbol, name in DEFAULT_WATCHLIST.items():
            self.assertTrue(name, f"{symbol} is missing a company name")


class TestGetWatchlist(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = patch.object(pbs, "PORTFOLIO_B_STATE_DIR", self.tmpdir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_get_watchlist_returns_just_symbols(self):
        self.assertEqual(sorted(get_watchlist()), sorted(DEFAULT_WATCHLIST.keys()))

    def test_get_watchlist_with_names_returns_the_full_mapping(self):
        self.assertEqual(get_watchlist_with_names(), DEFAULT_WATCHLIST)

    def test_reflects_a_change_made_via_save_watchlist(self):
        get_watchlist()   # seeds the file
        pbs.save_watchlist({"ONLY.NS": "Only Co"})
        self.assertEqual(get_watchlist(), ["ONLY.NS"])
        self.assertEqual(get_watchlist_with_names(), {"ONLY.NS": "Only Co"})


class TestBuildWatchlistSignal(unittest.TestCase):
    def test_entry_price_is_latest_close(self):
        signal = build_watchlist_signal("AAA.NS", _price_history([90, 95, 100]))
        self.assertEqual(signal.entry_price, 100.0)

    def test_stop_loss_is_eight_percent_below_entry(self):
        signal = build_watchlist_signal("AAA.NS", _price_history([100]))
        self.assertAlmostEqual(signal.stop_loss, 100.0 * (1 - PROTECTIVE_STOP_PCT))

    def test_target_is_two_r_above_entry(self):
        signal = build_watchlist_signal("AAA.NS", _price_history([100]))
        risk = 100.0 - signal.stop_loss
        self.assertAlmostEqual(signal.target, 100.0 + 2 * risk)

    def test_direction_is_always_buy(self):
        signal = build_watchlist_signal("AAA.NS", _price_history([100]))
        self.assertEqual(signal.direction, "BUY")

    def test_strategy_name_identifies_it_as_watchlist_not_a_researched_strategy(self):
        signal = build_watchlist_signal("AAA.NS", _price_history([100]))
        self.assertEqual(signal.strategy_name, "portfolio_b_watchlist")


if __name__ == "__main__":
    unittest.main()
