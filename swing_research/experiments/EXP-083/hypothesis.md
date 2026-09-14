# Crypto Trend Timing (Faber 10-month SMA)

## Mechanism
Prices trend because information is absorbed slowly and investors herd; a long moving-average filter stays with the trend and steps aside for the deepest drawdowns. Faber's rule -- hold an asset when its month-end price is above its 10-month SMA, otherwise cash -- matched buy-and-hold returns across five asset classes over 1973-2012 with roughly half the drawdown, trading 3-4 times a year per asset. Crypto's trends and 70-80% drawdowns are the textbook case for it.

## Rationale
The five largest coins (BTC, ETH, BNB, XRP, SOL) as the timed assets, 20% sleeves, month-end = last UTC calendar day, SMA of the last 10 month-end closes including the current one, exactly as Faber computes it. A coin joins the universe when its Binance history allows a 10-month SMA.

20% protective stop (not in the source) because this engine sizes from the entry-to-stop distance; 4% risk-per-unit against it reproduces the 20% sleeve. Book in USDT with fractional quantities. Costs (0.30%/side + 10 bps spread) and India's 31.2% per-profitable-trade VDA tax with no loss set-off are applied before the audit; pre-tax recorded alongside. Sample: Binance history from 2018 (SOL from 2020), which includes the 2018, 2022 and 2025-26 bear phases.

## Rules
At each month-end, for each asset: buy (or stay long) if the month-end price is above the 10-month simple moving average of month-end prices; otherwise sell to cash. Equal allocation across assets; all decisions on month-end prices only; no stop, no target. US stocks 1901-2012: timing 10.2%/yr vs buy-and-hold 9.3%, max drawdown -50% vs -84%.

## Why this candidate was selected
User-selected from the published-swing-research candidate report.
