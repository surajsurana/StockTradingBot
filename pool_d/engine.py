"""
Pool D's live tick engine -- reuses research_lab/backtesting_engineer.py's
OWN exit-check/sizing logic (never a second, parallel implementation that
could drift), just driven by real, currently-arriving Kite bars instead
of a pre-fetched historical DataFrame, and settled against ONE shared
book (see pool_d/state.py's own module docstring for the full context:
known REJECT strategy, run deliberately; single Rs.1,00,000 book across
the whole universe per explicit direction 2026-09-10).

Book mechanics:
- Sizing: risk_per_share from the signal's own stop, quantity =
  (starting_capital x RISK_PER_TRADE_PCT) / risk_per_share -- the same
  formula as the research engine, but on the shared book's capital --
  then CAPPED by the cash actually free (a Rs.1,00,000 book cannot
  hold ten Rs.20,000 positions at once; the backtest's per-symbol books
  never had this constraint, which is the one behavioural difference
  and it is disclosed here).
- Entries reserve entry_price x quantity of cash (shorts too -- treated
  as margin, the simplest consistent rule); exits release it plus P&L.
- Daily loss limit is POOL-level (realized_pnl_today vs the book), the
  per-symbol max-trades-per-day cap stays per symbol -- both via
  research_lab/risk_manager_research.py's should_block_new_trade().

MARKET-HOURS SAFETY: this module does NOT check the clock -- that is
run_pool_d.py's job (the cron-invoked entry point), so this stays
trivially testable with any `now` a test wants to pass in.
"""

import datetime
from typing import Optional

from research_lab.backtesting_engineer import Trade, _check_exit, _risk_per_share, _trade_pnl
from research_lab.risk_manager_research import RiskParameters, should_block_new_trade
from research_lab.strategies.vwap_extension_exhaustion_fade import VwapExtensionExhaustionFadeStrategy

from pool_d.state import append_trade

RISK_PER_TRADE_PCT = 0.01
RISK_PARAMS = RiskParameters()   # defaults: max_trades_per_day=3, daily_loss_limit_pct=0.02 -- matches EXP-008
FORCE_SQUARE_OFF_HOUR = 15.42    # 15:25 -- a few minutes' buffer before the real 15:30 close,
                                  # same spirit as run_daily.py's own "buffer for the data provider" caveats


def _hour_float(ts) -> float:
    return ts.hour + ts.minute / 60


def process_tick(state: dict, todays_bars_by_symbol: dict, context_by_symbol: dict,
                  now: Optional[datetime.datetime] = None, strategy=None) -> dict:
    """
    Mutates `state` in place (positions / cash / trades_today_by_symbol /
    realized_pnl_today) -- caller still owns saving it (matches
    deployment/paper_trading_engine.py's own convention). Returns
    {"new_entries": [...], "new_exits": [...]} -- the fills that happened
    on THIS call.

    todays_bars_by_symbol: {symbol: DataFrame} of TODAY's bars only, from
    open up to the current bar -- a fresh, cheap single-day fetch.

    strategy: defaults to VwapExtensionExhaustionFadeStrategy() (Pool D's
    only real use), but injectable -- same reason every other engine in
    this program takes its strategy as a parameter: lets tests exercise
    the entry/exit/sizing/force-square-off/missed-poll logic with a
    fully-controlled test double.
    """
    now = now or datetime.datetime.now()
    current_hour = _hour_float(now)
    force_square_off = current_hour >= FORCE_SQUARE_OFF_HOUR
    capital = float(state["starting_capital"])

    new_entries, new_exits = [], []
    strategy = strategy or VwapExtensionExhaustionFadeStrategy()

    # 1. Exits first, so cash released today is available to entries in the same tick.
    for symbol in list(state["positions"].keys()):
        todays_bars = todays_bars_by_symbol.get(symbol)
        if todays_bars is None or todays_bars.empty:
            continue
        position = state["positions"][symbol]
        latest_bar = todays_bars.iloc[-1]
        # Scan every bar strictly AFTER entry, in order -- catches the FIRST
        # stop/target hit even if a poll was missed, same precedence as
        # backtesting_engineer.simulate_symbol()'s own bar-by-bar loop.
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
        if exit_price is None:
            continue

        pnl = _trade_pnl(position["direction"], position["entry_price"], exit_price, position["quantity"])
        state["cash"] += position["entry_price"] * position["quantity"] + pnl
        state["realized_pnl_today"] += pnl
        state["trades_today_by_symbol"][symbol] = state["trades_today_by_symbol"].get(symbol, 0) + 1
        Trade(   # same record shape as the research engine; kept for parity, not persisted as-is
            symbol=symbol, entry_date=entry_ts.date(), exit_date=now.date(),
            entry_price=position["entry_price"], exit_price=exit_price,
            quantity=position["quantity"], pnl=pnl, exit_reason=exit_reason,
            entry_hour=position["entry_hour"], direction=position["direction"],
        )
        append_trade({"symbol": symbol, "entry_price": position["entry_price"],
                      "exit_price": exit_price, "quantity": position["quantity"],
                      "pnl": round(pnl, 2), "reason": exit_reason, "direction": position["direction"],
                      "exit_date": now.date().isoformat()})
        new_exits.append({"symbol": symbol, "exit_price": exit_price, "pnl": round(pnl, 2),
                          "reason": exit_reason, "direction": position["direction"]})
        del state["positions"][symbol]

    if force_square_off:
        return {"new_entries": new_entries, "new_exits": new_exits}   # no new entries in the square-off window

    # 2. Entries.
    for symbol, todays_bars in todays_bars_by_symbol.items():
        if todays_bars is None or todays_bars.empty or symbol in state["positions"]:
            continue
        context = context_by_symbol.get(symbol)
        if context is None:
            continue
        trades_today = state["trades_today_by_symbol"].get(symbol, 0)
        if should_block_new_trade(state["realized_pnl_today"], trades_today, capital, RISK_PARAMS):
            continue

        signal = strategy.generate_signal(todays_bars, context)
        if signal is None:
            continue
        risk_per_share = _risk_per_share(signal.entry_price, signal.stop_loss, signal.direction)
        if risk_per_share <= 0:
            continue
        quantity = int((capital * RISK_PER_TRADE_PCT) / risk_per_share)
        affordable = int(state["cash"] // signal.entry_price) if signal.entry_price > 0 else 0
        quantity = min(quantity, affordable)
        if quantity <= 0:
            continue

        entry_ts = todays_bars.index[-1]
        state["cash"] -= signal.entry_price * quantity
        state["positions"][symbol] = {
            "entry_price": signal.entry_price, "stop_loss": signal.stop_loss, "target": signal.target,
            "quantity": quantity, "direction": signal.direction,
            "entry_hour": _hour_float(entry_ts), "entry_timestamp": entry_ts.isoformat(),
        }
        new_entries.append({"symbol": symbol, "entry_price": signal.entry_price, "stop_loss": signal.stop_loss,
                            "target": signal.target, "quantity": quantity, "direction": signal.direction,
                            "reason": signal.reason})

    return {"new_entries": new_entries, "new_exits": new_exits}
