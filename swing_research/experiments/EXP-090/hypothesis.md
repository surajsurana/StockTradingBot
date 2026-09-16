# Moving Average Pullback

## Mechanism
In an established uptrend (20-day average above 50-day), a dip to the 20-day average that closes back above it on a green candle is read as buyers defending the trend; the trade targets the recent swing high. A trend-continuation pattern, not an anomaly with an academic literature.

## Rationale
The ported rules exactly as they run in Pool A (SW-014), including the fixed target.

Long only. 1% risk per unit against the pattern's own stop, at most 10 positions, ranked by entry order (no natural cross-sectional ranking measure). The informal 5-year, 26-symbol prototype backtest (242 trades, 37.6% win rate, net +Rs.5,830) was never a walk-forward test; this is its first.

## Rules
Uptrend: 20-day MA above 50-day MA. Pullback: today's Low within 1.5% of the 20-day MA from above, no close below it. Reaction: close above both the 20-day MA and today's Open. Entry on the day all three first hold. Stop: the tighter of (Low minus an ATR buffer) or (20-day MA less 3%), bounded to 1.5%-10% risk. Target: the highest High of the last 20 days including today, taken only if it gives at least 1.8x reward-to-risk.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
