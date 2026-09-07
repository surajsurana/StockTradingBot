"""
The hand-picked candidate pairs for EXP-009 (Intra-Sector Pair Spread
Reversion). The hypothesis says "pre-select 4-5 historically highly
correlated stock pairs within the same sector" -- a curated list, the
same way run_experiment.py's LIQUID_UNIVERSE is curated, NOT an automated
all-pairs correlation scan of the universe (a different, larger
hypothesis nobody proposed). The ">0.8 trailing-60-day correlation" rule
is applied as a DAILY ELIGIBILITY filter inside pairs_simulator, not as
the mechanism that built this list.

Each pair is two of the largest, most-traded names in one
data/nifty500_constituents.csv Industry bucket; validate_pair_candidates()
checks that the CSV still agrees, so a constituent reshuffle can't
silently turn a "same-sector" pair into a cross-sector one.
"""

from typing import Optional

PAIR_CANDIDATES = [
    ("HDFCBANK", "ICICIBANK"),     # Financial Services -- the two largest private banks
    ("TCS", "INFY"),               # Information Technology -- the two largest IT services exporters
    ("SUNPHARMA", "CIPLA"),        # Healthcare -- two large generic-pharma majors
    ("ULTRACEMCO", "AMBUJACEM"),   # Construction Materials -- two large cement producers
    ("BAJFINANCE", "SHRIRAMFIN"),  # Financial Services -- two large NBFCs
]


def pair_symbols(pairs: list = PAIR_CANDIDATES) -> list:
    """Sorted, de-duplicated list of every leg -- what actually gets fetched."""
    return sorted({symbol for pair in pairs for symbol in pair})


def validate_pair_candidates(pairs: list = PAIR_CANDIDATES, sector_map: Optional[dict] = None) -> list:
    """Raises ValueError naming EVERY pair whose two legs don't share the
    same sector label (or are missing from the map), so a stale CSV is
    caught before any data is fetched. Returns the pairs unchanged when
    all are valid."""
    if sector_map is None:
        from research_lab.performance_analyst import load_sector_map
        sector_map = load_sector_map()
    problems = []
    for symbol_a, symbol_b in pairs:
        sector_a, sector_b = sector_map.get(symbol_a), sector_map.get(symbol_b)
        if sector_a is None or sector_b is None:
            problems.append(f"{symbol_a}/{symbol_b}: missing from sector map "
                            f"({symbol_a}={sector_a!r}, {symbol_b}={sector_b!r})")
        elif sector_a != sector_b:
            problems.append(f"{symbol_a}/{symbol_b}: different sectors ({sector_a!r} vs {sector_b!r})")
    if problems:
        raise ValueError("Invalid pair candidate(s):\n  " + "\n  ".join(problems))
    return list(pairs)
