"""
US equity tax for an Indian resident (Pool I, 2026-10-01) -- per explicit
direction ("i will also need to check taxes for this ... it should be as per
what will actually apply"). A different regime from both India-listed equity
(STT-linked ss.111A/112A) and crypto's flat 31.2% VDA tax, so it gets its own
model here, not a reuse of either.

As understood on 2026-10-01 -- confirm with a CA before any real money:

CAPITAL GAINS on foreign/US shares do NOT qualify for 111A/112A's
concessional STT-linked rates (no Indian STT is paid on a US trade):
  - LONG-TERM (held > 24 months): 20% flat. Indexation benefit exists in law
    but needs Cost Inflation Index data this program doesn't maintain -- NOT
    modeled, a disclosed simplification that slightly OVERSTATES long-term
    tax (the same conservative-direction choice as India VDA tax's "harshest
    reading, deliberately").
  - SHORT-TERM (held <= 24 months): added to total income, taxed at the
    investor's MARGINAL SLAB RATE under the New Tax Regime's progressive
    brackets -- NOT a flat percentage, since it stacks on top of whatever
    other income the investor already has, and the investor's income can
    change year to year. Modeled here as genuinely progressive:
    config.settings.US_EQUITY_OTHER_ANNUAL_INCOME (update whenever it
    changes -- there is no way to infer this, it is personal information)
    plus the running short-term gains already booked THIS FINANCIAL YEAR
    (April 1 - March 31) determines where each further dollar of gain falls
    across the brackets. A single gain can straddle more than one bracket.
    No Section 87A rebate/marginal-relief modeled -- another conservative
    simplification (slightly overstates tax for anyone whose total income
    is near the Rs12L rebate threshold).
  - Losses give no automatic set-off modeled (real law does allow setting a
    capital loss against capital gains) -- only positive gains are taxed,
    same "don't assume a benefit" conservative direction as the rest of this
    program's cost models.

NOT modeled (disclosed, out of scope for a paper book):
  - Dividend withholding (25% US withholding, then taxable in India again
    with a foreign tax credit via Form 67) -- these are price-based swing
    strategies, not dividend plays, so this is a minor gap.
  - LRS/TCS on remittance, and the Schedule FA foreign-asset disclosure
    requirement -- both triggered by actually moving real money abroad, not
    by a paper book. Relevant again only once this reaches PILOT_LIVE/
    PRODUCTION.
"""

from dataclasses import dataclass, field
from datetime import date

LONG_TERM_HOLDING_DAYS = 24 * 30   # ~24 months, approximated as 30-day months -- disclosed
LTCG_RATE = 0.20   # flat, no indexation modeled
CESS_RATE = 0.04   # health & education cess, on the tax amount

# New Tax Regime slabs, FY2025-26 (AY2026-27), as understood on 2026-10-01 --
# (upper bound of the bracket, rate). Confirm against the current Finance
# Act before relying on this for real money.
NEW_REGIME_SLABS = [
    (400_000, 0.0),
    (800_000, 0.05),
    (1_200_000, 0.10),
    (1_600_000, 0.15),
    (2_000_000, 0.20),
    (2_400_000, 0.25),
    (float("inf"), 0.30),
]


def holding_days(entry_date: str, exit_date: str) -> int:
    return (date.fromisoformat(exit_date) - date.fromisoformat(entry_date)).days


def financial_year_start(d: date) -> date:
    """India's financial year runs April 1 - March 31."""
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def marginal_stcg_rate(other_annual_income: float, stcg_booked_so_far: float, next_gain: float) -> float:
    """
    The EFFECTIVE rate (post-cess) on `next_gain`, a short-term gain being
    booked on top of `other_annual_income` (config.settings's own setting,
    see module docstring) and `stcg_booked_so_far` already booked this
    financial year -- genuinely progressive: `next_gain` can straddle more
    than one bracket. No 87A rebate modeled (see docstring). Returns a
    fraction, not a bracket label.
    """
    if next_gain <= 0:
        return 0.0
    floor, ceiling = other_annual_income + stcg_booked_so_far, other_annual_income + stcg_booked_so_far + next_gain
    tax, lower = 0.0, 0.0
    for upper, rate in NEW_REGIME_SLABS:
        band_lo, band_hi = max(lower, floor), min(upper, ceiling)
        if band_hi > band_lo:
            tax += (band_hi - band_lo) * rate
        lower = upper
        if upper >= ceiling:
            break
    return (tax * (1 + CESS_RATE)) / next_gain


@dataclass(frozen=True)
class USEquityCostModel:
    other_annual_income: float = 1_400_000.0   # see config.settings.US_EQUITY_OTHER_ANNUAL_INCOME


def classify_and_tax(entry_date: str, exit_date: str, pnl: float, stcg_booked_so_far: float,
                     model: USEquityCostModel) -> dict:
    """
    One trade's (or one still-open position's, marked "as if sold today")
    tax: {"is_long_term", "rate", "tax"}. `stcg_booked_so_far`: the running
    total of OTHER short-term gains already booked this financial year, for
    the progressive calculation above -- the caller is responsible for
    accumulating this correctly across trades it processes in date order
    (see reporting/pool_i.py's _book()).
    """
    is_long_term = holding_days(entry_date, exit_date) > LONG_TERM_HOLDING_DAYS
    gain = max(pnl, 0.0)
    if is_long_term:
        rate = LTCG_RATE * (1 + CESS_RATE)
        tax = gain * rate
    else:
        rate = marginal_stcg_rate(model.other_annual_income, stcg_booked_so_far, gain) if gain else 0.0
        tax = gain * rate
    return {"is_long_term": is_long_term, "rate": rate, "tax": tax}
