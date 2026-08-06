# Strategy Library: Post-Earnings Announcement Drift (PEAD) (SW-007)

**Status: DEFERRED — historical earnings surprise dataset unavailable. No Research Verdict. No backtest was run.**

## Original Publication (for reference, when data becomes available)

Bernard, V. and Thomas, J. (1989), "Post-Earnings-Announcement Drift: Delayed Price Response or Risk Premium?", *Journal of Accounting Research*; Bernard and Thomas (1990), "Evidence That Stock Prices Do Not Fully Reflect The Implications Of Current Earnings For Future Earnings", *Journal of Accounting and Economics*. Original Standardized Unexpected Earnings (SUE) construction: Foster, Olsen and Shevlin (1984).

## Why Deferred

PEAD's core signal is SUE — a standardized measure of how much a quarter's actual earnings surprised the market, requiring **~8-10 years of quarterly earnings-surprise history per symbol** across the 457-symbol frozen universe, to support this program's standard pipeline (2-year validation, 10-year evaluation, mandatory recent-period check, post-COVID robustness run).

**Investigation performed (2026-08-05):**
- `fundamentals/fundamental_agent.py`: point-in-time-only snapshot (trailing P/E, ROE, debt/equity, revenue growth) — no earnings-surprise field, no historical time-series capability.
- `yfinance` (the only data source integrated anywhere in this program), live-tested against real NSE symbols (RELIANCE.NS, TCS.NS):
  - `Ticker.earnings_history` returns the correct *fields* (`epsActual`, `epsEstimate`, `epsDifference`, `surprisePercent`) — but only **~4 trailing quarters (~1 year)**, roughly a tenth of what's needed.
  - `quarterly_income_stmt`/`quarterly_financials` EPS rows: similarly capped at **~5 quarters**.
  - `Ticker.calendar`: forward-looking only (next unannounced earnings date) — no historical use.
- No cached earnings/fundamentals dataset, consensus-estimate archive, or SUE data exists anywhere else in the repository (confirmed via repo-wide search).

**This is a hard data-availability ceiling, not a disclosed-assumption situation** like every other strategy's NSE adaptations (long-only, single-vintage holding, synthetic stop-loss, etc.) — those were deliberate, faithful-implementation choices within available data. This is the absence of the data itself.

## What Was NOT Done (per explicit direction)

- PEAD was **not** implemented using the incomplete (~1-year) data.
- The evaluation period was **not** reduced to fit available data.
- No substitute/proxy signal was used in place of earnings surprise.
- No approximation was created that would change the published strategy's actual mechanism.
- The frozen research framework (`acceptance_criteria.py`, `evidence_quality.py`, `cross_strategy_review.py`, `deployment/`) was **not** weakened, bypassed, or modified to accommodate this strategy.

## Revisit Condition

PEAD remains eligible for the exact same frozen research protocol used for every other strategy in this program, if a historical earnings/analyst-consensus dataset covering the Nifty 500 for approximately 10 years becomes available — e.g. a paid data vendor (I/B/E/S-style consensus data, Refinitiv, Bloomberg), or an Indian-market-specific historical fundamentals source (Screener.in export, Trendlyne, Tijori Finance, or an NSE corporate-announcements archive) that the user sources independently. At that point, PEAD would go through: published-research planning → NSE adaptation table → plan-mode approval → implementation → 2-year validation → 10-year evaluation → mandatory recent-period check → post-COVID robustness run → evidence quality scoring → acceptance verdict → Strategy Library update — identical to Turtle, Minervini, 52-Week High Momentum, and Cross-Sectional Momentum.

## Deployment Status

`RESEARCH` (default — no promotion possible without a Research Verdict). Recorded via `deployment_manager.set_deployment_status('pead', DeploymentStatus.RESEARCH, reason=...)` — the deferral reason is permanently logged in the strategy's `deployment_status_history`, using only existing, unmodified deployment-framework functions.
