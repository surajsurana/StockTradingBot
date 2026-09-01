"""
Portfolio B's Telegram message -- structurally identical to
portfolio_c/report.py's own format_portfolio_c_message(), just labeled
for Portfolio B, so the two are distinguishable in the same chat.
"""

from reporting.telegram_templates import _escape_markdown


def format_portfolio_b_message(result: dict) -> str:
    """result: the dict run_portfolio_b_daily() returns."""
    lines = [f"*Portfolio B -- Fixed Watchlist* ({_escape_markdown(result['as_of_date'])})"]

    if result["status"] == "skipped_already_processed":
        lines.append(f"Already processed through {_escape_markdown(result['last_processed_date'])} -- no-op.")
        return "\n".join(lines)

    new_entries = result.get("new_entries", [])
    new_exits = result.get("new_exits", [])

    if new_entries:
        lines.append("\n*Bought:*")
        for e in new_entries:
            lines.append(f"  {_escape_markdown(e['symbol'])}: {e['quantity']} @ "
                          f"Rs.{e['entry_price']:.2f}")
    if new_exits:
        lines.append("\n*Sold:*")
        for e in new_exits:
            pnl = e.get("pnl", 0.0)
            sign = "+" if pnl >= 0 else ""
            lines.append(f"  {_escape_markdown(e['symbol'])}: {e['quantity']} @ "
                          f"Rs.{e['exit_price']:.2f} ({sign}Rs.{pnl:.2f}, {_escape_markdown(e.get('exit_reason', ''))})")
    if not new_entries and not new_exits:
        lines.append("\nNo new entries or exits today.")

    lines.append(f"\nOpen positions: {result['open_positions']}")
    lines.append(f"Cash: Rs.{result['cash']:,.2f}")
    lines.append(f"Equity: Rs.{result['mark_to_market_equity']:,.2f}")

    return "\n".join(lines)
