# Intra-Sector Pair Spread Reversion

## Mechanism
Pre-select 4-5 historically highly-correlated stock pairs within the same sector (e.g., two large private banks, two large IT majors). Track the intraday price ratio (spread) between each pair relative to its own 20-day intraday mean and standard deviation. When the spread deviates beyond 2 standard deviations AND the sector index itself has not moved commensurately (i.e., the divergence is idiosyncratic to the pair, not sector-wide), simultaneously go long the laggard and short the leader in equal notional value.

## Rationale
Two fundamentally similar, highly correlated stocks temporarily diverging without a sector-level catalyst is most plausibly explained by short-term liquidity/order-flow imbalance (a large order in one name, index-fund rebalancing flows, algo-driven dispersion) rather than genuine differential information -- these tend to be arbitraged back by relative-value desks, providing a real, market-neutral mean-reversion edge distinct from single-stock directional bets.

## Rules
Entry: when spread z-score crosses 2.0 (single-bar trigger, no waiting for confirmation). Stop: spread z-score reaching 3.5 (widens further). Target: spread z-score reverting to 0.5, or EOD square-off (both legs closed together). Position sized market-neutral (equal notional long/short). Only trade pairs with rolling correlation > 0.8 over trailing 60 days.

## Why this candidate was selected
Same hypothesis as EXP-009 -- re-validated on a wider sample.
