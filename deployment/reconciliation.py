"""
Does the book match the exchange? Gate H's reconciliation requirement.

WHY IT MATTERS MORE THAN IT SOUNDS. The live book is written by the strategy's own cycle as part of
deciding -- it records a position the moment it decides to buy, before any order is placed. Anything
that then fails (a refused order, a rejected fill, a network drop between sending and recording)
leaves the book believing it holds something it does not. The next run sizes against that belief, and
may try to SELL a coin the account has never held. One silent divergence compounds.

This compares what the book claims against what the exchange actually reports, every run, before any
new order is placed.

THE ASYMMETRY IS DELIBERATE. Short and long are not the same kind of wrong:

  - the exchange holding LESS than the book claims is a REAL problem. The book will try to sell what
    is not there, and every sizing decision downstream is built on a position that does not exist.
    That halts trading.
  - the exchange holding MORE is noted but does not halt. The account is the owner's, not the bot's:
    coins bought by hand, an airdrop, or a balance that predates the bot are all perfectly normal and
    none of them are the bot's to reason about. Halting on them would mean the bot stops because its
    owner used their own account.

DUST TOLERANCE. Exchanges round, fees are taken in kind, and a quantity is stored to six decimals.
An exact-equality check would halt on arithmetic rather than on a problem, so a position is
considered matched within a relative tolerance.

IT NEVER REPAIRS ANYTHING. It reports, and the caller refuses to trade. Silently rewriting a book to
match an exchange would destroy the evidence of whatever went wrong, and guessing which side is right
is exactly the judgement a program should not make about someone's money.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from deployment.venues import COINDCX, exchange_symbol

# A position matches if book and exchange agree within this fraction of the book's own quantity.
# 0.5% absorbs fee-in-kind and rounding without hiding a real shortfall.
RELATIVE_TOLERANCE = 0.005
# Below this absolute quantity nothing is worth halting over, whatever the relative difference.
DUST_QUANTITY = 1e-6


@dataclass
class PositionCheck:
    symbol: str
    book_quantity: float
    exchange_quantity: float
    verdict: str                      # "match" | "short" | "extra"

    @property
    def difference(self) -> float:
        return round(self.exchange_quantity - self.book_quantity, 8)


@dataclass
class Reconciliation:
    ok: bool                          # False means: do not place new orders
    checked_at: str
    positions: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    error: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _matches(book_qty: float, exchange_qty: float) -> str:
    if abs(exchange_qty - book_qty) <= max(abs(book_qty) * RELATIVE_TOLERANCE, DUST_QUANTITY):
        return "match"
    return "short" if exchange_qty < book_qty else "extra"


def load_book_positions(book_path: str) -> dict:
    """{symbol: quantity} from a live book. A missing book is NO positions, which is correct for a
    strategy that has not traded yet; an unreadable one raises, because 'cannot read the book' must
    never be mistaken for 'the book is empty' when the next step is placing orders."""
    if not os.path.exists(book_path):
        return {}
    with open(book_path, encoding="utf-8") as f:
        book = json.load(f)
    out = {}
    for symbol, position in (book.get("positions") or {}).items():
        try:
            out[str(symbol).upper()] = float(position.get("quantity", 0) or 0)
        except (TypeError, ValueError, AttributeError):
            out[str(symbol).upper()] = 0.0
    return out


def reconcile(book_positions: dict, exchange_quantities: dict,
              now: Optional[datetime] = None) -> Reconciliation:
    """Pure comparison -- no network, no files. Everything that can go wrong with a broker call is
    the caller's to handle, which keeps the rule itself trivially testable."""
    checked_at = (now or datetime.now()).isoformat(timespec="seconds")
    positions, problems, notes = [], [], []

    for symbol, book_qty in sorted(book_positions.items()):
        exchange_qty = float(exchange_quantities.get(symbol.upper(), 0.0) or 0.0)
        verdict = _matches(book_qty, exchange_qty)
        positions.append(PositionCheck(symbol, book_qty, exchange_qty, verdict))
        if verdict == "short":
            problems.append(
                f"{symbol}: the book holds {book_qty:g} but the exchange reports {exchange_qty:g}. "
                f"Selling or sizing against that position would act on something that is not there.")

    # Holdings the exchange has that the book never bought. Reported, never a halt -- the account
    # belongs to its owner, who may hold whatever they like in it.
    for symbol, exchange_qty in sorted(exchange_quantities.items()):
        if exchange_qty > DUST_QUANTITY and symbol.upper() not in book_positions:
            notes.append(f"{symbol}: the exchange holds {exchange_qty:g} that this book did not buy "
                         f"(yours, not the bot's -- ignored).")

    return Reconciliation(ok=not problems, checked_at=checked_at, positions=positions,
                          problems=problems, notes=notes)


def reconcile_against_exchange(book_path: str, client, symbols: Optional[list] = None,
                               now: Optional[datetime] = None, venue: str = COINDCX) -> Reconciliation:
    """Reads the book and the exchange and compares them.

    `venue` decides how instruments are spelled on each side. The books use the research convention
    (RELIANCE.NS); Kite trades RELIANCE. Both sides are put through the same translation before being
    compared -- comparing the raw spellings read "the book holds 40 but the exchange reports 0" for
    every equity position, which would have halted every equity run the first time one went live.
    It defaults to COINDCX, the only venue that had a live book when this was written, and for which
    the translation is the identity.

    ANY failure to establish the facts returns ok=False. Not knowing whether the book is right is not
    the same as it being right, and the caller's next step is placing real orders."""
    try:
        book_positions = {exchange_symbol(k, venue): v
                          for k, v in load_book_positions(book_path).items()}
    except (OSError, ValueError) as e:
        return Reconciliation(ok=False, checked_at=(now or datetime.now()).isoformat(timespec="seconds"),
                              problems=[f"Could not read the live book, so nothing can be verified: {e}"],
                              error=str(e))
    try:
        rows = client.balances()
    except Exception as e:
        return Reconciliation(ok=False, checked_at=(now or datetime.now()).isoformat(timespec="seconds"),
                              problems=[f"Could not read the exchange balances, so the book cannot be "
                                        f"verified: {type(e).__name__}: {e}"], error=str(e))

    exchange = {}
    for row in rows or []:
        try:
            currency = exchange_symbol(row.get("currency", ""), venue)
            # free + locked: a coin committed to a resting sell order is still held, and excluding it
            # would read as a shortfall the moment the book tries to exit a position.
            exchange[currency] = float(row.get("balance", 0) or 0) + float(row.get("locked_balance", 0) or 0)
        except (TypeError, ValueError, AttributeError):
            continue
    if symbols:
        wanted = {exchange_symbol(s, venue) for s in symbols}
        exchange = {k: v for k, v in exchange.items() if k in wanted}
    return reconcile(book_positions, exchange, now=now)
