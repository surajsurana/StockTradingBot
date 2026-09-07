"""
Pool D's live tick engine -- reuses research_lab/backtesting_engineer.py's
OWN exit-check/sizing logic (never a second, parallel implementation that
could drift), just driven by real, currently-arriving Kite bars instead
of a pre-fetched historical DataFrame. See pool_d/state.py's own module
docstring for the full context (known REJECT strategy, run deliberately,
capital structure).

MARKET-HOURS SAFETY: run_tick() itself does NOT check the clock -- that
is run_pool_d.py's job (the cron-invoked entry point), so this module
stays trivially testable with any `now` a test wants to pass in.
"""

import datetime
from typing import Optional

from research_lab.backtesting_engineer import (
    Trade, _check_exit, _compute_day_context, _risk_per_share, _trade_pnl,
)
from research_lab.risk_manager_research import RiskParameters, should_block_new_trade
from research_lab.strategies.vwap_extension_exhaustion_fade import VwapExtensionExhaustionFadeStrategy

from pool_d.state import append_trade

RISK_PER_TRADE_PCT = 0.01
RISK_PARAMS = RiskParameters()   # defaults: max_trades_per_day=3, daily_loss_limit_pct=0.02 -- matches EXP-008
FORCE_SQUARE_OFF_HOUR = 15.42    # 15:25 -- a few minutes' buffer before the real 15:30 close,
                                  # same spirit as run_daily.py's own "buffer for the data provider" caveats


def _hour_float(ts) -> float:
    return ts.hour + ts.minute / 60


def compute_todays_context(history_by_symbol: dict, today: datetime.date) -> dict:
    """history_by_symbol: {symbol: DataFrame} of the trailing ~90 days of
    5-minute bars (fetched ONCE per day, before the first tick, by
    run_pool_d.py -- expensive relative to a single-day fetch, so never
    repeated within the same day). Returns {symbol: context dict} via
    research_lab's own _compute_day_context(), reused verbatim."""
    context_by_symbol = {}
    for symbol, df in history_by_symbol.items():
        if df is None or df.empty:
            continue
        context_by_symbol[symbol] = _compute_day_context(df, today)
    return context_by_symbol


def process_tick(state: dict, todays_bars_by_symbol: dict, context_by_symbol: dict,
                  capital_per_symbol: float, now: Optional[datetime.datetime] = None,
                  strategy=None) -> dict:
    """
    Mutates `state` in place (positions/cash_by_symbol/trades_today_by_symbol/
    realized_pnl_today_by_symbol) -- caller still owns saving it (matches
    deployment/paper_trading_engine.py's own convention). Returns
    {"new_entries": [...], "new_exits": [...]} -- the fills that happened
    on THIS call, for the caller to build a Telegram notification from.

    todays_bars_by_symbol: {symbol: DataFrame} of TODAY's bars only, from
    open up to the current bar -- a fresh, cheap single-day fetch, NOT the
    same object compute_todays_context() was given.

    strategy: defaults to VwapExtensionExhaustionFadeStrategy() (Pool D's
    only real use), but injectable -- same reason every other engine in
    this program takes its strategy as a parameter rather than hardcoding
    it (deployment/paper_trading_engine.py's run_daily(), etc.): lets
    tests exercise the entry/exit/sizing/force-square-off/missed-poll
    logic with a fully-controlled test double instead of depending on the
    real strategy's own signal conditions ever firing.
    """
    now = now or datetime.datetime.now()
    current_hour = _hour_float(now)
    force_square_off = current_hour >= FORCE_SQUARE_OFF_HOUR

    new_entries, new_exits = [], []
    strategy = strategy or VwapExtensionExhaustionFadeStrategy()

    for symbol, todays_bars in todays_bars_by_symbol.items():
        if todays_bars is None or todays_bars.empty:
            continue
        context = context_by_symbol.get(symbol)
        if context is None:
            continue

        state["cash_by_symbol"].setdefault(symbol, capital_per_symbol)
        state["trades_today_by_symbol"].setdefault(symbol, 0)
        state["realized_pnl_today_by_symbol"].setdefault(symbol, 0.0)

        position = state["positions"].get(symbol)
        latest_bar = todays_bars.iloc[-1]

        if position is not None:
            # Scan every bar strictly AFTER entry, in order -- catches the
            # FIRST stop/target hit even if a poll was missed, same
            # precedence as backtesting_engineer.simulate_symbol()'s own
            # bar-by-bar loop (stop checked before target on a straddling bar).
            entry_ts = datetime.datetime.fromisoformat(position["entry_timestamp"])
            bars_since_entry = todays_bars[todays_bars.index > entry_ts]
            exit_price, exit_reason = None, None
            for _, bar in bars_since_entry.iterrows():
                hit_stop, hit_target = _check_exit(
                    position["direction"], float(bar["Low"]), float(bar["High"]),
                    position["stop_loss"], position["target"],
                )
                if hit_stop:
                    exit_price, exit_reason = position["stop_loss"], "stop_loss"
                    break
                if hit_target:
                    exit_price, exit_reason = position["target"], "target"
                    break
            if exit_price is None and force_square_off:
                exit_price, exit_reason = float(latest_bar["Close"]), "eod_square_off"

            if exit_price is not None:
                pnl = _trade_pnl(position["direction"], position["entry_price"], exit_price, position["quantity"])
                state["cash_by_symbol"][symbol] += pnl
                state["realized_pnl_today_by_symbol"][symbol] += pnl
                state["trades_today_by_symbol"][symbol] += 1
                trade = Trade(
                    symbol=symbol, entry_date=entry_ts.date(), exit_date=now.date(),
                    entry_price=position["entry_price"], exit_price=exit_price,
                    quantity=position["quantity"], pnl=pnl, exit_reason=exit_reason,
                    entry_hour=position["entry_hour"], direction=position["direction"],
                )
                append_trade({"symbol": symbol, "entry_price": position["entry_price"],
                              "exit_price": exit_price, "quantity": position["quantity"],
                              "pnl": round(pnl, 2), "reason": exit_reason, "direction": position["direction"]})
                new_exits.append({"symbol": symbol, "exit_price": exit_price, "pnl": round(pnl, 2),
                                   "reason": exit_reason, "direction": position["direction"]})
                del state["positions"][symbol]
            continue

        if force_square_off:
            continue   # no new entries once we're in the square-off window
        if should_block_new_trade(state["realized_pnl_today_by_symbol"][symbol],
                                   state["trades_today_by_symbol"][symbol], capital_per_symbol, RISK_PARAMS):
            continue

        signal = strategy.generate_signal(todays_bars, context)
        if signal is None:
            continue
        risk_per_share = _risk_per_share(signal.entry_price, signal.stop_loss, signal.direction)
        if risk_per_share <= 0:
            continue
        quantity = int((capital_per_symbol * RISK_PER_TRADE_PCT) / risk_per_share)
        if quantity <= 0:
            continue

        entry_ts = todays_bars.index[-1]
        state["positions"][symbol] = {
            "entry_price": signal.entry_price, "stop_loss": signal.stop_loss, "target": signal.target,
            "quantity": quantity, "direction": signal.direction,
            "entry_hour": _hour_float(entry_ts), "entry_timestamp": entry_ts.isoformat(),
        }
        new_entries.append({"symbol": symbol, "entry_price": signal.entry_price, "stop_loss": signal.stop_loss,
                            "target": signal.target, "quantity": quantity, "direction": signal.direction,
                            "reason": signal.reason})

    return {"new_entries": new_entries, "new_exits": new_exits}
