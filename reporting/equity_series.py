"""
One strategy's equity, day by day, for the little chart behind the Capital and Net Profit figures.

WHERE IT COMES FROM. Every paper book already writes a daily_equity.jsonl as it runs -- the engine's
own record of what the book was worth at each day's close. Nothing new is computed here and no
number is derived a second way: this reads those files and lines them up by date.

A STRATEGY CAN BE SEVERAL BOOKS. The same key runs in Pools A, F, A2 and A3, Pool E and Pool E1, or
Pools I, I2 and I3, and the Strategies row shows their combined capital, so the series adds them up
the same way. A date only one book has is still a real date for the strategy -- the others are
carried forward from their last known value rather than dropped, or the total would dip on a day a
book simply had no entry.

CURRENCY. The crypto and US books keep their equity in USD. The row beside the chart is in rupees,
so these are converted at one rate -- today's -- which is exactly what the row does. That makes the
SHAPE of a foreign book's curve faithful and its older rupee values approximate; a per-day rate
would redraw history every time the rupee moved, which is worse.
"""

import json
import os
from typing import Optional

# Where each pool keeps its books. A directory holding the book folders, or a single book itself.
BOOK_ROOTS = ("paper_trading", "pool_f", "pool_a2", "pool_a3", "pool_e", "pool_e1", "pool_i", "pool_i2", "pool_i3",
              "pool_d", "archived")
# A pool that IS one book keeps its file at the pool's own root rather than under a key.
SINGLE_BOOKS = {"portfolio_b": "portfolio_b", "portfolio_c": "portfolio_c",
                "portfolio_g": "pool_g", "pool_d_vwap_fade": "pool_d"}
# Books kept in dollars; everything else is already rupees.
USD_ROOTS = ("pool_e", "pool_e1", "pool_i", "pool_i2", "pool_i3", "pool_g")


def _read(path: str) -> list:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                date, equity = row.get("date"), row.get("equity")
                if date and equity is not None:
                    out.append((str(date), float(equity)))
    except OSError:
        return []
    return out


def books_for(state_dir: str, key: str) -> list:
    """[(pool root, path to its daily_equity.jsonl)] for every book this strategy runs."""
    found = []
    for root in BOOK_ROOTS:
        path = os.path.join(state_dir, root, key, "daily_equity.jsonl")
        if os.path.exists(path):
            found.append((root, path))
    if key in SINGLE_BOOKS:
        path = os.path.join(state_dir, SINGLE_BOOKS[key], "daily_equity.jsonl")
        if os.path.exists(path):
            found.append((SINGLE_BOOKS[key], path))
    return found


def series_for(state_dir: str, key: str, usdinr: float = 0.0) -> dict:
    """{"points": [{"date", "equity"}], "pools": [...], "converted": bool}, oldest first.

    Empty points -- never an error -- when the strategy has no book yet. A strategy that has never
    traded is an ordinary thing to click on."""
    books = books_for(state_dir, key)
    if not books:
        return {"key": key, "points": [], "pools": [], "converted": False}

    per_book, converted = [], False
    for root, path in books:
        rows = _read(path)
        if root in USD_ROOTS and usdinr:
            rows = [(d, v * usdinr) for d, v in rows]
            converted = True
        per_book.append(dict(rows))

    dates = sorted({d for book in per_book for d in book})
    points, last = [], [None] * len(per_book)
    for date in dates:
        total = 0.0
        for i, book in enumerate(per_book):
            if date in book:
                last[i] = book[date]
            # A book with no entry on this date has not vanished; it is worth what it was worth.
            total += last[i] or 0.0
        points.append({"date": date, "equity": round(total, 2)})
    return {"key": key, "points": points, "pools": [r for r, _ in books], "converted": converted}
