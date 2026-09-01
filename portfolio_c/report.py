"""
Portfolio C's Telegram message -- deliberately its own lean format, not a
reuse of reporting/telegram_templates.py's per-strategy templates: those
assume ONE anchor strategy per message, but Portfolio C's positions can
come from either of two anchor strategies feeding one shared, agent-
allocated capital pool. Reuses that module's _escape_markdown() helper
(the one genuinely shared piece) so dynamic values (symbols, reasons)
can't break Telegram's Markdown parsing.
"""

from reporting.telegram_templates import _escape_markdown


def format_portfolio_c_message(result: dict) -> str:
    """
    result: the dict run_portfolio_c_daily() returns (status, as_of_date,
    new_entries, new_exits, open_positions, cash, mark_to_market_equity).
    """
    lines = [f"*Portfolio C -- Agent Overlay* ({_escape_markdown(result['as_of_date'])})"]

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
