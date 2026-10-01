"""
Loads the S&P 500 universe -- the US equivalent of data/nifty500_universe.py,
built for the same reason: a broad, liquid US stock list for swing_research's
US-market experiments (swing_research/universe_us.py) rather than a small
hand-picked watchlist.

data/sp500_constituents.csv was downloaded from
https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv
on 2026-09-30 -- a community-maintained mirror of Wikipedia's "List of S&P 500
companies" table, not an official exchange archive (unlike the Nifty 500 file,
which came straight from NSE's own archive) -- S&P/S&P Dow Jones Indices does
not publish a free public constituent list, so this is the practical standard
source used across the industry for this exact purpose. It's a snapshot, not
a live feed -- re-download it every few months to stay current, same
staleness caveat as the Nifty 500 file (a stale list isn't dangerous, worst
case you miss a recent addition/removal).

503 rows as of the snapshot date (S&P 500 sometimes has 500-505 tickers on
the wire at any moment when a stock has two share classes both counted, or
during an index transition).
"""

import csv
import os

CSV_PATH = os.path.join(os.path.dirname(__file__), "sp500_constituents.csv")


def get_sp500_symbols() -> list:
    """
    Returns yfinance-style tickers for every symbol in the S&P 500 snapshot
    CSV. Raises a clear error if the file is missing, rather than silently
    falling back to a smaller list -- callers should decide themselves
    whether to fall back to something else.

    yfinance quirk (confirmed against this exact snapshot): a handful of
    dual-class tickers are written with a dot in the source data (e.g.
    "BRK.B", "BF.B") but yfinance expects a dash ("BRK-B", "BF-B") -- this
    loader converts "." to "-" so every symbol it returns is yfinance-ready
    without the caller needing to know about the quirk.
    """
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(
            f"S&P 500 constituent file not found at {CSV_PATH}. "
            f"Download it from https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv "
            f"and save it at that path."
        )
    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return sorted({row["Symbol"].strip().replace(".", "-") for row in rows if row.get("Symbol", "").strip()})
