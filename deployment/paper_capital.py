"""
Changing how much money a PAPER strategy trades with.

WHY IT EXISTS (2026-10-14, per explicit direction: "live capital in paper we should build, because
the rule is no difference in paper and live modes"). Live mode has a box on each row for assigning
real capital; paper had none, and every paper book simply started at whatever its runner seeded it
with and stayed there for ever. Same screen, same control, each mode's own kind of money.

WHAT A BOOK'S SIZE ACTUALLY IS. Two numbers that have to move together: `cash`, which is what the
book can spend, and `starting_capital`, the base its returns are quoted against. Adding Rs50,000
means both go up by Rs50,000 -- moving one alone would either invent a profit (cash up, base
unchanged) or invent a loss (base up, cash unchanged).

IT IS A DELTA, NOT AN ASSIGNMENT, AND THAT MATTERS. The figure on the page is not
`starting_capital`: the dashboard computes capital as cash + deployed - realised (see
dashboard/state_view.py's _with_capital), precisely because some books' `starting_capital` is stale
-- PEAD's still reads Rs10,00,000 for a Rs1,00,000 book. So the new size is reached by moving each
book by the difference from what the page is SHOWING, which is the number the person typed against.
Treating the typed figure as a direct assignment to `starting_capital` would move a stale book by
nine lakh rupees in the wrong direction.

A STRATEGY CAN BE SEVERAL BOOKS -- the same key in Pool A and Pool F, or Pool E and Pool E1 -- and
the row shows their combined capital, so the typed figure is the combined one and it is split evenly
between them. For the ordinary one-book strategy that is simply the number itself. Pools I2 and I3
are the exception: they exist to be 2x and 3x Pool I's size, so a US strategy's figure is split
1:2:3 (BOOK_WEIGHTS) -- an even split would erase the very difference they are there to measure.
Pools A2 and A3 (2026-10-19) are the same for Pool A: 2 and 3 shares to Pool A's (and Pool F's) one.

CURRENCY. The crypto and US books are kept in dollars and the row shows them converted, so a rupee
figure typed against them is converted back at the same rate the page used. Without a rate those
books cannot be resized at all, which is better than sizing a 1,000 USDT book at 1,00,000 USDT.

WHAT IT REFUSES. Anything live: real money is assigned through deployment/live_allocations.py, which
has its own checks, and this must never become a second way in. And it will not take out money that
is not there -- a book holding Rs80,000 of open positions on Rs20,000 of cash cannot be sized down
to Rs50,000 without selling something, which this does not do.

THE RECORD IS KEPT, NOT RESET. The trades, the daily equity and the P&L all stand; only the size
changes, and each change is written into the book so a step in the equity curve has a reason
attached rather than looking like a day nobody can explain.
"""

import json
import os
from datetime import datetime
from typing import Optional

from deployment.base import DeploymentStatus

# Where each pool keeps its books -- the same roots reporting/equity_series.py reads, minus
# "archived": a retired book is a record, not something to resize.
POOL_OF_ROOT = {"paper_trading": "Pool A", "pool_f": "Pool F", "pool_e": "Pool E",
                "pool_e1": "Pool E1", "pool_i": "Pool I", "pool_i2": "Pool I2", "pool_i3": "Pool I3",
                "pool_a2": "Pool A2", "pool_a3": "Pool A3"}
# How many shares of the typed figure each book takes; anything unlisted takes one.
BOOK_WEIGHTS = {"pool_i2": 2, "pool_i3": 3, "pool_a2": 2, "pool_a3": 3}
# A pool that IS one book keeps its file at the pool's own root rather than under a key.
SINGLE_BOOKS = {"portfolio_b": ("portfolio_b", "Pool B"), "portfolio_c": ("portfolio_c", "Pool C"),
                "portfolio_g": ("pool_g", "Pool G"), "pool_d_vwap_fade": ("pool_d", "Pool D")}
# Books kept in dollars; everything else is already rupees.
USD_ROOTS = ("pool_e", "pool_e1", "pool_i", "pool_i2", "pool_i3", "pool_g")


def books_for(state_dir: str, key: str) -> list:
    """[{"root", "pool", "path", "usd"}] -- every live paper book this strategy runs."""
    found = []
    for root, pool in POOL_OF_ROOT.items():
        path = os.path.join(state_dir, root, key, "portfolio.json")
        if os.path.exists(path):
            found.append({"root": root, "pool": pool, "path": path, "usd": root in USD_ROOTS})
    if key in SINGLE_BOOKS:
        root, pool = SINGLE_BOOKS[key]
        path = os.path.join(state_dir, root, "portfolio.json")
        if os.path.exists(path):
            found.append({"root": root, "pool": pool, "path": path, "usd": root in USD_ROOTS})
    return found


def _held(book: dict) -> float:
    """What this book cannot give back: its open positions, at cost, in the book's own currency."""
    total = 0.0
    for pos in (book.get("positions") or {}).values():
        if not isinstance(pos, dict):
            continue
        try:
            total += float(pos.get("entry_price") or 0) * float(pos.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
    return total


def set_capital(state_dir: str, key: str, amount: float, current_by_pool: Optional[dict] = None,
                usdinr: float = 0.0, record=None, now=None) -> dict:
    """Resize a paper strategy to `amount` rupees across its books.

    `current_by_pool` is what the page is showing per pool, in rupees (the row's own
    pools_breakdown) -- the figure the typed amount is a change FROM. Returns
    {"ok", "error", "books", "total", "moved"} and never raises.

    Nothing is written until every book has been checked, so a refusal on the second book cannot
    leave the first one resized."""
    now = now or datetime.now()
    current_by_pool = current_by_pool or {}

    if record is not None and record.deployment_status in (DeploymentStatus.PILOT_LIVE,
                                                           DeploymentStatus.PRODUCTION):
        return {"ok": False, "books": 0,
                "error": "This strategy is live. Real capital is assigned in Live mode."}
    # Only something actually paper trading has a size worth changing. A retired strategy's book is
    # a record of what happened and must stay as it stands -- resizing it would rewrite the return
    # that retired it -- and a RESEARCH candidate has no book at all.
    if record is not None and record.deployment_status != DeploymentStatus.PAPER_TRADING:
        return {"ok": False, "books": 0,
                "error": "This strategy is not paper trading, so there is no book to size."}
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "books": 0, "error": "That is not a number."}
    if amount < 0:
        return {"ok": False, "books": 0, "error": "Capital cannot be negative."}

    books = books_for(state_dir, key)
    if not books:
        return {"ok": False, "books": 0, "error": "This strategy has no paper book yet."}

    total_weight = sum(BOOK_WEIGHTS.get(b["root"], 1) for b in books)
    planned, moved = [], 0.0
    for b in books:
        each_inr = amount * BOOK_WEIGHTS.get(b["root"], 1) / total_weight
        if b["usd"] and not usdinr:
            return {"ok": False, "books": 0,
                    "error": (f"{b['pool']} is kept in dollars and today's USD/INR rate is not "
                              f"available, so it cannot be resized right now.")}
        rate = usdinr if b["usd"] else 1.0
        try:
            with open(b["path"], encoding="utf-8") as f:
                book = json.load(f)
        except (OSError, ValueError) as e:
            return {"ok": False, "books": 0,
                    "error": f"Could not read {b['pool']}'s book ({type(e).__name__})."}

        # The change is measured from what the page shows, not from starting_capital -- see the
        # module docstring. Falling back to starting_capital only matters for a book the page did
        # not report, which in practice means a book it has never drawn.
        shown_inr = current_by_pool.get(b["pool"])
        if shown_inr is None:
            shown = float(book.get("starting_capital") or 0)
        else:
            shown = float(shown_inr) / rate
        delta = (each_inr / rate) - shown
        cash = float(book.get("cash") or 0) + delta
        if cash < 0:
            held = _held(book) * rate
            return {"ok": False, "books": 0,
                    "error": (f"That would leave {b['pool']} short: it holds about {held:,.0f} in "
                              f"open positions, so it cannot be sized below that without selling.")}
        book["cash"] = round(cash, 8)
        book["starting_capital"] = round(float(book.get("starting_capital") or 0) + delta, 8)
        book.setdefault("capital_changes", []).append(
            {"at": now.isoformat(timespec="seconds"), "by": round(delta, 4),
             "to": round(shown + delta, 4), "asked_inr": round(amount, 2)})
        planned.append((b["path"], book))
        moved += delta * rate

    for path, book in planned:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(book, f, indent=2)
        os.replace(tmp, path)
    return {"ok": True, "error": "", "books": len(planned), "total": round(amount, 2),
            "moved": round(moved, 2)}
