# Swing Research Program -- Head of Research Roadmap

Maintained by `swing_research/research_roadmap.py` (Published Research Analyst's roadmap extension). Regenerate with `python run_research_roadmap.py` any time the portfolio changes -- diversification scoring reads the LIVE deployment registry, never a stale snapshot.

**Research universe restriction (standing, 2026-08-12):** only peer-reviewed academic papers, well-known quantitative finance research, and widely accepted trading books with substantial historical validation. No YouTube/Reddit/social-media strategies, no commercial black-box systems, no unverified blogs -- these are never catalogued here at all, not scored-and-rejected.

## Current Portfolio State (live, from the deployment registry)

| Strategy | ID | Research Verdict | Deployment Status |
|---|---|---|---|
| Turtle Trading -- System 2 | SW-001 | REJECT | ARCHIVED |
| Minervini Trend Template Filter | SW-002 | INCONCLUSIVE | RESEARCH |
| 52-Week High Momentum | SW-003 | PASS | PAPER_TRADING |
| MA Crossover | SW-004 | REJECT | ARCHIVED |
| Mean Reversion | SW-005 | REJECT | ARCHIVED |
| Cross-Sectional Momentum | SW-006 | PASS | RESEARCH |
| Post-Earnings Announcement Drift (PEAD) | SW-007 | NOT_YET_EVALUATED | RESEARCH |
| Short-Term Reversal | SW-008 | PASS | PAPER_TRADING |
| Betting Against Beta | SW-009 | REJECT | RESEARCH |

## Scoring Methodology

Weighted 0-10 axes, summing to a 0-10 total score:

| Axis | Weight |
|---|---|
| Academic Evidence | 20% |
| Data Availability | 15% |
| Implementation Feasibility | 15% |
| Diversification | 20% |
| Expected Robustness | 15% |
| Operational Simplicity | 10% |
| Research Value | 5% |

Diversification is scored dynamically against the live portfolio above (a strategy sharing a factor family with something already PASS+PAPER_TRADING costs far more diversification credit than one sharing a family with something REJECTed/ARCHIVED). Data availability and implementation feasibility are gated by `classify_data_feasibility()` -- any candidate needing data this platform doesn't have is moved out of the ranked roadmap entirely into 'Deferred Pending Better Data' below, regardless of how well it would otherwise score.

## Ranked Research Roadmap (Top 18)

| Rank | Strategy | Author(s), Year | Factor Family | Total Score | Diversification |
|---|---|---|---|---|---|
| 1 | Amihud Illiquidity Premium | Amihud, 2002 | Liquidity risk premium | 9.05/10 | 10.0/10 |
| 2 | MAX Effect (Lottery-Demand Anomaly) | Bali, 2011 | Behavioral / lottery-demand anomaly | 8.7/10 | 10.0/10 |
| 3 | Overnight Return Anomaly | Lou, 2019 | Market microstructure / attention-driven | 8.5/10 | 10.0/10 |
| 4 | Long-Term (De Bondt-Thaler) Reversal | De Bondt, 1985 | Reversal (long-horizon overreaction) | 8.2/10 | 10.0/10 |
| 5 | Idiosyncratic Volatility Anomaly | Ang, 2006 | Risk-based / low-volatility anomaly | 8.05/10 | 9.5/10 |
| 6 | Turn-of-the-Month Effect | Ariel, 1987 | Calendar seasonality | 7.9/10 | 10.0/10 |
| 7 | High-Volume Return Premium | Gervais, 2001 | Volume-driven attention/visibility premium | 7.8/10 | 10.0/10 |
| 8 | Realized Low Volatility (Nifty100 Low Volatility 30 methodology) | NSE Indices Limited, 2016 | Risk-based (realized volatility, not beta) | 7.75/10 | 9.5/10 |
| 9 | Downside Beta / Downside Risk | Ang, 2006 | Risk-based (downside-conditional) | 7.75/10 | 9.5/10 |
| 10 | Long-Term Contrarian with 1-Year Skip Period (Sehgal & Balakrishnan 2002) | Sehgal, 2002 | Reversal (long-horizon, India-specific evidence) | 7.55/10 | 10.0/10 |
| 11 | Turn-of-the-Year / January Effect | Keim, 1983 | Calendar seasonality | 7.4/10 | 10.0/10 |
| 12 | Jensen's Alpha Selection (Nifty Alpha 50 / Nifty200 Alpha 30 methodology) | NSE Indices Limited, 2011 | Risk-adjusted regression alpha | 7.35/10 | 9.5/10 |
| 13 | Day-of-the-Week (Weekend) Effect | French, 1980 | Calendar seasonality | 7.3/10 | 10.0/10 |
| 14 | Turnover / Liquidity Anomaly | Datar, 1998 | Liquidity risk premium | 7.25/10 | 10.0/10 |
| 15 | Combined Alpha + Low-Volatility Screen (Nifty Alpha Low-Volatility 30 methodology) | NSE Indices Limited, 2017 | Combined risk-based (alpha + volatility) | 7.1/10 | 9.5/10 |
| 16 | Risk-Adjusted Blended Momentum (Nifty200 Momentum 30 methodology) | NSE Indices Limited, 2019 | Momentum (risk-adjusted, blended horizon) | 6.5/10 | 4.0/10 |
| 17 | Industry Momentum | Moskowitz, 1999 | Momentum (industry-level, not stock-level) | 6.15/10 | 4.0/10 |
| 18 | Volume-Based Momentum and Contrarian Strategies (Maheshwari & Dhankar 2017) | Maheshwari, 2017 | Momentum/reversal, VOLUME-conditioned | 6.0/10 | 4.0/10 |

## Full Comparison Table (every candidate, every score)

| Strategy | Feasibility | Evidence | Data Avail. | Feasibility Score | Diversification | Robustness | Simplicity | Research Value | Total |
|---|---|---|---|---|---|---|---|---|---|
| Amihud Illiquidity Premium | IMPLEMENTABLE | 9/10 | 10/10 | 9/10 | 10.0/10 | 8/10 | 8/10 | 8/10 | 9.05/10 |
| MAX Effect (Lottery-Demand Anomaly) | IMPLEMENTABLE | 8/10 | 10/10 | 9/10 | 10.0/10 | 7/10 | 8/10 | 8/10 | 8.7/10 |
| Overnight Return Anomaly | IMPLEMENTABLE | 8/10 | 10/10 | 8/10 | 10.0/10 | 6/10 | 9/10 | 8/10 | 8.5/10 |
| Long-Term (De Bondt-Thaler) Reversal | IMPLEMENTABLE | 9/10 | 10/10 | 7/10 | 10.0/10 | 6/10 | 5/10 | 9/10 | 8.2/10 |
| Idiosyncratic Volatility Anomaly | IMPLEMENTABLE | 8/10 | 10/10 | 8/10 | 9.5/10 | 6/10 | 6/10 | 7/10 | 8.05/10 |
| Turn-of-the-Month Effect | IMPLEMENTABLE | 6/10 | 10/10 | 10/10 | 10.0/10 | 4/10 | 9/10 | 4/10 | 7.9/10 |
| High-Volume Return Premium | IMPLEMENTABLE | 6/10 | 10/10 | 9/10 | 10.0/10 | 5/10 | 7/10 | 6/10 | 7.8/10 |
| Realized Low Volatility (Nifty100 Low Volatility 30 methodology) | IMPLEMENTABLE | 6/10 | 10/10 | 9/10 | 9.5/10 | 5/10 | 9/10 | 3/10 | 7.75/10 |
| Downside Beta / Downside Risk | IMPLEMENTABLE | 7/10 | 10/10 | 8/10 | 9.5/10 | 6/10 | 6/10 | 5/10 | 7.75/10 |
| Long-Term Contrarian with 1-Year Skip Period (Sehgal & Balakrishnan 2002) | IMPLEMENTABLE | 6/10 | 10/10 | 8/10 | 10.0/10 | 6/10 | 5/10 | 5/10 | 7.55/10 |
| Turn-of-the-Year / January Effect | IMPLEMENTABLE | 6/10 | 10/10 | 8/10 | 10.0/10 | 3/10 | 9/10 | 3/10 | 7.4/10 |
| Jensen's Alpha Selection (Nifty Alpha 50 / Nifty200 Alpha 30 methodology) | IMPLEMENTABLE | 6/10 | 10/10 | 7/10 | 9.5/10 | 5/10 | 6/10 | 7/10 | 7.35/10 |
| Day-of-the-Week (Weekend) Effect | IMPLEMENTABLE | 5/10 | 10/10 | 10/10 | 10.0/10 | 2/10 | 9/10 | 2/10 | 7.3/10 |
| Turnover / Liquidity Anomaly | IMPLEMENTABLE | 7/10 | 8/10 | 6/10 | 10.0/10 | 6/10 | 6/10 | 5/10 | 7.25/10 |
| Combined Alpha + Low-Volatility Screen (Nifty Alpha Low-Volatility 30 methodology) | IMPLEMENTABLE | 6/10 | 10/10 | 7/10 | 9.5/10 | 5/10 | 6/10 | 2/10 | 7.1/10 |
| Value (Earnings Yield / Book-to-Market) | NOT_CURRENTLY_IMPLEMENTABLE | 10/10 | 2/10 | 1/10 | 10.0/10 | 8/10 | 5/10 | 9/10 | N/A (blocked) |
| Risk-Adjusted Blended Momentum (Nifty200 Momentum 30 methodology) | IMPLEMENTABLE | 6/10 | 10/10 | 8/10 | 4.0/10 | 6/10 | 7/10 | 4/10 | 6.5/10 |
| Quality (Piotroski F-Score / Novy-Marx Gross Profitability / QMJ) | NOT_CURRENTLY_IMPLEMENTABLE | 9/10 | 2/10 | 1/10 | 10.0/10 | 8/10 | 4/10 | 8/10 | N/A (blocked) |
| Industry Momentum | IMPLEMENTABLE | 7/10 | 8/10 | 7/10 | 4.0/10 | 6/10 | 6/10 | 4/10 | 6.15/10 |
| Volume-Based Momentum and Contrarian Strategies (Maheshwari & Dhankar 2017) | IMPLEMENTABLE | 5/10 | 10/10 | 7/10 | 4.0/10 | 5/10 | 6/10 | 6/10 | 6.0/10 |
| Asset Growth Anomaly | NOT_CURRENTLY_IMPLEMENTABLE | 8/10 | 2/10 | 1/10 | 10.0/10 | 7/10 | 5/10 | 6/10 | N/A (blocked) |
| Accruals Anomaly | NOT_CURRENTLY_IMPLEMENTABLE | 8/10 | 2/10 | 1/10 | 10.0/10 | 7/10 | 4/10 | 6/10 | N/A (blocked) |
| Analyst Earnings-Revision Momentum | NOT_CURRENTLY_IMPLEMENTABLE | 7/10 | 1/10 | 1/10 | 10.0/10 | 6/10 | 5/10 | 6/10 | N/A (blocked) |
| Net Share Issuance / Buyback Anomaly | NOT_CURRENTLY_IMPLEMENTABLE | 7/10 | 1/10 | 1/10 | 10.0/10 | 6/10 | 5/10 | 5/10 | N/A (blocked) |
| Insider Trading Anomaly | NOT_CURRENTLY_IMPLEMENTABLE | 7/10 | 1/10 | 1/10 | 10.0/10 | 5/10 | 5/10 | 6/10 | N/A (blocked) |
| ROE/Leverage/Earnings-Stability Composite (Nifty200 Quality 30 methodology) | NOT_CURRENTLY_IMPLEMENTABLE | 6/10 | 2/10 | 1/10 | 10.0/10 | 6/10 | 4/10 | 5/10 | N/A (blocked) |
| Nifty Index Inclusion/Exclusion Effect | NOT_CURRENTLY_IMPLEMENTABLE | 6/10 | 1/10 | 1/10 | 10.0/10 | 4/10 | 5/10 | 8/10 | N/A (blocked) |
| Earnings/Book/Dividend Value Composite (Nifty50 Value 20 methodology) | NOT_CURRENTLY_IMPLEMENTABLE | 5/10 | 2/10 | 1/10 | 10.0/10 | 6/10 | 4/10 | 4/10 | N/A (blocked) |
| Promoter Share-Pledging as a Governance/Distress Signal | NOT_CURRENTLY_IMPLEMENTABLE | 5/10 | 1/10 | 1/10 | 10.0/10 | 5/10 | 5/10 | 8/10 | N/A (blocked) |
| Short Interest Anomaly | NOT_CURRENTLY_IMPLEMENTABLE | 7/10 | 0/10 | 0/10 | 10.0/10 | 6/10 | 4/10 | 5/10 | N/A (blocked) |
| Post-IPO Long-Run Underperformance | NOT_CURRENTLY_IMPLEMENTABLE | 7/10 | 0/10 | 0/10 | 10.0/10 | 6/10 | 4/10 | 5/10 | N/A (blocked) |
| Coffee Can Portfolio (decade-consistency quality-growth screen) | NOT_CURRENTLY_IMPLEMENTABLE | 5/10 | 1/10 | 1/10 | 10.0/10 | 6/10 | 3/10 | 6/10 | N/A (blocked) |
| Pairs Trading / Statistical Arbitrage | NOT_CURRENTLY_IMPLEMENTABLE | 7/10 | 0/10 | 0/10 | 10.0/10 | 5/10 | 3/10 | 6/10 | N/A (blocked) |
| Options-Based Volatility Risk Premium | NOT_CURRENTLY_IMPLEMENTABLE | 7/10 | 0/10 | 0/10 | 10.0/10 | 6/10 | 2/10 | 4/10 | N/A (blocked) |
| FII/DII Net-Flow Market-Timing Overlay | NOT_CURRENTLY_IMPLEMENTABLE | 5/10 | 1/10 | 0/10 | 10.0/10 | 4/10 | 2/10 | 6/10 | N/A (blocked) |
| India VIX Regime Overlay (equity-only operationalization of the volatility risk premium) | NOT_CURRENTLY_IMPLEMENTABLE | 3/10 | 2/10 | 1/10 | 10.0/10 | 3/10 | 4/10 | 4/10 | N/A (blocked) |
| Bonus Issue Announcement Drift | NOT_CURRENTLY_IMPLEMENTABLE | 2/10 | 1/10 | 1/10 | 10.0/10 | 2/10 | 5/10 | 1/10 | N/A (blocked) |

## Recommended Research Order (top 5, with rationale)

### 1. Amihud Illiquidity Premium (Amihud, Y., 2002)

**Why this:** One of the most-cited asset-pricing papers ever; illiquidity premia are documented as STRONGER in emerging markets than in the US large-cap samples most other candidates here were tested on -- directly relevant to NSE.

**Portfolio overlap:** None -- no existing strategy shares this factor family.

**Known risk:** A pure long-only illiquidity tilt raises real EXECUTION risk (wide spreads, slippage) that this platform's own Execution Realism Study already flagged as a modeling gap -- this strategy would stress-test that gap harder than any strategy tried so far.


### 2. MAX Effect (Lottery-Demand Anomaly) (Bali, T.G., Cakici, N. and Whitelaw, R.F., 2011)

**Why this:** Well-cited, robust behavioral finding, purely price-data-based, and offers a genuinely distinct behavioral mechanism (gambling preference) never touched by this program.

**Portfolio overlap:** None -- no existing strategy shares this factor family.

**Known risk:** Meaningful conceptual overlap with idiosyncratic volatility -- if both were eventually implemented, their overlap should be disclosed, not treated as two fully independent diversification wins.


### 3. Overnight Return Anomaly (Lou, D., Polk, C. and Skouras, S. (see also Berkman, Koch, Tuttle and Zhang 2012), 2019)

**Why this:** Needs literally ZERO new data -- Open and Close are already columns in every OHLCV pull this program already makes; a genuinely novel mechanism no strategy in this program has touched.

**Portfolio overlap:** None -- no existing strategy shares this factor family.

**Known risk:** A newer finding (2019) with less multi-decade replication than the classics; and unusually SENSITIVE to exactly the fill-timing assumption this platform's own Execution Realism Study already flagged as unmodeled (same-day-close fills, not realistic next-day-open fills) -- this strategy's entire edge lives inside that exact gap, so it should not be seriously evaluated before that framework recommendation is addressed.


### 4. Long-Term (De Bondt-Thaler) Reversal (De Bondt, W.F.M. and Thaler, R., 1985)

**Why this:** One of the foundational behavioral-finance papers; genuinely orthogonal horizon regime to every existing strategy in this program (all of which are 1 month to 6 months).

**Portfolio overlap:** None -- no existing strategy shares this factor family.

**Known risk:** Multi-decade replications show the effect has WEAKENED since discovery and concentrates in small/illiquid names -- a real concern for NSE liquidity; the 10-year history this platform holds fits only 2-3 non-overlapping 3-5yr eras, which strains the walk-forward pipeline's window mechanics (few, long windows rather than many, short ones).


### 5. Idiosyncratic Volatility Anomaly (Ang, A., Hodrick, R.J., Xing, Y. and Zhang, X., 2006)

**Why this:** Extremely well-known 'low-vol puzzle', directly computable from daily returns already fetched, no new data source needed at all.

**Portfolio overlap:** Betting Against Beta (SW-009, REJECT/RESEARCH) shares: risk_based

**Known risk:** The original measure is sensitive to the exact estimation window and known to interact with short-term reversal if not controlled for -- a genuine implementation-risk area, not just a data gap.


**Why not the rest of the top 20:** lower total score, driven variously by family overlap with existing strategies (e.g. Industry Momentum vs. SW-003/SW-006), documented historical decay (the calendar-seasonality cluster), or a thinner academic replication record than the candidates above -- see the full comparison table for the exact scores behind each.

## Deferred Pending Better Data

Real, well-cited published strategies this platform cannot yet implement faithfully -- not excluded, just blocked on data this program doesn't have today. See Dataset Recommendations below for what would unlock each.

- **Accruals Anomaly** (Sloan, R.G., 1996) -- Requires 'point_in_time_fundamentals_history', confirmed unavailable on this platform.
- **Analyst Earnings-Revision Momentum** (Womack, K.L., 1996) -- Requires 'analyst_estimates_history', confirmed unavailable on this platform.
- **Asset Growth Anomaly** (Cooper, M.J., Gulen, H. and Schill, M.J., 2008) -- Requires 'point_in_time_fundamentals_history', confirmed unavailable on this platform.
- **Bonus Issue Announcement Drift** (Multiple (e.g. Malhotra, Thenmozhi & ArunKumar; Mishra; Dhar & Chhaochharia; others), 2005) -- Requires 'bonus_issue_announcement_history', confirmed unavailable on this platform.
- **Coffee Can Portfolio (decade-consistency quality-growth screen)** (Mukherjea, S., Ranjan, R. and Uniyal, P., 2018) -- Requires 'point_in_time_fundamentals_history', confirmed unavailable on this platform.
- **Earnings/Book/Dividend Value Composite (Nifty50 Value 20 methodology)** (NSE Indices Limited, 2009) -- Requires 'point_in_time_fundamentals_history', confirmed unavailable on this platform.
- **FII/DII Net-Flow Market-Timing Overlay** (Multiple (e.g. Springer Future Business Journal 2020 causality study; MDPI JRFM 2024 FII-to-DII-dominance study; several others), 2020) -- Requires 'fii_dii_flow_history', confirmed unavailable on this platform.
- **India VIX Regime Overlay (equity-only operationalization of the volatility risk premium)** (N/A -- adapted from the general volatility-risk-premium literature (see the global roadmap's Options-Based Volatility Risk Premium entry) applied to India's own published implied-volatility index, 2008) -- Requires 'india_vix_history', confirmed unavailable on this platform.
- **Insider Trading Anomaly** (Seyhun, H.N., 1986) -- Requires 'insider_transaction_data', confirmed unavailable on this platform.
- **Net Share Issuance / Buyback Anomaly** (Ikenberry, D., Lakonishok, J. and Vermaelen, T.; Pontiff, J. and Woodgate, A., 1995) -- Requires 'corporate_actions_buyback_history', confirmed unavailable on this platform.
- **Nifty Index Inclusion/Exclusion Effect** (Multiple (e.g. Selvam, Indhumathi & Lydia 2012; more recent 2010-2024 studies), 2012) -- Requires 'index_reconstitution_history', confirmed unavailable on this platform.
- **Options-Based Volatility Risk Premium** (Various -- see e.g. Carr, P. and Wu, L., "Variance Risk Premia", Review of Financial Studies (2009), 2009) -- Requires 'options_data', confirmed unavailable on this platform.
- **Pairs Trading / Statistical Arbitrage** (Gatev, E., Goetzmann, W.N. and Rouwenhorst, K.G., 2006) -- Requires 'short_interest_borrow_availability', confirmed unavailable on this platform.
- **Post-IPO Long-Run Underperformance** (Ritter, J.R., 1991) -- Requires 'ipo_date_history', confirmed unavailable on this platform.; Requires 'index_membership_history', confirmed unavailable on this platform.
- **Promoter Share-Pledging as a Governance/Distress Signal** (Multiple (e.g. recent Indian-listed-firm studies on promoter pledging and downside risk, 2009-2023 SEBI disclosure-regime-based samples), 2023) -- Requires 'promoter_pledge_disclosure_history', confirmed unavailable on this platform.
- **Quality (Piotroski F-Score / Novy-Marx Gross Profitability / QMJ)** (Piotroski, J.D.; Novy-Marx, R.; Asness, C.S., Frazzini, A. and Pedersen, L.H., 2000) -- Requires 'point_in_time_fundamentals_history', confirmed unavailable on this platform.
- **ROE/Leverage/Earnings-Stability Composite (Nifty200 Quality 30 methodology)** (NSE Indices Limited, 2018) -- Requires 'point_in_time_fundamentals_history', confirmed unavailable on this platform.
- **Short Interest Anomaly** (Asquith, P., Pathak, P.A. and Ritter, J.R., 2005) -- Requires 'short_interest_borrow_availability', confirmed unavailable on this platform.
- **Value (Earnings Yield / Book-to-Market)** (Basu, S.; Fama, E.F. and French, K.R.; Rosenberg, B., Reid, K. and Lanstein, R., 1977) -- Requires 'point_in_time_fundamentals_history', confirmed unavailable on this platform.

## Permanently Excluded

- **Moving-Average Crossover variants** -- Same mechanism family as SW-004 (MA Crossover), which received a formal REJECT verdict. No new economic rationale has been identified that would change the underlying temporal-robustness failure -- re-testing a parameter variant of an already-REJECTed mechanism is not a good use of research time.
- **RSI / Bollinger-Band mean-reversion variants** -- Same mechanism family as SW-005 (Mean Reversion), which received a formal REJECT verdict, for the same reason as MA Crossover variants above.
- **Turtle Trading -- System 1** -- A documented fast-follow variant of SW-001 (Turtle System 2, REJECT), differing only by a whipsaw filter on the prior signal's outcome. SW-001's REJECT was driven by a structural temporal-robustness failure (worked over 10 years, stopped working in the most recent period), not by a parameter this filter would change -- low expected value for the research time required. Kept out of the ranked roadmap, not deleted from consideration entirely, should the roadmap ever run short of fresher ideas.
- **Any commercial/black-box signal, YouTube strategy, Reddit strategy, or unverified blog strategy** -- Categorically outside this program's research universe per explicit standing direction (2026-08-12) -- never evaluated, never added as a CandidateProfile at all.

## Future Dataset Recommendations

### Point-in-time (as-reported, not restated) historical fundamentals for NSE-listed companies, ~10 years, quarterly

**Unlocks:** Value, Quality (F-Score/Gross Profitability/QMJ), Accruals, and Asset Growth -- the single largest blocked bucket in this roadmap (4 candidates, arguably the most famous anomalies in the academic literature).

**Notes:** The generalization of the exact gap already identified during PEAD's (SW-007) deferral -- a paid vendor (e.g. a Screener.in/Trendlyne/Tijori Finance bulk export, or Refinitiv/Bloomberg) would very likely unlock this AND PEAD simultaneously.

### Historical analyst consensus-estimate data (I/B/E/S-style)

**Unlocks:** PEAD's SUE construction (SW-007) and Analyst Earnings-Revision Momentum.

**Notes:** A narrower, more specialized (and typically more expensive) data category than plain fundamentals.

### NSE insider-trading (SAST) disclosure history

**Unlocks:** Insider Trading Anomaly.

**Notes:** Comparatively the CHEAPEST gap to close of the blocked candidates -- the underlying filings are already public; this would be a scraping/integration project rather than a paid-vendor purchase.

### Securities lending/borrow availability + a genuine short-selling execution path

**Unlocks:** The full documented spread of every risk-based/momentum/reversal candidate already implemented or proposed (all currently long-only by disclosed necessity), plus Pairs Trading / Statistical Arbitrage outright.

**Notes:** An execution/infrastructure investment, not just a data one -- the largest lift on this list.

### NSE F&O historical options-chain data

**Unlocks:** Options-Based Volatility Risk Premium strategies.

**Notes:** Would introduce an entirely new asset class to the platform (options), not just a new signal within cash equities -- a bigger scope decision than a typical dataset purchase.

### Historical index-membership dates (not just current constituents) + a broader point-in-time universe (including delisted/since-removed names)

**Unlocks:** Post-IPO Long-Run Underperformance, and removes the survivorship-bias caveat already disclosed in swing_research/universe.py for every existing and future strategy.

**Notes:** Also strengthens every OTHER strategy's evidence quality, not just IPO-specific research.
