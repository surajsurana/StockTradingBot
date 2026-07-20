"""
Runs a few times during market hours (market open, midday, close -- matches
ARCHITECTURE.md's "checks signals a few times a day, not a tick loop" design
principle) to have the same agent team that picks new trades also watch the
ones already open.

Why this exists: run_daily.py only opens new positions once a day. The hard
stop-loss/target for each position is enforced by a GTT order placed at entry
(execution/execution_engine.py's _place_gtt_exit) -- Zerodha's own servers
handle that trigger, no live quotes or polling needed. But a GTT only reacts
to price. This script is the "judgment" layer on top of that: it re-runs
Technical + Fundamental + News + Research Analyst against every symbol
currently held, exactly as run_daily.py does for new candidates, and exits
early if the picture has turned unfavorable for reasons a price trigger alone
wouldn't catch (bad news, deteriorating fundamentals, a technical reversal).

Usage:
    python monitor_positions.py                 # checks all current holdings
    python monitor_positions.py --paper          # forces paper-equivalent
                                                  # behavior (skips the check
                                                  # entirely -- see below)

Only meaningful once LIVE_TRADING is on -- paper mode has no real Kite
holdings to check (see run_daily.py's get_current_holdings for why), so
--paper here just short-circuits to "nothing to do", matching run_daily.py's
own --paper override semantics.
"""

import sys
from datetime import datetime

from config import settings
from data.fetch_historical import fetch_all, fetch_nifty
from strategies.base import Signal
from strategies.market_regime import build_regime_series
from strategies.technical_agent import get_technical_signals
from strategies.price_action import compute_price_action
from fundamentals.fundamental_agent import fetch_fundamentals, check_health
from news.news_agent import analyze_news_cached, disabled_news_assessment, ClaudeAPIError
from research.research_analyst import analyze_stock
from risk.risk_manager import ApprovedTrade
from risk.trailing_stop import compute_trailing_stop_update
from risk.partial_profit import should_book_partial_profit, compute_extended_target, compute_booking_split
from execution.execution_engine import ExecutionEngine
from execution.positions import fetch_all_holdings
from execution.position_state import (reconcile_closed_positions, load_known_positions,
                                       update_position_stop, record_partial_profit_booking)
from auth.kite_auto_login import ensure_fresh_kite_session
from cio.plan_state import load_monthly_plan, effective_active_strategies
from reporting.telegram_notifier import send_telegram_message


def _trailing_stop_would_be_rejected(new_stop: float, current_price) -> bool:
    """
    True if placing a GTT at new_stop would fail Kite's "trigger prices must
    bracket current price" validation -- i.e. new_stop is at or above the
    current market price. new_stop is computed from the highest price
    reached since entry (a past peak), so if price has since pulled back to
    or through that level, submitting it is guaranteed to be rejected
    outright rather than silently accepted at a wrong level. Returns False
    (assume placeable) if current_price isn't available -- the actual GTT
    call is still the authoritative check either way.
    """
    if current_price is None:
        return False
    return new_stop >= current_price


def _highest_high_since(price_history, opened_at_iso: str):
    """
    Highest daily High from a position's entry date to today, used to arm
    the trailing stop -- compares by DATE only (not full timestamp) since
    price_history's index (from yfinance) and opened_at (server local time)
    aren't guaranteed to share a timezone, and date-level granularity is all
    a daily-candle trailing stop needs anyway. Returns None if no rows fall
    on/after the entry date (shouldn't normally happen for a real open
    position, but a stale/corrupted opened_at shouldn't crash monitoring).
    """
    opened_date = datetime.fromisoformat(opened_at_iso).date()
    subset = price_history.loc[price_history.index.date >= opened_date]
    if subset.empty:
        return None
    return float(subset["High"].max())


def parse_cli_args():
    force_paper = "--paper" in sys.argv[1:]
    return force_paper


def price_pnl_text(holding) -> str:
    """
    Short "current price (P&L Rs., P&L %)" fragment for a holding, using
    last_price straight from Kite's holdings/positions response -- a
    periodic snapshot (not full live-quote-tier data), but good enough to
    show at-a-glance P&L on every position-check Telegram line without
    needing to open Kite separately. Empty string if last_price wasn't
    available for some reason (never blocks the rest of the line).
    """
    if holding.last_price is None or holding.average_price <= 0:
        return ""
    pnl = (holding.last_price - holding.average_price) * holding.quantity
    pnl_pct = (holding.last_price - holding.average_price) / holding.average_price * 100
    return f"Rs.{holding.last_price:,.2f} ({pnl_pct:+.2f}%, Rs.{pnl:+,.2f})"


def evaluate_holding(symbol: str, regime_series, active_strategies: list, entry_price: float = None):
    """
    Re-runs the same Technical + Fundamental + News + Research Analyst
    pipeline run_daily.py uses for new candidates, against an already-held
    symbol -- now also including price-action facts (recent move magnitude,
    position vs moving averages, volume) so a position quietly breaking down
    isn't invisible just because neither strategy generates a SELL signal.

    Returns (ResearchAssessment, price_history), or (None, None) if price
    history/fundamentals couldn't be fetched (treated as "skip this check",
    not as an exit signal -- a data-fetch hiccup shouldn't trigger a real
    sell). price_history is returned alongside the assessment so the caller
    can also run the trailing-stop check without fetching it a second time.
    """
    try:
        price_history = fetch_all([symbol], period="1y").get(symbol)
    except Exception as e:
        print(f"WARNING: could not fetch price history for {symbol}: {e}")
        return None, None
    if price_history is None or len(price_history) < 60:
        return None, None

    technical_signals = get_technical_signals(symbol, price_history, regime_series, active_strategies)
    price_action = compute_price_action(price_history, entry_price=entry_price)

    try:
        metrics = fetch_fundamentals(symbol)
    except Exception as e:
        print(f"WARNING: could not fetch fundamentals for {symbol}: {e}")
        return None, None
    fundamentals_result = check_health(symbol, metrics, settings.FUNDAMENTALS_CRITERIA)

    if settings.USE_NEWS_AGENT:
        # analyze_news_cached (not analyze_news) -- this function runs several
        # times a day against the same held symbols, so it skips the Claude
        # call and reuses the last verdict whenever the headlines haven't
        # actually changed since the previous check.
        news_assessment = analyze_news_cached(symbol, api_key=settings.ANTHROPIC_API_KEY,
                                               max_items=settings.NEWS_MAX_ARTICLES)
    else:
        news_assessment = disabled_news_assessment(symbol)

    assessment = analyze_stock(symbol, technical_signals, fundamentals_result, news_assessment,
                                api_key=settings.ANTHROPIC_API_KEY, price_action=price_action)
    return assessment, price_history


def check_holding(holding, regime_series, active_strategies: list, known_positions: dict,
                   execution_engine: ExecutionEngine) -> tuple[str, bool]:
    """
    Full check + action for ONE held position: fresh Research Analyst
    verdict, early exit if unfavorable, otherwise partial profit booking
    then trailing stop. Returns (checked_line, exited) -- the Telegram
    report line for this symbol, and whether it was fully exited this
    cycle (so the caller can add it to reconciliation).

    Deliberately a single function called per-symbol from main()'s loop and
    wrapped in a try/except there -- an unexpected failure partway through
    one symbol's checks (seen live: an occasional Claude API response
    shape crashing the whole process) must never prevent every OTHER held
    position from being checked in the same cycle.
    """
    price_pnl = price_pnl_text(holding)
    prefix = f"- {holding.symbol}: {price_pnl} -- " if price_pnl else f"- {holding.symbol}: "

    assessment, price_history = evaluate_holding(
        holding.symbol, regime_series, active_strategies, entry_price=holding.average_price,
    )
    if assessment is None:
        return f"{prefix}could not complete a fresh check -- left as-is.", False

    print(f"  Fresh verdict: {assessment.verdict.upper()} ({assessment.confidence:.0%})")

    if assessment.verdict == "unfavorable":
        known = known_positions.get(holding.symbol)
        gtt_id = known.gtt_id if known else None

        exit_signal = Signal(
            symbol=holding.symbol, direction="SELL", entry_price=holding.average_price,
            stop_loss=holding.average_price, target=holding.average_price, confidence=assessment.confidence,
            strategy_name="monitor_positions", reason=assessment.reasoning,
        )
        sell_result = execution_engine.place_order(ApprovedTrade(
            signal=exit_signal, quantity=holding.quantity,
            capital_deployed=holding.quantity * holding.average_price,
        ))
        print(f"  Exited early: {sell_result}")

        if sell_result.get("status") == "success":
            if gtt_id is not None:
                execution_engine.cancel_gtt(gtt_id)
            return (f"{prefix}EXITED EARLY (verdict turned unfavorable, "
                    f"{assessment.confidence:.0%} confidence) -- {assessment.reasoning}"), True
        else:
            # The exit SELL itself failed -- do NOT cancel the GTT and do NOT
            # mark this as exited. This position is still really held, so its
            # GTT stop-loss/target must stay in place; treating a failed exit
            # as successful would leave a real position with no protection.
            print(f"WARNING: exit SELL order for {holding.symbol} did not succeed -- "
                  f"GTT left in place, still counted as held. Result: {sell_result}")
            return (f"{prefix}verdict turned unfavorable ({assessment.confidence:.0%} "
                    f"confidence) but the exit order failed -- still held, GTT stop-loss/target "
                    f"unaffected. {assessment.reasoning}"), False

    trailing_note = ""
    partial_note = ""
    current_quantity = holding.quantity
    known = known_positions.get(holding.symbol)
    has_gtt_state = (known is not None and known.stop_loss is not None
                      and known.target is not None and known.gtt_id is not None)
    highest_high = _highest_high_since(price_history, known.opened_at) if has_gtt_state else None

    # --- Partial profit booking, checked FIRST: it can restructure the
    # GTT and reduce the live quantity, which the trailing-stop check
    # below must then use (via current_quantity / known, both updated
    # in place here), not the pre-booking figures. ---
    if (settings.USE_PARTIAL_PROFIT_BOOKING and has_gtt_state and not known.partial_booked
            and highest_high is not None
            and should_book_partial_profit(
                entry_price=known.entry_price, target=known.target,
                highest_high_since_entry=highest_high,
                activation_fraction=settings.PARTIAL_PROFIT_ACTIVATION_FRACTION,
            )):
        split = compute_booking_split(current_quantity, settings.PARTIAL_PROFIT_BOOKING_FRACTION)
        if split is None:
            print(f"  Partial profit booking triggered for {holding.symbol} but position "
                  f"(qty {current_quantity}) is too small to split -- skipping.")
        else:
            booking_qty, remaining_qty = split
            extended_target = compute_extended_target(
                known.entry_price, known.target, settings.PARTIAL_PROFIT_TARGET_EXTENSION_MULTIPLE,
            )
            runner_signal = Signal(
                symbol=holding.symbol, direction="BUY", entry_price=known.entry_price,
                stop_loss=known.stop_loss, target=extended_target, confidence=assessment.confidence,
                strategy_name="partial_profit", reason="Runner tranche after partial booking",
            )
            try:
                # Reduced-quantity runner GTT placed FIRST -- same lesson
                # as replace_gtt's own fix: never remove existing
                # protection before its replacement is confirmed live.
                new_gtt_id = execution_engine.replace_gtt(known.gtt_id, ApprovedTrade(
                    signal=runner_signal, quantity=remaining_qty,
                    capital_deployed=remaining_qty * known.entry_price,
                ))
                record_partial_profit_booking(holding.symbol, remaining_qty, extended_target, new_gtt_id)
                known.quantity = remaining_qty
                known.target = extended_target
                known.gtt_id = new_gtt_id
                known.partial_booked = True
                current_quantity = remaining_qty
                print(f"  Runner GTT placed for {holding.symbol}: qty {remaining_qty}, "
                      f"stop Rs.{known.stop_loss:,.2f}, extended target Rs.{extended_target:,.2f}")

                booking_price = holding.last_price if holding.last_price else known.entry_price
                booking_signal = Signal(
                    symbol=holding.symbol, direction="SELL", entry_price=booking_price,
                    stop_loss=booking_price, target=booking_price, confidence=assessment.confidence,
                    strategy_name="partial_profit", reason="Partial profit booking",
                )
                booking_result = execution_engine.place_order(ApprovedTrade(
                    signal=booking_signal, quantity=booking_qty,
                    capital_deployed=booking_qty * booking_price,
                ))
                if booking_result.get("status") == "success":
                    partial_note = (f" | Partial profit booked: sold {booking_qty} shares, "
                                     f"{remaining_qty} remain with extended target Rs.{extended_target:,.2f}")
                    print(f"  Partial profit booking SELL succeeded for {holding.symbol}: {booking_result}")
                else:
                    # The runner GTT is already live and correctly covers
                    # remaining_qty -- real risk is bounded to just the
                    # booking_qty shares that failed to sell, not the
                    # whole position. Retrying the sell itself (not the
                    # GTT restructure, which succeeded and is recorded)
                    # is the only thing left to do, next check or manually.
                    print(f"WARNING: partial profit booking SELL for {holding.symbol} failed after "
                          f"the runner GTT was already restructured -- {booking_qty} share(s) are "
                          f"currently unprotected by any stop-loss (real holdings {holding.quantity}, "
                          f"only {remaining_qty} covered by the new GTT). Result: {booking_result}")
                    partial_note = (f" | Partial profit booking SELL FAILED after GTT restructure -- "
                                     f"{booking_qty} share(s) currently unprotected, needs attention")
            except Exception as e:
                print(f"WARNING: could not restructure GTT for partial profit booking on "
                      f"{holding.symbol}: {e} -- original stop-loss/target (covering the full "
                      f"position) remain in place, nothing was sold.")
                partial_note = " | Partial profit booking FAILED, original GTT unaffected"

    # --- Trailing stop -- uses `known`/current_quantity, which may
    # already reflect a partial-profit restructure from just above. ---
    if has_gtt_state:
        if highest_high is None:
            highest_high = _highest_high_since(price_history, known.opened_at)
        if highest_high is not None:
            new_stop = compute_trailing_stop_update(
                entry_price=known.entry_price, current_stop=known.stop_loss,
                target=known.target, highest_high_since_entry=highest_high,
                activation_fraction=settings.TRAILING_STOP_ACTIVATION_FRACTION,
                lock_in_fraction=settings.TRAILING_STOP_LOCK_IN_FRACTION,
            )
            if new_stop is not None and _trailing_stop_would_be_rejected(new_stop, holding.last_price):
                # Kite requires a GTT's stop-loss to sit BELOW current price when
                # placed (the two triggers must bracket the current price). new_stop
                # was computed from the highest price reached since entry -- a past
                # peak -- so if price has since pulled back to/through that level,
                # attempting to place it would be rejected outright (seen live: Kite
                # error "Trigger prices must bracket current price"). Skip this cycle
                # rather than retry a call guaranteed to fail; the ORIGINAL stop is
                # completely unaffected, so nothing is lost by waiting -- if price
                # moves back up past this level, the ratchet will succeed next check.
                print(f"  Trailing stop would raise to Rs.{new_stop:,.2f}, but price has since "
                      f"pulled back to Rs.{holding.last_price:,.2f} -- skipping this cycle, "
                      f"original stop-loss (Rs.{known.stop_loss:,.2f}) unaffected.")
                trailing_note = " | Trailing stop deferred (price pulled back below the computed level)"
            elif new_stop is not None:
                trailing_signal = Signal(
                    symbol=holding.symbol, direction="BUY", entry_price=known.entry_price,
                    stop_loss=new_stop, target=known.target, confidence=assessment.confidence,
                    strategy_name="trailing_stop", reason="Trailing stop ratchet",
                )
                try:
                    new_gtt_id = execution_engine.replace_gtt(known.gtt_id, ApprovedTrade(
                        signal=trailing_signal, quantity=current_quantity,
                        capital_deployed=current_quantity * known.entry_price,
                    ))
                    update_position_stop(holding.symbol, new_stop, new_gtt_id)
                    trailing_note = f" | Trailing stop raised to Rs.{new_stop:,.2f} (locking in gain)"
                    print(f"  Trailing stop raised: Rs.{known.stop_loss:,.2f} -> Rs.{new_stop:,.2f}")
                except Exception as e:
                    print(f"WARNING: could not raise trailing stop for {holding.symbol}: {e} "
                          f"-- original stop-loss (Rs.{known.stop_loss:,.2f}) remains in place.")
                    trailing_note = " | Trailing stop raise FAILED, original stop-loss unaffected"

    return (f"{prefix}held ({assessment.verdict}, {assessment.confidence:.0%} confidence)"
            f"{partial_note}{trailing_note}"), False


def main():
    force_paper = parse_cli_args()
    live_trading = settings.LIVE_TRADING and not force_paper

    print("=" * 60)
    print("POSITION MONITOR -- " + ("LIVE" if live_trading
                                     else "PAPER MODE" + (" (forced by --paper)" if force_paper else "")))
    print("=" * 60)

    if not live_trading:
        print("Not in live mode -- nothing to monitor (paper mode has no real Kite holdings).")
        return

    if not ensure_fresh_kite_session(settings):
        print("\nABORTING -- could not establish a valid Kite session.")
        send_telegram_message(
            "*Position monitor aborted*\n\nCould not establish a valid Kite session.",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        return

    try:
        holdings = fetch_all_holdings(settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN)
    except Exception as e:
        print(f"\nABORTING -- could not fetch current holdings: {e}")
        send_telegram_message(
            f"*Position monitor aborted*\n\nCould not fetch current holdings: {e}",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        return

    closed = reconcile_closed_positions(
        holdings, settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN,
        settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
    )
    if closed:
        print(f"Reconciled {len(closed)} closure(s) since last check: {', '.join(closed)}")

    if not holdings:
        print("No open positions to check.")
        send_telegram_message("*Position monitor*\n\nNo open positions to check.",
                               settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
        return

    print("\nFetching Nifty 50 regime...")
    nifty = fetch_nifty(period="1y")
    regime_series = build_regime_series(nifty)

    active_strategies = effective_active_strategies(load_monthly_plan(), settings)

    execution_engine = ExecutionEngine(
        live_trading=True,
        api_key=settings.KITE_API_KEY,
        access_token=settings.KITE_ACCESS_TOKEN,
        limit_order_buffer_pct=settings.LIMIT_ORDER_BUFFER_PCT,
    )

    known_positions = load_known_positions()
    exited_symbols = []
    checked_lines = []

    for holding in holdings:
        print(f"\nChecking {holding.symbol}...")
        try:
            checked_line, exited = check_holding(
                holding, regime_series, active_strategies, known_positions, execution_engine,
            )
        except Exception as e:
            # A single symbol's check must never take down monitoring for
            # every other position -- seen live: an occasional Claude API
            # response shape (a "thinking" block with no text block, a
            # genuine transient response, not a ClaudeAPIError this file
            # otherwise catches) crashed the whole process mid-run and
            # silently skipped every remaining symbol's trailing-stop/
            # partial-profit/exit checks for that entire cycle. Existing
            # GTT stop-loss/target protection is completely unaffected
            # either way -- this only means today's judgment-layer check
            # for this one symbol didn't run; it'll run again next cycle.
            print(f"WARNING: unexpected error while checking {holding.symbol}: {e} -- skipping this "
                  f"symbol this cycle, its GTT stop-loss/target (if any) is unaffected.")
            checked_lines.append(f"- {holding.symbol}: unexpected error during check ({e}) -- "
                                  f"skipped this cycle, GTT unaffected.")
            continue

        checked_lines.append(checked_line)
        if exited:
            exited_symbols.append(holding.symbol)

    # Early exits placed above still show up in Kite holdings until the sell
    # settles, so reconciliation can't diff them out yet by comparing against
    # a fresh holdings fetch. Instead, reconcile against `holdings` with the
    # exited symbols filtered out by hand -- this is what makes
    # reconcile_closed_positions see them as "closed" right now, with the
    # correct reason, rather than waiting for a later run to (incorrectly)
    # attribute the closure to a GTT trigger.
    if exited_symbols:
        reconcile_closed_positions(
            [h for h in holdings if h.symbol not in exited_symbols],
            settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN,
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
            reason="Exited early by monitor_positions.py (unfavorable verdict)",
        )

    report = (
        f"*Position monitor -- checked {len(holdings)} position(s)*\n\n"
        + "\n".join(checked_lines)
    )
    send_telegram_message(report, settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    try:
        main()
    except ClaudeAPIError as e:
        print(f"\nABORTING -- {e}")
        send_telegram_message(
            f"*Position monitor aborted -- could not reach Claude*\n\n{e}\n\n"
            f"Open positions were not re-checked this run. Their GTT stop-loss/target "
            f"orders remain active and unaffected.",
            settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID,
        )
        sys.exit(1)
