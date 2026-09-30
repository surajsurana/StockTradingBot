import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from fundamentals.fundamental_agent import check_health, fetch_fundamentals


class TestRoeFallback(unittest.TestCase):
    """yfinance's pre-computed returnOnEquity is frequently None for NSE
    stocks (confirmed for RELIANCE.NS, VEDL.NS, RVNL.NS, IRCTC.NS). These
    tests cover the Net Income / Stockholders Equity fallback added for
    that gap, so Pool B stops auto-failing stocks it has no real reason
    to exclude."""

    def _mock_ticker(self, info, financials=None, balance_sheet=None):
        ticker = MagicMock()
        ticker.info = info
        ticker.financials = financials
        ticker.balance_sheet = balance_sheet
        return ticker

    def test_uses_direct_field_when_present_no_fallback_needed(self):
        info = {"trailingEps": 10.0, "returnOnEquity": 0.25}
        ticker = self._mock_ticker(info)
        with patch("fundamentals.fundamental_agent.yf.Ticker", return_value=ticker):
            metrics = fetch_fundamentals("TCS.NS")
        self.assertEqual(metrics["returnOnEquity"], 0.25)

    def test_falls_back_to_statements_when_direct_field_missing(self):
        info = {"trailingEps": 123.7, "debtToEquity": 25.632, "revenueGrowth": 0.255}
        financials = pd.DataFrame({"2025": [1000.0]}, index=["Net Income"])
        balance_sheet = pd.DataFrame({"2025": [10000.0]}, index=["Stockholders Equity"])
        ticker = self._mock_ticker(info, financials, balance_sheet)
        with patch("fundamentals.fundamental_agent.yf.Ticker", return_value=ticker):
            metrics = fetch_fundamentals("POWERMECH.NS")
        self.assertAlmostEqual(metrics["returnOnEquity"], 0.10)

    def test_tries_alternate_row_names(self):
        info = {}
        financials = pd.DataFrame({"2025": [500.0]}, index=["Net Income Common Stockholders"])
        balance_sheet = pd.DataFrame({"2025": [2500.0]}, index=["Common Stock Equity"])
        ticker = self._mock_ticker(info, financials, balance_sheet)
        with patch("fundamentals.fundamental_agent.yf.Ticker", return_value=ticker):
            metrics = fetch_fundamentals("SOME.NS")
        self.assertAlmostEqual(metrics["returnOnEquity"], 0.20)

    def test_no_fallback_value_when_statements_also_unavailable(self):
        info = {"trailingEps": 10.0}
        ticker = self._mock_ticker(info, financials=None, balance_sheet=None)
        with patch("fundamentals.fundamental_agent.yf.Ticker", return_value=ticker):
            metrics = fetch_fundamentals("OBSCURE.NS")
        self.assertNotIn("returnOnEquity", metrics)

    def test_zero_equity_does_not_raise_or_produce_a_value(self):
        info = {}
        financials = pd.DataFrame({"2025": [100.0]}, index=["Net Income"])
        balance_sheet = pd.DataFrame({"2025": [0.0]}, index=["Stockholders Equity"])
        ticker = self._mock_ticker(info, financials, balance_sheet)
        with patch("fundamentals.fundamental_agent.yf.Ticker", return_value=ticker):
            metrics = fetch_fundamentals("ZEROEQ.NS")
        self.assertNotIn("returnOnEquity", metrics)

    def test_fallback_roe_flows_through_to_a_passing_health_check(self):
        criteria = {"max_debt_to_equity": 150, "min_roe": 0.10, "min_revenue_growth": -0.10}
        info = {"trailingEps": 123.7, "debtToEquity": 25.632, "revenueGrowth": 0.255}
        financials = pd.DataFrame({"2025": [1445.0]}, index=["Net Income"])
        balance_sheet = pd.DataFrame({"2025": [10000.0]}, index=["Stockholders Equity"])
        ticker = self._mock_ticker(info, financials, balance_sheet)
        with patch("fundamentals.fundamental_agent.yf.Ticker", return_value=ticker):
            metrics = fetch_fundamentals("POWERMECH.NS")
        result = check_health("POWERMECH.NS", metrics, criteria)
        self.assertTrue(result.passed)


if __name__ == "__main__":
    unittest.main()
