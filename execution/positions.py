"""
Reads your real, currently-held equity positions from Kite -- the missing
piece that let RiskManager's open-position count and deployed-capital
tracking reset to zero on every run (see risk/risk_manager.py's
seed_existing_positions). Uses Kite's holdings endpoint, which -- like order
placement, and unlike live quotes -- works fine on this account's free
"Personal" tier (see PROJECT_CONTEXT.md's Known Issues #2).
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
