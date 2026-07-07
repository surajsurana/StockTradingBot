"""
Execution layer. Paper mode (default) just logs what would have happened.
Live mode calls Zerodha's Kite Connect order-placement API directly.

The two modes share the same call signature on purpose -- switching from
paper to live is a one-line config change (LIVE_TRADING = True in
config/settings.py), not a rewrite. This is deliberate: the exact code path
that gets tested in paper mode is the same one that runs live.
"""

import csv
import os
from datetime import datetime

import requests

from risk.risk_manager import ApprovedTrade


PAPER_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "paper_trades_log.csv")


def fetch_available_capital(api_key: str, access_token: str) -> float:
    """
    Fetches your real available trading capital from Kite's margins API, so
    position sizing reflects what's actually in the account right now rather
    than a hardcoded number in config.settings that you'd have to remember to
    keep updating by hand.

    Uses the equity segment's "net" figure (matches what test_kite_connection.py
    and Kite's own dashboard show as available funds).

    Raises clearly on any failure -- a stale/missing access_token, a network
    problem, or an unexpected response shape -- rather than silently falling
    back to a guessed number. Callers decide what to do with that failure:
    run_daily.py treats it as a hard abort in live mode (never trade on an
    unknown capital figure) but a soft fallback to config.STARTING_CAPITAL in
    paper mode (paper trading can still proceed with a placeholder number).
    """
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }
    resp = requests.get("https://api.kite.trade/user/margins", headers=headers)
    result = resp.json()

    if resp.status_code != 200 or "data" not in result:
        raise RuntimeError(
            f"Could not fetch margins from Kite (status {resp.status_code}): {result}. "
            f"Common cause: KITE_ACCESS_TOKEN is stale -- run refresh_kite_token.py first."
        )

    equity = result["data"].get("equity")
    if not equity or "net" not in equity:
        raise RuntimeError(f"Kite margins response didn't include the expected equity.net field: {result}")

    return float(equity["net"])


def _round_to_tick(price: float, tick: float = 0.05) -> float:
    """NSE equity prices must be in multiples of 0.05."""
    return round(round(price / tick) * tick, 2)


class ExecutionEngine:
    def __init__(self, live_trading: bool, api_key: str = "", access_token: str = "",
                 limit_order_buffer_pct: float = 0.015):
        """
        limit_order_buffer_pct: how far through the market to price live LIMIT
        orders (see _place_live_order's docstring for why LIMIT instead of
        MARKET). Defaults to 1.5% -- wider than the 1% used for the manual
        test_live_order.py script, since signal.entry_price here can be a bit
        stale (computed from the last available price data during Stage 1,
        not a fresh quote -- this account's Kite API tier doesn't have access
        to live quotes). Override via config.settings.LIMIT_ORDER_BUFFER_PCT
        if fills are being missed or prices moved further than expected.
        """
        self.live_trading = live_trading
        self.api_key = api_key
        self.access_token = access_token
        self.limit_order_buffer_pct = limit_order_buffer_pct

    def place_order(self, trade: ApprovedTrade) -> dict:
        if self.live_trading:
            return self._place_live_order(trade)
        return self._log_paper_order(trade)

    def _log_paper_order(self, trade: ApprovedTrade) -> dict:
        signal = trade.signal
        row = {
            "timestamp": datetime.now().isoformat(),
            "symbol": signal.symbol,
            "direction": signal.direction,
            "strategy": signal.strategy_name,
            "quantity": trade.quantity,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "target": signal.target,
            "capital_deployed": trade.capital_deployed,
            "reason": signal.reason,
        }

        file_exists = os.path.exists(PAPER_LOG_PATH)
        with open(PAPER_LOG_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        print(f"[PAPER TRADE] {signal.direction} {trade.quantity} x {signal.symbol} "
              f"@ {signal.entry_price} (stop {signal.stop_loss}, target {signal.target})")
        return {"status": "paper", **row}

    def _place_live_order(self, trade: ApprovedTrade) -> dict:
        """
        Places a REAL order via Kite Connect. Only reached if config.LIVE_TRADING
        is True. Uses the regular equity order endpoint, CNC product type
        (delivery, appropriate for swing trading -- not MIS intraday).

        Uses a LIMIT order priced through signal.entry_price (by
        limit_order_buffer_pct) rather than a plain MARKET order -- Kite's
        API rejects plain MARKET orders unless "market protection" is
        configured on the account (confirmed via test_live_order.py's live
        testing), and this account's Kite API tier doesn't have access to
        live quotes to price a marketable limit off a fresher number. A
        LIMIT order priced a bit through the market fills essentially like a
        MARKET order would for a liquid, small-quantity trade.
        """
        if not self.api_key or not self.access_token:
            raise RuntimeError("Live trading is enabled but api_key/access_token are missing.")

        signal = trade.signal
        symbol = signal.symbol.replace(".NS", "")  # Kite uses raw NSE symbols, not the .NS suffix

        if signal.direction == "BUY":
            limit_price = _round_to_tick(signal.entry_price * (1 + self.limit_order_buffer_pct))
        else:
            limit_price = _round_to_tick(signal.entry_price * (1 - self.limit_order_buffer_pct))

        headers = {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.api_key}:{self.access_token}",
        }
        payload = {
            "exchange": "NSE",
            "tradingsymbol": symbol,
            "transaction_type": "BUY" if signal.direction == "BUY" else "SELL",
            "quantity": trade.quantity,
            "order_type": "LIMIT",
            "price": limit_price,
            "product": "CNC",     # delivery/swing, not intraday
            "validity": "DAY",
        }

        resp = requests.post(
            "https://api.kite.trade/orders/regular",
            headers=headers,
            data=payload,
        )
        result = resp.json()
        print(f"[LIVE ORDER] status={resp.status_code} response={result}")
        return result
