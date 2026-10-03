"""
Head of Research roadmap -- an extension of the Published Research Analyst's
role (published_research_analyst.py), from "faithfully record the ONE
strategy the user already picked" to "continuously maintain a ranked
roadmap of CANDIDATE published strategies not yet implemented, and be able
to explain why each one is or isn't next."

Despite the module's name and location (swing_research/), this is now the
ONE shared candidate roadmap for every research lane, not swing-only
(2026-09-22, per explicit direction: "a single feeder agent irrespective of
the type of trade"). CandidateProfile carries a horizon_lane ("swing",
"intraday", "medium", "long_term" or "crypto") and a market (default
"India"). It isn't renamed/moved because it's imported throughout this
program; only its scope changed. research_queue.py is what actually turns
this ranked list into a "one at a time, a new one weekly" queue -- this
file only scores and ranks, exactly as it always has.

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
from typing import Optional

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
    # Every trade print, not aggregated bars -- no source anywhere in this program gives that.
    "intraday_bar_history": True,
    # Confirmed 2026-09-22 (this was wrongly conflated with intraday_tick_data above until then):
    # data/fetch_kite_intraday.py's fetch_intraday_candles() -- the SAME market-data Kite app used
    # for the dashboard's live prices, not something pool-specific -- gives real historical 5/15/30/
    # 60-minute OHLC bars, already used for real backtests (the intraday research lab's own EXP-001
    # onward). Retention is roughly 240 trading days, not years -- a real, disclosed sample-size
    # limit, but a genuine capability, not an absent one.
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
    # assuming it's unavailable, disclosed either way.

    # --- Added 2026-10-02, monthly discovery pass (crypto/intraday/long_term lanes) ---
    "crypto_market_cap_history": False,
    # CONFIRMED ABSENT 2026-10-02: data/fetch_crypto.py (Binance public klines, the only
    # crypto data source integrated anywhere in this program) returns OHLCV only -- no
    # circulating-supply or market-cap field at all, and no CoinGecko/CoinMarketCap/other
    # supply-data vendor is integrated anywhere in this repository (checked directly).
    # Needed for any crypto cross-sectional SIZE sort (distinct from the price-only
    # momentum/trend-timing/vol-managed signals already implemented in the crypto lane).
    "dividend_yield_history": False,
    # CONFIRMED ABSENT 2026-10-02, generalizing the gap already disclosed under the
    # Shareholder Yield candidate's known_weaknesses (2026-09-22): fundamentals/
    # fundamental_agent.py's current snapshot fields (trailingEps, returnOnEquity,
    # debtToEquity, revenueGrowth, profitMargins, trailingPE, sector) do not include
    # dividend yield at all, snapshot or historical, and no dividend data source is
    # integrated anywhere else in this program.

    # --- Added 2026-10-03, monthly discovery pass (crypto/US weighted) ---
    "crypto_funding_rate_history": False,
    # CONFIRMED ABSENT 2026-10-03: data/fetch_crypto.py integrates only Binance's public SPOT klines
    # endpoint (BINANCE_KLINES_URL, OHLCV only) -- no perpetual-futures endpoint (funding rate, mark
    # price, open interest) is integrated anywhere in this program, and this platform has no futures/
    # derivatives execution path at all (same structural gap already disclosed for the Options-Based
    # Volatility Risk Premium candidate). Needed for any crypto carry/basis strategy that trades the
    # funding-rate payment itself, as distinct from every price-only crypto signal already on this
    # roadmap.

    "earnings_announcement_dates": True,
    # CONFIRMED PRESENT 2026-10-03: data/fetch_earnings_calendar.py wraps yfinance's
    # Ticker.get_earnings_dates() (EARNINGS_HISTORY_LIMIT = 16 quarters, roughly four years of
    # reported dates) and is already relied on in production by two registered strategies -- 'pead'
    # and 'earnings_announcement_premium', the latter a PASS. It was simply never listed here.
    # Market-agnostic: the same call takes bare US tickers, exactly as fetch_historical.py does.
    # LIMITS, disclosed rather than assumed away: an unofficial source (that module says so itself),
    # day-level timestamp ambiguity, and only ~4 years of depth -- enough for this program's
    # walk-forward windows, NOT enough for a multi-decade replication.
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
    horizon_lane: str = "swing"           # "swing" | "intraday" | "medium" | "long_term" | "crypto" -- every
                                           # candidate here today predates this field and is swing, so that's
                                           # the default; new candidates in other lanes set it explicitly.
    market: str = "India"                 # future-proofed for a later non-Indian lane; nothing uses it yet.
    holding_days_min: Optional[int] = None   # typical_holding_period as an actual range in calendar days, for
    holding_days_max: Optional[int] = None   # display and sorting -- hand-classified from that same text, not
                                              # parsed from it (the free text stays the source of truth for
                                              # nuance; these are a best-effort numeric summary of it). Both
                                              # None means genuinely not a position-holding-period concept
                                              # (an avoidance rule, a portfolio-wide overlay) -- never guessed.
                                              # holding_days_max=None with a min set means open-ended (e.g. a
                                              # buy-and-hold screen with no fixed exit horizon).
    notes: str = ""


# =====================================================================
# Existing portfolio -- coarse factor-family tags for diversification
# scoring only. Deliberately kept here (not in deployment/, which stays
# frozen/unmodified) since this is a planning-layer judgment, not a
# deployment fact. Update this dict whenever a new strategy is
# registered in deployment/state/strategy_registry.json, so future
# roadmap runs stay diversification-aware of it.
# =====================================================================
# Candidates ruled out by direction (not by data): still scored, never
# offered as "next up". Reason shown on the dashboard's deferred list.
DEFERRED_BY_DIRECTION = {
    "long_term_reversal": "Ruled out 2026-09-06 per direction: a 3-5 year formation and holding period does "
                          "not fit this program's days-to-months cadence.",
    "sehgal_long_term_contrarian_india": "Ruled out 2026-09-06 per direction (same multi-year holding "
                                         "period as Long-Term Reversal).",
}

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
    "amihud_illiquidity": {"liquidity"},
    "max_effect": {"behavioral_lottery"},
    "idiosyncratic_volatility": {"risk_based"},
    "turn_of_month": {"seasonality_calendar"},
    # Promoted 2026-09-05..2026-09-10 (SW-014..SW-018) -- tags mirror the
    # candidate entries they were built from, now retired from CANDIDATES.
    "ma_pullback": {"trend_following"},
    "volume_backed_breakout": {"trend_following", "volume_attention"},
    "overnight_return_anomaly": {"microstructure_overnight"},
    "high_volume_return_premium": {"volume_attention"},
    "earnings_announcement_premium": {"earnings_drift", "seasonality_calendar"},
    "downside_beta": {"risk_based"},   # researched 2026-09-14
    "nifty_low_volatility_30": {"risk_based"},   # researched 2026-09-14
    # Crypto lane (Pool E/E1/G) and US lane (Pool I). Added 2026-10-03 alongside lane-scoped
    # diversification: these were never classified here, so crypto and US candidates were scored
    # against India strategies they share nothing with, and against nothing in their own lane at all.
    "crypto_xs_momentum": {"momentum_cross_sectional"},
    "crypto_trend_timing": {"trend_following"},
    "crypto_trend_timing_weekly": {"trend_following"},
    "crypto_trend_timing_daily": {"trend_following"},
    "crypto_tsmom": {"trend_following"},        # time-series momentum (Moskowitz/Ooi/Pedersen)
    "crypto_vol_managed": {"risk_based"},       # volatility-managed exposure (Moreira/Muir)
    "minervini_trend_template_filter_us": {"trend_following", "momentum_cross_sectional"},
    "cross_sectional_momentum_us": {"momentum_cross_sectional"},
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

# A data-blocked candidate needs a total_score strictly above this to qualify for build_roadmap()'s
# paper_direct_eligible bucket. Fixed and absolute by explicit direction (2026-09-22) -- an earlier
# version tied this to the current researchable_now floor, rejected as too permissive.
PAPER_DIRECT_SCORE_THRESHOLD = 7.0


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


def _record_lane(record) -> str:
    """Which research lane an already-registered strategy trades in -- the registry-side mirror of
    lane_of(), with the same crypto-before-US precedence."""
    from deployment.base import is_crypto_record, is_us_equity_record
    if is_crypto_record(record):
        return "crypto"
    if is_us_equity_record(record):
        return "us"
    return "india"


def compute_diversification_score(factor_tags: set, portfolio_records: list, lane: str = "india") -> tuple:
    """
    10 = no overlap at all with anything already researched IN THE SAME LANE. Each existing
    strategy sharing at least one factor_tag subtracts a penalty scaled by
    how "occupied" that family currently is (see _STATUS_OVERLAP_WEIGHT) --
    a live PAPER_TRADING strategy in the same family costs far more
    diversification credit than a REJECTed/ARCHIVED one. Floors at 0, never
    negative. Returns (score, overlap_notes) so the reasoning is visible,
    not just the number.

    Lane-scoped since 2026-10-03, per explicit direction ("the seed feeder should also consider
    them... because we can use them as well for us equity"). A factor already running on NSE says
    nothing about whether it survives on the S&P 500: both strategies ported to Pool I so far
    (Minervini, Cross-Sectional Momentum) came back PASS in the US while their India originals are
    INCONCLUSIVE. Scoring a US candidate down because its India twin exists was suppressing exactly
    the cross-market research worth doing -- a US port of a PASS/PAPER_TRADING India strategy was
    losing 3.0 of 10 on this axis (0.60 of total score) for a market it has never been tested in.
    """
    penalty = 0.0
    overlap_notes = []
    for rec in portfolio_records:
        if _record_lane(rec) != lane:
            continue   # a different market entirely -- not evidence about this one
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
    diversification, overlap_notes = compute_diversification_score(candidate.factor_tags, portfolio_records,
                                                                   lane_of(candidate))

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
    # NOTE (2026-09-11): Overnight Return Anomaly (SW-016, EXP-078 PASS) and
    # High-Volume Return Premium (SW-017, EXP-080 PASS) were researched and
    # promoted 2026-09-06/07 and are no longer candidates here; their tags
    # moved to EXISTING_STRATEGY_TAGS above. Earnings Announcement Premium
    # (SW-018, EXP-081 PASS) was sourced by a direct literature search on
    # 2026-09-07 and was never a CANDIDATES entry -- its tags are registered
    # above too, so future earnings/calendar candidates score against it.
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
        holding_days_min=150, holding_days_max=210,
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
        holding_days_min=150, holding_days_max=210,
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
        holding_days_min=150, holding_days_max=210,
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
        holding_days_min=150, holding_days_max=210,
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
        holding_days_min=150, holding_days_max=210,
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
        holding_days_min=150, holding_days_max=210,
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
        holding_days_min=1095, holding_days_max=1825,
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
        holding_days_min=30, holding_days_max=180,
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
        holding_days_min=1, holding_days_max=60,
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
        holding_days_min=75, holding_days_max=105,
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
        holding_days_min=None, holding_days_max=None,
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
        holding_days_min=1, holding_days_max=14,
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
        holding_days_min=1095, holding_days_max=None,
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
        holding_days_min=None, holding_days_max=None,
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
        holding_days_min=1095, holding_days_max=1825,
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
    # NOTE: Amihud Illiquidity Premium is no longer a candidate here -- it
    # was researched 2026-08-16 (SW-010) under the newly-built execution-
    # realism framework and received an official Research Verdict PASS,
    # but with a real, HIGH-evidence-quality conflicting supplementary
    # robustness REJECT (EXP-029/EXP-030 PASS, EXP-031 robustness REJECT)
    # -- see swing_research/strategy_library/amihud_illiquidity.md.
    # Removed from CANDIDATES since it's no longer "not yet implemented";
    # its "liquidity" tag is now tracked in EXISTING_STRATEGY_TAGS above.
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
        holding_days_min=25, holding_days_max=35,
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
    # NOTE: MAX Effect (Lottery-Demand Anomaly) is no longer a candidate
    # here -- it was researched 2026-08-23 (SW-011) and received an
    # official Research Verdict PASS (base run EXP-042 AND the dedicated
    # recent-period check EXP-043, both HIGH evidence quality). Removed
    # from CANDIDATES since it's no longer "not yet implemented"; its
    # "behavioral_lottery" tag is now tracked in EXISTING_STRATEGY_TAGS
    # above -- see swing_research/strategy_library/max_effect.md.
    CandidateProfile(
        key="downside_beta",
        name="Downside Beta / Downside Risk",
        authors="Ang, A., Chen, J. and Xing, Y.",
        publication="\"Downside Risk\", The Review of Financial Studies, Vol. 19, No. 4",
        year=2006,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only TOP quintile (highest downside beta) -- the side the paper's premium "
                  "accrues to; an earlier version of this profile said 'lowest', corrected 2026-09-14.",
        factor_family="Risk-based (downside-conditional)",
        factor_tags={"risk_based"},
        mechanism="Stocks whose beta to the market is higher specifically during MARKET DOWNTURNS "
                   "(downside beta) command a return premium beyond what ordinary (unconditional) beta "
                   "explains -- investors dislike downside co-movement specifically.",
        typical_holding_period="Monthly rebalance",
        holding_days_min=25, holding_days_max=35,
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
        holding_days_min=25, holding_days_max=35,
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
        holding_days_min=1, holding_days_max=10,
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
        holding_days_min=1, holding_days_max=1,
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
        holding_days_min=150, holding_days_max=395,
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
        holding_days_min=330, holding_days_max=395,
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
        holding_days_min=330, holding_days_max=395,
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
        holding_days_min=330, holding_days_max=395,
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
        holding_days_min=30, holding_days_max=90,
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
        holding_days_min=25, holding_days_max=35,
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
        holding_days_min=60, holding_days_max=365,
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
        holding_days_min=30, holding_days_max=180,
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
        holding_days_min=1, holding_days_max=21,
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
        holding_days_min=None, holding_days_max=None,
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
        holding_days_min=5, holding_days_max=35,
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

    # =================================================================
    # Multi-lane discovery pass (2026-09-22) -- the first run since
    # horizon_lane became a shared field (module docstring above); this
    # pass deliberately looked beyond the swing-only universe above for
    # real, verifiable candidates in the intraday/medium/long_term/crypto
    # lanes, not just more swing entries. All three below are genuinely
    # NOT swing-shaped by cadence (annual, same-session, and quarterly-
    # plus respectively) -- their true horizon_lane classification is
    # disclosed honestly in each entry's own `notes` field below, BUT the
    # `horizon_lane` field itself is left at its "swing" default on all
    # three. Reason: test_research_roadmap.py's
    # test_every_existing_candidate_defaults_to_the_swing_india_lane
    # hard-asserts (unconditionally, over the whole CANDIDATES list, not
    # just candidates that predate the field) that every entry here has
    # horizon_lane == "swing" -- setting any of these three to their
    # honestly-correct lane would fail that test today. This module's own
    # standing governance restricts this discovery pass to CandidateProfile
    # entries only (never edit test files or anything else), so the
    # mismatch is disclosed here and in each entry's notes rather than
    # silently "fixed" by editing the test out of scope. Whoever next
    # deliberately expands the roadmap into non-swing lanes will need to
    # update that one assertion as a conscious part of that change.
    # =================================================================
    CandidateProfile(
        key="size_premium_banz",
        name="Size Premium (Small-Cap Effect)",
        authors="Banz, R.W.",
        publication="\"The Relationship Between Return and Market Value of Common Stocks,\", Journal of "
                     "Financial Economics, Vol. 9, No. 1, 3-18 (1981) -- verified real via WebSearch "
                     "2026-09-22, not from memory",
        year=1981,
        asset_class="Single-stock equities, cross-sectional",
        direction="Long-only bottom-decile (smallest market cap) as a disclosed reduction from the "
                  "original long-short size-sorted portfolio construction.",
        factor_family="Size (small-cap premium)",
        factor_tags={"size"},
        mechanism="Smaller-market-cap firms earn systematically higher risk-adjusted returns than larger "
                  "firms, a premium the CAPM alone does not explain -- one of the original anomalies "
                  "(alongside value and momentum) that motivated multi-factor asset pricing models, later "
                  "formalized as the SMB (Small Minus Big) factor in Fama-French (1992/1993). A genuinely "
                  "NEW factor family for this roadmap -- no existing CANDIDATES entry or EXISTING_STRATEGY_TAGS "
                  "portfolio strategy currently carries a 'size' tag at all.",
        typical_holding_period="Annual rebalance -- the standard academic cadence for size-sorted "
                                "portfolios (Fama-French rebalance their size/BM portfolios each June).",
        holding_days_min=330, holding_days_max=395,
        expected_trade_frequency="Low",
        data_requirements=["daily_ohlcv_history", "shares_outstanding_snapshot"],
        known_strengths="One of the three foundational anomalies (alongside value and momentum) that "
                        "originally undermined pure CAPM and motivated the multi-factor asset-pricing "
                        "paradigm -- an enormous, decades-deep replication record. Fully implementable "
                        "today from data already fetched (price x shares outstanding for market cap), "
                        "unlike this platform's already-blocked value/quality candidates.",
        known_weaknesses="Three real, disclosed concerns temper the raw premium: (1) the effect is "
                         "concentrated in the SMALLEST, least liquid names -- exactly the segment where "
                         "real-world trading costs and NSE liquidity constraints bite hardest, a concern "
                         "this platform's own already-REJECTed Turnover/Liquidity and Amihud findings "
                         "corroborate directly; (2) whether a clean size premium still exists post-discovery "
                         "is itself a well-cited, CONTESTED question in the literature (much of the original "
                         "1981-1993-era premium may be concentrated in a January-effect/small-sample "
                         "artifact per later critiques) -- unlike value or momentum, this is not a settled "
                         "anomaly; (3) needs today's shares-outstanding SNAPSHOT applied across historical "
                         "formation dates (no historical shares-outstanding time series exists on this "
                         "platform) -- the same disclosed approximation already used for the existing "
                         "Turnover/Liquidity candidate, mild for stable large/mid caps, more material for "
                         "any stock with a big historical share-count change from splits/buybacks/dilution.",
        academic_replication_quality="Extremely well-replicated globally over 40+ years, but with real, "
                                      "well-documented ambiguity about whether the premium has decayed or "
                                      "reversed since discovery -- the modern consensus on whether a clean "
                                      "size premium still exists is genuinely mixed, not just weakened.",
        evidence_sufficiency_note="Sufficient as one of the most foundational anomalies in asset pricing, "
                                   "but the post-discovery decay/reversal literature means this should be "
                                   "tested on the RECENT period specifically (this platform's own "
                                   "recency-check discipline is especially relevant here), not assumed to "
                                   "hold at its originally-measured 1981 magnitude.",
        academic_evidence_score=9, expected_robustness_score=4, operational_simplicity_score=8,
        research_value_score=7, data_availability_score=8, implementation_feasibility_score=7,
        notes="Honest cadence is annual rebalance (330-395 days), which is neither this platform's usual "
              "1-6 month swing tactical hold nor a multi-year buy-and-hold -- a genuinely 'medium'-lane "
              "candidate by nature. Filed under horizon_lane='swing' (the field's default) only because of "
              "the test-suite constraint described in the discovery-pass comment above this entry; treat "
              "this note, not the horizon_lane field, as the honest classification until that test is "
              "deliberately updated.",
    ),
    CandidateProfile(
        key="intraday_momentum_half_hour",
        name="Intraday Momentum (First Half-Hour Return Predicts Last Half-Hour Return)",
        authors="Gao, L., Han, Y., Li, S.Z. and Zhou, G.",
        publication="\"Market Intraday Momentum,\" Journal of Financial Economics, Vol. 129, No. 2, "
                     "394-414 (2018) -- verified real via WebSearch 2026-09-22 (including the exact "
                     "author list and journal, corrected from an initially-misremembered journal name "
                     "during this same search), not from memory",
        year=2018,
        asset_class="Broad market index/ETF (originally S&P 500 SPY), intraday",
        direction="Long-only in the direction of the first half-hour return, held into the final half-hour "
                  "of the same session -- a same-day timing signal, not a cross-sectional stock-selection "
                  "strategy.",
        factor_family="Intraday momentum (index/ETF timing)",
        factor_tags={"intraday_momentum"},
        mechanism="Using high-frequency S&P 500 ETF (SPY) data 1993-2013, the FIRST half-hour return of "
                  "the trading session significantly predicts the LAST half-hour return in the SAME "
                  "direction -- an intraday timing pattern the authors link to informed institutional "
                  "trading strategically executed late in the day. The effect is stronger on high-"
                  "volatility days, high-volume days, recession days, and major macro-news days. A "
                  "structurally different SHAPE of strategy from every other candidate on this roadmap: a "
                  "single-instrument, same-session timing signal, not a cross-sectional multi-stock decile "
                  "sort, and mechanically distinct from Pool D's existing VWAP-fade intraday strategy "
                  "(SW-027, a mean-reversion/exhaustion signal, not a momentum-continuation one).",
        typical_holding_period="Same trading session only -- entered shortly after the opening half-hour, "
                                "exited before the close; no overnight hold at all. Recorded as a single "
                                "calendar day below, this platform's existing convention for a sub-day "
                                "holding period (matching the Day-of-Week entry's own single-day recording).",
        holding_days_min=1, holding_days_max=1,
        expected_trade_frequency="Very high -- potentially one round-trip per trading session",
        data_requirements=["daily_ohlcv_history", "intraday_bar_history"],
        known_strengths="A well-cited paper in a top-tier finance journal (JFE) with a plausible, tested "
                        "economic mechanism (informed late-day institutional trading), not a data-mined "
                        "curiosity -- the original paper documents the pattern's strength varying "
                        "sensibly with volatility/volume/macro-news conditions rather than appearing as a "
                        "flat, unconditional effect. Later extensions (e.g. intraday time-series momentum "
                        "evidence in Chinese equity index futures, and broader international index "
                        "evidence) find related patterns outside the original US SPY sample -- not a "
                        "purely single-market finding. Genuinely orthogonal SHAPE (single-instrument, "
                        "same-day timing) to every other candidate in this program, which are all either "
                        "cross-sectional multi-stock decile sorts or multi-day-to-multi-year holds.",
        known_weaknesses="Real methodological concerns, distinct from data access (corrected 2026-09-22 -- "
                         "see notes): (1) real historical intraday bars exist via the Kite market-data app "
                         "(fetch_kite_intraday.py, already used for real backtests elsewhere), but "
                         "retention is only ~240 trading days -- a much smaller sample than the original "
                         "paper's 20 years on SPY, and this platform's backtesting engine has no walk-"
                         "forward path wired up for sub-daily bars yet, real integration work; (2) the "
                         "original finding is on a broad MARKET INDEX/ETF (SPY), not individual "
                         "cross-sectional stocks -- applying it to individual NSE stocks rather than a "
                         "NIFTY-index-tracking instrument is an adaptation the paper itself does not test, "
                         "since single-stock intraday patterns are typically noisier and more idiosyncratic "
                         "than index-level ones, and this program has no documented NIFTY-index-ETF trading "
                         "path elsewhere; (3) even in the original paper, transaction costs and the "
                         "bid-ask spread materially erode the raw pattern at high trade frequency, and this "
                         "platform's backtesting engine does not yet model transaction costs at all -- a "
                         "concern especially acute for a same-day, high-frequency signal like this one.",
        academic_replication_quality="A single foundational paper in a top journal (JFE), with several "
                                      "later extensions supporting the general pattern outside the "
                                      "original US SPY sample -- reasonably replicated for a relatively "
                                      "recent (2018) finding, though no NSE-specific or single-stock "
                                      "replication was found.",
        evidence_sufficiency_note="Sufficient to research now that real intraday bar data is confirmed "
                                   "available (2026-09-22) -- but genuinely untested on NSE specifically "
                                   "and on single stocks rather than an index instrument, and on a much "
                                   "shorter (~240 trading day) sample than the original paper -- real open "
                                   "questions a backtest here would have to confront honestly, not "
                                   "reasons to expect the same result.",
        academic_evidence_score=7, expected_robustness_score=5, operational_simplicity_score=6,
        research_value_score=7, data_availability_score=7, implementation_feasibility_score=5,
        notes="Reclassified 2026-09-22 (initially filed IMPLEMENTATION-BLOCKED by Discovery Scout's first "
              "run, per direction after review: real intraday bar data already exists in this platform via "
              "the same shared Kite market-data session used everywhere else, just under a different, more "
              "precise capability tag than the one originally matched -- see DATA_CAPABILITIES' "
              "intraday_bar_history entry). Honestly an 'intraday'-lane candidate (same-session, no "
              "overnight hold at all), the first genuinely intraday-shaped academic candidate on this "
              "roadmap. Filed under horizon_lane='swing' (the field's default) only because of the "
              "test-suite constraint described in the discovery-pass comment above -- treat this note, not "
              "the horizon_lane field, as the honest classification until that test is deliberately "
              "updated.",
    ),
    CandidateProfile(
        key="shareholder_yield_faber",
        name="Shareholder Yield (Dividends + Buybacks + Debt Paydown Composite)",
        authors="Faber, M.T.",
        publication="\"Shareholder Yield: A Better Approach to Dividend Investing\" (2015), Cambria "
                     "Investment Management -- verified real via WebSearch 2026-09-22, including the "
                     "live, SEC-registered Cambria Shareholder Yield ETF (ticker SYLD) that tracks this "
                     "exact methodology per its own SEC prospectus filings, not from memory",
        year=2015,
        asset_class="Single-stock equities, cross-sectional fundamentals/capital-allocation screen",
        direction="Long-only top-decile by combined shareholder yield.",
        factor_family="Shareholder yield (cash-return composite)",
        factor_tags={"shareholder_yield", "corporate_actions"},
        mechanism="Combines three separate channels by which a company returns cash to (or reduces net "
                  "claims against) shareholders -- dividend yield, net share buyback yield (shares "
                  "repurchased minus shares issued), and net debt paydown yield -- into a single composite "
                  "score, then buys the highest-scoring names. Explicitly built as a broader, more complete "
                  "measure than dividend yield alone; the book's central finding is that portfolios of "
                  "high-shareholder-yield firms outperform both the broad market and high-dividend-yield-"
                  "only portfolios. A genuinely different construction from this roadmap's existing value "
                  "candidates' price-to-fundamentals ratios or quality's profitability/leverage composite -- "
                  "this is a CAPITAL-ALLOCATION/cash-return signal. Tagged with the shared 'corporate_actions' "
                  "factor tag (alongside its own new 'shareholder_yield' tag) to make its real mechanism "
                  "overlap with this roadmap's existing Net Share Issuance/Buyback Anomaly candidate "
                  "(Ikenberry-Lakonishok-Vermaelen 1995/Pontiff-Woodgate 2008) mechanically visible to "
                  "future diversification scoring, not just described in prose -- both use buyback "
                  "behavior as a signal, though this one combines it with dividends and debt paydown "
                  "rather than using net issuance alone.",
        typical_holding_period="Cambria's own live, SEC-registered SYLD ETF discloses (per its prospectus, "
                                "confirmed via SEC filing search 2026-09-22) that it reconstitutes and "
                                "rebalances AT LEAST QUARTERLY -- a real, verified operational floor. The "
                                "book's own underlying investment case, like this roadmap's existing Coffee "
                                "Can candidate, argues for a longer-horizon 'persistently high shareholder "
                                "yield compounds' philosophy rather than quarterly churn; this profile "
                                "honestly discloses BOTH the confirmed quarterly operational floor and the "
                                "book's own lower-turnover philosophy rather than picking whichever framing "
                                "suits a preferred classification.",
        holding_days_min=90, holding_days_max=None,
        expected_trade_frequency="Low to moderate -- at least quarterly by the live ETF's own confirmed "
                                  "methodology",
        data_requirements=["daily_ohlcv_history", "corporate_actions_buyback_history",
                            "point_in_time_fundamentals_history"],
        known_strengths="A widely-read, practitioner-standard book (not a blog) with a REAL, currently-"
                        "live fund (Cambria Shareholder Yield ETF, ticker SYLD) tracking this exact "
                        "composite methodology since 2013 per SEC filings -- 'widely accepted trading "
                        "book' per this program's own research-universe rule, with unusually strong "
                        "real-world validation (an actual tracked, SEC-registered product, not just a "
                        "backtest described in a book). Genuinely distinct factor_family from every "
                        "existing candidate: a capital-allocation/cash-return signal, not a price-based "
                        "valuation or accounting-profitability composite.",
        known_weaknesses="The single most data-hungry candidate of the three added this run: needs a "
                         "genuine buyback-announcement/net-issuance history (confirmed absent, the same "
                         "gap already blocking this roadmap's existing Net Share Issuance/Buyback "
                         "candidate) AND historical debt-level data for the paydown leg (the same point-"
                         "in-time fundamentals gap already blocking every value/quality candidate on this "
                         "roadmap) -- a compounded, not single, data gap. Dividend history itself, the "
                         "simplest of the three legs, is ALSO not currently a tracked capability anywhere "
                         "in this program's fundamentals pipeline (fundamentals/fundamental_agent.py's "
                         "current snapshot fields do not include dividend yield) -- a further, currently-"
                         "undeclared gap worth flagging even though the other two legs already block this "
                         "candidate outright on their own; no new DATA_CAPABILITIES tag was added for it "
                         "since it isn't needed to reach a correct (blocked) classification. The book's own "
                         "backtests are US-market-only; no India-specific validation of this exact "
                         "three-part composite was found.",
        academic_replication_quality="Not peer-reviewed academic research -- a published, widely-read "
                                      "practitioner book (the same category this program already accepts "
                                      "for Coffee Can Investing) with unusually strong real-world "
                                      "validation via a real, SEC-registered ETF tracking the identical "
                                      "methodology since 2013; no independent academic replication of this "
                                      "exact three-part composite was found.",
        evidence_sufficiency_note="Sufficient as a well-known, real, currently-tracked practitioner "
                                   "methodology per this program's research-universe rule; blocked by a "
                                   "genuinely compounded data gap (buybacks AND historical debt levels AND, "
                                   "less critically, dividend history) rather than a single missing dataset.",
        academic_evidence_score=5, expected_robustness_score=6, operational_simplicity_score=4,
        research_value_score=6, data_availability_score=1, implementation_feasibility_score=1,
        notes="Honestly a 'long_term'-lane candidate by the book's own philosophy (though the only "
              "independently-confirmed cadence, the live SYLD ETF's quarterly rebalance floor, sits at the "
              "swing/medium boundary -- both are disclosed in typical_holding_period above rather than "
              "picking one). Filed under horizon_lane='swing' (the field's default) only because of the "
              "test-suite constraint described in the discovery-pass comment above this entry; treat this "
              "note, not the horizon_lane field, as the honest classification until that test is "
              "deliberately updated.",
    ),

    # =================================================================
    # Monthly discovery pass (2026-10-02) -- deliberately looked beyond
    # swing again, this time landing candidates in "intraday", "crypto"
    # (x2) and "long_term" lanes. Unlike the 2026-09-22 pass above, these
    # four are NEW keys, not in test_research_roadmap.py's
    # _PRE_HORIZON_LANE_KEYS set -- that test only constrains the keys it
    # names, so horizon_lane/market are set to their honest values
    # directly below, no "filed under swing because of a test constraint"
    # workaround needed.
    # =================================================================
    CandidateProfile(
        key="opening_range_breakout",
        name="Opening Range Breakout (ORB)",
        authors="Crabel, T.; Zarattini, C., Barbon, A. and Aziz, A.",
        publication="Crabel, T. (1990), \"Day Trading with Short-Term Price Patterns and Opening Range "
                     "Breakout,\" Traders Press -- a widely-read, widely-cited professional day-trading "
                     "book, the originating 'widely accepted trading book' source per this program's "
                     "research-universe rule; Zarattini, C., Barbon, A. and Aziz, A. (2024), \"A "
                     "Profitable Day Trading Strategy For The U.S. Equity Market,\" Swiss Finance "
                     "Institute Research Paper No. 24-98 (SSRN) -- a modern, rigorously backtested "
                     "academic-style working paper replicating and refining the same core pattern -- "
                     "both verified real via WebSearch 2026-10-02, not from memory",
        year=1990,
        asset_class="Single-stock equities, intraday",
        direction="Long-only breakout above the opening range's high (the source material's mirror "
                  "short-side breakout below the range's low is out of scope here, same disclosed "
                  "long-only reduction as every other strategy in this program).",
        factor_family="Intraday breakout (opening-range)",
        factor_tags={"intraday_breakout", "volume_attention"},
        mechanism="The first few minutes of the trading session (Crabel's original studies use 5-, 10- "
                  "and 30-minute windows; the 2024 SFI paper settles on 5 minutes as the best-performing "
                  "duration it tested) establish an 'opening range' (its high and low); a subsequent "
                  "breakout above that range's high is traded as a same-session continuation signal, on "
                  "the premise that the earliest post-open price action reveals which direction informed/"
                  "institutional flow is pushing that session. The 2024 paper's key refinement: the edge "
                  "concentrates almost entirely in stocks with unusually high RELATIVE opening volume "
                  "(versus their own trailing 14-day average opening volume) -- a 'stocks in play' "
                  "filter, not a universal effect. A structurally different SHAPE from every other "
                  "candidate in this program: a single-stock, same-session breakout signal, mechanically "
                  "distinct from Pool D's existing VWAP-fade intraday strategy (SW-027, a mean-reversion/"
                  "exhaustion signal, the opposite direction of trade) and from this roadmap's own "
                  "Intraday Momentum candidate (Gao et al. 2018, an index/ETF return-continuation timing "
                  "signal, not a per-stock range breakout).",
        typical_holding_period="Same trading session only -- entered shortly after the opening range "
                                "closes, exited by the session's close (or on a stop); no overnight hold.",
        holding_days_min=1, holding_days_max=1,
        expected_trade_frequency="High -- potentially one round-trip per trading session per qualifying stock",
        data_requirements=["daily_ohlcv_history", "intraday_bar_history"],
        known_strengths="Unusually well-evidenced for an intraday pattern: a widely-read, decades-old "
                        "professional trading book (the same 'widely accepted trading book' category "
                        "this program already accepts for Coffee Can Investing and Shareholder Yield) AND "
                        "a recent (2024), rigorously backtested SSRN working paper with an explicit, "
                        "quantified, relative-volume-filtered edge (1,637% cumulative / 41.6% annualized "
                        "IRR, 2016-2023, on a top-20-by-relative-volume U.S. equity universe) -- real "
                        "convergence across a 34-year gap and two very different evidentiary standards. "
                        "Fully implementable today: this platform's existing Kite intraday-bar "
                        "infrastructure (fetch_kite_intraday.py, already used for the intraday research "
                        "lab's EXP-001 onward) gives exactly the 5-minute bars the 2024 paper's best "
                        "configuration uses.",
        known_weaknesses="The SAME 2024 paper that gives this candidate its strongest modern evidence "
                         "also found the UNFILTERED, plain version of the pattern weak -- almost all the "
                         "edge concentrates in a 'stocks in play' relative-volume filter, so a faithful "
                         "implementation needs that filter (a real design/calibration decision) or should "
                         "expect a materially weaker result than the headline number above. That filter "
                         "itself shares this program's existing 'volume_attention' family (already "
                         "represented by the PASS-verdicted High-Volume Return Premium, SW-017) -- not a "
                         "fully orthogonal diversification source despite the fresh SHAPE (intraday vs. "
                         "multi-day). This platform's ~240-trading-day Kite intraday retention is a much "
                         "shorter sample than either source's own backtest window, and the backtesting "
                         "engine has no transaction-cost model yet -- a real concern for a same-day, "
                         "potentially high-frequency signal.",
        academic_replication_quality="A widely-cited, decades-old practitioner book plus an independent, "
                                      "recent (2024) rigorous replication on a different market/instrument "
                                      "set (QQQ/TQQQ and a relative-volume-filtered U.S. equity universe) "
                                      "finding a materially similar core pattern -- genuinely convergent, "
                                      "if not peer-reviewed-journal-published, evidence.",
        evidence_sufficiency_note="Sufficient to justify research time -- real, convergent, quantified "
                                   "evidence exists, but the 2024 paper's own finding (plain ORB is weak; "
                                   "the relative-volume filter does nearly all the work) must be carried "
                                   "into any implementation, not simplified away.",
        academic_evidence_score=7, expected_robustness_score=5, operational_simplicity_score=5,
        research_value_score=7, data_availability_score=7, implementation_feasibility_score=5,
        horizon_lane="intraday", market="India",
    ),
    CandidateProfile(
        key="crypto_size_factor",
        name="Cryptocurrency Size Factor",
        authors="Liu, Y., Tsyvinski, A. and Wu, X.",
        publication="\"Common Risk Factors in Cryptocurrency,\" The Journal of Finance, Vol. 77, No. 2, "
                     "1133-1177 (2022) -- verified real via WebSearch 2026-10-02, not from memory; the "
                     "same paper already underlies this program's live Crypto Cross-Sectional Momentum "
                     "strategy (swing_research/strategies/crypto_xs_momentum.py), which implements only "
                     "that paper's MOMENTUM factor, not its SIZE factor",
        year=2022,
        asset_class="Cryptocurrencies, cross-sectional",
        direction="Long-only smallest-market-cap quintile (the paper's long-short goes short the largest "
                  "coins) -- same disclosed reduction as every other candidate in this program.",
        factor_family="Size (small-cap premium, crypto)",
        factor_tags={"size", "crypto_factor"},
        mechanism="Alongside momentum, Liu-Tsyvinski-Wu's three-factor model (market, size, momentum) "
                  "identifies a SIZE factor in the cross-section of cryptocurrencies: smaller-market-cap "
                  "coins earn systematically higher risk-adjusted returns than larger ones, directly "
                  "analogous to the equity size premium (Banz 1981, also newly added to this roadmap this "
                  "run) but documented separately and specifically for crypto. A genuinely different "
                  "SIGNAL from this program's existing crypto strategies, all of which are price/return-"
                  "based (momentum, trend-timing, volatility-managed exposure) -- this is the first "
                  "crypto candidate keyed on market CAPITALIZATION rather than price history.",
        typical_holding_period="Weekly rebalance, matching the same paper's momentum factor as already "
                                "implemented in this program's crypto lane (crypto_xs_momentum.py).",
        holding_days_min=7, holding_days_max=7,
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history", "crypto_market_cap_history"],
        known_strengths="Same top-tier, already-partially-implemented source paper as this program's "
                        "live Crypto Cross-Sectional Momentum strategy -- the size factor is reported in "
                        "that exact paper with the same statistical rigor as the momentum factor this "
                        "program already trades, not a weaker or less-verified companion finding. "
                        "Genuinely new factor_family for the crypto lane (every crypto strategy currently "
                        "implemented is price-based; this is the first capitalization-based one).",
        known_weaknesses="Needs a circulating-supply/market-cap time series for each coin in the traded "
                         "universe -- confirmed absent (data/fetch_crypto.py's Binance klines give OHLCV "
                         "only, no supply field, and no CoinGecko/CoinMarketCap-style vendor is integrated "
                         "anywhere in this program). Conceptually close to the newly-added equity Size "
                         "Premium candidate on this same roadmap (same economic story, different asset "
                         "class) -- not a fully independent idea, though this module's diversification "
                         "scoring only compares a candidate against the LIVE portfolio registry, not "
                         "against other candidates, so that overlap isn't mechanically penalized here. "
                         "Crypto 'size' is also a less mature, less-replicated literature than the "
                         "equivalent 40+-year equity size literature -- a single foundational paper, not "
                         "yet an extensively cross-validated finding.",
        academic_replication_quality="A single, but top-tier (Journal of Finance), foundational paper for "
                                      "crypto factor investing -- not yet independently replicated by a "
                                      "second research team the way the equity size/value/momentum "
                                      "anomalies have been.",
        evidence_sufficiency_note="Sufficient to catalogue given the paper's rigor and this program's own "
                                   "precedent of already trading its momentum factor, but blocked purely "
                                   "by a market-cap data gap, not by any doubt about the source.",
        academic_evidence_score=7, expected_robustness_score=5, operational_simplicity_score=6,
        research_value_score=6, data_availability_score=1, implementation_feasibility_score=1,
        horizon_lane="crypto", market="Global",
    ),
    CandidateProfile(
        key="crypto_long_horizon_reversal",
        name="Cryptocurrency Long-Horizon Reversal",
        authors="Dobrynskaya, V.; Fičura, M. and Colak, G.",
        publication="Dobrynskaya, V. (2023), \"Cryptocurrency Momentum and Reversal,\" The Journal of "
                     "Alternative Investments, Vol. 26, No. 1, 65-76; Fičura, M. and Colak, G. (2023), "
                     "\"Impact of Size and Volume on Cryptocurrency Momentum and Reversal,\" SSRN working "
                     "paper -- both verified real via WebSearch 2026-10-02, not from memory",
        year=2023,
        asset_class="Cryptocurrencies, cross-sectional",
        direction="Long-only top-decile (past LOSERS over the reversal-horizon formation window) -- the "
                  "papers' own long-short construction reduced to long-only, same disclosed reduction as "
                  "every other candidate in this program.",
        factor_family="Reversal (longer-horizon, crypto-specific)",
        factor_tags={"reversal_long_horizon", "crypto_factor"},
        mechanism="Using a sample of up to 2,000 cryptocurrencies (2014-2020), Dobrynskaya finds momentum "
                  "at SHORT horizons (up to 2-4 weeks) but a significant REVERSAL once the formation/"
                  "holding horizon extends beyond roughly one month -- past losers over these longer "
                  "windows subsequently outperform. Fičura & Colak corroborate a related horizon-"
                  "dependent momentum/reversal switch and show it is also modulated by coin size and "
                  "volume. The authors describe crypto's much faster switch from momentum to reversal "
                  "(about 1 month) than equities' multi-year De Bondt-Thaler-style reversal as evidence of "
                  "a 'faster metabolism' in crypto markets -- a genuinely distinct mechanism from every "
                  "existing crypto strategy in this program, all of which are either short-horizon "
                  "momentum/trend-following (crypto_xs_momentum, crypto_tsmom, crypto_trend_timing*) or "
                  "volatility-scaling (crypto_vol_managed), never reversal.",
        typical_holding_period="The papers test sort/hold horizons from 1 week to 2 years; the reversal "
                                "effect itself is reported as strongest and most consistent in the "
                                "roughly 1-3 month band immediately beyond where momentum fades, with the "
                                "specific horizon (and whether a 2026-era universe still shows the same "
                                "switch point) needing confirmation against the full papers before "
                                "implementation, not assumed from this summary.",
        holding_days_min=30, holding_days_max=90,
        expected_trade_frequency="Low-moderate",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Fully implementable TODAY from data this program already fetches (crypto daily "
                        "OHLCV via data/fetch_crypto.py -- no market-cap or other new data source needed, "
                        "unlike the Size Factor candidate added alongside this one). Genuinely orthogonal "
                        "mechanism (reversal, not continuation) to every crypto strategy currently "
                        "implemented in this program. Two independent, recent (2023) studies corroborate "
                        "the same horizon-dependent momentum-to-reversal switch, a real replication, not a "
                        "single isolated finding.",
        known_weaknesses="A young (2023) literature relative to the decades-deep equity long-term-"
                         "reversal record, on a relatively short (2014-2020) underlying crypto sample; a "
                         "separate, more skeptical recent literature (e.g. Grobys, \"Cryptocurrency "
                         "Momentum: Is It an Illusion?\", International Journal of Finance & Economics, "
                         "2026) actively disputes the robustness of crypto momentum/reversal patterns more "
                         "broadly, a live academic disagreement this candidate should be read against, not "
                         "treated as settled. The exact horizon at which reversal is strongest needs "
                         "confirmation against the full papers (not just this summary) before "
                         "implementation -- the holding-day range above is this module's own conservative "
                         "estimate from the available abstracts/summaries, not a number taken directly "
                         "from either paper's own stated rule.",
        academic_replication_quality="Two independent, recent (2023) studies find a materially consistent "
                                      "horizon-dependent pattern, partially offset by at least one more "
                                      "recent (2026) critical paper questioning crypto momentum/reversal "
                                      "robustness generally -- a genuinely live, unsettled debate rather "
                                      "than a convergent consensus.",
        evidence_sufficiency_note="Sufficient to justify research time given two corroborating studies and "
                                   "full data availability, but the disclosed critical counter-literature "
                                   "means this should be tested on the most recent sub-period specifically, "
                                   "not assumed to hold at its originally-measured strength.",
        academic_evidence_score=5, expected_robustness_score=4, operational_simplicity_score=6,
        research_value_score=6, data_availability_score=10, implementation_feasibility_score=6,
        horizon_lane="crypto", market="Global",
    ),
    CandidateProfile(
        key="dogs_of_the_dow",
        name="Dogs of the Dow (High Dividend-Yield Selection)",
        authors="O'Higgins, M.B. and Downes, J.",
        publication="O'Higgins, M.B. (1991), \"Beating the Dow,\" HarperCollins -- a widely-read, "
                     "widely-followed practitioner book (real, institutionally-tracked assets exceeding "
                     "$20 billion at its peak per multiple industry sources) -- verified real via "
                     "WebSearch 2026-10-02, not from memory",
        year=1991,
        asset_class="Single-stock equities (Dow Jones Industrial Average constituents), cross-sectional",
        direction="Long-only top-10-by-dividend-yield (the strategy's own construction is long-only by "
                  "design, unlike most other candidates in this roadmap -- no long-short reduction needed).",
        factor_family="Dividend yield (high-yield blue-chip selection)",
        factor_tags={"dividend_yield", "value"},
        mechanism="Each year, rank the 30 Dow Jones Industrial Average constituents by dividend yield and "
                  "buy the 10 highest-yielding names in equal weight; hold for exactly one year, then "
                  "re-rank and repeat. O'Higgins' own 17-year sample (reported in the book) found the "
                  "'Dogs' averaged 17.9% annually versus 11.1% for the rest of the Dow -- a simple, "
                  "exact, entirely mechanical rule (no blending/weighting beyond equal-weighting the top "
                  "10), unlike this roadmap's existing Shareholder Yield candidate's three-part composite. "
                  "A genuinely new factor_family for this roadmap: every existing value candidate here "
                  "(Basu/Fama-French Value, the NSE Value 20 index) sorts on PRICE ratios (earnings yield, "
                  "book-to-market); this sorts purely on current DIVIDEND yield.",
        typical_holding_period="Exactly one year, by explicit design (annual rebalance).",
        holding_days_min=330, holding_days_max=395,
        expected_trade_frequency="Very low -- once a year by construction",
        data_requirements=["daily_ohlcv_history", "dividend_yield_history"],
        known_strengths="One of the most widely-known, widely-followed mechanical equity strategies in "
                        "retail/practitioner finance ('widely accepted trading book' per this program's "
                        "research-universe rule, the same category already accepted for Coffee Can "
                        "Investing and Shareholder Yield) -- real institutional assets and multiple "
                        "tracking funds/websites built around it since the early 1990s. Trivially simple "
                        "mechanically (rank by one number, buy the top 10, hold a year) -- the lowest "
                        "implementation complexity of any value-family candidate on this roadmap, if the "
                        "data existed. Also the first CANDIDATES entry to use this program's existing "
                        "US-equity lane (swing_research/universe_us.py) for a genuinely US-native "
                        "strategy, rather than adapting a US finding onto the NSE universe.",
        known_weaknesses="Needs a dividend-yield time series this program does not have at all, snapshot "
                         "or historical (confirmed absent 2026-10-02, generalizing the exact gap already "
                         "flagged under the Shareholder Yield candidate's own known_weaknesses) -- the "
                         "single blocking gap. Also needs a Dow-30-specific constituent list; this "
                         "program's existing US universe (universe_us.py) freezes the S&P 500, not the "
                         "narrower, differently-selected (price-weighted index committee membership, not "
                         "a market-cap rule) Dow 30 -- a second, smaller adaptation needed on top of the "
                         "data gap. The underlying 10-stock, 30-name universe is also far more "
                         "concentrated than every other cross-sectional candidate in this program (which "
                         "typically sort over hundreds of names), a real statistical-power concern "
                         "independent of the data and universe gaps.",
        academic_replication_quality="Not peer-reviewed academic research -- a widely-read, decades-"
                                      "followed practitioner book, explicitly the 'widely accepted trading "
                                      "book' category this program's research universe already permits; "
                                      "independent academic replication of the exact 10-stock rule is "
                                      "thinner than for a peer-reviewed anomaly, though the broader "
                                      "high-dividend-yield-premium literature it sits within is well-"
                                      "established.",
        evidence_sufficiency_note="Sufficient as a well-known, real, long-tracked mechanical strategy per "
                                   "this program's own book-acceptance precedent; blocked by a genuine "
                                   "data gap (dividend yield, not tracked at all) plus a smaller, "
                                   "independent universe adaptation (Dow 30 vs. the existing S&P 500 "
                                   "freeze).",
        academic_evidence_score=4, expected_robustness_score=5, operational_simplicity_score=7,
        research_value_score=5, data_availability_score=1, implementation_feasibility_score=1,
        horizon_lane="long_term", market="US",
    ),

    # =================================================================
    # Monthly discovery pass (2026-10-03) -- deliberately weighted toward
    # the crypto and US-equity lanes per explicit direction (both were
    # still thin: 2 crypto candidates total, one blocked; exactly 1 US
    # candidate, also blocked). All four below verified real via WebSearch
    # 2026-10-03, not from memory. Two candidates proposed by the initial
    # literature search this run were REJECTED before being added here
    # because they turned out to be the SAME paper/mechanism already
    # implemented in this program under a different market (Lou-Polk-
    # Skouras 2019's overnight-return persistence finding IS this
    # program's own live Overnight Return Anomaly strategy, SW-016/
    # EXP-078 PASS; Gervais-Kaniel-Mingelgrin 2001's volume-shock finding
    # IS this program's own live High-Volume Return Premium strategy,
    # SW-017/EXP-080 PASS) -- exactly the "same core mechanism, different
    # market" duplicate this module's governance rules out, caught by
    # cross-checking against EXISTING_STRATEGY_TAGS/the strategy source
    # files before writing these entries, not after.
    # =================================================================
    CandidateProfile(
        key="crypto_idiosyncratic_volatility",
        name="Cryptocurrency Idiosyncratic Volatility Factor",
        authors="Zhang, W. and Li, Y.",
        publication="\"Is idiosyncratic volatility priced in cryptocurrency markets?\", Research in "
                     "International Business and Finance, Vol. 54 (2020), Elsevier -- verified real via "
                     "WebSearch 2026-10-03 (confirmed authors, journal, volume and year, and that it is "
                     "an original, cited empirical finding, not a survey), not from memory",
        year=2020,
        asset_class="Cryptocurrencies, cross-sectional",
        direction="Long-only top-decile (highest idiosyncratic volatility) -- the paper's own documented "
                  "sign for crypto (see mechanism below), a disclosed reduction from its long-short "
                  "portfolio-sort construction, same convention as every other candidate in this program.",
        factor_family="Idiosyncratic volatility (risk-based, crypto-specific)",
        factor_tags={"risk_based", "crypto_factor"},
        mechanism="For each coin, regress daily returns on a crypto-market-factor proxy (a Fama-MacBeth-"
                  "style market model, the same general regression machinery this program already built "
                  "for Jensen's Alpha and Betting Against Beta); the regression RESIDUAL's volatility is "
                  "that coin's idiosyncratic volatility (IVOL), net of common crypto-market risk. "
                  "Cross-sectionally sorting coins by IVOL finds a POSITIVE IVOL-return relationship in "
                  "crypto -- the OPPOSITE sign from the well-known equity-market 'IVOL puzzle' (Ang, Chen, "
                  "Xing and colleagues' well-documented NEGATIVE relationship, already represented in this "
                  "program's own 'risk_based' family via idiosyncratic_volatility, INCONCLUSIVE/PAPER_"
                  "TRADING). A genuinely different mechanism SHAPE from every existing crypto candidate: "
                  "this is the first crypto candidate that cross-sectionally ranks coins by a RISK measure "
                  "(residual volatility) rather than by price-return momentum/reversal/trend, or by "
                  "Crypto Volatility-Managed Exposure's (crypto_vol_managed, already implemented, REJECT) "
                  "entirely different use of volatility -- that strategy TIME-SCALES one portfolio's net "
                  "exposure by its own trailing realized volatility over time, never cross-sectionally "
                  "ranking coins against each other by a residual-risk measure the way this candidate does.",
        typical_holding_period="Monthly portfolio sorts and rebalancing, the paper's own Fama-MacBeth "
                                "regression cadence.",
        holding_days_min=25, holding_days_max=35,
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Fully implementable TODAY from data this program already fetches (crypto daily "
                        "OHLCV via data/fetch_crypto.py for both the individual-coin return series and the "
                        "market-factor proxy, built the same way this program's existing crypto momentum "
                        "strategies already construct a cross-sectional universe return) -- no new data "
                        "source needed, unlike the Size Factor and Funding-Rate Carry candidates on this "
                        "same roadmap. Reuses this program's existing rolling-regression infrastructure "
                        "(already built for Jensen's Alpha / Betting Against Beta) rather than needing new "
                        "signal-construction machinery from scratch.",
        known_weaknesses="The SIGN of this effect is genuinely contested in the broader crypto-volatility "
                         "literature -- related studies (e.g. work on a crypto 'low volatility anomaly') "
                         "report the OPPOSITE (negative) relationship in different samples/periods, so this "
                         "should be read as 'a real, published, positive-sign finding worth testing "
                         "empirically on this platform's own universe,' not as a settled direction to "
                         "assume going in. The underlying crypto sample periods in this literature are "
                         "still short (pre-2020 crypto data is thin and survivorship-prone), a general "
                         "immaturity this entire crypto lane already discloses elsewhere on this roadmap. "
                         "Shares this program's 'risk_based' factor tag with the already-implemented, "
                         "INCONCLUSIVE equity Idiosyncratic Volatility strategy -- not the same signal (crypto "
                         "vs. India equities, opposite documented sign), but real conceptual/mechanical "
                         "lineage (both are residual-volatility measures from a market-model regression), "
                         "so a meaningful, not full, diversification credit is warranted.",
        academic_replication_quality="A single foundational paper in a mid-tier but real, peer-reviewed "
                                      "finance journal (Research in International Business and Finance, "
                                      "Elsevier) -- not yet as deeply replicated as this program's top-tier "
                                      "(Journal of Finance) crypto momentum/size source paper, and the "
                                      "sign itself is actively disputed by at least one other study in this "
                                      "young literature.",
        evidence_sufficiency_note="Sufficient to catalogue and test empirically given the paper's real, "
                                   "peer-reviewed sourcing and full data availability, but the contested "
                                   "sign means any implementation should treat the direction as a hypothesis "
                                   "to verify on this platform's own universe, not a given.",
        academic_evidence_score=5, expected_robustness_score=4, operational_simplicity_score=6,
        research_value_score=7, data_availability_score=10, implementation_feasibility_score=7,
        horizon_lane="crypto", market="Global",
    ),
    CandidateProfile(
        key="crypto_illiquidity_premium",
        name="Cryptocurrency Illiquidity Premium (Amihud-style)",
        authors="Ali, A., Peng, S. and Shams, S.; corroborated by Zhang, W. and Li, Y.",
        publication="Ali, Peng and Shams, \"Unravelling cross-sectional patterns in cryptocurrencies: a "
                     "four-factor asset pricing model,\" China Accounting and Finance Review, Vol. 27, No. "
                     "4, 493 (2025), Emerald (DOI 10.1108/CAFR-06-2024-0077); corroborated by Zhang, W. and "
                     "Li, Y., \"Liquidity risk and expected cryptocurrency returns,\" International Journal "
                     "of Finance & Economics, Vol. 28 (2023), 472-492, Wiley -- both verified real via "
                     "WebSearch 2026-10-03 (confirmed authors, journals, DOIs/volumes and years), not from "
                     "memory",
        year=2025,
        asset_class="Cryptocurrencies, cross-sectional",
        direction="Long-only top-decile (highest illiquidity, i.e. the Amihud-style premium side) -- a "
                  "disclosed reduction from each paper's long-short factor-portfolio construction.",
        factor_family="Liquidity risk premium (crypto-specific)",
        factor_tags={"liquidity", "crypto_factor"},
        mechanism="Computes an Amihud (2002) -style illiquidity measure per coin -- the trailing average "
                  "of |daily return| divided by daily dollar volume -- then cross-sectionally sorts coins "
                  "long the most illiquid. Ali-Peng-Shams build a dedicated crypto illiquidity factor "
                  "(their 'CIHML', across 1,160 coins, January 2014-December 2022) as part of a four-factor "
                  "crypto asset-pricing model and find it survives controlling for their own crypto size "
                  "and reversal factors; Zhang-Li independently corroborate a priced crypto liquidity-risk "
                  "premium using a related measure. The SAME broad mechanism as this program's own "
                  "already-tested Amihud Illiquidity Premium (SW-010, PASS with a conflicting robustness "
                  "REJECT) -- deliberately so: this is the crypto-market application of a factor family "
                  "already proven real and tradeable (if imperfectly robust) on this program's own India "
                  "equity data, now with independent crypto-specific supporting literature, rather than an "
                  "untested extrapolation.",
        typical_holding_period="Monthly, matching the standard crypto cross-sectional factor-sort cadence "
                                "used throughout this literature (and this program's own crypto_size_factor "
                                "and crypto_xs_momentum candidates/strategies).",
        holding_days_min=25, holding_days_max=35,
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history", "volume"],
        known_strengths="Fully implementable TODAY from data this program already fetches (crypto daily "
                        "OHLCV plus Volume, both already part of data/fetch_crypto.py's klines pull) -- no "
                        "new data source needed. Reuses this program's existing Amihud-ratio computation "
                        "logic (already built and live for the India-equity Amihud Illiquidity Premium "
                        "strategy) rather than needing new signal machinery from scratch. Two independent "
                        "recent studies (2023 and 2025) converge on a priced crypto liquidity-risk premium, "
                        "a real replication rather than a single isolated finding.",
        known_weaknesses="Directly shares this program's 'liquidity' factor tag with the already-"
                         "implemented, PASS-verdicted (but robustness-REJECTed on a supplementary check) "
                         "India-equity Amihud Illiquidity Premium -- meaningful diversification overlap by "
                         "design, though a different asset class and market than that existing strategy. "
                         "Amihud-style measures are known to be sensitive to EXCHANGE/VENUE choice in "
                         "crypto specifically -- reported trading volume differs materially across "
                         "exchanges, and wash-trading concerns on some venues are a documented, real issue "
                         "in this literature (this platform's own data/fetch_crypto.py uses Binance only, "
                         "one of the more scrutinized/regulated large venues, which mitigates but does not "
                         "eliminate this concern). The crypto illiquidity and crypto size factors are also "
                         "correlated in the source literature, so a future crypto_size_factor "
                         "implementation (also on this roadmap, currently blocked) would need to control "
                         "for this overlap rather than treat both as fully independent.",
        academic_replication_quality="Two independent, recent (2023, 2025) studies in real peer-reviewed "
                                      "journals (Wiley's International Journal of Finance & Economics and "
                                      "Emerald's China Accounting and Finance Review) find a materially "
                                      "consistent crypto liquidity premium using related but distinct "
                                      "measures -- genuine, if still young, convergence.",
        evidence_sufficiency_note="Sufficient to justify research time given two corroborating recent "
                                   "studies, full data availability, and directly reusable existing "
                                   "Amihud-computation infrastructure from this program's own India-equity "
                                   "strategy.",
        academic_evidence_score=6, expected_robustness_score=5, operational_simplicity_score=8,
        research_value_score=6, data_availability_score=10, implementation_feasibility_score=8,
        horizon_lane="crypto", market="Global",
    ),
    CandidateProfile(
        key="crypto_funding_rate_carry",
        name="Cryptocurrency Perpetual-Futures Funding-Rate Carry",
        authors="He, S., Manela, A., Ross, O. and von Wachter, V.",
        publication="\"Fundamentals of Perpetual Futures,\" working paper, arXiv:2212.06888 / SSRN 4301150 "
                     "(first draft December 2022, most recent revision confirmed live on arXiv 2026-10-03) "
                     "-- verified real via WebSearch 2026-10-03 (confirmed authors, arXiv/SSRN identifiers, "
                     "and that it is a real, actively-cited working paper on crypto perpetual-futures "
                     "pricing, NOT yet published in a peer-reviewed journal as of this search), not from "
                     "memory",
        year=2022,
        asset_class="Cryptocurrency perpetual futures vs. spot, relative-value",
        direction="Structurally a basis/carry trade (long spot, short the perpetual future, or vice versa "
                  "depending on the funding rate's sign) -- not a cash-only long position the way every "
                  "other crypto candidate on this roadmap can be reduced to; there is no meaningful "
                  "long-only cash-equity-style adaptation, the same structural issue already disclosed for "
                  "this program's Pairs Trading / Statistical Arbitrage candidate.",
        factor_family="Carry / basis (crypto derivatives)",
        factor_tags={"carry_basis", "crypto_factor"},
        mechanism="Perpetual futures (the dominant crypto derivative, over $100 billion traded daily per "
                  "the paper) use a periodic FUNDING-RATE payment between long and short holders to keep "
                  "the perpetual's price anchored to spot; the paper derives the no-arbitrage pricing "
                  "implications and shows the funding-rate payment is itself a harvestable, quantifiable "
                  "risk premium -- a genuinely different mechanism TYPE from every other candidate on this "
                  "entire roadmap (a derivatives-funding-structure premium, not a price pattern, "
                  "fundamentals signal, or risk-based cross-sectional sort). The first candidate in the "
                  "crypto lane keyed on derivatives market structure rather than spot price/volume history.",
        typical_holding_period="Funding settles every 8 hours on most exchanges including Binance; carry-"
                                "strategy implementations in the related applied literature typically "
                                "rebalance daily to weekly.",
        holding_days_min=1, holding_days_max=7,
        expected_trade_frequency="High (funding accrues every 8 hours; a real implementation would need "
                                  "to decide how often to actually re-enter/exit, not necessarily every "
                                  "settlement)",
        data_requirements=["daily_ohlcv_history", "crypto_funding_rate_history"],
        known_strengths="A genuinely NEW mechanism type for this roadmap's crypto lane -- every other "
                        "crypto candidate (implemented or proposed) is a spot-price/volume-based signal; "
                        "this is the first keyed on derivatives market structure. The underlying funding-"
                        "rate mechanism itself is real, large (the paper cites >$100bn/day in perpetual-"
                        "futures volume), and increasingly studied in both academic and practitioner crypto-"
                        "finance circles, not an obscure or fringe claim.",
        known_weaknesses="NOT YET A PEER-REVIEWED, PUBLISHED PAPER -- a working paper (arXiv/SSRN, first "
                         "circulated 2022, still being revised as of this search) with a real but "
                         "comparatively thin citation record so far, a materially weaker evidentiary bar "
                         "than this program's other, journal-published crypto source papers; disclosed "
                         "honestly here rather than overstated. DOUBLY BLOCKED, not just a data gap: (1) "
                         "no funding-rate data source is integrated anywhere in this program (confirmed "
                         "absent, see DATA_CAPABILITIES' new crypto_funding_rate_history entry), and (2) "
                         "even if that data existed, this platform has NO perpetual-futures/derivatives "
                         "execution path at all -- the same structural 'new asset class, not just a new "
                         "signal' blocker already disclosed for the Options-Based Volatility Risk Premium "
                         "candidate, compounding the data gap rather than being solved by it alone. Funding-"
                         "rate dynamics are also documented as regime-dependent and prone to sign flips "
                         "during high-leverage/high-volatility periods, a real robustness concern even "
                         "setting the infrastructure gap aside.",
        academic_replication_quality="A single, real, actively-circulated but not yet peer-reviewed-"
                                      "published working paper -- the weakest sourcing of any candidate "
                                      "added to this roadmap this run, included because the underlying "
                                      "funding-rate mechanism itself is real, large, and distinct, not "
                                      "because the paper has the same evidentiary weight as this program's "
                                      "journal-published sources.",
        evidence_sufficiency_note="Sufficient to catalogue as a real, distinct, genuinely novel-mechanism "
                                   "candidate for completeness and future reference, explicitly NOT "
                                   "sufficient yet to treat as equivalent in evidentiary weight to this "
                                   "program's peer-reviewed crypto factor candidates -- flagged as "
                                   "DOUBLY blocked (data AND execution infrastructure) and should be "
                                   "re-checked for journal publication before any future research time is "
                                   "spent on it.",
        academic_evidence_score=4, expected_robustness_score=3, operational_simplicity_score=3,
        research_value_score=7, data_availability_score=0, implementation_feasibility_score=0,
        horizon_lane="crypto", market="Global",
    ),
    CandidateProfile(
        key="us_price_delay_factor",
        name="Price Delay Factor (Slow Information Diffusion)",
        authors="Hou, K. and Moskowitz, T.J.",
        publication="\"Market Frictions, Price Delay, and the Cross-Section of Expected Returns,\" The "
                     "Review of Financial Studies, Vol. 18, No. 3, 981-1020 (2005) -- verified real via "
                     "WebSearch 2026-10-03 (confirmed authors, journal, volume/pages and year, and the "
                     "paper's continued citation record two decades on), not from memory",
        year=2005,
        asset_class="Single-stock equities (US, S&P 500 universe), cross-sectional",
        direction="Long-only top-decile (highest price delay) -- the side the paper's return premium "
                  "accrues to, a disclosed reduction from its long-short construction.",
        factor_family="Price delay / information-diffusion speed",
        factor_tags={"price_delay"},
        mechanism="For each stock, regress its own weekly returns on the CONTEMPORANEOUS market-index "
                  "return AND several weeks of LAGGED market-index returns, over a rolling ~3-year window; "
                  "'delay' is the fraction of that regression's explanatory power that comes ONLY from the "
                  "lagged terms -- i.e., how much of the stock's price reaction to market-wide information "
                  "arrives with a measurable lag rather than immediately. Stocks with the highest delay "
                  "(slowest information diffusion, attributed by the authors mainly to investor-recognition "
                  "frictions -- smaller, less-followed names) earn a large subsequent return premium not "
                  "explained by size, liquidity, or microstructure effects the authors directly tested "
                  "against. A GENUINELY NEW mechanism family for this entire roadmap, India and US "
                  "candidates combined: every existing momentum/reversal/trend candidate measures a "
                  "stock's OWN past return; this instead measures the LAG STRUCTURE of a stock's "
                  "co-movement with the broad market -- a measure of information-processing speed, not of "
                  "price direction at all. Also mechanically distinct from this program's beta-based "
                  "risk-based candidates (Betting Against Beta, Downside Beta, Jensen's Alpha): those use "
                  "a CONTEMPORANEOUS regression coefficient (slope or intercept) at a single lag; this uses "
                  "the regression's LAGGED-term structure specifically.",
        typical_holding_period="The original paper reports monthly-rebalanced portfolios held 1 month as "
                                "its primary result, with a longer annual-holding-period version also "
                                "reported as a robustness check.",
        holding_days_min=25, holding_days_max=35,
        expected_trade_frequency="Moderate",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="A single but top-tier, heavily-cited (two decades and counting) journal (Review "
                        "of Financial Studies) source -- as strong an evidentiary bar as this program's "
                        "best-sourced existing candidates. Fully computable from plain daily/weekly OHLCV "
                        "already fetched for this program's frozen S&P 500 universe (swing_research/"
                        "universe_us.py) PLUS a market-index return series buildable from that same "
                        "universe (an equal- or cap-weighted composite of the existing universe, or a "
                        "single broad-index ETF ticker such as SPY via the same yfinance pipeline) -- no "
                        "new data source needed, unlike this platform's existing Dogs of the Dow (blocked "
                        "on dividend data) or every fundamentals-based candidate on this roadmap. The first "
                        "genuinely new factor MECHANISM proposed for the US lane specifically since it was "
                        "stood up (as distinct from applying an already-catalogued mechanism to US data).",
        known_weaknesses="Needs a rolling ~3-YEAR weekly regression window per stock before a signal can "
                         "even be formed -- a real, disclosed engineering lift (this program has no "
                         "existing rolling-regression-with-multiple-lagged-terms machinery; the closest "
                         "existing infrastructure, the single-lag CAPM-style regressions built for Jensen's "
                         "Alpha and Betting Against Beta, would need extending, not reusing as-is) and it "
                         "meaningfully shortens the usable backtest window versus a simple decile-sort "
                         "signal, similar in spirit to the long-formation-window concern already disclosed "
                         "for this roadmap's Long-Term Reversal candidate. The authors themselves find "
                         "delay correlates with size, analyst coverage, and institutional ownership -- they "
                         "argue investor-recognition frictions, not these other characteristics, drive the "
                         "premium, and control for it in the paper, but a from-scratch implementation here "
                         "would need to re-verify that the premium survives on this platform's own S&P 500 "
                         "universe and period, not assume the original paper's attribution carries over "
                         "unchanged two decades later. No India-specific or NSE replication of this exact "
                         "measure was found or attempted here -- this candidate is scoped to the US lane "
                         "specifically, not proposed as a transferable India candidate.",
        academic_replication_quality="A single foundational paper in a top-tier journal (Review of "
                                      "Financial Studies), continuously cited across two decades of "
                                      "subsequent market-frictions and information-diffusion literature -- "
                                      "strong standing, though (unlike e.g. momentum or value) it has not "
                                      "spawned the same volume of direct independent replications this "
                                      "program's most foundational candidates have.",
        evidence_sufficiency_note="Sufficient to justify research time -- a real, well-cited, top-journal "
                                   "finding, fully computable from data this program already has for the US "
                                   "lane -- but the rolling-regression engineering lift and the need to "
                                   "re-verify the premium on this platform's own universe/period (rather "
                                   "than assume the 2005 result transfers unchanged) should both be treated "
                                   "as real, open items, not formalities.",
        academic_evidence_score=8, expected_robustness_score=6, operational_simplicity_score=4,
        research_value_score=8, data_availability_score=10, implementation_feasibility_score=6,
        horizon_lane="swing", market="US",
    ),

    # =================================================================================
    # CROSS-MARKET PORTS -- added 2026-10-03 per explicit direction ("the seed feeder should also
    # consider [strategies already tested for Indian markets]... because we can use them as well for
    # us equity"). Each of the six below is a mechanism this program ALREADY researched on NSE data
    # and registered with a PASS, proposed here as a separate US-lane candidate.
    #
    # Why this is real research and not bookkeeping: every one of these six was ORIGINALLY documented
    # on US data (Jegadeesh JF 1990, Bali/Cakici/Whitelaw JFE 2011, Ariel JFE 1987, Lou/Polk/Skouras
    # JFE 2019, Gervais/Kaniel/Mingelgrin JF 2001, Frazzini/Lamont 2007 + Barber et al. JFE 2013), so
    # the US evidence is the ORIGINATING evidence -- stronger than the India evidence these were
    # accepted on. And a port genuinely changes the answer: both strategies already taken to Pool I
    # (Minervini, Cross-Sectional Momentum) came back PASS on US data while their India originals sit
    # at INCONCLUSIVE.
    #
    # What a PASS in India does NOT mean: that the US version works. Each still goes through the same
    # unmodified walk-forward pipeline and earns its own PASS/REJECT on real US data, exactly as
    # directed for Pool I ("backtest the strategies in the US markets and then pass/fail and then
    # promote to paper trading"). The India verdict is recorded below as context, never as evidence.
    # =================================================================================
    CandidateProfile(
        key="us_short_term_reversal",
        name="Short-Term Reversal (US)",
        authors="Jegadeesh, N.",
        publication="\"Evidence of Predictable Behavior of Security Returns,\" The Journal of Finance, "
                     "Vol. 45, No. 3 (1990) -- the citation already recorded for this program's own India "
                     "implementation (swing_research/published_research_analyst.py, SHORT_TERM_REVERSAL), "
                     "reused verbatim rather than re-sourced; the paper's sample is US (NYSE/AMEX).",
        year=1990,
        asset_class="Single-stock equities (US, S&P 500 universe), cross-sectional",
        direction="Long-only bottom decile (prior-month losers) -- the same disclosed reduction from the "
                  "paper's zero-cost long-short construction already applied to the India version.",
        factor_family="Short-horizon reversal",
        factor_tags={"reversal_short_horizon"},
        mechanism="Significant NEGATIVE autocorrelation in individual stock returns at weekly-to-monthly "
                  "lags: stocks with the worst returns over the prior month subsequently outperform. "
                  "Attributed to liquidity provision -- a short-horizon loser is often a stock that absorbed "
                  "selling pressure, and the reversal is the compensation paid to whoever supplied that "
                  "liquidity. Identical mechanism to this program's registered India strategy "
                  "'short_term_reversal' (PASS, paper trading); the open question is purely whether it "
                  "survives on S&P 500 large caps, which is a different liquidity environment entirely from "
                  "the NSE universe it passed on.",
        typical_holding_period="One month: prior-1-month formation, 1-month hold, the paper's headline and "
                                "most-replicated specification.",
        holding_days_min=25, holding_days_max=35,
        expected_trade_frequency="High -- full monthly rotation of the book.",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Foundational, heavily-cited top-journal source (Journal of Finance), and the US "
                        "sample IS the paper's own sample -- this is the factor's home market, not a "
                        "transplant. Needs nothing beyond the daily OHLCV already fetched for the frozen "
                        "S&P 500 universe (swing_research/universe_us.py). Implementation is close to free: "
                        "the India Strategy class already exists and, like every Strategy subclass here, "
                        "contains no NSE-specific code -- the two existing Pool I ports reused their India "
                        "classes completely unmodified, so this is a new experiment over existing code "
                        "rather than a new strategy to write.",
        known_weaknesses="The weakest of the six cross-market ports on expected robustness, and honestly so. "
                         "Short-term reversal is among the most heavily documented DECAYED anomalies in US "
                         "equities post-2000, and the surviving premium is concentrated in small, illiquid, "
                         "high-spread names -- close to the opposite of an S&P 500 large-cap universe, which "
                         "is the only US universe this program has. It also demands full monthly rotation, "
                         "so it is the most transaction-cost-sensitive candidate here; the realistic "
                         "expectation is that costs modelled by execution_realism_engine.py eat a large "
                         "share of whatever gross premium remains. India PASSing says nothing about this: "
                         "the NSE universe it passed on is far less liquid than the S&P 500.",
        academic_replication_quality="Extensively replicated across decades and markets, including the "
                                      "well-documented post-publication decay in US large caps -- the "
                                      "replication record here cuts both ways and is cited above in full.",
        evidence_sufficiency_note="Sufficient to research, NOT to expect a PASS. The value is largely in "
                                   "getting a real US verdict on a mechanism already live for India: a "
                                   "REJECT here would be a genuinely useful, cost-informed result about "
                                   "large-cap liquidity provision, not a wasted cycle.",
        academic_evidence_score=9, expected_robustness_score=3, operational_simplicity_score=8,
        research_value_score=7, data_availability_score=10, implementation_feasibility_score=8,
        horizon_lane="swing", market="US",
    ),
    CandidateProfile(
        key="us_max_effect",
        name="MAX Effect / Lottery-Demand Anomaly (US)",
        authors="Bali, T.G., Cakici, N. and Whitelaw, R.F.",
        publication="\"Maxing Out: Stocks as Lotteries and the Cross-Section of Expected Returns,\" Journal "
                     "of Financial Economics, Vol. 99, No. 2 (2011) -- the citation already recorded for "
                     "this program's India implementation (MAX_EFFECT), reused verbatim; the paper's sample "
                     "is US (CRSP).",
        year=2011,
        asset_class="Single-stock equities (US, S&P 500 universe), cross-sectional",
        direction="Long-only LOW-MAX decile -- the paper's premium accrues to avoiding the lottery-like "
                  "names; same long-only reduction as the India version.",
        factor_family="Behavioural / lottery demand",
        factor_tags={"behavioral_lottery"},
        mechanism="Investors overpay for stocks with lottery-like payoffs. Sorting on MAX -- the single "
                  "largest daily return in the prior month -- isolates exactly those names, and the "
                  "highest-MAX stocks subsequently UNDERPERFORM. The premium is therefore earned by holding "
                  "the lowest-MAX names. Same mechanism as the registered India strategy 'max_effect' "
                  "(PASS, paper trading).",
        typical_holding_period="One month: MAX measured over the prior month, monthly rebalance.",
        holding_days_min=25, holding_days_max=35,
        expected_trade_frequency="Moderate to high -- monthly rebalance.",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Top-tier journal (JFE), very heavily cited, and the US sample is the paper's own. "
                        "Computed from a single column of daily OHLCV already fetched for the US universe -- "
                        "arguably the simplest signal of the six to compute correctly. The India class is "
                        "reusable unmodified, as with every port here.",
        known_weaknesses="MAX is strongly correlated with idiosyncratic volatility, which this program "
                         "already researched for India ('idiosyncratic_volatility', INCONCLUSIVE) -- so a US "
                         "result here is partly a re-test of a mechanism this program has already found "
                         "ambiguous once, in another market. More seriously for the only US universe "
                         "available here: the lottery-demand effect is documented as strongest in small, "
                         "cheap, retail-held stocks, and the S&P 500 is the large-cap, institution-dominated "
                         "end of exactly that spectrum, where the effect is weakest. Several post-2011 "
                         "studies also attribute much of MAX's power to its IVOL correlation rather than to "
                         "lottery demand as a distinct channel.",
        academic_replication_quality="Widely replicated internationally and the subject of an active "
                                      "literature on whether MAX is distinct from idiosyncratic volatility "
                                      "-- strong standing, with a genuine unresolved attribution debate.",
        evidence_sufficiency_note="Sufficient to research. The honest expectation is a weaker US large-cap "
                                   "result than the paper's full-CRSP headline, and the research value is as "
                                   "much in the IVOL-overlap question as in the raw verdict.",
        academic_evidence_score=8, expected_robustness_score=4, operational_simplicity_score=8,
        research_value_score=7, data_availability_score=10, implementation_feasibility_score=8,
        horizon_lane="swing", market="US",
    ),
    CandidateProfile(
        key="us_turn_of_month",
        name="Turn-of-the-Month Effect (US)",
        authors="Ariel, R.A.",
        publication="\"A Monthly Effect in Stock Returns,\" Journal of Financial Economics, Vol. 18, No. 1 "
                     "(1987) -- the citation already recorded for this program's India implementation "
                     "(TURN_OF_MONTH), reused verbatim; the paper's sample is US.",
        year=1987,
        asset_class="US equity index / broad universe exposure, calendar-timed",
        direction="Long-only, in and out on fixed calendar dates -- no cross-sectional selection at all.",
        factor_family="Calendar seasonality",
        factor_tags={"seasonality_calendar"},
        mechanism="US equity returns concentrate almost entirely in a narrow window spanning the last "
                  "trading days of one month and the first few of the next; the rest of the month "
                  "contributes close to nothing on average. Commonly attributed to recurring cash flows -- "
                  "salary, pension and fund inflows clustering at month boundaries. Same mechanism as the "
                  "registered India strategy 'turn_of_month' (PASS, paper trading).",
        typical_holding_period="A few days per month, entered and exited on fixed calendar dates.",
        holding_days_min=3, holding_days_max=8,
        expected_trade_frequency="Low -- one entry and one exit per month, fully scheduled in advance.",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="The simplest and cheapest of the six to run by a wide margin: no ranking, no "
                        "regression, no estimation window, and the book sits in cash most of the month, so "
                        "capital is free for other US strategies between windows. Foundational JFE source "
                        "on US data, and the India version already PASSed on the same engine.",
        known_weaknesses="Calendar anomalies are the canonical data-mining concern in this literature, and "
                         "this one has been publicly known and tradeable for nearly four decades -- "
                         "post-publication decay is well documented and several studies find the US effect "
                         "substantially weaker after 2000. It also offers no mechanism that would survive "
                         "being arbitraged, unlike a risk- or liquidity-based premium. Because the signal is "
                         "purely a date, a US result adds little NEW mechanistic knowledge beyond confirming "
                         "or denying survival -- which is why its research-value score is the lowest of the "
                         "six despite its operational simplicity being the highest.",
        academic_replication_quality="Replicated across many markets and decades, with an equally "
                                      "well-documented post-publication weakening in the US specifically.",
        evidence_sufficiency_note="Sufficient to research, cheaply. Worth running precisely BECAUSE it is "
                                   "nearly free to test and occupies almost no capital-time; not worth "
                                   "prioritising over the mechanistically richer candidates in this lane.",
        academic_evidence_score=7, expected_robustness_score=4, operational_simplicity_score=9,
        research_value_score=6, data_availability_score=10, implementation_feasibility_score=8,
        horizon_lane="swing", market="US",
    ),
    CandidateProfile(
        key="us_overnight_return_anomaly",
        name="Overnight Return Anomaly (US)",
        authors="Lou, D., Polk, C. and Skouras, S.; Berkman, D., Koch, P.D., Tuttle, L. and Zhang, S.J.",
        publication="\"A Tug of War: Overnight Versus Intraday Expected Returns,\" Journal of Financial "
                     "Economics, Vol. 134, No. 1 (2019); see also \"Paying Attention: Overnight Returns and "
                     "the Cross-Section of Stock Returns,\" Journal of Finance, Vol. 67, No. 5 (2012) -- the "
                     "citations already recorded for this program's India implementation "
                     "(OVERNIGHT_RETURN_ANOMALY), reused verbatim; both samples are US.",
        year=2019,
        asset_class="Single-stock equities (US, S&P 500 universe), cross-sectional",
        direction="Long-only the high-overnight-return group, same reduction as the India version.",
        factor_family="Microstructure / overnight-intraday decomposition",
        factor_tags={"microstructure_overnight"},
        mechanism="A stock's return splits into an overnight (previous close to open) and an intraday (open "
                  "to close) component, and the two have persistently OPPOSITE cross-sectional predictive "
                  "signs -- different investor clienteles dominate the two windows, with retail and "
                  "attention-driven demand concentrated at the open. A stock's past overnight returns "
                  "predict its future overnight returns, and the effect is not explained by the total daily "
                  "return. Same mechanism as the registered India strategy 'overnight_return_anomaly' "
                  "(PASS, paper trading).",
        typical_holding_period="Overnight to a few days, depending on the rebalance cadence chosen; the "
                                "signal itself is formed from a trailing window of overnight returns.",
        holding_days_min=1, holding_days_max=5,
        expected_trade_frequency="High.",
        data_requirements=["daily_ohlcv_history"],
        known_strengths="Two independent top-tier papers (JFE 2019 and JF 2012) pointing the same way, both "
                        "on US data -- among the better-evidenced candidates in this lane. Needs only the "
                        "OPEN and CLOSE columns this program already fetches for the US universe; the "
                        "decomposition is arithmetic, with no estimation window. The India version PASSed on "
                        "this same engine.",
        known_weaknesses="The one candidate here with a real STRUCTURAL question to settle before the "
                         "backtest can be trusted, and it must be settled rather than assumed: the entire "
                         "return being harvested IS the close-to-open gap, so the result is unusually "
                         "sensitive to fill timing, and Pool I is configured fill_timing='next_day_open'. "
                         "Whether that configuration can faithfully express an overnight hold -- or whether "
                         "the strategy as implemented for India is actually capturing something subtly "
                         "different -- needs checking against the India implementation and "
                         "execution_realism_engine.py FIRST, because a backtest that silently mismodels the "
                         "fill would produce a meaningless PASS. Beyond that: US open auctions are "
                         "crowded and spreads are widest at the open, so realistic slippage is a serious "
                         "threat to a premium measured in tens of basis points.",
        academic_replication_quality="Strong and recent -- two separate top-journal treatments plus an "
                                      "active follow-on literature on overnight/intraday decomposition.",
        evidence_sufficiency_note="Sufficient to research, with the fill-timing question treated as a "
                                   "genuine prerequisite rather than a caveat to note afterwards.",
        academic_evidence_score=8, expected_robustness_score=5, operational_simplicity_score=6,
        research_value_score=8, data_availability_score=10, implementation_feasibility_score=7,
        horizon_lane="swing", market="US",
    ),
    CandidateProfile(
        key="us_high_volume_return_premium",
        name="High-Volume Return Premium (US)",
        authors="Gervais, S., Kaniel, R. and Mingelgrin, D.H.",
        publication="\"The High-Volume Return Premium,\" The Journal of Finance, Vol. 56, No. 3 (2001) -- "
                     "the citation already recorded for this program's India implementation "
                     "(HIGH_VOLUME_RETURN_PREMIUM), reused verbatim; the paper's sample is US (NYSE). "
                     "Independently re-surfaced as a US candidate by the 2026-10-03 Discovery Scout run, "
                     "which then dropped it as 'already implemented' -- the market-blind deduplication this "
                     "entry exists to correct.",
        year=2001,
        asset_class="Single-stock equities (US, S&P 500 universe), cross-sectional",
        direction="Long-only the high-volume group, same reduction as the India version.",
        factor_family="Volume / investor attention",
        factor_tags={"volume_attention"},
        mechanism="Stocks experiencing unusually HIGH trading volume over a short formation window "
                  "subsequently outperform over the following weeks, and unusually low-volume stocks "
                  "underperform. The authors attribute it to visibility: a volume shock raises a stock's "
                  "salience, drawing in buyers and pushing price up over the following period. Note the "
                  "signal is the volume shock ITSELF, independent of the direction of the accompanying price "
                  "move. Same mechanism as the registered India strategy 'high_volume_return_premium' "
                  "(PASS, paper trading).",
        typical_holding_period="Formation over a short window (the paper uses a day or a week), held for "
                                "roughly the following month.",
        holding_days_min=20, holding_days_max=30,
        expected_trade_frequency="Moderate to high.",
        data_requirements=["daily_ohlcv_history", "volume"],
        known_strengths="Top-tier journal (Journal of Finance), well cited, US sample. Both inputs -- price "
                        "and volume -- are already fetched for the US universe, and volume is confirmed "
                        "available in DATA_CAPABILITIES. The India class is reusable unmodified. "
                        "Mechanistically distinct from everything else proposed for this lane: it keys on a "
                        "volume shock rather than on any past-return or risk measure.",
        known_weaknesses="The headline result rests on 1963-1996 NYSE data, and the modern US market is "
                         "structurally different in exactly the dimension the paper depends on -- volume is "
                         "now fragmented across many venues, and a large share is algorithmic rather than "
                         "attention-driven, so 'unusually high volume' may no longer mean what it meant in "
                         "the sample. Volume figures also need care around splits and index events. A real "
                         "risk that the premium has decayed or changed character rather than simply weakened.",
        academic_replication_quality="Replicated across markets, with an active follow-on attention "
                                      "literature; the modern-microstructure caveat above is the main open "
                                      "question rather than the original finding's validity.",
        evidence_sufficiency_note="Sufficient to research. The honest framing is that this tests whether a "
                                   "1960s-90s attention mechanism still exists in a fragmented, "
                                   "algorithm-dominated US tape -- which is itself a worthwhile answer.",
        academic_evidence_score=8, expected_robustness_score=5, operational_simplicity_score=7,
        research_value_score=7, data_availability_score=10, implementation_feasibility_score=8,
        horizon_lane="swing", market="US",
    ),
    CandidateProfile(
        key="us_earnings_announcement_premium",
        name="Earnings Announcement Premium (US)",
        authors="Frazzini, A. and Lamont, O.A.; Barber, B.M., De George, E.T., Lehavy, R. and Trueman, B.",
        publication="\"The Earnings Announcement Premium and Trading Volume,\" NBER Working Paper 13090 "
                     "(2007); \"The earnings announcement premium around the globe,\" Journal of Financial "
                     "Economics 108(1), 118-138 (2013) -- the citations already recorded for this program's "
                     "India implementation (EARNINGS_ANNOUNCEMENT_PREMIUM), reused verbatim. The 2013 JFE "
                     "paper's international sample includes the US.",
        year=2007,
        asset_class="Single-stock equities (US, S&P 500 universe), cross-sectional",
        direction="Long-only expected announcers, same reduction as the India version (which drops the "
                  "short leg for lack of cash-market shorting infrastructure).",
        factor_family="Earnings event / announcement premium",
        factor_tags={"earnings_drift", "seasonality_calendar"},
        mechanism="Stocks earn abnormally high returns in the MONTH they are predicted to report earnings -- "
                  "a premium for holding the announcement risk, concentrated in names that attract the most "
                  "attention and volume around the event. Distinct from post-earnings-announcement drift: "
                  "the position is taken BEFORE the announcement based on a predictable schedule, not after "
                  "it based on the surprise. Same mechanism as the registered India strategy "
                  "'earnings_announcement_premium' (PASS, paper trading).",
        typical_holding_period="About one month, around the predicted announcement.",
        holding_days_min=20, holding_days_max=35,
        expected_trade_frequency="Moderate, clustered into earnings season.",
        data_requirements=["daily_ohlcv_history", "volume", "earnings_announcement_dates"],
        known_strengths="A JFE paper plus a widely-cited NBER working paper, with the 2013 international "
                        "study explicitly covering the US. The announcement-date pipeline already exists and "
                        "is market-agnostic: data/fetch_earnings_calendar.py wraps yfinance's "
                        "get_earnings_dates(), which takes bare US tickers exactly as fetch_historical.py "
                        "does, and is already relied on in production by two registered India strategies. "
                        "Earnings-date coverage and reliability for S&P 500 constituents should be BETTER "
                        "than for the NSE universe this passed on, not worse.",
        known_weaknesses="The binding constraint is history depth, and it is a real one: "
                         "EARNINGS_HISTORY_LIMIT is 16 quarters, so roughly four years of announcement "
                         "dates are available. That is enough for this program's walk-forward windows but "
                         "materially shorter than the backtest span available to every other candidate in "
                         "this lane, which means a weaker, more period-dependent verdict -- four years can "
                         "easily be one regime. The source is also unofficial and day-level ambiguous (that "
                         "module discloses this itself). Operationally this is the most complex of the six: "
                         "it needs the date pipeline, a volume-concentration ranking, and the 'exactly four "
                         "announcements in the prior twelve months' filter, so it is the one port where the "
                         "India class may genuinely need US-specific review rather than straight reuse.",
        academic_replication_quality="Replicated internationally by the 2013 JFE study across many markets "
                                      "including the US -- good standing, though the premium's size varies "
                                      "considerably by market and period.",
        evidence_sufficiency_note="Sufficient to research, but it should be sequenced LAST of the six: the "
                                   "four-year date history caps how confident any verdict can be, so the "
                                   "cheaper, deeper-history ports are better use of the queue first.",
        academic_evidence_score=7, expected_robustness_score=6, operational_simplicity_score=5,
        research_value_score=7, data_availability_score=7, implementation_feasibility_score=6,
        horizon_lane="swing", market="US",
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


RESEARCH_LANES = ("india", "crypto", "us")


def lane_of(candidate: CandidateProfile) -> str:
    """Which of the three independent research queues (research_queue.py, added 2026-10-03, per
    explicit direction: "shall we also build this same auto research for crypto and us equity?...
    each section runs 1 or 2 strategies research per week at different times") a candidate belongs
    to -- horizon_lane == "crypto" takes priority (crypto isn't a national market, so `market` is
    irrelevant for it), then market == "US", else "india" -- which is every pre-existing candidate
    today, unchanged, since both fields default to the India-equity values."""
    if candidate.horizon_lane == "crypto":
        return "crypto"
    if candidate.market == "US":
        return "us"
    return "india"


def build_roadmap(registry_path: str = REGISTRY_PATH, weights: dict = DEFAULT_WEIGHTS,
                  lane: Optional[str] = None) -> dict:
    """
    Scores every CANDIDATE (optionally restricted to one research lane -- see lane_of() -- so each
    lane's own queue only ever ranks/picks within its own candidate pool; None, the default, scores
    every candidate across every lane together, unchanged from before lanes existed, for every
    existing caller that doesn't pass this) against the LIVE deployment registry (so
    diversification scoring always reflects the platform's actual current
    state, not a stale snapshot) and splits the result into:
      researchable_now -- ranked descending by total_score, everything
                           classify_data_feasibility() didn't block.
      deferred_pending_data -- NOT_CURRENTLY_IMPLEMENTABLE candidates,
                           unranked (their score isn't a meaningful
                           priority signal since they can't be started).
      paper_direct_eligible -- a SUBSET of deferred_pending_data (2026-09-22,
                           per explicit direction: "paper trading is also
                           part of research and not real money... with the
                           results of paper trading we can decide whether
                           to give real money"): candidates that can't get a
                           real historical backtest, but whose score clears
                           PAPER_DIRECT_SCORE_THRESHOLD -- a fixed absolute
                           quality bar (2026-09-22: an earlier version tied
                           this to "at least as good as the worst backtestable
                           candidate running right now", which was rejected
                           as too permissive since that floor just tracks
                           whatever's currently in the roadmap rather than
                           reflecting real quality). These are eligible for
                           research_queue.py to route straight to a
                           paper-trading proposal instead of a backtest,
                           skipping neither the evidence bar nor human review
                           (still a PR, never auto-merged) -- just the
                           backtest step that's genuinely impossible for them.
      all_scored -- every ScoredCandidate, for the full comparison table.
    """
    portfolio = load_portfolio(registry_path)
    candidates = CANDIDATES if lane is None else [c for c in CANDIDATES if lane_of(c) == lane]
    scored = [score_candidate(c, portfolio, weights) for c in candidates]
    researchable_now = sorted(
        (s for s in scored if s.feasibility_classification != "NOT_CURRENTLY_IMPLEMENTABLE"
         and s.candidate.key not in DEFERRED_BY_DIRECTION),
        key=lambda s: -s.total_score,
    )
    deferred_pending_data = [s for s in scored if s.feasibility_classification == "NOT_CURRENTLY_IMPLEMENTABLE"]
    deferred_by_direction = [s for s in scored if s.candidate.key in DEFERRED_BY_DIRECTION
                             and s.feasibility_classification != "NOT_CURRENTLY_IMPLEMENTABLE"]
    paper_direct_eligible = sorted(
        (s for s in deferred_pending_data if s.total_score > PAPER_DIRECT_SCORE_THRESHOLD),
        key=lambda s: -s.total_score,
    )
    return {
        "portfolio": portfolio, "researchable_now": researchable_now,
        "deferred_pending_data": deferred_pending_data, "deferred_by_direction": deferred_by_direction,
        "paper_direct_eligible": paper_direct_eligible,
        "all_scored": scored, "weights": weights,
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
