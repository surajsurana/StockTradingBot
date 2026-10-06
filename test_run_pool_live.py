"""
run_pool_live.py -- the live runner (Gate H step 1).

The properties under test: it places nothing, it refuses anything not both promoted and funded, and
it never writes to the paper book -- whose track record is the control the live book is compared
against, and which a stray write would silently corrupt.
"""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import deployment.paper_trading_engine as pte
import run_pool_live as rpl
from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.live_allocations import set_allocation


def _record(key="alpha", status=DeploymentStatus.PILOT_LIVE):
    return StrategyRecord(strategy_key=key, display_name=key, strategy_family="fam",
                          research_verdict=ResearchVerdict.PASS, deployment_status=status)


class TestEligibility(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_promoted_but_unfunded_strategy_is_skipped_not_an_error(self):
        # promotion without funding is a deliberate, safe intermediate state: it sizes to zero
        with patch.object(rpl, "list_strategies", return_value=[_record()]):
            self.assertEqual(rpl.eligible_strategies(self.d), [])

    def test_a_funded_but_unpromoted_strategy_is_skipped(self):
        set_allocation(self.d, "alpha", 50_000, available_balance=100_000)
        with patch.object(rpl, "list_strategies",
                          return_value=[_record(status=DeploymentStatus.PAPER_TRADING)]):
            self.assertEqual(rpl.eligible_strategies(self.d), [])

    def test_promoted_and_funded_is_eligible(self):
        set_allocation(self.d, "alpha", 50_000, available_balance=100_000)
        with patch.object(rpl, "list_strategies", return_value=[_record()]):
            got = rpl.eligible_strategies(self.d)
        self.assertEqual([(r.strategy_key, a) for r, a in got], [("alpha", 50_000.0)])


class TestRunLiveRefusals(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _run(self, records):
        with patch.object(rpl, "list_strategies", return_value=records):
            return rpl.run_live("alpha", fetch_data_fn=lambda: {}, state_dir=self.d)

    def test_unknown_strategy_refused(self):
        r = self._run([])
        self.assertEqual(r["status"], "refused")
        self.assertIn("not in the registry", r["reason"])

    def test_paper_trading_strategy_refused(self):
        r = self._run([_record(status=DeploymentStatus.PAPER_TRADING)])
        self.assertEqual(r["status"], "refused")
        self.assertIn("PAPER_TRADING", r["reason"])

    def test_unfunded_strategy_refused(self):
        r = self._run([_record()])
        self.assertEqual(r["status"], "refused")
        self.assertIn("no live capital", r["reason"])

    def test_a_refusal_runs_no_engine_and_writes_nothing(self):
        with patch.object(pte, "run_daily") as engine:
            self._run([_record(status=DeploymentStatus.RESEARCH)])
        engine.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.d, "live")))


class TestItNeverTouchesThePaperBook(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        set_allocation(self.d, "alpha", 50_000, available_balance=100_000)
        self.spec = SimpleNamespace(strategy_key="alpha", strategy_factory=lambda: object(),
                                    compute_extra_columns_fn=None)

    def test_the_engine_is_pointed_at_the_live_book_and_restored_afterwards(self):
        before = pte.PAPER_TRADING_STATE_DIR
        seen = {}

        def fake_run_daily(*a, **k):
            seen["dir"] = pte.PAPER_TRADING_STATE_DIR      # where the engine would have written
            return {"status": "processed"}

        with patch.object(rpl, "list_strategies", return_value=[_record()]), \
             patch.dict(rpl._SPECS_BY_KEY, {"alpha": self.spec}), \
             patch.object(pte, "run_daily", side_effect=fake_run_daily):
            rpl.run_live("alpha", fetch_data_fn=lambda: {}, state_dir=self.d)

        self.assertEqual(seen["dir"], os.path.join(self.d, "live", "paper_trading"))
        self.assertEqual(pte.PAPER_TRADING_STATE_DIR, before)       # restored

    def test_the_redirection_is_restored_even_when_the_engine_raises(self):
        # a leaked global would send the NEXT pool's paper run into the live book
        before = pte.PAPER_TRADING_STATE_DIR
        with patch.object(rpl, "list_strategies", return_value=[_record()]), \
             patch.dict(rpl._SPECS_BY_KEY, {"alpha": self.spec}), \
             patch.object(pte, "run_daily", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                rpl.run_live("alpha", fetch_data_fn=lambda: {}, state_dir=self.d)
        self.assertEqual(pte.PAPER_TRADING_STATE_DIR, before)

    def test_the_live_book_is_created_funded_with_the_assigned_capital(self):
        with patch.object(rpl, "list_strategies", return_value=[_record()]), \
             patch.dict(rpl._SPECS_BY_KEY, {"alpha": self.spec}), \
             patch.object(pte, "run_daily", return_value={"status": "processed"}):
            rpl.run_live("alpha", fetch_data_fn=lambda: {}, state_dir=self.d)
        with open(os.path.join(self.d, "live", "paper_trading", "alpha", "portfolio.json")) as f:
            book = json.load(f)
        self.assertEqual((book["starting_capital"], book["cash"]), (50_000.0, 50_000.0))

    def test_an_existing_live_book_is_never_reset(self):
        # its cash is the real, already-traded balance; overwriting it would rewrite the live record
        path = os.path.join(self.d, "live", "paper_trading", "alpha", "portfolio.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"starting_capital": 50_000, "cash": 1234.5, "positions": {"X": {}}}, f)
        with patch.object(rpl, "list_strategies", return_value=[_record()]), \
             patch.dict(rpl._SPECS_BY_KEY, {"alpha": self.spec}), \
             patch.object(pte, "run_daily", return_value={"status": "processed"}):
            rpl.run_live("alpha", fetch_data_fn=lambda: {}, state_dir=self.d)
        with open(path) as f:
            self.assertEqual(json.load(f)["cash"], 1234.5)


class TestIntendedOrders(unittest.TestCase):
    def test_filled_and_queued_decisions_both_count_as_orders(self):
        orders = rpl.intended_orders({
            "new_entries": [{"symbol": "A", "quantity": 3, "entry_price": 100.0}],
            "new_exits": [{"symbol": "B", "quantity": 1, "exit_price": 250.0}],
            "new_pending_entries": [{"symbol": "C", "quantity": 7}],
            "new_pending_exits": [{"symbol": "D", "quantity": 2}],
        })
        self.assertEqual([(o["side"], o["symbol"], o["quantity"]) for o in orders],
                         [("BUY", "A", 3), ("SELL", "B", 1), ("BUY", "C", 7), ("SELL", "D", 2)])
        self.assertEqual([o["when"] for o in orders][2:], ["next open", "next open"])

    def test_an_empty_result_is_no_orders(self):
        self.assertEqual(rpl.intended_orders({}), [])


class TestItCannotPlaceAnOrder(unittest.TestCase):
    def test_the_module_contains_no_broker_call(self):
        # structural: step 1 reports intent only. Sending to Kite is a separate, later change.
        with open(rpl.__file__, encoding="utf-8") as f:
            source = f.read()
        for forbidden in ("import requests", "kiteconnect", "place_order",
                          "from execution", "import execution"):
            self.assertNotIn(forbidden, source, forbidden)


if __name__ == "__main__":
    unittest.main()
