"""
Mock-based unit tests for execution/positions.py's fetch_holdings() -- run with:

    python test_execution_positions.py
"""

import unittest
from unittest.mock import patch, MagicMock

from execution.positions import fetch_holdings, Holding


def _resp(status_code=200, json_data=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data or {}
    return m


class TestFetchHoldings(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
