"""
The live dashboard: a small stdlib HTTP server (no new dependencies --
the VPS venv has no Flask, and no sudo for a system service) that serves
dashboard/index.html and a JSON view of the whole paper-trading estate
(dashboard/state_view.py). Meant to be reached at http://<vps-ip>:8085/.

ACCESS KEY: if deployment/state/dashboard_key.txt exists, every request
must carry that key -- as ?key=... on the first visit (the server then
sets a cookie so later visits need nothing) -- otherwise 403. The page
shows paper-trading state only, never credentials, but a bare IP on the
open internet gets scanned, so the key keeps casual visitors out. Plain
HTTP: do not reuse any real password as the key.

PRICES: unrealised P&L needs a latest price per held symbol. A background
thread refreshes them from yfinance every PRICE_REFRESH_SECONDS; the
page shows what it has and says how old it is. The state itself (cash,
positions, trades) is re-read from disk on every request, so it is always
current to the last run/tick.

    python dashboard/server.py            # 0.0.0.0:8085
    python dashboard/server.py --port N

Kept alive by cron (@reboot + a 5-minute watchdog), see the crontab.
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_DIR)

from deployment.deployment_manager import list_strategies   # noqa: E402
from deployment.settings import STATE_DIR                   # noqa: E402
from dashboard.state_view import build_dashboard_state             # noqa: E402

LOGS_DIR = os.path.join(REPO_DIR, "logs")
INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
KEY_PATH = os.path.join(STATE_DIR, "dashboard_key.txt")
PRICE_REFRESH_SECONDS = 300
COOKIE_NAME = "dash_key"


def load_access_key() -> str:
    if not os.path.exists(KEY_PATH):
        return ""
    with open(KEY_PATH, encoding="utf-8") as f:
        return f.read().strip()


def is_authorized(query: dict, cookie_header: str, access_key: str) -> bool:
    """No key configured -> open. Otherwise the key must arrive as ?key= or
    as the dash_key cookie set after a successful ?key= visit."""
    if not access_key:
        return True
    if query.get("key", [""])[0] == access_key:
        return True
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME and value == access_key:
            return True
    return False


class PriceCache:
    """Latest price per held symbol, refreshed in the background."""

    def __init__(self, state_dir: str, refresh_seconds: int = PRICE_REFRESH_SECONDS):
        self.state_dir = state_dir
        self.refresh_seconds = refresh_seconds
        self.prices = {}
        self.as_of = None
        self._lock = threading.Lock()

    def _held_symbols(self) -> list:
        from reporting.pool_summary import _held_symbols
        return sorted(_held_symbols(self.state_dir))

    def refresh_once(self) -> None:
        from data.fetch_historical import fetch_all
        symbols = self._held_symbols()
        if not symbols:
            with self._lock:
                self.prices, self.as_of = {}, datetime.now().isoformat(timespec="minutes")
            return
        fresh = {}
        for symbol, df in fetch_all(symbols, period="5d").items():
            if df is not None and not df.empty:
                fresh[symbol] = float(df["Close"].iloc[-1])
        with self._lock:
            self.prices = fresh
            self.as_of = datetime.now().isoformat(timespec="minutes")

    def snapshot(self) -> tuple:
        with self._lock:
            return dict(self.prices), self.as_of

    def start(self) -> None:
        def loop():
            while True:
                try:
                    self.refresh_once()
                except Exception as e:   # never let a yfinance hiccup kill the refresher
                    print(f"price refresh failed: {type(e).__name__}: {e}", flush=True)
                time.sleep(self.refresh_seconds)
        threading.Thread(target=loop, daemon=True, name="price-refresh").start()


class DashboardHandler(BaseHTTPRequestHandler):
    price_cache: PriceCache = None
    access_key: str = ""

    def log_message(self, fmt, *args):   # quieter than the default (one line per request is enough)
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _send(self, status: int, body: bytes, content_type: str, extra_headers: dict = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not is_authorized(query, self.headers.get("Cookie", ""), self.access_key):
            self._send(HTTPStatus.FORBIDDEN, b"Forbidden: add ?key=<access key> to the URL once.", "text/plain")
            return
        extra = {}
        if self.access_key and query.get("key", [""])[0] == self.access_key:
            extra["Set-Cookie"] = f"{COOKIE_NAME}={self.access_key}; Path=/; Max-Age=2592000; SameSite=Lax"

        if parsed.path == "/api/state":
            prices, as_of = self.price_cache.snapshot()
            state = build_dashboard_state(STATE_DIR, LOGS_DIR, list_strategies(), prices, as_of)
            self._send(HTTPStatus.OK, json.dumps(state).encode("utf-8"), "application/json", extra)
            return
        if parsed.path in ("/", "/index.html"):
            with open(INDEX_PATH, "rb") as f:
                self._send(HTTPStatus.OK, f.read(), "text/html; charset=utf-8", extra)
            return
        self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8085)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    DashboardHandler.access_key = load_access_key()
    DashboardHandler.price_cache = PriceCache(STATE_DIR)
    DashboardHandler.price_cache.start()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard on http://{args.host}:{args.port}/ "
          f"({'access key required' if DashboardHandler.access_key else 'OPEN -- no access key configured'})",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
