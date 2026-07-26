"""
Cross-sectional simulation engine -- unlike backtesting_engineer.py's
simulate_symbol()/run_backtest() (which simulate each symbol completely
independently, no awareness of any other symbol), this iterates by
TIMESTAMP across the WHOLE universe simultaneously, computing a
MarketState snapshot once per bar and sharing it with every symbol's
strategy call that same bar. Built to unblock EXP-004 (Breadth-Thrust
Laggard Catch-Up) and designed as a generic, reusable foundation for any
future cross-sectional hypothesis, per Suraj's explicit request
(2026-07-25) -- not a one-off breadth-only implementation.

Reuses backtesting_engineer.py's Trade/_check_exit/_trade_pnl/
_risk_per_share/_compute_day_context (import, not duplicated) -- the
per-symbol exit mechanics (mandatory EOD square-off, direction-agnostic
stop/target, one-trade-per-symbol-per-day default) are IDENTICAL to the
single-symbol engine; only the signal-generation step differs (each
symbol's strategy call also receives a shared MarketState this bar).

Output shape matches backtesting_engineer.run_backtest()'s exactly
({"trades", "trading_calendar", "symbols", "capital_per_symbol"}) so it
plugs into the same compute_metrics()/walk_forward_split()/
statistical_auditor.audit() pipeline with no changes needed downstream.

Entirely within research_lab -- never imported by, or importing from, the
swing production package.
"""

from typing import Optional

import pandas as pd

from research_lab.backtesting_engineer import Trade, _check_exit, _compute_day_context, _risk_per_share, _trade_pnl
from research_lab.base import Strategy
from research_lab.market_state import compute_market_state
from research_lab.risk_manager_research import RiskParameters, should_block_new_trade


def simulate_universe_cross_sectional(data: dict, strategy: Strategy, capital_per_symbol: float,
                                       risk_per_trade_pct: float, sector_map: dict,
                                       risk_params: Optional[RiskParameters] = None,
                                       nifty_data: Optional[pd.DataFrame] = None,
                                       leader_laggard_n: int = 5) -> dict:
    """
    data: {symbol: DataFrame of intraday bars}, same shape run_backtest()
    takes. Each symbol still gets its own independent capital_per_symbol
    (same first-pass methodology as the single-symbol engine) -- the
    cross-sectional part is only about SIGNAL GENERATION seeing the whole
    universe each bar, not shared capital or shared position limits.

    nifty_data: NIFTY 50 index intraday bars (data/fetch_kite_intraday.py's
    fetch_nifty_intraday()), for MarketState.nifty_return_since_open_pct.
    Optional -- strategies must handle it being None gracefully.
    """
    prepped = {}
    for symbol, df in data.items():
        if df is None or df.empty:
            continue
        df = df.copy()
        df["trade_date"] = df.index.date
        prepped[symbol] = df

    all_days = sorted({d for df in prepped.values() for d in df["trade_date"].unique()})

    nifty_prepped = None
    if nifty_data is not None and not nifty_data.empty:
        nifty_prepped = nifty_data.copy()
        nifty_prepped["trade_date"] = nifty_prepped.index.date

    trades = []
    calendar = set()

    for trade_date in all_days:
        day_bars = {}
        day_contexts = {}
        for symbol, df in prepped.items():
            today_df = df[df["trade_date"] == trade_date].drop(columns=["trade_date"])
            if len(today_df) < 4:
                continue  # partial/holiday-truncated day, not enough bars to matter
            day_bars[symbol] = today_df
            day_contexts[symbol] = _compute_day_context(df.drop(columns=["trade_date"]), trade_date)
        if not day_bars:
            continue
        calendar.add(trade_date)

        nifty_today = None
        if nifty_prepped is not None:
            nifty_today_full = nifty_prepped[nifty_prepped["trade_date"] == trade_date].drop(columns=["trade_date"])
            nifty_today = nifty_today_full if not nifty_today_full.empty else None

        all_timestamps = sorted(set().union(*(set(df.index) for df in day_bars.values())))

        positions = {}      # symbol -> position dict
        trades_today = {}   # symbol -> count
        pnl_today = {}       # symbol -> realized pnl today

        for i, ts in enumerate(all_timestamps):
            is_last_timestamp_of_day = i == len(all_timestamps) - 1

            # 1. Check exits for every open position using this bar, if this symbol has one
            for symbol in list(positions.keys()):
                df = day_bars[symbol]
                if ts not in df.index:
                    continue
                bar = df.loc[ts]
                position = positions[symbol]
                hit_stop, hit_target = _check_exit(
                    position["direction"], float(bar["Low"]), float(bar["High"]),
                    position["stop_loss"], position["target"],
                )
                symbol_is_at_its_own_last_bar = ts == df.index[-1]
                if hit_stop or hit_target:
                    exit_price = position["stop_loss"] if hit_stop else position["target"]
                    exit_reason = "stop_loss" if hit_stop else "target"
                elif symbol_is_at_its_own_last_bar or is_last_timestamp_of_day:
                    exit_price = float(bar["Close"])
                    exit_reason = "eod_square_off"
                else:
                    continue
                pnl = _trade_pnl(position["direction"], position["entry_price"], exit_price, position["quantity"])
                trades.append(Trade(
                    symbol=symbol, entry_date=trade_date, exit_date=trade_date,
                    entry_price=position["entry_price"], exit_price=exit_price,
                    quantity=position["quantity"], pnl=pnl, exit_reason=exit_reason,
                    entry_hour=position["entry_hour"], direction=position["direction"],
                ))
                trades_today[symbol] = trades_today.get(symbol, 0) + 1
                pnl_today[symbol] = pnl_today.get(symbol, 0.0) + pnl
                del positions[symbol]

            if is_last_timestamp_of_day:
                continue  # no new entries on the day's final bar

            # 2. Shared MarketState snapshot for this timestamp, from every
            # symbol's bars up to and including `ts` -- no lookahead.
            bars_so_far_by_symbol = {}
            for symbol, df in day_bars.items():
                sub = df[df.index <= ts]
                if not sub.empty:
                    bars_so_far_by_symbol[symbol] = sub
            nifty_so_far = None
            if nifty_today is not None:
                nifty_sub = nifty_today[nifty_today.index <= ts]
                nifty_so_far = nifty_sub if not nifty_sub.empty else None
            market_state = compute_market_state(
                bars_so_far_by_symbol, sector_map, ts,
                nifty_bars_so_far=nifty_so_far, leader_laggard_n=leader_laggard_n,
            )

            # 3. New signals -- only for symbols with a bar exactly at `ts`
            # (not stale data from an earlier bar) and without an open position.
            for symbol, bars_so_far in bars_so_far_by_symbol.items():
                if symbol in positions or bars_so_far.index[-1] != ts:
                    continue
                if risk_params is None:
                    if trades_today.get(symbol, 0) >= 1:
                        continue
                elif should_block_new_trade(pnl_today.get(symbol, 0.0), trades_today.get(symbol, 0),
                                             capital_per_symbol, risk_params):
                    continue

                signal = strategy.generate_signal(bars_so_far, day_contexts.get(symbol), market_state)
                if signal is None:
                    continue
                risk_per_share = _risk_per_share(signal.entry_price, signal.stop_loss, signal.direction)
                if risk_per_share <= 0:
                    continue
                quantity = int((capital_per_symbol * risk_per_trade_pct) / risk_per_share)
                if quantity <= 0:
                    continue
                positions[symbol] = {
                    "entry_price": signal.entry_price, "stop_loss": signal.stop_loss,
                    "target": signal.target, "quantity": quantity, "direction": signal.direction,
                    "entry_hour": ts.hour + ts.minute / 60,
                }

    return {
        "trades": trades, "trading_calendar": sorted(calendar),
        "symbols": list(prepped.keys()), "capital_per_symbol": capital_per_symbol,
    }
