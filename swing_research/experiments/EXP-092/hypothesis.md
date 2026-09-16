# Volume-Backed Breakout

## Mechanism
A close above the prior 20-day high on at least 1.5x normal volume is read as institutional buying starting a move; the trade rides it to a fixed 2x reward-to-risk target. Trend initiation, not an anomaly with an academic literature.

## Rationale
The ported rules exactly as they run in Pool A (SW-015), including the fixed target.

Long only. 1% risk per unit against the pattern's own stop, at most 10 positions, ranked by entry order. The informal 5-year, 26-symbol prototype backtest (415 trades, 37.1% win rate, net +Rs.11,507) was never a walk-forward test; this is its first.

## Rules
Breakout: Close above the highest High of the prior 20 days (excluding today). Volume: today's Volume at least 1.5x the prior 20-day average. Entry on the day both hold. Stop: the tighter of (prior 20-day high minus an ATR buffer) or (1.5 ATR below entry). Target: entry plus 2x the stop distance.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
