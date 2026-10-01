import unittest
from datetime import datetime

from dashboard.state_view import (
    markets_view, commodities_view, currencies_view, _nse_open, _us_market_open, _us_hours_ist,
    MARKETS, COMMODITIES, CURRENCIES,
)


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
        self.assertFalse(_us_market_open(datetime(2026, 10, 4, 20, 0)))   # Sunday IST evening -> Sunday ET afternoon

    def test_us_hours_ist_crosses_midnight_in_edt(self):
        # 9:30 ET = 19:00 IST same day; 16:00 ET = 01:30 IST the NEXT day (EDT, UTC-4).
        label = _us_hours_ist(datetime(2026, 10, 1, 10, 0))
        self.assertEqual(label, "19:00-01:30 (+1d) IST")

    def test_us_hours_ist_reflects_est_in_winter(self):
        # EST (UTC-5): 9:30 ET = 20:00 IST, 16:00 ET = 02:30 IST next day.
        label = _us_hours_ist(datetime(2026, 1, 15, 10, 0))
        self.assertEqual(label, "20:00-02:30 (+1d) IST")


class TestMarketsView(unittest.TestCase):
    def test_no_quotes_yet_returns_markets_with_nulls_not_an_error(self):
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
        us = next(m for m in result if m["id"] == "us")
        self.assertEqual(nse["price"], 22553.95)
        self.assertEqual(us["change_pct"], -0.25)

    def test_names_match_their_own_index_exactly(self):
        # Regression: the US card used to headline "NASDAQ/NYSE" while showing the
        # S&P 500 rate -- a genuine mismatch (S&P 500 isn't an index of either
        # exchange alone). Names must now be unambiguous about what's shown.
        us = next(m for m in MARKETS if m["id"] == "us")
        self.assertIn("S&P 500", us["name"])
        self.assertEqual(us["index_name"], "S&P 500")

    def test_no_capital_positions_or_pool_fields_on_a_market_card(self):
        result = markets_view({}, datetime(2026, 10, 1, 10, 0))
        for m in result:
            for key in ("capital", "deployed", "cash", "unrealised", "realised", "positions", "pools", "movers"):
                self.assertNotIn(key, m)

    def test_status_reflects_real_market_hours(self):
        result = markets_view({}, datetime(2026, 10, 1, 10, 0))   # NSE open, US closed, crypto always open
        by_id = {m["id"]: m["status"] for m in result}
        self.assertEqual(by_id["nse"], "Open")
        self.assertEqual(by_id["crypto"], "Open")
        self.assertEqual(by_id["us"], "Closed")

    def test_only_us_carries_an_ist_hours_label(self):
        result = markets_view({}, datetime(2026, 10, 1, 10, 0))
        by_id = {m["id"]: m["hours_ist"] for m in result}
        self.assertIsNone(by_id["nse"])
        self.assertIsNone(by_id["crypto"])
        self.assertIsNotNone(by_id["us"])


class TestCommoditiesAndCurrencies(unittest.TestCase):
    def test_commodities_view_passes_through_known_quotes(self):
        quotes = {"Gold": {"price": 4200.0, "change_pct": 0.5}, "Nifty 50": {"price": 22553.95}}
        result = commodities_view(quotes)
        self.assertEqual(len(result), len(COMMODITIES))
        gold = next(r for r in result if r["name"] == "Gold")
        self.assertEqual(gold["price"], 4200.0)
        self.assertEqual(gold["change_pct"], 0.5)
        self.assertIn("icon", gold)

    def test_currencies_view_passes_through_known_quotes(self):
        quotes = {"USD/INR": {"price": 95.97, "change_pct": -0.09}}
        result = currencies_view(quotes)
        self.assertEqual(len(result), len(CURRENCIES))
        usdinr = next(r for r in result if r["name"] == "USD/INR")
        self.assertEqual(usdinr["price"], 95.97)

    def test_missing_quotes_return_nulls_not_an_error(self):
        result = commodities_view(None)
        for r in result:
            self.assertIsNone(r["price"])


if __name__ == "__main__":
    unittest.main()
