"""The dashboard's price cache must never show a blank or shrunken set of quotes because a refresh failed or the
server restarted: unrealised P&L is worked out from these prices, and a missing price means "valued at cost", which
read as a strategy's P&L suddenly dropping to 0 or negative."""
import os
import tempfile
import time
import unittest
from unittest import mock

import pandas as pd

from dashboard.server import PriceCache


def _frame(price):
    return pd.DataFrame({"Close": [price - 1, price]}, index=pd.to_datetime(["2026-09-18", "2026-09-21"]))


class TestPriceCache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _cache(self, symbols=("AAA.NS", "BBB.NS")):
        c = PriceCache(self.dir)
        c._held_symbols = lambda: list(symbols)
        c._kite_ltp = lambda syms: {}
        c._pool_e_symbols = lambda: []
        return c

    def test_a_restart_starts_from_the_last_good_prices(self):
        c = self._cache()
        c.prices, c.prev_close, c.as_of = {"AAA.NS": 101.0}, {"AAA.NS": 100.0}, "2026-09-21T13:40:00"
        c.crypto_prices, c.usdinr = {"BTCUSDT": 50000.0}, 88.0
        c._save_disk(force=True)
        fresh = PriceCache(self.dir)                                   # a restart
        self.assertEqual((fresh.prices, fresh.prev_close, fresh.as_of), ({"AAA.NS": 101.0}, {"AAA.NS": 100.0}, "2026-09-21T13:40:00"))
        self.assertEqual((fresh.crypto_prices, fresh.usdinr), ({"BTCUSDT": 50000.0}, 88.0))

    def test_a_very_old_cache_file_is_ignored(self):
        c = self._cache()
        c.prices = {"AAA.NS": 1.0}
        c._save_disk(force=True)
        path = os.path.join(self.dir, PriceCache.CACHE_FILE)
        old = time.time() - 100 * 3600
        os.utime(path, (old, old))
        self.assertEqual(PriceCache(self.dir).prices, {})

    def test_an_empty_or_partial_refresh_keeps_the_quotes_it_already_had(self):
        c = self._cache()
        c.prices = {"AAA.NS": 101.0, "BBB.NS": 202.0}
        with mock.patch("data.fetch_historical.fetch_all", return_value={}), mock.patch("data.fetch_crypto.fetch_crypto_last_prices", return_value={}), \
                mock.patch("data.fetch_crypto.fetch_usdinr_rate", return_value=None):
            c.refresh_once()
        self.assertEqual(c.prices, {"AAA.NS": 101.0, "BBB.NS": 202.0})     # a failed fetch changes nothing
        with mock.patch("data.fetch_historical.fetch_all", return_value={"AAA.NS": _frame(103.0)}), mock.patch("data.fetch_crypto.fetch_crypto_last_prices", return_value={}), \
                mock.patch("data.fetch_crypto.fetch_usdinr_rate", return_value=None):
            c.refresh_once()
        self.assertEqual(c.prices, {"AAA.NS": 103.0, "BBB.NS": 202.0})     # only the symbol that came back moves

    def test_symbols_no_longer_held_are_dropped(self):
        c = self._cache(("AAA.NS",))
        c.prices = {"AAA.NS": 101.0, "GONE.NS": 5.0}
        with mock.patch("data.fetch_historical.fetch_all", return_value={"AAA.NS": _frame(102.0)}), mock.patch("data.fetch_crypto.fetch_crypto_last_prices", return_value={}), \
                mock.patch("data.fetch_crypto.fetch_usdinr_rate", return_value=None):
            c.refresh_once()
        self.assertEqual(c.prices, {"AAA.NS": 102.0})

    def test_a_failed_crypto_fetch_keeps_the_last_coin_prices_and_rate(self):
        c = self._cache()
        c._pool_e_symbols = lambda: ["BTCUSDT"]
        c.crypto_prices, c.usdinr = {"BTCUSDT": 50000.0}, 88.0
        with mock.patch("data.fetch_crypto.fetch_crypto_last_prices", return_value={}), mock.patch("data.fetch_crypto.fetch_usdinr_rate", return_value=None):
            c.refresh_crypto(with_rate=True)
        self.assertEqual((c.crypto_prices, c.usdinr), ({"BTCUSDT": 50000.0}, 88.0))


if __name__ == "__main__":
    unittest.main()
