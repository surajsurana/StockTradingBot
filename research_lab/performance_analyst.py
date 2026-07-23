"""
Performance Analyst -- all the actual numbers below are computed by pure
deterministic code first (monthly returns already come from
backtesting_engineer.compute_metrics(); sector/time-of-day/regime
breakdowns are computed here). Claude's role is strictly downstream: it
receives those already-computed numbers as structured input and writes
the prose interpretation. It never computes a metric itself and cannot
alter the Statistical Auditor's verdict -- explain() takes the verdict as
an already-decided fact to comment on, not something it evaluates.

Sector data reuses data/nifty500_constituents.csv's existing Industry
column (already-proven infra from earlier this session). Market regime
reuses strategies/market_regime.py READ-ONLY (build_regime_series/
is_bullish_on) -- same "common infrastructure" reuse already established,
never modifying that module.
"""

import csv
import os
from typing import Callable, Optional

from news.news_agent import call_claude

NIFTY500_CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "nifty500_constituents.csv")


def load_sector_map(csv_path: str = NIFTY500_CSV_PATH) -> dict:
    """{bare_symbol: industry}, e.g. {"RELIANCE": "Oil Gas & Consumable Fuels"}."""
    if not os.path.exists(csv_path):
        return {}
    mapping = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            symbol = row.get("Symbol", "").strip()
            industry = row.get("Industry", "").strip()
            if symbol:
                mapping[symbol] = industry
    return mapping


def compute_sector_breakdown(trades: list, sector_map: dict) -> dict:
    breakdown = {}
    for t in trades:
        sector = sector_map.get(t.symbol, "Unknown")
        breakdown[sector] = breakdown.get(sector, 0.0) + t.pnl
    return {k: round(v, 2) for k, v in sorted(breakdown.items(), key=lambda kv: -kv[1])}


def compute_time_of_day_breakdown(trades: list, bucket_hours: float = 1.0) -> dict:
    """Buckets by entry hour, e.g. 9.0 = the 9:00-9:59 bucket. Trades with
    no entry_hour recorded are skipped, not silently mis-bucketed."""
    breakdown = {}
    for t in trades:
        if t.entry_hour is None:
            continue
        bucket = int(t.entry_hour // bucket_hours) * bucket_hours
        breakdown[bucket] = breakdown.get(bucket, 0.0) + t.pnl
    return {k: round(v, 2) for k, v in sorted(breakdown.items())}


def compute_regime_breakdown(trades: list, regime_series) -> dict:
    """regime_series: from strategies/market_regime.py's build_regime_series
    (read-only reuse, never modified). Buckets each trade's P&L by whether
    the broader Nifty was bullish or bearish on its entry_date."""
    from strategies.market_regime import is_bullish_on

    breakdown = {"bullish": 0.0, "bearish": 0.0, "unknown": 0.0}
    for t in trades:
        try:
            bucket = "bullish" if is_bullish_on(regime_series, t.entry_date) else "bearish"
        except Exception:
            bucket = "unknown"
        breakdown[bucket] += t.pnl
    return {k: round(v, 2) for k, v in breakdown.items()}


def build_narrative_prompt(hypothesis_name: str, verdict: dict, metrics: dict,
                            sector_breakdown: dict, time_of_day_breakdown: dict,
                            regime_breakdown: dict) -> str:
    return f"""You are the Performance Analyst for an NSE cash-equity intraday strategy research \
lab. The Statistical Auditor has ALREADY decided the verdict below -- your job is only to explain \
WHY, using the numbers provided. You cannot change the verdict, only interpret it. Do not \
recommend deploying or rejecting anything yourself; that decision is already made.

Hypothesis: {hypothesis_name}
Auditor's verdict: {verdict.get('decision')}
Auditor's reasoning: {verdict.get('reasoning')}

Overall metrics: {metrics}
P&L by sector: {sector_breakdown}
P&L by entry-hour bucket: {time_of_day_breakdown}
P&L by market regime (bullish/bearish Nifty on entry day): {regime_breakdown}

Write a short analysis (150-250 words) covering: why the strategy likely performed this way given \
the mechanism, which sectors/times/regimes it depended on (or was hurt by), and 1-2 concrete \
follow-up ideas for a future hypothesis -- whether this one passed or was rejected. Be specific and \
grounded in the numbers above, not generic."""


def explain(hypothesis_name: str, verdict: dict, metrics: dict, sector_breakdown: dict,
            time_of_day_breakdown: dict, regime_breakdown: dict, api_key: str = "",
            call_fn: Optional[Callable[[str], str]] = None) -> str:
    """Returns the narrative text. Raises ClaudeAPIError on API failure --
    same as quant_researcher.propose_hypotheses(), there's no safe
    fallback narrative to substitute, so a failure here should surface
    rather than silently produce an empty observations.md."""
    prompt = build_narrative_prompt(hypothesis_name, verdict, metrics, sector_breakdown,
                                     time_of_day_breakdown, regime_breakdown)
    call = call_fn or (lambda p: call_claude(p, api_key))
    return call(prompt)
