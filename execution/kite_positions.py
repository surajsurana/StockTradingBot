"""
What the Kite account actually holds, for reconciliation.

The equity counterpart of CoinDCXClient.balances(). Read-only: it fetches and returns, and places,
modifies and cancels nothing. A test asserts it contains no order call at all.

TWO ENDPOINTS, BOTH NEEDED, and conflating them is the mistake this is written to avoid:

  - /portfolio/holdings is what you OWN -- delivery stock, settled, sitting in the demat account.
    Everything the paper pools trade is CNC delivery, so this is where their positions appear from
    the next settlement onwards.
  - /portfolio/positions is what you have OPEN TODAY -- including intraday, and including a
    delivery buy made this morning that has not yet become a holding.

A position bought today appears in `positions` and not in `holdings`; the same position tomorrow
appears in `holdings` and not in `positions`. Reconciling against only one of them would report a
real, correctly-held position as missing on exactly one of those two days, which would halt trading
for no reason. So quantities are summed across both.

Uses requests directly rather than the kiteconnect client, matching fetch_available_capital() in
execution/execution_engine.py -- same auth header, same failure style, no extra dependency.
"""

from typing import Optional

HOLDINGS_URL = "https://api.kite.trade/portfolio/holdings"
POSITIONS_URL = "https://api.kite.trade/portfolio/positions"
DEFAULT_TIMEOUT = 30


def _headers(api_key: str, access_token: str) -> dict:
    return {"X-Kite-Version": "3", "Authorization": f"token {api_key}:{access_token}"}


def _get(url: str, api_key: str, access_token: str, session=None, timeout: int = DEFAULT_TIMEOUT):
    if session is None:
        import requests
        session = requests
    response = session.get(url, headers=_headers(api_key, access_token), timeout=timeout)
    status = getattr(response, "status_code", 0)
    if status != 200:
        raise RuntimeError(f"Kite {url} returned HTTP {status}: {str(getattr(response, 'text', ''))[:300]}")
    body = response.json()
    if not isinstance(body, dict) or "data" not in body:
        raise RuntimeError(f"Kite {url} returned an unexpected shape: {str(body)[:200]}")
    return body["data"]


def _quantity(row: dict) -> float:
    """Net quantity for one row. `quantity` on a holding; on a position the net of the day, which is
    already signed, so a sold-out position reads zero rather than a phantom holding."""
    for field in ("quantity", "net_quantity"):
        if field in row:
            try:
                return float(row.get(field) or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def fetch_holdings(api_key: str, access_token: str, session=None) -> dict:
    """{TRADINGSYMBOL: quantity} across holdings AND today's open positions.

    Summed across both because the same delivery position lives in one on the day it is bought and
    the other from the next settlement -- see the module docstring. Symbols come back as Kite spells
    them (RELIANCE), without the .NS suffix the research side uses; the caller normalises."""
    held = {}
    for row in _get(HOLDINGS_URL, api_key, access_token, session) or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("tradingsymbol", "")).strip().upper()
        if symbol:
            held[symbol] = held.get(symbol, 0.0) + _quantity(row)

    positions = _get(POSITIONS_URL, api_key, access_token, session) or {}
    # Kite returns {"net": [...], "day": [...]}. "net" is the authoritative open position; "day"
    # would double-count what "net" already reflects.
    rows = positions.get("net", []) if isinstance(positions, dict) else positions
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("tradingsymbol", "")).strip().upper()
        if symbol:
            held[symbol] = held.get(symbol, 0.0) + _quantity(row)
    return {k: v for k, v in held.items() if abs(v) > 0}


class KiteHoldingsClient:
    """Adapts fetch_holdings() to the .balances() shape deployment/reconciliation.py expects, so one
    reconciliation rule serves both venues instead of each growing its own."""

    def __init__(self, api_key: str, access_token: str, session=None):
        self._key, self._token, self._session = api_key, access_token, session

    def balances(self) -> list:
        held = fetch_holdings(self._key, self._token, self._session)
        return [{"currency": symbol, "balance": quantity, "locked_balance": 0.0}
                for symbol, quantity in held.items()]
