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
    def test_no_quotes_yet_returns_markets_with_null_prices_not_an_error(self):
        result = markets_view(None, datetime(2026, 10, 1, 10, 0))
        self.assertEqual(len(result), len(MARKETS))
        for m in result:
            self.assertIsNone(m["price"])
            self.assertIsNone(m["change_pct"])

    def test_each_market_shows_its_own_representative_rate(self):
        quotes = {"Nifty 50": {"price": 22553.95, "change_pct": -0.29},
                 "Bitcoin": {"price": 84260.62, "change_pct": 0.76},
                 "S&P 500": {"price": 7651.54, "change_pct": -0.25}}
        result = markets_view(quotes, datetime(2026, 10, 1, 10, 0))
        nse = next(m for m in result if m["id"] == "nse")
        crypto = next(m for m in result if m["id"] == "crypto")
        us = next(m for m in result if m["id"] == "us")
        self.assertEqual(nse["price"], 22553.95)
        self.assertEqual(nse["index_name"], "Nifty 50")
        self.assertEqual(crypto["price"], 84260.62)
        self.assertEqual(us["change_pct"], -0.25)

    def test_no_capital_or_position_fields_on_a_market_card(self):
        # Explicit follow-up direction: markets show rates, not our own capital/P&L there.
        result = markets_view({}, datetime(2026, 10, 1, 10, 0))
        for m in result:
            for key in ("capital", "deployed", "cash", "unrealised", "realised", "positions"):
                self.assertNotIn(key, m)

    def test_status_reflects_real_market_hours(self):
        result = markets_view({}, datetime(2026, 10, 1, 10, 0))   # NSE open, US closed, crypto always open
        by_id = {m["id"]: m["status"] for m in result}
        self.assertEqual(by_id["nse"], "Open")
        self.assertEqual(by_id["crypto"], "Open")
        self.assertEqual(by_id["us"], "Closed")

    def test_each_market_still_carries_its_pool_labels(self):
        result = markets_view({}, datetime(2026, 10, 1, 10, 0))
        us = next(m for m in result if m["id"] == "us")
        self.assertEqual(us["pools"], ["Pool I"])
        nse = next(m for m in result if m["id"] == "nse")
        self.assertIn("Pool A", nse["pools"])
        self.assertIn("Pool H", nse["pools"])


class TestMacroView(unittest.TestCase):
    def test_none_returns_empty_list(self):
        self.assertEqual(macro_view(None), [])

    def test_passes_through_known_instruments_only(self):
        quotes = {"Gold": {"price": 4200.0, "change_pct": 0.5}, "Nifty 50": {"price": 22553.95}}
        result = macro_view(quotes)
        names = [r["name"] for r in result]
        self.assertIn("Gold", names)
        self.assertNotIn("Nifty 50", names)   # that's a market-card rate, not a macro instrument
        gold = next(r for r in result if r["name"] == "Gold")
        self.assertEqual(gold["price"], 4200.0)
        self.assertEqual(gold["change_pct"], 0.5)


if __name__ == "__main__":
    unittest.main()
