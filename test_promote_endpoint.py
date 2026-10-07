"""
POST /api/live/promote -- the dashboard's promote-to-live button.

Exercised against a REAL server on a real socket, not by calling a function, because what is being
tested is partly the handler's own behaviour: that it re-derives the gate from disk instead of
believing the request, and that a refusal leaves the registry untouched.

This is the first of the four independent human acts that have to line up before a real order can
happen (promote, fund, LIVE_TRADING=True, no kill switch). It must not be a shortcut past any other.
"""
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import dashboard.server as srv
from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.live_guard import check_order_allowed
from deployment.pilot_live import PROMOTION_OVERRIDE_MARKER, promotion_override


def _record(key="alpha", status=DeploymentStatus.PAPER_TRADING, verdict=ResearchVerdict.PASS):
    return StrategyRecord(strategy_key=key, display_name=key.title(), strategy_family="fam",
                          research_verdict=verdict, deployment_status=status)


def _write_book(root, pool, key, trades, first_date="2026-01-01"):
    book = os.path.join(root, pool, key)
    os.makedirs(book, exist_ok=True)
    with open(os.path.join(book, "trades.jsonl"), "w", encoding="utf-8") as f:
        for _ in range(trades):
            f.write(json.dumps({"entry_date": first_date, "pnl": 1.0}) + "\n")
    with open(os.path.join(book, "daily_equity.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"date": first_date, "equity": 100_000}) + "\n")
    return book


class PromoteEndpointCase(unittest.TestCase):
    """Starts a real server, points it at a temp state dir, and records what it tried to change."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.records = []
        self.changes = []                       # every set_deployment_status the handler actually made

        self._prev_key = getattr(srv.DashboardHandler, "access_key", None)
        srv.DashboardHandler.access_key = ""    # auth is tested elsewhere; this is about the gate
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), srv.DashboardHandler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        def fake_set_status(key, status, reason="", **kw):
            self.changes.append({"key": key, "status": status, "reason": reason})
            return _record(key, status)

        self._patches = [
            patch.object(srv, "STATE_DIR", self.root),
            patch.object(srv, "list_strategies", side_effect=lambda *a, **k: list(self.records)),
            patch("deployment.deployment_manager.set_deployment_status", side_effect=fake_set_status),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        if self._prev_key is not None:
            srv.DashboardHandler.access_key = self._prev_key

    def post(self, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/live/promote",
                                     data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class TestPromotion(PromoteEndpointCase):
    def test_a_strategy_that_meets_the_gates_is_promoted(self):
        self.records = [_record()]
        _write_book(self.root, "paper_trading", "alpha", 25, "2026-01-01")
        status, body = self.post({"key": "alpha", "to": "PILOT_LIVE"})
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(len(self.changes), 1)
        self.assertEqual(self.changes[0]["status"], DeploymentStatus.PILOT_LIVE)

    def test_the_recorded_reason_states_the_evidence_and_what_was_judged_by_hand(self):
        # the audit trail has to survive the person who clicked forgetting why
        self.records = [_record()]
        _write_book(self.root, "paper_trading", "alpha", 25, "2026-01-01")
        self.post({"key": "alpha", "to": "PILOT_LIVE"})
        reason = self.changes[0]["reason"]
        for expected in ("25 closed trades", "verdict PASS", "LIVE_PROMOTION_CRITERIA.md", "judged by hand"):
            self.assertIn(expected, reason)

    def test_a_strategy_short_of_the_gates_is_refused_and_nothing_changes(self):
        self.records = [_record()]
        _write_book(self.root, "paper_trading", "alpha", 3, "2026-01-01")   # 3 trades, needs 20
        status, body = self.post({"key": "alpha", "to": "PILOT_LIVE"})
        self.assertEqual((status, body["ok"]), (400, False))
        self.assertIn("automated gates do not pass", body["error"])
        self.assertEqual(self.changes, [])

    def test_the_request_cannot_talk_its_own_way_past_the_gate(self):
        # the page computes the same gate to decide whether to draw the button, but a stale tab or a
        # hand-made request must not be able to assert its way through. Extra fields are ignored.
        self.records = [_record()]
        _write_book(self.root, "paper_trading", "alpha", 1, "2026-01-01")
        status, body = self.post({"key": "alpha", "to": "PILOT_LIVE", "eligible": True,
                                  "pilot": {"eligible": True, "state": "qualified"},
                                  "days": 999, "trades": 999, "force": True})
        self.assertEqual((status, body["ok"]), (400, False))
        self.assertEqual(self.changes, [])

    def test_a_rejected_verdict_is_refused_however_long_it_has_paper_traded(self):
        self.records = [_record(verdict=ResearchVerdict.REJECT)]
        _write_book(self.root, "paper_trading", "alpha", 400, "2024-01-01")
        status, body = self.post({"key": "alpha", "to": "PILOT_LIVE"})
        self.assertEqual((status, body["ok"]), (400, False))
        self.assertEqual(self.changes, [])

    def test_an_unknown_strategy_is_refused(self):
        self.records = []
        status, body = self.post({"key": "ghost", "to": "PILOT_LIVE"})
        self.assertEqual((status, body["ok"]), (400, False))
        self.assertIn("not in the deployment registry", body["error"])
        self.assertEqual(self.changes, [])

    def test_an_unrecognised_target_status_is_refused(self):
        self.records = [_record()]
        for target in ("PRODUCTION", "ARCHIVED", "RESEARCH", "", "pilot_live", "nonsense"):
            status, body = self.post({"key": "alpha", "to": target})
            self.assertEqual(status, 400, target)
        self.assertEqual(self.changes, [])      # PRODUCTION included: that is not this button's job

    def test_a_missing_or_malformed_body_is_refused_not_a_crash(self):
        self.records = [_record()]
        for body_in in ({}, {"key": "alpha"}, {"to": "PILOT_LIVE"}, {"key": None, "to": None}):
            status, body = self.post(body_in)
            self.assertEqual(status, 400, body_in)
            self.assertFalse(body["ok"])
        self.assertEqual(self.changes, [])


class TestDemotion(PromoteEndpointCase):
    def test_a_flat_live_strategy_can_be_returned_to_paper(self):
        self.records = [_record(status=DeploymentStatus.PILOT_LIVE)]
        status, body = self.post({"key": "alpha", "to": "PAPER_TRADING"})
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(self.changes[0]["status"], DeploymentStatus.PAPER_TRADING)

    def test_demotion_does_not_require_the_paper_gate_to_pass(self):
        # a live strategy's gate reads "not qualified" precisely BECAUSE it is already live; requiring
        # it here would make the way out depend on the way in, and strand it
        self.records = [_record(status=DeploymentStatus.PILOT_LIVE)]
        status, _ = self.post({"key": "alpha", "to": "PAPER_TRADING"})
        self.assertEqual(status, 200)

    def test_demotion_is_refused_while_the_strategy_holds_live_positions(self):
        # the live runner skips anything not PILOT_LIVE, so demoting now would leave real positions
        # with nothing to exit them -- an orphaned position is worse than a strategy left running
        self.records = [_record(status=DeploymentStatus.PILOT_LIVE)]
        book = os.path.join(self.root, "live", "paper_trading", "alpha")
        os.makedirs(book)
        with open(os.path.join(book, "portfolio.json"), "w", encoding="utf-8") as f:
            json.dump({"cash": 1000, "positions": {"RELIANCE.NS": {"quantity": 5}}}, f)
        status, body = self.post({"key": "alpha", "to": "PAPER_TRADING"})
        self.assertEqual((status, body["ok"]), (400, False))
        self.assertIn("holds 1 live position", body["error"])
        self.assertIn("LIVE_TRADING_HALTED", body["error"])     # points at the right tool instead
        self.assertEqual(self.changes, [])

    def test_an_unreadable_live_book_also_refuses_demotion(self):
        self.records = [_record(status=DeploymentStatus.PILOT_LIVE)]
        book = os.path.join(self.root, "live", "paper_trading", "alpha")
        os.makedirs(book)
        with open(os.path.join(book, "portfolio.json"), "w", encoding="utf-8") as f:
            f.write("{ truncated")
        status, body = self.post({"key": "alpha", "to": "PAPER_TRADING"})
        self.assertEqual((status, body["ok"]), (400, False))    # fails closed, not open
        self.assertEqual(self.changes, [])


class TestItPlacesNothing(unittest.TestCase):
    def test_promoting_cannot_place_an_order_by_itself(self):
        # structural: the endpoint changes a registry field. Real orders additionally need capital
        # assigned, LIVE_TRADING True and no kill switch -- three separate human acts.
        with open(srv.__file__, encoding="utf-8") as f:
            source = f.read()
        block = source[source.index('/api/live/promote'):source.index('/api/research/start')]
        # comments stripped: the block explains in prose what it deliberately does NOT do, and a
        # structural check that matched its own comments would pass or fail on the wording
        code = " ".join(line.split("#")[0] for line in block.splitlines())
        for forbidden in ("place_order", "place_live_order", "kiteconnect", "LIVE_TRADING = True",
                          "set_allocation", "force=True", "force = True"):
            self.assertNotIn(forbidden, code, forbidden)


if __name__ == "__main__":
    unittest.main()


class TestManualOverride(PromoteEndpointCase):
    """Promoting past the gates on purpose. Suraj asked for this: the gates are a floor he set, and
    he keeps the right to go past his own floor. What is not optional is writing down what was
    skipped."""

    def test_override_promotes_a_strategy_that_fails_the_gates(self):
        self.records = [_record()]
        _write_book(self.root, "paper_trading", "alpha", 2, "2026-09-25")
        status, body = self.post({"key": "alpha", "to": "PILOT_LIVE", "override": True})
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertEqual(self.changes[0]["status"], DeploymentStatus.PILOT_LIVE)

    def test_the_override_records_the_marker_and_the_exact_gates_it_skipped(self):
        self.records = [_record()]
        _write_book(self.root, "paper_trading", "alpha", 2, "2026-09-25")
        self.post({"key": "alpha", "to": "PILOT_LIVE", "override": True})
        reason = self.changes[0]["reason"]
        self.assertIn(PROMOTION_OVERRIDE_MARKER, reason)
        self.assertIn("Gates NOT met", reason)
        self.assertIn("2 closed trades", reason)          # the evidence as it actually stood
        self.assertIn("20", reason)                        # the trade floor it fell short of

    def test_a_qualified_strategy_is_not_marked_as_an_override(self):
        # passing override:true on a strategy that passes anyway must not stain its record
        self.records = [_record()]
        _write_book(self.root, "paper_trading", "alpha", 25, "2026-01-01")
        self.post({"key": "alpha", "to": "PILOT_LIVE", "override": True})
        self.assertNotIn(PROMOTION_OVERRIDE_MARKER, self.changes[0]["reason"])

    def test_without_the_override_flag_a_failing_strategy_is_still_refused(self):
        self.records = [_record()]
        _write_book(self.root, "paper_trading", "alpha", 2, "2026-09-25")
        for payload in ({"key": "alpha", "to": "PILOT_LIVE"},
                        {"key": "alpha", "to": "PILOT_LIVE", "override": False},
                        {"key": "alpha", "to": "PILOT_LIVE", "override": "yes"},    # not the boolean
                        {"key": "alpha", "to": "PILOT_LIVE", "override": 1}):
            status, _ = self.post(payload)
            self.assertEqual(status, 400, payload)
        self.assertEqual(self.changes, [])


class TestTheOverrideSurvivesToOrderTime(unittest.TestCase):
    """An override that promotes and is then ignored by the per-order guard would be the worst
    outcome: the strategy would look live and silently do nothing."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.settings = SimpleNamespace(LIVE_TRADING=True, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t")
        self.failing = SimpleNamespace(eligible=False, reasons=["Only 2 paper trades recorded."])

    def _record_with(self, reason):
        r = _record("alpha", DeploymentStatus.PILOT_LIVE)
        r.deployment_status_history = [{"from_status": "paper_trading", "to_status": "pilot_live",
                                        "timestamp": 1760000000.0, "reason": reason}]
        return r

    def _check(self, record):
        # An explicit in-hours timestamp: these tests are about the promotion override, and since
        # 2026-10-07 the guard also refuses equity orders outside 09:15-15:30, so leaving the clock
        # to chance would make them pass or fail depending on what time the suite was run.
        from datetime import datetime
        return check_order_allowed(settings=self.settings, record=record, state_dir=self.d,
                                   order_value_rupees=1000.0, current_live_exposure_rupees=0.0,
                                   orders_placed_today=0, eligibility=self.failing,
                                   now=datetime(2026, 10, 7, 11, 0))

    def test_an_overridden_strategy_may_still_place_orders(self):
        decision = self._check(self._record_with(PROMOTION_OVERRIDE_MARKER + " promoted anyway"))
        self.assertTrue(decision.allowed, decision.reasons)

    def test_the_bypass_is_recorded_on_the_decision_never_silent(self):
        decision = self._check(self._record_with(PROMOTION_OVERRIDE_MARKER + " promoted anyway"))
        self.assertTrue(decision.overrides)
        self.assertIn("Only 2 paper trades recorded.", " ".join(decision.overrides))

    def test_a_normally_promoted_strategy_is_still_stopped_when_it_drifts_out(self):
        decision = self._check(self._record_with("Promoted to pilot live from the dashboard."))
        self.assertFalse(decision.allowed)
        self.assertTrue(any("no longer passes the pilot gates" in r for r in decision.reasons))

    def test_the_override_suppresses_only_the_gate_check(self):
        # everything else still applies -- this is a bypass of one check, not of the guard
        record = self._record_with(PROMOTION_OVERRIDE_MARKER + " promoted anyway")
        off = SimpleNamespace(LIVE_TRADING=False, KITE_API_KEY="k", KITE_ACCESS_TOKEN="t")
        self.assertFalse(check_order_allowed(settings=off, record=record, state_dir=self.d,
                                             order_value_rupees=1000.0, current_live_exposure_rupees=0.0,
                                             orders_placed_today=0, eligibility=self.failing).allowed)
        huge = check_order_allowed(settings=self.settings, record=record, state_dir=self.d,
                                   order_value_rupees=9_000_000.0, current_live_exposure_rupees=0.0,
                                   orders_placed_today=0, eligibility=self.failing)
        self.assertFalse(huge.allowed)
        self.assertTrue(any("per-order cap" in r for r in huge.reasons))

    def test_a_later_clean_promotion_clears_an_earlier_override(self):
        r = _record("alpha", DeploymentStatus.PILOT_LIVE)
        r.deployment_status_history = [
            {"to_status": "pilot_live", "timestamp": 1.0, "reason": PROMOTION_OVERRIDE_MARKER + " x"},
            {"to_status": "paper_trading", "timestamp": 2.0, "reason": "returned to paper"},
            {"to_status": "pilot_live", "timestamp": 3.0, "reason": "promoted through the gates"}]
        self.assertIsNone(promotion_override(r))
        self.assertFalse(self._check(r).allowed)

    def test_no_history_at_all_is_not_an_override(self):
        r = _record("alpha", DeploymentStatus.PILOT_LIVE)
        r.deployment_status_history = []
        self.assertIsNone(promotion_override(r))
        self.assertFalse(self._check(r).allowed)
