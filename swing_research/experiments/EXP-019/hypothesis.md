# Short-Term Reversal

## Mechanism
Significant NEGATIVE autocorrelation in individual stock returns at short (weekly-to-monthly) lags -- the opposite sign from the 3-12 month momentum effect (Jegadeesh & Titman 1993). Stocks with the worst returns over the prior month subsequently outperform, and vice versa -- the mirror image of every momentum strategy already tested in this program.

## Rationale
1-month formation / 1-month holding -- the paper's own headline, most-cited specification. Single-vintage holding, not the paper's overlapping-portfolio construction -- identical structural adaptation to every prior cross-sectional strategy in this program, approved 2026-08-05, polarity reversed and horizon shortened vs. the momentum strategies.

LONG ONLY (approved, disclosed, same reason as every prior strategy -- no NSE SLB infrastructure for a genuine short). SINGLE-VINTAGE HOLDING instead of the paper's overlapping-portfolio construction -- mirrors every prior cross-sectional strategy's own approved deviation. EXIT RULE is ONLY the 21-trading-day time-stop or the synthetic protective stop -- deliberately no percentile-based early exit, same discipline established for every prior strategy. PROTECTIVE STOP-LOSS (8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- the source paper is a factor-return study with no position-level risk management whatsoever. NAMING: explicitly distinguished from the unrelated existing production strategy strategies/mean_reversion.py (an RSI/Bollinger-based per-symbol technical signal, a completely different mechanism) -- this strategy's key is short_term_reversal, never mean_reversion, to avoid any confusion between the two.

## Rules
Formation period: prior ONE MONTH return (the paper's headline, most-replicated lag; other lags examined but 1-month is primary). Cross-sectional decile sort by formation-period return at each formation date. Long the BOTTOM decile (worst performers / 'losers'); the paper's zero-cost portfolio shorts the top decile (best performers / 'winners'). Holding period: one month, standard Jegadeesh-style overlapping-portfolio construction (new position formed every month).

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
