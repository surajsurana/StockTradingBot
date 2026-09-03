"""
Pool A1 -- the legacy, large-capital books for Minervini, Cross-Sectional
Momentum, and Short-Term Reversal, set aside from Pool A on 2026-09-03
(per explicit direction) so Pool A's own daily comparison against
Portfolio B and C starts from the same clean Rs.1,00,000 baseline as
both of those.

Classification rule (per explicit direction, "the strategies that are
showing capital worth more than 100000"), applied against Pool A's
CURRENTLY ACTIVE (deployment_status=PAPER_TRADING) strategies on
2026-09-03: cross_sectional_momentum, minervini_trend_template_filter,
and short_term_reversal were each seeded with Rs.10,00,000 and are
still carrying that scale of capital (cash + open positions well above
Rs.1,00,000) -- these three move here. max_effect and turn_of_month
were already seeded at Rs.1,00,000 -- these stay in Pool A untouched.
pead's starting_capital field still reads Rs.10,00,000 (a stale field,
not a live discrepancy -- see deployment/capital_winddown.py) but its
ACTUAL current state is already wound down to the Rs.1,00,000 floor
with zero open positions -- nothing to move, it stays in Pool A too.

fifty_two_week_high_momentum (SW-003) is DELIBERATELY EXCLUDED, despite
also carrying Rs.10,00,000-scale leftover paper capital: its registry
deployment_status is ARCHIVED (research_verdict REJECT) -- it was
retired under governance before this Pool A1 restructuring and its
capital is orphaned leftover state, not part of Pool A's 6 active
PAPER_TRADING strategies this restructuring is about. Confirmed
2026-09-03 by checking deployment/state/strategy_registry.json directly
after an earlier version of this classification wrongly included it
(caught only because it had also silently stopped appearing in
run_paper_trading.py's own --all-due runs since 2026-08-31 -- exactly
the ARCHIVED-strategies-get-skipped behavior working as intended). Its
state was restored to its original deployment/state/paper_trading/
location, untouched otherwise. See _guard_still_paper_trading() below
for the safeguard added after this incident.

Pool A1 keeps exactly the state (positions, cash, history) each of
these three strategies had at the moment it was set aside -- this is
NOT a reset, NOT a force-sell, and NOT deleted. It just never opens
another new position (entries_enabled=False -- see
deployment/paper_trading_engine.py's own docstring): mechanical stops
and each strategy's own exit_signal_at() keep monitoring and closing
existing holdings exactly as they always have, so this book winds
itself down to zero positions naturally over time as each holding's own
exit condition eventually fires, and then simply has nothing left to do.

Runs LEAN on purpose: none of these three strategies' exit_signal_at()
methods read a cross-sectional percentile column (Minervini exits on
its own ma50, the other two on elapsed holding period) -- confirmed
by reading all three -- so this only ever
fetches price data for the symbols actually still held, never the full
457-symbol universe Pool A's own run needs, and never computes
percentile ranks at all (each strategy's own precompute() already
degrades gracefully -- NaN, not a crash -- when that column is absent;
it's simply irrelevant here since entries_enabled=False means the
entry logic that would have consumed it never runs).

Deliberately FULLY SILENT: no Telegram message, ever, from this script
-- confirmed 2026-09-03, per explicit direction ("summary only shows me
Pool A, B and C and not Pool A1"). Check on it manually (this script's
own print() output, or deployment/state/paper_trading_legacy/<key>/)
if you ever want to.

Two passes, mirroring run_paper_trading.py's own --all-due /
--resolve-at-open split, for the SAME reason: this reuses
_DEFAULT_EXECUTION_CONFIG's fill_timing="next_day_open" (Pool A's own
live execution assumption, kept identical here rather than silently
switching to same_day_close), which means a new exit detected by an EOD
run() call is only QUEUED into pending_exits, not filled yet -- it
needs a SEPARATE pass the next morning against the real Open to
actually fill (deployment/paper_trading_engine.py's own
resolve_pending_fills_at_open()). Without this second pass a queued
exit would sit in pending_exits forever and this book would never
actually finish winding down. Run `--resolve-at-open` once, daily,
shortly after market open (e.g. 9:30 IST, same slot as Pool A's own
counterpart); run with no flag once, daily, after EOD (e.g. 15:40 IST,
just after Pool A's own 15:35 --all-due).

Runs in its own isolated state directory
(deployment/state/paper_trading_legacy/) -- a separate directory tree
from deployment/state/paper_trading/ (Pool A's now-fresh Rs.1,00,000
books), so a bug here can never touch Pool A's real state, and vice
versa. Reuses deployment/paper_trading_engine.py's real run_daily() --
the SAME entry/exit logic Pool A's own strategies use -- just pointed
at this separate directory (the same technique test_deployment.py's own
fixtures already use: patch.object(pte, "PAPER_TRADING_STATE_DIR", ...),
just for this process's whole lifetime rather than one test) and always
called with entries_enabled=False.
"""

import argparse
import datetime
import os

import deployment.paper_trading_engine as pte
from data.fetch_historical import fetch_all
from deployment.base import DeploymentStatus
from deployment.deployment_manager import get_strategy
from deployment.settings import STATE_DIR
from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS

POOL_A1_STATE_DIR = os.path.join(STATE_DIR, "paper_trading_legacy")
POOL_A1_STRATEGY_KEYS = (
    "minervini_trend_template_filter",
    "cross_sectional_momentum",
    "short_term_reversal",
)

_SPECS_BY_KEY = {spec.strategy_key: spec for spec in PAPER_TRADING_STRATEGY_SPECS}


def _guard_still_paper_trading(strategy_key: str) -> bool:
    """Added 2026-09-03 after an incident where an earlier version of
    POOL_A1_STRATEGY_KEYS wrongly included fifty_two_week_high_momentum
    -- a strategy that had ALREADY been retired to ARCHIVED/REJECT under
    governance before this restructuring, whose leftover paper capital
    just happened to also be large. Nothing in the original design
    checked the registry, so it got processed anyway (a live run_daily()
    call against an archived strategy) until caught by manually noticing
    it had gone missing from run_paper_trading.py's own --all-due log
    output. This is the guard that should have caught it automatically:
    every call site below re-checks the registry before touching a
    strategy, so if a currently-PAPER_TRADING member of Pool A1 is ever
    retired later, this stops silently instead of continuing to run a
    dead strategy indefinitely."""
    record = get_strategy(strategy_key)
    if record is None or record.deployment_status != DeploymentStatus.PAPER_TRADING:
        status = record.deployment_status.value if record else "NOT IN REGISTRY"
        print(f"[{strategy_key}] Pool A1: SKIPPING -- registry deployment_status is {status}, "
              f"not PAPER_TRADING. This strategy should be removed from POOL_A1_STRATEGY_KEYS.")
        return False
    return True


def _run_one(strategy_key: str) -> None:
    if not _guard_still_paper_trading(strategy_key):
        return
    spec = _SPECS_BY_KEY[strategy_key]
    strategy = spec.strategy_factory()

    portfolio = pte.load_portfolio(strategy_key)
    symbols = sorted(set(portfolio.get("positions", {})) | set(portfolio.get("pending_exits", {})))
    if not symbols:
        print(f"[{strategy_key}] Pool A1: no open positions left -- fully wound down.")
        return

    print(f"[{strategy_key}] Pool A1: fetching data for {len(symbols)} held symbol(s)...")
    data = fetch_all(symbols, period="3y")

    result = pte.run_daily(
        strategy_key, strategy, fetch_data_fn=lambda: data,
        as_of_date=datetime.date.today(),
        execution_config=pte.ExecutionRealismConfig(fill_timing="next_day_open"),
        entries_enabled=False,
    )
    print(f"[{strategy_key}] {result}")


def _resolve_one_at_open(strategy_key: str) -> None:
    if not _guard_still_paper_trading(strategy_key):
        return
    spec = _SPECS_BY_KEY[strategy_key]
    strategy = spec.strategy_factory()

    portfolio = pte.load_portfolio(strategy_key)
    pending_symbols = sorted(set(portfolio.get("pending_entries", {})) | set(portfolio.get("pending_exits", {})))
    if not pending_symbols:
        print(f"[{strategy_key}] Pool A1: nothing pending at open.")
        return

    print(f"[{strategy_key}] Pool A1: resolving {len(pending_symbols)} pending fill(s) at today's real Open...")
    result = pte.resolve_pending_fills_at_open(
        strategy_key, strategy, fetch_open_data_fn=lambda: fetch_all(pending_symbols, period="5d"),
        execution_config=pte.ExecutionRealismConfig(fill_timing="next_day_open"),
    )
    print(f"[{strategy_key}] {result}")


def main(resolve_at_open: bool = False):
    pte.PAPER_TRADING_STATE_DIR = POOL_A1_STATE_DIR
    for strategy_key in POOL_A1_STRATEGY_KEYS:
        try:
            if resolve_at_open:
                _resolve_one_at_open(strategy_key)
            else:
                _run_one(strategy_key)
        except Exception as e:
            stage = "the open-resolution pass" if resolve_at_open else "today's paper trading run"
            print(f"ERROR: Pool A1 '{strategy_key}' failed during {stage}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolve-at-open", action="store_true",
                         help="Resolve fills queued by a prior EOD run against today's real market Open. "
                              "Run with no flag for the normal end-of-day exit-detection pass.")
    args = parser.parse_args()
    main(resolve_at_open=args.resolve_at_open)
