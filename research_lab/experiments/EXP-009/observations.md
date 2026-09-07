# Analysis: Intra-Sector Pair Spread Reversion — REJECT

The headline numbers (Sharpe 3.08, profit factor 6.16, 71% win rate) look compelling, but they are built on only **7 trades** — a sample so small that a single outlier trade drives most of the statistics. This is a classic case of overfitting the illusion of edge: the mechanism (intra-sector pair spread reversion) may be real, but 7 fills cannot distinguish genuine mean-reversion from noise or a handful of lucky regime-specific setups.

The concentration data confirms fragility. All P&L is bucketed under "Unknown" sector, meaning the sector-dependency of the hypothesis was never actually validated by sector — we have no evidence the effect holds across the sectors it's theoretically designed for. Timing is similarly lopsided: 86% of P&L (2,409.2 of 2,795.3) came from the 9:00 entry-hour bucket alone, with only 386.1 from 10:00 and nothing beyond that. This suggests the "edge" may just be an opening-range volatility artifact rather than a durable spread-reversion signal. No regime breakdown exists at all, so bullish/bearish dependency is untested.

Most damning: the single out-of-sample trade lost money (expectancy -142.80), directly contradicting the in-sample profile. That's the tell that in-sample performance was curve-fit noise, not signal.

**Follow-ups:** (1) Re-run with a properly defined universe of sector pairs and require 30+ OOS trades before re-evaluating. (2) Isolate the 9:00 entry-hour effect — test whether it's actually an opening-range reversion strategy mislabeled as pair-spread reversion.