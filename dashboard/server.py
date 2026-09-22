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
thread does a full yfinance refresh every PRICE_REFRESH_SECONDS and, while
the market is open, a Kite last-traded-price refresh for every held
symbol every LIVE_SECONDS (added 2026-09-11 so the page genuinely moves
with the tape -- real quotes, never simulated movement). Crypto's last-
traded price (Binance, refresh_crypto()) refreshes every LIVE_SECONDS
ALWAYS, not gated on NSE hours at all -- crypto trades round the clock,
so tying its cadence to whether the Indian equity market happened to be
open (the loop used to sleep 60s instead of LIVE_SECONDS whenever NSE was
shut, which slowed crypto down for no reason of its own) was a bug, fixed
2026-09-17. The page shows what it has and says how old it is. The state
itself (cash, positions, trades) is re-read from disk on every request,
so it is always current to the last run/tick.

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
from data.fetch_groww import load_snapshot as load_groww_snapshot   # noqa: E402


def _advice_extra():
    from advice.screener import load_screener
    from advice.track import load_log
    return {"screener": load_screener(STATE_DIR), "log": load_log(STATE_DIR)}


def _advice_results():
    from advice.results import load_results
    return load_results(STATE_DIR)


def _advice_done():
    from advice.tasks import load_done
    return load_done(STATE_DIR)


def _advice_params(query: dict) -> dict:
    """What-if inputs from the Advice tab (monthly amount, yearly step-up, this month's deposit, return overrides)."""
    out = {}
    for key, lo, hi in (("monthly", 0, 10_000_000), ("deposit", 0, 10_000_000), ("stepup", 0, 50), ("low", -10, 40), ("base", -10, 40), ("high", -10, 40)):
        try:
            v = float(query.get(key, [""])[0])
        except ValueError:
            continue
        if lo <= v <= hi:
            out[key] = v
    return out


def _load_reports(state_dir: str):
    """The return-report data built from the downloaded Groww reports, or None if it has not been built."""
    try:
        with open(os.path.join(state_dir, "groww_reports.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


LOGS_DIR = os.path.join(REPO_DIR, "logs")
INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
KEY_PATH = os.path.join(STATE_DIR, "dashboard_key.txt")
PRICE_REFRESH_SECONDS = 300   # full yfinance refresh
GROWW_REFRESH_SECONDS = 600   # re-read the Groww holdings (a read-only call)
LIVE_SECONDS = 20             # Kite quote refresh while the market is open (2 requests per pass)
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
        self.crypto_prices = {}
        self.usdinr = None
        self.prev_close = {}          # symbol -> last close BEFORE today (for "today's move" on open positions)
        self.crypto_prev_close = {}   # coin -> last completed UTC daily close
        self._lock = threading.Lock()
        self._saved_at = 0.0
        self._load_disk()

    # The quotes are also kept on disk so a restart of the dashboard starts from the last good prices. With an
    # empty cache every open position is valued at cost until the first refresh finishes (about a minute), which
    # showed unrealised P&L as 0 and totals that jumped for a minute after every restart.
    CACHE_FILE = "price_cache.json"
    CACHE_MAX_AGE_HOURS = 72

    def _load_disk(self) -> None:
        import json
        path = os.path.join(self.state_dir, self.CACHE_FILE)
        try:
            if time.time() - os.path.getmtime(path) > self.CACHE_MAX_AGE_HOURS * 3600:
                return
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            self.prices, self.prev_close = dict(d.get("prices", {})), dict(d.get("prev_close", {}))
            self.crypto_prices, self.crypto_prev_close = dict(d.get("crypto_prices", {})), dict(d.get("crypto_prev_close", {}))
            self.usdinr, self.as_of = d.get("usdinr"), d.get("as_of")
        except (OSError, ValueError):
            pass

    def _save_disk(self, force: bool = False) -> None:
        import json
        if not force and time.monotonic() - self._saved_at < 15:
            return
        self._saved_at = time.monotonic()
        with self._lock:
            payload = {"as_of": self.as_of, "prices": self.prices, "prev_close": self.prev_close, "crypto_prices": self.crypto_prices,
                       "crypto_prev_close": self.crypto_prev_close, "usdinr": self.usdinr}
            text = json.dumps(payload)
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            tmp = os.path.join(self.state_dir, self.CACHE_FILE + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, os.path.join(self.state_dir, self.CACHE_FILE))
        except OSError:
            pass

    def _held_symbols(self) -> list:
        from reporting.pool_summary import _held_symbols
        from data.fetch_groww import load_snapshot
        groww = {f"{h['symbol']}.NS" for h in load_snapshot(self.state_dir).get("holdings", [])}   # priced like any held stock
        return sorted(_held_symbols(self.state_dir) | groww)

    def _pool_d_symbols(self) -> list:
        import json
        path = os.path.join(self.state_dir, "pool_d", "portfolio.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return sorted((json.load(f).get("positions") or {}).keys())

    def _pool_e_symbols(self) -> list:
        import glob
        import json
        held = set()
        for pool_dir in ("pool_e", "pool_e1"):   # pool_e1 (2026-09-22): Pool E's partial-booking twin
            for path in glob.glob(os.path.join(self.state_dir, pool_dir, "*", "portfolio.json")):
                with open(path, encoding="utf-8") as f:
                    held |= set((json.load(f).get("positions") or {}).keys())
        pool_g_path = os.path.join(self.state_dir, "pool_g", "portfolio.json")
        if os.path.exists(pool_g_path):
            with open(pool_g_path, encoding="utf-8") as f:
                held |= set((json.load(f).get("positions") or {}).keys())
        return sorted(held)

    _groww_last = 0.0

    def refresh_groww(self) -> None:
        """Re-read the real Groww holdings (read-only) at most every GROWW_REFRESH_SECONDS."""
        if time.monotonic() - self._groww_last < GROWW_REFRESH_SECONDS:
            return
        self._groww_last = time.monotonic()
        from data.fetch_groww import sync_holdings
        sync_holdings(self.state_dir)

    def refresh_crypto(self, with_rate: bool = False) -> None:
        """Binance last prices for Pool E's open coins (one cheap call; the
        coins trade 24x7 so this runs on every loop pass) and, on the full
        refresh, the USD/INR rate."""
        from data.fetch_crypto import fetch_crypto_last_prices, fetch_usdinr_rate
        symbols = self._pool_e_symbols()
        quotes = fetch_crypto_last_prices(symbols) if symbols else {}
        rate = fetch_usdinr_rate() if (with_rate or self.usdinr is None) else None
        prev = {}
        if with_rate and symbols:   # once per full refresh: the last completed UTC daily close per held coin
            from datetime import date as _date, timedelta
            from data.fetch_crypto import fetch_binance_daily
            for sym in symbols:
                try:
                    df = fetch_binance_daily(sym, start=_date.today() - timedelta(days=4))
                    if not df.empty:
                        prev[sym] = float(df["Close"].iloc[-1])
                except Exception:
                    pass
        with self._lock:
            if symbols and quotes:
                self.crypto_prices = {**{k: v for k, v in self.crypto_prices.items() if k in symbols}, **quotes}
            if rate:
                self.usdinr = rate
            if prev:
                self.crypto_prev_close = {**{k: v for k, v in self.crypto_prev_close.items() if k in symbols}, **prev}
        self._save_disk()

    _kite_headers = None
    _kite_headers_day = None

    def _kite_ohlc(self, symbols: list) -> dict:
        """Real-time last traded price AND the exchange's own previous close, for Pool D's bare
        symbols and every held swing/Pool H symbol, via Kite (one login per day, cached; a 403
        forces a re-login next time). Falls back to {} so a Kite hiccup never blocks the swing
        prices -- prices and prev_close just keep whatever yfinance/the last refresh already had.
        Was _kite_ltp (last_price only) until 2026-09-22: see fetch_kite_intraday.fetch_ohlc's
        docstring for why yfinance's own daily close isn't trustworthy enough for "yesterday's close"
        on its own."""
        from config import settings
        from data.fetch_kite_intraday import fetch_ohlc, get_market_data_session
        today = datetime.now().date()
        try:
            if self._kite_headers is None or self._kite_headers_day != today:
                self._kite_headers = get_market_data_session(settings)
                self._kite_headers_day = today
            return fetch_ohlc(symbols, self._kite_headers)
        except Exception as e:
            print(f"Kite OHLC failed ({type(e).__name__}: {e}) -- re-login on next refresh", flush=True)
            self._kite_headers = None
            return {}

    def refresh_once(self) -> None:
        """Full refresh: yfinance closes for every held swing symbol, then
        Kite quotes on top for everything Kite knows (bare symbols)."""
        from data.fetch_historical import fetch_all
        fresh, prev = {}, {}
        symbols = self._held_symbols()
        today = datetime.now().date()
        if symbols:
            for symbol, df in fetch_all(symbols, period="5d").items():
                if df is not None and not df.empty:
                    fresh[symbol] = float(df["Close"].iloc[-1])
                    before_today = df[df.index.date < today]
                    if not before_today.empty:
                        prev[symbol] = float(before_today["Close"].iloc[-1])
        with self._lock:
            held = set(symbols)
            self.prices = {**{k: v for k, v in self.prices.items() if k in held}, **fresh}
            self.prev_close = {**{k: v for k, v in self.prev_close.items() if k in held}, **prev}
            if fresh:
                self.as_of = datetime.now().isoformat(timespec="seconds")
        self.refresh_live()
        try:
            self.refresh_crypto(with_rate=True)
        except Exception as e:
            print(f"crypto price refresh failed: {type(e).__name__}: {e}", flush=True)

    def refresh_live(self) -> None:
        """Quote refresh: Kite last-traded price AND previous close for every held symbol (swing
        books' 'X.NS' keys map to Kite's bare 'X') plus Pool D's open names. During market hours this
        runs every LIVE_SECONDS, so unbooked P&L and "today's" move both track the live tape; outside
        it, it just confirms the close. Symbols Kite doesn't return keep their yfinance price/close
        (Kite's own OHLC is the authoritative previous-close source as of 2026-09-22 -- see
        fetch_kite_intraday.fetch_ohlc's docstring -- so this is also what fixes "today's P&L" once
        Kite has a symbol, not just the live price)."""
        held = self._held_symbols() + self._pool_d_symbols()
        if not held:
            return
        bare = sorted({s[:-3] if s.endswith(".NS") else s for s in held})
        quotes = self._kite_ohlc(bare)
        if not quotes:
            return
        with self._lock:
            for s in held:
                b = s[:-3] if s.endswith(".NS") else s
                if b in quotes:
                    self.prices[s] = quotes[b]["price"]
                    self.prev_close[s] = quotes[b]["prev_close"]
            self.as_of = datetime.now().isoformat(timespec="seconds")
        self._save_disk()

    def snapshot(self) -> tuple:
        with self._lock:
            return dict(self.prices), self.as_of

    def crypto_snapshot(self) -> tuple:
        with self._lock:
            return dict(self.crypto_prices), self.usdinr

    def prev_close_snapshot(self) -> tuple:
        with self._lock:
            return dict(self.prev_close), dict(self.crypto_prev_close)

    @staticmethod
    def _market_open(now=None) -> bool:
        now = now or datetime.now()
        return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (15, 30)

    def start(self) -> None:
        def loop():
            last_full = 0.0
            while True:
                try:
                    self.refresh_groww()
                    if time.monotonic() - last_full > self.refresh_seconds:
                        self.refresh_once()
                        last_full = time.monotonic()
                    else:
                        if self._market_open():
                            self.refresh_live()
                        self.refresh_crypto()
                except Exception as e:   # never let a data hiccup kill the refresher
                    print(f"price refresh failed: {type(e).__name__}: {e}", flush=True)
                # ALWAYS LIVE_SECONDS, never a slower fallback tied to NSE hours -- crypto's own
                # refresh_crypto() call above runs every iteration regardless of market hours, and
                # crypto trades around the clock, so this loop must not slow down just because the
                # Indian equity market happens to be shut (see module docstring, fixed 2026-09-17).
                time.sleep(LIVE_SECONDS)
        threading.Thread(target=loop, daemon=True, name="price-refresh").start()


class RoadmapCache:
    """swing_research/research_roadmap.py's build_roadmap() re-scores 30+
    candidates against the registry; cheap, but not per request."""

    def __init__(self, ttl_seconds: int = 600):
        self.ttl = ttl_seconds
        self._value, self._at = None, 0.0
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            if self._value is None or time.monotonic() - self._at > self.ttl:
                try:
                    from swing_research.research_roadmap import build_roadmap
                    self._value = build_roadmap()
                except Exception as e:
                    print(f"roadmap unavailable: {type(e).__name__}: {e}", flush=True)
                    self._value = None
                self._at = time.monotonic()
            return self._value


class DashboardHandler(BaseHTTPRequestHandler):
    price_cache: PriceCache = None
    roadmap_cache: RoadmapCache = RoadmapCache()
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

    def do_POST(self):
        """Manual sell from the page (dashboard/manual_exit.py). Only the
        paper books; 'market' resolves to the latest cached quote."""
        parsed = urlparse(self.path)
        if not is_authorized(parse_qs(parsed.query), self.headers.get("Cookie", ""), self.access_key):
            self._send(HTTPStatus.FORBIDDEN, b'{"error": "forbidden"}', "application/json")
            return
        if parsed.path == "/api/advice/done":     # the Advice page's "Done" button: remember a task was completed
            from advice.tasks import mark_done
            try:
                length = int(self.headers.get("Content-Length", "0"))
                mark_done(STATE_DIR, str(json.loads(self.rfile.read(length) or b"{}").get("id", "")))
                self._send(HTTPStatus.OK, b'{"ok": true}', "application/json")
            except (ValueError, OSError) as e:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"), "application/json")
            return
        if parsed.path == "/api/research/start":   # Strategies tab's "Start research" button: jump the queue
            import research_queue
            try:
                length = int(self.headers.get("Content-Length", "0"))
                key = str(json.loads(self.rfile.read(length) or b"{}").get("key", ""))
                roadmap = self.roadmap_cache.get()
                if not roadmap:
                    raise ValueError("the research roadmap isn't available right now")
                research_queue.start_now(STATE_DIR, key, roadmap)
                self._send(HTTPStatus.OK, b'{"ok": true}', "application/json")
            except (ValueError, OSError) as e:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"), "application/json")
            return
        if parsed.path == "/api/research/started":   # the research routine calls this the instant it commits
            # to a candidate -- locks the queue so advance()/start_now() can no longer bump it (2026-09-22:
            # "interrupting and changing mid week or anytime is ok, only if a research is already ongoing
            # then it should not interrupt").
            import research_queue
            try:
                length = int(self.headers.get("Content-Length", "0"))
                key = str(json.loads(self.rfile.read(length) or b"{}").get("key", ""))
                research_queue.mark_in_progress(STATE_DIR, key)
                self._send(HTTPStatus.OK, b'{"ok": true}', "application/json")
            except (ValueError, OSError) as e:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"), "application/json")
            return
        if parsed.path == "/api/research/resolve":   # the unattended research routine (Phase 2, a scheduled
            # cloud agent with no VPS/SSH access) calls this over the internet once it finishes the current
            # candidate, since it can't write deployment/state/research_queue.json directly.
            import research_queue
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                research_queue.resolve(STATE_DIR, str(body.get("key", "")), str(body.get("outcome", "")),
                                       experiment_id=body.get("experiment_id"), branch=body.get("branch"))
                self._send(HTTPStatus.OK, b'{"ok": true}', "application/json")
            except (ValueError, OSError) as e:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"), "application/json")
            return
        if parsed.path not in ("/api/manual_exit", "/api/manual_entry"):
            self._send(HTTPStatus.NOT_FOUND, b'{"error": "not found"}', "application/json")
            return
        from dashboard.manual_exit import ManualExitError, apply_manual_entry, apply_manual_exit
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            pool, book = str(body.get("pool", "")).replace("Pool ", "").strip(), body.get("book") or None
            symbol, quantity = str(body.get("symbol", "")), float(body.get("quantity", 0))
            mode = "market" if body.get("price_mode") == "market" else "manual"
            if mode == "market":
                prices, _ = self.price_cache.snapshot()
                crypto, usdinr = self.price_cache.crypto_snapshot()
                price = crypto.get(symbol) if pool == "E" else prices.get(symbol, prices.get(symbol.replace(".NS", "")))
                if not price:
                    raise ManualExitError(f"no live quote for {symbol} right now -- enter a price manually")
            else:
                price = float(body.get("price", 0))
            if parsed.path == "/api/manual_entry":
                trade = apply_manual_entry(STATE_DIR, pool, book, symbol, quantity, price, price_mode=mode)
            else:
                trade = apply_manual_exit(STATE_DIR, pool, book, symbol, quantity, price, price_mode=mode)
            self._send(HTTPStatus.OK, json.dumps({"ok": True, "trade": trade}).encode("utf-8"), "application/json")
        except ManualExitError as e:
            self._send(HTTPStatus.CONFLICT, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"), "application/json")
        except Exception as e:   # a bad body or an unexpected state file -- report, never crash the server
            self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}).encode("utf-8"),
                       "application/json")

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
            # mode=live is a VIEW of deployment/state/live/ (the same layout
            # as the paper folders) -- it exists so the page's Live/Paper
            # switch has somewhere to point once real-money books exist.
            # Nothing here places orders; with no live folder the view is
            # simply empty.
            mode = "live" if query.get("mode", ["paper"])[0] == "live" else "paper"
            state_root = os.path.join(STATE_DIR, "live") if mode == "live" else STATE_DIR
            prices, as_of = self.price_cache.snapshot()
            crypto_prices, usdinr = self.price_cache.crypto_snapshot()
            prev_close, crypto_prev_close = self.price_cache.prev_close_snapshot()
            registry = list_strategies()
            import research_queue
            state = build_dashboard_state(state_root, LOGS_DIR, registry, prices, as_of,
                                          roadmap=self.roadmap_cache.get(), mode=mode,
                                          research_queue=research_queue.load(STATE_DIR),
                                          crypto_prices=crypto_prices, usdinr=usdinr,
                                          prev_close=prev_close, crypto_prev_close=crypto_prev_close,
                                          groww=load_groww_snapshot(STATE_DIR) if mode == "live" else None,
                                          reports=_load_reports(STATE_DIR) if mode == "live" else None,
                                          advice_params=_advice_params(query) if mode == "live" else None,
                                          advice_done=_advice_done() if mode == "live" else None,
                                          advice_results=_advice_results() if mode == "live" else None,
                                          advice_extra=_advice_extra() if mode == "live" else None)
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
