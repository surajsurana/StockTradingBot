"""
Reads your real, currently-held equity positions from Kite -- the missing
piece that let RiskManager's open-position count and deployed-capital
tracking reset to zero on every run (see risk/risk_manager.py's
seed_existing_positions). Uses Kite's holdings endpoint, which -- like order
placement, and unlike live quotes -- works fine on this account's free
"Personal" tier (see PROJECT_CONTEXT.md's Known Issues #2).

/portfolio/holdings only reflects SETTLED holdings -- Indian equity
delivery settles T+1, so a stock bought today won't appear there until
tomorrow. A production bug came directly from this: monitor_positions.py
called fetch_holdings() alone a few hours after a same-day BUY, saw the
symbol missing from settled holdings, and concluded the position had
closed (logging a fake trade and cancelling nothing only because the GTT
placement had separately failed that day -- if it hadn't, this same gap
would have cancelled a live, working GTT on a position that was still very
much open). fetch_all_holdings() merges settled holdings with same-day CNC
positions from /portfolio/positions so "what do I currently hold" is
correct on the same day a trade happens, not just from the next day on.
"""

from dataclasses import dataclass

import requests


@dataclass
class Holding:
    symbol: str          # yfinance-style, e.g. "INFY.NS" (matches Signal.symbol elsewhere)
    quantity: int
    average_price: float


def fetch_holdings(api_key: str, access_token: str) -> list[Holding]:
    """
    Fetches your current CNC (delivery) holdings from Kite's
    /portfolio/holdings endpoint. Raises clearly on failure (stale token,
    network issue, unexpected response shape) rather than silently
    returning an empty list -- callers must not mistake "couldn't check"
    for "you hold nothing".
    """
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }
    resp = requests.get("https://api.kite.trade/portfolio/holdings", headers=headers)
    result = resp.json()

    if resp.status_code != 200 or "data" not in result:
        raise RuntimeError(
            f"Could not fetch holdings from Kite (status {resp.status_code}): {result}. "
            f"Common cause: KITE_ACCESS_TOKEN is stale -- run refresh_kite_token.py or "
            f"auth.kite_auto_login first."
        )

    holdings = []
    for row in result["data"]:
        quantity = row.get("quantity", 0)
        if quantity <= 0:
            continue  # fully sold/closed positions still show up here with quantity 0
        holdings.append(Holding(
            symbol=f"{row['tradingsymbol']}.NS",
            quantity=quantity,
            average_price=float(row["average_price"]),
        ))
    return holdings


def fetch_same_day_positions(api_key: str, access_token: str) -> list[Holding]:
    """
    Fetches today's CNC (delivery) positions from Kite's /portfolio/positions
    endpoint -- specifically the "net" list, filtered to product == "CNC"
    and a positive net quantity. This is what catches a same-day BUY that
    hasn't settled into /portfolio/holdings yet (see this module's
    docstring for why that gap matters).
    """
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }
    resp = requests.get("https://api.kite.trade/portfolio/positions", headers=headers)
    result = resp.json()

    if resp.status_code != 200 or "data" not in result:
        raise RuntimeError(
            f"Could not fetch positions from Kite (status {resp.status_code}): {result}. "
            f"Common cause: KITE_ACCESS_TOKEN is stale -- run refresh_kite_token.py or "
            f"auth.kite_auto_login first."
        )

    positions = []
    for row in result["data"].get("net", []):
        if row.get("product") != "CNC":
            continue
        quantity = row.get("quantity", 0)
        if quantity <= 0:
            continue
        positions.append(Holding(
            symbol=f"{row['tradingsymbol']}.NS",
            quantity=quantity,
            average_price=float(row["average_price"]),
        ))
    return positions


def fetch_all_holdings(api_key: str, access_token: str) -> list[Holding]:
    """
    What you actually, currently hold -- settled holdings plus any same-day
    CNC buy that hasn't settled yet. This is what every caller that means
    "do I currently hold this symbol" (reconciliation, exclude-already-held
    filtering, RiskManager seeding) should use instead of fetch_holdings()
    alone, which only reflects T+1-settled positions.

    Settled holdings take priority on a symbol that somehow appears in
    both (shouldn't normally happen -- once settled, a symbol moves out of
    same-day positions -- but the settled figure is the authoritative one
    if it ever does).
    """
    holdings = fetch_holdings(api_key, access_token)
    same_day = fetch_same_day_positions(api_key, access_token)

    known_symbols = {h.symbol for h in holdings}
    merged = list(holdings)
    for position in same_day:
        if position.symbol not in known_symbols:
            merged.append(position)
    return merged
