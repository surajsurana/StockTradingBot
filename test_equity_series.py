"""
Tests for reporting/equity_series.py -- the day-by-day curve behind a strategy's Capital and Net
Profit figures.

    python test_equity_series.py
"""

import json
import os
import tempfile
import unittest

from reporting.equity_series import books_for, series_for


def _book(root: str, pool: str, key: str, rows: list) -> None:
    d = os.path.join(root, pool, key)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "daily_equity.jsonl"), "w", encoding="utf-8") as f:
        for date, equity in rows:
            f.write(json.dumps({"date": date, "cash": 0, "equity": equity}) + "\n")


class TestOneBook(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        _book(self.d, "paper_trading", "alpha", [("2026-10-01", 100.0), ("2026-10-02", 110.0)])

    def test_it_reads_the_engines_own_file(self):
        s = series_for(self.d, "alpha")
        self.assertEqual([p["date"] for p in s["points"]], ["2026-10-01", "2026-10-02"])
        self.assertEqual([p["equity"] for p in s["points"]], [100.0, 110.0])
        self.assertEqual(s["pools"], ["paper_trading"])

    def test_a_strategy_with_no_book_is_empty_not_an_error(self):
        """Clicking a strategy that has never traded is an ordinary thing to do."""
        s = series_for(self.d, "never_ran")
        self.assertEqual(s["points"], [])
        self.assertEqual(s["pools"], [])

    def test_a_blank_or_broken_line_is_skipped_not_fatal(self):
        path = os.path.join(self.d, "paper_trading", "alpha", "daily_equity.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n{not json\n" + json.dumps({"date": "2026-10-03"}) + "\n")
        self.assertEqual(len(series_for(self.d, "alpha")["points"]), 2)


class TestAStrategyThatIsSeveralBooks(unittest.TestCase):
    """The same key runs in Pool A and Pool F, and the row beside the chart shows their combined
    capital -- so the curve adds them up the same way."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        _book(self.d, "paper_trading", "alpha", [("2026-10-01", 100.0), ("2026-10-02", 110.0)])
        _book(self.d, "pool_f", "alpha", [("2026-10-01", 200.0), ("2026-10-02", 190.0)])

    def test_the_books_are_added_together(self):
        s = series_for(self.d, "alpha")
        self.assertEqual([p["equity"] for p in s["points"]], [300.0, 300.0])
        self.assertEqual(sorted(s["pools"]), ["paper_trading", "pool_f"])

    def test_a_day_only_one_book_has_carries_the_other_forward(self):
        """Dropping it would make the total dip on a day a book simply had no entry -- a fall that
        never happened, drawn as if it had."""
        _book(self.d, "pool_f", "alpha", [("2026-10-01", 200.0)])      # no 10-02 row
        s = series_for(self.d, "alpha")
        self.assertEqual([p["equity"] for p in s["points"]], [300.0, 310.0])

    def test_a_book_that_starts_later_does_not_count_before_it_existed(self):
        _book(self.d, "pool_f", "alpha", [("2026-10-02", 200.0)])
        s = series_for(self.d, "alpha")
        self.assertEqual([p["equity"] for p in s["points"]], [100.0, 310.0])


class TestDollarBooks(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        _book(self.d, "pool_i", "us_alpha", [("2026-10-01", 1000.0), ("2026-10-02", 1100.0)])

    def test_a_dollar_book_is_shown_in_rupees(self):
        s = series_for(self.d, "us_alpha", usdinr=95.0)
        self.assertEqual([p["equity"] for p in s["points"]], [95000.0, 104500.0])
        self.assertTrue(s["converted"])

    def test_with_no_rate_it_is_left_alone_rather_than_zeroed(self):
        s = series_for(self.d, "us_alpha", usdinr=0)
        self.assertEqual([p["equity"] for p in s["points"]], [1000.0, 1100.0])
        self.assertFalse(s["converted"])

    def test_a_rupee_book_is_never_converted(self):
        _book(self.d, "paper_trading", "alpha", [("2026-10-01", 100.0)])
        self.assertFalse(series_for(self.d, "alpha", usdinr=95.0)["converted"])


class TestItOnlyReads(unittest.TestCase):
    def test_it_writes_nothing_anywhere(self):
        import reporting.equity_series as mod
        with open(mod.__file__, encoding="utf-8") as f:
            code = " ".join(l.split("#")[0] for l in f.read().splitlines())
        for forbidden in ('open(path, "w"', "json.dump", "os.replace", "makedirs", "rmtree"):
            self.assertNotIn(forbidden, code, forbidden)

    def test_every_pool_that_keeps_books_is_looked_in(self):
        d = tempfile.mkdtemp()
        for pool in ("paper_trading", "pool_f", "pool_e", "pool_e1", "pool_i", "pool_d"):
            _book(d, pool, "alpha", [("2026-10-01", 1.0)])
        self.assertEqual(len(books_for(d, "alpha")), 6)


if __name__ == "__main__":
    unittest.main()
