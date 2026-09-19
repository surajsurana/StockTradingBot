"""Unit tests for data/fetch_groww.py and dashboard.state_view.portfolio_view -- no network, no real token."""
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from dashboard.state_view import portfolio_view
from data import fetch_groww as fg


def _resp(status, body):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = body
    r.raise_for_status.side_effect = None if status < 400 else RuntimeError("http error")
    return r


class TestFetchHoldings(unittest.TestCase):
    def test_parses_holdings_and_only_ever_calls_the_holdings_endpoint(self):
        body = {"status": "SUCCESS", "payload": {"holdings": [
            {"isin": "INE002A01018", "trading_symbol": "RELIANCE", "quantity": 10, "average_price": 2400.5},
            {"isin": "X", "trading_symbol": "SOLD", "quantity": 0, "average_price": 10}]}}
        with mock.patch.object(fg.requests, "get", return_value=_resp(200, body)) as get:
            out = fg.fetch_holdings("TOKEN")
        self.assertEqual(out, [{"symbol": "RELIANCE", "isin": "INE002A01018", "quantity": 10.0, "avg_price": 2400.5}])   # zero-quantity dropped
        url = get.call_args.args[0]
        self.assertEqual(url, "https://api.groww.in/v1/holdings/user")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer TOKEN")
        self.assertNotIn("order", url.lower())

    def test_a_rejected_token_raises_the_auth_error(self):
        with mock.patch.object(fg.requests, "get", return_value=_resp(401, {})):
            with self.assertRaises(fg.GrowwAuthError):
                fg.fetch_holdings("OLD")


class TestSync(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.now = datetime(2026, 9, 19, 10, 0)

    def _token(self, text="SECRET-TOKEN"):
        with open(os.path.join(self.dir, fg.TOKEN_FILE), "w") as f:
            f.write(text + "\n")

    def test_no_token_means_not_connected(self):
        snap = fg.sync_holdings(self.dir, fetch_fn=lambda t: self.fail("must not call Groww without a token"), now=self.now)
        self.assertEqual((snap["status"], snap["holdings"]), ("not_connected", []))

    def test_good_token_stores_the_holdings(self):
        self._token()
        snap = fg.sync_holdings(self.dir, fetch_fn=lambda t: [{"symbol": "TCS", "quantity": 2.0, "avg_price": 3000.0, "isin": None}], now=self.now)
        self.assertEqual((snap["status"], len(snap["holdings"])), ("connected", 1))
        self.assertEqual(fg.load_snapshot(self.dir)["fetched_at"], "2026-09-19T10:00:00")

    def test_expired_token_keeps_the_last_good_holdings_and_never_leaks_the_token(self):
        self._token()
        fg.sync_holdings(self.dir, fetch_fn=lambda t: [{"symbol": "TCS", "quantity": 2.0, "avg_price": 3000.0, "isin": None}], now=self.now)

        def rejected(t):
            raise fg.GrowwAuthError("Groww rejected the token (HTTP 401)")
        snap = fg.sync_holdings(self.dir, fetch_fn=rejected, now=datetime(2026, 9, 20, 7, 0))
        self.assertEqual(snap["status"], "expired")
        self.assertEqual(len(snap["holdings"]), 1)                   # still shown
        self.assertEqual(snap["fetched_at"], "2026-09-19T10:00:00")  # honest about how old it is
        self.assertNotIn("SECRET-TOKEN", open(os.path.join(self.dir, fg.SNAPSHOT_FILE)).read())

    def test_network_trouble_is_an_error_status_that_keeps_holdings(self):
        self._token()
        fg.sync_holdings(self.dir, fetch_fn=lambda t: [{"symbol": "TCS", "quantity": 1.0, "avg_price": 1.0, "isin": None}], now=self.now)

        def boom(t):
            raise ConnectionError("no route")
        snap = fg.sync_holdings(self.dir, fetch_fn=boom, now=self.now)
        self.assertEqual((snap["status"], len(snap["holdings"])), ("error", 1))
        self.assertIn("ConnectionError", snap["message"])


class TestAutoToken(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, fg.CREDS_FILE), "w") as f:
            f.write('{"totp_token": "TT", "totp_secret": "JBSWY3DPEHPK3PXP"}')
        # 2026-09-19 10:00 IST
        self.t0 = datetime(2026, 9, 19, 10, 0, tzinfo=fg.IST).timestamp()
        self.mints = []

    def _mint(self, tok, sec):
        self.mints.append((tok, sec))
        return f"ACCESS{len(self.mints)}"

    def test_mints_once_then_reuses_cached_token_until_6am_ist(self):
        seen = []
        fg.sync_holdings(self.dir, fetch_fn=lambda t: seen.append(t) or [], mint_fn=self._mint, now_epoch=self.t0)
        fg.sync_holdings(self.dir, fetch_fn=lambda t: seen.append(t) or [], mint_fn=self._mint, now_epoch=self.t0 + 3600)
        self.assertEqual((len(self.mints), seen), (1, ["ACCESS1", "ACCESS1"]))
        next_day = datetime(2026, 9, 20, 6, 1, tzinfo=fg.IST).timestamp()
        fg.sync_holdings(self.dir, fetch_fn=lambda t: seen.append(t) or [], mint_fn=self._mint, now_epoch=next_day)
        self.assertEqual(seen[-1], "ACCESS2")

    def test_rejected_cached_token_is_reminted_once_and_retried(self):
        fg.sync_holdings(self.dir, fetch_fn=lambda t: [], mint_fn=self._mint, now_epoch=self.t0)
        calls = []

        def fetch(t):
            calls.append(t)
            if t == "ACCESS1":
                raise fg.GrowwAuthError("401")
            return [{"symbol": "TCS", "quantity": 1.0, "avg_price": 1.0, "isin": None}]
        snap = fg.sync_holdings(self.dir, fetch_fn=fetch, mint_fn=self._mint, now_epoch=self.t0 + 400)
        self.assertEqual((snap["status"], calls), ("connected", ["ACCESS1", "ACCESS2"]))

    def test_rejected_credentials_give_auth_failed_and_mint_attempts_are_throttled(self):
        def deny(a, b):
            self.mints.append(1)
            raise fg.GrowwAuthError("Groww rejected the TOTP credentials (HTTP 401)")
        fetch = lambda t: self.fail("no token, no fetch")
        s1 = fg.sync_holdings(self.dir, fetch_fn=fetch, mint_fn=deny, now_epoch=self.t0)
        s2 = fg.sync_holdings(self.dir, fetch_fn=fetch, mint_fn=deny, now_epoch=self.t0 + 60)
        self.assertEqual((s1["status"], s2["status"], len(self.mints)), ("auth_failed", "auth_failed", 1))

    def test_mint_parses_token_shapes_and_sends_totp(self):
        for body in ({"token": "A"}, {"payload": {"token": "A"}}, {"access_token": "A"}):
            with mock.patch.object(fg.requests, "post", return_value=_resp(200, body)) as post:
                self.assertEqual(fg.mint_access_token("TT", "JBSWY3DPEHPK3PXP"), "A")
        kw = post.call_args.kwargs
        self.assertEqual((kw["json"]["key_type"], len(kw["json"]["totp"]), kw["headers"]["Authorization"]), ("totp", 6, "Bearer TT"))
        with mock.patch.object(fg.requests, "post", return_value=_resp(401, {})):
            with self.assertRaises(fg.GrowwAuthError):
                fg.mint_access_token("TT", "JBSWY3DPEHPK3PXP")

    def test_secrets_never_reach_the_snapshot(self):
        fg.sync_holdings(self.dir, fetch_fn=lambda t: [], mint_fn=self._mint, now_epoch=self.t0)
        self.assertNotIn("ACCESS1", open(os.path.join(self.dir, fg.SNAPSHOT_FILE)).read())


class TestPortfolioView(unittest.TestCase):
    def test_value_pnl_today_and_totals(self):
        snap = {"status": "connected", "fetched_at": "x", "holdings": [
            {"symbol": "AAA", "quantity": 10, "avg_price": 100.0}, {"symbol": "BBB", "quantity": 5, "avg_price": 200.0}]}
        v = portfolio_view(snap, {"AAA.NS": 110.0}, {"AAA.NS": 108.0})
        a, b = v["holdings"]
        self.assertEqual((a["invested"], a["value"], a["pnl"], a["pct"], a["today"]), (1000.0, 1100.0, 100.0, 10.0, 20.0))
        self.assertEqual((b["price"], b["value"], b["pnl"]), (None, None, None))     # not priced: shown at cost, no guessed P&L
        t = v["totals"]
        self.assertEqual((t["invested"], t["unpriced"], t["pnl"], t["today"]), (2000.0, 1, 100.0, 20.0))
        self.assertEqual(t["value"], 2100.0)          # priced holding at market, unpriced at cost

    def test_no_snapshot_is_simply_not_connected(self):
        v = portfolio_view(None, {})
        self.assertEqual((v["status"], v["holdings"], v["totals"]["holdings"]), ("not_connected", [], 0))


if __name__ == "__main__":
    unittest.main()
