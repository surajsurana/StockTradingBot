"""
Tests for deployment/broker_auth_watch.py -- the alert when a live broker stops accepting our key
(added 2026-10-29 after CoinDCX expired the key and Pool G halted with nobody told).
"""

import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from deployment import broker_auth_watch as w

# The exact text the 29 Oct 08:35 run logged.
EXPIRED = ("The book and the exchange disagree, so the strategy did not run. Could not read the "
           "exchange balances, so the book cannot be verified: CoinDCXError: CoinDCX "
           "/exchange/v1/users/balances returned HTTP 422: {\"code\":422,\"message\":\"API key "
           "expired. Please generate new keys.\",\"status\":\"error\"}")


class _TempState(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sent = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def rec(self, venue, ok, detail="", hour=8):
        return w.record(venue, ok, detail, self.dir, source="test",
                        now=datetime(2026, 10, 29, hour, 5), send=self.sent.append)


class TestIsAuthFailure(unittest.TestCase):
    def test_recognises_every_way_a_refused_key_shows_up(self):
        for text in (EXPIRED,
                     'CoinDCX /exchange/v1/users/balances returned HTTP 401: {"code":401,"message":"Invalid credentials"}',
                     "TokenException: Incorrect `api_key` or `access_token`.",
                     "The Kite session could not be refreshed, so no equity order can be placed.",
                     "coindcx credentials are not configured, so portfolio_g cannot trade."):
            with self.subTest(text=text[:40]):
                self.assertTrue(w.is_auth_failure(text))

    def test_other_failures_are_not_key_failures(self):
        for text in ("The book holds 0.02 BNB but the exchange reports 0.019",
                     "ConnectionError: Max retries exceeded", "HTTP 500: internal error",
                     "Rs10,000 is assigned but the account holds Rs7,781", ""):
            with self.subTest(text=text[:40]):
                self.assertFalse(w.is_auth_failure(text))


class TestRecord(_TempState):
    def test_first_failure_alerts_once_and_repeats_stay_quiet(self):
        msg = self.rec("coindcx", False, "API key expired")
        self.assertIn("CoinDCX", msg)
        self.assertIn("paused", msg)
        self.assertIn("168.144.66.161", msg)
        self.assertIsNone(self.rec("coindcx", False, "API key expired", hour=20))
        self.assertEqual(len(self.sent), 1)

    def test_recovery_sends_the_all_clear_once(self):
        self.rec("coindcx", False, "expired")
        msg = self.rec("coindcx", True, hour=20)
        self.assertIn("working again", msg)
        self.assertIsNone(self.rec("coindcx", True, hour=21))
        self.assertEqual(len(self.sent), 2)

    def test_a_first_ever_success_is_silent(self):
        self.assertIsNone(self.rec("kite", True))
        self.assertEqual(self.sent, [])

    def test_could_not_tell_changes_nothing(self):
        self.rec("coindcx", False, "expired")
        self.assertIsNone(self.rec("coindcx", None, "ConnectionError"))
        self.assertFalse(w.load(self.dir)["venues"]["coindcx"]["ok"])

    def test_venues_are_independent(self):
        self.rec("coindcx", False, "expired")
        self.assertIsNotNone(self.rec("kite", False, "TokenException"))
        self.assertEqual(len(self.sent), 2)

    def test_a_failed_send_still_records_the_state(self):
        def boom(_):
            raise RuntimeError("telegram down")
        w.record("coindcx", False, "expired", self.dir, source="test", send=boom)
        self.assertFalse(w.load(self.dir)["venues"]["coindcx"]["ok"])

    def test_since_marks_when_the_state_began(self):
        self.rec("coindcx", False, "expired", hour=8)
        self.rec("coindcx", False, "expired", hour=20)
        self.assertEqual(w.load(self.dir)["venues"]["coindcx"]["since"], "2026-10-29T08:05:00")

    def test_events_feed_the_notifications_timeline(self):
        self.rec("coindcx", False, "expired", hour=8)
        self.rec("coindcx", True, hour=20)
        rows = w.timeline_rows(self.dir)
        self.assertEqual([r["kind"] for r in rows], ["auth_fail", "auth_ok"])
        from reporting.lifecycle import timeline
        whats = [r["what"] for r in timeline(self.dir, records=[])]
        self.assertEqual(whats, ["CoinDCX key working", "CoinDCX key failed"])


class TestObserveRun(_TempState):
    def test_the_29_oct_halt_alerts(self):
        results = [{"key": "portfolio_g", "venue": "coindcx", "status": "halted", "reason": EXPIRED}]
        out = w.observe_run(results, self.dir, send=self.sent.append)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(self.sent), 1)

    def test_reconciliation_problems_are_read_too(self):
        results = [{"key": "portfolio_g", "venue": "coindcx", "status": "halted",
                    "reason": "Could not reach the exchange", "reconciliation": {"problems": [EXPIRED]}}]
        w.observe_run(results, self.dir, send=self.sent.append)
        self.assertEqual(len(self.sent), 1)

    def test_a_processed_run_counts_as_working_and_a_book_mismatch_says_nothing(self):
        self.rec("coindcx", False, "expired")
        w.observe_run([{"venue": "coindcx", "status": "halted", "reason": "book holds 1, exchange 2"}],
                      self.dir, send=self.sent.append)
        self.assertEqual(len(self.sent), 1)
        w.observe_run([{"venue": "coindcx", "status": "processed"}], self.dir, send=self.sent.append)
        self.assertEqual(len(self.sent), 2)

    def test_a_failure_beats_a_success_at_the_same_venue(self):
        results = [{"venue": "coindcx", "status": "processed"},
                   {"venue": "coindcx", "status": "halted", "reason": EXPIRED}]
        w.observe_run(results, self.dir, send=self.sent.append)
        self.assertFalse(w.load(self.dir)["venues"]["coindcx"]["ok"])


class TestProbe(unittest.TestCase):
    def test_coindcx_expired_key_is_a_failure(self):
        from execution.coindcx_client import CoinDCXError
        client = MagicMock()
        client.balances.side_effect = CoinDCXError(EXPIRED)
        with patch("deployment.credential_store.credential", return_value="x"), \
             patch("execution.coindcx_client.CoinDCXClient", return_value=client):
            ok, detail = w.probe("coindcx", MagicMock(), "config")
        self.assertIs(ok, False)
        self.assertIn("expired", detail)

    def test_coindcx_network_trouble_is_unknown(self):
        client = MagicMock()
        client.balances.side_effect = ConnectionError("Max retries exceeded")
        with patch("deployment.credential_store.credential", return_value="x"), \
             patch("execution.coindcx_client.CoinDCXClient", return_value=client):
            ok, _ = w.probe("coindcx", MagicMock(), "config")
        self.assertIsNone(ok)

    def test_coindcx_working_key(self):
        with patch("deployment.credential_store.credential", return_value="x"), \
             patch("execution.coindcx_client.CoinDCXClient") as cls:
            ok, _ = w.probe("coindcx", MagicMock(), "config")
        self.assertIs(ok, True)
        cls.return_value.balances.assert_called_once()

    def test_kite_follows_the_session_refresh(self):
        for fresh, expected in ((True, True), (False, False)):
            with self.subTest(fresh=fresh), \
                 patch("auth.kite_auto_login.ensure_fresh_kite_session", return_value=fresh):
                self.assertIs(w.probe("kite", MagicMock(), "config")[0], expected)


class TestRunLiveTagsTheVenue(unittest.TestCase):
    def test_refused_results_carry_their_venue(self):
        import run_live
        record = MagicMock(strategy_key="portfolio_g")
        with patch.object(run_live, "eligible", return_value=[(record, 10000)]), \
             patch.object(run_live, "venue_of", return_value="coindcx"), \
             patch.object(run_live, "run_one", return_value={"key": "portfolio_g", "status": "refused",
                                                              "reason": "coindcx credentials are not configured"}):
            results = run_live.run_all(settings=MagicMock(), state_dir=tempfile.gettempdir())
        self.assertEqual(results[0]["venue"], "coindcx")


@unittest.skipUnless(shutil.which("openssl"), "needs openssl to make a throwaway certificate")
class TestHttpsSurvivesASilentClient(unittest.TestCase):
    """The :8443 freeze of 2026-10-29: one client connecting and never sending a TLS hello used to
    block every later visitor, because the handshake ran on the accepting thread."""

    def test_a_silent_connection_does_not_block_the_next_visitor(self):
        from http.server import BaseHTTPRequestHandler
        from dashboard import server

        class Hello(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        cert, key = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                        "-out", cert, "-days", "1", "-subj", "/CN=localhost"],
                       check=True, capture_output=True)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv = server._TLSHandshakeInWorkerServer(("127.0.0.1", 0), Hello, ctx)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]

        silent = socket.create_connection(("127.0.0.1", port))      # connects, never speaks
        self.addCleanup(silent.close)
        time.sleep(0.2)

        client_ctx = ssl.create_default_context()
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw, \
             client_ctx.wrap_socket(raw) as tls:
            tls.sendall(b"GET / HTTP/1.0\r\n\r\n")
            reply = tls.recv(4096)
        self.assertIn(b"200", reply)


if __name__ == "__main__":
    unittest.main()
