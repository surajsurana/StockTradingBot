"""
Read-only link to your real Groww long-term portfolio, for the dashboard's My Portfolio tab.

WHAT THIS DOES: one GET to Groww's holdings endpoint. It never calls the orders or smart-order
endpoints, never places, changes or cancels anything, and does not use Groww's SDK (plain HTTP).

AUTH (Groww's own docs): a bearer access token, generated in the Groww app under Profile ->
Settings -> Trading APIs -> Generate API keys -> Access token. It EXPIRES DAILY AT 6:00 AM IST, and
the key + secret / TOTP routes also need a daily approval on Groww Cloud, so a fresh token has to be
supplied each day. When it lapses this module keeps showing the last good snapshot and flags it as
expired rather than showing nothing.

FILES (both under deployment/state/, both git-ignored -- they hold a credential and your holdings):
  groww_token.txt      the access token, one line
  groww_holdings.json  the last good snapshot plus the connection status

Holdings carry quantity and average cost only (live prices are Groww's paid data API), so the
dashboard prices them itself from the same feeds it already uses for the paper pools.
"""

import json
import os
from datetime import datetime
from typing import Callable, Optional

import requests

HOLDINGS_URL = "https://api.groww.in/v1/holdings/user"
TOKEN_FILE = "groww_token.txt"
SNAPSHOT_FILE = "groww_holdings.json"


class GrowwAuthError(Exception):
    """The token is missing, expired or rejected."""


def read_token(state_dir: str) -> Optional[str]:
    path = os.path.join(state_dir, TOKEN_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        token = f.read().strip()
    return token or None


def fetch_holdings(token: str, timeout: int = 20) -> list:
    """The account's current holdings as [{symbol, isin, quantity, avg_price}] (quantity is the net
    held; average cost in rupees). Raises GrowwAuthError on a rejected token."""
    resp = requests.get(HOLDINGS_URL, timeout=timeout, headers={
        "Accept": "application/json", "Authorization": f"Bearer {token}", "X-API-VERSION": "1.0"})
    if resp.status_code in (401, 403):
        raise GrowwAuthError(f"Groww rejected the token (HTTP {resp.status_code})")
    resp.raise_for_status()
    body = resp.json()
    raw = body if isinstance(body, list) else ((body.get("payload") or {}).get("holdings") if isinstance(body.get("payload"), dict)
                                              else body.get("holdings"))
    out = []
    for h in raw or []:
        qty = float(h.get("quantity", 0) or 0)
        symbol = str(h.get("trading_symbol") or "").strip()
        if symbol and qty > 0:
            out.append({"symbol": symbol, "isin": h.get("isin"), "quantity": qty, "avg_price": float(h.get("average_price", 0) or 0)})
    return out


def load_snapshot(state_dir: str) -> dict:
    path = os.path.join(state_dir, SNAPSHOT_FILE)
    if not os.path.exists(path):
        return {"status": "not_connected", "holdings": [], "fetched_at": None, "message": ""}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(state_dir: str, snap: dict) -> None:
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, SNAPSHOT_FILE), "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1)


def sync_holdings(state_dir: str, fetch_fn: Optional[Callable[[str], list]] = None, now: Optional[datetime] = None) -> dict:
    """Refresh the snapshot. Status: not_connected (no token file), connected, expired (token
    rejected: last holdings kept), error (Groww unreachable or odd reply: last holdings kept).
    The message never contains the token."""
    now = now or datetime.now()
    previous = load_snapshot(state_dir)
    token = read_token(state_dir)
    if not token:
        snap = {"status": "not_connected", "holdings": previous.get("holdings", []), "fetched_at": previous.get("fetched_at"), "message": "No Groww token yet."}
    else:
        try:
            holdings = (fetch_fn or fetch_holdings)(token)
            snap = {"status": "connected", "holdings": holdings, "fetched_at": now.isoformat(timespec="seconds"), "message": ""}
        except GrowwAuthError as e:
            snap = {"status": "expired", "holdings": previous.get("holdings", []), "fetched_at": previous.get("fetched_at"), "message": str(e)}
        except Exception as e:
            snap = {"status": "error", "holdings": previous.get("holdings", []), "fetched_at": previous.get("fetched_at"),
                    "message": f"{type(e).__name__} while reaching Groww"}
    _save(state_dir, snap)
    return snap
