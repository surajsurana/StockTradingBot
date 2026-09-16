# Crypto Weekly Trend Timing (Faber 10-month SMA, weekly cadence)

## Mechanism
Identical to SW-020: a long moving-average filter stays with a trend and steps aside for deep drawdowns. This variant reads the filter every week instead of every month -- crypto trades every day, so a weekly check is a natural cadence; the question is whether faster reaction to a trend change (helps) outweighs reacting to more noise (hurts) once costs and tax are paid.

## Rationale
Same five majors, 20% sleeves, 20% stop, ~300-day SMA warmed up from full history; decision day = every Sunday (UTC) instead of month-end.

Identical to SW-020's (long only, no leverage, 20% stop not in the source, costs and 31.2% tax before the audit); the only change is decision frequency.

## Rules
Exactly Faber's rule (see SW-020): hold above the 10-month SMA, cash below it -- read on the last day of every ISO week instead of every month.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
