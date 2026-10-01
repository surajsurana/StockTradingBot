"""
US-equity counterpart to swing_research/universe.py -- freezes and documents
the test universe for this program's US-market experiments, same discipline
and same reason: every US experiment (Minervini US, Cross-Sectional Momentum
US, and whatever comes after) is compared on the exact same symbol set, even
if data/sp500_constituents.csv gets re-downloaded later for unrelated reasons.

Read-only reuse of data/sp500_universe.get_sp500_symbols() -- that module and
its underlying CSV are never modified by this program.

How the freeze works: identical mechanism to universe.py -- the first call
reads the live CSV via get_sp500_symbols() and writes a versioned snapshot to
swing_research/universe_snapshot_us.json; every call after that reads the
frozen snapshot, not the live CSV. Re-freezing is deliberate (bump
SWING_UNIVERSE_US_VERSION and delete the snapshot file), never automatic.
"""

import json
import os
from datetime import date

from data.sp500_universe import get_sp500_symbols, CSV_PATH as PRODUCTION_CSV_PATH

# Bump this and delete universe_snapshot_us.json to deliberately re-freeze the
# universe for a new generation of experiments -- never done silently.
SWING_UNIVERSE_US_VERSION = "v1"

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "universe_snapshot_us.json")


def _freeze_universe() -> dict:
    """
    Builds a fresh snapshot from the production S&P 500 CSV (read-only) and
    writes it to SNAPSHOT_PATH. Only called when no snapshot exists yet for
    the current SWING_UNIVERSE_US_VERSION -- see get_swing_universe_us().
    """
    symbols = sorted(get_sp500_symbols())
    snapshot = {
        "version": SWING_UNIVERSE_US_VERSION,
        "frozen_on": date.today().isoformat(),
        "source_csv": PRODUCTION_CSV_PATH,
        "source_csv_documented_snapshot_date": "2026-09-30",  # per data/sp500_universe.py's own docstring
        "symbol_count": len(symbols),
        "symbols": symbols,
        "notes": (
            "Frozen S&P 500 snapshot for the Swing Research Program's US-market experiments. "
            "Read-only reuse of data/sp500_universe.py at freeze time -- this file is now the "
            "source of truth for this program's US lane, NOT the live production CSV, so every "
            "US experiment is compared on an identical, stable symbol set. Survivorship-bias "
            "caveat (same as the India universe): this is CURRENT constituents applied to a "
            "multi-year backtest window -- stocks removed from the index historically, or not "
            "yet added at the start of the window, are not represented. Disclosed, not solved."
        ),
    }
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    return snapshot


def get_swing_universe_us() -> list:
    """
    Returns the frozen list of yfinance-style US tickers (e.g. "AAPL",
    "BRK-B") for this program's US test universe. Creates the freeze on
    first call if it doesn't exist yet for the current
    SWING_UNIVERSE_US_VERSION; every call after that reads the same frozen
    file, not the live production CSV.
    """
    if os.path.exists(SNAPSHOT_PATH):
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            snapshot = json.load(f)
        if snapshot.get("version") == SWING_UNIVERSE_US_VERSION:
            return snapshot["symbols"]
    snapshot = _freeze_universe()
    return snapshot["symbols"]


def get_universe_us_metadata() -> dict:
    """Full snapshot metadata (version, freeze date, source, counts, notes) -- for
    recording in experiment parameters.json so every US experiment's record is
    self-documenting about exactly which universe it ran against."""
    if not os.path.exists(SNAPSHOT_PATH):
        _freeze_universe()
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        return json.load(f)
