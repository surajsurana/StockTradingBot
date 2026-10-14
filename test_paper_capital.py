"""
Tests for deployment/paper_capital.py -- resizing a paper strategy's book.

    python test_paper_capital.py
"""

import json
import os
import tempfile
import unittest
from datetime import datetime

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.paper_capital import books_for, set_capital

NOW = datetime(2026, 10, 14, 11, 0)


def _record(key="alpha", status=DeploymentStatus.PAPER_TRADING):
    return StrategyRecord(strategy_key=key, display_name=key, strategy_family="fam",
                          strategy_id="SW-001", research_verdict=ResearchVerdict.PASS,
                          deployment_status=status)


def _write(root, rel, **fields):
    path = os.path.join(root, rel, "portfolio.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    book = {"cash": 100000.0, "starting_capital": 100000.0, "positions": {}}
    book.update(fields)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(book, f)
    return path


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestOneBook(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = _write(self.root, "paper_trading/alpha")

    def test_cash_and_the_base_move_together(self):
        """THE WHOLE POINT. Moving one alone invents a profit or a loss out of nothing."""
        r = set_capital(self.root, "alpha", 150000, {"Pool A": 100000.0}, record=_record(), now=NOW)
        self.assertTrue(r["ok"], r["error"])
        book = _read(self.path)
        self.assertAlmostEqual(book["cash"], 150000.0, places=2)
        self.assertAlmostEqual(book["starting_capital"], 150000.0, places=2)

    def test_it_moves_by_the_difference_from_what_the_page_shows(self):
        """A STALE starting_capital must not drag the book nine lakh the wrong way: PEAD's reads
        Rs10,00,000 for a Rs1,00,000 book, and the page shows the Rs1,00,000."""
        path = _write(self.root, "paper_trading/pead", cash=40000.0, starting_capital=1000000.0)
        r = set_capital(self.root, "pead", 150000, {"Pool A": 100000.0}, record=_record("pead"), now=NOW)
        self.assertTrue(r["ok"], r["error"])
        book = _read(path)
        self.assertAlmostEqual(book["cash"], 90000.0, places=2)      # +50,000, not -8,50,000
        self.assertAlmostEqual(book["starting_capital"], 1050000.0, places=2)

    def test_shrinking_below_the_open_positions_is_refused(self):
        _write(self.root, "paper_trading/alpha", cash=20000.0, starting_capital=100000.0,
               positions={"TCS": {"entry_price": 800.0, "quantity": 100}})
        r = set_capital(self.root, "alpha", 50000, {"Pool A": 100000.0}, record=_record(), now=NOW)
        self.assertFalse(r["ok"])
        self.assertIn("80,000", r["error"])
        self.assertAlmostEqual(_read(self.path)["cash"], 20000.0, places=2)   # untouched

    def test_shrinking_to_what_is_still_in_cash_is_allowed(self):
        r = set_capital(self.root, "alpha", 60000, {"Pool A": 100000.0}, record=_record(), now=NOW)
        self.assertTrue(r["ok"], r["error"])
        self.assertAlmostEqual(_read(self.path)["cash"], 60000.0, places=2)

    def test_the_change_is_written_into_the_book(self):
        """So a step in the equity curve has a reason attached."""
        set_capital(self.root, "alpha", 250000, {"Pool A": 100000.0}, record=_record(), now=NOW)
        changes = _read(self.path)["capital_changes"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["at"], "2026-10-14T11:00:00")
        self.assertAlmostEqual(changes[0]["by"], 150000.0, places=2)

    def test_a_second_change_is_appended_not_replaced(self):
        set_capital(self.root, "alpha", 150000, {"Pool A": 100000.0}, record=_record(), now=NOW)
        set_capital(self.root, "alpha", 120000, {"Pool A": 150000.0}, record=_record(), now=NOW)
        self.assertEqual(len(_read(self.path)["capital_changes"]), 2)
        self.assertAlmostEqual(_read(self.path)["cash"], 120000.0, places=2)


class TestTwoBooks(unittest.TestCase):
    """The same strategy in Pool A and Pool F: the row shows the pair combined, so the typed figure
    is the combined one."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.a = _write(self.root, "paper_trading/alpha")
        self.f = _write(self.root, "pool_f/alpha")

    def test_the_typed_total_is_split_evenly(self):
        r = set_capital(self.root, "alpha", 300000, {"Pool A": 100000.0, "Pool F": 100000.0},
                        record=_record(), now=NOW)
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual(r["books"], 2)
        self.assertAlmostEqual(_read(self.a)["cash"], 150000.0, places=2)
        self.assertAlmostEqual(_read(self.f)["cash"], 150000.0, places=2)

    def test_a_refusal_on_the_second_book_leaves_the_first_untouched(self):
        """All-or-nothing. A half-applied resize is a book nobody can reconcile."""
        _write(self.root, "pool_f/alpha", cash=1000.0, starting_capital=100000.0,
               positions={"TCS": {"entry_price": 990.0, "quantity": 100}})
        r = set_capital(self.root, "alpha", 60000, {"Pool A": 100000.0, "Pool F": 100000.0},
                        record=_record(), now=NOW)
        self.assertFalse(r["ok"])
        self.assertEqual(r["books"], 0)
        self.assertAlmostEqual(_read(self.a)["cash"], 100000.0, places=2)
        self.assertNotIn("capital_changes", _read(self.a))


class TestDollarBooks(unittest.TestCase):
    """Pool E/E1/I/G keep dollars; the row shows rupees."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = _write(self.root, "pool_e/crypto_tsmom", cash=1000.0, starting_capital=1000.0)

    def test_a_rupee_figure_is_converted_back_at_the_pages_own_rate(self):
        r = set_capital(self.root, "crypto_tsmom", 176000, {"Pool E": 88000.0}, usdinr=88.0,
                        record=_record("crypto_tsmom"), now=NOW)
        self.assertTrue(r["ok"], r["error"])
        self.assertAlmostEqual(_read(self.path)["cash"], 2000.0, places=2)      # $1,000 -> $2,000

    def test_without_a_rate_it_refuses_rather_than_sizing_in_the_wrong_currency(self):
        """Sizing a 1,000 USDT book at 1,76,000 USDT is the bug this prevents."""
        r = set_capital(self.root, "crypto_tsmom", 176000, {"Pool E": 88000.0}, usdinr=0,
                        record=_record("crypto_tsmom"), now=NOW)
        self.assertFalse(r["ok"])
        self.assertIn("dollars", r["error"])
        self.assertAlmostEqual(_read(self.path)["cash"], 1000.0, places=2)

    def test_the_rupees_actually_moved_are_reported(self):
        r = set_capital(self.root, "crypto_tsmom", 176000, {"Pool E": 88000.0}, usdinr=88.0,
                        record=_record("crypto_tsmom"), now=NOW)
        self.assertAlmostEqual(r["moved"], 88000.0, places=0)


class TestWhatItRefuses(unittest.TestCase):
    def test_a_live_strategy_is_never_resized_here(self):
        """Real money goes through deployment/live_allocations.py and its own checks. This must
        never become a second way in."""
        root = tempfile.mkdtemp()
        path = _write(root, "paper_trading/alpha")
        for status in (DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION):
            with self.subTest(status=status):
                r = set_capital(root, "alpha", 500000, {"Pool A": 100000.0},
                                record=_record(status=status), now=NOW)
                self.assertFalse(r["ok"])
                self.assertIn("Live mode", r["error"])
                self.assertAlmostEqual(_read(path)["cash"], 100000.0, places=2)

    def test_a_retired_or_research_strategy_is_refused(self):
        """A retired book is the record that retired it. Resizing it would rewrite that return."""
        root = tempfile.mkdtemp()
        path = _write(root, "paper_trading/alpha")
        for status in (DeploymentStatus.ARCHIVED, DeploymentStatus.RESEARCH):
            with self.subTest(status=status):
                r = set_capital(root, "alpha", 500000, {"Pool A": 100000.0},
                                record=_record(status=status), now=NOW)
                self.assertFalse(r["ok"])
                self.assertIn("not paper trading", r["error"])
                self.assertAlmostEqual(_read(path)["cash"], 100000.0, places=2)

    def test_a_negative_amount_is_refused(self):
        root = tempfile.mkdtemp()
        _write(root, "paper_trading/alpha")
        r = set_capital(root, "alpha", -1, {"Pool A": 100000.0}, record=_record(), now=NOW)
        self.assertFalse(r["ok"])
        self.assertIn("negative", r["error"])

    def test_nonsense_is_refused_without_raising(self):
        root = tempfile.mkdtemp()
        _write(root, "paper_trading/alpha")
        for bad in ("", "abc", None, {}):
            with self.subTest(bad=bad):
                r = set_capital(root, "alpha", bad, {"Pool A": 100000.0}, record=_record(), now=NOW)
                self.assertFalse(r["ok"])

    def test_a_strategy_with_no_book_is_refused_plainly(self):
        r = set_capital(tempfile.mkdtemp(), "nothing_here", 100000, {}, record=_record(), now=NOW)
        self.assertFalse(r["ok"])
        self.assertIn("no paper book", r["error"])

    def test_zero_is_allowed(self):
        """Standing a strategy down without archiving it is a real thing to want."""
        root = tempfile.mkdtemp()
        path = _write(root, "paper_trading/alpha")
        r = set_capital(root, "alpha", 0, {"Pool A": 100000.0}, record=_record(), now=NOW)
        self.assertTrue(r["ok"], r["error"])
        self.assertAlmostEqual(_read(path)["cash"], 0.0, places=2)


class TestWhichBooksItFinds(unittest.TestCase):
    def test_the_single_book_pools_are_found_at_their_own_root(self):
        root = tempfile.mkdtemp()
        for key, (sub, pool) in (("portfolio_b", ("portfolio_b", "Pool B")),
                                 ("portfolio_c", ("portfolio_c", "Pool C")),
                                 ("portfolio_g", ("pool_g", "Pool G")),
                                 ("pool_d_vwap_fade", ("pool_d", "Pool D"))):
            with self.subTest(key=key):
                _write(root, sub)
                found = books_for(root, key)
                self.assertEqual([b["pool"] for b in found], [pool])

    def test_a_us_suffixed_key_is_not_confused_with_its_india_original(self):
        """cross_sectional_momentum and cross_sectional_momentum_us are different strategies with
        different books, and a sloppy prefix match would resize both."""
        root = tempfile.mkdtemp()
        _write(root, "paper_trading/cross_sectional_momentum")
        _write(root, "pool_i/cross_sectional_momentum_us")
        self.assertEqual([b["pool"] for b in books_for(root, "cross_sectional_momentum")], ["Pool A"])
        self.assertEqual([b["pool"] for b in books_for(root, "cross_sectional_momentum_us")], ["Pool I"])

    def test_an_archived_book_is_not_resizable(self):
        """A retired book is a record, not something to top up."""
        root = tempfile.mkdtemp()
        _write(root, "archived/alpha")
        self.assertEqual(books_for(root, "alpha"), [])

    def test_the_dollar_pools_are_exactly_the_ones_the_charts_convert(self):
        """One list of foreign books, not two that can drift apart."""
        from deployment.paper_capital import USD_ROOTS
        from reporting.equity_series import USD_ROOTS as CHART_USD
        self.assertEqual(set(USD_ROOTS), set(CHART_USD))


if __name__ == "__main__":
    unittest.main()
