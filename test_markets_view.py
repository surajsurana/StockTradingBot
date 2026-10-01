import unittest
from datetime import datetime

from dashboard.state_view import markets_view, macro_view, _nse_open, _us_market_open, MARKETS


class TestMarketHours(unittest.TestCase):
    def test_nse_open_during_market_hours_on_a_weekday(self):
        self.assertTrue(_nse_open(datetime(2026, 10, 1, 10, 0)))   # Thursday, 10:00 IST

    def test_nse_closed_before_open(self):
        self.assertFalse(_nse_open(datetime(2026, 10, 1, 9, 0)))

    def test_nse_closed_on_saturday(self):
        self.assertFalse(_nse_open(datetime(2026, 10, 3, 10, 0)))   # Saturday

    def test_us_market_open_during_edt_session(self):
        # 2026-10-01 is in EDT (UTC-4): 9:30-16:00 ET = 19:00 IST - 01:30 IST next day.
        self.assertTrue(_us_market_open(datetime(2026, 10, 1, 20, 0)))   # ~13:30 ET

    def test_us_market_closed_in_the_ist_morning(self):
        self.assertFalse(_us_market_open(datetime(2026, 10, 1, 10, 0)))   # ~00:30 ET, after close

    def test_us_market_closed_on_saturday_ist(self):
        # IST Saturday morning is still Friday night ET in this window -- pick a point
        # unambiguously inside the US weekend instead.
        self.assertFalse(_us_market_open(datetime(2026, 10, 4, 20, 0)))   # Sunday IST evening -> Sunday ET afternoon


class TestMarketsView(unittest.TestCase):
    def test_empty_everything_returns_zeroed_markets_not_an_error(self):
        result = markets_view({}, {}, {}, {}, {}, {}, datetime(2026, 10, 1, 10, 0))
        self.assertEqual(len(result), len(MARKETS))
        for m in result:
            self.assertEqual(m["capital"], 0)
            self.assertEqual(m["positions"], 0)

    def test_nse_aggregates_pools_a_through_h_and_pool_d(self):
        pools = {"A": {"capital": 100.0, "deployed": 50.0, "cash": 50.0, "unrealised": 5.0, "realised": 2.0, "positions": 1},
                 "H": {"capital": 1000.0, "deployed": 900.0, "cash": 100.0, "unrealised": 50.0, "realised": 0.0, "positions": 3}}
        pool_d = {"capital": 10.0, "deployed": 5.0, "cash": 5.0, "unrealised": 1.0, "realised": 0.5, "positions": 1}
        result = markets_view(pools, pool_d, {}, {}, {}, {}, datetime(2026, 10, 1, 10, 0))
        nse = next(m for m in result if m["id"] == "nse")
        self.assertEqual(nse["capital"], 1110.0)
        self.assertEqual(nse["positions"], 5)
        self.assertEqual(nse["status"], "Open")

    def test_crypto_aggregates_pool_e_pool_e1_and_pool_g_with_their_own_rates(self):
        pool_e = {"inr": {"capital": 9000.0, "deployed": 4000.0, "cash": 5000.0,
                          "unbooked": {"raw": 100.0}, "booked": {"raw": 50.0}},
                 "usdt": {"positions": 2}}
        pool_e1 = {"inr": {"capital": 9000.0, "deployed": 0.0, "cash": 9000.0,
                           "unbooked": {"raw": 0.0}, "booked": {"raw": 0.0}},
                  "usdt": {"positions": 0}}
        pool_g = {"capital": 100.0, "deployed": 20.0, "cash": 80.0, "usdinr": 90.0,
                 "unbooked": {"raw": 2.0}, "booked": {"raw": 1.0}, "positions": 1}
        result = markets_view({}, {}, pool_e, pool_e1, pool_g, {}, datetime(2026, 10, 1, 10, 0))
        crypto = next(m for m in result if m["id"] == "crypto")
        self.assertEqual(crypto["capital"], 9000.0 + 9000.0 + 100.0 * 90.0)
        self.assertEqual(crypto["positions"], 3)
        self.assertEqual(crypto["status"], "Open")   # crypto is always open

    def test_us_equities_reads_pool_i_inr_and_usd_positions(self):
        pool_i = {"inr": {"capital": 90000.0, "deployed": 45000.0, "cash": 45000.0,
                          "unbooked": {"raw": 500.0}, "booked": {"raw": 900.0}},
                 "usd": {"positions": 1}}
        result = markets_view({}, {}, {}, {}, {}, pool_i, datetime(2026, 10, 1, 20, 0))   # inside US hours
        us = next(m for m in result if m["id"] == "us")
        self.assertEqual(us["capital"], 90000.0)
        self.assertEqual(us["positions"], 1)
        self.assertEqual(us["status"], "Open")

    def test_each_market_carries_its_pool_labels(self):
        result = markets_view({}, {}, {}, {}, {}, {}, datetime(2026, 10, 1, 10, 0))
        us = next(m for m in result if m["id"] == "us")
        self.assertEqual(us["pools"], ["Pool I"])
        nse = next(m for m in result if m["id"] == "nse")
        self.assertIn("Pool A", nse["pools"])
        self.assertIn("Pool H", nse["pools"])


class TestMacroView(unittest.TestCase):
    def test_none_returns_empty_list(self):
        self.assertEqual(macro_view(None), [])

    def test_passes_through_known_instruments_only(self):
        quotes = {"Gold": {"price": 4200.0, "change_pct": 0.5}, "Unknown Thing": {"price": 1.0}}
        result = macro_view(quotes)
        names = [r["name"] for r in result]
        self.assertIn("Gold", names)
        self.assertNotIn("Unknown Thing", names)
        gold = next(r for r in result if r["name"] == "Gold")
        self.assertEqual(gold["price"], 4200.0)
        self.assertEqual(gold["change_pct"], 0.5)


if __name__ == "__main__":
    unittest.main()
