"""
Head of Research roadmap -- an extension of the Published Research Analyst's
role (published_research_analyst.py), from "faithfully record the ONE
strategy the user already picked" to "continuously maintain a ranked
roadmap of CANDIDATE published strategies not yet implemented, and be able
to explain why each one is or isn't next."

Deliberately a NEW, separate module rather than an edit to
published_research_analyst.py: that file's PublishedStrategy records are
the permanent, faithful record of strategies this program has ALREADY
committed to implementing (imported by research_director.py's
run_*_experiment wrappers) -- a different job from planning what to look
at next. Keeping them apart means this file can be re-run/re-scored freely
without any risk of touching the frozen, already-implemented records.

ISOLATION / GOVERNANCE (unchanged from the rest of this program): this
module never modifies acceptance_criteria.py, evidence_quality.py,
cross_strategy_review.py, or anything under deployment/ -- it imports
deployment.deployment_manager.list_strategies() READ-ONLY, the same
reuse-by-import convention used throughout this program, purely to know
what's already been researched (for diversification scoring). It writes
nothing back to the registry. It does not run backtests, touch paper
trading, or affect certification/scheduler/deployment status in any way --
purely a planning layer over candidates that haven't been implemented yet.

RESEARCH UNIVERSE (per explicit direction 2026-08-12): only peer-reviewed
academic papers, well-known quantitative finance research, and widely
accepted trading books with substantial historical validation. No YouTube/
Reddit/social-media strategies, no commercial black-box systems, no
unverified blogs -- these are never even added as CandidateProfile entries,
not scored-and-rejected.

DATA FEASIBILITY: DATA_CAPABILITIES below is a declarative, evidence-based
record of what this platform can ACTUALLY source today -- most of it
already load-bearing precedent (e.g. the point-in-time-fundamentals gap was
independently confirmed, live, against real NSE symbols, during the PEAD
deferral investigation on 2026-08-05; see swing_research/strategy_library/pead.md
and deployment/state/strategy_registry.json's pead entry for the primary
source). classify_data_feasibility() checks each candidate's declared
data_requirements against this table mechanically, so a candidate is never
silently mis-classified by hand.
"""

import os
from dataclasses import dataclass

from deployment.deployment_manager import list_strategies, REGISTRY_PATH


# =====================================================================
# What this platform can actually source today (facts, not aspirations).
# =====================================================================
#
# True  = fully available, already used somewhere in this program.
# False = confirmed absent -- either directly investigated (see the
#         per-flag comment) or structurally impossible given the platform's
#         data source (yfinance, free tier) and NSE cash-equity-only scope.
DATA_CAPABILITIES = {
    "daily_ohlcv_history": True,
    # data/fetch_historical.py, yfinance -- up to "max" period, used by
    # every strategy in this program.
    "volume": True,
    # Part of the same OHLCV pull.
    "sector_classification": True,
    # research_lab/performance_analyst.py's load_sector_map() (NSE Nifty
    # 500 CSV) -- a coarse sector proxy, already used as Turtle's
    # correlation-group cap.
    "current_fundamentals_snapshot": True,
    # fundamentals/fundamental_agent.py: trailingEps, returnOnEquity,
    # debtToEquity, revenueGrowth, profitMargins, trailingPE, sector --
    # a SNAPSHOT as of today only, not a historical time series.
    "shares_outstanding_snapshot": True,
    # yfinance .info's sharesOutstanding -- also snapshot-only.
    "point_in_time_fundamentals_history": False,
    # CONFIRMED ABSENT 2026-08-05 (PEAD deferral investigation, live-tested
    # against RELIANCE.NS/TCS.NS): yfinance's quarterly_income_stmt /
    # quarterly_financials / earnings_history all cap out around 4-5
    # trailing quarters (~1 year) -- nowhere near the ~8-10 years of
    # point-in-time (as-then-reported, not restated) fundamentals a
    # multi-year cross-sectional factor backtest needs. No other data
    # source is integrated anywhere in this program. This single gap is
    # why value/quality/profitability/accruals/asset-growth candidates
    # below are NOT_CURRENTLY_IMPLEMENTABLE, not just "need an adaptation"
    # -- using TODAY's fundamentals to generate a signal dated years in the
    # past would be look-ahead bias, not a disclosed scope reduction.
    "analyst_estimates_history": False,
    # Same 2026-08-05 investigation: no consensus-estimate history via any
    # integrated source (needed for SUE/PEAD and analyst-revision momentum).
    "options_data": False,
    "order_book_data": False,
    "intraday_tick_data": False,
    # Only daily candles are fetched anywhere in this program.
    "short_interest_borrow_availability": False,
    # No NSE SLB (securities lending/borrowing) integration -- the same
    # reason every strategy in this program already discloses LONG ONLY as
    # a scope reduction (see published_research_analyst.py).
    "macro_economic_timeseries": False,
    # macro/macro_strategist.py reads world/market HEADLINES via Claude,
    # not a macro time series (rates, CPI, PMI, VIX-equivalent, etc.) --
    # a genuinely different kind of input.
    "insider_transaction_data": False,
    # NSE does publish insider-trading (SAST) disclosures publicly, but
    # nothing in this program scrapes or stores them.
    "corporate_actions_buyback_history": False,
    "ipo_date_history": False,
    "index_membership_history": False,
    # swing_research/universe.py freezes CURRENT constituents only --
    # disclosed survivorship-bias caveat in that file already.

    # --- Added 2026-08-15, India-specific discovery pass ---
    "index_reconstitution_history": False,
    # NSE publishes Nifty 50/200/500 addition/deletion circulars publicly,
    # but nothing in this program compiles or stores them as a queryable
    # historical dataset.
    "promoter_pledge_disclosure_history": False,
    # SEBI-mandated quarterly shareholding-pattern disclosures (promoter
    # pledge %) are public filings, not integrated anywhere here.
    "fii_dii_flow_history": False,
    # NSE/SEBI publish daily FII/DII net-flow figures publicly; not
    # integrated. Also NOTE: this would be a portfolio-level MARKET-TIMING
    # input, not a per-symbol signal -- a structural mismatch with this
    # program's Strategy interface (entry_signal_at is per-symbol), a
    # second, non-data blocker even if the data gap were closed.
    "bonus_issue_announcement_history": False,
    # Historical corporate-action announcement dates/text are not
    # integrated anywhere in this program.
    "india_vix_history": False,
    # NOT CONFIRMED available via yfinance/any integrated source -- unlike
    # every other False flag above (which are confirmed-absent via direct
    # investigation, e.g. the 2026-08-05 PEAD probe), this one is simply
    # UNVERIFIED. Plausibly one of the cheaper gaps to close (a single
    # index ticker, not a new vendor) -- worth a quick check before
    # assuming it's unavailable, disclosed here rather than guessed either way.
}


@dataclass
class CandidateProfile:
    """One candidate strategy the Head of Research is aware of but has
    NOT yet implemented. Fields mirror the structured profile requested
    2026-08-12: identity, mechanism, operational shape, data needs, and
    the qualitative judgments (strengths/weaknesses/replication quality)
    a human would want before spending research time on it.

    The four *_score fields are this module's own judgment (0-10), each
    with a one-line rationale baked into known_strengths/known_weaknesses/
    academic_replication_quality -- NOT computed from the other fields, so
    they can be individually revisited/disputed without touching the
    mechanical fields (data_requirements, factor_tags) that other
    functions below rely on.
    """
    key: str
    name: str
    authors: str
    publication: str
    year: int
    asset_class: str
    direction: str                    # "Long only", "Long-short", etc.
    factor_family: str                # human-readable
    factor_tags: set                  # coarse tags, for diversification overlap vs. existing portfolo
    mechanism: str
    typical_holding_period: str
    expected_trade_frequency: str
    data_requirements: list           # tags into DATA_CAPABILITIES
    known_strengths: str
    known_weaknesses: str
    academic_replication_quality: str
    evidence_sufficiency_note: str
    academic_evidence_score: float        # 0-10, this module's judgment
    expected_robustness_score: float      # 0-10
    operational_simplicity_score: float   # 0-10
    research_value_score: float           # 0-10
    data_availability_score: float        # 0-10 (how much of what's needed we actually have)
    implementation_feasibility_score: float  # 0-10 (adaptation risk GIVEN available data)
    notes: str = ""


# =====================================================================
# Existing portfolio -- coarse factor-family tags for diversification
# scoring only. Deliberately kept here (not in deployment/, which stays
# frozen/unmodified) since this is a planning-layer judgment, not a
# deployment fact. Update this dict whenever a new strategy is
# registered in deployment/state/strategy_registry.json, so future
# roadmap runs stay diversification-aware of it.
# =====================================================================
EXISTING_STRATEGY_TAGS = {
    "turtle_system2": {"trend_following"},
    "minervini_trend_template_filter": {"trend_following", "momentum_cross_sectional"},
    "fifty_two_week_high_momentum": {"momentum_cross_sectional"},
    "ma_crossover": {"trend_following"},
    "mean_reversion": {"reversal_short_horizon"},
    "cross_sectional_momentum": {"momentum_cross_sectional"},
    "pead": {"earnings_drift"},
    "short_term_reversal": {"reversal_short_horizon"},
    "betting_against_beta": {"risk_based"},
}

# How much a given (research_verdict, deployment_status) combination
# "occupies" a factor family for diversification purposes -- a strategy
# that's live in paper trading crowds that family far more than one that
# was REJECTed and archived (which still tells us the mechanism was tried,
# worth a small residual penalty so we don't re-propose near-duplicates,
# but shouldn't block a genuinely different treatment of the same broad
# family the way a currently-running strategy should).
_STATUS_OVERLAP_WEIGHT = {
    ("PASS", "PAPER_TRADING"): 3.0,
    ("PASS", "PILOT_LIVE"): 4.0,
    ("PASS", "PRODUCTION"): 5.0,
    ("PASS", "RESEARCH"): 2.0,
    ("INCONCLUSIVE", "RESEARCH"): 1.0,
    ("REJECT", "ARCHIVED"): 0.5,
    ("REJECT", "RESEARCH"): 0.5,
    ("REJECT", "PRODUCTION"): 0.5,
    ("NOT_YET_EVALUATED", "RESEARCH"): 0.0,
}
_DEFAULT_OVERLAP_WEIGHT = 1.0

DEFAULT_WEIGHTS = {
    "academic_evidence": 0.20,
    "data_availability": 0.15,
    "implementation_feasibility": 0.15,
    "diversification": 0.20,
    "expected_robustness": 0.15,
    "operational_simplicity": 0.10,
    "research_value": 0.05,
}
assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


def classify_data_feasibility(data_requirements: list) -> tuple:
    """
    Mechanically checks a candidate's declared data_requirements against
    DATA_CAPABILITIES. Returns (classification, reasons) where
    classification is one of:
      "IMPLEMENTABLE"                        -- every requirement is fully
                                                 available.
      "IMPLEMENTABLE_WITH_DISCLOSED_ADAPTATIONS" -- every requirement is
                                                 available but the
                                                 candidate itself declares
                                                 (via its own notes/known_
                                                 weaknesses) a real proxy
                                                 stands in for something not
                                                 directly measurable; this
                                                 function can only detect
                                                 the data-presence half, so
                                                 candidates set this via
                                                 the needs_disclosed_adaptation
                                                 flag below, not inferred.
      "NOT_CURRENTLY_IMPLEMENTABLE"           -- at least one requirement
                                                 is confirmed absent.
    This never invents an adaptation -- a requirement DATA_CAPABILITIES
    doesn't recognize at all is treated as unavailable (fail closed, same
    conservative default fundamentals/fundamental_agent.py already uses
    for missing metrics).
    """
    missing = [tag for tag in data_requirements if not DATA_CAPABILITIES.get(tag, False)]
    if missing:
        reasons = [f"Requires '{tag}', confirmed unavailable on this platform." for tag in missing]
        return "NOT_CURRENTLY_IMPLEMENTABLE", reasons
    reasons = [f"Requires '{tag}', available." for tag in data_requirements]
    return "IMPLEMENTABLE", reasons


def _overlap_weight(research_verdict: str, deployment_status: str) -> float:
    return _STATUS_OVERLAP_WEIGHT.get((research_verdict, deployment_status), _DEFAULT_OVERLAP_WEIGHT)


def compute_diversification_score(factor_tags: set, portfolio_records: list) -> tuple:
    """
    10 = no overlap at all with anything already researched. Each existing
    strategy sharing at least one factor_tag subtracts a penalty scaled by
    how "occupied" that family currently is (see _STATUS_OVERLAP_WEIGHT) --
    a live PAPER_TRADING strategy in the same family costs far more
    diversification credit than a REJECTed/ARCHIVED one. Floors at 0, never
    negative. Returns (score, overlap_notes) so the reasoning is visible,
    not just the number.
    """
    penalty = 0.0
    overlap_notes = []
    for rec in portfolio_records:
        tags = EXISTING_STRATEGY_TAGS.get(rec.strategy_key)
        if tags is None:
            continue   # strategy not yet classified here -- see module docstring
        shared = tags & factor_tags
        if not shared:
            continue
        w = _overlap_weight(rec.research_verdict.value, rec.deployment_status.value)
        penalty += w
        overlap_notes.append(
            f"{rec.display_name} ({rec.strategy_id}, {rec.research_verdict.value}/"
            f"{rec.deployment_status.value}) shares: {', '.join(sorted(shared))}"
        )
    return round(max(0.0, 10.0 - penalty), 1), overlap_notes


@dataclass
class ScoredCandidate:
    candidate: CandidateProfile
    feasibility_classification: str
    feasibility_reasons: list
    diversification_score: float
    diversification_overlap_notes: list
    axis_scores: dict
    total_score: float


def score_candidate(candidate: CandidateProfile, portfolio_records: list,
                     weights: dict = DEFAULT_WEIGHTS) -> ScoredCandidate:
    feasibility_classification, feasibility_reasons = classify_data_feasibility(candidate.data_requirements)
    diversification, overlap_notes = compute_diversification_score(candidate.factor_tags, portfolio_records)

    axis_scores = {
        "academic_evidence": candidate.academic_evidence_score,
        "data_availability": candidate.data_availability_score,
        "implementation_feasibility": candidate.implementation_feasibility_score,
        "diversification": diversification,
        "expected_robustness": candidate.expected_robustness_score,
        "operational_simplicity": candidate.operational_simplicity_score,
        "research_value": candidate.research_value_score,
    }
    total = sum(axis_scores[k] * weights[k] for k in weights)

    return ScoredCandidate(
        candidate=candidate, feasibility_classification=feasibility_classification,
        feasibility_reasons=feasibility_reasons, diversification_score=diversification,
        diversification_overlap_notes=overlap_notes, axis_scores=axis_scores,
        total_score=round(total, 2),
    )


# =====================================================================
# Candidate database. Every entry is a real, published, peer-reviewed or
# widely-accepted-book strategy -- see each publication field for the
# citation. No YouTube/Reddit/social/black-box source is ever added here
# (per the 2026-08-12 research-universe restriction) -- excluded
# candidates of that KIND are simply never entries in this list at all,
# not scored-and-rejected (see PERMANENTLY_EXCLUDED below for the
# different, platform-specific exclusion reasons that DO apply to a few
# genuinely-published strategies).
# =====================================================================
CANDIDATES = [
    # NOTE: Betting Against Beta (Frazzini & Pedersen 2014) is no longer a
    # candidate here -- it was researched 2026-08-15 (SW-009) and REJECTed
    # (temporal robustness failure, EXP-024/EXP-025/EXP-026 -- see
    # swing_research/strategy_library/betting_against_beta.md). Removed
    # from CANDIDATES since it's no longer "not yet implemented"; its
    # "risk_based" tag is now tracked in EXISTING_STRATEGY_TAGS above so
    # future risk-based candidates (idiosyncratic volatility, downside
    # beta, MAX effect) score their diversification against it correctly.

    # =================================================================
    # India-specific discovery pass (2026-08-15) -- NSE/India-market-
    # focused candidates, researched via WebSearch/WebFetch against real,
    # verifiable sources (NSE Indices' own published methodology PDFs,
    # peer-reviewed Indian-market journal papers, and Saurabh Mukherjea/
    # Ambit Capital's published book methodology). Not automatically
    # favored over the global candidates above -- scored on the identical
    # weighted rubric, including diversification against strategies
    # already tested in THIS program (not against each other).
    # =================================================================
    CandidateProfile(
        key="nifty_momentum_30_style",
        name="Risk-Adjusted Blended Momentum (Nifty200 Momentum 30 methodology)",
        authors="NSE Indices Limited",
        publication="Nifty200 Momentum 30 Index Methodology (official NSE Indices methodology document, "
                     "nsearchives.nseindia.com) -- verified via WebFetch/WebSearch 2026-08-15, not from memory",
        year=2019,
        asset_class="Single-stock equities (Nifty 200 constituents), cross-sectional",
        direction="Long-only by construction (an index, not a long-short factor).",
        factor_family="Momentum (risk-adjusted, blended horizon)",
        factor_tags={"momentum_cross_sectional"},
        mechanism="A 'Normalised Momentum Score' blends 6-month AND 12-month price return, each divided "
                  "by the stock's own daily-return volatility (a Sharpe-ratio-like risk adjustment) -- a "
                  "genuinely more sophisticated construction than a single-horizon raw-return momentum "
                  "score. Real, live product: multiple AMCs (SBI, HDFC, etc.) run index funds/ETFs "
                  "tracking this exact methodology.",
        typical_holding_period="Semi-annual reconstitution (per the index's own methodology)",
        expected_trade_frequency="Low-moderate -- twice-yearly rebalance is less frequent than every "
                                  "other cross-sectional strategy in this program",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Exact, publicly documented, currently-live methodology with real institutional "
                         "capital tracking it -- about as strong a 'this is real and used' signal as exists "
                         "outside academic replication; fully implementable from data already fetched.",
        known_weaknesses="Same broad momentum family already represented twice in this portfolio (SW-003 "
                          "PASS/PAPER, SW-006 PASS/HOLD) -- the volatility-adjustment and dual-horizon "
                          "blend are real refinements, but the marginal diversification value of a THIRD "
                          "momentum-family candidate is limited, same reasoning already applied to "
                          "Industry Momentum on the global roadmap.",
        academic_replication_quality="Not an academic paper -- an official index-provider methodology, "
                                      "live since 2019, semi-annually audited and rebalanced by NSE Indices "
                                      "itself. Different credibility TYPE than a peer-reviewed paper "
                                      "(institutional/regulatory rather than academic), explicitly permitted "
                                      "under 'well-known quantitative finance research.'",
        evidence_sufficiency_note="Sufficient as a real, live, audited methodology -- though it is a "
                                   "PRODUCT specification, not a research finding making a causal claim; "
                                   "treat 'the index exists and has AUM' as different evidence than 'a "
                                   "paper found a statistically significant premium.'",
        academic_evidence_score=6, expected_robustness_score=6, operational_simplicity_score=7,
        research_value_score=4, data_availability_score=10, implementation_feasibility_score=8,
    ),
    CandidateProfile(
        key="nifty_alpha_jensens",
        name="Jensen's Alpha Selection (Nifty Alpha 50 / Nifty200 Alpha 30 methodology)",
        authors="NSE Indices Limited",
        publication="Nifty Alpha 50 / Nifty200 Alpha 30 Index Methodology (official NSE Indices "
                     "methodology document) -- verified via WebSearch 2026-08-15",
        year=2011,
        asset_class="Single-stock equities (Nifty 100/200 constituents), cross-sectional",
        direction="Long-only by construction.",
        factor_family="Risk-adjusted regression alpha",
        # Tagged with BOTH "risk_based_alpha" and "risk_based" (not just the
        # former) -- it shares real regression machinery and conceptual
        # lineage with Betting Against Beta's (SW-009, REJECT) shrunk-beta
        # estimator, per this candidate's own known_weaknesses below. Tags
        # must carry the mechanical overlap themselves (compute_diversification_score()
        # does exact-set intersection, not fuzzy matching) -- describing an
        # overlap in prose without tagging it would silently score this as
        # fully independent (10/10), contradicting the analysis below.
        factor_tags={"risk_based_alpha", "risk_based"},
        mechanism="Selects and weights stocks by Jensen's Alpha -- the INTERCEPT term of a CAPM-style "
                  "regression of each stock's returns against the market (Nifty), i.e. risk-adjusted "
                  "outperformance NOT explained by market beta. Mechanically related to (reuses similar "
                  "rolling-regression machinery as) Betting Against Beta's shrunk-beta estimator, but "
                  "selects on the regression's INTERCEPT rather than its SLOPE -- a genuinely different "
                  "economic claim ('this stock beats what its risk alone would predict') from BAB's "
                  "('this stock's risk itself is underpriced').",
        typical_holding_period="Semi-annual reconstitution",
        expected_trade_frequency="Low-moderate",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="A real, live, currently-tracked NSE methodology; distinct economic claim from "
                         "every strategy tested so far, including the just-REJECTed BAB; reuses "
                         "infrastructure (rolling market-model regression) already built for BAB, so "
                         "implementation cost is lower than a from-scratch signal.",
        known_weaknesses="Shares computational machinery and the 'risk-based/regression' family with "
                          "Betting Against Beta (SW-009, REJECT) -- not the same signal, but enough "
                          "conceptual/methodological overlap that a moderate, not full, diversification "
                          "credit is warranted. Also, BAB's own REJECT was specifically a temporal-"
                          "robustness failure possibly linked to a SHORTENED regression lookback (see "
                          "SW-009's Strategy Library doc) -- the same lookback-length tension would need "
                          "to be resolved again here before implementation, not assumed solved.",
        academic_replication_quality="Official, live, audited index-provider methodology (institutional "
                                      "credibility, not peer-reviewed-academic credibility).",
        evidence_sufficiency_note="Sufficient as a real, live, audited methodology, same caveat as the "
                                   "Momentum 30 entry above about product-vs-research evidence type.",
        academic_evidence_score=6, expected_robustness_score=5, operational_simplicity_score=6,
        research_value_score=7, data_availability_score=10, implementation_feasibility_score=7,
    ),
    CandidateProfile(
        key="nifty_low_volatility_30",
        name="Realized Low Volatility (Nifty100 Low Volatility 30 methodology)",
        authors="NSE Indices Limited",
        publication="Nifty100 Low Volatility 30 Index Methodology -- verified via WebSearch 2026-08-15",
        year=2016,
        asset_class="Single-stock equities (Nifty 100 constituents), cross-sectional",
        direction="Long-only by construction.",
        factor_family="Risk-based (realized volatility, not beta)",
        factor_tags={"risk_based"},
        mechanism="Selects and inverse-volatility-weights the lowest-realized-volatility stocks, using "
                  "1-year daily log-return standard deviation directly -- NO market-model regression at "
                  "all, unlike BAB (beta) or the Alpha index above (regression intercept). The simplest, "
                  "most directly comparable candidate to Betting Against Beta on this roadmap.",
        typical_holding_period="Semi-annual reconstitution",
        expected_trade_frequency="Low-moderate",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Real, live, audited methodology; trivially simple to compute (a rolling standard "
                         "deviation, no regression machinery needed at all) -- the lowest implementation "
                         "risk of any candidate in this India-specific batch.",
        known_weaknesses="Shares this program's 'risk_based' tag directly with Betting Against Beta "
                          "(SW-009, REJECT) -- raw volatility and beta are correlated risk measures "
                          "(a low-vol stock is very often also a low-beta stock), so this candidate should "
                          "be read as a CLOSE cousin of the just-rejected strategy, not an independent test. "
                          "A REJECT on BAB is meaningful, but non-trivial, prior evidence about how this "
                          "family performs on this exact universe/period, not proof this specific measure fails too.",
        academic_replication_quality="Official, live, audited index-provider methodology.",
        evidence_sufficiency_note="Sufficient as a live methodology; given the direct family overlap with "
                                   "SW-009's REJECT, this is better framed as 'worth a quick look given how "
                                   "cheap it is to test' than a high-conviction independent candidate.",
        academic_evidence_score=6, expected_robustness_score=5, operational_simplicity_score=9,
        research_value_score=3, data_availability_score=10, implementation_feasibility_score=9,
    ),
    CandidateProfile(
        key="nifty_alpha_low_volatility_30",
        name="Combined Alpha + Low-Volatility Screen (Nifty Alpha Low-Volatility 30 methodology)",
        authors="NSE Indices Limited",
        publication="Nifty Alpha Low-Volatility 30 Index Methodology -- verified via WebSearch 2026-08-15",
        year=2017,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only by construction.",
        factor_family="Combined risk-based (alpha + volatility)",
        factor_tags={"risk_based_alpha", "risk_based"},
        mechanism="A real, separately-published NSE index combining the Alpha selection above with a "
                  "low-volatility screen/weighting overlay -- included for completeness since it is a "
                  "genuinely distinct, separately-tracked product, not merely 'the average of two rows above.'",
        typical_holding_period="Semi-annual reconstitution",
        expected_trade_frequency="Low-moderate",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Real, live, audited methodology; fully price-data-implementable.",
        known_weaknesses="Touches BOTH risk-based tags already present in this program's tested history "
                          "(Alpha-family overlap AND direct volatility/beta-family overlap with SW-009) -- "
                          "the weakest diversification case of any candidate in this batch by construction, "
                          "since it's explicitly a combination of two already-represented mechanisms.",
        academic_replication_quality="Official, live, audited index-provider methodology.",
        evidence_sufficiency_note="Sufficient as a live methodology; lowest research-priority in this batch "
                                   "given the compounded family overlap.",
        academic_evidence_score=6, expected_robustness_score=5, operational_simplicity_score=6,
        research_value_score=2, data_availability_score=10, implementation_feasibility_score=7,
    ),
    CandidateProfile(
        key="nifty_quality_30",
        name="ROE/Leverage/Earnings-Stability Composite (Nifty200 Quality 30 methodology)",
        authors="NSE Indices Limited",
        publication="Nifty200 Quality 30 Index Methodology -- verified via WebFetch/WebSearch 2026-08-15",
        year=2018,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only by construction.",
        factor_family="Quality (fundamentals composite)",
        factor_tags={"quality"},
        mechanism="Quality score = Return on Equity + Financial Leverage (Debt/Equity) + Earnings (EPS) "
                  "growth VARIABILITY, each measured over the PRIOR 5 YEARS -- a shorter fundamentals "
                  "window than Piotroski/QMJ's typical multi-year point-in-time comparisons, but still a "
                  "genuine historical (not snapshot) fundamentals requirement.",
        typical_holding_period="Semi-annual reconstitution",
        expected_trade_frequency="Low",
        data_requirements=["daily_ohlcv_history", "point_in_time_fundamentals_history"],
        known_strengths="Real, live, currently-tracked NSE methodology (multiple AMC index funds track it) "
                         "-- strong evidence this is a credible, institutionally-accepted India-specific "
                         "quality construction, not a hypothetical.",
        known_weaknesses="Blocked by the same point-in-time fundamentals-history gap as every "
                          "quality/value candidate on the global roadmap -- being India-specific does not "
                          "change this platform's underlying yfinance data ceiling at all.",
        academic_replication_quality="Official, live, audited index-provider methodology.",
        evidence_sufficiency_note="Sufficient as a live methodology; blocked purely by data access, "
                                   "identical situation to the global roadmap's Quality/Value bucket.",
        academic_evidence_score=6, expected_robustness_score=6, operational_simplicity_score=4,
        research_value_score=5, data_availability_score=2, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="nifty_value_20",
        name="Earnings/Book/Dividend Value Composite (Nifty50 Value 20 methodology)",
        authors="NSE Indices Limited",
        publication="Nifty50 Value 20 Index Methodology (general index-family description; exact current "
                     "weighting formula not independently re-verified beyond the general value-composite "
                     "description found via WebSearch 2026-08-15 -- disclosed as lower-confidence than the "
                     "Momentum/Alpha/Low-Vol/Quality entries above, which were directly confirmed)",
        year=2009,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only by construction.",
        factor_family="Value",
        factor_tags={"value"},
        mechanism="A value composite drawing on earnings yield (P/E), price-to-book, dividend yield, and "
                  "return on capital -- the same broad value construction as the global roadmap's Basu/"
                  "Fama-French value candidate, applied to the Nifty 50 specifically.",
        typical_holding_period="Semi-annual reconstitution",
        expected_trade_frequency="Low",
        data_requirements=["daily_ohlcv_history", "point_in_time_fundamentals_history"],
        known_strengths="A real, live NSE product; India-specific evidence that a value tilt is considered "
                         "institutionally investable here.",
        known_weaknesses="Same fundamentals-history block as Quality above. Also the single candidate in "
                          "this India-specific batch with the lowest source-verification confidence -- the "
                          "exact scoring formula should be re-confirmed against NSE's own methodology "
                          "document before any implementation, not just this summary.",
        academic_replication_quality="Official index-provider methodology (confidence on exact formula "
                                      "details lower than other NSE-index entries in this batch).",
        evidence_sufficiency_note="Directionally sufficient (value-in-India is extremely well-established "
                                   "generally), but THIS specific index's exact formula should be "
                                   "re-verified, not taken from this summary alone, before implementation.",
        academic_evidence_score=5, expected_robustness_score=6, operational_simplicity_score=4,
        research_value_score=4, data_availability_score=2, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="sehgal_long_term_contrarian_india",
        name="Long-Term Contrarian with 1-Year Skip Period (Sehgal & Balakrishnan 2002)",
        authors="Sehgal, S. and Balakrishnan, A.",
        publication="\"Contrarian and Momentum Strategies in the Indian Capital Market,\" Vikalpa, 27(1), "
                     "13-19 (2002) -- verified real via WebSearch 2026-08-15",
        year=2002,
        asset_class="Single-stock equities (Indian capital market), cross-sectional",
        direction="Long-short in the original (contrarian long-short portfolio); long-only bottom-decile "
                   "here, same disclosed reduction as every strategy in this program.",
        factor_family="Reversal (long-horizon, India-specific evidence)",
        factor_tags={"reversal_long_horizon"},
        mechanism="Tests BOTH short-term momentum (continuation) and long-term contrarian (reversal) "
                  "specifically on Indian data, finding momentum in short-term returns and reversal in "
                  "long-term returns -- but critically, the long-term contrarian test explicitly inserts "
                  "a ONE-YEAR SKIP PERIOD between the formation period and the holding period (to avoid "
                  "short-term momentum/microstructure effects contaminating the long-horizon reversal "
                  "measurement) -- a specific methodological detail the global roadmap's De Bondt-Thaler "
                  "candidate does not itself specify, and this program's existing momentum strategies "
                  "(SW-003, SW-006) explicitly do NOT use a skip period at all (a disclosed omission in "
                  "both).",
        typical_holding_period="Long-horizon (multi-year, consistent with the global De Bondt-Thaler entry)",
        expected_trade_frequency="Very low",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Direct India-specific evidence for the long-term reversal effect already on the "
                         "global roadmap (De Bondt-Thaler) -- corroborating, independent confirmation "
                         "rather than a purely US/global finding being assumed to transfer. The 1-year "
                         "skip-period detail is a genuine, disclosed methodological refinement worth "
                         "carrying into whichever long-term-reversal implementation is eventually built.",
        known_weaknesses="Same factor family (reversal_long_horizon) as the global roadmap's De Bondt-"
                          "Thaler candidate -- this is best treated as ADDITIONAL EVIDENCE for that same "
                          "candidate (and its skip-period detail folded into that implementation), not a "
                          "fully independent second candidate to implement separately.",
        academic_replication_quality="A single, older (2002) India-specific paper -- real and "
                                      "peer-reviewed-adjacent (Vikalpa is IIM Ahmedabad's management "
                                      "journal), but a thinner, less-replicated evidence base than the "
                                      "original De Bondt-Thaler (1985) or its decades of international "
                                      "replication.",
        evidence_sufficiency_note="Sufficient as corroborating evidence, not as a standalone primary source "
                                   "-- strengthens the case for the existing global candidate rather than "
                                   "standing alone.",
        academic_evidence_score=6, expected_robustness_score=6, operational_simplicity_score=5,
        research_value_score=5, data_availability_score=10, implementation_feasibility_score=8,
    ),
    CandidateProfile(
        key="volume_weighted_momentum_india",
        name="Volume-Based Momentum and Contrarian Strategies (Maheshwari & Dhankar 2017)",
        authors="Maheshwari, S. and Dhankar, R.S.",
        publication="\"Profitability of Volume-based Momentum and Contrarian Strategies in the Indian "
                     "Stock Market,\" published in a peer-reviewed journal (SAGE) -- verified real via "
                     "WebSearch 2026-08-15",
        year=2017,
        asset_class="Single-stock equities (Indian stock market), cross-sectional",
        direction="Long-short in the original; long-only here, same disclosed reduction.",
        factor_family="Momentum/reversal, VOLUME-conditioned",
        factor_tags={"momentum_cross_sectional", "volume_attention"},
        mechanism="Forms momentum/contrarian portfolios using TRADING VOLUME as a screen or weighting "
                  "input alongside past return, rather than a pure price-return formation score -- "
                  "genuinely distinct signal CONSTRUCTION from every price-only momentum/reversal "
                  "candidate already tested in this program, even though the broad economic story "
                  "(continuation/reversal) is related.",
        typical_holding_period="Not independently re-confirmed here (would need the full paper); assumed "
                                "comparable to other Indian momentum studies (months, not years)",
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Genuinely different signal construction (volume-conditioned) from every existing "
                         "momentum/reversal strategy in this program; fully implementable from data already "
                         "fetched (Volume is already an OHLCV column); India-specific evidence.",
        known_weaknesses="Still broadly in the momentum/reversal family by mechanism, so meaningful (if not "
                          "complete) tag overlap with SW-003/SW-006/SW-008; the exact volume-weighting "
                          "formula was not independently re-verified here beyond the paper's existence and "
                          "abstract-level description -- would need the full paper before implementation.",
        academic_replication_quality="Single peer-reviewed paper (SAGE journal) -- real, but not yet "
                                      "independently replicated elsewhere the way this module's global "
                                      "candidates' foundational papers have been.",
        evidence_sufficiency_note="Directionally sufficient to justify a closer read of the full paper "
                                   "before committing research time; not yet at the same evidentiary bar "
                                   "as the global roadmap's top-ranked candidates.",
        academic_evidence_score=5, expected_robustness_score=5, operational_simplicity_score=6,
        research_value_score=6, data_availability_score=10, implementation_feasibility_score=7,
    ),
    CandidateProfile(
        key="nifty_index_inclusion_effect",
        name="Nifty Index Inclusion/Exclusion Effect",
        authors="Multiple (e.g. Selvam, Indhumathi & Lydia 2012; more recent 2010-2024 studies)",
        publication="Multiple peer-reviewed studies on CNX Nifty/Nifty 50 index addition and deletion "
                     "effects (e.g. Journal of Business and Economic Studies-adjacent venues) -- verified "
                     "real, multiple independent studies, via WebSearch 2026-08-15",
        year=2012,
        asset_class="Single-stock equities (index reconstitution events), event-driven",
        direction="Long newly-added stocks around inclusion / avoid or short newly-removed stocks around "
                   "exclusion; long-only-additions here.",
        factor_family="Event-driven / forced institutional flow",
        factor_tags={"index_flow_effect"},
        mechanism="Stocks ADDED to the Nifty 50 (or other Nifty indices) see forced buying from index-"
                  "tracking funds around the reconstitution date, producing abnormal positive returns; "
                  "excluded stocks see the mirror-image forced selling. A STRUCTURALLY DIFFERENT "
                  "mechanism from every other candidate in this program -- driven by mechanical fund "
                  "flows, not price pattern, fundamentals, or risk.",
        typical_holding_period="Short, event-window-based (days to ~60 days -- multiple studies found "
                                "abnormal returns partially REVERSING within roughly 60 days of inclusion)",
        expected_trade_frequency="Very low -- gated by how often the underlying index actually "
                                  "reconstitutes (semi-annual for most Nifty indices), a handful of "
                                  "genuine events per cycle",
        data_requirements=["daily_ohlcv_history", "index_reconstitution_history"],
        known_strengths="Genuinely distinct mechanism (forced flow, not signal-based) -- the strongest "
                         "diversification candidate in this entire India-specific batch. Multiple "
                         "independent Indian studies (2012 and 2010-2024 evidence) find a real, if "
                         "DECAYING and PARTIALLY REVERSING, effect -- consistent with the well-documented "
                         "global S&P 500 inclusion-effect literature this Indian evidence extends.",
        known_weaknesses="Effect is explicitly documented as DECAYING over time (weaker in 2010-2018 than "
                          "2000-2009 per one study) and PARTIALLY REVERSING within ~60 days in another -- "
                          "this is not a clean, stable premium, and event count is inherently low (a "
                          "handful of true reconstitution events per year across the frozen universe), "
                          "meaning statistical power for this program's usual trade-count thresholds "
                          "would be a real concern even if the data gap were closed.",
        academic_replication_quality="Multiple independent Indian-market studies across different time "
                                      "periods, with a consistent (though weakening) direction -- "
                                      "reasonably well-replicated for an India-specific literature, though "
                                      "not to the depth of the classic global anomalies.",
        evidence_sufficiency_note="Sufficient to justify data investment, with the explicit caveat that "
                                   "the effect's own literature describes it as weakening -- any future "
                                   "implementation should test the MOST RECENT sub-period specifically, "
                                   "not just the full historical record.",
        academic_evidence_score=6, expected_robustness_score=4, operational_simplicity_score=5,
        research_value_score=8, data_availability_score=1, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="promoter_pledge_governance_signal",
        name="Promoter Share-Pledging as a Governance/Distress Signal",
        authors="Multiple (e.g. recent Indian-listed-firm studies on promoter pledging and downside risk, "
                 "2009-2023 SEBI disclosure-regime-based samples)",
        publication="Multiple peer-reviewed/working-paper studies on promoter share pledging and Indian "
                     "firm risk -- verified real via WebSearch 2026-08-15 (SEBI's post-2009 mandatory "
                     "promoter-encumbrance disclosure regime is the underlying data source these studies use)",
        year=2023,
        asset_class="Single-stock equities, governance/distress signal",
        direction="Avoid/underweight high-pledge names (long-only universe filter) or long low/no-pledge "
                   "names -- not a classic long-short factor construction in the source literature.",
        factor_family="Governance / distress risk (India-specific)",
        factor_tags={"governance_distress_signal"},
        mechanism="Firms whose promoters have pledged a large fraction of their shares as loan collateral "
                  "show measurably elevated downside-risk exposure and behavioral distortions (reduced "
                  "capex/R&D, forced-selling risk if margin calls trigger) -- a GENUINELY India-specific "
                  "phenomenon at this scale (promoter share pledging is a much larger and more "
                  "structurally embedded practice in Indian markets than in most developed markets), not "
                  "an Indian replication of a Western anomaly.",
        typical_holding_period="Not standardized in the literature -- would need to be defined as an "
                                "implementation choice (e.g. quarterly, matching SEBI's own disclosure "
                                "cadence) rather than taken directly from a single paper's holding period.",
        expected_trade_frequency="Low -- pledge disclosures update quarterly, not daily",
        data_requirements=["daily_ohlcv_history", "promoter_pledge_disclosure_history"],
        known_strengths="The single most genuinely INDIA-SPECIFIC (not a replicated Western anomaly) "
                         "candidate in this entire roadmap, global and India-specific batches combined -- "
                         "no comparable large-scale promoter-pledging phenomenon exists in most developed "
                         "markets this program's other sources study. Real, multi-study evidence (elevated "
                         "downside risk, reduced investment) across a meaningful sample (1,452+ firms in "
                         "one study).",
        known_weaknesses="This is a RISK/AVOIDANCE signal (elevated distress risk), not a demonstrated "
                          "POSITIVE-return-predicting factor the way momentum/value/quality are -- the "
                          "literature supports 'these firms are riskier,' not yet clearly 'avoiding them "
                          "or shorting them earns an documented, quantified excess return' in the same "
                          "decile-sort-backtest sense as every other candidate here. Would need a more "
                          "careful read of the source studies to confirm a genuinely tradeable, quantified "
                          "claim exists before treating this as equivalent in evidentiary weight to a "
                          "return-predictability anomaly paper.",
        academic_replication_quality="Multiple independent recent studies (2023-2025 vintage), consistent "
                                      "direction, but a young and still-developing literature relative to "
                                      "the decades-old classics on the global roadmap.",
        evidence_sufficiency_note="Sufficient to justify data investment and a closer literature read, but "
                                   "NOT yet sufficient to treat as a proven return-predictability finding "
                                   "the way the global roadmap's top candidates are -- flagged as a genuine "
                                   "research-value opportunity specifically BECAUSE it's underexplored, not "
                                   "because the return case is already made.",
        academic_evidence_score=5, expected_robustness_score=5, operational_simplicity_score=5,
        research_value_score=8, data_availability_score=1, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="fii_dii_flow_market_timing",
        name="FII/DII Net-Flow Market-Timing Overlay",
        authors="Multiple (e.g. Springer Future Business Journal 2020 causality study; MDPI JRFM 2024 "
                 "FII-to-DII-dominance study; several others)",
        publication="Multiple peer-reviewed studies on FII/DII flows and Indian stock market returns -- "
                     "verified real via WebSearch 2026-08-15, including bidirectional Granger-causality "
                     "findings between flows and returns",
        year=2020,
        asset_class="Market-level (Nifty/Sensex), not single-stock -- a portfolio-wide overlay",
        direction="A regime/exposure adjustment (e.g. reduce net exposure when FII selling pressure is "
                   "elevated), not a stock-selection long/short construction.",
        factor_family="Institutional-flow market-timing",
        factor_tags={"institutional_flow_market_timing"},
        mechanism="Daily/monthly aggregate FII (Foreign Institutional Investor) and DII (Domestic "
                  "Institutional Investor) net-flow figures, published publicly by NSE/SEBI, show "
                  "documented (bidirectional) causal relationships with subsequent market-level returns "
                  "and volatility -- a genuinely different SHAPE of strategy from every other candidate "
                  "on this roadmap: a market-wide regime overlay, not a per-symbol cross-sectional signal.",
        typical_holding_period="N/A in the per-symbol sense -- would operate as a portfolio-wide exposure "
                                "adjustment, structurally closer to this platform's existing Macro "
                                "Strategist (macro/macro_strategist.py) than to any swing_research Strategy.",
        expected_trade_frequency="N/A -- not a per-symbol entry/exit signal",
        data_requirements=["daily_ohlcv_history", "fii_dii_flow_history"],
        known_strengths="A genuinely distinct MECHANISM TYPE, not just a distinct signal -- if implemented, "
                         "it would be the first market-timing/regime overlay in the swing_research program "
                         "(the existing Macro Strategist plays an analogous role in the LIVE daily pipeline, "
                         "but reads news headlines via Claude, not a quantified flow time series). "
                         "Multiple independent studies confirm the underlying flow-return relationship is real.",
        known_weaknesses="IMPLEMENTATION-SHAPE MISMATCH, not just a data gap: this program's "
                          "swing_research.base.Strategy interface is built around per-symbol "
                          "entry_signal_at()/exit_signal_at() hooks -- a portfolio-level flow overlay "
                          "doesn't fit that shape at all and would need a different architectural pattern "
                          "(closer to the regime filter in strategies/market_regime.py) to even express, "
                          "a second, structural blocker beyond the missing flow-history data itself. Also, "
                          "the causality literature itself is genuinely mixed on DIRECTION (does flow "
                          "predict returns, or do returns predict flow, or both) -- a real, disclosed "
                          "ambiguity about whether this is actually a PREDICTIVE signal or just a "
                          "correlated/coincident one.",
        academic_replication_quality="Multiple independent, fairly recent (2020-2025) studies, generally "
                                      "consistent on the existence of a relationship, genuinely mixed on "
                                      "causal direction -- moderate replication quality with an important "
                                      "open question.",
        evidence_sufficiency_note="Sufficient to justify further investigation, explicitly NOT sufficient "
                                   "to treat as a clean, directional, tradeable signal without resolving "
                                   "the causality-direction ambiguity first.",
        academic_evidence_score=5, expected_robustness_score=4, operational_simplicity_score=2,
        research_value_score=6, data_availability_score=1, implementation_feasibility_score=0,
    ),
    CandidateProfile(
        key="bonus_issue_announcement_drift",
        name="Bonus Issue Announcement Drift",
        authors="Multiple (e.g. Malhotra, Thenmozhi & ArunKumar; Mishra; Dhar & Chhaochharia; others)",
        publication="Multiple SSRN/peer-reviewed working papers on Indian bonus-issue announcement market "
                     "reactions -- verified real via WebSearch 2026-08-15, findings explicitly MIXED across "
                     "studies",
        year=2005,
        asset_class="Single-stock equities, corporate-action event-driven",
        direction="Long ahead of anticipated bonus announcements (if a reliable pre-announcement signal "
                   "existed) -- the literature does not support a clean, agreed-upon trading rule.",
        factor_family="Corporate-action event drift",
        factor_tags={"corporate_action_event"},
        mechanism="Some studies find positive abnormal returns in the days BEFORE a bonus-issue "
                  "announcement (consistent with the announcement being partially anticipated/leaked); "
                  "others find near-zero or even negative reaction ON the announcement day; at least one "
                  "study concludes the Indian market shows semi-strong-form efficiency here (information "
                  "already priced in) and finds NO exploitable reaction at all.",
        typical_holding_period="Short, event-window (days around announcement, per the studies' own event-study design)",
        expected_trade_frequency="Low -- gated by how often bonus issues actually occur in the universe",
        data_requirements=["daily_ohlcv_history", "bonus_issue_announcement_history"],
        known_strengths="A genuinely India-specific corporate-action pattern (bonus issues are far more "
                         "common in India than the economically-similar US practice of stock splits/"
                         "buybacks) -- if a real edge existed, it would be distinctly India-native.",
        known_weaknesses="THE WEAKEST EVIDENTIARY CASE IN THIS ENTIRE INDIA-SPECIFIC BATCH -- the source "
                          "studies directly CONTRADICT each other on both the sign and the existence of any "
                          "abnormal return. This is not a case of 'real effect, decaying over time' (like "
                          "index inclusion) -- it's a case of no clear consensus that a reliably tradeable "
                          "effect exists at all. Included for completeness/transparency of the search, not "
                          "because it clears this program's own evidence bar.",
        academic_replication_quality="Multiple studies exist, but they DISAGREE with each other -- the "
                                      "opposite of convergent replication.",
        evidence_sufficiency_note="INSUFFICIENT -- mixed/contradictory findings across the available "
                                   "studies mean this does not meet the bar of 'enough academic evidence to "
                                   "justify spending research time,' independent of the data-availability "
                                   "question.",
        academic_evidence_score=2, expected_robustness_score=2, operational_simplicity_score=5,
        research_value_score=1, data_availability_score=1, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="coffee_can_quality_growth",
        name="Coffee Can Portfolio (decade-consistency quality-growth screen)",
        authors="Mukherjea, S., Ranjan, R. and Uniyal, P.",
        publication="\"Coffee Can Investing: The Low-Risk Road to Stupendous Wealth\" (2018), Ambit Capital "
                     "/ Marcellus Investment Managers -- verified real via WebSearch 2026-08-15 including "
                     "the exact quantitative screen",
        year=2018,
        asset_class="Single-stock equities, cross-sectional fundamentals screen",
        direction="Long-only, buy-and-hold (explicitly a low-turnover, 'forget about it' philosophy).",
        factor_family="Quality + growth (decade-consistency)",
        factor_tags={"quality"},
        mechanism="A simple, exact, quantifiable screen: minimum market cap Rs. 100 crore, revenue growth "
                  "of AT LEAST 10% per year for EACH of the prior 10 years, and pre-tax Return on Capital "
                  "Employed (ROCE) of AT LEAST 15% for EACH of the prior 10 years -- a genuinely simple "
                  "rule (unlike most academic quality composites, no weighting/blending, just two hard "
                  "thresholds sustained for a decade), widely followed by Indian retail and institutional "
                  "investors alike (Ambit Capital and the author's later firm, Marcellus, run real, "
                  "SEBI-registered PMS products on this philosophy).",
        typical_holding_period="Very long (multi-year buy-and-hold by explicit design)",
        expected_trade_frequency="Extremely low",
        data_requirements=["daily_ohlcv_history", "point_in_time_fundamentals_history"],
        known_strengths="An exact, simple, widely-known, India-native screen with real institutional "
                         "capital following it (Marcellus PMS); 'widely accepted trading book' per this "
                         "program's own research-universe rule, explicitly permitted alongside "
                         "peer-reviewed papers.",
        known_weaknesses="Requires a FULL DECADE of consistent, point-in-time (as-then-reported) annual "
                          "revenue and ROCE figures for every candidate stock, at every formation date, "
                          "across the backtest period -- the single MOST fundamentals-data-hungry candidate "
                          "on this entire roadmap (more demanding than Piotroski, QMJ, or the NSE Quality/"
                          "Value indices' own 5-year windows). Blocked by the exact same point-in-time "
                          "fundamentals gap as every other quality/value candidate, just more severely.",
        academic_replication_quality="Not peer-reviewed academic research -- a published, widely-read "
                                      "practitioner book with real institutional capital deployed on the "
                                      "underlying philosophy, explicitly the 'widely accepted trading book' "
                                      "category this program's research universe already permits.",
        evidence_sufficiency_note="Sufficient as a well-known, exact, India-native screen; blocked purely "
                                   "by (an especially severe version of) this platform's existing data ceiling.",
        academic_evidence_score=5, expected_robustness_score=6, operational_simplicity_score=3,
        research_value_score=6, data_availability_score=1, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="india_vix_regime_overlay",
        name="India VIX Regime Overlay (equity-only operationalization of the volatility risk premium)",
        authors="N/A -- adapted from the general volatility-risk-premium literature (see the global "
                 "roadmap's Options-Based Volatility Risk Premium entry) applied to India's own published "
                 "implied-volatility index",
        publication="India VIX (NSE's own implied-volatility index, methodology licensed from CBOE) -- "
                     "the INDEX itself is a real, long-published NSE data series; no single dedicated "
                     "India-VIX-trading paper verified here, this candidate is a scoped, honest adaptation, "
                     "not a direct paper replication",
        year=2008,
        asset_class="Equity-only regime overlay (NOT an options strategy)",
        direction="A portfolio-wide risk-reduction overlay (e.g. reduce new-entry risk when India VIX is "
                   "elevated) -- not a per-symbol signal, not a genuine volatility-selling strategy.",
        factor_family="Volatility regime (equity-only proxy)",
        factor_tags={"volatility_regime"},
        mechanism="The TRUE volatility-risk-premium trade (implied vol systematically exceeds realized "
                  "vol) requires selling options -- blocked here exactly like the global roadmap's "
                  "Options-Based Volatility Risk Premium candidate, no options data or infrastructure "
                  "exists in this program. The only NSE-cash-equity-feasible operationalization is an "
                  "EQUITY-ONLY regime overlay: use India VIX's LEVEL (not its risk premium) as a risk-off "
                  "signal, conceptually similar to this program's own live Macro Strategist, but "
                  "quantified from a real index series instead of Claude-read headlines.",
        typical_holding_period="N/A -- a regime overlay, not a position-holding rule",
        expected_trade_frequency="N/A",
        data_requirements=["daily_ohlcv_history", "india_vix_history"],
        known_strengths="India VIX is a REAL, long-published (since 2008), NSE-native index -- if it turns "
                         "out to be fetchable from an existing or easily-added source, this would be one "
                         "of the cheaper data gaps to close among the blocked candidates in this batch. "
                         "Would give this program's research pipeline its first genuinely macro/volatility-"
                         "timing input.",
        known_weaknesses="This is an HONEST DOWNGRADE from the real volatility-risk-premium academic "
                          "literature, not a faithful implementation of it -- without options, this can "
                          "only ever be a coarse regime filter, structurally similar to the FII/DII "
                          "candidate's implementation-shape mismatch with this program's per-symbol "
                          "Strategy interface. india_vix_history's availability is UNVERIFIED (not "
                          "confirmed-absent like most other False flags), so its feasibility classification "
                          "here is conservative, not definitive -- worth a real check before ruling out.",
        academic_replication_quality="The underlying volatility-risk-premium literature is well-established "
                                      "globally; this specific India-equity-only adaptation has no dedicated "
                                      "paper behind it -- an honest scoping exercise, not a citation.",
        evidence_sufficiency_note="INSUFFICIENT as a standalone research candidate in its current form -- "
                                   "flagged for completeness and because the underlying data series is "
                                   "real and possibly cheap to access, not because a specific tradeable rule "
                                   "has been established in the literature.",
        academic_evidence_score=3, expected_robustness_score=3, operational_simplicity_score=4,
        research_value_score=4, data_availability_score=2, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="idiosyncratic_volatility",
        name="Idiosyncratic Volatility Anomaly",
        authors="Ang, A., Hodrick, R.J., Xing, Y. and Zhang, X.",
        publication="\"The Cross-Section of Volatility and Expected Returns\", The Journal of Finance, Vol. 61, No. 1",
        year=2006,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-short in the original; long-only top-decile (lowest idiosyncratic vol) here.",
        factor_family="Risk-based / low-volatility anomaly",
        factor_tags={"risk_based"},
        mechanism="Stocks with high idiosyncratic (residual, market-model-adjusted) volatility earn "
                  "anomalously LOW subsequent returns -- the opposite of what a risk premium would "
                  "predict, attributed to lottery-preference/limits-to-arbitrage effects.",
        typical_holding_period="Monthly rebalance",
        expected_trade_frequency="Moderate, same cadence family as BAB above",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Extremely well-known 'low-vol puzzle', directly computable from daily returns "
                         "already fetched, no new data source needed at all.",
        known_weaknesses="The original measure is sensitive to the exact estimation window and known "
                          "to interact with short-term reversal if not controlled for -- a genuine "
                          "implementation-risk area, not just a data gap.",
        academic_replication_quality="Extensively replicated internationally; the puzzle itself (why does high risk pay less?) remains actively debated.",
        evidence_sufficiency_note="Sufficient for a first test, with the reversal-interaction risk explicitly disclosed.",
        academic_evidence_score=8, expected_robustness_score=6, operational_simplicity_score=6,
        research_value_score=7, data_availability_score=10, implementation_feasibility_score=8,
    ),
    CandidateProfile(
        key="long_term_reversal",
        name="Long-Term (De Bondt-Thaler) Reversal",
        authors="De Bondt, W.F.M. and Thaler, R.",
        publication="\"Does the Stock Market Overreact?\", The Journal of Finance, Vol. 40, No. 3",
        year=1985,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-short in the original (long past losers, short past winners); long-only "
                   "bottom-decile (3-5yr formation) here.",
        factor_family="Reversal (long-horizon overreaction)",
        factor_tags={"reversal_long_horizon"},
        mechanism="Investors systematically OVERREACT to extended runs of good/bad news; stocks that "
                  "performed worst over the prior 3-5 years subsequently outperform, mean-reverting as "
                  "the overreaction unwinds -- a genuinely different behavioral story from short-term "
                  "(1-month) reversal's microstructure/liquidity-provision explanation.",
        typical_holding_period="3-5 years (formation and holding both multi-year)",
        expected_trade_frequency="Very low -- one of the lowest-turnover candidates in this roadmap",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="One of the foundational behavioral-finance papers; genuinely orthogonal "
                         "horizon regime to every existing strategy in this program (all of which are "
                         "1 month to 6 months).",
        known_weaknesses="Multi-decade replications show the effect has WEAKENED since discovery and "
                          "concentrates in small/illiquid names -- a real concern for NSE liquidity; "
                          "the 10-year history this platform holds fits only 2-3 non-overlapping "
                          "3-5yr eras, which strains the walk-forward pipeline's window mechanics "
                          "(few, long windows rather than many, short ones).",
        academic_replication_quality="Extensively replicated but with well-documented decay/crowding since the 1980s.",
        evidence_sufficiency_note="Sufficient historically, but the platform's own recency-check discipline "
                                   "(acceptance_criteria.py) is especially important here given the decay concern.",
        academic_evidence_score=9, expected_robustness_score=6, operational_simplicity_score=5,
        research_value_score=9, data_availability_score=10, implementation_feasibility_score=7,
    ),
    CandidateProfile(
        key="amihud_illiquidity",
        name="Amihud Illiquidity Premium",
        authors="Amihud, Y.",
        publication="\"Illiquidity and Stock Returns: Cross-Section and Time-Series Effects\", Journal of Financial Markets, Vol. 5, No. 1",
        year=2002,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only top-decile (most illiquid, by ILLIQ = |return| / dollar volume) -- matches the paper's own long-side premium framing.",
        factor_family="Liquidity risk premium",
        factor_tags={"liquidity"},
        mechanism="Investors demand compensation for holding hard-to-trade (price-impact-sensitive) "
                  "stocks; the ILLIQ ratio (average |daily return| / daily dollar volume) proxies this "
                  "directly from price and volume alone, no shares-outstanding or float data needed.",
        typical_holding_period="Monthly rebalance",
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history", "volume"],
        known_strengths="One of the most-cited asset-pricing papers ever; illiquidity premia are "
                         "documented as STRONGER in emerging markets than in the US large-cap samples "
                         "most other candidates here were tested on -- directly relevant to NSE.",
        known_weaknesses="A pure long-only illiquidity tilt raises real EXECUTION risk (wide spreads, "
                          "slippage) that this platform's own Execution Realism Study already flagged "
                          "as a modeling gap -- this strategy would stress-test that gap harder than any "
                          "strategy tried so far.",
        academic_replication_quality="Extensively replicated across developed and emerging markets.",
        evidence_sufficiency_note="Sufficient -- but pair with the Execution Realism Study's own recommendation before any live promotion.",
        academic_evidence_score=9, expected_robustness_score=8, operational_simplicity_score=8,
        research_value_score=8, data_availability_score=10, implementation_feasibility_score=9,
    ),
    CandidateProfile(
        key="turnover_liquidity",
        name="Turnover / Liquidity Anomaly",
        authors="Datar, V.T., Naik, N.Y. and Radcliffe, R.",
        publication="\"Liquidity and Stock Returns: An Alternative Test\", Journal of Financial Markets, Vol. 1, No. 2",
        year=1998,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only top-decile (lowest share turnover).",
        factor_family="Liquidity risk premium",
        factor_tags={"liquidity"},
        mechanism="Low-turnover stocks earn a premium for illiquidity, using turnover (volume / shares "
                   "outstanding) rather than Amihud's price-impact ratio as the liquidity proxy -- a "
                   "different operationalization of the same broad liquidity-premium family.",
        typical_holding_period="Monthly rebalance",
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history", "volume", "shares_outstanding_snapshot"],
        known_strengths="Well-cited alternative liquidity measure; a useful cross-check against Amihud "
                         "if both were ever run.",
        known_weaknesses="True turnover needs a HISTORICAL shares-outstanding series; this platform "
                          "only has a current snapshot, so a real backtest would need to apply TODAY's "
                          "share count across past history -- a disclosed approximation (mild for "
                          "large, stable Nifty 500 constituents, more material for any stock with a "
                          "big historical share-count change from splits/buybacks/dilution). "
                          "Conceptually redundant with Amihud above -- lower research value as a result.",
        academic_replication_quality="Well-cited but less extensively replicated internationally than Amihud.",
        evidence_sufficiency_note="Sufficient, but the disclosed shares-outstanding approximation should be flagged prominently if implemented.",
        academic_evidence_score=7, expected_robustness_score=6, operational_simplicity_score=6,
        research_value_score=5, data_availability_score=8, implementation_feasibility_score=6,
    ),
    CandidateProfile(
        key="high_volume_return_premium",
        name="High-Volume Return Premium",
        authors="Gervais, S., Kaniel, R. and Mingelgrin, D.H.",
        publication="\"The High-Volume Return Premium\", The Journal of Finance, Vol. 56, No. 3",
        year=2001,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only (stocks with unusually high recent trading volume relative to their own history).",
        factor_family="Volume-driven attention/visibility premium",
        factor_tags={"volume_attention"},
        mechanism="A stock experiencing unusually high trading volume gets a temporary visibility/"
                  "attention boost that predicts short-term positive returns -- a distinct mechanism "
                  "from both momentum (past RETURN) and liquidity (average volume LEVEL).",
        typical_holding_period="Days to a few weeks",
        expected_trade_frequency="Higher than the cross-sectional monthly-rebalance candidates -- "
                                  "volume spikes are more frequent, idiosyncratic events",
        data_requirements=["daily_ohlcv_history", "volume"],
        known_strengths="Directly computable from data already fetched; short holding period offers a "
                         "genuinely different operational cadence from every existing strategy except SW-008.",
        known_weaknesses="Less overwhelming replication evidence than the classics (BAB, Amihud, "
                          "momentum); effect size in the original paper is modest.",
        academic_replication_quality="Cited and replicated, but a smaller, less foundational literature than the anomalies above it in this list.",
        evidence_sufficiency_note="Marginal-but-sufficient; treat as a lower-conviction test than the top-ranked candidates.",
        academic_evidence_score=6, expected_robustness_score=5, operational_simplicity_score=7,
        research_value_score=6, data_availability_score=10, implementation_feasibility_score=9,
    ),
    CandidateProfile(
        key="max_lottery_effect",
        name="MAX Effect (Lottery-Demand Anomaly)",
        authors="Bali, T.G., Cakici, N. and Whitelaw, R.F.",
        publication="\"Maxing Out: Stocks as Lotteries and the Cross-Section of Expected Returns\", Journal of Financial Economics, Vol. 99, No. 2",
        year=2011,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only bottom-decile (avoid/underweight the highest recent single-day-return stocks); "
                   "as a standalone strategy, long the LOW-MAX decile.",
        factor_family="Behavioral / lottery-demand anomaly",
        factor_tags={"behavioral_lottery"},
        mechanism="Stocks with an extreme maximum daily return in the recent month get bid up by "
                   "investors with a preference for lottery-like payoffs, then underperform as that "
                   "demand fades -- distinct from idiosyncratic volatility (level of variance) despite "
                   "some conceptual overlap.",
        typical_holding_period="Monthly rebalance",
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Well-cited, robust behavioral finding, purely price-data-based, and offers a "
                         "genuinely distinct behavioral mechanism (gambling preference) never touched "
                         "by this program.",
        known_weaknesses="Meaningful conceptual overlap with idiosyncratic volatility -- if both were "
                          "eventually implemented, their overlap should be disclosed, not treated as "
                          "two fully independent diversification wins.",
        academic_replication_quality="Well-replicated internationally, part of the broader low-vol/lottery-preference literature.",
        evidence_sufficiency_note="Sufficient.",
        academic_evidence_score=8, expected_robustness_score=7, operational_simplicity_score=8,
        research_value_score=8, data_availability_score=10, implementation_feasibility_score=9,
    ),
    CandidateProfile(
        key="downside_beta",
        name="Downside Beta / Downside Risk",
        authors="Ang, A., Chen, J. and Xing, Y.",
        publication="\"Downside Risk\", The Review of Financial Studies, Vol. 19, No. 4",
        year=2006,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only bottom-decile (lowest downside beta).",
        factor_family="Risk-based (downside-conditional)",
        factor_tags={"risk_based"},
        mechanism="Stocks whose beta to the market is higher specifically during MARKET DOWNTURNS "
                   "(downside beta) command a return premium beyond what ordinary (unconditional) beta "
                   "explains -- investors dislike downside co-movement specifically.",
        typical_holding_period="Monthly rebalance",
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Reasonably well-cited risk-based refinement; purely price-data-based.",
        known_weaknesses="Meaningful mechanical and economic overlap with Betting Against Beta and "
                          "idiosyncratic volatility above -- if more than one risk-based candidate is "
                          "chosen, the marginal diversification value of a second or third is small; "
                          "this module's own diversification scoring already reflects that once one "
                          "risk-based candidate is implemented.",
        academic_replication_quality="Well-cited but a narrower, more specialized literature than plain beta or idio-vol.",
        evidence_sufficiency_note="Sufficient, but lowest priority within the risk-based cluster.",
        academic_evidence_score=7, expected_robustness_score=6, operational_simplicity_score=6,
        research_value_score=5, data_availability_score=10, implementation_feasibility_score=8,
    ),
    CandidateProfile(
        key="overnight_return_anomaly",
        name="Overnight Return Anomaly",
        authors="Lou, D., Polk, C. and Skouras, S. (see also Berkman, Koch, Tuttle and Zhang 2012)",
        publication="\"A Tug of War: Overnight versus Intraday Expected Returns\", Journal of Financial Economics, Vol. 134, No. 1",
        year=2019,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only (decompose daily return into overnight [today's open vs. yesterday's "
                   "close] and intraday [today's close vs. today's open] components; strategy holds "
                   "specifically overnight, based on recent overnight-return persistence).",
        factor_family="Market microstructure / attention-driven",
        factor_tags={"microstructure_overnight"},
        mechanism="Retail order flow concentrates at the open, institutional order flow concentrates "
                   "intraday -- producing a persistent, exploitable split between overnight and "
                   "intraday expected returns that a standard close-to-close return masks entirely.",
        typical_holding_period="Overnight only (enter near close, exit near next open) -- a wholly "
                                "different HOLDING MECHANIC from every existing strategy, which all "
                                "hold across many days.",
        expected_trade_frequency="High -- a new decision every trading day per qualifying symbol",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Needs literally ZERO new data -- Open and Close are already columns in every "
                         "OHLCV pull this program already makes; a genuinely novel mechanism no "
                         "strategy in this program has touched.",
        known_weaknesses="A newer finding (2019) with less multi-decade replication than the classics; "
                          "and unusually SENSITIVE to exactly the fill-timing assumption this platform's "
                          "own Execution Realism Study already flagged as unmodeled (same-day-close "
                          "fills, not realistic next-day-open fills) -- this strategy's entire edge "
                          "lives inside that exact gap, so it should not be seriously evaluated before "
                          "that framework recommendation is addressed.",
        academic_replication_quality="Well-cited, growing literature, but younger and less battle-tested than the pre-2000 classics in this roadmap.",
        evidence_sufficiency_note="Sufficient to research, but implementation should wait for (or explicitly "
                                   "caveat around) the Execution Realism Study's realistic-fill recommendation.",
        academic_evidence_score=8, expected_robustness_score=6, operational_simplicity_score=9,
        research_value_score=8, data_availability_score=10, implementation_feasibility_score=8,
    ),
    CandidateProfile(
        key="industry_momentum",
        name="Industry Momentum",
        authors="Moskowitz, T.J. and Grinblatt, M.",
        publication="\"Do Industries Explain Momentum?\", The Journal of Finance, Vol. 54, No. 4",
        year=1999,
        asset_class="Industry/sector groups (equities aggregated), cross-sectional",
        direction="Long-only top-decile industries by trailing return, held via their constituent stocks.",
        factor_family="Momentum (industry-level, not stock-level)",
        factor_tags={"momentum_cross_sectional"},
        mechanism="Momentum in individual stock returns is argued to be substantially an INDUSTRY-level "
                   "effect -- buying stocks in recently-strong industries, rather than recently-strong "
                   "individual stocks, captures most of the same premium with different turnover/risk.",
        typical_holding_period="Monthly rebalance",
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history", "sector_classification"],
        known_strengths="Well-cited; reuses the existing sector-map infrastructure already built for Turtle's correlation-group caps.",
        known_weaknesses="Same broad momentum family already represented twice in this portfolio "
                          "(SW-003, SW-006) -- the diversification benefit of a THIRD momentum-family "
                          "candidate is genuinely limited unless the industry-level mechanism turns out "
                          "to behave very differently in practice, which isn't guaranteed. Also, this "
                          "program's sector map is a coarse NSE-sector proxy, not the finer Fama-French-"
                          "style industry classification the original paper uses -- a disclosed adaptation.",
        academic_replication_quality="Well-replicated, foundational momentum-decomposition paper.",
        evidence_sufficiency_note="Sufficient evidence, but weak case for research priority given existing family overlap -- see diversification score.",
        academic_evidence_score=7, expected_robustness_score=6, operational_simplicity_score=6,
        research_value_score=4, data_availability_score=8, implementation_feasibility_score=7,
    ),
    CandidateProfile(
        key="turn_of_month",
        name="Turn-of-the-Month Effect",
        authors="Ariel, R.A.",
        publication="\"A Monthly Effect in Stock Returns\", Journal of Financial Economics, Vol. 18, No. 1",
        year=1987,
        asset_class="Broad market / single-stock equities, calendar-based",
        direction="Long-only, held only across the turn-of-month window.",
        factor_family="Calendar seasonality",
        factor_tags={"seasonality_calendar"},
        mechanism="Returns are disproportionately concentrated in the few trading days around each "
                   "month's turn (last day of the month through the first few days of the next) -- "
                   "originally linked to institutional cash-flow/payroll-driven buying patterns.",
        typical_holding_period="A few days per month, in and out",
        expected_trade_frequency="High-frequency by calendar (every month), but each holding period is very short",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Trivial to test -- pure calendar logic on data already in hand, essentially free to check.",
        known_weaknesses="A textbook example of a classic seasonal anomaly that has PARTIALLY DECAYED "
                          "since discovery as it became widely known and arbitraged in liquid developed "
                          "markets -- real uncertainty about whether it still holds in the recent-period "
                          "check this platform requires. Low economic magnitude even where it does hold.",
        academic_replication_quality="Well-documented historically; more recent samples show materially weaker effects.",
        evidence_sufficiency_note="Sufficient to test cheaply, but expect it may fail the recent-period gate.",
        academic_evidence_score=6, expected_robustness_score=4, operational_simplicity_score=9,
        research_value_score=4, data_availability_score=10, implementation_feasibility_score=10,
    ),
    CandidateProfile(
        key="turn_of_year",
        name="Turn-of-the-Year / January Effect",
        authors="Keim, D.B.",
        publication="\"Size-Related Anomalies and Stock Return Seasonality: Further Empirical Evidence\", Journal of Financial Economics, Vol. 12, No. 1",
        year=1983,
        asset_class="Single-stock equities (small-cap concentrated), calendar-based",
        direction="Long-only, held only across the year-turn window, small-cap tilted.",
        factor_family="Calendar seasonality",
        factor_tags={"seasonality_calendar"},
        mechanism="Small-cap stocks show abnormally strong returns in the first days of January, "
                   "historically linked to December tax-loss-selling pressure unwinding.",
        typical_holding_period="A few days per year, in and out",
        expected_trade_frequency="Very low -- once a year by construction",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Trivial to test, essentially free.",
        known_weaknesses="One of the most widely cited examples of an anomaly LARGELY ARBITRAGED AWAY "
                          "after publication; also India's tax year (April-March) and capital-gains "
                          "tax-loss-selling incentives don't map cleanly onto a US January-specific "
                          "mechanism, a real transferability concern beyond the usual disclosed "
                          "adaptations. Once-a-year trade frequency makes statistical significance hard "
                          "to establish even over a full 10-year backtest (only ~10 independent events).",
        academic_replication_quality="Historically well-documented; widely considered decayed/arbitraged in modern markets, "
                                      "and the underlying tax-calendar mechanism is US-specific.",
        evidence_sufficiency_note="Weak -- both because of documented decay and low event count over the available history.",
        academic_evidence_score=6, expected_robustness_score=3, operational_simplicity_score=9,
        research_value_score=3, data_availability_score=10, implementation_feasibility_score=8,
    ),
    CandidateProfile(
        key="day_of_week",
        name="Day-of-the-Week (Weekend) Effect",
        authors="French, K.R.",
        publication="\"Stock Returns and the Weekend Effect\", Journal of Financial Economics, Vol. 8, No. 1",
        year=1980,
        asset_class="Broad market, calendar-based",
        direction="Long-only, avoid/short-window around specific weekdays.",
        factor_family="Calendar seasonality",
        factor_tags={"seasonality_calendar"},
        mechanism="Average returns differ systematically by day of the week (historically, negative "
                   "Monday returns) -- one of the earliest documented market-efficiency anomalies.",
        typical_holding_period="Single day",
        expected_trade_frequency="Very high (daily), but with a very small expected per-trade edge",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Trivial, free to test.",
        known_weaknesses="Widely considered the MOST decayed of the classic seasonal anomalies -- most "
                          "recent literature finds it has essentially disappeared in modern liquid "
                          "markets. Very small per-trade edge means transaction costs (not yet modeled "
                          "in this platform's own backtesting engine) would very plausibly erase any "
                          "measured effect entirely.",
        academic_replication_quality="Historically documented; modern replications largely fail to find a economically "
                                      "meaningful effect. The weakest evidentiary case in this entire roadmap.",
        evidence_sufficiency_note="Insufficient to justify real research time on its own -- listed for completeness "
                                   "and as a near-free robustness sanity-check, not a genuine priority.",
        academic_evidence_score=5, expected_robustness_score=2, operational_simplicity_score=9,
        research_value_score=2, data_availability_score=10, implementation_feasibility_score=10,
    ),
    # --- NOT_CURRENTLY_IMPLEMENTABLE candidates below: real, well-cited
    # published strategies, catalogued so the roadmap is complete and so
    # future dataset decisions have a concrete target list -- but not
    # scored into the researchable-now ranking (see build_roadmap()).
    CandidateProfile(
        key="value_earnings_yield",
        name="Value (Earnings Yield / Book-to-Market)",
        authors="Basu, S.; Fama, E.F. and French, K.R.; Rosenberg, B., Reid, K. and Lanstein, R.",
        publication="Basu (1977) Journal of Finance; Fama-French (1992/1993) Journal of Finance / "
                    "Journal of Financial Economics; Rosenberg-Reid-Lanstein (1985) Journal of Portfolio Management",
        year=1977,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only top-decile (cheapest by E/P or B/M) as a first, disclosed reduction.",
        factor_family="Value",
        factor_tags={"value"},
        mechanism="Stocks cheap relative to fundamentals (earnings, book value) earn a persistent "
                   "premium -- one of the two original Fama-French factors, arguably the most famous "
                   "anomaly in all of empirical asset pricing.",
        typical_holding_period="Annual to semi-annual rebalance (fundamentals update slowly)",
        expected_trade_frequency="Low",
        data_requirements=["daily_ohlcv_history", "point_in_time_fundamentals_history"],
        known_strengths="The single most foundational, most-replicated anomaly in the academic literature.",
        known_weaknesses="Requires point-in-time (as-then-reported) historical earnings/book-value data "
                          "at every formation date across ~10 years -- exactly the gap PEAD (SW-007) was "
                          "already deferred for.",
        academic_replication_quality="Maximal -- the founding anomaly of factor investing, replicated globally for 40+ years.",
        evidence_sufficiency_note="Overwhelming evidence exists in the literature; the blocker is entirely "
                                   "this platform's own data access, not the strategy's credibility.",
        academic_evidence_score=10, expected_robustness_score=8, operational_simplicity_score=5,
        research_value_score=9, data_availability_score=2, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="quality_composite",
        name="Quality (Piotroski F-Score / Novy-Marx Gross Profitability / QMJ)",
        authors="Piotroski, J.D.; Novy-Marx, R.; Asness, C.S., Frazzini, A. and Pedersen, L.H.",
        publication="Piotroski (2000) Journal of Accounting Research; Novy-Marx (2013) Journal of "
                    "Financial Economics; Asness-Frazzini-Pedersen (working paper 2013, published Review "
                    "of Accounting Studies 2019)",
        year=2000,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only top-decile by a quality composite (profitability, growth stability, low leverage, payout).",
        factor_family="Quality / profitability",
        factor_tags={"quality"},
        mechanism="Fundamentally strong, high-quality companies (profitable, low leverage, stable "
                   "earnings) are systematically underpriced relative to weaker peers -- 'quality' as a "
                   "return factor distinct from and complementary to value.",
        typical_holding_period="Annual rebalance",
        expected_trade_frequency="Low",
        data_requirements=["daily_ohlcv_history", "point_in_time_fundamentals_history"],
        known_strengths="Three independently well-cited formulations (F-Score, gross profitability, "
                         "QMJ) all point the same direction -- unusually convergent evidence.",
        known_weaknesses="Same point-in-time fundamentals-history gap as value above; F-Score "
                          "specifically needs multiple YEAR-OVER-YEAR fundamental comparisons per "
                          "signal, an even deeper history requirement than a single-point value ratio.",
        academic_replication_quality="Extensively replicated, convergent evidence across three independent research lineages.",
        evidence_sufficiency_note="Overwhelming; blocked purely by data access.",
        academic_evidence_score=9, expected_robustness_score=8, operational_simplicity_score=4,
        research_value_score=8, data_availability_score=2, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="accruals_anomaly",
        name="Accruals Anomaly",
        authors="Sloan, R.G.",
        publication="\"Do Stock Prices Fully Reflect Information in Accruals and Cash Flows about Future Earnings?\", The Accounting Review, Vol. 71, No. 3",
        year=1996,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only bottom-decile (lowest accruals, i.e. earnings backed by real cash flow).",
        factor_family="Quality / earnings-quality",
        factor_tags={"quality"},
        mechanism="Firms with high accruals (earnings driven more by accounting adjustments than cash "
                   "flow) subsequently underperform -- investors naively over-weight reported earnings "
                   "without adjusting for their lower cash-flow backing.",
        typical_holding_period="Annual rebalance",
        expected_trade_frequency="Low",
        data_requirements=["daily_ohlcv_history", "point_in_time_fundamentals_history"],
        known_strengths="Foundational earnings-quality anomaly, extremely well cited in accounting/finance.",
        known_weaknesses="Needs historical balance-sheet AND cash-flow-statement line items to compute "
                          "accruals at each point in time -- same fundamentals-history gap as value/quality above.",
        academic_replication_quality="Extensively replicated, foundational to the earnings-quality literature.",
        evidence_sufficiency_note="Overwhelming; blocked purely by data access.",
        academic_evidence_score=8, expected_robustness_score=7, operational_simplicity_score=4,
        research_value_score=6, data_availability_score=2, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="asset_growth_anomaly",
        name="Asset Growth Anomaly",
        authors="Cooper, M.J., Gulen, H. and Schill, M.J.",
        publication="\"Asset Growth and the Cross-Section of Stock Returns\", The Journal of Finance, Vol. 63, No. 4",
        year=2008,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only bottom-decile (lowest year-over-year total-asset growth).",
        factor_family="Investment factor",
        factor_tags={"quality"},
        mechanism="Companies that grow their asset base aggressively subsequently underperform -- "
                   "consistent with over-investment/empire-building or market over-extrapolation of "
                   "growth, and the basis of Fama-French's own later 'investment' (CMA) factor.",
        typical_holding_period="Annual rebalance",
        expected_trade_frequency="Low",
        data_requirements=["daily_ohlcv_history", "point_in_time_fundamentals_history"],
        known_strengths="Well-cited, later formalized into the Fama-French 5-factor model's CMA factor.",
        known_weaknesses="Needs historical balance-sheet total-asset figures -- same fundamentals-history gap.",
        academic_replication_quality="Well-replicated, now a standard factor-model component.",
        evidence_sufficiency_note="Sufficient; blocked purely by data access.",
        academic_evidence_score=8, expected_robustness_score=7, operational_simplicity_score=5,
        research_value_score=6, data_availability_score=2, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="analyst_revision_momentum",
        name="Analyst Earnings-Revision Momentum",
        authors="Womack, K.L.",
        publication="\"Do Brokerage Analysts' Recommendations Have Investment Value?\", The Journal of Finance, Vol. 51, No. 1",
        year=1996,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only top-decile (most positive recent analyst estimate revisions).",
        factor_family="Analyst-information momentum",
        factor_tags={"analyst_information"},
        mechanism="Stock prices underreact to analyst upgrades/estimate revisions, producing predictable "
                   "drift in the direction of the revision -- conceptually adjacent to PEAD but driven "
                   "by analyst forecasts rather than the earnings announcement itself.",
        typical_holding_period="1-3 months",
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history", "analyst_estimates_history"],
        known_strengths="Well-cited; would complement PEAD (SW-007) if both became feasible together.",
        known_weaknesses="Needs a historical analyst-consensus-estimate database -- confirmed absent "
                          "during the same 2026-08-05 PEAD investigation that found no such source "
                          "integrated anywhere in this program.",
        academic_replication_quality="Well-replicated in developed markets with analyst coverage depth; less-tested in NSE-specific coverage conditions.",
        evidence_sufficiency_note="Sufficient in the literature; blocked purely by data access.",
        academic_evidence_score=7, expected_robustness_score=6, operational_simplicity_score=5,
        research_value_score=6, data_availability_score=1, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="short_interest_anomaly",
        name="Short Interest Anomaly",
        authors="Asquith, P., Pathak, P.A. and Ritter, J.R.",
        publication="\"Short Interest, Institutional Ownership, and Stock Returns\", Journal of Financial Economics, Vol. 78, No. 2",
        year=2005,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only avoid/underweight heavily-shorted names, or long-short in the original.",
        factor_family="Informed-trading / short-interest signal",
        factor_tags={"short_interest"},
        mechanism="Heavily shorted stocks subsequently underperform -- short sellers are, on average, "
                   "informed, so aggregate short interest is itself a predictive signal.",
        typical_holding_period="Monthly rebalance",
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history", "short_interest_borrow_availability"],
        known_strengths="Well-cited; a genuinely different information source (positioning, not price/fundamentals).",
        known_weaknesses="Needs short-interest data, which doesn't exist in this platform's pipeline, "
                          "AND presupposes NSE short-selling/SLB infrastructure this program has "
                          "disclosed as absent for every other strategy already.",
        academic_replication_quality="Well-replicated in US markets with mandated short-interest disclosure; NSE disclosure regime differs.",
        evidence_sufficiency_note="Sufficient in the literature; blocked by data AND execution infrastructure, a double gap.",
        academic_evidence_score=7, expected_robustness_score=6, operational_simplicity_score=4,
        research_value_score=5, data_availability_score=0, implementation_feasibility_score=0,
    ),
    CandidateProfile(
        key="net_issuance_buybacks",
        name="Net Share Issuance / Buyback Anomaly",
        authors="Ikenberry, D., Lakonishok, J. and Vermaelen, T.; Pontiff, J. and Woodgate, A.",
        publication="Ikenberry-Lakonishok-Vermaelen (1995) Journal of Financial Economics; Pontiff-Woodgate (2008) The Journal of Finance",
        year=1995,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only top-decile (net repurchasers / lowest net share issuance).",
        factor_family="Corporate-action-driven",
        factor_tags={"corporate_actions"},
        mechanism="Firms that repurchase shares subsequently outperform, firms that issue heavily "
                   "subsequently underperform -- interpreted as management exploiting private "
                   "information about relative mispricing via the issuance/buyback decision itself.",
        typical_holding_period="Multi-month to annual",
        expected_trade_frequency="Low",
        data_requirements=["daily_ohlcv_history", "corporate_actions_buyback_history"],
        known_strengths="Well-cited, economically intuitive (management-information) mechanism.",
        known_weaknesses="Needs a historical corporate-actions/buyback-announcement dataset this "
                          "platform doesn't have; a shares-outstanding-CHANGE history (not just a "
                          "snapshot) would be the minimum viable proxy and isn't available either.",
        academic_replication_quality="Well-replicated in US/developed markets.",
        evidence_sufficiency_note="Sufficient in the literature; blocked purely by data access.",
        academic_evidence_score=7, expected_robustness_score=6, operational_simplicity_score=5,
        research_value_score=5, data_availability_score=1, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="insider_trading_anomaly",
        name="Insider Trading Anomaly",
        authors="Seyhun, H.N.",
        publication="\"Insiders' Profits, Costs of Trading, and Market Efficiency\", Journal of Financial Economics, Vol. 16, No. 2",
        year=1986,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only (stocks with recent net insider BUYING).",
        factor_family="Informed-trading signal",
        factor_tags={"insider_information"},
        mechanism="Corporate insiders' own trades predict subsequent returns in the same direction -- "
                   "insiders are informed about their own company's prospects.",
        typical_holding_period="1-6 months following a disclosed insider transaction",
        expected_trade_frequency="Low, event-driven",
        data_requirements=["daily_ohlcv_history", "insider_transaction_data"],
        known_strengths="Well-cited, intuitive mechanism; unlike several other blocked candidates, the "
                         "underlying disclosures (NSE SAST filings) are PUBLIC, unlike e.g. analyst "
                         "consensus data which no free source publishes at all -- see dataset "
                         "recommendations below, this is comparatively the cheapest gap to close.",
        known_weaknesses="Nothing in this program currently scrapes or stores NSE insider-disclosure filings.",
        academic_replication_quality="Well-replicated in US markets; India-specific replication evidence is thinner.",
        evidence_sufficiency_note="Sufficient in the literature generally; India-specific evidence would be worth "
                                   "a literature check before committing, once the data gap is closed.",
        academic_evidence_score=7, expected_robustness_score=5, operational_simplicity_score=5,
        research_value_score=6, data_availability_score=1, implementation_feasibility_score=1,
    ),
    CandidateProfile(
        key="pairs_trading_stat_arb",
        name="Pairs Trading / Statistical Arbitrage",
        authors="Gatev, E., Goetzmann, W.N. and Rouwenhorst, K.G.",
        publication="\"Pairs Trading: Performance of a Relative-Value Arbitrage Rule\", The Review of Financial Studies, Vol. 19, No. 3",
        year=2006,
        asset_class="Single-stock equities, relative-value (paired long/short)",
        direction="Genuinely LONG-SHORT by construction (long the underperforming leg of a "
                   "cointegrated pair, short the outperforming leg) -- there is no meaningful "
                   "long-only adaptation, unlike every other candidate in this roadmap.",
        factor_family="Statistical arbitrage / relative value",
        factor_tags={"stat_arb_pairs"},
        mechanism="Two historically co-moving stocks (same industry/business model) that diverge in "
                   "price are traded on the expectation their spread reverts -- market-neutral by "
                   "construction, a structurally different approach from every cross-sectional-factor "
                   "candidate elsewhere in this roadmap.",
        typical_holding_period="Days to weeks per pair-divergence event",
        expected_trade_frequency="Moderate, event-driven per pair",
        data_requirements=["daily_ohlcv_history", "short_interest_borrow_availability"],
        known_strengths="Structurally market-neutral -- would be a genuinely different RISK PROFILE "
                         "from every existing directional strategy, not just a different signal.",
        known_weaknesses="Requires an actual short leg to function as designed; without NSE SLB "
                          "infrastructure, there is no faithful long-only adaptation the way there is "
                          "for a cross-sectional decile-sort strategy -- this is a harder blocker than "
                          "most other candidates, which merely lose half their spread when long-only'd.",
        academic_replication_quality="Well-replicated, though returns have compressed since the strategy became widely known/crowded.",
        evidence_sufficiency_note="Sufficient in the literature; blocked by execution infrastructure (short-selling), not data per se.",
        academic_evidence_score=7, expected_robustness_score=5, operational_simplicity_score=3,
        research_value_score=6, data_availability_score=0, implementation_feasibility_score=0,
    ),
    CandidateProfile(
        key="post_ipo_underperformance",
        name="Post-IPO Long-Run Underperformance",
        authors="Ritter, J.R.",
        publication="\"The Long-Run Performance of Initial Public Offerings\", The Journal of Finance, Vol. 46, No. 1",
        year=1991,
        asset_class="Single-stock equities, event-driven cross-sectional",
        direction="Short/avoid recent IPOs (or long-only inverse: avoid names within N years of listing).",
        factor_family="Event-driven / IPO anomaly",
        factor_tags={"ipo_event"},
        mechanism="Newly public companies systematically underperform matched peers over the 3-5 years "
                   "following their IPO, attributed to window-dressing at issuance and overoptimistic "
                   "initial pricing.",
        typical_holding_period="Avoid/underweight for 3-5 years post-listing",
        expected_trade_frequency="Low, event-driven",
        data_requirements=["daily_ohlcv_history", "ipo_date_history", "index_membership_history"],
        known_strengths="Well-cited, intuitive mechanism.",
        known_weaknesses="Needs an IPO-date history AND would require testing against stocks NOT "
                          "currently in the frozen Nifty 500 snapshot (many post-IPO underperformers "
                          "never join a large-cap index at all) -- this platform's universe.py is "
                          "explicitly current-constituents-only, a second, compounding data gap beyond "
                          "just IPO dates.",
        academic_replication_quality="Well-replicated in US/international IPO markets.",
        evidence_sufficiency_note="Sufficient in the literature; blocked by two independent data gaps (IPO dates + a broader, historical universe).",
        academic_evidence_score=7, expected_robustness_score=6, operational_simplicity_score=4,
        research_value_score=5, data_availability_score=0, implementation_feasibility_score=0,
    ),
    CandidateProfile(
        key="options_volatility_premia",
        name="Options-Based Volatility Risk Premium",
        authors="Various -- see e.g. Carr, P. and Wu, L., \"Variance Risk Premia\", Review of Financial Studies (2009)",
        publication="Variance Risk Premia literature (multiple peer-reviewed papers, no single canonical source)",
        year=2009,
        asset_class="Index/single-stock options",
        direction="Structurally long-short via option positions (e.g. short variance) -- not a cash-equity strategy at all.",
        factor_family="Volatility risk premium",
        factor_tags={"options_volatility"},
        mechanism="Implied volatility priced into options systematically exceeds subsequently realized "
                   "volatility, a persistent risk premium collectible by systematically selling options/variance.",
        typical_holding_period="Weekly to monthly (options expiry-driven)",
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history", "options_data"],
        known_strengths="A wholly distinct mechanism/instrument family from everything else in this roadmap.",
        known_weaknesses="No options data source integrated anywhere in this program, and this "
                          "platform's entire execution/risk/portfolio stack (execution/, risk/, "
                          "portfolio/) is built for cash equities only -- this would be a new asset "
                          "class for the whole platform, not just a new signal.",
        academic_replication_quality="Well-established literature, but represents a different asset class than this platform trades at all.",
        evidence_sufficiency_note="Sufficient in the literature; out of scope until/unless the platform decides to trade derivatives at all.",
        academic_evidence_score=7, expected_robustness_score=6, operational_simplicity_score=2,
        research_value_score=4, data_availability_score=0, implementation_feasibility_score=0,
    ),
]


# =====================================================================
# Permanently excluded -- real published strategies (not the categorical
# YouTube/Reddit/black-box exclusion, which never gets a CandidateProfile
# entry at all) that this program has a SPECIFIC, standing reason never to
# spend research time on, independent of data availability.
# =====================================================================
PERMANENTLY_EXCLUDED = [
    {
        "name": "Moving-Average Crossover variants",
        "reason": "Same mechanism family as SW-004 (MA Crossover), which received a formal REJECT "
                   "verdict. No new economic rationale has been identified that would change the "
                   "underlying temporal-robustness failure -- re-testing a parameter variant of an "
                   "already-REJECTed mechanism is not a good use of research time.",
    },
    {
        "name": "RSI / Bollinger-Band mean-reversion variants",
        "reason": "Same mechanism family as SW-005 (Mean Reversion), which received a formal REJECT "
                   "verdict, for the same reason as MA Crossover variants above.",
    },
    {
        "name": "Turtle Trading -- System 1",
        "reason": "A documented fast-follow variant of SW-001 (Turtle System 2, REJECT), differing "
                   "only by a whipsaw filter on the prior signal's outcome. SW-001's REJECT was driven "
                   "by a structural temporal-robustness failure (worked over 10 years, stopped working "
                   "in the most recent period), not by a parameter this filter would change -- low "
                   "expected value for the research time required. Kept out of the ranked roadmap, not "
                   "deleted from consideration entirely, should the roadmap ever run short of fresher ideas.",
    },
    {
        "name": "Any commercial/black-box signal, YouTube strategy, Reddit strategy, or unverified blog strategy",
        "reason": "Categorically outside this program's research universe per explicit standing "
                   "direction (2026-08-12) -- never evaluated, never added as a CandidateProfile at all.",
    },
]


def load_portfolio(registry_path: str = REGISTRY_PATH) -> list:
    """Read-only portfolio-awareness -- never writes to the registry."""
    return list_strategies(registry_path)


def build_roadmap(registry_path: str = REGISTRY_PATH, weights: dict = DEFAULT_WEIGHTS) -> dict:
    """
    Scores every CANDIDATE against the LIVE deployment registry (so
    diversification scoring always reflects the platform's actual current
    state, not a stale snapshot) and splits the result into:
      researchable_now -- ranked descending by total_score, everything
                           classify_data_feasibility() didn't block.
      deferred_pending_data -- NOT_CURRENTLY_IMPLEMENTABLE candidates,
                           unranked (their score isn't a meaningful
                           priority signal since they can't be started).
      all_scored -- every ScoredCandidate, for the full comparison table.
    """
    portfolio = load_portfolio(registry_path)
    scored = [score_candidate(c, portfolio, weights) for c in CANDIDATES]
    researchable_now = sorted(
        (s for s in scored if s.feasibility_classification != "NOT_CURRENTLY_IMPLEMENTABLE"),
        key=lambda s: -s.total_score,
    )
    deferred_pending_data = [s for s in scored if s.feasibility_classification == "NOT_CURRENTLY_IMPLEMENTABLE"]
    return {
        "portfolio": portfolio, "researchable_now": researchable_now,
        "deferred_pending_data": deferred_pending_data, "all_scored": scored, "weights": weights,
    }


DATASET_RECOMMENDATIONS = [
    {
        "dataset": "Point-in-time (as-reported, not restated) historical fundamentals for NSE-listed "
                   "companies, ~10 years, quarterly",
        "unlocks": "Value, Quality (F-Score/Gross Profitability/QMJ), Accruals, and Asset Growth -- the "
                    "single largest blocked bucket in this roadmap (4 candidates, arguably the most "
                    "famous anomalies in the academic literature).",
        "notes": "The generalization of the exact gap already identified during PEAD's (SW-007) "
                 "deferral -- a paid vendor (e.g. a Screener.in/Trendlyne/Tijori Finance bulk export, "
                 "or Refinitiv/Bloomberg) would very likely unlock this AND PEAD simultaneously.",
    },
    {
        "dataset": "Historical analyst consensus-estimate data (I/B/E/S-style)",
        "unlocks": "PEAD's SUE construction (SW-007) and Analyst Earnings-Revision Momentum.",
        "notes": "A narrower, more specialized (and typically more expensive) data category than plain fundamentals.",
    },
    {
        "dataset": "NSE insider-trading (SAST) disclosure history",
        "unlocks": "Insider Trading Anomaly.",
        "notes": "Comparatively the CHEAPEST gap to close of the blocked candidates -- the underlying "
                 "filings are already public; this would be a scraping/integration project rather than "
                 "a paid-vendor purchase.",
    },
    {
        "dataset": "Securities lending/borrow availability + a genuine short-selling execution path",
        "unlocks": "The full documented spread of every risk-based/momentum/reversal candidate already "
                   "implemented or proposed (all currently long-only by disclosed necessity), plus "
                   "Pairs Trading / Statistical Arbitrage outright.",
        "notes": "An execution/infrastructure investment, not just a data one -- the largest lift on this list.",
    },
    {
        "dataset": "NSE F&O historical options-chain data",
        "unlocks": "Options-Based Volatility Risk Premium strategies.",
        "notes": "Would introduce an entirely new asset class to the platform (options), not just a new signal "
                 "within cash equities -- a bigger scope decision than a typical dataset purchase.",
    },
    {
        "dataset": "Historical index-membership dates (not just current constituents) + a broader "
                   "point-in-time universe (including delisted/since-removed names)",
        "unlocks": "Post-IPO Long-Run Underperformance, and removes the survivorship-bias caveat "
                    "already disclosed in swing_research/universe.py for every existing and future strategy.",
        "notes": "Also strengthens every OTHER strategy's evidence quality, not just IPO-specific research.",
    },
]


def render_roadmap_markdown(roadmap: dict, top_n: int = 20) -> str:
    """Renders the full Head of Research report -- ranked roadmap, full
    comparison table, recommended order, deferred/excluded lists, and
    dataset recommendations -- as markdown, matching this program's
    existing strategy_library/ doc style."""
    lines = []
    w = roadmap["weights"]

    lines.append("# Swing Research Program -- Head of Research Roadmap\n")
    lines.append(
        "Maintained by `swing_research/research_roadmap.py` (Published Research Analyst's roadmap "
        "extension). Regenerate with `python run_research_roadmap.py` any time the portfolio changes "
        "-- diversification scoring reads the LIVE deployment registry, never a stale snapshot.\n"
    )
    lines.append(
        "**Research universe restriction (standing, 2026-08-12):** only peer-reviewed academic papers, "
        "well-known quantitative finance research, and widely accepted trading books with substantial "
        "historical validation. No YouTube/Reddit/social-media strategies, no commercial black-box "
        "systems, no unverified blogs -- these are never catalogued here at all, not scored-and-rejected.\n"
    )

    lines.append("## Current Portfolio State (live, from the deployment registry)\n")
    lines.append("| Strategy | ID | Research Verdict | Deployment Status |")
    lines.append("|---|---|---|---|")
    for rec in sorted(roadmap["portfolio"], key=lambda r: r.strategy_id):
        lines.append(f"| {rec.display_name} | {rec.strategy_id} | {rec.research_verdict.value} | {rec.deployment_status.value} |")
    lines.append("")

    lines.append("## Scoring Methodology\n")
    lines.append("Weighted 0-10 axes, summing to a 0-10 total score:\n")
    lines.append("| Axis | Weight |")
    lines.append("|---|---|")
    for k, v in w.items():
        lines.append(f"| {k.replace('_', ' ').title()} | {v:.0%} |")
    lines.append(
        "\nDiversification is scored dynamically against the live portfolio above (a strategy sharing "
        "a factor family with something already PASS+PAPER_TRADING costs far more diversification "
        "credit than one sharing a family with something REJECTed/ARCHIVED). Data availability and "
        "implementation feasibility are gated by `classify_data_feasibility()` -- any candidate needing "
        "data this platform doesn't have is moved out of the ranked roadmap entirely into 'Deferred "
        "Pending Better Data' below, regardless of how well it would otherwise score.\n"
    )

    researchable = roadmap["researchable_now"][:top_n]
    lines.append(f"## Ranked Research Roadmap (Top {len(researchable)})\n")
    lines.append("| Rank | Strategy | Author(s), Year | Factor Family | Total Score | Diversification |")
    lines.append("|---|---|---|---|---|---|")
    for i, s in enumerate(researchable, 1):
        c = s.candidate
        lines.append(f"| {i} | {c.name} | {c.authors.split(',')[0].split(' and ')[0]}, {c.year} | "
                      f"{c.factor_family} | {s.total_score}/10 | {s.diversification_score}/10 |")
    lines.append("")

    lines.append("## Full Comparison Table (every candidate, every score)\n")
    lines.append("| Strategy | Feasibility | Evidence | Data Avail. | Feasibility Score | "
                  "Diversification | Robustness | Simplicity | Research Value | Total |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in sorted(roadmap["all_scored"], key=lambda s: -s.total_score):
        c = s.candidate
        a = s.axis_scores
        total_display = f"{s.total_score}/10" if s.feasibility_classification != "NOT_CURRENTLY_IMPLEMENTABLE" else "N/A (blocked)"
        lines.append(
            f"| {c.name} | {s.feasibility_classification} | {a['academic_evidence']}/10 | "
            f"{a['data_availability']}/10 | {a['implementation_feasibility']}/10 | "
            f"{s.diversification_score}/10 | {a['expected_robustness']}/10 | "
            f"{a['operational_simplicity']}/10 | {a['research_value']}/10 | {total_display} |"
        )
    lines.append("")

    lines.append("## Recommended Research Order (top 5, with rationale)\n")
    for i, s in enumerate(researchable[:5], 1):
        c = s.candidate
        lines.append(f"### {i}. {c.name} ({c.authors}, {c.year})\n")
        lines.append(f"**Why this:** {c.known_strengths}\n")
        if s.diversification_overlap_notes:
            lines.append(f"**Portfolio overlap:** {'; '.join(s.diversification_overlap_notes)}\n")
        else:
            lines.append("**Portfolio overlap:** None -- no existing strategy shares this factor family.\n")
        lines.append(f"**Known risk:** {c.known_weaknesses}\n")
        lines.append("")
    if len(researchable) > 5:
        lines.append("**Why not the rest of the top 20:** lower total score, driven variously by "
                      "family overlap with existing strategies (e.g. Industry Momentum vs. SW-003/SW-006), "
                      "documented historical decay (the calendar-seasonality cluster), or a thinner "
                      "academic replication record than the candidates above -- see the full comparison "
                      "table for the exact scores behind each.\n")

    lines.append("## Deferred Pending Better Data\n")
    lines.append("Real, well-cited published strategies this platform cannot yet implement faithfully "
                  "-- not excluded, just blocked on data this program doesn't have today. See Dataset "
                  "Recommendations below for what would unlock each.\n")
    for s in sorted(roadmap["deferred_pending_data"], key=lambda s: s.candidate.name):
        c = s.candidate
        lines.append(f"- **{c.name}** ({c.authors}, {c.year}) -- {'; '.join(s.feasibility_reasons)}")
    lines.append("")

    lines.append("## Permanently Excluded\n")
    for item in PERMANENTLY_EXCLUDED:
        lines.append(f"- **{item['name']}** -- {item['reason']}")
    lines.append("")

    lines.append("## Future Dataset Recommendations\n")
    for d in DATASET_RECOMMENDATIONS:
        lines.append(f"### {d['dataset']}\n")
        lines.append(f"**Unlocks:** {d['unlocks']}\n")
        lines.append(f"**Notes:** {d['notes']}\n")

    return "\n".join(lines)


ROADMAP_PATH = os.path.join(os.path.dirname(__file__), "RESEARCH_ROADMAP.md")
