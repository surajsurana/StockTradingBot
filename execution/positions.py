"""
Reads your real, currently-held equity positions from Kite -- the missing
piece that let RiskManager's open-position count and deployed-capital
tracking reset to zero on every run (see risk/risk_manager.py's
seed_existing_positions). Uses Kite's holdings endpoint, which -- like order
placement, and unlike live quotes -- works fine on this account's free
"Personal" tier (see PROJECT_CONTEXT.md's Known Issues #2).

Settlement has THREE states a same-day buy passes through, not two:
1. Trade day (T): shows in /portfolio/positions' "net" list (CNC, positive
   quantity), NOT yet in /portfolio/holdings at all.
2. T+1, before full settlement: shows in /portfolio/holdings, but with
   quantity=0 and the real amount under t1_quantity instead.
3. Fully settled: shows in /portfolio/holdings with quantity=<real amount>,
   t1_quantity=0.

Two separate production bugs came directly from only handling state 3.
State 1 (fetch_holdings() alone, checked hours after a same-day BUY): saw
the symbol missing entirely from settled holdings, concluded it had
closed. State 2 (fetch_holdings() checking `quantity` only, checked the
very next trading day): saw quantity=0 (t1_quantity=15, unread), concluded
the same thing again -- a real position bought Wednesday still showed this
way Thursday morning. Both times: a fake trade got logged and a false
"position closed" Telegram message went out, while the real GTT sat there,
still active, completely unaffected. fetch_holdings() now reads quantity +
t1_quantity together; fetch_all_holdings() additionally merges in
same-day /portfolio/positions so state 1 is covered too.
"""

from dataclasses import dataclass

import requests


@dataclass
class Holding:
    symbol: str          # yfinance-style, e.g. "INFY.NS" (matches Signal.symbol elsewhere)
    quantity: int
    average_price: float
    last_price: float | None = None   # Kite includes this directly in holdings/positions --
                                       # a periodic snapshot, not live-quote-tier data, but
                                       # good enough for a periodic P&L check


def fetch_holdings(api_key: str, access_token: str) -> list[Holding]:
    """
    Fetches your current CNC (delivery) holdings from Kite's
    /portfolio/holdings endpoint. Raises clearly on failure (stale token,
    network issue, unexpected response shape) rather than silently
    returning an empty list -- callers must not mistake "couldn't check"
    for "you hold nothing".

    quantity + t1_quantity, not quantity alone: a stock bought yesterday
    (T) shows up today (T+1) with quantity=0 and t1_quantity=<real amount>
    -- Kite's holdings API distinguishes "fully settled" quantity from
    "T+1, real and yours, not yet fully settled" t1_quantity. A real
    production position (NTPC.NS, bought T, checked T+1) had exactly this
    shape and was wrongly treated as sold/closed because only `quantity`
    was checked -- t1_quantity becomes 0 and rolls into quantity once
    settlement completes, so this covers both states without double-counting.
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
        quantity = row.get("quantity", 0) + row.get("t1_quantity", 0)
        if quantity <= 0:
            continue  # fully sold/closed positions still show up here with quantity 0
        last_price = row.get("last_price")
        holdings.append(Holding(
            symbol=f"{row['tradingsymbol']}.NS",
            quantity=quantity,
            average_price=float(row["average_price"]),
            last_price=float(last_price) if last_price else None,
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
        last_price = row.get("last_price")
        positions.append(Holding(
            symbol=f"{row['tradingsymbol']}.NS",
            quantity=quantity,
            average_price=float(row["average_price"]),
            last_price=float(last_price) if last_price else None,
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
