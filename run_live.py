"""
THE live runner. One entry point, every promoted strategy, whatever pool it lives in.

Suraj's requirement, stated 2026-10-07: "click P in paper trading and allocate capital, then it
starts trading with real money. It should be that simple. No additional step." This is the piece
that makes that true for more than one pool.

WHAT IT DOES. Finds every strategy that is BOTH promoted (PILOT_LIVE/PRODUCTION) and funded, works
out which broker it trades at, runs its own engine against its own live book, reconciles that book
against the exchange, and places what it decided -- through deployment/live_guard.py, which has the
final say on every order.

WHY A DISPATCHER AND NOT ONE ENGINE. The pools genuinely differ underneath: A, F, E, E1 and I run
deployment/paper_trading_engine.py, while Pool G has its own LLM cycle in portfolio_g/daily.py that
is not in PAPER_TRADING_STRATEGY_SPECS at all. Pretending they are the same would mean rewriting one
of them, and rewriting a working strategy engine to make a runner tidier is a bad trade. Instead each
family gets a thin adapter that answers one question -- "run against this live book and tell me the
orders you decided on" -- and everything after that point is shared: the venue, the reconciliation,
the guard, the executor, the order log, the caps.

ADDING A POOL IS AN ADAPTER. Nothing else. That is the whole point of the shape.

WHAT IT REFUSES. A strategy with no venue (US equity -- Pool I is paper-only and no US broker is
wired), no capital, no adapter, or whose book does not match the exchange. Refusing is always
reported, never silent, and never resolved by guessing.
"""

import argparse
import os
from typing import Optional

from deployment.base import DeploymentStatus
from deployment.deployment_manager import list_strategies
from deployment.live_allocations import allocation_for
from deployment.settings import STATE_DIR
from deployment.venues import COINDCX, KITE, NONE, venue_of

POOL_G_KEY = "portfolio_g"


def eligible(state_dir: str = STATE_DIR) -> list:
    """(record, rupees) for every strategy both promoted and funded.

    Promoted-but-unfunded is a deliberate, safe intermediate state -- it sizes every position to
    zero -- so it is skipped rather than treated as an error."""
    out = []
    for record in list_strategies():
        if record.deployment_status not in (DeploymentStatus.PILOT_LIVE, DeploymentStatus.PRODUCTION):
            continue
        rupees = allocation_for(state_dir, record.strategy_key)
        if rupees > 0:
            out.append((record, rupees))
    return out


def _client_for(venue: str, settings, config_dir: str):
    """The read/write client for a venue, or None if its credentials are not configured."""
    from deployment.credential_store import credential
    if venue == COINDCX:
        from execution.coindcx_client import CoinDCXClient
        key = credential("COINDCX_API_KEY", config_dir, settings)
        secret = credential("COINDCX_API_SECRET", config_dir, settings)
        return CoinDCXClient(key, secret) if (key and secret) else None
    if venue == KITE:
        from execution.kite_positions import KiteHoldingsClient
        key = credential("KITE_API_KEY", config_dir, settings)
        token = credential("KITE_ACCESS_TOKEN", config_dir, settings)
        return KiteHoldingsClient(key, token) if (key and token) else None
    return None


def run_one(record, rupees: float, *, settings, state_dir: str = STATE_DIR, dry_run: bool = True,
            client=None, now=None, resolve_at_open: bool = False) -> dict:
    """Runs ONE promoted, funded strategy end to end and reports what happened.

    dry_run=True is the default everywhere in this file: the whole path runs -- engine, book,
    reconciliation, guard -- and nothing is sent. Placing requires asking for it."""
    key = record.strategy_key
    venue = venue_of(record)
    if venue == NONE:
        return {"key": key, "status": "refused",
                "reason": f"No broker is wired for {key}'s market, so it cannot trade live."}

    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
    if client is None:
        try:
            client = _client_for(venue, settings, config_dir)
        except Exception as e:
            return {"key": key, "status": "refused",
                    "reason": f"Could not build a {venue} client: {type(e).__name__}: {e}"}
    if client is None:
        return {"key": key, "status": "refused",
                "reason": f"{venue} credentials are not configured, so {key} cannot trade."}

    if key == POOL_G_KEY:
        import run_pool_g_live as adapter
        from config import settings as cfg
        from data.fetch_crypto import (CRYPTO_MAJORS, fetch_all_crypto_daily,
                                       fetch_crypto_last_prices, fetch_usdinr_rate)
        history = fetch_all_crypto_daily(CRYPTO_MAJORS, years=1.5)
        out = adapter.run_live(history, fetch_crypto_last_prices, cfg.ANTHROPIC_API_KEY,
                               fetch_usdinr_rate(), state_dir=state_dir, settings=settings,
                               client=client, dry_run=dry_run, now=now)
        return {"key": key, "venue": venue, "allocated": rupees, **out}

    # Everything else runs the shared paper-trading engine against its own live book. The data it
    # needs depends on the market, not the pool: a crypto strategy wants Binance candles, an equity
    # one wants the NSE universe.
    import run_pool_live as adapter
    usdinr = 0.0
    if venue == COINDCX:
        from data.fetch_crypto import fetch_all_crypto_daily, fetch_usdinr_rate
        from run_pool_e import HISTORY_YEARS, POOL_E_STRATEGIES
        entry = POOL_E_STRATEGIES.get(key)
        if entry is None:
            return {"key": key, "status": "refused",
                    "reason": f"{key} is a crypto strategy with no engine entry, so it cannot run."}
        data = fetch_all_crypto_daily(entry[1], years=HISTORY_YEARS)
        usdinr = fetch_usdinr_rate()
    else:
        from data.fetch_historical import fetch_all
        from swing_research.universe import get_swing_universe
        data = fetch_all(get_swing_universe(), period="2y")
    out = adapter.run_live(key, fetch_data_fn=lambda d=data: d, state_dir=state_dir,
                           settings=settings, client=client, dry_run=dry_run, now=now,
                           usdinr=usdinr, resolve_at_open=resolve_at_open)
    return {"key": key, "venue": venue, "allocated": rupees, **out}


def run_all(*, settings=None, state_dir: str = STATE_DIR, dry_run: bool = True, now=None,
            venues=None, resolve_at_open: bool = False) -> list:
    """Every eligible strategy, one after another.

    One failing strategy must not stop the others: a crash in one pool's engine is reported against
    that pool and the loop continues, because a shared runner that dies on the first problem would
    silently stop managing every other live position."""
    if settings is None:
        from config import settings as settings            # noqa: PLC0415
    results = []
    for record, rupees in eligible(state_dir):
        # `venues` lets one cron line serve one market. The two markets keep different hours --
        # crypto trades around the clock, the NSE does not -- so they cannot share a schedule, and
        # a run that woke for equities should not also re-run every crypto book.
        if venues and venue_of(record) not in venues:
            continue
        try:
            results.append(run_one(record, rupees, settings=settings, state_dir=state_dir,
                                   dry_run=dry_run, now=now, resolve_at_open=resolve_at_open))
        except Exception as e:
            results.append({"key": record.strategy_key, "status": "error",
                            "reason": f"{type(e).__name__}: {e}"[:300]})
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Run every promoted, funded strategy's live book.")
    ap.add_argument("--live", action="store_true",
                    help="actually place the orders (without this nothing is sent)")
    ap.add_argument("--market", choices=["all", "equity", "crypto"], default="all",
                    help="which market to run; the two keep different hours, so they run on "
                         "different schedules")
    ap.add_argument("--resolve-at-open", action="store_true",
                    help="place the entries a previous after-close run queued, priced against "
                         "today's real open (equities only -- crypto fills same day)")
    args = ap.parse_args()

    venues = {"equity": {KITE}, "crypto": {COINDCX}}.get(args.market)
    results = run_all(dry_run=not args.live, venues=venues,
                      resolve_at_open=args.resolve_at_open)
    if not results:
        print("Nothing to run: no strategy is both promoted to live and funded.")
        return
    for r in results:
        print(f"[{r['key']}] {r.get('status')}" + (f" -- {r['reason']}" if r.get("reason") else ""))
        for order in r.get("orders", []) or []:
            print(f"    {order['side']:4s} {order.get('quantity')} {order.get('symbol')} "
                  f"@ {order.get('price')}")
        for done in r.get("placed", []) or []:
            state = (f"placed {done.get('order_id')}" if done.get("placed")
                     else f"NOT placed: {' '.join(done.get('reasons') or [])}")
            print(f"    -> {done.get('symbol')}: {state}")
    if not args.live:
        print("\nNothing was sent. Use --live to place real orders.")


if __name__ == "__main__":
    main()
