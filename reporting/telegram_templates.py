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


def _format_bought_sold_section(new_entries: list, new_exits: list) -> list:
    """
    Returns message lines for a numbered *Bought*/*Sold* section plus
    Total Bought/Total Sold aggregate lines at the end -- shared by
    format_execution_notification() and format_strategy_notification()
    so both use IDENTICAL structure. Rewritten 2026-08-19 per direct
    feedback on the prior per-stock-header design: "BOUGHT" was
    confusingly repeated twice per stock (once in a header, once in the
    price line) -- replaced with ONE section header and a numbered list;
    the Signal price (the close the signal was originally computed from,
    the day before -- omitted if not available, e.g. an entry queued
    before this field existed) now shows next to the symbol; Total
    Bought/Total Sold let a glance answer "how much did I put to work /
    take off the table today."

    new_entries / new_exits: same shape
    deployment.paper_trading_engine.run_daily()'s result produces --
    {"symbol", "entry_price"/"exit_price", "quantity", "stop_loss"/
    "reason", optionally "signal_price"}.
    """
    lines = []
    total_bought = 0.0
    total_sold = 0.0

    if new_entries:
        lines.append("*Bought*")
        lines.append("")
        for i, e in enumerate(new_entries, 1):
            signal_note = f" (Signal: {format_metric(e['signal_price'])})" if e.get("signal_price") is not None else ""
            total = e["quantity"] * e["entry_price"]
            total_bought += total
            lines.append(
                f"{i}. {_escape_markdown(e['symbol'])}{_escape_markdown(signal_note)}\n"
                f"{e['quantity']} x {format_metric(e['entry_price'])} = {format_metric(total)}\n"
                f"Stop: {format_metric(e['stop_loss'])}"
            )
            lines.append("")

    if new_exits:
        lines.append("*Sold*")
        lines.append("")
        for i, x in enumerate(new_exits, 1):
            total = x["quantity"] * x["exit_price"] if x.get("quantity") is not None else None
            if total is not None:
                total_sold += total
                fill_line = f"{x['quantity']} x {format_metric(x['exit_price'])} = {format_metric(total)}"
            else:
                fill_line = format_metric(x["exit_price"])
            lines.append(
                f"{i}. {_escape_markdown(x['symbol'])}\n"
                f"{fill_line}\n"
                f"P&L: {format_metric(x['pnl'])} ({_escape_markdown(x['reason'])})"
            )
            lines.append("")

    if new_entries or new_exits:
        lines.append(f"Total Bought = {format_metric(total_bought)}")
        lines.append(f"Total Sold = {format_metric(total_sold)}")

    return lines


def format_strategy_notification(mode: str, strategy_display_name: str,
                                  new_entries: list, new_exits: list, open_positions: list,
                                  daily_pnl: Optional[float], total_equity: float, drawdown_pct: Optional[float],
                                  win_rate: Optional[float], expectancy: Optional[float],
                                  observations: str = "", strategy_id: str = "",
                                  report_links: Optional[dict] = None,
                                  pending_entries: Optional[list] = None,
                                  pending_exits: Optional[list] = None) -> str:
    """
    One strategy's notification for one trading day -- ONE strategy per
    message (never combine strategies), per explicit direction. mode:
    "LIVE" or "PAPER" -- the only visible header difference; every other
    section is identical.

    new_entries / new_exits: list of dicts -- these are ACTUAL FILLS (the
    real execution price, at whatever fill_timing was actually used --
    e.g. "next_day_open" means entry_price/exit_price IS that day's real
    market Open, not an estimate). Same shape
    deployment.paper_trading_engine.run_daily()'s result already
    produces -- {"symbol", "entry_price"/"exit_price", "quantity",
    "stop_loss"/"reason", optionally "signal_date" when the fill resolves
    a signal queued on an earlier day}.
    pending_entries / pending_exits: signals detected TODAY but NOT yet
    filled (only ever populated under fill_timing="next_day_open") --
    {"symbol", "stop_loss"/"exit_reason", "signal_date"}. Shown in their
    own section so a detected-but-unfilled signal is never silently
    invisible (previously looked identical to "no signal at all").
    open_positions: list of {"symbol", "quantity", "current_price",
    "current_value", "unrealized_pnl", "unrealized_pnl_pct"} -- what each
    position is worth NOW, not its entry price (see
    deployment.paper_trading_engine.run_daily()'s open_positions_detail).
    strategy_id: the strategy's PERMANENT id (e.g. "SW-003") -- folded into
    the header line itself as "{emoji} {strategy_id} | {display_name}"
    (per explicit direction, 2026-08-05), so opening Telegram immediately
    shows which strategy generated the message without reading further --
    important once multiple strategies are paper/live trading side by side.
    report_links: optional {"Experiment": "EXP-013", "Daily Report": "path",
    "Drift Report": "path", ...} -- rendered as a final section so the
    detailed reports behind this message are easy to find later.
    """
    pending_entries = pending_entries or []
    pending_exits = pending_exits or []

    emoji = MODE_EMOJI.get(mode, mode)
    if strategy_id:
        header_line = f"*{emoji} {_escape_markdown(strategy_id)} | {_escape_markdown(strategy_display_name)}*"
    else:
        header_line = f"*{emoji} {_escape_markdown(strategy_display_name)}*"
    lines = [header_line, ""]

    if not new_entries and not new_exits and not pending_entries and not pending_exits:
        lines.append("No qualifying setups found today.")
    else:
        lines += _format_bought_sold_section(new_entries, new_exits)
        if pending_entries:
            lines.append("*Queued for Next Open (not yet bought)*")
            lines.append("")
            for e in pending_entries:
                block = f"{_escape_markdown(e['symbol'])}\n"
                if e.get("signal_price") is not None:
                    block += f"Signal {format_metric(e['signal_price'])}\n"
                block += f"Planned stop {format_metric(e['stop_loss'])}"
                lines.append(block)
                lines.append("")
        if pending_exits:
            lines.append("*Queued for Next Open (not yet sold)*")
            lines.append("")
            for x in pending_exits:
                lines.append(f"{_escape_markdown(x['symbol'])}\n{_escape_markdown(x['exit_reason'])}")
                lines.append("")

    lines.append("*Open Positions*")
    lines.append("")
    if open_positions:
        for p in open_positions:
            pnl = p.get("unrealized_pnl")
            pnl_pct = p.get("unrealized_pnl_pct")
            pnl_str = f"{format_metric(pnl)} ({format_metric(pnl_pct)}%)" if pnl is not None else "n/a"
            lines.append(f"{_escape_markdown(p['symbol'])}\nQty {p['quantity']} | "
                         f"Value {format_metric(p.get('current_value'))} | P&L {pnl_str}")
            lines.append("")
    else:
        lines.append("(none)")
        lines.append("")

    lines += [
        "*Summary*",
        f"Daily P&L: {format_metric(daily_pnl)}" if daily_pnl is not None else "Daily P&L: n/a",
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


def format_execution_notification(mode: str, strategy_display_name: str, new_entries: list, new_exits: list,
                                   strategy_id: str = "") -> str:
    """
    A FOCUSED, near-real-time execution confirmation -- sent by
    deployment.paper_trading_engine.resolve_pending_fills_at_open() (via
    run_paper_trading.py's --resolve-at-open mode) immediately after a
    queued signal actually fills at market open, added 2026-08-18 per
    direct user feedback ("I should be getting a Telegram message at the
    live time when something is bought or sold"). Deliberately lean --
    just what was bought/sold, at what price, right now -- NOT a
    replacement for that same strategy's end-of-day
    format_strategy_notification() message, which still reports these
    SAME fills again later alongside the day's full signal-detection
    results, metrics, and open-positions snapshot. Never called when
    nothing actually filled (an empty new_entries/new_exits pair is not
    worth a message -- see run_paper_trading.py's own call site).

    ONE numbered list under a single *Bought*/*Sold* header each (not a
    repeated header per stock), with Total Bought/Total Sold at the end
    -- rewritten 2026-08-19 per direct feedback that "BOUGHT" appearing
    twice per stock (header + price line) was confusing, and to show
    the Signal price (the close the signal was originally computed
    from, the day before) next to each symbol -- omitted for an entry
    queued before this field existed, never a broken "None". See
    _format_bought_sold_section()'s own docstring for the exact layout,
    shared with format_strategy_notification() so both use IDENTICAL
    structure. No Target line -- these strategies have no actual
    profit-target exit rule, only stop-loss/time-based/signal-based, so
    a fabricated target would misrepresent how the position will
    actually exit; per explicit direction, skipped rather than invented.
    """
    emoji = MODE_EMOJI.get(mode, mode)
    if strategy_id:
        header = f"*{emoji} EXECUTED -- {_escape_markdown(strategy_id)} | {_escape_markdown(strategy_display_name)}*"
    else:
        header = f"*{emoji} EXECUTED -- {_escape_markdown(strategy_display_name)}*"
    lines = [header, ""]
    lines += _format_bought_sold_section(new_entries, new_exits)
    return "\n".join(lines).rstrip()


def format_daily_summary(strategy_results: list, closed_trades_today: int, open_positions_total: int,
                          daily_pnl_total: Optional[float], portfolio_equity_total: float,
                          blended_win_rate: Optional[float],
                          total_unrealized_pnl: Optional[float] = None,
                          booked_pnl_today: Optional[float] = None) -> str:
    """
    ONE additional message sent after ALL scheduled strategies have
    finished for the day -- in addition to, never a replacement for, each
    strategy's own individual message. Currently PAPER-only (no live
    system calls this).

    strategy_results: list of {"display_name": str, "new_entries": list,
    "new_exits": list} for every strategy that ran today, in the order
    they ran -- used to build the "Strategies Executed" and "Signals
    Today" sections (both BUYs and SELLs -- a strategy that only closed
    a position today, with no new entry, previously showed "No Setup",
    indistinguishable from a day with no activity at all).
    daily_pnl_total: sum of each strategy's OWN daily_pnl (today's
    mark-to-market equity change -- realized AND unrealized, see
    deployment.paper_trading_engine.run_daily()'s daily_pnl). None if no
    strategy has a prior day to compare against.
    booked_pnl_today: sum of P&L from trades that actually CLOSED today
    (realized only) -- a distinct, more concrete figure from
    daily_pnl_total (which also includes open positions' price movement),
    shown alongside it so "did I actually make/lose money on something I
    sold today" is never conflated with "my paper gains moved because
    prices moved." Optional (None omits the line) so existing callers
    keep working unchanged.
    total_unrealized_pnl: sum of each strategy's open positions'
    unrealized P&L (cumulative since entry, not a single day's change) --
    a distinct figure from both P&L lines above, shown alongside them.
    Optional (None omits the line) so existing callers keep working
    unchanged.
    """
    lines = ["\U0001F4CA DAILY PAPER TRADING SUMMARY", "", "*Strategies Executed*"]
    for r in strategy_results:
        lines.append(f"- {_escape_markdown(r['display_name'])}")

    lines += ["", "*Signals Today*", "--------------"]
    for r in strategy_results:
        n_entries = len(r["new_entries"])
        n_exits = len(r["new_exits"])
        parts = []
        if n_entries:
            parts.append(f"{n_entries} BUY")
        if n_exits:
            parts.append(f"{n_exits} SELL")
        signal_text = ", ".join(parts) if parts else "No Setup"
        lines.append(f"{_escape_markdown(r['display_name'])}: {signal_text}")

    lines += ["", f"*Today's Closed Trades*: {closed_trades_today}"]
    if booked_pnl_today is not None:
        lines.append(f"*Booked P&L (closed trades today)*: {format_metric(booked_pnl_today)}")
    lines += [
        f"*Current Open Positions*: {open_positions_total}",
        f"*Today's Total P&L (mark-to-market)*: {format_metric(daily_pnl_total)}"
        if daily_pnl_total is not None else "*Today's Total P&L (mark-to-market)*: n/a",
    ]
    if total_unrealized_pnl is not None:
        lines.append(f"*Total Unrealized P&L (open positions)*: {format_metric(total_unrealized_pnl)}")
    lines += [
        f"*Portfolio Equity*: {portfolio_equity_total:,.2f}",
        f"*Win Rate*: {format_metric(blended_win_rate)}" if blended_win_rate is not None else "*Win Rate*: n/a",
    ]
    return "\n".join(lines)
