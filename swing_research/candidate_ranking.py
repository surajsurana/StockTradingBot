"""
Generic candidate ranking -- replaces raw dict/universe iteration order
as the tie-breaker whenever more valid entry signals exist on a given day
than there is capital or slot capacity for.

Background (2026-08-30): SW-013 (Turn-of-the-Month) exposed that
swing_research/backtesting_engine.py's simulate_portfolio() and
deployment/paper_trading_engine.py's run_daily()/_resolve_pending_fills()
both process candidates in the FROZEN UNIVERSE'S ALPHABETICAL dict order
and stop once max_units_total/cash is exhausted -- meaning, whenever more
symbols qualify on a day than there is room for, alphabetical position
was silently deciding which candidates got capital.

Two earlier, SW-013-specific attempts to fix this were both found
insufficient, for the SAME underlying reason: both used a hash of the
symbol to build a coarse ELIGIBILITY pre-filter (a rotating quarter-
bucket, then a per-month top-40 cutoff) -- but the actual FILL ORDER
downstream of that filter was never touched, so the engine still walked
its eligible-today candidates in the same fixed alphabetical order and
filled the first few it reached. Reshuffling who gets to COMPETE each
day never fixed who WINS the competition. This module fixes the real
mechanism instead: it produces the actual fill order directly, using
(a) each strategy's own already-computed signal strength where one
exists (never an invented score), and (b) for genuine ties -- including
every strategy with no natural per-stock ranking at all, e.g. Turtle
System 2 and Turn-of-the-Month, which both leave Signal.confidence at
its neutral default of 1.0 for every candidate -- an INDEPENDENT,
per-(symbol, date) pseudo-random key. "Independent" is the operative
word: each candidate's tie-break key is computed on its own, with no
shared shuffle sequence, so sorting by it is provably invariant to
whatever order the caller happened to collect candidates in (verified
in test_candidate_ranking.py, including an explicit check that the
alphabetically-last symbol in a large synthetic universe is not
systematically favored or disfavored across many dates).

Used identically by both swing_research/backtesting_engine.py (research
backtests, base + walk-forward) and deployment/paper_trading_engine.py
(live paper trading) -- one shared implementation, per explicit
direction that the same allocation behavior must be reproducible in
both. Deliberately depends on nothing from either caller's own data
shapes (Signal, a pending-entry dict, ...) -- callers pass plain
(symbol, confidence) pairs and get back a ranked list of symbols, so
this module has zero coupling to either engine's internals.
"""

import hashlib
from datetime import date

# Namespaced so this program's tie-break seed can never accidentally
# collide with a seed derived for an unrelated purpose elsewhere.
_TIE_BREAK_SALT = "swing_research_candidate_tie_break_v2"


def _tie_break_key(symbol: str, as_of_date: date) -> int:
    """
    Deterministic, INDEPENDENT pseudo-random key for one (symbol, date)
    pair, via SHA-256 (a cryptographic hash -- uniformly distributed and
    uncorrelated with lexicographic symbol order, unlike a weak function
    such as a character-code sum). "Independent" means this key is
    computed for each candidate on its own, not via a shared shuffle
    sequence applied to a list -- that is what makes rank_candidate_
    symbols()'s output invariant to the caller's input order (a shuffle
    keyed by date alone, applied in-place to a list, would still depend
    on that list's STARTING order -- an earlier version of this module
    had exactly that bug, caught by this module's own test suite).
    Deliberately NOT Python's built-in hash() (salted per-process by
    default, so the same (symbol, date) pair would rank differently on
    every run).
    """
    digest = hashlib.sha256(f"{_TIE_BREAK_SALT}:{as_of_date.isoformat()}:{symbol}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def rank_candidate_symbols(symbol_confidence_pairs: list, as_of_date: date) -> list:
    """
    symbol_confidence_pairs: [(symbol, confidence), ...] for every symbol
    with a valid entry signal today, in ANY order -- the whole point of
    this function is that the caller's own iteration order becomes
    irrelevant to the result.

    Returns just the symbols (a list[str]), ordered by:
      1. confidence, descending -- a strategy's own, already-computed
         ranking signal (a percentile, a raw magnitude like PEAD's SUE,
         ...). Real signal strength always wins over the tie-break below.
      2. For exact ties -- most commonly because EVERY candidate shares
         the same neutral confidence (either a strategy with no natural
         ranking at all, like Turtle or Turn-of-the-Month, or several
         candidates landing at the identical percentile) -- each tied
         candidate's own independent _tie_break_key(symbol, as_of_date),
         descending.
    """
    pairs = list(symbol_confidence_pairs)
    pairs.sort(key=lambda pair: (pair[1], _tie_break_key(pair[0], as_of_date)), reverse=True)
    return [symbol for symbol, _confidence in pairs]
