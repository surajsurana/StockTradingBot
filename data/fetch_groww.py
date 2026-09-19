"""
Read-only link to your real Groww long-term portfolio, for the dashboard's My Portfolio tab.

WHAT THIS DOES: one GET to Groww's holdings endpoint. It never calls the orders or smart-order
endpoints, never places, changes or cancels anything, and does not use Groww's SDK (plain HTTP).

AUTH: Groww's API uses a bearer access token that EXPIRES DAILY AT 6:00 AM IST. Two ways to get one:
  automatic (preferred)  a one-time "TOTP" key pair from Groww Cloud's API Keys page: a TOTP token and
                         a TOTP secret. Per Groww's Python SDK page this route has no expiry, so this
                         module mints a fresh access token itself (POST /v1/token/api/access with a
                         6-digit code computed from the secret) -- no daily action from you.
  manual (fallback)      an access token pasted from Groww's Trading APIs settings, valid until 6 AM.
(One page of Groww's curl docs says the TOTP route also needs a daily approval; their SDK page and
several independent implementations say it does not. The first real run settles it: if it does need
approval the status reads "auth_failed" and holdings stay at the last good snapshot.)
Minted tokens are cached and reused until the next 6 AM IST; the token endpoint is limited to 150
calls a day, so a mint is attempted at most once every 5 minutes and only when needed.
When anything fails the last good snapshot stays on screen, labelled with how old it is.

FILES (all under deployment/state/, all git-ignored -- they hold credentials and your holdings):
  groww_totp.json      {"totp_token": "...", "totp_secret": "..."}  (automatic route)
  groww_token.txt      a manually pasted access token, one line       (fallback)
  groww_access.json    the minted access token and when it was minted
  groww_holdings.json  the last good snapshot plus the connection status

Holdings carry quantity and average cost only (live prices are Groww's paid data API), so the
dashboard prices them itself from the same feeds it already uses for the paper pools.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import requests

HOLDINGS_URL = "https://api.groww.in/v1/holdings/user"
TOKEN_URL = "https://api.groww.in/v1/token/api/access"
TOKEN_FILE = "groww_token.txt"
CREDS_FILE = "groww_totp.json"
ACCESS_FILE = "groww_access.json"
SNAPSHOT_FILE = "groww_holdings.json"
IST = timezone(timedelta(hours=5, minutes=30))
MIN_SECONDS_BETWEEN_MINTS = 300


class GrowwAuthError(Exception):
    """The token or TOTP credentials are missing, expired or rejected."""


def read_token(state_dir: str) -> Optional[str]:
    path = os.path.join(state_dir, TOKEN_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        token = f.read().strip()
    return token or None


def read_credentials(state_dir: str) -> Optional[dict]:
    path = os.path.join(state_dir, CREDS_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        creds = json.load(f)
    return creds if creds.get("totp_token") and creds.get("totp_secret") else None


def mint_access_token(totp_token: str, totp_secret: str, timeout: int = 20) -> str:
    """Exchange the TOTP key pair for today's access token. Raises GrowwAuthError if Groww rejects
    the key or code, RuntimeError if the reply carries no token."""
    import pyotp
    resp = requests.post(TOKEN_URL, timeout=timeout, json={"key_type": "totp", "totp": pyotp.TOTP(totp_secret).now()}, headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": f"Bearer {totp_token}", "X-API-VERSION": "1.0"})
    if resp.status_code in (401, 403):
        raise GrowwAuthError(f"Groww rejected the TOTP credentials (HTTP {resp.status_code})")
    resp.raise_for_status()
    body = resp.json()
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    token = body.get("token") or body.get("access_token") or payload.get("token") or payload.get("access_token")
    if not token:
        raise RuntimeError("Groww's token reply had no token")
    return str(token)


def _last_6am_ist(now_epoch: float) -> float:
    now = datetime.fromtimestamp(now_epoch, IST)
    cutoff = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now < cutoff:
        cutoff -= timedelta(days=1)
    return cutoff.timestamp()


def get_token(state_dir: str, force: bool = False, now_epoch: Optional[float] = None,
              mint_fn: Optional[Callable[[str, str], str]] = None) -> Optional[str]:
    """The access token to use: a cached minted one while still valid (minted since the last 6 AM
    IST), else a fresh mint from the TOTP key pair, else the manually pasted token, else None.
    Mint attempts are spaced at least MIN_SECONDS_BETWEEN_MINTS apart. Raises GrowwAuthError if
    minting is needed and Groww rejects the credentials."""
    now_epoch = now_epoch if now_epoch is not None else time.time()
    creds = read_credentials(state_dir)
    if not creds:
        return read_token(state_dir)
    path = os.path.join(state_dir, ACCESS_FILE)
    cache = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    if not force and cache.get("token") and cache.get("minted_at", 0) >= _last_6am_ist(now_epoch):
        return cache["token"]
    if now_epoch - cache.get("attempted_at", 0) < MIN_SECONDS_BETWEEN_MINTS:
        raise GrowwAuthError("Groww token was refused or is being rate-limited; retrying shortly")
    os.makedirs(state_dir, exist_ok=True)
    cache["attempted_at"] = now_epoch
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    token = (mint_fn or mint_access_token)(creds["totp_token"], creds["totp_secret"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"token": token, "minted_at": now_epoch, "attempted_at": now_epoch}, f)
    return token


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


def sync_holdings(state_dir: str, fetch_fn: Optional[Callable[[str], list]] = None, now: Optional[datetime] = None,
                  mint_fn: Optional[Callable[[str, str], str]] = None, now_epoch: Optional[float] = None) -> dict:
    """Refresh the snapshot. Status: not_connected (no credentials at all), connected, expired (a
    manual token was rejected), auth_failed (the TOTP key pair was rejected), error (Groww
    unreachable or an odd reply). Last holdings are kept on every failure. If a cached minted token
    is rejected it is re-minted once and the fetch retried. The message never contains a token or
    secret."""
    now = now or datetime.now()
    previous = load_snapshot(state_dir)
    fetch = fetch_fn or fetch_holdings

    def result(status, message="", holdings=None, fetched_at=None):
        snap = {"status": status, "holdings": previous.get("holdings", []) if holdings is None else holdings,
                "fetched_at": previous.get("fetched_at") if fetched_at is None else fetched_at, "message": message}
        _save(state_dir, snap)
        return snap

    automatic = read_credentials(state_dir) is not None
    try:
        token = get_token(state_dir, now_epoch=now_epoch, mint_fn=mint_fn)
        if not token:
            return result("not_connected", "No Groww credentials yet.")
        try:
            holdings = fetch(token)
        except GrowwAuthError:
            if not automatic:
                raise
            holdings = fetch(get_token(state_dir, force=True, now_epoch=now_epoch, mint_fn=mint_fn))
        return result("connected", "", holdings, now.isoformat(timespec="seconds"))
    except GrowwAuthError as e:
        return result("auth_failed" if automatic else "expired", str(e))
    except Exception as e:
        return result("error", f"{type(e).__name__} while reaching Groww")
