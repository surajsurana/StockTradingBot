"""
Mock-based unit tests for run_daily.py's exclude_held_symbols() -- the guard
that stops the same symbol from being bought twice when run_daily.py runs
several times a day (see cron: 9:20am/11:10am/1:10pm/2:55pm). Run with:

    python test_run_daily.py
"""

import unittest

from execution.positions import Holding
from run_daily import exclude_held_symbols


class TestExcludeHeldSymbols(unittest.TestCase):
    def test_drops_symbols_already_held(self):
        symbols = ["RELIANCE.NS", "TCS.NS", "INFY.NS"]
        holdings = [Holding(symbol="TCS.NS", quantity=5, average_price=2000.0)]

        result = exclude_held_symbols(symbols, holdings)

        self.assertEqual(result, ["RELIANCE.NS", "INFY.NS"])

    def test_no_holdings_returns_all_symbols_unchanged(self):
        symbols = ["RELIANCE.NS", "TCS.NS"]
        self.assertEqual(exclude_held_symbols(symbols, []), symbols)

    def test_all_symbols_held_returns_empty(self):
        symbols = ["RELIANCE.NS", "TCS.NS"]
        holdings = [
            Holding(symbol="RELIANCE.NS", quantity=1, average_price=1000.0),
            Holding(symbol="TCS.NS", quantity=1, average_price=2000.0),
        ]
        self.assertEqual(exclude_held_symbols(symbols, holdings), [])

    def test_holding_not_in_universe_is_ignored(self):
        symbols = ["RELIANCE.NS", "TCS.NS"]
        holdings = [Holding(symbol="SOMEOTHER.NS", quantity=1, average_price=100.0)]
        self.assertEqual(exclude_held_symbols(symbols, holdings), symbols)


if __name__ == "__main__":
    unittest.main()
