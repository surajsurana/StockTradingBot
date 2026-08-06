# Execution Realism Study (Research Only)

**Status: Complete. Findings and recommendation only — NO framework changes implemented, per explicit direction.**

Generated: 2026-08-05.

## Objective

Every experiment in this program to date has assumed a strategy's signal, generated from day T's completed daily candle, is FILLED at day T's own Close price (see `swing_research/base.py`'s `Strategy.entry_signal_at()` docstring and every strategy module's own execution-assumption disclosure). In live trading, this fill is not literally achievable the same way — a real order placed after seeing the completed candle can only realistically fill at the next trading day's Open (or via a market-on-close order type, which has its own broker-specific mechanics not modeled here). This study quantifies how much that difference actually matters, before any strategy is considered for LIVE promotion.

## Scope (per explicit direction)

- **SW-003 (52-Week High Momentum) and SW-008 (Short-Term Reversal) only** — the two current paper-trading candidates, not a re-run of every historical experiment.
- Read-only analysis. No modification to `swing_research/backtesting_engine.py` or any frozen file.

## Methodology

For each strategy, the SAME real trade list (`simulate_portfolio()`, identical to every other experiment) was re-generated. For every trade, the next trading day's Open price after both the entry date and exit date was looked up (from the same already-fetched OHLCV data — no new data source). An ALTERNATE trade list was built substituting these next-day-open prices for the actual entry/exit prices, and aggregate metrics were recomputed via `swing_research.metrics.compute_metrics()` — the same, unmodified function every experiment uses. Overnight gap (`next-day Open / that day's Close - 1`) was also measured directly across every trade's actual entry and exit days.

Note: absolute CAGR figures below differ slightly from each strategy's own saved base-run experiment (EXP-013/EXP-020) because this is a fresh re-run against a slightly later data cutoff (today vs. several days ago) — expected, disclosed variance, not a discrepancy in the finding itself (which is about the DELTA between the two fill assumptions on the same trade list).

## Findings

### 52-Week High Momentum (SW-003)

324 trades (316 with a valid next-day-open substitute; 8 skipped — no further trading day existed in the dataset, e.g. a trade open at the very end of the 10-year window).

| Metric | Close-fill (current assumption) | Next-day-open-fill (alternate) | Delta |
|---|---|---|---|
| CAGR | 15.19% | 14.26% | -0.93pp |
| Sharpe | 0.903 | 0.942 | +0.039 |
| Sortino | 1.443 | 1.075 | **-0.368** |
| Win Rate | 33.95% | 34.81% | +0.86pp |
| Expectancy | ₹959.76 | ₹883.11 | -₹76.65 |
| Max Drawdown | 28.9% | 23.4% | **-5.5pp** |

Overnight gap (entry days, n=324): avg **-0.056%**, best **+8.41%**, worst **-17.66%**.
Overnight gap (exit days, n=316): avg **+0.055%**, best **+9.34%**, worst **-14.45%**.

### Short-Term Reversal (SW-008)

1,254 trades (1,246 with a valid substitute; 8 skipped).

| Metric | Close-fill (current assumption) | Next-day-open-fill (alternate) | Delta |
|---|---|---|---|
| CAGR | 15.97% | 16.0% | +0.03pp |
| Sharpe | 0.872 | 0.9 | +0.028 |
| Sortino | 1.028 | 0.832 | **-0.196** |
| Win Rate | 45.06% | 45.83% | +0.77pp |
| Expectancy | ₹270.70 | ₹273.58 | +₹2.88 |
| Max Drawdown | 50.5% | 41.21% | **-9.29pp** |

Overnight gap (entry days, n=1,254): avg **+0.131%**, best **+9.48%**, worst **-18.53%**.
Overnight gap (exit days, n=1,246): avg **+0.141%**, best **+10.00%**, worst **-12.69%**.

## Interpretation

**Headline return metrics are NOT materially affected.** CAGR, win rate, and expectancy all shift by well under 1 percentage point (or a few rupees) for both strategies — the direction of these shifts isn't even consistent between the two strategies (SW-003's CAGR drops slightly under next-day-open fill; SW-008's rises slightly), which is itself evidence that this isn't a systematic bias favoring the current assumption, just noise-level variation.

**Sortino and Max Drawdown shift more meaningfully, and in a consistent direction.** Both strategies show Sortino *worsening* (SW-003: -0.368, SW-008: -0.196) and Max Drawdown *improving* (SW-003: -5.5pp, SW-008: -9.29pp) under next-day-open fill. This is a real, non-trivial, tail-risk-sensitive effect — the close-fill assumption and the next-day-open assumption paint a genuinely different picture of downside risk specifically, even though they agree closely on average return.

**Overnight gaps themselves are non-trivial at the extremes.** Worst-case overnight gaps of **-17.66%** (SW-003) and **-18.53%** (SW-008) are large enough that a strategy relying on same-day-close fills is implicitly assuming away a real execution risk that would actually be faced in live trading — an order can't literally be placed at a price that was already known to be the day's final close.

## Recommendation

**A future framework update to model a more realistic fill assumption (e.g. next-day-open, or actual broker market-on-close order mechanics) would be justified before any strategy is promoted to LIVE trading** — not because it would overturn any PASS/REJECT verdict already reached (headline CAGR/win-rate/expectancy are not materially sensitive to this assumption), but because the tail-risk metrics that matter most for a LIVE capital-allocation decision (Sortino, Max Drawdown) show a real, consistent, non-trivial shift, and worst-case overnight gaps of nearly -19% represent genuine execution risk this program's backtests have not yet modeled.

This is a recommendation only. No change has been made to `swing_research/backtesting_engine.py`, any strategy file, or any frozen framework file. If and when the user decides to act on this recommendation, it would be scoped as its own separate, explicitly-approved framework change — not bundled into any individual strategy's evaluation.
