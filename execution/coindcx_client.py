"""
The CoinDCX REST client. The only module that talks to the crypto broker.

SCOPE IS DELIBERATELY NARROW. It can read balances, read a ticker, place an order and read an order
back. It contains NO withdrawal or transfer call of any kind -- not because the API key lacks the
permission (it does lack it, and that is the real control), but because a function that cannot move
coins off the exchange cannot be called by mistake, misused by a future caller, or reached by a bug.
That absence is asserted by a test.

AUTHENTICATION is HMAC-SHA256 over the exact JSON body sent, so the body must be serialised ONCE and
both signed and posted -- re-serialising would change the separators and the signature would not
match the payload. The secret is used to sign and is never logged, echoed or returned.

WHAT IT DOES NOT DO. It does not decide anything: no sizing, no symbol selection, no retry-on-reject.
It is a transport. deployment/live_guard.py decides whether an order may be placed at all, and the
caller decides what to place. Keeping judgement out of here is what makes it safe to reuse.

MARKETS. CoinDCX names a market by concatenation -- BTCINR, BTCUSDT. Which quote currency to trade is
a real decision, not a detail: this program's strategies were researched on Binance USDT prices, so a
USDT market tracks the data they were built on, while an INR market is what an INR-funded account
trades natively and carries its own premium to the global price. The caller passes the market
explicitly; this module never guesses one.
"""

import hashlib
import hmac
import json
import time
from typing import Optional

BASE_URL = "https://api.coindcx.com"
BALANCES_PATH = "/exchange/v1/users/balances"
CREATE_ORDER_PATH = "/exchange/v1/orders/create"
ORDER_STATUS_PATH = "/exchange/v1/orders/status"
TICKER_PATH = "/exchange/ticker"
MARKETS_PATH = "/exchange/v1/markets_details"
DEFAULT_TIMEOUT = 30


class CoinDCXError(RuntimeError):
    """A broker-side refusal or a transport failure. Carries the broker's own message where there is
    one, because 'rejected' without a reason is useless when real money is involved."""


class CoinDCXClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str = BASE_URL,
                 timeout: int = DEFAULT_TIMEOUT, session=None):
        if not str(api_key or "").strip() or not str(api_secret or "").strip():
            raise ValueError("CoinDCX client needs both an API key and an API secret.")
        self._key = str(api_key).strip()
        self._secret = str(api_secret).strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = session          # injectable so tests never touch a network

    # -- transport ---------------------------------------------------------------------------

    def _signed_post(self, path: str, body: dict) -> object:
        """Signs and posts `body`. The SAME serialised string is signed and sent; signing a
        re-serialised copy is the classic way to get an 'Invalid signature' that looks like a
        credential problem but is not."""
        payload = dict(body or {})
        payload.setdefault("timestamp", int(time.time() * 1000))
        text = json.dumps(payload, separators=(",", ":"))
        signature = hmac.new(self._secret.encode(), text.encode(), hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-AUTH-APIKEY": self._key,
                   "X-AUTH-SIGNATURE": signature}
        session = self._session
        if session is None:
            import requests
            session = requests
        try:
            response = session.post(self.base_url + path, data=text, headers=headers,
                                    timeout=self.timeout)
        except Exception as e:                       # network, DNS, TLS -- all one failure to us
            raise CoinDCXError(f"Could not reach CoinDCX: {type(e).__name__}: {e}") from e
        return self._decode(response, path)

    @staticmethod
    def _decode(response, path: str) -> object:
        status = getattr(response, "status_code", 0)
        text = getattr(response, "text", "")
        if status != 200:
            # An IP-bound key called from the wrong address fails here, and the message says so --
            # worth surfacing verbatim rather than flattening to "unauthorised".
            raise CoinDCXError(f"CoinDCX {path} returned HTTP {status}: {str(text)[:300]}")
        try:
            return response.json()
        except Exception as e:
            raise CoinDCXError(f"CoinDCX {path} returned unreadable JSON: {str(text)[:200]}") from e

    # -- reads -------------------------------------------------------------------------------

    def balances(self) -> list:
        """Every currency the account holds, as the broker reports it."""
        data = self._signed_post(BALANCES_PATH, {})
        return data if isinstance(data, list) else []

    def balance_of(self, currency: str) -> float:
        """Free balance of one currency, 0.0 if absent. `locked_balance` is deliberately excluded:
        money already committed to a resting order is not available to size a new one."""
        for row in self.balances():
            if str(row.get("currency", "")).upper() == str(currency).upper():
                try:
                    return float(row.get("balance", 0) or 0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def ticker(self) -> list:
        """Public, unauthenticated. Used to price a limit order and to sanity-check a market name."""
        session = self._session
        if session is None:
            import requests
            session = requests
        try:
            response = session.get(self.base_url + TICKER_PATH, timeout=self.timeout)
        except Exception as e:
            raise CoinDCXError(f"Could not reach CoinDCX ticker: {type(e).__name__}: {e}") from e
        data = self._decode(response, TICKER_PATH)
        return data if isinstance(data, list) else []

    def last_price(self, market: str) -> Optional[float]:
        for row in self.ticker():
            if str(row.get("market", "")).upper() == str(market).upper():
                try:
                    return float(row.get("last_price"))
                except (TypeError, ValueError):
                    return None
        return None

    def market_details(self) -> dict:
        """{MARKET: rules} from the exchange, fetched once per client and cached.

        The rules are not cosmetic. CoinDCX rejects an order outright for a price with too many
        decimals -- "INR precision should be 1" -- which is how the first real order of this
        program's life was turned away. Guessing the precision would work until a market with
        different rules was added; asking is the only version that keeps working."""
        if getattr(self, "_markets", None) is None:
            session = self._session
            if session is None:
                import requests
                session = requests
            try:
                response = session.get(self.base_url + MARKETS_PATH, timeout=self.timeout)
            except Exception as e:
                raise CoinDCXError(f"Could not read CoinDCX market rules: {type(e).__name__}: {e}") from e
            rows = self._decode(response, MARKETS_PATH)
            self._markets = {str(r.get("coindcx_name", "")).upper(): r
                             for r in (rows or []) if isinstance(r, dict)}
        return self._markets

    def rules_for(self, market: str) -> dict:
        """Price/quantity precision and the minimums for one market, or {} if the exchange does not
        list it -- which the caller must treat as "do not send", never as "no limits"."""
        row = self.market_details().get(str(market).strip().upper()) or {}
        if not row:
            return {}
        def _int(name, default):
            try:
                return int(row.get(name, default))
            except (TypeError, ValueError):
                return default
        def _float(name, default):
            try:
                return float(row.get(name, default))
            except (TypeError, ValueError):
                return default
        return {"price_decimals": _int("base_currency_precision", 1),
                "quantity_decimals": _int("target_currency_precision", 6),
                "min_quantity": _float("min_quantity", 0.0),
                "min_notional": _float("min_notional", 0.0)}

    def order_status(self, order_id: str) -> dict:
        data = self._signed_post(ORDER_STATUS_PATH, {"id": str(order_id)})
        return data if isinstance(data, dict) else {}

    # -- the one write ------------------------------------------------------------------------

    def place_order(self, *, market: str, side: str, quantity: float,
                    price: Optional[float] = None) -> dict:
        """Places ONE order and returns the broker's own record of it.

        `price` None means a market order. A limit order is the safer default for a thin book -- it
        cannot fill arbitrarily far from the price the decision was made at -- so the caller is
        expected to pass one; this module does not choose for it.

        Validation here is only of the kind that prevents a malformed request: a side the API would
        not understand, a non-positive quantity. Whether the order SHOULD be placed is
        deployment/live_guard.py's question, and it is asked before this is ever called."""
        side_text = str(side or "").strip().lower()
        if side_text not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
        try:
            qty = float(quantity)
        except (TypeError, ValueError):
            raise ValueError(f"quantity must be a number, got {quantity!r}")
        if qty <= 0:
            raise ValueError(f"quantity must be positive, got {qty}")
        if not str(market or "").strip():
            raise ValueError("market is required (e.g. BTCINR or BTCUSDT)")

        body = {"market": str(market).strip().upper(), "side": side_text,
                "total_quantity": qty, "order_type": "market_order"}
        if price is not None:
            body["order_type"] = "limit_order"
            body["price_per_unit"] = float(price)
        data = self._signed_post(CREATE_ORDER_PATH, body)
        orders = (data or {}).get("orders") if isinstance(data, dict) else None
        if isinstance(orders, list) and orders:
            return orders[0]
        if isinstance(data, dict):
            return data
        raise CoinDCXError(f"CoinDCX accepted nothing recognisable for {side_text} {qty} {market}: {data!r}")
