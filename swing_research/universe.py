"""
Defines and FREEZES the Swing Research Program's test universe -- built once,
documented here, and never silently re-derived from a moving source. This
exists specifically so that every future strategy in this program (Turtle
Trading and whatever comes after it) is compared on the exact same symbol
set, even if the production Nifty 500 snapshot (data/nifty500_constituents.csv)
gets re-downloaded/updated later for unrelated production reasons.

Read-only reuse of data/nifty500_universe.get_nifty500_symbols() -- that
module and its underlying CSV are never modified by this program.

How the freeze works: the first time this module needs a universe, it reads
the live CSV via get_nifty500_symbols() and writes a versioned snapshot to
swing_research/universe_snapshot.json. Every call after that (including in
future sessions) reads the frozen snapshot file, not the live CSV -- so the
universe is genuinely stable across the whole program's lifetime, not just
within one process run. Re-freezing (a new version) is a deliberate,
explicit action (bump SWING_UNIVERSE_VERSION and delete the snapshot file),
never automatic.
"""

import json
import os
from datetime import date

from data.nifty500_universe import get_nifty500_symbols, CSV_PATH as PRODUCTION_CSV_PATH

# Bump this and delete universe_snapshot.json to deliberately re-freeze the
# universe for a new generation of experiments -- never done silently.
SWING_UNIVERSE_VERSION = "v1"

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "universe_snapshot.json")


def _freeze_universe() -> dict:
    """
    Builds a fresh snapshot from the production Nifty 500 CSV (read-only)
    and writes it to SNAPSHOT_PATH. Only called when no snapshot exists yet
    for the current SWING_UNIVERSE_VERSION -- see get_swing_universe().
    """
    symbols = sorted(get_nifty500_symbols())
    snapshot = {
        "version": SWING_UNIVERSE_VERSION,
        "frozen_on": date.today().isoformat(),
        "source_csv": PRODUCTION_CSV_PATH,
        "source_csv_documented_snapshot_date": "2026-07-06",  # per data/nifty500_universe.py's own docstring
        "symbol_count": len(symbols),
        "symbols": symbols,
        "notes": (
            "Frozen Nifty 500 snapshot for the Swing Research Program. Read-only reuse of "
            "data/nifty500_universe.py at freeze time -- this file is now the source of truth "
            "for this program, NOT the live production CSV, so every experiment (Turtle Trading "
            "and any future swing strategy) is compared on an identical, stable symbol set. "
            "Survivorship-bias caveat: this is CURRENT constituents applied to a multi-year "
            "backtest window -- stocks removed from the index historically, or not yet liquid "
            "at the start of the window, are not represented. Disclosed, not solved, here."
        ),
    }
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    return snapshot


def get_swing_universe() -> list:
    """
    Returns the frozen list of yfinance-style tickers (e.g. "RELIANCE.NS")
    for this program's test universe. Creates the freeze on first call if
    it doesn't exist yet for the current SWING_UNIVERSE_VERSION; every call
    after that reads the same frozen file, not the live production CSV.
    """
    if os.path.exists(SNAPSHOT_PATH):
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            snapshot = json.load(f)
        if snapshot.get("version") == SWING_UNIVERSE_VERSION:
            return snapshot["symbols"]
        # A version bump with no matching frozen file yet -- re-freeze deliberately.
    snapshot = _freeze_universe()
    return snapshot["symbols"]


def get_universe_metadata() -> dict:
    """Full snapshot metadata (version, freeze date, source, counts, notes) -- for
    recording in experiment parameters.json so every experiment's record is
    self-documenting about exactly which universe it ran against."""
    if not os.path.exists(SNAPSHOT_PATH):
        _freeze_universe()
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    symbols = get_swing_universe()
    meta = get_universe_metadata()
    print(f"Swing Research universe {meta['version']} (frozen {meta['frozen_on']}): "
          f"{len(symbols)} symbols. First 10: {symbols[:10]}")
