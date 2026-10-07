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
import ssl
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


def _open_live_positions(strategy_key: str) -> int:
    """How many live positions a strategy currently holds. Capital may not be reassigned while it
    holds any (see live_allocations.set_allocation). There are no live books yet, so this reads 0
    today -- but it reads the real file rather than returning a constant, so it keeps telling the
    truth once the live executor exists. Unreadable counts as "holds positions", refusing the
    change, because sizing the rest of a position off new capital is the thing being prevented."""
    path = os.path.join(STATE_DIR, "live", "paper_trading", strategy_key, "portfolio.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            return len(json.load(f).get("positions") or {})
    except (OSError, ValueError, AttributeError):
        return 1


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
CONFIG_DIR = os.path.join(REPO_DIR, "config")
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
        self.us_prices = {}           # Pool I's open US symbols -> latest yfinance close
        self.us_prev_close = {}       # Pool I -> last close before today
        self.macro = {}               # Markets tab -- indices/gold/silver/crude/currency pairs, {name: {price, change_pct}}
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
            self.us_prices, self.us_prev_close = dict(d.get("us_prices", {})), dict(d.get("us_prev_close", {}))
            self.macro = dict(d.get("macro", {}))
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
                       "crypto_prev_close": self.crypto_prev_close, "usdinr": self.usdinr,
                       "us_prices": self.us_prices, "us_prev_close": self.us_prev_close, "macro": self.macro}
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

    def _pool_i_symbols(self) -> list:
        import glob
        import json
        held = set()
        for path in glob.glob(os.path.join(self.state_dir, "pool_i", "*", "portfolio.json")):
            with open(path, encoding="utf-8") as f:
                held |= set((json.load(f).get("positions") or {}).keys())
        return sorted(held)

    def refresh_us_equity(self) -> None:
        """yfinance closes for Pool I's open US symbols -- EOD cadence only
        (Pool I trades once a day like every swing book; no Kite coverage of
        US exchanges exists, so there's no live intraday tier here the way
        Pool D/swing have via refresh_live())."""
        from data.fetch_historical import fetch_all
        symbols = self._pool_i_symbols()
        if not symbols:
            return
        today = datetime.now().date()
        fresh, prev = {}, {}
        for symbol, df in fetch_all(symbols, period="5d").items():
            if df is not None and not df.empty:
                fresh[symbol] = float(df["Close"].iloc[-1])
                before_today = df[df.index.date < today]
                if not before_today.empty:
                    prev[symbol] = float(before_today["Close"].iloc[-1])
        with self._lock:
            held = set(symbols)
            self.us_prices = {**{k: v for k, v in self.us_prices.items() if k in held}, **fresh}
            self.us_prev_close = {**{k: v for k, v in self.us_prev_close.items() if k in held}, **prev}
        self._save_disk()

    def refresh_macro(self) -> None:
        """Markets tab quotes: one representative index/rate per market we trade
        (MARKET_INDEX_TICKERS) plus gold/silver/crude/major currency pairs for the
        broader backdrop (MACRO_INSTRUMENTS, not something any pool trades
        directly). Same cadence as the full refresh (refresh_once()); a quote
        that fails to fetch just keeps its last known value rather than
        disappearing."""
        from dashboard.state_view import MACRO_INSTRUMENTS, MARKET_INDEX_TICKERS
        import yfinance as yf
        quotes = {}
        for name, ticker in {**MARKET_INDEX_TICKERS, **MACRO_INSTRUMENTS}.items():
            try:
                hist = yf.Ticker(ticker).history(period="5d")
                if hist is not None and not hist.empty:
                    last = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
                    quotes[name] = {"price": last, "change_pct": round((last / prev - 1) * 100, 2) if prev else None}
            except Exception as e:
                print(f"macro quote failed for {name} ({ticker}): {type(e).__name__}: {e}", flush=True)
        with self._lock:
            self.macro = {**self.macro, **quotes}
        self._save_disk()

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
        try:
            self.refresh_us_equity()
        except Exception as e:
            print(f"US equity price refresh failed: {type(e).__name__}: {e}", flush=True)
        try:
            self.refresh_macro()
        except Exception as e:
            print(f"macro quote refresh failed: {type(e).__name__}: {e}", flush=True)

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

    def us_snapshot(self) -> tuple:
        with self._lock:
            return dict(self.us_prices), self.usdinr

    def macro_snapshot(self) -> dict:
        with self._lock:
            return dict(self.macro)

    def prev_close_snapshot(self) -> tuple:
        with self._lock:
            return dict(self.prev_close), dict(self.crypto_prev_close), dict(self.us_prev_close)

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


class KiteBalanceCache:
    """The real equity balance from Kite's margins API, cached.

    The dashboard refreshes every 15-60s; the balance moves only on a fill or a transfer, so polling
    a broker API at that rate would be rude and would risk rate limits for no benefit. Ten minutes.

    The daily token expiry is handled by auth/kite_auto_login.py's TOTP login, which this calls
    before each refresh -- it verifies the existing token first and only logs in when it has actually
    expired, so this costs one cheap call on all but the first refresh of the day.

    A FAILURE IS STILL NOT ZERO. Auto-login can fail too (a changed password, a 2FA change, Kite
    being down), and reporting that as a zero balance would read as "the account is empty" -- which,
    where this feeds an allocation cap, is the difference between refusing a change and silently
    appearing to have nothing. It returns None for "unknown", callers treat unknown as "do not allow
    new capital to be assigned" (the same fail-closed rule as live_guard.py), and a failed refresh
    backs off to failure_ttl so a broken login is not retried every few minutes."""

    def __init__(self, ttl_seconds: int = 600, failure_ttl_seconds: int = 1800):
        self.ttl = ttl_seconds
        self.failure_ttl = failure_ttl_seconds   # back off harder when it is not working
        self._value, self._error, self._at = None, "", 0.0
        self._lock = threading.Lock()

    def get(self) -> tuple:
        """(balance_or_None, error_text)."""
        with self._lock:
            ttl = self.ttl if self._value is not None else self.failure_ttl
            if self._at and time.monotonic() - self._at <= ttl:
                return self._value, self._error
            self._at = time.monotonic()
            try:
                from config import settings
                if not str(getattr(settings, "KITE_API_KEY", "") or "").strip():
                    self._value, self._error = None, "No Kite API key configured."
                    return self._value, self._error
                # The access token expires daily. ensure_fresh_kite_session() checks it with one cheap
                # call and only logs in (TOTP) when it is actually stale, mutating settings in place --
                # so this is a no-op on all but the first call of the day, and the token must be re-read
                # from settings afterwards rather than captured before.
                from auth.kite_auto_login import ensure_fresh_kite_session
                if not ensure_fresh_kite_session(settings):
                    self._value = None
                    self._error = ("Kite session is stale and automatic login did not succeed. "
                                   "Check KITE_USER_ID / KITE_PASSWORD / KITE_TOTP_SECRET.")
                    return self._value, self._error
                from execution.execution_engine import fetch_available_capital
                self._value = float(fetch_available_capital(settings.KITE_API_KEY,
                                                            settings.KITE_ACCESS_TOKEN))
                self._error = ""
            except Exception as e:                      # never let a broker hiccup break the dashboard
                self._value = None
                self._error = f"{type(e).__name__}: {e}"[:200]
            return self._value, self._error


class StateCache:
    """The built dashboard state, reused for a few seconds.

    WHY. build_dashboard_state() re-reads every book's portfolio.json and its trades.jsonl IN FULL on
    every call, then serialises about 2MB of JSON. The page polls every 15-60s, each open tab polls
    independently, and the VPS has 458MB of RAM shared with several other services -- so the same
    expensive rebuild was being done several times a minute for identical output. That is the memory
    churn behind the dashboard being killed.

    A few seconds of staleness is invisible on a page that polls every 15s anyway, and N open tabs
    now cost the same as one. Prices are NOT cached here -- they have their own longer-lived cache --
    so this only collapses duplicate rebuilds, it does not freeze the numbers.

    Anything that changes state (promote, allocate, a research start) calls invalidate(), so an
    action the user just took is never answered from a stale copy.
    """

    def __init__(self, ttl_seconds: float = 12.0):
        self.ttl = ttl_seconds
        self._entries = {}
        self._lock = threading.Lock()

    def get(self, key, build):
        """Returns the cached payload for `key`, or builds it. The build happens OUTSIDE the lock so
        a slow rebuild cannot block every other request; two racing builds for the same key is a
        waste but never a corruption, and the window is a few seconds once a minute."""
        now = time.time()
        with self._lock:
            hit = self._entries.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        payload = build()
        with self._lock:
            self._entries[key] = (time.time(), payload)
            if len(self._entries) > 8:          # bounded: one entry per mode, not per visitor
                oldest = min(self._entries, key=lambda k: self._entries[k][0])
                self._entries.pop(oldest, None)
        return payload

    def invalidate(self):
        with self._lock:
            self._entries.clear()


class LiveRunner:
    """Runs the live book on demand, in the background, one at a time.

    IN THE BACKGROUND because the cycle calls an LLM and fetches market data -- tens of seconds. A
    synchronous request would hold a connection open that long and time out in the browser on a slow
    day, and this server has a handful of threads on a 458MB box.

    ONE AT A TIME because two concurrent runs would both read the same book, both decide, and both
    place orders against a balance each thought it had to itself -- the one way to double a position
    without any individual check failing.

    A DRY RUN IS FREE AND A LIVE RUN IS NOT, so they are the same code path with one explicit flag,
    and the flag is carried from the request rather than defaulted anywhere in here.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._result = None
        self._started_at = None
        self._mode = ""

    def status(self) -> dict:
        with self._lock:
            return {"running": self._running, "mode": self._mode,
                    "started_at": self._started_at, "result": self._result}

    def start(self, live: bool) -> dict:
        with self._lock:
            if self._running:
                return {"ok": False, "error": f"A {self._mode} run is already going; wait for it to finish."}
            self._running = True
            self._mode = "live" if live else "dry"
            self._started_at = datetime.now().isoformat(timespec="seconds")
            self._result = None
        threading.Thread(target=self._run, args=(live,), daemon=True).start()
        return {"ok": True, "running": True, "mode": self._mode}

    def _run(self, live: bool) -> None:
        payload = {}
        try:
            from config import settings
            from data.fetch_crypto import (CRYPTO_MAJORS, fetch_all_crypto_daily,
                                           fetch_crypto_last_prices, fetch_usdinr_rate)
            import run_pool_g_live as rpg
            history = fetch_all_crypto_daily(CRYPTO_MAJORS, years=1.5)
            payload = rpg.run_live(history, fetch_crypto_last_prices, settings.ANTHROPIC_API_KEY,
                                   fetch_usdinr_rate(), dry_run=not live)
        except Exception as e:                  # a failed run must never leave it stuck "running"
            payload = {"status": "error", "reason": f"{type(e).__name__}: {e}"[:400]}
        finally:
            with self._lock:
                self._running = False
                self._result = payload
            try:
                DashboardHandler.state_cache.invalidate()   # the books moved
            except Exception:
                pass


class CoinDCXBalanceCache:
    """The real INR balance at CoinDCX, cached, on exactly the same terms as the Kite one.

    Free balance only -- `locked_balance` is money already committed to a resting order and cannot
    size a new position, so counting it would overstate what is deployable.

    A FAILURE IS NOT ZERO, for the same reason it is not at Kite: reporting an unreachable exchange
    as an empty account is the difference between refusing to assign capital and appearing to have
    none. None means unknown, and callers treat unknown as "do not allow new capital".

    Credentials come through the shared resolver, so a key set on the dashboard's Settings tab is
    picked up here without an edit or a restart."""

    def __init__(self, ttl_seconds: int = 600, failure_ttl_seconds: int = 1800):
        self.ttl = ttl_seconds
        self.failure_ttl = failure_ttl_seconds
        self._value, self._error, self._at = None, "", 0.0
        self._lock = threading.Lock()

    def get(self) -> tuple:
        """(inr_balance_or_None, error_text)."""
        with self._lock:
            ttl = self.ttl if self._value is not None else self.failure_ttl
            if self._at and time.monotonic() - self._at <= ttl:
                return self._value, self._error
            self._at = time.monotonic()
            try:
                from config import settings
                from deployment.credential_store import credential
                key = credential("COINDCX_API_KEY", CONFIG_DIR, settings)
                secret = credential("COINDCX_API_SECRET", CONFIG_DIR, settings)
                if not (key and secret):
                    self._value, self._error = None, "No CoinDCX API key configured."
                    return self._value, self._error
                from execution.coindcx_client import CoinDCXClient
                self._value = float(CoinDCXClient(key, secret).balance_of("INR"))
                self._error = ""
            except Exception as e:              # never let an exchange hiccup break the dashboard
                self._value = None
                self._error = f"{type(e).__name__}: {e}"[:200]
            return self._value, self._error


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
    state_cache = StateCache()      # replaced in main(); here so the class is usable standalone
    coindcx_cache = CoinDCXBalanceCache()
    live_runner = LiveRunner()
    price_cache: PriceCache = None
    roadmap_cache: RoadmapCache = RoadmapCache()
    balance_cache: KiteBalanceCache = KiteBalanceCache()
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
        if parsed.path == "/api/live/run":   # Live view: run Pool G's live book on demand
            # live=True is carried from the request and defaulted NOWHERE here. Even so it cannot
            # place anything unless LIVE_TRADING is True and the kill switch is absent -- this starts
            # the runner, it does not authorise it.
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                out = self.live_runner.start(live=body.get("live") is True)
                self._send(HTTPStatus.OK if out.get("ok") else HTTPStatus.BAD_REQUEST,
                           json.dumps(out).encode("utf-8"), "application/json")
            except (ValueError, OSError) as e:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"),
                           "application/json")
            return
        if parsed.path == "/api/live-settings":   # Settings tab: the numeric live-trading limits
            # Plain http is allowed here, unlike credentials: this carries no secret, and the same
            # access key already gates /api/live/allocate, which commits far more. The master switch
            # (LIVE_TRADING) is deliberately NOT settable here -- see deployment/live_settings.py.
            from deployment.live_settings import save as save_setting, status as setting_status
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                updates = body.get("settings")
                if not isinstance(updates, dict):
                    raise ValueError("Nothing to save.")
                written = save_setting(CONFIG_DIR, updates)
                self.state_cache.invalidate()          # the cap changes what the cards report
                self._send(HTTPStatus.OK, json.dumps({"ok": True, "saved": written,
                                                      "settings": setting_status(CONFIG_DIR)}).encode("utf-8"),
                           "application/json")
            except (ValueError, OSError) as e:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"),
                           "application/json")
            return
        if parsed.path == "/api/live/allocate":   # Live view: assign real capital to one strategy
            # Validation lives in deployment/live_allocations.py, not here -- this only supplies the
            # facts it needs (the funded pool, and whether the strategy is currently flat) and
            # reports its verdict. A refusal changes nothing.
            from deployment.live_allocations import set_allocation
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                from deployment.live_settings import setting as live_setting
                pool = live_setting("LIVE_CAPITAL_POOL_RUPEES", CONFIG_DIR)
                key = str(body.get("key", ""))
                # WHICH ACCOUNT holds this strategy's money. Rupees at Kite cannot buy crypto and
                # rupees at CoinDCX cannot buy shares, so capping a crypto allocation against the
                # equity balance (which this did) refuses funding that is sitting right there in the
                # other account. The venue follows the strategy, not the dashboard.
                record = next((r for r in list_strategies() if r.strategy_key == key), None)
                if record is None:
                    raise ValueError(f"{key or 'That strategy'} is not in the deployment registry.")
                from deployment.venues import COINDCX, KITE, venue_of, venue_label
                venue_id, venue = venue_of(record), venue_label(record)
                if venue_id == COINDCX:
                    balance, balance_error = self.coindcx_cache.get()
                elif venue_id == KITE:
                    balance, balance_error = self.balance_cache.get()
                else:
                    # No broker is wired for this market, so there is no account to fund it from.
                    # Refusing is the only honest answer; defaulting to one would fund it from the
                    # wrong account, which is the bug this replaced.
                    raise ValueError(f"No broker is configured for {key}, so it cannot be funded.")
                # The account's balance and the deployment cap are checked SEPARATELY, against
                # different things: the balance against what is assigned AT THIS BROKER, the cap
                # against what is deployed everywhere. Folding them into one number meant CoinDCX
                # allocations refused Kite ones, and a refusal by the cap looked like an empty
                # account. An unknown balance still refuses new capital rather than falling back to
                # the setting -- assigning money we cannot confirm exists is the mistake to avoid.
                if balance is None:
                    self._send(HTTPStatus.BAD_REQUEST, json.dumps({
                        "ok": False, "error": f"Cannot confirm the {venue} balance right now, so "
                                              f"capital cannot be assigned. " + balance_error}).encode("utf-8"),
                        "application/json")
                    return
                self.state_cache.invalidate()
                peers = {r.strategy_key for r in list_strategies() if venue_of(r) == venue_id}
                result = set_allocation(STATE_DIR, key, body.get("rupees"),
                                        available_balance=balance, same_venue_keys=peers,
                                        deployment_cap=pool,
                                        open_live_positions=_open_live_positions(key))
                payload = {"ok": result.ok, "allocations": result.allocations,
                           "error": " ".join(result.reasons)}
                self._send(HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST,
                           json.dumps(payload).encode("utf-8"), "application/json")
            except (ValueError, OSError) as e:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"),
                           "application/json")
            return
        if parsed.path == "/api/credentials":   # Settings tab: store broker API credentials
            # REFUSED OVER PLAIN HTTP, deliberately and without an override. The dashboard is reachable
            # at http://<vps>:8085 across the public internet; an API secret typed into that form
            # would cross it in cleartext, which is precisely how keys get stolen. The same server
            # already serves TLS on :8443, so the secure path exists -- this just insists on it.
            if not self._is_tls():
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({
                    "ok": False, "error": "Credentials can only be set over HTTPS. Reopen the "
                                          "dashboard on https://<this-host>:8443/ and try again -- "
                                          "sent over plain http they would cross the internet in "
                                          "cleartext."}).encode("utf-8"), "application/json")
                return
            from deployment.credential_store import save, status
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                updates = body.get("credentials")
                if not isinstance(updates, dict):
                    raise ValueError("Nothing to save.")
                written = save(CONFIG_DIR, updates)
                # The response carries STATUS only. A secret that went in never comes back out, so a
                # stolen access key can break the bot's credentials but cannot read them.
                self.state_cache.invalidate()
                self._send(HTTPStatus.OK, json.dumps({"ok": True, "saved": written,
                                                      "credentials": status(CONFIG_DIR)}).encode("utf-8"),
                           "application/json")
            except (ValueError, OSError) as e:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"),
                           "application/json")
            return
        if parsed.path == "/api/live/promote":   # Strategies tab: promote to PILOT_LIVE, or back to paper
            # The button is the FIRST of the four independent human acts that have to line up before a
            # real order can happen (promote, fund, LIVE_TRADING=True, no kill switch). It is not a
            # shortcut past any of the others, and it places nothing by itself.
            #
            # THE GATE IS RE-CHECKED HERE, not taken from the request. The page computed the same gate
            # to decide whether to draw the button, but that page may be hours stale and the body is
            # only text a client chose to send -- so what the browser believed is treated as a hint,
            # and the registry on disk decides.
            from dashboard.state_view import pilot_gate_for_key
            from deployment.base import DeploymentStatus
            from deployment.deployment_manager import set_deployment_status
            from deployment.pilot_live import PROMOTION_OVERRIDE_MARKER
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                key, target = str(body.get("key", "")), str(body.get("to", ""))
                # WHICH book goes live, for a strategy that runs in two. Validated against the
                # runner's own list rather than trusted: a pool name the runner does not know would
                # promote the strategy and then resolve to the default, which is the silent wrong
                # answer this whole change exists to remove.
                from run_pool_live import POOL_VARIANTS
                pool = str(body.get("pool", "") or "").strip().upper()
                if pool and pool not in POOL_VARIANTS:
                    raise ValueError(f"{pool} is not a pool that can be promoted "
                                     f"({', '.join(sorted(POOL_VARIANTS))}).")
                record = next((r for r in list_strategies() if r.strategy_key == key), None)
                if record is None:
                    raise ValueError(f"{key or 'that strategy'} is not in the deployment registry.")

                if target == "PILOT_LIVE":
                    gate = pilot_gate_for_key(record, STATE_DIR)
                    evidence = (f"verdict {getattr(record.research_verdict, 'value', record.research_verdict)}, "
                                f"{gate['days']} paper days, {gate['trades']} closed trades")
                    which = f" Pool {pool} is the live book." if pool else ""
                    if gate["eligible"]:
                        reason = (f"Promoted to pilot live from the dashboard.{which} Automated gates at "
                                  f"promotion: {evidence}. Remaining gates (drift, costs, drawdown, "
                                  f"backtest credibility) were judged by hand -- see "
                                  f"deployment/LIVE_PROMOTION_CRITERIA.md.")
                    elif body.get("override") is True:
                        # The gates are a floor the user set, and he keeps the right to go past his
                        # own floor. What is NOT optional is writing down what was skipped: the marker
                        # is what deployment/pilot_live.promotion_override() later reads, so the
                        # per-order guard honours this instead of silently refusing every order.
                        reason = (f"{PROMOTION_OVERRIDE_MARKER} Promoted to pilot live from the "
                                  f"dashboard in spite of the automated gates.{which} Evidence at promotion: "
                                  f"{evidence}. Gates NOT met: {' '.join(gate['reasons'])} "
                                  f"Deliberate human decision; see deployment/LIVE_PROMOTION_CRITERIA.md.")
                    else:
                        raise ValueError("The automated gates do not pass: " + " ".join(gate["reasons"]))
                elif target == "PAPER_TRADING":
                    # Demoting a strategy that still holds real positions would orphan them: the live
                    # runner skips anything not PILOT_LIVE, so nobody would manage the exits. The kill
                    # switch is the tool for stopping NEW orders while keeping the book managed.
                    held = _open_live_positions(key)
                    if held:
                        raise ValueError(
                            f"{key} still holds {held} live position(s). Demoting now would leave them "
                            f"with no runner to exit them. Close them first, or engage the kill switch "
                            f"({os.path.join(STATE_DIR, 'LIVE_TRADING_HALTED')}) to stop new orders "
                            f"while the book is still managed.")
                    reason = "Returned to paper trading from the dashboard."
                else:
                    raise ValueError("Target status must be PILOT_LIVE or PAPER_TRADING.")

                # No force=True: deployment/base.py's transition map allows both of these moves, and
                # anything it disallows is a move that should be made deliberately, not from a button.
                self.state_cache.invalidate()
                set_deployment_status(key, DeploymentStatus(target), reason=reason,
                                      live_pool=pool if target == "PILOT_LIVE" else None)
                self._send(HTTPStatus.OK,
                           json.dumps({"ok": True, "status": target, "pool": pool}).encode("utf-8"),
                           "application/json")
            except (ValueError, KeyError, OSError) as e:
                self._send(HTTPStatus.BAD_REQUEST, json.dumps({"ok": False, "error": str(e)}).encode("utf-8"),
                           "application/json")
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

    def _is_tls(self) -> bool:
        """True when this request arrived over the HTTPS listener. Read from the socket itself rather
        than from a header: X-Forwarded-Proto and friends are set by whoever is talking to us."""
        return isinstance(getattr(self, "connection", None), ssl.SSLSocket)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not is_authorized(query, self.headers.get("Cookie", ""), self.access_key):
            self._send(HTTPStatus.FORBIDDEN, b"Forbidden: add ?key=<access key> to the URL once.", "text/plain")
            return
        extra = {}
        if self.access_key and query.get("key", [""])[0] == self.access_key:
            extra["Set-Cookie"] = f"{COOKIE_NAME}={self.access_key}; Path=/; Max-Age=2592000; SameSite=Lax"

        if parsed.path == "/api/live/run":
            self._send(HTTPStatus.OK, json.dumps(self.live_runner.status()).encode("utf-8"),
                       "application/json", extra)
            return
        if parsed.path == "/api/credentials":
            # Status only -- which credentials are configured and where they came from, never a
            # value. Safe over plain http precisely because it carries nothing worth intercepting;
            # the write side insists on TLS.
            from deployment.credential_store import status
            from deployment.live_settings import status as setting_status
            self._send(HTTPStatus.OK, json.dumps({
                "credentials": status(CONFIG_DIR), "settings": setting_status(CONFIG_DIR),
                "tls": self._is_tls(),
                "https_port": getattr(DashboardHandler, "https_port", 8443),
            }).encode("utf-8"), "application/json", extra)
            return
        if parsed.path == "/api/state":
            # mode=live is a VIEW of deployment/state/live/ (the same layout
            # as the paper folders) -- it exists so the page's Live/Paper
            # switch has somewhere to point once real-money books exist.
            # Nothing here places orders; with no live folder the view is
            # simply empty.
            mode = "live" if query.get("mode", ["paper"])[0] == "live" else "paper"
            state_root = os.path.join(STATE_DIR, "live") if mode == "live" else STATE_DIR
            # The Advice tab's what-if inputs change the answer, so they are part of the cache key.
            # Everything else about a request produces identical output for a given mode.
            advice = _advice_params(query) if mode == "live" else None
            cache_key = (mode, json.dumps(advice, sort_keys=True) if advice else "")

            def build_state():
                return self._build_state(mode, state_root, advice, query)

            payload = self.state_cache.get(cache_key, build_state)
            self._send(HTTPStatus.OK, payload, "application/json", extra)
            return
        if parsed.path in ("/", "/index.html"):
            with open(INDEX_PATH, "rb") as f:
                self._send(HTTPStatus.OK, f.read(), "text/html; charset=utf-8", extra)
            return
        self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    def _build_state(self, mode: str, state_root: str, advice, query) -> bytes:
        """Builds and serialises the whole dashboard state. Called only on a cache miss.

        Returns BYTES, not a dict: serialising once and caching the result means repeat requests skip
        json.dumps on a ~2MB structure as well as the rebuild, and the cached object is a flat buffer
        rather than a large nested graph the collector has to walk."""
        prices, as_of = self.price_cache.snapshot()
        crypto_prices, usdinr = self.price_cache.crypto_snapshot()
        us_prices, _ = self.price_cache.us_snapshot()
        macro_quotes = self.price_cache.macro_snapshot()
        prev_close, crypto_prev_close, us_prev_close = self.price_cache.prev_close_snapshot()
        registry = list_strategies()
        import research_queue
        state = build_dashboard_state(state_root, LOGS_DIR, registry, prices, as_of,
                                      roadmap=self.roadmap_cache.get(), mode=mode,
                                      research_queues={lane: research_queue.load(STATE_DIR, lane)
                                                       for lane in research_queue.LANES},
                                      kite_balance=self.balance_cache.get(),
                                      coindcx_balance=self.coindcx_cache.get(),
                                      crypto_prices=crypto_prices, usdinr=usdinr,
                                      prev_close=prev_close, crypto_prev_close=crypto_prev_close,
                                      us_prices=us_prices, us_prev_close=us_prev_close,
                                      macro_quotes=macro_quotes,
                                      groww=load_groww_snapshot(STATE_DIR) if mode == "live" else None,
                                      reports=_load_reports(STATE_DIR) if mode == "live" else None,
                                      advice_params=advice,
                                      advice_done=_advice_done() if mode == "live" else None,
                                      advice_results=_advice_results() if mode == "live" else None,
                                      advice_extra=_advice_extra() if mode == "live" else None)
        return json.dumps(state).encode("utf-8")



TLS_CERT_PATH = os.path.join(STATE_DIR, "dashboard_tls_cert.pem")
TLS_KEY_PATH = os.path.join(STATE_DIR, "dashboard_tls_key.pem")


def _start_https_listener(host: str, port: int) -> None:
    """A second listener, HTTPS on `port`, alongside the main plain-HTTP one -- same handler class,
    same access-key auth, same everything, just a different front door. Self-signed (no domain, no
    Let's Encrypt -- those need a real hostname to validate against; this is a bare IP), added
    2026-09-28 because the unattended research routine's cloud sandbox could never reach the plain
    http://<ip>:8085 URL (repeatable connection timeout, confirmed NOT caused by anything on this
    VPS -- no DigitalOcean Cloud Firewall attached, ufw inactive, iptables INPUT chain empty) while
    its own https://github.com check worked fine every time -- the leading theory is that the
    sandbox's own outbound network policy is stricter about plain HTTP to a raw IP than it is about
    HTTPS, so this gives the routine an HTTPS door to try instead. A client hitting this will get a
    certificate warning (self-signed, no browser/CA trust chain) -- curl needs `-k`/`--insecure` to
    accept it; that's expected, not a misconfiguration.
    Runs in a daemon thread from main() so a failure here (e.g. missing cert files on a fresh
    deploy) never takes down the real, already-working plain-HTTP dashboard on :8085."""
    if not (os.path.exists(TLS_CERT_PATH) and os.path.exists(TLS_KEY_PATH)):
        print(f"HTTPS listener not started -- {TLS_CERT_PATH} / {TLS_KEY_PATH} not found. "
              f"Generate a self-signed cert there to enable it (see dashboard/README or ARCHITECTURE.md).", flush=True)
        return
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=TLS_CERT_PATH, keyfile=TLS_KEY_PATH)
        https_server = ThreadingHTTPServer((host, port), DashboardHandler)
        https_server.socket = ctx.wrap_socket(https_server.socket, server_side=True)
        print(f"Dashboard ALSO on https://{host}:{port}/ (self-signed -- clients need -k/--insecure or "
              f"to accept the certificate warning)", flush=True)
        https_server.serve_forever()
    except Exception as e:
        print(f"WARNING: HTTPS listener on port {port} failed to start (non-fatal, plain HTTP on the "
              f"main port is unaffected): {type(e).__name__}: {e}", flush=True)


def _start_fx_history_refresher() -> None:
    """Keeps data/usdinr_history.py's cache fresh in the background.

    Off the request path on purpose: the ledger reads the cache without ever refreshing it, so a slow
    or dead yfinance delays nothing a user is waiting for. A failed refresh leaves the previous cache
    in place, and the ledger falls back to today's rate and marks the row.
    """
    import threading
    from data import usdinr_history

    def loop():
        while True:
            try:
                usdinr_history.history_for(STATE_DIR, refresh_if_stale=True)
            except Exception:
                pass                       # a refresher that can kill the dashboard is worse than a stale rate
            time.sleep(6 * 3600)

    threading.Thread(target=loop, daemon=True).start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8085)
    parser.add_argument("--https-port", type=int, default=8443)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    DashboardHandler.access_key = load_access_key()
    DashboardHandler.state_cache = StateCache()
    DashboardHandler.coindcx_cache = CoinDCXBalanceCache()
    DashboardHandler.live_runner = LiveRunner()
    DashboardHandler.price_cache = PriceCache(STATE_DIR)
    DashboardHandler.price_cache.start()

    import threading
    threading.Thread(target=_start_https_listener, args=(args.host, args.https_port), daemon=True).start()

    DashboardHandler.https_port = args.https_port
    _start_fx_history_refresher()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard on http://{args.host}:{args.port}/ "
          f"({'access key required' if DashboardHandler.access_key else 'OPEN -- no access key configured'})",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
