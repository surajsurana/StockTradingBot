"""
Builds P&L summaries from trade logs and (eventually) sends them to WhatsApp.

WhatsApp sending is stubbed for now -- the two realistic options are:
1. Twilio's WhatsApp API (easiest to set up, sandbox is free to test)
2. Meta's WhatsApp Business Platform API directly (more setup, no per-message
   markup from a middleman)
Once you pick one, `send_whatsapp_message` below is the only function that
needs real implementation -- everything upstream (building the report text)
is already done.
"""

from backtest.backtester import BacktestResult


def build_monthly_plan_text(month_label: str, capital_allocated: float, target_return_pct: float,
                             active_strategies: list, risk_per_trade_pct: float = None,
                             notes: str = "") -> str:
    """
    The "here's the plan for this month" message, sent at the start of each
    month. States what capital is being used and what return is being aimed
    for, so there's a clear promise to check the month's actual result
    against later -- see build_monthly_review_text.

    risk_per_trade_pct: optional, a fraction (0.01 = 1%). Omit if the caller
    doesn't have Chief Investment AI's risk decision to report.
    """
    target_amount = capital_allocated * (target_return_pct / 100)
    lines = [
        f"*Monthly trading plan -- {month_label}*",
        "",
        f"Capital being used this month: {capital_allocated:,.2f}",
        f"Target return: {target_return_pct:.1f}% (approx. {target_amount:,.2f})",
    ]
    if risk_per_trade_pct is not None:
        lines.append(f"Risk per trade: {risk_per_trade_pct:.2%} of capital")
    lines.append(f"Active strategies: {', '.join(active_strategies)}")
    if notes:
        lines.append("")
        lines.append(notes)
    return "\n".join(lines)


def build_monthly_review_text(month_label: str, capital_allocated: float, target_return_pct: float,
                               result: BacktestResult) -> str:
    """
    The "here's how last month's plan actually went" message. Meant to be
    sent alongside the next month's plan, so every plan comes with
    accountability for the previous one.
    """
    target_amount = capital_allocated * (target_return_pct / 100)
    actual_pct = (result.total_pnl / capital_allocated * 100) if capital_allocated else 0.0
    hit_target = result.total_pnl >= target_amount
    verdict = "Target met" if hit_target else "Target missed"

    lines = [
        f"*Monthly review -- {month_label}*",
        "",
        f"Target: {target_return_pct:.1f}% ({target_amount:,.2f})",
        f"Actual: {actual_pct:.1f}% ({result.total_pnl:,.2f})",
        verdict,
        "",
        f"Trades closed: {len(result.trades)}",
        f"Win rate: {result.win_rate:.1%}",
        f"Max drawdown: {result.max_drawdown:.1%}",
    ]
    return "\n".join(lines)


def build_report_text(result: BacktestResult, period_label: str) -> str:
    lines = [
        f"*Trading report -- {period_label}*",
        "",
        f"Trades closed: {len(result.trades)}",
        f"Win rate: {result.win_rate:.1%}",
        f"Total P&L: {result.total_pnl:,.2f}",
        f"Ending capital: {result.ending_capital:,.2f}",
        f"Max drawdown: {result.max_drawdown:.1%}",
        "",
    ]

    if result.trades:
        lines.append("Recent trades:")
        for t in result.trades[-5:]:
            outcome = "WIN" if t.pnl > 0 else "LOSS"
            lines.append(
                f"{outcome} {t.symbol} ({t.strategy_name}): "
                f"{t.entry_date} -> {t.exit_date}, P&L {t.pnl:,.2f} ({t.exit_reason})"
            )

    return "\n".join(lines)


def send_whatsapp_message(message: str, to_number: str):
    """
    STUB -- not yet wired to a real WhatsApp API.
    Once you choose Twilio or Meta's WhatsApp Business API, this function
    makes the actual HTTP call. For now it just prints, so the reporting
    pipeline can be tested end-to-end without a live WhatsApp account.
    """
    print("=" * 50)
    print(f"[WHATSAPP -> {to_number or '(not configured)'}]")
    print(message)
    print("=" * 50)


if __name__ == "__main__":
    from backtest.backtester import BacktestResult, ClosedTrade

    fake = BacktestResult(starting_capital=100000, ending_capital=104500)
    fake.trades = [
        ClosedTrade("RELIANCE.NS", "ma_crossover", "2026-01-05", "2026-01-20",
                    2400, 2550, 2350, 2600, 10, 1500, "target"),
        ClosedTrade("TCS.NS", "ma_crossover", "2026-01-10", "2026-01-15",
                    3600, 3550, 3550, 3700, 8, -400, "stop_loss"),
    ]
    print(build_report_text(fake, "test run"))
    print()
    print(build_monthly_plan_text("July 2026", 100000, 3.0, ["ma_crossover", "mean_reversion"],
                                   notes="First live month -- conservative target while we validate real performance."))
    print()
    print(build_monthly_review_text("July 2026", 100000, 3.0, fake))
