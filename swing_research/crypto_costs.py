"""
Costs and Indian tax for the crypto lane (Pool F, 2026-09-13) -- applied
to a list of swing_research.backtesting_engine.Trade as post-processes,
so the Statistical Auditor judges what an Indian resident would actually
keep, and reports carry both pre-tax and post-tax figures.

EXCHANGE COSTS (CryptoCostModel): a percentage fee per side (default
0.30% -- between Binance's 0.10% and CoinDCX's ~0.50% for small volumes)
plus a spread/slippage assumption per side (default 10 bps; INR pairs
are thinner than NSE large-caps). Both are modelling assumptions,
settable per run and recorded in each experiment's parameters.

INDIA VDA TAX (Income-tax Act s.115BBH / s.194S, as understood on
2026-09-13 -- to be confirmed with a CA before any real money):
  - 30% on the gain from each transfer, plus 4% health & education cess
    -> 31.2% effective. Applied PER TRADE: a losing trade gives no relief
    (no set-off against other VDA gains or any other income, no carry
    forward) -- the harshest reading, deliberately.
  - Only the cost of acquisition is deductible: exchange fees are NOT.
    So the tax base is the raw price gain; fees come out of the post-tax
    number separately.
  - 1% TDS on every sale value. Refundable at filing, so it is NOT a P&L
    cost -- reported as "TDS withheld" (a capital lock), never deducted.
"""

from dataclasses import dataclass, replace

INDIA_VDA_TAX_RATE = 0.312   # 30% + 4% cess, per profitable transfer, no loss offset
INDIA_VDA_TDS_RATE = 0.01    # on sale value, refundable


@dataclass(frozen=True)
class CryptoCostModel:
    fee_pct_per_side: float = 0.003
    spread_bps_per_side: float = 10.0

    def round_trip_cost(self, buy_value: float, sell_value: float) -> float:
        turnover = buy_value + sell_value
        return turnover * self.fee_pct_per_side + turnover * self.spread_bps_per_side / 10_000

    def describe(self) -> dict:
        return {"fee_pct_per_side": self.fee_pct_per_side * 100,
                "spread_bps_per_side_assumption": self.spread_bps_per_side}


def _values(trade) -> tuple:
    entry_value = float(trade.entry_price) * float(trade.quantity)
    exit_value = float(trade.exit_price) * float(trade.quantity)
    return entry_value, exit_value


def trade_cost(trade, model: CryptoCostModel) -> float:
    entry_value, exit_value = _values(trade)
    return model.round_trip_cost(entry_value, exit_value)


def trade_tax(trade, rate: float = INDIA_VDA_TAX_RATE) -> float:
    """Tax on the RAW gain (sale minus cost of acquisition); zero on a loss."""
    return max(float(trade.pnl), 0.0) * rate


def apply_crypto_costs(trades: list, model: CryptoCostModel) -> list:
    """New Trades with exchange fee + spread taken out of pnl (pre-tax)."""
    return [replace(t, pnl=float(t.pnl) - trade_cost(t, model)) for t in trades]


def apply_india_tax(trades_raw: list, model: CryptoCostModel, rate: float = INDIA_VDA_TAX_RATE) -> list:
    """New Trades whose pnl is what an Indian resident keeps: raw gain,
    minus fees, minus 31.2% of the raw gain when positive. Takes the RAW
    (fee-free) trades so the tax base is the un-netted price gain."""
    return [replace(t, pnl=float(t.pnl) - trade_cost(t, model) - trade_tax(t, rate)) for t in trades_raw]


def tax_summary(trades_raw: list, model: CryptoCostModel, rate: float = INDIA_VDA_TAX_RATE,
                tds_rate: float = INDIA_VDA_TDS_RATE) -> dict:
    """Both sides of the ledger for reports and the dashboard."""
    raw = sum(float(t.pnl) for t in trades_raw)
    fees = sum(trade_cost(t, model) for t in trades_raw)
    tax = sum(trade_tax(t, rate) for t in trades_raw)
    sales = sum(_values(t)[1] for t in trades_raw)
    winners = sum(1 for t in trades_raw if float(t.pnl) > 0)
    return {
        "raw_pnl": round(raw, 2), "fees_and_spread": round(fees, 2),
        "pre_tax_pnl": round(raw - fees, 2), "tax": round(tax, 2), "post_tax_pnl": round(raw - fees - tax, 2),
        "tds_withheld_refundable": round(sales * tds_rate, 2), "tax_rate": rate, "tds_rate": tds_rate,
        "winning_trades": winners, "losing_trades": len(trades_raw) - winners,
    }
