# Earnings Announcement Premium

## Mechanism
A scheduled earnings announcement is an attention-grabbing event: attention-constrained individual investors who rarely sell short are net BUYERS of any stock in the news, and volume predictably surges at the release -- the resulting price pressure lifts the stock before, at and after the announcement (F&L Table VII: ~25 bps in the 10 days before, ~21 bps in the 3-day window, ~30 bps after). Stocks whose past volume concentrates in announcement months carry the bulk of the premium (153 bps/month vs 39 bps for low-concentration stocks). Distinct from PEAD (SW-007), which enters AFTER the announcement conditional on the surprise sign; this enters BEFORE it, unconditionally, and holds through it.

## Rationale
F&L's previous-year-announcement-month forecast (their more accurate method) with the exactly-4-announcements restriction; month-end-to-month-end holding exactly as published; the volume concentration ratio used as the entry RANKING (Signal.confidence) rather than a quintile cut, because this engine holds at most 10 positions -- ranking all qualifying announcers and taking the top of the list is the high-concentration group in practice. Ratio computed from as few as 24 months / 8 announcement months (F&L: 48) as a disclosed warm-up relaxation. Announcement dates from yfinance's reported-earnings history (date only).

LONG ONLY (expected-announcer leg only; the expected non-announcer short leg dropped -- same reason as every prior strategy). 8% protective stop-loss and 1% risk-per-unit sizing, NOT PART OF THE ORIGINAL METHODOLOGY AT ALL. At most 10 positions vs F&L's value-weighted portfolio of hundreds of announcers. India-specific published evidence is weak (see rules) -- disclosed before the experiment, not after.

## Rules
Expected announcer for month t = announced in the same calendar month one year earlier, restricted to firms with exactly 4 announcements in the prior 12 months (93% forecast accuracy). On the last trading day of month t-1 buy every expected announcer; hold until the last trading day of month t; re-form monthly. Cross-sectional sort by the volume concentration ratio: the share of the stock's total volume over the previous 4 years that fell in its actual announcement months, lagged 3 months. US 1973-2004: long-short 61 bps/month (t > 5, Sharpe 0.94), 153 bps/month for high-concentration stocks (~18%/yr). Global 1990-2009 (46 countries): announcement months beat other months by ~11%/yr; India within-country coefficient +0.287%/month, t = 0.63 (positive, not significant).

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
