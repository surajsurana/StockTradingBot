"""
Shared strategy-notification Telegram formatter -- NEW, ADDITIVE FILE
(2026-08-04), does not modify report_generator.py or telegram_notifier.py.
Production's existing live-trading message text remains entirely inline
in run_daily.py/monitor_positions.py/execution/position_state.py,
UNCHANGED, per explicit direction: "Keep the production Telegram
implementation completely untouched... migrating the live system [is] a
separate production-approved task."

Currently called ONLY by the isolated deployment/ (paper trading) system,
with mode="PAPER". Designed so a FUTURE, separately-approved migration of
the live call sites to mode="LIVE" would need no format changes here --
only the header and any live-specific fields (e.g. broker fill
confirmation) would need to be threaded through by that future work.

format_strategy_notification() builds message TEXT ONLY -- sending is
still reporting.telegram_notifier.send_telegram_message()'s job (a
generic, mode-agnostic HTTP POST helper, reused unmodified).
"""

from typing import Optional

from reporting.format_utils import format_metric

MODE_HEADERS = {
    "LIVE": "\U0001F680 LIVE TRADING",     # rocket
    "PAPER": "\U0001F9EA PAPER TRADING",   # test tube
}

MODE_EMOJI = {
    "LIVE": "\U0001F680",     # rocket
    "PAPER": "\U0001F9EA",    # test tube
}

# Telegram's legacy Markdown parse_mode (used unmodified by
# reporting.telegram_notifier.send_telegram_message()) treats _ * ` [ as
# formatting delimiters -- an UNPAIRED occurrence anywhere in the message
# (e.g. "signal_exit", "stop_loss", or an underscore-heavy file path like
# "fifty_two_week_high_momentum") makes Telegram reject the ENTIRE message
# with a 400 "can't find end of the entity" error. Per Telegram's Bot API
# docs, these four characters can be escaped with a preceding backslash to
# be rendered literally. Applied to every DYNAMIC (interpolated) value
# below -- never to this module's own static *bold* markers.
_MARKDOWN_SPECIAL_CHARS = "_*`["


def _escape_markdown(value) -> str:
    text = str(value)
    for ch in _MARKDOWN_SPECIAL_CHARS:
        text = text.replace(ch, f"\\{ch}")
    return text


def format_strategy_notification(mode: str, strategy_display_name: str,
                                  new_entries: list, new_exits: list, open_positions: list,
                                  daily_pnl: float, total_equity: float, drawdown_pct: Optional[float],
                                  win_rate: Optional[float], expectancy: Optional[float],
                                  observations: str = "", strategy_id: str = "",
                                  report_links: Optional[dict] = None) -> str:
    """
    One strategy's notification for one trading day -- ONE strategy per
    message (never combine strategies), per explicit direction. mode:
    "LIVE" or "PAPER" -- the only visible header difference; every other
    section is identical.

    new_entries / new_exits: list of dicts (same shape
    deployment.paper_trading_engine.run_daily()'s result already
    produces -- {"symbol", "entry_price"/"exit_price", ...}).
    open_positions: list of {"symbol", "entry_price", "quantity", "stop_loss"}.
    strategy_id: the strategy's PERMANENT id (e.g. "SW-003") -- folded into
    the header line itself as "{emoji} {strategy_id} | {display_name}"
    (per explicit direction, 2026-08-05), so opening Telegram immediately
    shows which strategy generated the message without reading further --
    important once multiple strategies are paper/live trading side by side.
    report_links: optional {"Experiment": "EXP-013", "Daily Report": "path",
    "Drift Report": "path", ...} -- rendered as a final section so the
    detailed reports behind this message are easy to find later.
    """
    emoji = MODE_EMOJI.get(mode, mode)
    if strategy_id:
        header_line = f"*{emoji} {_escape_markdown(strategy_id)} | {_escape_markdown(strategy_display_name)}*"
    else:
        header_line = f"*{emoji} {_escape_markdown(strategy_display_name)}*"
    lines = [header_line, ""]

    if not new_entries and not new_exits:
        lines.append("No qualifying setups found today.")
    else:
        if new_entries:
            lines.append("*New Entries*")
            for e in new_entries:
                lines.append(f"- {_escape_markdown(e['symbol'])}: entry {format_metric(e['entry_price'])} "
                             f"x {e['quantity']}, stop {format_metric(e['stop_loss'])}")
            lines.append("")
        if new_exits:
            lines.append("*Exits*")
            for x in new_exits:
                lines.append(f"- {_escape_markdown(x['symbol'])}: exit {format_metric(x['exit_price'])}, "
                             f"P&L {format_metric(x['pnl'])} ({_escape_markdown(x['reason'])})")
            lines.append("")

    lines.append("*Open Positions*")
    if open_positions:
        for p in open_positions:
            lines.append(f"- {_escape_markdown(p['symbol'])}: entry {format_metric(p['entry_price'])} "
                         f"x {p['quantity']}, stop {format_metric(p['stop_loss'])}")
    else:
        lines.append("(none)")
    lines.append("")

    lines += [
        "*Summary*",
        f"Daily P&L: {daily_pnl:,.2f}",
        f"Total Equity: {total_equity:,.2f}",
        f"Drawdown: {format_metric(drawdown_pct)}%" if drawdown_pct is not None else "Drawdown: n/a",
        f"Win Rate: {format_metric(win_rate)}" if win_rate is not None else "Win Rate: n/a",
        f"Expectancy: {format_metric(expectancy)}" if expectancy is not None else "Expectancy: n/a",
    ]

    if observations:
        lines += ["", "*Observations*", _escape_markdown(observations)]

    if report_links:
        lines += ["", "*Reports*"]
        for label, path in report_links.items():
            lines.append(f"{_escape_markdown(label)}: {_escape_markdown(path)}")

    return "\n".join(lines)


def format_daily_summary(strategy_results: list, closed_trades_today: int, open_positions_total: int,
                          daily_pnl_total: float, portfolio_equity_total: float,
                          blended_win_rate: Optional[float]) -> str:
    """
    ONE additional message sent after ALL scheduled strategies have
    finished for the day -- in addition to, never a replacement for, each
    strategy's own individual message. Currently PAPER-only (no live
    system calls this).

    strategy_results: list of {"display_name": str, "new_entries": list,
    "new_exits": list} for every strategy that ran today, in the order
    they ran -- used to build the "Strategies Executed" and "Signals
    Today" sections.
    """
    lines = ["\U0001F4CA DAILY PAPER TRADING SUMMARY", "", "*Strategies Executed*"]
    for r in strategy_results:
        lines.append(f"- {_escape_markdown(r['display_name'])}")

    lines += ["", "*Signals Today*", "--------------"]
    for r in strategy_results:
        n_entries = len(r["new_entries"])
        signal_text = f"{n_entries} BUY" if n_entries > 0 else "No Setup"
        lines.append(f"{_escape_markdown(r['display_name'])}: {signal_text}")

    lines += [
        "", f"*Today's Closed Trades*: {closed_trades_today}",
        f"*Current Open Positions*: {open_positions_total}",
        f"*Today's P&L*: {daily_pnl_total:,.2f}",
        f"*Portfolio Equity*: {portfolio_equity_total:,.2f}",
        f"*Win Rate*: {format_metric(blended_win_rate)}" if blended_win_rate is not None else "*Win Rate*: n/a",
    ]
    return "\n".join(lines)
