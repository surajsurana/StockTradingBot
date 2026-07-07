"""
Chief Investment AI -- the top-level, monthly-cadence agent.

Every other agent in this system operates day-to-day, per-trade (Technical,
Fundamental, News, Research Analyst, Portfolio Manager). This one is
different: it runs once a month, and its job is two-fold --

1. REVIEW last month: given the plan that was set (capital allocated, target
   return, active strategies) and what actually happened (BacktestResult /
   real trade results), explain in plain language why the target was hit or
   missed, and flag anything that suggests a change is warranted.

2. PLAN the coming month: decide how much capital to deploy, what return to
   target, and which strategies should be active -- informed by the review
   above. Portfolio Manager then operates within whatever this agent decides
   for the month; it never trades outside this envelope.

This is what produces the monthly WhatsApp plan + review messages (see
reporting/report_generator.py's build_monthly_plan_text and
build_monthly_review_text) -- this module decides the NUMBERS that go into
those messages; report_generator just formats them for WhatsApp.

Guardrails (same fail-safe philosophy as every other agent here): the AI's
recommendation is never applied blindly.
- Capital allocated can't change by more than MAX_MONTHLY_CAPITAL_CHANGE_PCT
  in either direction in a single month, no matter what's recommended -- this
  prevents a single bad month's reasoning from causing a runaway swing.
- Target return is clamped to a realistic band for swing trading
  (MIN/MAX_TARGET_RETURN_PCT) -- an unrealistically high target would
  quietly push the system toward riskier trade selection to try to hit it.
- Active strategies are validated against KNOWN_STRATEGIES -- a hallucinated
  or misspelled strategy name is dropped rather than silently breaking
  main.py's STRATEGY_REGISTRY lookup.
- If Claude's response can't be parsed, this falls back to keeping last
  month's plan unchanged, with that fact clearly stated in the notes --
  never guesses.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from news.news_agent import call_claude
from backtest.backtester import BacktestResult

# Extend this whenever a new strategy module is added to strategies/ and
# registered in main.py's STRATEGY_REGISTRY -- keeps the CIO from ever
# recommending a strategy that doesn't actually exist in the codebase.
KNOWN_STRATEGIES = {"ma_crossover", "mean_reversion"}

MAX_MONTHLY_CAPITAL_CHANGE_PCT = 0.20   # capital can move at most +/-20% month over month
MIN_TARGET_RETURN_PCT = 0.5             # don't let a quiet month's reasoning produce a near-zero target
MAX_TARGET_RETURN_PCT = 10.0            # keep ambitions realistic for swing trading, not aggressive/risky


@dataclass
class MonthlyReview:
    month_label: str
    target_met: bool
    actual_return_pct: float
    reasoning: str


@dataclass
class MonthlyPlan:
    month_label: str
    capital_allocated: float
    target_return_pct: float
    active_strategies: list = field(default_factory=list)
    notes: str = ""


def build_review_prompt(month_label: str, previous_plan: MonthlyPlan, result: BacktestResult) -> str:
    actual_pct = (result.total_pnl / previous_plan.capital_allocated * 100) if previous_plan.capital_allocated else 0.0
    target_amount = previous_plan.capital_allocated * (previous_plan.target_return_pct / 100)

    return f"""You are the Chief Investment Officer reviewing last month's trading performance for an Indian (NSE) swing-trading system.

LAST MONTH'S PLAN ({month_label}):
- Capital allocated: Rs.{previous_plan.capital_allocated:,.2f}
- Target return: {previous_plan.target_return_pct:.1f}% (approx. Rs.{target_amount:,.2f})
- Active strategies: {', '.join(previous_plan.active_strategies)}

ACTUAL RESULT:
- P&L: Rs.{result.total_pnl:,.2f} ({actual_pct:.1f}% of allocated capital)
- Trades closed: {len(result.trades)}
- Win rate: {result.win_rate:.1%}
- Max drawdown: {result.max_drawdown:.1%}

In two or three sentences, explain why the target was likely hit or missed, and note whether anything here suggests a change for next month (e.g. a strategy underperforming and worth pausing, drawdown too high to keep the same capital level, or strong enough results to justify modestly increasing capital or target). Be conservative -- a single month is a small sample, so don't recommend dramatic changes based on one month alone.

Respond in EXACTLY this format, nothing else:
REASONING: <two or three sentences>"""


def parse_review_response(month_label: str, raw_response: str, previous_plan: MonthlyPlan,
                           result: BacktestResult) -> MonthlyReview:
    actual_pct = (result.total_pnl / previous_plan.capital_allocated * 100) if previous_plan.capital_allocated else 0.0
    target_amount = previous_plan.capital_allocated * (previous_plan.target_return_pct / 100)
    target_met = result.total_pnl >= target_amount

    reasoning_match = re.search(r"REASONING:\s*(.+)", raw_response, re.IGNORECASE | re.DOTALL)
    reasoning = reasoning_match.group(1).strip() if reasoning_match else (
        f"Could not parse a clear explanation from the model's response: {raw_response[:200]}"
    )

    return MonthlyReview(
        month_label=month_label, target_met=target_met,
        actual_return_pct=actual_pct, reasoning=reasoning,
    )


def build_plan_prompt(month_label: str, review: Optional[MonthlyReview], previous_plan: MonthlyPlan,
                       market_is_bullish: bool) -> str:
    review_text = (
        f"Last month's review: {'target met' if review.target_met else 'target missed'} "
        f"({review.actual_return_pct:.1f}% actual return). {review.reasoning}"
        if review is not None else "This is the first month -- no prior review available."
    )

    return f"""You are the Chief Investment Officer setting the trading plan for the coming month ({month_label}) for an Indian (NSE) swing-trading system.

{review_text}

Currently allocated capital: Rs.{previous_plan.capital_allocated:,.2f}
Current target return: {previous_plan.target_return_pct:.1f}%
Current active strategies: {', '.join(previous_plan.active_strategies)}
Available strategies in the system: {', '.join(sorted(KNOWN_STRATEGIES))}
Broader market (Nifty 50) regime right now: {"bullish (above its 200-day average)" if market_is_bullish else "bearish/choppy (below its 200-day average)"}

Decide the plan for this coming month: how much capital to allocate, what return to target, and which of the available strategies should be active. Be conservative and gradual -- avoid large swings from one month to the next based on limited data. A neutral, unchanged plan is a legitimate and often correct answer when nothing strongly suggests a change.

Respond in EXACTLY this format, nothing else:
CAPITAL: <a number, the Rupee amount to allocate>
TARGET_RETURN_PCT: <a number between 0 and 10, the target return percent for the month>
ACTIVE_STRATEGIES: <comma-separated strategy names from the available list above>
NOTES: <one or two sentences explaining the plan>"""


def parse_plan_response(month_label: str, raw_response: str, previous_plan: MonthlyPlan) -> MonthlyPlan:
    capital_match = re.search(r"CAPITAL:\s*([\d,.]+)", raw_response)
    target_match = re.search(r"TARGET_RETURN_PCT:\s*([\d.]+)", raw_response)
    strategies_match = re.search(r"ACTIVE_STRATEGIES:\s*(.+)", raw_response)
    notes_match = re.search(r"NOTES:\s*(.+)", raw_response, re.IGNORECASE | re.DOTALL)

    if not capital_match or not target_match or not strategies_match:
        # Fail-safe: couldn't parse a usable plan -- keep last month's plan
        # unchanged rather than guess, and say so explicitly.
        return MonthlyPlan(
            month_label=month_label,
            capital_allocated=previous_plan.capital_allocated,
            target_return_pct=previous_plan.target_return_pct,
            active_strategies=list(previous_plan.active_strategies),
            notes=(f"Could not parse a clear plan from the model's response -- keeping last month's "
                   f"plan unchanged. Raw response: {raw_response[:200]}"),
        )

    # --- Capital: clamp to +/- MAX_MONTHLY_CAPITAL_CHANGE_PCT of last month's allocation ---
    raw_capital = float(capital_match.group(1).replace(",", ""))
    min_capital = previous_plan.capital_allocated * (1 - MAX_MONTHLY_CAPITAL_CHANGE_PCT)
    max_capital = previous_plan.capital_allocated * (1 + MAX_MONTHLY_CAPITAL_CHANGE_PCT)
    capital_allocated = max(min_capital, min(max_capital, raw_capital))

    # --- Target return: clamp to a realistic band ---
    raw_target = float(target_match.group(1))
    target_return_pct = max(MIN_TARGET_RETURN_PCT, min(MAX_TARGET_RETURN_PCT, raw_target))

    # --- Active strategies: validate against known strategies, drop anything hallucinated ---
    proposed = [s.strip() for s in strategies_match.group(1).split(",") if s.strip()]
    valid_strategies = [s for s in proposed if s in KNOWN_STRATEGIES]
    if not valid_strategies:
        valid_strategies = list(previous_plan.active_strategies)  # fail-safe: keep previous list

    notes = notes_match.group(1).strip() if notes_match else "(no notes provided)"

    return MonthlyPlan(
        month_label=month_label, capital_allocated=capital_allocated,
        target_return_pct=target_return_pct, active_strategies=valid_strategies, notes=notes,
    )


def review_month(month_label: str, previous_plan: MonthlyPlan, result: BacktestResult,
                  api_key: str, call_fn: Optional[Callable[[str], str]] = None) -> MonthlyReview:
    """Full pipeline: build the review prompt, call Claude, parse the result."""
    prompt = build_review_prompt(month_label, previous_plan, result)
    call = call_fn or (lambda p: call_claude(p, api_key))
    raw_response = call(prompt)
    return parse_review_response(month_label, raw_response, previous_plan, result)


def plan_month(month_label: str, previous_plan: MonthlyPlan, review: Optional[MonthlyReview],
               market_is_bullish: bool, api_key: str,
               call_fn: Optional[Callable[[str], str]] = None) -> MonthlyPlan:
    """Full pipeline: build the planning prompt, call Claude, parse + clamp the result."""
    prompt = build_plan_prompt(month_label, review, previous_plan, market_is_bullish)
    call = call_fn or (lambda p: call_claude(p, api_key))
    raw_response = call(prompt)
    return parse_plan_response(month_label, raw_response, previous_plan)
