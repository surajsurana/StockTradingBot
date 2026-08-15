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
