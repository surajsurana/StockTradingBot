"""
deployment/crypto_executor.py -- the module that moves real money at CoinDCX.

Same properties as the equity executor, for the same reasons: the guard cannot be bypassed, the
attempt is recorded before it is sent, and nothing raises. Plus one specific to this venue -- the
caps are in rupees, so an order decided in USDT must be priced at the exchange's own INR market
before the caps mean anything.
"""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord
from deployment.crypto_executor import market_for, place_crypto_order
from deployment.live_executor import order_log_path

INR_PRICE = 8_453_486.20          # CoinDCX BTCINR, 2026-10-06
USDT_PRICE = 85_784.83            # Binance BTCUSDT the same moment
USDINR = 96.42                    # => about +2.2% India premium


def _settings(**over):
    base = dict(LIVE_TRADING=True, COINDCX_API_KEY="ck", COINDCX_API_SECRET="cs",
                LIVE_MAX_ORDER_VALUE_RUPEES=5_000.0, LIVE_MAX_EXPOSURE_RUPEES=25_000.0,
                LIVE_MAX_ORDERS_PER_DAY=20)
    base.update(over)
    return SimpleNamespace(**base)


def _record(status=DeploymentStatus.PILOT_LIVE):
    return StrategyRecord(strategy_key="portfolio_g", display_name="Pool G", strategy_family="crypto",
                          research_verdict=ResearchVerdict.PASS, deployment_status=status)


# The real rules CoinDCX returns for BTCINR, read from the live exchange 2026-10-07.
BTCINR_RULES = {"price_decimals": 1, "quantity_decimals": 5, "min_quantity": 1e-05,
                "min_notional": 100.0}


class _Client:
    def rules_for(self, market):
        if self.rules_raises:
            raise self.rules_raises
        return dict(self.rules) if self.rules is not None else {}

    def __init__(self, price=INR_PRICE, result=None, raises=None, price_raises=None,
                 rules=None, rules_raises=None):
        self.rules = BTCINR_RULES if rules is None else rules
        self.rules_raises = rules_raises
        # what the order looks like once it has actually filled, as CoinDCX reports it
        self.status_row = {"id": "cd-1", "status": "filled", "avg_price": 8_387_793.3,
                           "total_quantity": 0.00031, "fee_amount": 15.34}
        self.price = price
        self.result = result if result is not None else {"id": "cd-1", "status": "open",
                                                         "avg_price": INR_PRICE}
        self.raises = raises
        self.price_raises = price_raises
        self.orders = []

    def last_price(self, market):
        if self.price_raises:
            raise self.price_raises
        return self.price

    def place_order(self, **kw):
        self.orders.append(kw)
        if self.raises:
            raise self.raises
        return self.result

    def order_status(self, order_id):
        return self.status_row


def _place(state_dir, client=None, **over):
    kwargs = dict(settings=_settings(), record=_record(), state_dir=state_dir, symbol="BTC",
                  side="BUY", quantity=0.0003, reference_price_usdt=USDT_PRICE,
                  strategy_key="portfolio_g", usdinr=USDINR,
                  client=client if client is not None else _Client())
    kwargs.update(over)
    return place_crypto_order(**kwargs)


def _log(state_dir):
    path = order_log_path(state_dir)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestMarketNaming(unittest.TestCase):
    def test_a_coin_becomes_its_inr_market(self):
        self.assertEqual(market_for("BTC"), "BTCINR")
        self.assertEqual(market_for(" eth "), "ETHINR")

    def test_nothing_is_translated_or_guessed(self):
        for coin in ("BTC", "ETH", "BNB", "XRP", "SOL"):
            self.assertEqual(market_for(coin), coin + "INR")


class TestTheGuardCannotBeBypassed(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_live_trading_off_sends_nothing(self):
        c = _Client()
        out = _place(self.d, client=c, settings=_settings(LIVE_TRADING=False))
        self.assertFalse(out.placed)
        self.assertEqual(c.orders, [])
        self.assertEqual([r["stage"] for r in _log(self.d)], ["refused"])

    def test_a_paper_strategy_sends_nothing(self):
        c = _Client()
        self.assertFalse(_place(self.d, client=c, record=_record(DeploymentStatus.PAPER_TRADING)).placed)
        self.assertEqual(c.orders, [])

    def test_missing_coindcx_credentials_send_nothing_even_with_kite_configured(self):
        c = _Client()
        only_kite = _settings(COINDCX_API_KEY="", COINDCX_API_SECRET="",
                              KITE_API_KEY="k", KITE_ACCESS_TOKEN="t")
        with patch("deployment.live_guard.CONFIG_DIR", tempfile.mkdtemp()):
            out = _place(self.d, client=c, settings=only_kite)
        self.assertFalse(out.placed)
        self.assertEqual(c.orders, [])
        self.assertTrue(any("CoinDCX" in r for r in out.reasons), out.reasons)

    def test_the_kill_switch_sends_nothing(self):
        from deployment.live_guard import KILL_SWITCH_FILENAME
        open(os.path.join(self.d, KILL_SWITCH_FILENAME), "w").close()
        c = _Client()
        self.assertFalse(_place(self.d, client=c).placed)
        self.assertEqual(c.orders, [])


class TestTheCapsAreCheckedInRupees(unittest.TestCase):
    """The order is decided in USDT but the caps are rupee amounts, so the quantity has to be priced
    at the exchange's own INR market before any cap means anything."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_an_order_over_the_rupee_cap_is_refused(self):
        c = _Client()
        out = _place(self.d, client=c, quantity=0.01)        # 0.01 BTC ~ Rs84,500
        self.assertFalse(out.placed)
        self.assertEqual(c.orders, [])
        self.assertTrue(any("per-order cap" in r for r in out.reasons), out.reasons)

    def test_a_small_order_passes_the_same_cap(self):
        out = _place(self.d, quantity=0.0003)                # ~Rs2,536, a 25% sleeve of Rs10,000
        self.assertTrue(out.placed, out.reasons)

    def test_an_unpriceable_market_refuses_rather_than_sizing_blind(self):
        for client in (_Client(price=None), _Client(price=0),
                       _Client(price_raises=RuntimeError("ticker down"))):
            out = _place(tempfile.mkdtemp(), client=client)
            self.assertFalse(out.placed)
            self.assertEqual(client.orders, [])


class TestTheRecordIsWrittenBeforeTheOrderIsSent(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_sent_is_logged_before_the_exchange_is_called(self):
        seen = {}
        d = self.d

        class Watcher(_Client):
            def place_order(self, **kw):
                seen["stages"] = [r["stage"] for r in _log(d)]
                return super().place_order(**kw)

        _place(self.d, client=Watcher())
        self.assertIn("sent", seen["stages"])

    def test_the_india_premium_is_recorded_on_every_order(self):
        # when the live book later diverges from paper, this is how premium drift is told from slippage
        _place(self.d)
        sent = next(r for r in _log(self.d) if r["stage"] == "sent")
        self.assertAlmostEqual(sent["india_premium_pct"], 2.2, delta=0.3)
        self.assertEqual(sent["usdinr"], USDINR)
        self.assertEqual(sent["reference_price_usdt"], USDT_PRICE)
        self.assertEqual(sent["broker"], "coindcx")

    def test_if_the_log_cannot_be_written_no_order_is_sent(self):
        c = _Client()
        with patch("deployment.crypto_executor._append_log", side_effect=OSError("read-only")):
            out = _place(self.d, client=c)
        self.assertFalse(out.placed)
        self.assertEqual(c.orders, [])


class TestItPlacesALimitThroughTheMarket(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_buy_is_priced_slightly_above_and_a_sell_slightly_below(self):
        # CoinDCX INR books are thinner than the global ones; a market order can fill a long way from
        # the price the decision assumed
        buy = _Client()
        _place(self.d, client=buy, side="BUY")
        self.assertGreater(buy.orders[0]["price"], INR_PRICE)
        sell = _Client()
        _place(tempfile.mkdtemp(), client=sell, side="SELL")
        self.assertLess(sell.orders[0]["price"], INR_PRICE)

    def test_the_quantity_is_rounded_to_the_exchanges_own_precision(self):
        # BTCINR accepts 5 decimals; sending 6 is rejected outright, so the cycle's quantity is
        # rounded to what the market will take rather than sent and refused
        c = _Client()
        _place(self.d, client=c, quantity=0.000275)
        self.assertEqual(c.orders[0]["quantity"], 0.00028)
        self.assertEqual(c.orders[0]["market"], "BTCINR")

    def test_the_price_is_rounded_to_the_exchanges_own_precision(self):
        # "INR precision should be 1" is the error that turned away the first real order this
        # program ever sent
        c = _Client()
        _place(self.d, client=c)
        price = c.orders[0]["price"]
        self.assertEqual(price, round(price, 1), price)

    def test_an_unlisted_market_is_refused_rather_than_sent_blind(self):
        c = _Client(rules={})
        out = _place(self.d, client=c)
        self.assertFalse(out.placed)
        self.assertEqual(c.orders, [])
        self.assertTrue(any("does not list" in r for r in out.reasons))

    def test_an_order_below_the_exchange_minimum_is_refused_with_a_readable_reason(self):
        c = _Client()
        out = _place(self.d, client=c, quantity=0.0000001)    # worth far under Rs100
        self.assertFalse(out.placed)
        self.assertEqual(c.orders, [])
        self.assertTrue(any("minimum" in r for r in out.reasons), out.reasons)


class TestTheRecordIsWhatHappenedNotWhatWasAsked(unittest.TestCase):
    """The create response returns the instant the order is ACCEPTED, before it fills, so its
    avg_price is empty and only the limit price is known. Logging that made the dashboard say
    Rs2,599 where CoinDCX said Rs2,600.21, with the Rs13.00 fee and Rs2.34 GST missing entirely --
    a small difference, which is the worst kind, because it reads as rounding rather than a cost."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_the_logged_fill_is_the_average_price_not_the_limit(self):
        c = _Client()
        out = _place(self.d, client=c)
        self.assertEqual(out.fill_price, 8_387_793.3)
        self.assertNotEqual(out.fill_price, c.orders[0]["price"])      # not the limit we asked for

    def test_the_logged_value_includes_the_exchange_fee(self):
        _place(self.d)
        accepted = next(r for r in _log(self.d) if r["stage"] == "accepted")
        self.assertAlmostEqual(accepted["value"], 8_387_793.3 * 0.00031 + 15.34, places=1)
        self.assertEqual(accepted["fee"], 15.34)
        self.assertEqual(accepted["status"], "filled")

    def test_an_order_that_cannot_be_re_read_still_counts_as_placed(self):
        # it filled whether or not we could confirm it; claiming otherwise invites a retry
        class Unreadable(_Client):
            def order_status(self, order_id):
                raise ConnectionError("down")

        out = _place(self.d, client=Unreadable())
        self.assertTrue(out.placed)
        self.assertIsNotNone(out.fill_price)          # falls back to the limit rather than nothing

    def test_it_does_not_wait_forever_for_a_fill(self):
        class NeverFills(_Client):
            def __init__(self):
                super().__init__()
                self.status_row = {"status": "open", "avg_price": 0}

        out = place_crypto_order(settings=_settings(), record=_record(), state_dir=self.d,
                                 symbol="BTC", side="BUY", quantity=0.0003,
                                 reference_price_usdt=USDT_PRICE, strategy_key="portfolio_g",
                                 usdinr=USDINR, client=NeverFills())
        self.assertTrue(out.placed)


class TestNothingRaises(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_an_exchange_exception_becomes_a_refusal(self):
        out = _place(self.d, client=_Client(raises=ConnectionError("network down")))
        self.assertFalse(out.placed)
        self.assertTrue(any("network down" in r for r in out.reasons))
        self.assertEqual([r["stage"] for r in _log(self.d)][-1], "failed")

    def test_a_rejection_is_reported_with_its_message(self):
        out = _place(self.d, client=_Client(result={"status": "rejected", "message": "insufficient funds"}))
        self.assertFalse(out.placed)
        self.assertTrue(any("insufficient funds" in r for r in out.reasons))

    def test_a_malformed_response_does_not_raise(self):
        for junk in (None, {}, [], "not a dict", {"status": "open", "avg_price": "n/a"}):
            out = _place(tempfile.mkdtemp(), client=_Client(result=junk))
            self.assertIsInstance(out.placed, bool)

    def test_accepted_without_an_id_still_counts_as_placed(self):
        out = _place(self.d, client=_Client(result={"status": "open"}))
        self.assertTrue(out.placed)                   # the money moved; claiming otherwise invites a retry
        self.assertTrue(any("no order id" in r for r in out.reasons))


if __name__ == "__main__":
    unittest.main()
