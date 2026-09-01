"""
Published Research Analyst -- the Swing Research Program's replacement for
research_lab/quant_researcher.py's role. Where the Quant Researcher asks
Claude to INVENT hypotheses, the Published Research Analyst's job is to
find, study, and faithfully RECORD an already-published, credible
methodology -- no generation, no invention.

Scope for this first experiment: a structured, source-cited record of
Turtle Trading (the strategy already selected by the user from the
published-research candidate report), saved into the Knowledge Base the
same way a Hypothesis would be -- establishing the pattern for future
strategies. Full Claude-automated "search for the next candidate" tooling
(mirroring quant_researcher.propose_hypotheses()) is a natural fast-follow
once this first faithful implementation is validated -- explicitly not
built here, since it isn't needed to complete Turtle Trading and the user
already did the candidate search/selection for this round via the report
this program's earlier research produced.
"""

from dataclasses import dataclass


@dataclass
class PublishedStrategy:
    name: str
    source_citation: str       # book/paper, author(s), year
    mechanism: str              # plain-language description of the causal/behavioral story
    rules: str                  # the complete, faithfully-documented trading rules
    variant_chosen: str         # which documented variant, and why (if multiple exist)
    scope_reductions: str       # any DISCLOSED adaptation from the original (e.g. long-only)
    distinctiveness: str = ""   # vs. anything already tried in this program or research_lab
    assumptions_impact: str = ""  # per-assumption estimate of how much each undocumented-rule
                                   # substitution could move the result, and in which direction
                                   # (required for every strategy since 2026-08-03, per the
                                   # Minervini implementation approval -- "explicitly distinguish
                                   # documented rules from implementation assumptions and estimate
                                   # the potential impact of those assumptions on the final results")


TURTLE_SYSTEM_2 = PublishedStrategy(
    name="Turtle Trading -- System 2 (Donchian Channel Breakout, long-term)",
    source_citation=(
        "Richard Dennis / William Eckhardt's 1983-84 trading program, documented by "
        "Curtis Faith in \"Way of the Turtle\" (2007) and the publicly-archived original "
        "Turtle Rules."
    ),
    mechanism=(
        "Pure trend-following: markets trend more often and longer than a random walk "
        "would predict. Volatility-normalized position sizing (via N, the 20-day Wilder-"
        "smoothed True Range) keeps risk constant per unit regardless of how volatile a "
        "given instrument currently is."
    ),
    rules=(
        "Entry: Close breaks above the highest High of the prior 55 days. "
        "Exit: Close breaks below the lowest Low of the prior 20 days. "
        "Stop-loss: 2N below the most recent unit's entry price, whole-position stop rises "
        "with each pyramid unit, never lowered. "
        "Position sizing: 1 Unit = floor(equity x 1% / N) shares (dollar-value-per-point=1 "
        "for cash equities, vs. a futures contract multiplier in the original). "
        "Pyramiding: add 1 unit per +0.5N favorable move from the last unit's entry, up to "
        "4 units per symbol. "
        "Portfolio limits: max 4 units/symbol, 6 units per correlated group (sector proxy "
        "for NSE equities), 10 units total (long-only collapses the original's separate "
        "10/12 caps into one)."
    ),
    variant_chosen=(
        "System 2 (55-day entry / 20-day exit, no whipsaw filter) over System 1 (20-day "
        "entry / 10-day exit, skips a signal if the prior signal in that market won). "
        "System 1's filter requires tracking each symbol's own prior-signal outcome across "
        "the whole backtest -- more state, more implementation-risk surface. System 2 is "
        "purely mechanical per-signal. Recommended and approved as the first, cleaner test; "
        "System 1 documented in the implementation plan as a fast-follow, not started."
    ),
    scope_reductions=(
        "LONG ONLY (approved, disclosed) -- NSE cash equities lack the SLB infrastructure "
        "for a genuine multi-week short. Single asset class (NSE cash equities only) vs. "
        "the original's ~20+ diversified, historically low-correlated futures markets -- "
        "this is the transferability question the experiment exists to answer, not a gap "
        "being patched."
    ),
    distinctiveness=(
        "First strategy in the Swing Research Program; no prior swing-horizon experiments "
        "exist to overlap with. Distinct from every research_lab intraday strategy tried so "
        "far (all EOD-square-off, none breakout-with-pyramiding)."
    ),
    assumptions_impact=(
        "Long-only scope reduction: DIRECTIONALLY UNKNOWN impact -- omits the short side "
        "entirely rather than biasing the long side's own measured performance; the real "
        "question (does the short side also work on NSE) is simply untested, not answered. "
        "Single-asset-class universe (vs. original's ~20+ diversified futures markets): "
        "LIKELY UNDERSTATES real-world risk-adjusted performance, since the original edge "
        "partly depends on cross-market diversification this NSE-only test can't capture -- "
        "confirmed material by the robustness analysis's own recent-period REJECT. Sector-"
        "as-correlation-group proxy (vs. futures asset-class grouping): MINOR, a reasonable "
        "structural analogue, not expected to materially change results either direction."
    ),
)


MINERVINI_TREND_TEMPLATE_FILTER = PublishedStrategy(
    name="Minervini Trend Template Filter",
    source_citation=(
        "Mark Minervini, \"Trade Like a Stock Market Wizard\" (2013) and \"Think & Trade Like "
        "a Champion\" (2016)."
    ),
    mechanism=(
        "Trend + relative-strength screening as a proxy for institutional accumulation "
        "already underway: a stock satisfying all 8 Trend Template criteria simultaneously "
        "(price/moving-average alignment, proximity to 52-week high, distance from 52-week "
        "low, top-30% relative strength vs. the universe) is in a confirmed, broad-based "
        "uptrend rather than a speculative or lagging one."
    ),
    rules=(
        "8 criteria, ALL must pass: (1) price above both 150-day and 200-day MA; (2) 150-day "
        "MA above 200-day MA; (3) 200-day MA trending up >=1 month; (4) 50-day MA above both "
        "150-day and 200-day MA; (5) price above 50-day MA; (6) price >=30% above 52-week low; "
        "(7) price within 25% of 52-week high; (8) Relative Strength ranking >=70th percentile "
        "vs. the universe. Stop-loss: 7-8% max from entry. Position sizing: 1.25-2.5% of "
        "equity at risk per trade. Pyramids into confirmed winners (trigger undocumented)."
    ),
    variant_chosen=(
        "Named 'Trend Template Filter', deliberately NOT 'SEPA' or 'VCP breakout' (per "
        "explicit approval 2026-08-03) -- this tests the 8-criterion screen exactly as "
        "documented, with a disclosed mechanical entry trigger standing in for Minervini's "
        "real VCP base/pivot selection, which has no publicly documented canonical numeric "
        "form. See assumptions_impact below and swing_research/strategy_library/ for the "
        "full documented-rules-vs-assumptions breakdown."
    ),
    scope_reductions=(
        "No pyramiding this round (undocumented trigger/sizing -- inventing one would violate "
        "the no-invented-hybrid-rules mandate). Entry trigger is a state-transition proxy for "
        "VCP breakout selection. Exit rule (50-day MA violation) is our own interpretation, "
        "the least-documented part of the source material in every version found. Relative "
        "Strength uses an open academic approximation, not IBD's proprietary formula."
    ),
    distinctiveness=(
        "First strategy in this program requiring a genuinely CROSS-SECTIONAL computation "
        "(RS percentile vs. the whole universe, swing_research/cross_sectional.py) rather "
        "than a per-symbol-independent one -- Turtle's portfolio caps were cross-sectional in "
        "effect (unit limits) but its signal generation was still purely per-symbol."
    ),
    assumptions_impact=(
        "Entry trigger (Template-qualification transition vs. real VCP pivot breakout): "
        "LIKELY MATERIAL, probably UNDERSTATES real SEPA -- a raw qualification day is likely "
        "noisier/earlier than a properly-formed VCP pivot breakout, so a weak or REJECT result "
        "here should be read as inconclusive about real SEPA, not a refutation of it. "
        "Exit rule (50-day MA close-below): MODERATE impact on holding period/win rate, "
        "directionally unverified against Minervini's own (undocumented) practice. "
        "Stop-loss (8% vs. documented 7-8% range): MINOR, within the documented range. "
        "No pyramiding: MODERATE, ONE-DIRECTIONAL -- can only understate real returns "
        "(the source explicitly adds to winners), never overstate them. "
        "Relative Strength substitute (open 3/6/9/12-month blend vs. IBD's proprietary "
        "formula): LIKELY MATERIAL, DIRECTIONALLY UNKNOWN -- the single biggest fidelity gap; "
        "criterion 8 may admit a meaningfully different symbol set than the real IBD screen."
    ),
)


FIFTY_TWO_WEEK_HIGH_MOMENTUM = PublishedStrategy(
    name="52-Week High Momentum",
    source_citation=(
        "George, T.J. and Hwang, C-Y. (2004), \"The 52-Week High and Momentum Investing,\" "
        "The Journal of Finance, Vol. 59, No. 5."
    ),
    mechanism=(
        "A stock's nearness to its own 52-week high, cross-sectionally ranked against the "
        "universe, is a better predictor of future returns than standard past-return "
        "(Jegadeesh-Titman) momentum -- stocks near their 52-week high continue to outperform, "
        "stocks far from it continue to underperform, an anchoring/reference-point effect "
        "distinct from pure trend-following."
    ),
    rules=(
        "Nearness ratio, each formation date: ratio = Price / 52-week-high price. "
        "Cross-sectional decile sort by nearness ratio at each formation date. "
        "Long the top decile (nearest to the 52-week high); the paper's factor construction "
        "shorts the bottom decile. "
        "Holding period K, tested at K=3,6,9,12 months in the paper; K=6 months is the most "
        "commonly cited/replicated specification. "
        "Standard Jegadeesh-Titman overlapping-portfolio construction: a new K-month position "
        "initiated every month, K simultaneous 1/K-weighted vintages held at any time, realized "
        "return = equal-weighted average across active vintages."
    ),
    variant_chosen=(
        "K=6 months (the most commonly cited/replicated specification), top decile = nearness "
        "percentile >= 90 (a direct restatement of 'top decile' in this program's existing "
        "0-100 percentile convention, already used by Minervini's RS percentile). Single-"
        "vintage holding, not the paper's overlapping-portfolio construction -- see "
        "scope_reductions and assumptions_impact below for why, and the magnitude of the "
        "resulting deviation."
    ),
    scope_reductions=(
        "LONG ONLY (approved, disclosed, same reason as Turtle -- no NSE SLB infrastructure "
        "for a genuine multi-month short). SINGLE-VINTAGE HOLDING instead of the paper's "
        "overlapping-portfolio construction (a new K-month position every month, K simultaneous "
        "vintages) -- the single largest structural deviation from the source methodology in "
        "this experiment, approved 2026-08-04. EXIT RULE is ONLY the 126-trading-day time-stop "
        "(the direct single-vintage analogue of the K=6-month holding period) or the synthetic "
        "protective stop -- deliberately NO percentile-based early exit, which was in an "
        "earlier draft of the implementation plan and was explicitly removed before "
        "implementation began, since it would have been an invented rule with no basis in the "
        "source paper. A percentile-based-exit variant is reserved for a future, separately "
        "labeled research iteration, never folded into this baseline. PROTECTIVE STOP-LOSS "
        "(8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE ORIGINAL METHODOLOGY "
        "AT ALL -- the source paper is a factor-return study with no position-level risk "
        "management whatsoever; both are required to run this through this program's "
        "position-based backtesting engine at all."
    ),
    distinctiveness=(
        "Second strategy in this program (after Minervini) requiring a genuinely "
        "cross-sectional computation -- reuses swing_research/cross_sectional.py's existing "
        "vectorized .rank(pct=True, axis=1) pattern via a new "
        "compute_52w_high_nearness_percentile_ranks() function, not a framework change. "
        "Mechanistically distinct from Minervini (a single cross-sectional signal driving "
        "entry directly, vs. Minervini's 8-criterion filter where RS percentile is only one "
        "of 8 gates) and from Turtle (no volatility-normalized sizing, no pyramiding, no "
        "portfolio-level correlation-group caps)."
    ),
    assumptions_impact=(
        "Long-only scope reduction: DIRECTIONALLY UNKNOWN impact -- omits the short side "
        "entirely, doesn't bias the long side's own measured performance. "
        "Single-vintage holding (vs. the paper's overlapping-portfolio construction): "
        "MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- a single-vintage system realizes ONE "
        "entry point's path per qualifying episode, with materially higher variance than the "
        "paper's smoothed multi-vintage average; could realize a better OR worse outcome than "
        "the academic average on any given episode. This is the single largest structural "
        "deviation from the source methodology in this experiment -- any REJECT or weak result "
        "should be read with this in mind, not as a refutation of the underlying 52-week-high "
        "effect itself. "
        "Exit rule (126-trading-day time-stop only, no percentile-based early exit): N/A for "
        "the time-stop itself (direct analogue of the documented K=6-month holding period); "
        "the ABSENCE of an early-exit rule means positions ride the full holding period "
        "regardless of interim nearness-percentile deterioration, a deliberate choice to avoid "
        "hybridizing the baseline with an invented rule. "
        "Protective stop-loss (8%) and position sizing (1% risk/unit): MINOR by themselves "
        "(standard conventions already used elsewhere in this program), but structurally "
        "significant in that NEITHER exists in the source paper at all -- the original is a "
        "factor-return study, not a trading system, and has zero position-level risk "
        "management; this is the most significant 'not really in the source material' "
        "addition of any strategy in this program so far."
    ),
)


CROSS_SECTIONAL_MOMENTUM = PublishedStrategy(
    name="Cross-Sectional Momentum",
    source_citation=(
        "Jegadeesh, N. and Titman, S. (1993), \"Returns to Buying Winners and Selling Losers: "
        "Implications for Stock Market Efficiency,\" The Journal of Finance, Vol. 48, No. 1."
    ),
    mechanism=(
        "Stocks with the highest returns over a J-month formation period continue to "
        "outperform over a subsequent K-month holding period -- the foundational cross-"
        "sectional momentum anomaly, the origin of the 'momentum' factor itself, and the "
        "paper 52-Week High Momentum's own source (George & Hwang 2004) explicitly built on "
        "and partly subsumed."
    ),
    rules=(
        "Formation-period return: cumulative return over the prior J months (J=3,6,9,12 "
        "tested). Cross-sectional decile sort by formation-period return at each formation "
        "date. Long the top decile (past winners); the paper's zero-cost portfolio shorts the "
        "bottom decile (past losers). Holding period K months (K=3,6,9,12 tested); J=6,K=6 is "
        "the specification the paper highlights as generating the strongest, most-cited "
        "result. Standard overlapping-portfolio construction: a new K-month portfolio formed "
        "every month, K simultaneous vintages held at once. The paper notes a 1-week skip "
        "between formation and holding as a refinement that avoids some short-term bid-ask/"
        "reversal contamination -- not part of the headline J=6/K=6 result."
    ),
    variant_chosen=(
        "J=6 months formation, K=6 months holding -- the paper's own most-cited "
        "specification. Single-vintage holding, not the paper's overlapping-portfolio "
        "construction -- identical structural adaptation to 52-Week High Momentum, approved "
        "2026-08-04 for the identical underlying reason (see scope_reductions below)."
    ),
    scope_reductions=(
        "LONG ONLY (approved, disclosed, same reason as every prior strategy -- no NSE SLB "
        "infrastructure for a genuine multi-month short). SINGLE-VINTAGE HOLDING instead of "
        "the paper's overlapping-portfolio construction (a new K-month position every month, "
        "K simultaneous vintages) -- mirrors 52-Week High Momentum's own approved deviation "
        "exactly, for the same reason: full overlapping-portfolio construction is an academic "
        "factor-fund construction, not a swing-trader's position rule. NO SKIP PERIOD between "
        "formation and holding -- the paper's own headline J=6/K=6 result doesn't require one; "
        "it's a secondary refinement, not the primary specification. EXIT RULE is ONLY the "
        "126-trading-day time-stop or the synthetic protective stop -- deliberately no "
        "percentile-based early exit, same discipline established for 52-Week High Momentum. "
        "PROTECTIVE STOP-LOSS (8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE "
        "ORIGINAL METHODOLOGY AT ALL -- the source paper is a factor-return study with no "
        "position-level risk management whatsoever."
    ),
    distinctiveness=(
        "Third strategy in this program requiring a genuinely cross-sectional computation "
        "(after Minervini's RS percentile and 52-Week High Momentum's nearness percentile) -- "
        "reuses swing_research/cross_sectional.py's existing vectorized "
        ".rank(pct=True, axis=1) pattern via a new compute_momentum_percentile_ranks() "
        "function (a SINGLE J=6-month formation return, distinct from Minervini's multi-"
        "horizon 3/6/9/12-month blended RS-Rating substitute -- a different signal for a "
        "different, faithfully-replicated paper). Structurally the closest strategy in this "
        "program to 52-Week High Momentum -- both are academic decile-sort factor studies "
        "from the same research lineage, sharing the identical single-vintage/no-skip-exit "
        "adaptation pattern; see the Strategy Library entry for an explicit diversification "
        "comparison between the two."
    ),
    assumptions_impact=(
        "Long-only scope reduction: DIRECTIONALLY UNKNOWN impact -- omits the short side "
        "entirely, doesn't bias the long side's own measured performance. "
        "J=6-month formation: MINOR, a direct restatement of the paper's own most-cited "
        "specification. "
        "Single-vintage holding (vs. the paper's overlapping-portfolio construction): "
        "MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- identical reasoning to 52-Week High "
        "Momentum's own single-vintage deviation; a single-vintage system realizes ONE entry "
        "point's path per qualifying episode, with materially higher variance than the "
        "paper's smoothed multi-vintage average. "
        "No skip period between formation and holding: MINOR-to-MODERATE, DIRECTIONALLY "
        "UNKNOWN -- the paper suggests a skip period reduces short-term reversal "
        "contamination, so omitting it could slightly understate net momentum performance, "
        "but this is a secondary refinement in the source material, not the headline result. "
        "Exit rule (126-trading-day time-stop only, no percentile-based early exit): N/A for "
        "the time-stop itself (direct analogue of the documented K=6-month holding period). "
        "Protective stop-loss (8%) and position sizing (1% risk/unit): MINOR by themselves, "
        "but structurally significant in that NEITHER exists in the source paper at all -- "
        "the original is a factor-return study with zero position-level risk management."
    ),
)


AMIHUD_ILLIQUIDITY_PREMIUM = PublishedStrategy(
    name="Amihud Illiquidity Premium",
    source_citation=(
        "Amihud, Y. (2002), \"Illiquidity and Stock Returns: Cross-Section and Time-Series Effects,\" "
        "Journal of Financial Markets, Vol. 5, No. 1."
    ),
    mechanism=(
        "Investors demand compensation for holding hard-to-trade (price-impact-sensitive) stocks. "
        "The ILLIQ ratio -- average |daily return| / daily rupee volume -- proxies this directly from "
        "price and volume alone. Second risk/friction-based (not price-pattern-based) mechanism in "
        "this program, after Betting Against Beta (SW-009, REJECT) -- but a genuinely different "
        "friction: BAB prices SYSTEMATIC RISK (covariance with the market), this prices TRADING COST "
        "ITSELF (how much a given trade moves the price)."
    ),
    rules=(
        "ILLIQ_i = (1/D) x sum_t |R_i,t| / VOLD_i,t, averaged over a formation period (the paper's "
        "headline measure uses the PRIOR YEAR, re-estimated annually). Cross-sectional decile sort "
        "by ILLIQ at each formation date. Long the TOP decile (most illiquid) -- the paper's own "
        "long-side framing of the premium, not a long-short zero-cost construction the way BAB's "
        "own factor is (Amihud's headline test is a cross-sectional/Fama-MacBeth REGRESSION of returns "
        "on lagged ILLIQ, not a literal decile-sort trading rule -- translating it into one is itself "
        "an interpretive step, consistent with the pattern the follow-on tradeable-portfolio "
        "literature, e.g. Amihud/Hameed/Kang/Zhang's emerging-market extensions, already uses)."
    ),
    variant_chosen=(
        "252-trading-day (~1 year) ILLIQ formation window -- the paper's OWN preferred window, "
        "UNCHANGED (unlike Betting Against Beta's beta lookback, this window fits comfortably within "
        "the frozen 3-year recent-period check without shortening -- see "
        "swing_research/cross_sectional.py's AMIHUD_ILLIQ_FORMATION_DAYS). Monthly single-vintage "
        "reformation (21 trading days) -- matches how the follow-on tradeable-portfolio literature "
        "operationalizes the paper's annual measure into an actual rebalanced portfolio, since Amihud's "
        "own test re-estimates the cross-section monthly even though each ILLIQ value looks back a "
        "full year. Rupee volume proxied as Close x Volume (no intraday VWAP available -- standard "
        "practice in the empirical liquidity literature itself, not a platform-specific approximation)."
    ),
    scope_reductions=(
        "LONG ONLY (approved, disclosed, same reason as every prior strategy). ZERO-VOLUME DAYS "
        "excluded from the trailing ILLIQ average (not treated as infinite illiquidity) -- a "
        "necessary, disclosed implementation detail for thinly-traded days, not a rule change. "
        "SINGLE-VINTAGE HOLDING instead of a rolling monthly regression -- same disclosed structural "
        "deviation as every cross-sectional strategy in this program. TOP-DECILE THRESHOLD "
        "(percentile >=90, most illiquid) with an 8% protective stop-loss and 1% risk-per-unit sizing, "
        "NOT PART OF THE ORIGINAL METHODOLOGY AT ALL -- same disclosed pattern as every other strategy. "
        "EXECUTION-REALISM CONFIGURATION (approved 2026-08-16, the central methodological difference "
        "from every prior strategy in this program): this is the FIRST strategy whose ACCEPTANCE "
        "VERDICT is computed from execution-realism-adjusted trades, not a zero-cost, same-day-close "
        "backtest -- a 5% trailing-20-day-ADV position-sizing cap, an ILLIQ-derived slippage cost "
        "(calibrated ONCE via a disclosed anchor -- 10bps one-way for a median-ILLIQ universe stock at "
        "a representative Rs.100,000 trade -- never tuned to this strategy's own results), and "
        "next-day-open fill timing (see swing_research/execution_realism_engine.py and "
        "execution_realism_framework_proposal.md). The RAW, zero-cost comparison is still computed and "
        "saved for transparency, but explicitly NOT used for the verdict -- see this strategy's "
        "Strategy Library entry for both sets of numbers side by side."
    ),
    distinctiveness=(
        "First strategy in this program selecting on TRADING-COST/LIQUIDITY-RISK rather than either a "
        "price pattern (every strategy except BAB) or systematic risk covariance (BAB specifically) -- "
        "a third, structurally distinct signal family. Also the first strategy whose own backtest "
        "models the execution friction its signal is theorized to be compensation for, rather than "
        "assuming a frictionless fill -- a fidelity improvement uniquely relevant to THIS strategy, "
        "since every prior strategy's edge doesn't structurally depend on the thing the zero-cost "
        "assumption hides."
    ),
    assumptions_impact=(
        "Long-only: DIRECTIONALLY UNKNOWN, standard. "
        "252-day formation: MINOR, a direct restatement of the paper's own preferred window. "
        "Close x Volume rupee-volume proxy: MINOR, standard practice in the literature itself. "
        "Single-vintage holding: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN, same reasoning as every "
        "prior cross-sectional strategy. "
        "Regression-to-decile-sort translation: a genuine interpretive step beyond the paper's own "
        "literal test design -- MODERATE, DIRECTIONALLY UNKNOWN, disclosed as a bigger fidelity gap "
        "than a strategy whose source paper already IS a decile-sort/portfolio-construction design "
        "(e.g. every Jegadeesh-family strategy already in this program). "
        "Execution-realism configuration: by design, LIKELY UNDERSTATES the zero-cost backtest's own "
        "apparent edge (that is the entire point) -- the SW-003/SW-008 validation found the "
        "illiquidity-cost component specifically can be large for a strategy trading less-liquid names "
        "even when that strategy wasn't designed to select on liquidity at all; Amihud selects on "
        "liquidity DIRECTLY, so this effect is expected to be at least as large, plausibly larger. "
        "This is treated as a FIDELITY IMPROVEMENT relative to every prior strategy's zero-cost "
        "backtest, not an additional disclosed weakness -- the zero-cost number was the less faithful "
        "one for this specific strategy."
    ),
)


BETTING_AGAINST_BETA = PublishedStrategy(
    name="Betting Against Beta (Low-Beta Anomaly)",
    source_citation=(
        "Frazzini, A. and Pedersen, L.H. (2014), \"Betting Against Beta,\" "
        "The Journal of Financial Economics, Vol. 111, No. 1."
    ),
    mechanism=(
        "Leverage-constrained investors overpay for high-beta stocks to get leveraged-like market "
        "exposure without borrowing, compressing high-beta expected returns and leaving low-beta "
        "stocks underpriced relative to their risk -- a risk-based, not momentum-based, story. "
        "First risk-based (as opposed to price-pattern-based) mechanism in this program."
    ),
    rules=(
        "The BAB factor is a beta-neutral long-short construction: long (1/beta_L) x [low-beta "
        "portfolio], short (1/beta_H) x [high-beta portfolio], each leg rescaled so its OWN beta "
        "is exactly 1 at formation (long leg levered up, short leg de-levered), funded/financed at "
        "the risk-free rate. "
        "Beta estimation (the paper's own specific estimator, distinct from a plain OLS regression "
        "beta): beta_hat = rho_hat x (sigma_hat_i / sigma_hat_m), where rho_hat (correlation) is "
        "estimated from OVERLAPPING 3-DAY LOG RETURNS over a trailing 1-year window (deliberately "
        "using multi-day overlapping returns to correct for non-synchronous/thin-trading understatement "
        "of correlation), and sigma_hat (volatility, for both the stock and the market) is estimated "
        "from 1-day log returns over a trailing 5-year window (the paper's own stated minimum is 3 "
        "years for stocks with a shorter history). The raw estimate is then SHRUNK toward the "
        "cross-sectional mean of 1.0: beta = 0.6 x beta_hat + 0.4 x 1 -- a fixed 0.6/0.4 weighting "
        "the paper states explicitly. Rebalanced monthly."
    ),
    variant_chosen=(
        "Long-only, unlevered, bottom-decile (lowest shrunk beta) selection -- see scope_reductions "
        "below for why the leverage-scaled, beta-neutral long-short construction itself is not "
        "attempted. Beta ESTIMATOR kept faithful to the paper's specific rho x (sigma_i/sigma_m), "
        "0.6/0.4-shrunk construction -- not simplified to a plain OLS regression beta. Correlation "
        "estimation kept faithful to the paper's overlapping-3-day-log-return method (approved "
        "2026-08-15, over a simpler plain-daily-return alternative) specifically because NSE's Nifty "
        "500 spans a wide liquidity range where the paper's own thin-trading correction is relevant, "
        "not a formality."
    ),
    scope_reductions=(
        "LONG ONLY, UNLEVERED (approved, disclosed) -- no margin/leverage infrastructure anywhere in "
        "this platform and no NSE SLB infrastructure for a genuine short (same reason as every prior "
        "strategy). This is a LARGER interpretive gap than every prior strategy's long-only reduction: "
        "we capture only the long, unlevered, low-beta STOCK-SELECTION half of the paper's construction, "
        "not the leverage-scaled, beta-neutral, zero-cost factor itself -- the backtest answers 'do "
        "low-beta NSE stocks outperform?' rather than the paper's own headline claim ('does a "
        "leverage-scaled bet against beta earn a return?'), a related but distinct question. "
        "VOLATILITY (and, to keep both windows consistent, CORRELATION) LOOKBACK SHORTENED FROM THE "
        "PAPER'S 5-YEAR (MINIMUM 3-YEAR) WINDOW TO 1 YEAR (approved 2026-08-15) -- the paper's own "
        "preferred sigma-estimation window is structurally incompatible with this program's frozen "
        "3-year recent-period check (swing_research/acceptance_criteria.py, RECENT_PERIOD_YEARS=3, "
        "never modified): a 5-year (or even the paper's stated 3-year minimum) warm-up requirement "
        "would consume the ENTIRE 3-year recent-period slice with zero days left to trade, guaranteeing "
        "an uninformative empty/REJECT result regardless of whether the strategy actually works. "
        "Shortening to 1 year (matching rho's own window) is the only choice compatible with the "
        "existing frozen _feasible_window_count machinery producing a meaningful recent-period result "
        "at all -- a real, disclosed deviation from the paper's preferred window, made specifically to "
        "fit this platform's mandatory recency gate, not an arbitrary simplification. "
        "RAW DAILY RETURNS used in place of returns in excess of a risk-free rate for beta estimation "
        "(no risk-free-rate time series is integrated anywhere in this platform) -- standard, "
        "negligible-impact simplification at daily frequency. "
        "SINGLE-VINTAGE HOLDING instead of continuous monthly rebalancing into overlapping positions "
        "-- mirrors every prior cross-sectional strategy's own approved deviation. PROTECTIVE "
        "STOP-LOSS (8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE ORIGINAL "
        "METHODOLOGY AT ALL -- the source paper is a return-predictability/factor-construction study "
        "with no position-level risk management whatsoever."
    ),
    distinctiveness=(
        "First RISK-BASED (not price-pattern-based) mechanism in this program -- every prior strategy "
        "(Turtle, Minervini, 52-Week High, Cross-Sectional Momentum, Short-Term Reversal) selects on "
        "some function of past price/return; this selects on estimated systematic risk (beta) itself, "
        "a structurally different signal family with no overlap in EXISTING deployment-registry tags "
        "(risk_based vs. trend_following/momentum_cross_sectional/reversal_short_horizon/earnings_drift). "
        "Reuses swing_research/cross_sectional.py's established vectorized .rank(pct=True, axis=1) "
        "percentile pattern via a new compute_shrunk_beta_percentile_ranks() function, but is the "
        "first strategy in this program needing an EXTERNAL market-index series (Nifty 50 daily "
        "closes, via data/fetch_historical.fetch_nifty(), already reused elsewhere for the regime "
        "gate/breakdown) as an input to its own cross-sectional signal, not just each symbol's own "
        "price history."
    ),
    assumptions_impact=(
        "Long-only, unlevered (no beta-neutral long-short construction): LIKELY UNDERSTATES the "
        "paper's own headline factor return -- the original edge is specifically largest for the "
        "LEVERED low-beta + DE-LEVERED high-beta PAIR; this measures only the long, unlevered "
        "low-beta leg's own stock-selection return, which the paper itself would predict to be a "
        "smaller, one-sided fraction of the full documented spread, not the spread itself. "
        "Volatility/correlation lookback shortened from 5yr(paper)/3yr(paper minimum) to 1yr: "
        "MODERATE, DIRECTIONALLY UNKNOWN -- a shorter window makes the beta estimate noisier "
        "(more sensitive to a single volatile year) and more reactive to recent regime shifts than "
        "the paper's own preferred longer, more stable estimate; this is a genuine fidelity cost "
        "accepted specifically to make the strategy testable under this platform's frozen recency "
        "gate, disclosed here rather than silently absorbed. "
        "Overlapping-3-day-return correlation (kept faithful, not simplified): MINOR positive -- "
        "preserves the paper's own thin-trading correction, plausibly more accurate for NSE's wide "
        "liquidity range than a plain daily-return correlation would be. "
        "Raw returns vs. excess-of-risk-free returns: NEGLIGIBLE at daily frequency, standard "
        "simplification. "
        "Single-vintage holding: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- identical reasoning "
        "to every prior cross-sectional strategy's own single-vintage deviation. "
        "Protective stop-loss (8%) and position sizing (1% risk/unit): MINOR by themselves, but "
        "structurally significant in that NEITHER exists in the source paper at all."
    ),
)


TURN_OF_MONTH = PublishedStrategy(
    name="Turn-of-the-Month Effect",
    source_citation=(
        "Ariel, R.A. (1987), \"A Monthly Effect in Stock Returns,\" Journal of Financial "
        "Economics, Vol. 18, No. 1."
    ),
    mechanism=(
        "Returns are disproportionately concentrated in the few trading days around each "
        "month's turn (the last trading day of the month through the first few days of the "
        "next) -- originally linked to institutional cash-flow/payroll-driven buying patterns "
        "concentrating around month-end/month-start. First pure CALENDAR/SEASONALITY mechanism "
        "in this program -- every prior strategy selects cross-sectionally against the "
        "universe; this one has no per-symbol selection criterion at all."
    ),
    rules=(
        "Turn-of-month window: the LAST trading day of the month through the THIRD trading day "
        "of the following month (a 4-trading-day window, the paper's own '-1 to +3' "
        "definition). The paper's finding is that essentially ALL of the market's cumulative "
        "return over its sample period is concentrated in this window; the remainder of the "
        "month is flat on average."
    ),
    variant_chosen=(
        "Long-only, applied per-symbol to every stock in the universe uniformly (see "
        "scope_reductions below for why) -- enter at Close on the last trading day of the "
        "month, exit exactly 3 trading days later (computed by row position, not a calendar-day "
        "approximation)."
    ),
    scope_reductions=(
        "APPLIED PER-SYMBOL, UNIFORMLY, NOT AS A MARKET-INDEX TIMING SIGNAL (disclosed) -- the "
        "original paper tests the aggregate market (index) return; this platform's Strategy "
        "interface is per-symbol, so every symbol in the universe qualifies on the SAME "
        "calendar day, a first for this program (every prior strategy narrows to a "
        "cross-sectional decile before the shared engine's own max_units_total=10 cap applies). "
        "Here that same, UNCHANGED cap applies across the ENTIRE undifferentiated universe "
        "instead of a pre-selected subset. "
        "DIVERSIFICATION FIX -- TWO ATTEMPTS, both disclosed (before any promotion decision): "
        "ATTEMPT 1 (discarded): a first backtest with NO tie-breaker confirmed EMPIRICALLY that "
        "this collapses onto the same alphabetically-early ~10 symbols (the frozen universe's "
        "own ticker order, zero economic meaning) filling every month for the entire 10-year "
        "history, touching only 10 of ~20 sectors. A fix restricting entry ELIGIBILITY to a "
        "STATIC rotating ~1/4 slice of the universe (a fixed per-symbol bucket, unchanging every "
        "time that quarter came due) improved coverage to 17/~20, but a follow-up check found "
        "this was NOT a full fix: every symbol from the 3 sectors still missing had 12-125 OTHER "
        "symbols in its SAME static bucket that came alphabetically before it, EVERY SINGLE TIME "
        "that bucket was active -- a persistent, near-permanent structural exclusion, not "
        "residual noise, since the underlying engine's fixed alphabetical iteration order was "
        "never actually touched, only the size of the pool competing within it. "
        "ATTEMPT 2 (current): replaces the static per-symbol bucket with a PER-MONTH COMBINED "
        "RANK of (symbol, absolute month index) -- every calendar month, all currently-tradeable "
        "symbols are ranked afresh by a deterministic hash mixing the symbol's own name with "
        "that specific month, and only the top ELIGIBLE_PER_MONTH (40) are eligible. Because the "
        "hash mixes in the month itself, both WHICH symbols are eligible and each eligible "
        "symbol's own alphabetical standing relative to that month's specific cohort genuinely "
        "reshuffle every month, instead of repeating the same fixed competitive landscape "
        "forever -- giving a previously-excluded symbol periodic access to a favorable draw "
        "roughly as often as any other symbol. Both attempts inject their column via "
        "extra_columns_by_symbol, the same mechanism every prior strategy already uses for its "
        "own cross-sectional signal -- NEITHER changes the shared backtesting engine or any "
        "other strategy's own frozen results. "
        "HOLDING PERIOD computed by ROW POSITION (exactly 3 trading days after entry), not the "
        "generic trading-day-to-calendar-day approximation every other strategy in this program "
        "uses -- that approximation is reasonable for 21+ trading day holds but would be "
        "unreliable at this strategy's much shorter, weekend-sensitive horizon. "
        "PROTECTIVE STOP-LOSS (8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE "
        "ORIGINAL METHODOLOGY AT ALL -- the source paper is a market-return-pattern study with "
        "no position-level risk management whatsoever."
    ),
    distinctiveness=(
        "First pure calendar/seasonality mechanism in this program -- no percentile ranking, no "
        "external market-index series, no rolling-window formation period of any kind. Every "
        "prior strategy's edge comes from a computed indicator selecting a subset of the "
        "universe; this one's entry condition is calendar date alone, identical for every "
        "symbol (narrowed only by the disclosed per-month eligibility fix above, not by any "
        "ranking of the underlying signal itself). Needs essentially no historical warm-up "
        "(min_lookback_days=1), the "
        "opposite structural situation from Long-Term Reversal's multi-year-formation problem -- "
        "this strategy should get many feasible walk-forward windows in the mandatory "
        "recent-period check rather than being structurally starved of them."
    ),
    assumptions_impact=(
        "Per-symbol application instead of an index-level timing signal: DIRECTIONALLY UNKNOWN "
        "-- a real implementation might reasonably use an index proxy instead; this backtest "
        "instead tells us whether the effect shows up in individual NSE stocks, a related but "
        "distinct question from the paper's own index-return finding. "
        "Per-month combined-rank diversification fix (Attempt 2): MODERATE, LIKELY POSITIVE for "
        "external validity -- verified to close the persistent, structural exclusion Attempt 1 "
        "left behind (the specific symbols/sectors previously locked out by a fixed 12-125-symbol "
        "same-bucket alphabetical disadvantage now have a genuinely reshuffling monthly draw). "
        "The shared engine's own iteration-order artifact still applies WITHIN any single month's "
        "eligible cohort, but that cohort's composition (and each symbol's standing within it) "
        "now changes every month rather than being fixed forever -- a genuine improvement in what "
        "the backtest is actually testing, not a parameter tuned to change the verdict (both the "
        "eligible-per-month count, 40, and the combined-rank design itself were chosen for "
        "coverage-breadth reasoning before any backtest was re-run with them, not selected after "
        "seeing results). "
        "Row-position-based 3-trading-day exit (kept faithful, not approximated): NEGLIGIBLE "
        "incremental risk -- this is more precise than the generic calendar-day approximation "
        "used elsewhere, not less. "
        "Protective stop-loss (8%) and position sizing (1% risk/unit): MINOR by themselves, but "
        "structurally significant in that NEITHER exists in the source paper at all -- and at "
        "only a 3-trading-day hold, the 8% stop is far less likely to bind than in any other "
        "strategy in this program, so its practical impact here is likely smaller than usual."
    ),
)


IDIOSYNCRATIC_VOLATILITY_ANOMALY = PublishedStrategy(
    name="Idiosyncratic Volatility Anomaly",
    source_citation=(
        "Ang, A., Hodrick, R.J., Xing, Y. and Zhang, X. (2006), \"The Cross-Section of "
        "Volatility and Expected Returns,\" The Journal of Finance, Vol. 61, No. 1."
    ),
    mechanism=(
        "Stocks with high idiosyncratic (residual, market-model-adjusted) volatility earn "
        "anomalously LOW subsequent returns -- the opposite of what a risk premium would "
        "predict -- attributed to lottery-preference/limits-to-arbitrage effects that keep "
        "high-idio-vol stocks persistently overpriced. Same RISK-BASED family as Betting "
        "Against Beta (SW-009), but a structurally distinct signal: residual (stock-specific) "
        "volatility after removing market-wide moves, not estimated systematic risk (beta)."
    ),
    rules=(
        "Idiosyncratic volatility = standard deviation of the residuals from a regression of "
        "daily stock returns on a factor model, estimated over the trailing month. The paper's "
        "PRIMARY, headline specification regresses on the FAMA-FRENCH 3-FACTOR model (market, "
        "SMB size factor, HML value factor). Stocks are quintile-sorted on this measure monthly; "
        "the paper longs the LOWEST-idio-vol quintile and shorts the HIGHEST, rebalanced monthly."
    ),
    variant_chosen=(
        "Long-only, bottom-decile (lowest idiosyncratic volatility) selection, using a "
        "SINGLE-FACTOR (CAPM/market-model) residual volatility in place of the paper's primary "
        "3-factor construction -- see scope_reductions below for why. Formation window kept "
        "faithful to the paper's own trailing-1-month (21 trading day) re-formation, not "
        "shortened or lengthened."
    ),
    scope_reductions=(
        "SINGLE-FACTOR (CAPM/market-model) RESIDUAL VOLATILITY INSTEAD OF THE PAPER'S PRIMARY "
        "3-FACTOR (MARKET+SMB+HML) CONSTRUCTION (disclosed) -- SMB and HML require point-in-time "
        "market-capitalization and book-to-market data, which this platform has already confirmed "
        "unavailable (research_roadmap.py's DATA_CAPABILITIES: point_in_time_fundamentals_history "
        "is a confirmed gap). This is NOT an invented substitute: the paper's own robustness "
        "section reports that results are qualitatively unchanged using a single-factor "
        "market-model residual in place of the 3-factor residual, so this is a faithful, "
        "disclosed alternative specification drawn from the paper itself. It is nonetheless a "
        "LARGER fidelity gap than most prior adaptations in this program, since it drops two of "
        "the three regressors behind the paper's own headline result, not merely a window length "
        "or a risk-free-rate simplification. "
        "LONG ONLY (approved, disclosed) -- no NSE SLB infrastructure for a genuine short (same "
        "reason as every prior strategy). We capture only the long, low-idio-vol STOCK-SELECTION "
        "leg, not the paper's own long-short spread. "
        "SINGLE-VINTAGE HOLDING instead of continuous monthly rebalancing into overlapping "
        "positions -- mirrors every prior cross-sectional strategy's own approved deviation. "
        "PROTECTIVE STOP-LOSS (8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE "
        "ORIGINAL METHODOLOGY AT ALL -- the source paper is a return-predictability/factor-"
        "construction study with no position-level risk management whatsoever. "
        "KNOWN INTERACTION RISK, DISCLOSED NOT CONTROLLED FOR: the idiosyncratic-volatility "
        "measure is documented in the literature to interact with short-term reversal if not "
        "separately controlled for -- this platform does not attempt that control (no existing "
        "orthogonalization machinery), so any observed edge should be read with that caveat."
    ),
    distinctiveness=(
        "Second RISK-BASED strategy in this program, after Betting Against Beta (SW-009, "
        "REJECTED -- base PASS, recent-period REJECT, robustness REJECT, genuine regime decay). "
        "Structurally distinct signal within the same factor family: RESIDUAL (stock-specific) "
        "volatility after removing the market-wide component, not estimated systematic risk "
        "(beta) itself -- a stock can have low beta and high idiosyncratic volatility, or vice "
        "versa, so the two rankings are not a relabeling of the same underlying quantity. Reuses "
        "swing_research/cross_sectional.py's established vectorized .rank(pct=True, axis=1) "
        "percentile pattern via compute_idiosyncratic_volatility_percentile_ranks(), and reuses "
        "the same 'needs an EXTERNAL market-index series' pattern Betting Against Beta "
        "established (data/fetch_historical.fetch_nifty()), applied here via a closed-form "
        "single-factor residual-variance identity (sigma_stock x sqrt(1-rho^2)) rather than "
        "Betting Against Beta's own beta_hat = rho x (sigma_i/sigma_m) formula."
    ),
    assumptions_impact=(
        "Single-factor residual volatility vs. the paper's primary 3-factor residual: "
        "MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- disclosed as the largest fidelity gap in "
        "this implementation. The paper's own robustness checks suggest the anomaly survives "
        "under a single-factor construction, but 'qualitatively unchanged' in a US large-cap "
        "sample is not a guarantee of the same magnitude on NSE's cross-section. "
        "Long-only (no long-short spread): measures only the long, low-idio-vol leg's own "
        "stock-selection return, a related but distinct question from the paper's own long-short "
        "factor return -- same reasoning as Betting Against Beta's identical long-only reduction. "
        "Formation window (kept faithful, not shortened): NEGLIGIBLE incremental risk beyond the "
        "single-factor substitution above -- this is the one dimension where fidelity to the "
        "paper is NOT reduced. "
        "Single-vintage holding: MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- identical "
        "reasoning to every prior cross-sectional strategy's own single-vintage deviation. "
        "Protective stop-loss (8%) and position sizing (1% risk/unit): MINOR by themselves, but "
        "structurally significant in that NEITHER exists in the source paper at all. "
        "Uncontrolled short-term-reversal interaction: DIRECTIONALLY UNKNOWN, disclosed as a "
        "genuine, un-mitigated risk rather than a theoretical footnote -- if this platform's own "
        "Short-Term Reversal strategy (SW-006) and this one show materially overlapping entries "
        "in the backtest, that would be direct empirical evidence the interaction is live here, "
        "not just in the literature."
    ),
)


POST_EARNINGS_ANNOUNCEMENT_DRIFT = PublishedStrategy(
    name="Post-Earnings Announcement Drift (PEAD) -- Forward Evidence Experiment",
    source_citation=(
        "Bernard, V. and Thomas, J. (1989), \"Post-Earnings-Announcement Drift: Delayed Price Response or "
        "Risk Premium?\", Journal of Accounting Research; Bernard and Thomas (1990), \"Evidence That Stock "
        "Prices Do Not Fully Reflect The Implications Of Current Earnings For Future Earnings\", Journal of "
        "Accounting and Economics. Original Standardized Unexpected Earnings (SUE) construction: Foster, "
        "Olsen and Shevlin (1984)."
    ),
    mechanism=(
        "Stock prices underreact to earnings surprises -- a large positive earnings surprise (high SUE) "
        "predicts continued positive drift over the following quarter, as the market only gradually "
        "incorporates the full information content of the announcement."
    ),
    rules=(
        "SUE_q = (EPS_q - EPS_q-4) / sigma, sigma = std dev of the trailing 8 quarters' own YoY EPS "
        "differences (the ORIGINAL Foster/Olsen/Shevlin 1984 seasonal-random-walk construction, not an "
        "analyst-estimate-based surprise). Cross-sectional decile sort at each formation date in the "
        "original literature; long the top decile (highest SUE), ~60-trading-day holding period "
        "(approximately one quarter)."
    ),
    variant_chosen=(
        "*** NO HISTORICAL BACKTEST EXISTS FOR THIS STRATEGY -- Research Verdict remains "
        "NOT_YET_EVALUATED, unchanged by this record's existence. *** Deferred 2026-08-05 "
        "(swing_research/strategy_library/pead.md): no usable multi-year historical earnings-surprise "
        "dataset was found via any integrated source at that time. This record exists ONLY to document the "
        "rules governing a FORWARD-ONLY, real-time paper-trading pipeline (deployment/pead_forward_engine.py) "
        "collecting NEW evidence going forward from 2026-08-17 -- explicitly distinguished from a "
        "historically-validated strategy. See deployment/pead_signal.py for the exact SUE formula and "
        "threshold used. Absolute SUE threshold (+2.0) used in place of cross-sectional decile ranking -- a "
        "disclosed adaptation for a live, incrementally-arriving event stream (see pead_signal.py's own "
        "docstring for the full reasoning), not a cross-sectional sort like every other strategy in this "
        "program. 60-trading-day single-vintage holding, 8% protective stop-loss, 1% risk-per-unit sizing -- "
        "same disclosed patterns as every other strategy (not part of the original methodology)."
    ),
    scope_reductions=(
        "NO BACKTEST: this program has never run PEAD through the frozen acceptance framework "
        "(acceptance_criteria.py) at all -- there is no base run, no recent-period check, no evidence "
        "quality score, and none is claimed. LONG ONLY (standard, disclosed reduction, same as every "
        "strategy). SEASONAL-RANDOM-WALK SUE (not analyst-estimate-based): a faithful restatement of the "
        "ORIGINAL SUE construction, chosen specifically because it needs only historical ACTUAL EPS (already "
        "confirmed available going forward via yfinance's get_earnings_dates(), 2026-08-17), not analyst "
        "estimates (confirmed UNAVAILABLE via any source during the original 2026-08-05 investigation). "
        "ABSOLUTE THRESHOLD instead of cross-sectional decile rank: disclosed adaptation for a live event "
        "stream, see pead_signal.py. CONSERVATIVE ENTRY TIMING: an announcement is never acted on the same "
        "calendar day it is first detected -- entry waits until at least the following trading day, "
        "regardless of the announcement's true before/after-market timing (which yfinance's own timestamp is "
        "not trusted to reliably indicate) -- see deployment/pead_forward_engine.py's own docstring."
    ),
    distinctiveness=(
        "First EVENT-DRIVEN (not daily-bar-driven) strategy in this program -- entries are triggered by a "
        "real-world earnings announcement, detected via a NEW forward-data pipeline "
        "(data/fetch_earnings_calendar.py), not by any technical or cross-sectional condition on a price "
        "bar. Reuses swing_research/strategies/pead.py's exit-side Strategy shim so the EXIT half (holding-"
        "period time-stop + protective stop) still runs through deployment/paper_trading_engine.py's "
        "existing, unmodified exit machinery."
    ),
    assumptions_impact=(
        "No backtest: the conceptual status of any observation from this pipeline is FORWARD EVIDENCE, not "
        "a validated research finding -- explicitly not comparable in confidence to any PASS/REJECT/"
        "INCONCLUSIVE verdict elsewhere in this program until/unless a real historical evaluation becomes "
        "possible and is separately, explicitly undertaken. "
        "Absolute SUE threshold vs. cross-sectional rank: DIRECTIONALLY UNKNOWN -- could over- or under-"
        "select relative to what a true decile sort would produce in any given reporting season. "
        "Conservative entry timing (never same-day): LIKELY UNDERSTATES the documented drift somewhat (real "
        "PEAD studies typically capture drift beginning very close to the announcement itself), a deliberate "
        "trade-off explicitly made to avoid lookahead given unverified announcement-timing data, not an "
        "oversight."
    ),
)


SHORT_TERM_REVERSAL = PublishedStrategy(
    name="Short-Term Reversal",
    source_citation=(
        "Jegadeesh, N. (1990), \"Evidence of Predictable Behavior of Security Returns,\" "
        "The Journal of Finance, Vol. 45, No. 3."
    ),
    mechanism=(
        "Significant NEGATIVE autocorrelation in individual stock returns at short "
        "(weekly-to-monthly) lags -- the opposite sign from the 3-12 month momentum effect "
        "(Jegadeesh & Titman 1993). Stocks with the worst returns over the prior month "
        "subsequently outperform, and vice versa -- the mirror image of every momentum "
        "strategy already tested in this program."
    ),
    rules=(
        "Formation period: prior ONE MONTH return (the paper's headline, most-replicated "
        "lag; other lags examined but 1-month is primary). Cross-sectional decile sort by "
        "formation-period return at each formation date. Long the BOTTOM decile (worst "
        "performers / 'losers'); the paper's zero-cost portfolio shorts the top decile "
        "(best performers / 'winners'). Holding period: one month, standard Jegadeesh-style "
        "overlapping-portfolio construction (new position formed every month)."
    ),
    variant_chosen=(
        "1-month formation / 1-month holding -- the paper's own headline, most-cited "
        "specification. Single-vintage holding, not the paper's overlapping-portfolio "
        "construction -- identical structural adaptation to every prior cross-sectional "
        "strategy in this program, approved 2026-08-05, polarity reversed and horizon "
        "shortened vs. the momentum strategies."
    ),
    scope_reductions=(
        "LONG ONLY (approved, disclosed, same reason as every prior strategy -- no NSE SLB "
        "infrastructure for a genuine short). SINGLE-VINTAGE HOLDING instead of the paper's "
        "overlapping-portfolio construction -- mirrors every prior cross-sectional "
        "strategy's own approved deviation. EXIT RULE is ONLY the 21-trading-day time-stop "
        "or the synthetic protective stop -- deliberately no percentile-based early exit, "
        "same discipline established for every prior strategy. PROTECTIVE STOP-LOSS (8%) "
        "and POSITION SIZING (1% risk per unit) are NOT PART OF THE ORIGINAL METHODOLOGY AT "
        "ALL -- the source paper is a factor-return study with no position-level risk "
        "management whatsoever. NAMING: explicitly distinguished from the unrelated "
        "existing production strategy strategies/mean_reversion.py (an RSI/Bollinger-based "
        "per-symbol technical signal, a completely different mechanism) -- this strategy's "
        "key is short_term_reversal, never mean_reversion, to avoid any confusion between "
        "the two."
    ),
    distinctiveness=(
        "Fourth strategy in this program requiring a genuinely cross-sectional computation "
        "-- reuses swing_research/cross_sectional.py's existing vectorized "
        ".rank(pct=True, axis=1) pattern via a new compute_short_term_reversal_percentile_ranks() "
        "function (a 21-day single-period formation return, distinct from Cross-Sectional "
        "Momentum's 126-day version and Minervini's multi-horizon blend). Mechanistically "
        "the OPPOSITE of every momentum strategy already tested in this program -- buys "
        "recent losers (bottom decile, percentile <=10) rather than recent winners, over a "
        "much shorter horizon (1 month vs. 6 months) -- the strongest genuine-diversification "
        "candidate available from price data alone. See the Strategy Library entry for the "
        "full comparison against every strategy researched so far and the Portfolio Impact "
        "Analysis."
    ),
    assumptions_impact=(
        "Long-only scope reduction: DIRECTIONALLY UNKNOWN impact. "
        "1-month formation period: MINOR, a direct restatement of the paper's own headline "
        "specification. "
        "Single-vintage holding (vs. the paper's overlapping-portfolio construction): "
        "MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- identical reasoning to every prior "
        "cross-sectional strategy's own single-vintage deviation; note the much shorter "
        "holding period (1 month vs. 6 months) means meaningfully more trade turnover than "
        "the momentum strategies, a genuine behavioral difference worth weighing in the "
        "eventual portfolio-impact analysis, not just a weaker/stronger version of the same "
        "mechanism. "
        "Exit rule (21-trading-day time-stop only, no percentile-based early exit): N/A for "
        "the time-stop itself. "
        "Protective stop-loss (8%) and position sizing (1% risk/unit): MINOR by themselves, "
        "but structurally significant in that NEITHER exists in the source paper at all -- "
        "worth noting that buying stocks which just fell sharply places entries inherently "
        "closer (in percentage terms) to a plausible further-decline scenario than a "
        "momentum strategy's entries, though the SAME 8% convention is used regardless, "
        "disclosed as a point of interpretive difference, not a rule change."
    ),
)


MAX_EFFECT = PublishedStrategy(
    name="MAX Effect (Lottery-Demand Anomaly)",
    source_citation=(
        "Bali, T.G., Cakici, N. and Whitelaw, R.F. (2011), \"Maxing Out: Stocks as Lotteries and "
        "the Cross-Section of Expected Returns,\" Journal of Financial Economics, Vol. 99, No. 2."
    ),
    mechanism=(
        "Stocks with an extreme maximum single-day return within the recent month get bid up by "
        "investors with a preference for lottery-like payoffs (skewness-seeking demand), then "
        "underperform as that demand fades. Documented as surviving controls for size, "
        "book-to-market, momentum, and short-term reversal -- a genuinely distinct behavioral "
        "mechanism from every strategy already tested in this program, though highly correlated "
        "with idiosyncratic volatility (a separate, not-yet-implemented roadmap candidate)."
    ),
    rules=(
        "MAX, each formation date: the single HIGHEST daily return within the trailing ONE MONTH "
        "(the paper's primary MAX(1) specification; MAX(5), the average of the 5 highest days, is "
        "a disclosed robustness variant, not the headline result). Cross-sectional decile sort by "
        "MAX at each formation date. Long the BOTTOM decile (lowest MAX -- calmest stocks); the "
        "paper's zero-cost portfolio shorts the top decile (highest MAX -- most lottery-like "
        "stocks). Holding period: one month, standard monthly-rebalance construction."
    ),
    variant_chosen=(
        "MAX(1), single highest daily return in the trailing month -- the paper's own headline, "
        "most-cited specification (not MAX(5), the paper's own secondary robustness check). "
        "1-month formation / 1-month holding. Single-vintage holding, not the paper's "
        "overlapping-portfolio construction -- identical structural adaptation to every prior "
        "cross-sectional strategy in this program."
    ),
    scope_reductions=(
        "LONG ONLY (approved, disclosed, same reason as every prior strategy -- no NSE SLB "
        "infrastructure for a genuine short). MAX(1) ONLY, not the paper's MAX(5) robustness "
        "variant -- MAX(1) is itself the paper's primary specification, not a weaker substitute. "
        "SINGLE-VINTAGE HOLDING instead of the paper's overlapping-portfolio construction -- "
        "mirrors every prior cross-sectional strategy's own approved deviation. EXIT RULE is ONLY "
        "the 21-trading-day time-stop or the synthetic protective stop -- deliberately no "
        "percentile-based early exit, same discipline established for every prior strategy. "
        "PROTECTIVE STOP-LOSS (8%) and POSITION SIZING (1% risk per unit) are NOT PART OF THE "
        "ORIGINAL METHODOLOGY AT ALL -- the source paper is a factor-return study with no "
        "position-level risk management whatsoever."
    ),
    distinctiveness=(
        "Ninth strategy in this program, and the first BEHAVIORAL (lottery/gambling-preference) "
        "mechanism tested -- every prior strategy is either trend-following, cross-sectional "
        "momentum, short-horizon reversal, event-driven (earnings), or risk-based (beta/"
        "liquidity). Zero factor-tag overlap with anything currently researched or paper-trading "
        "(see swing_research/research_roadmap.py's EXISTING_STRATEGY_TAGS) -- the strongest "
        "diversification candidate available from price data alone at the time this strategy was "
        "selected. Reuses swing_research/cross_sectional.py's existing vectorized "
        ".rank(pct=True, axis=1) pattern via a new compute_max_effect_percentile_ranks() function. "
        "Known future overlap risk (disclosed, not a concern for this strategy alone): highly "
        "correlated with Idiosyncratic Volatility Anomaly, a separate, not-yet-implemented "
        "roadmap candidate -- if both are ever implemented, their overlap should be disclosed "
        "together, not treated as two fully independent diversification wins."
    ),
    assumptions_impact=(
        "Long-only scope reduction: DIRECTIONALLY UNKNOWN impact. "
        "MAX(1) vs. MAX(5): MINOR -- MAX(1) is the paper's own primary, most-cited specification. "
        "1-month formation period: MINOR, a direct restatement of the paper's own headline window. "
        "Single-vintage holding (vs. the paper's overlapping-portfolio construction): "
        "MODERATE-to-MATERIAL, DIRECTIONALLY UNKNOWN -- identical reasoning to every prior "
        "cross-sectional strategy's own single-vintage deviation. "
        "Exit rule (21-trading-day time-stop only, no percentile-based early exit): N/A for the "
        "time-stop itself. "
        "Protective stop-loss (8%) and position sizing (1% risk/unit): MINOR by themselves, but "
        "structurally significant in that NEITHER exists in the source paper at all."
    ),
)
