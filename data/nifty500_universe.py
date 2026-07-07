"""
Loads the Nifty 500 universe -- the broad stock list the system scans daily
once you're ready to go beyond the small hand-picked watchlist in
config/settings.py (SYMBOLS / STRATEGY_SYMBOLS).

data/nifty500_constituents.csv was downloaded directly from NSE's official
archive (archives.nseindia.com/content/indices/ind_nifty500list.csv) on
2026-07-06. It's a snapshot, not a live feed -- Nifty 500's constituents get
reshuffled periodically (usually twice a year), so re-download this file
every few months to stay current. A stale list isn't dangerous (worst case
you miss a newly-added stock or scan one that's been removed from the
index -- the Fundamentals/Technical filters still apply normally to
whatever's in the file), just something to keep fresh.

Note: this snapshot has ~457 rows, not exactly 500 -- NSE's Nifty 500 list
size drifts slightly over time and a few placeholder/demerger rows were
excluded when this file was saved. Close enough to "the broad market" for
this system's purposes; re-download the CSV above whenever you want the
exact current count.
"""

import csv
import os

CSV_PATH = os.path.join(os.path.dirname(__file__), "nifty500_constituents.csv")


def get_nifty500_symbols() -> list:
    """
    Returns yfinance-style tickers (e.g. "RELIANCE.NS") for every symbol in
    the Nifty 500 snapshot CSV. Raises a clear error if the file is missing,
    rather than silently falling back to a smaller list -- callers should
    decide themselves whether to fall back to config.SYMBOLS.
    """
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(
            f"Nifty 500 constituent file not found at {CSV_PATH}. "
            f"Download it from https://archives.nseindia.com/content/indices/ind_nifty500list.csv "
            f"and save it at that path, or fall back to config.SYMBOLS."
        )

    symbols = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = row.get("Symbol", "").strip()
            if symbol:
                symbols.append(f"{symbol}.NS")

    return symbols


if __name__ == "__main__":
    symbols = get_nifty500_symbols()
    print(f"Loaded {len(symbols)} symbols. First 10: {symbols[:10]}")
