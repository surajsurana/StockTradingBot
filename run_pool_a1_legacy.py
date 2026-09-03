"""
Pool A1 -- the legacy, large-capital books for 52-Week High Momentum,
Minervini, Cross-Sectional Momentum, and Short-Term Reversal, set aside
from Pool A on 2026-09-03 (per explicit direction) so Pool A's own daily
comparison against Portfolio B and C starts from the same clean
Rs.1,00,000 baseline as both of those.

Classification rule (per explicit direction, "the strategies that are
showing capital worth more than 100000"), applied against actual VPS
state on 2026-09-03: cross_sectional_momentum, fifty_two_week_high_momentum,
minervini_trend_template_filter, and short_term_reversal were each
seeded with Rs.10,00,000 and are still carrying that scale of capital
(cash + open positions well above Rs.1,00,000) -- these four move here.
max_effect and turn_of_month were already seeded at Rs.1,00,000 -- these
stay in Pool A untouched. pead's starting_capital field still reads
Rs.10,00,000 (a stale field, not a live discrepancy -- see
deployment/capital_winddown.py) but its ACTUAL current state is already
wound down to the Rs.1,00,000 floor with zero open positions -- nothing
to move, it stays in Pool A too.

Pool A1 keeps exactly the state (positions, cash, history) each of
these four strategies had at the moment it was set aside -- this is
NOT a reset, NOT a force-sell, and NOT deleted. It just never opens
another new position (entries_enabled=False -- see
deployment/paper_trading_engine.py's own docstring): mechanical stops
and each strategy's own exit_signal_at() keep monitoring and closing
existing holdings exactly as they always have, so this book winds
itself down to zero positions naturally over time as each holding's own
exit condition eventually fires, and then simply has nothing left to do.

Runs LEAN on purpose: none of these four strategies' exit_signal_at()
methods read a cross-sectional percentile column (Minervini exits on
its own ma50, the other three on elapsed holding period) -- confirmed
by reading all four -- so this only ever
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

import datetime
import os

import deployment.paper_trading_engine as pte
from data.fetch_historical import fetch_all
from deployment.settings import STATE_DIR
from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS

POOL_A1_STATE_DIR = os.path.join(STATE_DIR, "paper_trading_legacy")
POOL_A1_STRATEGY_KEYS = (
    "fifty_two_week_high_momentum",
    "minervini_trend_template_filter",
    "cross_sectional_momentum",
    "short_term_reversal",
)

_SPECS_BY_KEY = {spec.strategy_key: spec for spec in PAPER_TRADING_STRATEGY_SPECS}


def _run_one(strategy_key: str) -> None:
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


def main():
    pte.PAPER_TRADING_STATE_DIR = POOL_A1_STATE_DIR
    for strategy_key in POOL_A1_STRATEGY_KEYS:
        try:
            _run_one(strategy_key)
        except Exception as e:
            print(f"ERROR: Pool A1 '{strategy_key}' failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
