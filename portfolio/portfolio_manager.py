"""
Portfolio Manager -- turns Research Analyst verdicts into actual trade decisions.

This is the layer between "what does each stock look like" (Research Analyst,
which already resolves Technical vs Fundamental vs News into one verdict per
symbol) and "what do we actually do about it" (Execution). Its job:

1. Only consider symbols the Research Analyst called "favorable" -- unfavorable
   and neutral verdicts are rejected outright, with the reason recorded.
2. Weight position size by how confident the Research Analyst was, instead of
   sizing every trade the same fixed 1%-of-capital amount. A 90%-confidence
   call gets a bigger position than a 55%-confidence one.
3. When several symbols look good on the same day but there isn't enough
   capital for all of them (a real constraint -- see config.MAX_DEPLOYED_CAPITAL_PCT),
   prioritize the highest-confidence trades first rather than first-come-first-served.
4. Produce one final, auditable decision per candidate symbol: approved (with
   quantity and capital deployed) or rejected (with a plain-language reason),
   so you can always see why any given trade was or wasn't taken.

Important: this does NOT replace Risk Manager's hard safety limits (daily loss
circuit breaker, max open positions, max deployed capital ceiling). Those still
apply exactly as before, in risk/risk_manager.py -- no confidence score can
override them. Portfolio Manager only decides the *relative* sizing between
candidate trades within whatever room Risk Manager's hard limits allow.
"""

from dataclasses import dataclass, field
from typing import Optional

from strategies.base import Signal
from research.research_analyst import ResearchAssessment
from risk.risk_manager import RiskManager, ApprovedTrade

# Below this confidence, a "favorable" verdict still isn't acted on -- the
# Research Analyst itself may be favorable but unsure, and we'd rather skip
# a low-conviction trade than take a small, noisy position in it.
MIN_CONFIDENCE_TO_TRADE = 0.5

# Confidence-to-risk-multiplier mapping: at MIN_CONFIDENCE_TO_TRADE, a trade
# is sized at MIN_RISK_MULTIPLIER x the account's normal per-trade risk; at
# confidence 1.0, it's sized at MAX_RISK_MULTIPLIER x. Linearly interpolated
# in between. This keeps low-conviction trades smaller and high-conviction
# trades bigger, without ever letting any single trade dominate the account
# (Risk Manager's max_deployed_capital_pct still caps the total).
MIN_RISK_MULTIPLIER = 0.5
MAX_RISK_MULTIPLIER = 1.5


@dataclass
class TradeCandidate:
    """One symbol's worth of inputs for the Portfolio Manager to weigh."""
    symbol: str
    signal: Signal                          # the technical strategy's proposed entry/stop/target
    research_assessment: ResearchAssessment  # the synthesized verdict + confidence + reasoning


@dataclass
class PortfolioDecision:
    """The final, auditable outcome for one candidate symbol."""
    symbol: str
    approved: bool
    quantity: int = 0
    capital_deployed: float = 0.0
    confidence: float = 0.0
    risk_multiplier: float = 0.0
    reason: str = ""
    approved_trade: Optional[ApprovedTrade] = field(default=None, repr=False)


def confidence_to_risk_multiplier(confidence: float) -> float:
    """
    Maps a Research Analyst confidence (0.0-1.0) to a risk multiplier applied
    to the account's normal per-trade risk percentage. Returns 0.0 if
    confidence is below MIN_CONFIDENCE_TO_TRADE (i.e. "don't trade this").
    """
    if confidence < MIN_CONFIDENCE_TO_TRADE:
        return 0.0

    span = 1.0 - MIN_CONFIDENCE_TO_TRADE
    if span <= 0:
        return MAX_RISK_MULTIPLIER

    t = (confidence - MIN_CONFIDENCE_TO_TRADE) / span
    t = max(0.0, min(1.0, t))
    return MIN_RISK_MULTIPLIER + t * (MAX_RISK_MULTIPLIER - MIN_RISK_MULTIPLIER)


def _rejection_reason_for_risk_manager_state(risk_manager: RiskManager, signal: Signal, risk_pct: float) -> str:
    """
    Risk Manager's evaluate() just returns None on rejection without saying
    why -- this re-runs the same checks evaluate() ran, in the same order,
    to give an accurate plain-language reason for the audit trail.

    Needs the specific signal/risk_pct that was evaluated (not just
    risk_manager's state) to tell apart two very different rejections that
    both come out of the same sizing code: a stock that's simply too
    expensive for the risk budget at this capital level (nothing to do with
    other trades) vs. capital genuinely already spoken for by higher-priority
    trades or existing holdings. Confusing these was a real bug -- a
    real-money run once reported "fully allocated to higher-confidence
    trades already approved today" for the very first and only candidate of
    the day, because both cases fell through to the same message.
    """
    if risk_manager.daily_loss_breached():
        return "Daily loss circuit breaker has been tripped -- no new positions today"
    if risk_manager.open_positions_count >= risk_manager.max_open_positions:
        return "Already at the maximum number of open positions"

    risk_amount = risk_manager.capital * risk_pct
    risk_per_share = signal.entry_price - signal.stop_loss
    if risk_per_share <= 0:
        return "Signal's stop-loss is at or above its entry price -- can't size a position"

    quantity = int(risk_amount / risk_per_share)
    if quantity <= 0:
        return (f"Risk budget for this trade (Rs.{risk_amount:,.2f}) can't buy even 1 share within "
                f"the stop-loss distance (Rs.{risk_per_share:,.2f}/share at a Rs.{signal.entry_price:,.2f} "
                f"entry) -- the stock is too expensive for the available capital at this risk level, "
                f"not a competing trade.")

    # Past this point the trade would have sized fine on its own (quantity > 0
    # from risk alone) -- any rejection from here on is genuinely about
    # capital already committed to higher-priority trades or existing
    # holdings, not this stock's price. No need to split further: whether the
    # remaining budget was zero or just short of 1 share, the cause is the
    # same.
    return ("Insufficient remaining capital budget -- fully allocated to "
            "higher-confidence trades already approved today (or existing holdings)")


def allocate(candidates: list[TradeCandidate], risk_manager: RiskManager) -> list[PortfolioDecision]:
    """
    Full pipeline: filters candidates down to favorable, sufficiently-confident
    verdicts, prioritizes them by confidence (highest first) so limited capital
    goes to the strongest convictions, sizes each via Risk Manager with a
    confidence-weighted risk percentage, and returns one PortfolioDecision per
    candidate (approved or rejected, always with a reason).

    Mutates risk_manager's state (via on_trade_opened) as it approves trades,
    so each subsequent candidate in the same call sees the remaining capital
    budget correctly -- this is what makes the capital-conflict prioritization
    work when multiple candidates compete for the same limited budget.
    """
    decisions: list[PortfolioDecision] = []

    # Step 1: filter out anything that isn't an actionable favorable verdict.
    actionable = []
    for candidate in candidates:
        verdict = candidate.research_assessment.verdict
        confidence = candidate.research_assessment.confidence

        if candidate.signal is None:
            decisions.append(PortfolioDecision(
                symbol=candidate.symbol, approved=False, confidence=confidence,
                reason="No technical signal proposed for this symbol today -- nothing to size.",
            ))
            continue

        if verdict != "favorable":
            decisions.append(PortfolioDecision(
                symbol=candidate.symbol, approved=False, confidence=confidence,
                reason=f"Research Analyst verdict was '{verdict}', not 'favorable' -- no trade taken.",
            ))
            continue

        multiplier = confidence_to_risk_multiplier(confidence)
        if multiplier <= 0.0:
            decisions.append(PortfolioDecision(
                symbol=candidate.symbol, approved=False, confidence=confidence,
                reason=(f"Verdict was favorable, but confidence ({confidence:.0%}) is below the "
                        f"minimum ({MIN_CONFIDENCE_TO_TRADE:.0%}) required to act on it."),
            ))
            continue

        actionable.append((candidate, multiplier))

    # Step 2: prioritize by confidence, highest first -- limited capital goes
    # to the strongest-conviction trades before weaker ones.
    actionable.sort(key=lambda pair: pair[0].research_assessment.confidence, reverse=True)

    # Step 3: size each in priority order, updating risk_manager state as we go
    # so capital conflicts are resolved correctly (earlier == higher priority).
    for candidate, multiplier in actionable:
        confidence = candidate.research_assessment.confidence
        risk_pct = risk_manager.risk_per_trade_pct * multiplier

        approved_trade = risk_manager.evaluate(candidate.signal, risk_pct_override=risk_pct)

        if approved_trade is None:
            decisions.append(PortfolioDecision(
                symbol=candidate.symbol, approved=False, confidence=confidence,
                risk_multiplier=multiplier,
                reason=_rejection_reason_for_risk_manager_state(risk_manager, candidate.signal, risk_pct),
            ))
            continue

        risk_manager.on_trade_opened(approved_trade)
        decisions.append(PortfolioDecision(
            symbol=candidate.symbol, approved=True,
            quantity=approved_trade.quantity,
            capital_deployed=approved_trade.capital_deployed,
            confidence=confidence, risk_multiplier=multiplier,
            reason=(f"Approved: favorable verdict at {confidence:.0%} confidence -- "
                    f"sized at {multiplier:.2f}x normal per-trade risk."),
            approved_trade=approved_trade,
        ))

    return decisions


def build_decision_log(decisions: list[PortfolioDecision], gtt_status: dict | None = None,
                        fill_prices: dict | None = None) -> str:
    """
    Human-readable audit trail of every candidate considered today, in the
    order they were decided (approved trades first, in priority order, then
    rejections) -- this is the record of "why" for each decision, for review
    or for a WhatsApp report.

    Every approved trade's Qty/Rate/GTT/Stop-loss share one consistent
    basis: Rate, which is the real average fill price when known (a LIMIT
    buy can fill at a BETTER price than the limit sent -- a real trade
    filled at 344.10 against a 349.15 limit, and reporting the limit price
    as "the price" never matched what Kite actually showed), falling back
    to signal.entry_price only if the fill price isn't available yet.
    GTT%/Stop-loss% are computed from that same Rate, so everything in the
    block relates consistently to the one number actually shown. Amount is
    Portfolio Manager's budgeted capital_deployed (qty * entry_price at
    sizing time) -- it can differ slightly from Rate x Qty when the real
    fill price isn't exactly the sizing estimate, which is normal.

    gtt_status: optional {symbol: pre-formatted GTT status string} --
    Portfolio Manager only ever knows the sizing decision, not whether the
    safety-net GTT actually got placed (that's only known after
    execution_engine.place_order() runs, later than this function is first
    called for the pre-execution console log). A production trade once had
    its GTT silently rejected (a tick-size mismatch) while the BUY itself
    succeeded, leaving a real position with no stop-loss for hours before
    anyone noticed by checking Kite directly -- passing this in (from
    run_daily.py, building the post-execution Telegram report) makes that
    failure show up in the message itself instead.

    fill_prices: optional {symbol: real average fill price}, same timing
    reasoning as gtt_status -- only known after execution.
    """
    lines = ["PORTFOLIO MANAGER -- DECISION LOG", "=" * 40]

    approved = [d for d in decisions if d.approved]
    rejected = [d for d in decisions if not d.approved]

    lines.append(f"\n{len(approved)} trade(s) approved, {len(rejected)} rejected.\n")

    if approved:
        lines.append("APPROVED:")
        for d in approved:
            lines.append(f"  - {d.symbol}")
            lines.append(f"      Qty: {d.quantity}")

            signal = d.approved_trade.signal if d.approved_trade else None
            rate = fill_prices.get(d.symbol) if fill_prices else None
            if rate is None and signal is not None:
                rate = signal.entry_price
            if rate is not None:
                lines.append(f"      Rate: Rs.{rate:,.2f}")
            lines.append(f"      Amount: Rs.{d.capital_deployed:,.2f}")

            if signal is not None and rate is not None and rate > 0:
                target_pct = (signal.target - rate) / rate * 100
                stop_pct = (rate - signal.stop_loss) / rate * 100
                lines.append(f"      GTT Rate: Rs.{signal.target:,.2f} (+{target_pct:.2f}%)")
                lines.append(f"      Stop Loss Rate: Rs.{signal.stop_loss:,.2f} (-{stop_pct:.2f}%)")

            gtt_text = gtt_status.get(d.symbol) if gtt_status else None
            if gtt_text:
                lines.append(f"      GTT Status: {gtt_text}")

            lines.append(f"      Confidence: {d.confidence:.0%} ({d.risk_multiplier:.2f}x sizing)")
            lines.append(f"      Reason: {d.reason}")

    if rejected:
        lines.append("\nREJECTED:")
        for d in rejected:
            lines.append(f"  - {d.symbol}: {d.reason}")

    return "\n".join(lines)
