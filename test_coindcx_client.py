"""
execution/coindcx_client.py -- the only module that talks to the crypto broker.

The properties under test: it cannot move coins off the exchange, it signs exactly the bytes it
sends, and a broker or network failure becomes a structured error rather than something that escapes
into a trading loop.
"""
import hashlib
import hmac
import json
import unittest

from execution.coindcx_client import CoinDCXClient, CoinDCXError


class _Response:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Session:
    """Records requests; never touches a network."""

    def __init__(self, response=None, raises=None):
        self.response = response or _Response(payload={"orders": [{"id": "o-1", "status": "open"}]})
        self.raises = raises
        self.posts = []
        self.gets = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append({"url": url, "data": data, "headers": headers})
        if self.raises:
            raise self.raises
        return self.response

    def get(self, url, timeout=None):
        self.gets.append(url)
        if self.raises:
            raise self.raises
        return self.response


def _client(session=None, **kw):
    return CoinDCXClient("key-123", "secret-abc", session=session or _Session(), **kw)


class TestItCannotMoveCoinsOffTheExchange(unittest.TestCase):
    def test_no_function_or_endpoint_can_move_coins(self):
        # The API key has no withdrawal permission, which is the real control. This is the second
        # one: a function that does not exist cannot be called by a future caller or reached by a
        # bug. Checked against the module's actual API surface -- its names and its URL strings --
        # rather than its prose, which necessarily discusses the thing it does not do.
        import ast
        import execution.coindcx_client as mod
        with open(mod.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names, urls = [], []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip()
                # endpoint strings only: a path or a URL with no spaces. Prose necessarily mentions
                # the very words being banned, which is why this looks at the API surface instead.
                if (text.startswith("/") or text.startswith("http")) and " " not in text:
                    urls.append(text)
        surface = " ".join(names + urls).lower()
        for forbidden in ("withdraw", "transfer", "send_to", "payout"):
            self.assertNotIn(forbidden, surface, forbidden)

    def test_the_only_endpoints_are_reads_and_order_creation(self):
        import execution.coindcx_client as mod
        with open(mod.__file__, encoding="utf-8") as f:
            source = f.read()
        paths = [ln.split("_PATH")[0] for ln in source.splitlines()
                 if "_PATH = " in ln and ln[:1].isupper()]
        self.assertEqual(sorted(paths),
                         ["BALANCES", "CREATE_ORDER", "MARKETS", "ORDER_STATUS", "TICKER"])


class TestSigning(unittest.TestCase):
    def test_it_signs_exactly_the_bytes_it_sends(self):
        # signing a re-serialised copy produces an "Invalid signature" that looks like a credential
        # problem and is not, so the sent body and the signed body must be the same string
        s = _Session(_Response(payload=[]))
        _client(s).balances()
        sent = s.posts[0]["data"]
        expected = hmac.new(b"secret-abc", sent.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(s.posts[0]["headers"]["X-AUTH-SIGNATURE"], expected)

    def test_the_secret_is_never_put_in_a_header_or_body(self):
        s = _Session(_Response(payload=[]))
        _client(s).balances()
        blob = json.dumps(s.posts[0])
        self.assertNotIn("secret-abc", blob)
        self.assertIn("key-123", blob)          # the KEY identifies us and is meant to be sent

    def test_every_request_carries_a_timestamp(self):
        s = _Session(_Response(payload=[]))
        _client(s).balances()
        self.assertIn("timestamp", json.loads(s.posts[0]["data"]))

    def test_it_refuses_to_construct_without_both_halves(self):
        for key, secret in (("", "s"), ("k", ""), ("  ", "s"), (None, "s")):
            with self.assertRaises(ValueError):
                CoinDCXClient(key, secret)


class TestPlaceOrder(unittest.TestCase):
    def test_a_limit_order_sends_the_price_and_the_right_type(self):
        s = _Session()
        out = _client(s).place_order(market="BTCINR", side="buy", quantity=0.01, price=5_000_000.0)
        body = json.loads(s.posts[0]["data"])
        self.assertEqual(body["order_type"], "limit_order")
        self.assertEqual((body["market"], body["side"], body["total_quantity"]),
                         ("BTCINR", "buy", 0.01))
        self.assertEqual(body["price_per_unit"], 5_000_000.0)
        self.assertEqual(out["id"], "o-1")

    def test_no_price_means_a_market_order_and_sends_no_price(self):
        s = _Session()
        _client(s).place_order(market="BTCUSDT", side="sell", quantity=1.5)
        body = json.loads(s.posts[0]["data"])
        self.assertEqual(body["order_type"], "market_order")
        self.assertNotIn("price_per_unit", body)

    def test_a_malformed_request_is_refused_before_it_reaches_the_broker(self):
        s = _Session()
        c = _client(s)
        for kwargs in ({"market": "BTCINR", "side": "hodl", "quantity": 1},
                       {"market": "BTCINR", "side": "buy", "quantity": 0},
                       {"market": "BTCINR", "side": "buy", "quantity": -1},
                       {"market": "BTCINR", "side": "buy", "quantity": "lots"},
                       {"market": "", "side": "buy", "quantity": 1}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                c.place_order(**kwargs)
        self.assertEqual(s.posts, [])            # nothing was sent

    def test_the_market_name_is_normalised_but_never_guessed(self):
        s = _Session()
        _client(s).place_order(market=" btcinr ", side="buy", quantity=1, price=1.0)
        self.assertEqual(json.loads(s.posts[0]["data"])["market"], "BTCINR")


class TestFailuresAreStructured(unittest.TestCase):
    def test_a_network_failure_becomes_a_CoinDCXError(self):
        with self.assertRaises(CoinDCXError) as ctx:
            _client(_Session(raises=OSError("connection reset"))).balances()
        self.assertIn("connection reset", str(ctx.exception))

    def test_a_non_200_surfaces_the_brokers_own_message(self):
        # an IP-bound key called from the wrong address fails here; the message is what explains it
        s = _Session(_Response(status=401, payload=None, text='{"message":"Invalid request ip"}'))
        with self.assertRaises(CoinDCXError) as ctx:
            _client(s).balances()
        self.assertIn("401", str(ctx.exception))
        self.assertIn("Invalid request ip", str(ctx.exception))

    def test_unreadable_json_does_not_raise_a_bare_ValueError(self):
        s = _Session(_Response(status=200, payload=None, text="<html>maintenance</html>"))
        with self.assertRaises(CoinDCXError):
            _client(s).balances()


class TestReads(unittest.TestCase):
    def test_balance_of_reports_free_balance_and_ignores_locked(self):
        rows = [{"currency": "INR", "balance": "1500.5", "locked_balance": "900"},
                {"currency": "BTC", "balance": "0", "locked_balance": "0.5"}]
        c = _client(_Session(_Response(payload=rows)))
        self.assertEqual(c.balance_of("inr"), 1500.5)    # locked money cannot size a new order
        self.assertEqual(c.balance_of("BTC"), 0.0)
        self.assertEqual(c.balance_of("DOGE"), 0.0)      # absent is zero, not an error

    def test_a_junk_balance_reads_zero_rather_than_raising(self):
        c = _client(_Session(_Response(payload=[{"currency": "INR", "balance": "n/a"}])))
        self.assertEqual(c.balance_of("INR"), 0.0)

    def test_last_price_matches_the_market_case_insensitively(self):
        rows = [{"market": "BTCINR", "last_price": "5100000.0"},
                {"market": "ETHINR", "last_price": "x"}]
        c = _client(_Session(_Response(payload=rows)))
        self.assertEqual(c.last_price("btcinr"), 5_100_000.0)
        self.assertIsNone(c.last_price("ETHINR"))        # unparseable is None, not a guess
        self.assertIsNone(c.last_price("SOLINR"))


if __name__ == "__main__":
    unittest.main()
