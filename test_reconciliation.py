"""
deployment/reconciliation.py -- does the book match the exchange?

The live book is written by the strategy's own cycle as part of deciding: it records a position the
moment it decides to buy, before any order is placed. Anything that then fails leaves the book
believing it holds something it does not, the next run sizes against that belief, and it may try to
SELL a coin the account has never held. This is the check that stops one silent divergence
compounding.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime

from deployment.reconciliation import (DUST_QUANTITY, Reconciliation, load_book_positions, reconcile,
                                       reconcile_against_exchange)


class _Client:
    def __init__(self, rows=None, raises=None):
        self.rows = rows if rows is not None else []
        self.raises = raises

    def balances(self):
        if self.raises:
            raise self.raises
        return self.rows


def _book(path, positions):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cash": 50.0, "positions": {s: {"quantity": q} for s, q in positions.items()}}, f)
    return path


class TestTheComparison(unittest.TestCase):
    def test_matching_positions_are_ok(self):
        out = reconcile({"BTC": 0.000304}, {"BTC": 0.000304})
        self.assertTrue(out.ok)
        self.assertEqual(out.positions[0].verdict, "match")
        self.assertEqual(out.problems, [])

    def test_the_exchange_holding_LESS_is_a_problem_and_halts(self):
        # the book would try to sell what is not there, and every sizing decision downstream rests
        # on a position that does not exist
        out = reconcile({"BTC": 0.001}, {"BTC": 0.0002})
        self.assertFalse(out.ok)
        self.assertEqual(out.positions[0].verdict, "short")
        self.assertIn("not there", out.problems[0])

    def test_a_position_missing_entirely_from_the_exchange_halts(self):
        out = reconcile({"SOL": 0.16}, {})
        self.assertFalse(out.ok)
        self.assertEqual(out.positions[0].verdict, "short")

    def test_the_exchange_holding_MORE_is_noted_but_does_NOT_halt(self):
        # the account belongs to its owner: coins bought by hand, an airdrop, or a balance that
        # predates the bot are all normal, and halting on them means the bot stops because its owner
        # used their own account
        out = reconcile({"BTC": 0.001}, {"BTC": 0.001, "ETH": 2.0})
        self.assertTrue(out.ok)
        self.assertEqual(out.problems, [])
        self.assertTrue(any("did not buy" in n for n in out.notes))

    def test_an_oversized_position_is_extra_not_short(self):
        out = reconcile({"BTC": 0.001}, {"BTC": 0.005})
        self.assertTrue(out.ok)
        self.assertEqual(out.positions[0].verdict, "extra")

    def test_rounding_and_fee_in_kind_do_not_halt(self):
        # exchanges round, fees are taken in kind, quantities are stored to 6dp -- an exact check
        # would halt on arithmetic rather than on a problem
        out = reconcile({"BTC": 1.0}, {"BTC": 0.9975})        # 0.25% short
        self.assertTrue(out.ok)
        self.assertEqual(out.positions[0].verdict, "match")

    def test_a_real_shortfall_still_halts_despite_the_tolerance(self):
        out = reconcile({"BTC": 1.0}, {"BTC": 0.95})           # 5% short
        self.assertFalse(out.ok)

    def test_dust_is_never_worth_halting_over(self):
        out = reconcile({"BTC": DUST_QUANTITY / 2}, {})
        self.assertTrue(out.ok)

    def test_an_empty_book_against_an_empty_exchange_is_ok(self):
        out = reconcile({}, {})
        self.assertTrue(out.ok)
        self.assertEqual(out.positions, [])

    def test_the_difference_is_reported_signed(self):
        out = reconcile({"BTC": 1.0}, {"BTC": 0.4})
        self.assertAlmostEqual(out.positions[0].difference, -0.6, places=6)


class TestReadingTheBook(unittest.TestCase):
    def test_a_missing_book_is_no_positions(self):
        # correct for a strategy that has not traded yet
        self.assertEqual(load_book_positions(os.path.join(tempfile.mkdtemp(), "absent.json")), {})

    def test_an_unreadable_book_raises_rather_than_reading_as_empty(self):
        # "cannot read the book" must never be mistaken for "the book is empty" when the next step
        # is placing orders
        path = os.path.join(tempfile.mkdtemp(), "portfolio.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ truncated")
        with self.assertRaises(ValueError):
            load_book_positions(path)

    def test_symbols_are_normalised(self):
        path = _book(os.path.join(tempfile.mkdtemp(), "portfolio.json"), {"btc": 0.5})
        self.assertEqual(load_book_positions(path), {"BTC": 0.5})


class TestAgainstAnExchange(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "portfolio.json")

    def test_locked_balance_counts_as_held(self):
        # a coin committed to a resting sell order is still held; excluding it would read as a
        # shortfall the moment the book tries to exit a position
        _book(self.path, {"BTC": 1.0})
        client = _Client([{"currency": "BTC", "balance": 0.4, "locked_balance": 0.6}])
        self.assertTrue(reconcile_against_exchange(self.path, client).ok)

    def test_an_unreachable_exchange_is_NOT_ok(self):
        # not knowing whether the book is right is not the same as it being right, and the caller's
        # next step is placing real orders
        _book(self.path, {"BTC": 1.0})
        out = reconcile_against_exchange(self.path, _Client(raises=ConnectionError("down")))
        self.assertFalse(out.ok)
        self.assertIn("cannot be verified", out.problems[0])

    def test_an_unreadable_book_is_NOT_ok(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ truncated")
        out = reconcile_against_exchange(self.path, _Client([]))
        self.assertFalse(out.ok)
        self.assertIn("nothing can be verified", out.problems[0])

    def test_junk_balance_rows_are_skipped_not_fatal(self):
        _book(self.path, {"BTC": 1.0})
        client = _Client([{"currency": "BTC", "balance": "1.0", "locked_balance": "0"},
                          {"currency": None, "balance": "x"}, "not a dict"])
        self.assertTrue(reconcile_against_exchange(self.path, client).ok)

    def test_the_real_shape_from_coindcx_reconciles(self):
        # the exact rows the live account returns today
        _book(self.path, {})
        rows = [{"balance": 0.0, "locked_balance": 0.0, "currency": "BTC"},
                {"balance": 0.0, "locked_balance": 0.0, "currency": "USDT"},
                {"balance": 10000.0, "locked_balance": 0.0, "currency": "INR"}]
        out = reconcile_against_exchange(self.path, _Client(rows))
        self.assertTrue(out.ok)
        self.assertTrue(any("INR" in n for n in out.notes))      # the rupees are noted, not a fault


class TestItNeverRepairsAnything(unittest.TestCase):
    def test_the_book_is_not_rewritten_by_a_check(self):
        # silently rewriting a book to match an exchange would destroy the evidence of whatever went
        # wrong, and guessing which side is right is not a judgement to make about someone's money
        path = _book(os.path.join(tempfile.mkdtemp(), "portfolio.json"), {"BTC": 1.0})
        before = open(path, encoding="utf-8").read()
        reconcile_against_exchange(path, _Client([{"currency": "BTC", "balance": 0.0,
                                                   "locked_balance": 0.0}]))
        self.assertEqual(open(path, encoding="utf-8").read(), before)

    def test_the_module_contains_no_write(self):
        import ast
        import deployment.reconciliation as mod
        with open(mod.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        calls = [n.func.id for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertNotIn("write_json", calls)
        with open(mod.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn('"w"', source)          # nothing is opened for writing


if __name__ == "__main__":
    unittest.main()
