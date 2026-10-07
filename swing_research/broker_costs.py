"""
Real Indian broker charges, applied to a backtest's trade list.

WHY THIS EXISTS. Until 2026-10-07 the research pipeline modelled friction as a PERCENTAGE only --
swing_research/execution_realism_engine.py calibrates illiquidity slippage to 0.1% one way -- and had
no concept of a FLAT fee. The live books then paid a flat DP charge of Rs13.5 per scrip on every
sell, plus GST. That is invisible in percentage terms until you look at the trade size, and the trade
sizes are small: across 774 closed trades in Pools A and F the median notional was Rs3,532, at which
the DP charge alone is 0.45% and the all-in round trip is 0.68% -- more than three times what the
backtest assumed. DP charges were 49% of all friction paid (Rs19,210 of Rs39,289).

So every Indian strategy in this program was accepted by criteria that assumed economics which do not
exist. This module closes that gap: a backtest now pays what a real contract note would.

RATES ARE NOT DUPLICATED HERE. They come from reporting/equity_costs.py, which already models
Zerodha's published schedule and is what the dashboard's P&L tab charges the paper books. One source,
so research and reporting can never drift apart -- drifting apart is how this bug happened.

ON BY DEFAULT, deliberately, and unlike execution_realism_engine.py's "every option defaults to OFF".
Slippage and fill timing are modelling CHOICES about which reasonable people differ. STT, stamp duty,
exchange and SEBI fees, GST and the DP charge are statutory or contractual: they are taken out of a
real account whether or not a model represents them. A backtest that omits them is not conservative,
it is wrong, so omitting them has to be the thing you ask for rather than the thing you get.

WHAT IT DOES NOT DO. It does not re-simulate. Charges are deducted from each completed trade's pnl
and from the equity curve on the day the trade closed -- the same post-process approximation
execution_realism_engine.py already documents, and it carries the same limitation: it does not
capture how a charged early trade would have left less equity to size later trades. It also does not
model tax; tax is a function of the whole book's result, not of one trade, and reporting/
equity_costs.book_costs() already does it where a book exists.
"""

from collections import defaultdict
from dataclasses import replace
from typing import Optional

from reporting.equity_costs import DP_CHARGE_PER_SELL, GST_RATE, leg_charges

# The charge on one delivery round trip splits into a part that scales with the trade and a part that
# does not. Only the flat part cares how big the trade is, and it is the whole problem.
FLAT_ROUND_TRIP = DP_CHARGE_PER_SELL * (1 + GST_RATE)       # Rs15.93: DP on the sell, plus GST

# How much of a trade's value may go to charges before taking it is not worth the trouble. At 0.35%
# a trade spends about 0.13% on the flat fee and the rest on statutory percentages it cannot avoid --
# the unavoidable floor is roughly 0.22%, so this allows the flat fee to add about half as much
# again. It is a judgement, stated here rather than buried, and min_viable_notional() turns whatever
# you choose into the rupee figure that follows from it.
MAX_ACCEPTABLE_ROUND_TRIP_PCT = 0.0035


def variable_round_trip_pct(intraday: bool = False) -> float:
    """The part of a round trip that scales with the trade, as a fraction of notional.

    Derived from leg_charges() on a large notional rather than restated, so it tracks any rate change
    automatically. A large notional is used precisely because it makes the flat part negligible."""
    big = 1_000_000_000.0
    both_legs = leg_charges(big, "BUY", intraday)["total"] + leg_charges(big, "SELL", intraday)["total"]
    return (both_legs - (0.0 if intraday else FLAT_ROUND_TRIP)) / big


def round_trip_pct(notional: float, intraday: bool = False) -> Optional[float]:
    """What a round trip of `notional` rupees actually costs, as a fraction of it.

    This is the number the research pipeline never had. At the Rs3,532 median trade of Pools A and F
    it returns about 0.0068 -- against the 0.002 the acceptance criteria assumed."""
    notional = abs(float(notional))
    if notional <= 0:
        return None
    both_legs = leg_charges(notional, "BUY", intraday)["total"] + leg_charges(notional, "SELL", intraday)["total"]
    return both_legs / notional


def min_viable_notional(max_round_trip_pct: float = MAX_ACCEPTABLE_ROUND_TRIP_PCT,
                        intraday: bool = False) -> float:
    """The smallest trade whose charges stay within `max_round_trip_pct` of its own value.

    Below this a trade is paying the flat DP fee on a notional too small to carry it. There is no
    such floor for intraday, which has no DP charge, so this returns 0.0 there."""
    variable = variable_round_trip_pct(intraday)
    flat = 0.0 if intraday else FLAT_ROUND_TRIP
    if flat <= 0:
        return 0.0
    headroom = float(max_round_trip_pct) - variable
    if headroom <= 0:
        # The statutory percentages alone already exceed the budget; no trade of any size qualifies,
        # and saying so is more useful than returning a number that looks achievable.
        return float("inf")
    return flat / headroom


def min_viable_book_size(risk_pct_per_unit: float, stop_distance_pct: float,
                         max_round_trip_pct: float = MAX_ACCEPTABLE_ROUND_TRIP_PCT) -> Optional[float]:
    """The smallest book whose risk-sized positions clear min_viable_notional().

    Risk sizing makes a position worth capital x risk_pct / stop_distance_pct, so a wide stop and a
    1% risk budget produce a small position however large the book is -- which is why Rs1,00,000
    books were trading Rs3,532 positions. Inverting that gives the capital at which the same strategy
    starts placing trades big enough to pay their own charges."""
    if risk_pct_per_unit <= 0 or stop_distance_pct <= 0:
        return None
    floor = min_viable_notional(max_round_trip_pct)
    if floor == float("inf"):
        return None
    return floor * float(stop_distance_pct) / float(risk_pct_per_unit)


def trade_charges(trade, intraday: bool = False) -> dict:
    """Both legs of one completed trade, as leg_charges() components plus "gst" and "total"."""
    quantity = abs(float(getattr(trade, "quantity", 0) or 0))
    entry_value = float(getattr(trade, "entry_price", 0) or 0) * quantity
    exit_value = float(getattr(trade, "exit_price", 0) or 0) * quantity
    short = str(getattr(trade, "direction", "BUY")).upper() == "SELL"
    entry = leg_charges(entry_value, "SELL" if short else "BUY", intraday)
    exit_ = leg_charges(exit_value, "BUY" if short else "SELL", intraday)
    return {k: entry.get(k, 0.0) + exit_.get(k, 0.0) for k in set(entry) | set(exit_)}


def charge_trades(trades: list, intraday: bool = False) -> dict:
    """Every trade's pnl, net of what a real contract note would have taken.

    Returns {"trades": new Trade objects with net pnl, "total": rupees charged,
    "detail": {component: rupees}, "by_exit_date": {date: rupees}}. The originals are not mutated --
    a caller that wants to report gross and net side by side still can."""
    charged, detail, by_date = [], defaultdict(float), defaultdict(float)
    total = 0.0
    for trade in trades or []:
        components = trade_charges(trade, intraday)
        cost = components.get("total", 0.0)
        total += cost
        for key, value in components.items():
            if key != "total":
                detail[key] += value
        exit_date = getattr(trade, "exit_date", None)
        if exit_date is not None:
            by_date[exit_date] += cost
        charged.append(replace(trade, pnl=float(getattr(trade, "pnl", 0.0) or 0.0) - cost))
    return {"trades": charged, "total": total, "detail": dict(detail), "by_exit_date": dict(by_date)}


def charge_equity_curve(daily_equity, by_exit_date: dict):
    """The same charges taken out of the equity curve, on the day each trade closed.

    Without this the trade list would be net of charges while Sharpe and max drawdown were still
    computed on a gross curve -- two different strategies described in one report. `daily_equity` is
    a sequence of (date, equity) pairs; anything else is returned untouched."""
    if not daily_equity or not by_exit_date:
        return daily_equity
    try:
        pairs = [(d, float(v)) for d, v in daily_equity]
    except (TypeError, ValueError):
        return daily_equity
    out, running = [], 0.0
    for day, equity in pairs:
        running += by_exit_date.get(day, 0.0)
        out.append((day, equity - running))
    return out


def apply_broker_costs(result: dict, intraday: bool = False) -> dict:
    """A simulate_portfolio() result with charges taken out of both the trades and the equity curve.

    A new dict; the input is not mutated. "broker_charges" carries the totals so a caller can report
    what was taken rather than only the number after it."""
    charged = charge_trades(result.get("trades") or [], intraday)
    out = dict(result)
    out["trades"] = charged["trades"]
    out["daily_equity"] = charge_equity_curve(result.get("daily_equity"), charged["by_exit_date"])
    out["broker_charges"] = {"total": charged["total"], "detail": charged["detail"]}
    return out
