"""
Transaction-cost model for research_lab backtests -- Zerodha's published
equity-intraday (MIS) charge schedule on NSE, applied per Trade as a
post-process, plus an explicit bid-ask/slippage assumption.

Added 2026-09-07 for EXP-011 (End-of-Day Reversal), the first hypothesis
whose documented GROSS edge (3.8-6.9 bps per day in the US source,
Baltussen/Da/Soebhag 2025) is the same order of magnitude as an Indian
retail round-trip. Every earlier experiment (EXP-001 to EXP-010) was
judged gross of costs -- harmless in hindsight, since all were REJECTed
even before costs, but it would flatter a small-edge, high-turnover
strategy like this one into a false PASS. From here on run_experiment.py
applies this model by default (--no-costs to disable).

Charges (zerodha.com/charges, read 2026-09-07):
  brokerage            0.03% of order value or Rs.20 per executed order, whichever is LOWER (each side)
  STT                  0.025% of the SELL-side value
  exchange txn (NSE)   0.00307% of turnover (both sides)
  SEBI                 Rs.10 per crore of turnover (both sides)
  stamp duty           0.003% of the BUY-side value
  GST                  18% on brokerage + exchange txn + SEBI charges

Spread/slippage is NOT a Zerodha charge -- it's a modelling assumption:
crossing the bid-ask on entry and again on exit. Default 2 bps per side
(a liquid NSE large-cap's typical half-spread), settable per run.

Only single-leg Trades are supported (entry_price/exit_price are real
prices, quantity is real shares). pairs_simulator's combined Trades carry
a price RATIO, so costs are not applied on the pairs path -- disclosed by
research_director.run_backtest_with_audit(), not silently skipped.
"""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class IntradayCostModel:
    brokerage_pct: float = 0.0003         # 0.03% of order value ...
    brokerage_cap_per_order: float = 20.0  # ... or Rs.20, whichever is lower
    stt_sell_pct: float = 0.00025          # 0.025% of sell value
    exchange_txn_pct: float = 0.0000307    # 0.00307% of turnover
    sebi_pct: float = 0.000001             # Rs.10 per crore = 0.0001%
    stamp_buy_pct: float = 0.00003         # 0.003% of buy value
    gst_pct: float = 0.18                  # on brokerage + exchange txn + SEBI
    spread_bps_per_side: float = 2.0       # modelling assumption, not a Zerodha charge

    def round_trip_cost(self, buy_value: float, sell_value: float) -> float:
        """Total cost in rupees of one intraday round trip: one buy order of
        `buy_value` and one sell order of `sell_value` (either order first)."""
        turnover = buy_value + sell_value
        brokerage = (min(buy_value * self.brokerage_pct, self.brokerage_cap_per_order)
                     + min(sell_value * self.brokerage_pct, self.brokerage_cap_per_order))
        stt = sell_value * self.stt_sell_pct
        exchange = turnover * self.exchange_txn_pct
        sebi = turnover * self.sebi_pct
        stamp = buy_value * self.stamp_buy_pct
        gst = (brokerage + exchange + sebi) * self.gst_pct
        spread = turnover * self.spread_bps_per_side / 10_000
        return brokerage + stt + exchange + sebi + stamp + gst + spread

    def describe(self) -> dict:
        return {
            "source": "Zerodha equity intraday (MIS) charges, NSE, read 2026-09-07",
            "brokerage": f"{self.brokerage_pct * 100:.3f}% or Rs.{self.brokerage_cap_per_order:.0f}/order, whichever lower",
            "stt_sell_side_pct": self.stt_sell_pct * 100,
            "exchange_txn_pct": self.exchange_txn_pct * 100,
            "sebi_pct": self.sebi_pct * 100,
            "stamp_buy_side_pct": self.stamp_buy_pct * 100,
            "gst_pct": self.gst_pct * 100,
            "spread_bps_per_side_assumption": self.spread_bps_per_side,
        }


def trade_cost(trade, model: IntradayCostModel) -> float:
    """Round-trip cost of one single-leg Trade. A long buys at entry and
    sells at exit; a short sells at entry and buys back at exit."""
    entry_value = trade.entry_price * trade.quantity
    exit_value = trade.exit_price * trade.quantity
    if trade.direction == "BUY":
        return model.round_trip_cost(buy_value=entry_value, sell_value=exit_value)
    return model.round_trip_cost(buy_value=exit_value, sell_value=entry_value)


def apply_transaction_costs(trades: list, model: IntradayCostModel) -> tuple:
    """Returns (net_trades, total_cost): a NEW list of Trades with each
    pnl reduced by its own round-trip cost (inputs untouched), plus the
    total cost deducted. Everything downstream (compute_metrics, the
    Auditor) then sees net-of-cost P&L with no changes of its own."""
    net_trades = []
    total_cost = 0.0
    for t in trades:
        cost = trade_cost(t, model)
        total_cost += cost
        net_trades.append(replace(t, pnl=t.pnl - cost))
    return net_trades, total_cost
