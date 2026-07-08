"""
Persists Chief Investment AI's current monthly plan and resolves it against
config.settings -- the missing link that used to make cio/chief_investment_ai.py
a dormant module: it could compute a MonthlyPlan, but nothing saved it or fed
it back into run_daily.py's actual risk settings.

Only ever stores the CURRENT plan (not a growing history) -- the monthly
Telegram messages (see reporting/report_generator.py + monthly_review.py)
already serve as the historical record, so there's no need to duplicate that
here.
"""

import json
import os

from cio.chief_investment_ai import MonthlyPlan

MONTHLY_PLAN_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "monthly_plan.json")


def load_monthly_plan(path: str = MONTHLY_PLAN_PATH) -> MonthlyPlan | None:
    """Returns the persisted plan, or None if Chief Investment AI hasn't run yet."""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return None
    return MonthlyPlan(**json.loads(content))


def save_monthly_plan(plan: MonthlyPlan, path: str = MONTHLY_PLAN_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "month_label": plan.month_label,
            "capital_allocated": plan.capital_allocated,
            "target_return_pct": plan.target_return_pct,
            "active_strategies": plan.active_strategies,
            "risk_per_trade_pct": plan.risk_per_trade_pct,
            "notes": plan.notes,
        }, f, indent=2)


def effective_active_strategies(plan: MonthlyPlan | None, settings) -> list:
    """Which strategies run_daily.py/monitor_positions.py should actually use
    today -- Chief Investment AI's decision if it's made one, otherwise the
    static config default."""
    return list(plan.active_strategies) if plan is not None else list(settings.ACTIVE_STRATEGIES)


def effective_capital_cap(plan: MonthlyPlan | None, real_capital: float) -> float:
    """
    How much capital run_daily.py should actually size trades against today.
    Chief Investment AI's capital_allocated is a CAP on top of real capital,
    not a replacement for it -- this can never let the bot size against money
    that isn't really in the account, and it can never let CIO silently
    authorize more than it actually decided.
    """
    if plan is None:
        return real_capital
    return min(real_capital, plan.capital_allocated)


def effective_risk_per_trade_pct(plan: MonthlyPlan | None, settings) -> float:
    """What fraction of capital to risk per trade today -- Chief Investment
    AI's monthly decision if it's made one, otherwise the static config
    default."""
    return plan.risk_per_trade_pct if plan is not None else settings.RISK_PER_TRADE_PCT


def bump_capital_cap_to_real_capital(plan: MonthlyPlan, real_capital: float) -> bool:
    """
    If real capital has grown past the plan's current cap -- e.g. Suraj
    added funds -- raises plan.capital_allocated to match immediately,
    rather than waiting for next month's Chief Investment AI review (which
    is clamped to at most +/-20% relative change per month).

    A deposit is a fact about the account, not an AI judgment call, so it
    shouldn't be throttled by the guardrail that exists to protect against
    CIO's own reasoning swinging capital allocation too fast in one step.
    This only ever raises the floor to match reality -- it never lowers it,
    and CIO can still choose to deploy less than 100% of real capital next
    month if it judges that prudent; withdrawals already work correctly via
    effective_capital_cap()'s min(real_capital, plan.capital_allocated)
    without needing this function at all.

    Mutates `plan` in place. Returns True if it actually changed anything --
    callers should persist via save_monthly_plan() when True.
    """
    if real_capital > plan.capital_allocated:
        plan.capital_allocated = real_capital
        return True
    return False
