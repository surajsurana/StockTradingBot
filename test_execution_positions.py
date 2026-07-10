"""
Mock-based unit tests for execution/positions.py's fetch_holdings(),
fetch_same_day_positions(), and fetch_all_holdings() -- run with:

    python test_execution_positions.py
"""

import unittest
from unittest.mock import patch, MagicMock

from execution.positions import fetch_holdings, fetch_same_day_positions, fetch_all_holdings, Holding


def _resp(status_code=200, json_data=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data or {}
    return m


class TestFetchHoldings(unittest.TestCase):
    @patch("execution.positions.requests.get")
    def test_last_price_captured_when_present(self, mock_get):
        mock_get.return_value = _resp(200, {"data": [
            {"tradingsymbol": "NTPC", "quantity": 15, "average_price": 344.1, "last_price": 345.6},
        ]})

        holdings = fetch_holdings("api_key", "token")

        self.assertEqual(holdings[0].last_price, 345.6)

    @patch("execution.positions.requests.get")
    def test_missing_last_price_defaults_to_none(self, mock_get):
        mock_get.return_value = _resp(200, {"data": [
            {"tradingsymbol": "NTPC", "quantity": 15, "average_price": 344.1},
        ]})

        holdings = fetch_holdings("api_key", "token")

        self.assertIsNone(holdings[0].last_price)

    @patch("execution.positions.requests.get")
    def test_success_maps_fields_and_appends_ns_suffix(self, mock_get):
        mock_get.return_value = _resp(200, {"data": [
            {"tradingsymbol": "INFY", "quantity": 5, "average_price": "1500.50"},
            {"tradingsymbol": "TCS", "quantity": 2, "average_price": 3200.0},
        ]})

        holdings = fetch_holdings("api_key", "token")

        self.assertEqual(holdings, [
            Holding(symbol="INFY.NS", quantity=5, average_price=1500.50),
            Holding(symbol="TCS.NS", quantity=2, average_price=3200.0),
        ])

    @patch("execution.positions.requests.get")
    def test_t1_quantity_counted_as_held(self, mock_get):
        # Regression test: a real T+1 holding (bought yesterday, not fully
        # settled yet) has quantity=0 and the real amount under
        # t1_quantity. Reading `quantity` alone made this look sold/closed
        # the day after a real buy, even though Kite's own GTT for it was
        # still active and the position was genuinely still held.
        mock_get.return_value = _resp(200, {"data": [
            {"tradingsymbol": "NTPC", "quantity": 0, "t1_quantity": 15, "average_price": 344.1},
        ]})

        holdings = fetch_holdings("api_key", "token")

        self.assertEqual(holdings, [Holding(symbol="NTPC.NS", quantity=15, average_price=344.1)])

    @patch("execution.positions.requests.get")
    def test_fully_settled_quantity_and_t1_quantity_not_double_counted(self, mock_get):
        # Once settlement completes, t1_quantity rolls into quantity and
        # goes to 0 -- adding them together must not double the real amount.
        mock_get.return_value = _resp(200, {"data": [
            {"tradingsymbol": "NTPC", "quantity": 15, "t1_quantity": 0, "average_price": 344.1},
        ]})

        holdings = fetch_holdings("api_key", "token")

        self.assertEqual(holdings[0].quantity, 15)

    @patch("execution.positions.requests.get")
    def test_zero_quantity_rows_filtered_out(self, mock_get):
        mock_get.return_value = _resp(200, {"data": [
            {"tradingsymbol": "INFY", "quantity": 0, "average_price": 1500.0},   # fully sold, still listed
            {"tradingsymbol": "TCS", "quantity": 2, "average_price": 3200.0},
        ]})

        holdings = fetch_holdings("api_key", "token")

        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0].symbol, "TCS.NS")

    @patch("execution.positions.requests.get")
    def test_empty_holdings(self, mock_get):
        mock_get.return_value = _resp(200, {"data": []})
        self.assertEqual(fetch_holdings("api_key", "token"), [])

    @patch("execution.positions.requests.get")
    def test_stale_token_raises_clearly(self, mock_get):
        mock_get.return_value = _resp(403, {"error_type": "TokenException", "message": "Invalid token"})
        with self.assertRaises(RuntimeError) as ctx:
            fetch_holdings("api_key", "stale_token")
        self.assertIn("stale", str(ctx.exception))


class TestFetchSameDayPositions(unittest.TestCase):
    @patch("execution.positions.requests.get")
    def test_filters_to_cnc_with_positive_quantity(self, mock_get):
        mock_get.return_value = _resp(200, {"data": {"net": [
            {"tradingsymbol": "NTPC", "product": "CNC", "quantity": 15, "average_price": 344.1},
            {"tradingsymbol": "RELIANCE", "product": "MIS", "quantity": 10, "average_price": 1300.0},
            {"tradingsymbol": "TCS", "product": "CNC", "quantity": 0, "average_price": 3200.0},
        ]}})

        positions = fetch_same_day_positions("api_key", "token")

        self.assertEqual(positions, [Holding(symbol="NTPC.NS", quantity=15, average_price=344.1)])

    @patch("execution.positions.requests.get")
    def test_empty_net_list(self, mock_get):
        mock_get.return_value = _resp(200, {"data": {"net": []}})
        self.assertEqual(fetch_same_day_positions("api_key", "token"), [])


class TestFetchAllHoldings(unittest.TestCase):
    @patch("execution.positions.fetch_same_day_positions")
    @patch("execution.positions.fetch_holdings")
    def test_merges_settled_and_same_day_by_symbol(self, mock_holdings, mock_same_day):
        # Regression test: a real production trade (NTPC.NS, bought same-day)
        # was missing from /portfolio/holdings until T+1 settlement -- calling
        # fetch_holdings() alone made monitor_positions.py conclude the
        # position had closed hours after a legitimate same-day BUY.
        mock_holdings.return_value = [Holding(symbol="INFY.NS", quantity=5, average_price=1500.0)]
        mock_same_day.return_value = [Holding(symbol="NTPC.NS", quantity=15, average_price=344.1)]

        merged = fetch_all_holdings("api_key", "token")

        self.assertEqual(len(merged), 2)
        symbols = {h.symbol for h in merged}
        self.assertEqual(symbols, {"INFY.NS", "NTPC.NS"})

    @patch("execution.positions.fetch_same_day_positions")
    @patch("execution.positions.fetch_holdings")
    def test_settled_holdings_take_priority_on_overlap(self, mock_holdings, mock_same_day):
        mock_holdings.return_value = [Holding(symbol="NTPC.NS", quantity=15, average_price=344.1)]
        mock_same_day.return_value = [Holding(symbol="NTPC.NS", quantity=999, average_price=1.0)]

        merged = fetch_all_holdings("api_key", "token")

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].quantity, 15)  # settled figure wins, not the same-day one

    @patch("execution.positions.fetch_same_day_positions")
    @patch("execution.positions.fetch_holdings")
    def test_no_same_day_positions_returns_holdings_unchanged(self, mock_holdings, mock_same_day):
        mock_holdings.return_value = [Holding(symbol="INFY.NS", quantity=5, average_price=1500.0)]
        mock_same_day.return_value = []

        merged = fetch_all_holdings("api_key", "token")

        self.assertEqual(merged, mock_holdings.return_value)


if __name__ == "__main__":
    unittest.main()
