"""
Which broker executes a given strategy's orders. ONE answer, used everywhere.

WHY THIS EXISTS. There were two different notions of "is this crypto" and they disagreed.
`is_crypto_record()` matches a strategy_family STARTING with "crypto" -- and its docstring is explicit
that it means the Pool E lane, whose books live under deployment/state/pool_e/. Pool G is also crypto
but its family reads "AI judgment book (crypto, Pool G)" and its books live under pool_g/, so that
helper correctly returns False for it. Meanwhile the Strategies table calls Pool G "Crypto", because
it reads a different source again (STRATEGY_BRIEFS).

The result: funding Pool G was capped against the Kite equity balance (Rs500) while its money sat at
CoinDCX (Rs10,000), and the card right above it said so. Two sources of truth, one of them wrong, and
the wrong one guarding real money.

So venue is now answered here and nowhere else. It is deliberately NOT derived from the family string
alone: a naming convention is a weak thing to hang a money decision on, which is exactly how this
broke. Books whose family does not announce itself are listed by key.

FAILING CLOSED. A strategy whose venue cannot be determined returns "" -- no venue -- and callers must
treat that as "cannot fund, cannot trade" rather than guessing a default. US equity books (Pool I) do
that today on purpose: they are paper-only and no US broker is wired, so there is no honest answer.
"""

from deployment.base import is_crypto_record, is_us_equity_record

KITE = "kite"
COINDCX = "coindcx"
NONE = ""

# Crypto books whose strategy_family does not begin with the Pool E prefix. Listed by key because a
# key is exact, where a substring match on a human-written description is a guess.
CRYPTO_STRATEGY_KEYS = frozenset({"portfolio_g"})

# What each venue's cash is held in, for the dashboard to label a balance correctly.
VENUE_LABEL = {KITE: "Kite", COINDCX: "CoinDCX", NONE: "no broker"}


def venue_of(record) -> str:
    """The broker that would execute this strategy's orders: KITE, COINDCX, or NONE.

    NONE is a real answer, not a failure to decide: it means no broker is wired for this market, and
    the caller must refuse rather than fall back."""
    key = str(getattr(record, "strategy_key", "") or "")
    if is_crypto_record(record) or key in CRYPTO_STRATEGY_KEYS:
        return COINDCX
    if is_us_equity_record(record):
        return NONE          # Pool I is paper-only; no US broker exists, so there is no honest answer
    return KITE


# How each venue spells an instrument. The books use the research side's convention -- yfinance's
# RELIANCE.NS -- while Kite trades RELIANCE. Order placement already strips the suffix
# (execution/execution_engine.py), but reconciliation compared the two spellings directly and read
# "the book holds 40 but the exchange reports 0" for every equity position it checked, which would
# have halted every equity run the first time one was promoted. CoinDCX needs no translation: the
# books hold BTC and the exchange reports BTC.
#
# Here rather than in reconciliation.py because it is the same kind of fact as which broker executes
# an order, and keeping both in one place is what stops a second, disagreeing answer growing.
EXCHANGE_SUFFIXES = (".NS", ".BO")


def exchange_symbol(symbol: str, venue: str) -> str:
    """How `venue` spells the instrument the books call `symbol`.

    Applied to BOTH sides of a comparison, never one, so the comparison is like-for-like whichever
    convention each side happens to use."""
    text = str(symbol or "").strip().upper()
    if venue != KITE:
        return text
    for suffix in EXCHANGE_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def is_crypto_venue(record) -> bool:
    return venue_of(record) == COINDCX


def venue_label(record) -> str:
    return VENUE_LABEL.get(venue_of(record), "no broker")
