"""
Research Director -- orchestrates one full experiment as a fixed-order
pipeline of plain function calls, each with a simple input/output
contract:

    research_director.review_research_history()    [Claude -- cross-experiment synthesis]
    -> quant_researcher.propose_hypotheses()        [Claude -- creative]
    -> research_director.hard_filter()             [deterministic]
    -> research_director.rank_hypotheses()         [Claude -- judgment, pre-backtest only]
    -> (Strategy Developer implements the winner -- see NOTE below)
    -> backtesting_engineer.run_backtest() x N walk-forward windows  [deterministic]
    -> statistical_auditor.audit()                 [deterministic, FINAL]
    -> performance_analyst.explain()                [Claude -- narrative only]
    -> experiment_manager.save_experiment()         [deterministic]

GOVERNANCE (enforced by this file's call ordering, not just documented):
the Auditor's PASS/REJECT is decided purely by statistical_auditor.audit()'s
deterministic rules and is computed BEFORE performance_analyst.explain() is
ever called. The narrative is given the verdict as an already-fixed fact
to comment on -- nothing it or any other LLM call in this pipeline
produces can change it.

NOTE on "Strategy Developer": converting a Hypothesis's plain-language
rules into an actual Strategy subclass is a genuine code-authoring step,
not push-button automation -- this project's convention (and Suraj's own
governance rule that signal generation must be deterministic and
reviewed, not LLM-generated at runtime) means this is done by hand in
research_lab/strategies/ after seeing which hypothesis wins, not inside
this pipeline automatically. Because of this, run_experiment.py runs in
two phases: `--propose` (through hard_filter + ranking, stops and reports
the winner) and `--continue` (backtesting through save, once the winning
hypothesis has an actual Strategy implementation).
"""

import re
from typing import Callable, Optional

from research_lab import backtesting_engineer, experiment_manager, performance_analyst, statistical_auditor
from research_lab.base import Strategy
from research_lab.knowledge_base import load_entries, record_conclusion, rejected_mechanisms
from research_lab.performance_analyst import compute_regime_breakdown, compute_sector_breakdown, \
    compute_time_of_day_breakdown, load_sector_map
from research_lab.quant_researcher import Hypothesis, propose_hypotheses
from research_lab.risk_manager_research import RiskParameters

# Cash-equity research lab has no access to exchange-wide breadth data
# (advance-decline ratios etc.) and PART 3 explicitly excludes
# futures/options -- flagged here rather than silently attempted.
INFEASIBLE_DATA_KEYWORDS = [
    "advance-decline", "advance decline", "market breadth", "tick index",
    "options", "futures", "open interest", "put-call ratio", "put call ratio",
]

_STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "with", "is",
              "this", "that", "its", "for", "as", "by", "from", "not", "than"}


def build_review_prompt(entries: list) -> str:
    blocks = "\n".join(
        f"- [{e.exp_id}] {e.hypothesis_name} -- verdict: {e.verdict}. "
        f"Mechanism: {e.mechanism_summary} Reason: {e.key_reason}"
        for e in entries
    )
    return f"""You are the Research Director for an NSE cash-equity intraday strategy research lab. \
Below is the FULL history of every hypothesis tested so far. Your job is NOT to summarize each one \
individually (that's already there) -- it's to step back and find HIGHER-LEVEL, CROSS-CUTTING \
lessons: recurring market-structure or behavioral assumptions that keep failing regardless of the \
specific mechanism used to express them, patterns across multiple DIFFERENT hypotheses, and concrete \
guidance for what the next batch of hypotheses should actively avoid assuming.

Full experiment history ({len(entries)} entries):
{blocks}

Write 2-4 higher-level conclusions (not a per-experiment recap). For each, name the underlying \
assumption that appears to be failing (or, if something has worked, what's supporting it), which \
experiments support that conclusion, and what it implies for the next round of hypotheses. Be \
specific and grounded in the actual results above, not generic trading platitudes."""


def review_research_history(api_key: str = "", call_fn: Optional[Callable[[str], str]] = None,
                             knowledge_base_path: Optional[str] = None,
                             conclusions_path: Optional[str] = None) -> str:
    """
    Cross-experiment synthesis step, run BEFORE Quant Researcher proposes a
    fresh batch -- distinct from both the per-experiment Performance
    Analyst narrative (which only ever looks at ONE experiment) and the
    raw Knowledge Base list (which is just facts, not synthesis). Records
    the result via knowledge_base.record_conclusion() so it becomes part
    of the permanent research history, then returns the text.

    Raises RuntimeError if there's no history yet to review (nothing
    useful to synthesize from zero experiments) -- callers should skip
    calling this on a genuinely first-ever run.
    """
    entries = load_entries(knowledge_base_path) if knowledge_base_path else load_entries()
    if not entries:
        raise RuntimeError("No experiment history to review yet -- skip this step on a first-ever run.")

    from news.news_agent import call_claude
    prompt = build_review_prompt(entries)
    call = call_fn or (lambda p: call_claude(p, api_key, max_tokens=2048))
    conclusion_text = call(prompt)

    exp_ids = [e.exp_id for e in entries]
    if conclusions_path:
        record_conclusion(conclusion_text, exp_ids, path=conclusions_path)
    else:
        record_conclusion(conclusion_text, exp_ids)
    return conclusion_text


def _significant_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _overlap_score(text_a: str, text_b: str) -> float:
    """Simple, explainable Jaccard word-overlap -- deterministic, not an
    LLM judgment. This is the hard filter's job: cheaply catch obvious
    re-proposals of a rejected mechanism (e.g., another opening-range
    breakout variant), not a nuanced semantic judgment -- that nuance is
    exactly what the ranking step's Claude call is for, on whatever
    survives this filter."""
    words_a, words_b = _significant_words(text_a), _significant_words(text_b)
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def hard_filter(hypotheses: list, knowledge_base_path: Optional[str] = None,
                 overlap_threshold: float = 0.35) -> tuple:
    """
    Deterministic pre-filter, applied BEFORE any LLM ranking:
    1. Reject hypotheses whose mechanism substantially overlaps a
       REJECT/SEEDED-negative Knowledge Base entry.
    2. Reject hypotheses that need data this lab doesn't have.
    Returns (survivors, rejected_with_reasons) -- rejected candidates are
    not silently dropped, they're reported so the record shows why.
    """
    rejected_mechs = rejected_mechanisms(knowledge_base_path) if knowledge_base_path else rejected_mechanisms()
    survivors = []
    rejected = []

    for h in hypotheses:
        combined_text = f"{h.mechanism} {h.rules}"
        max_overlap = max((_overlap_score(combined_text, m) for m in rejected_mechs), default=0.0)
        if max_overlap >= overlap_threshold:
            rejected.append((h, f"Mechanism overlaps {max_overlap:.0%} with a prior "
                                 f"REJECTed/SEEDED-negative entry (threshold {overlap_threshold:.0%})."))
            continue

        text_lower = combined_text.lower()
        infeasible_hit = next((kw for kw in INFEASIBLE_DATA_KEYWORDS if kw in text_lower), None)
        if infeasible_hit:
            rejected.append((h, f"Requires data this lab doesn't have access to ('{infeasible_hit}')."))
            continue

        survivors.append(h)

    return survivors, rejected


def build_ranking_prompt(hypotheses: list, knowledge_base_summary: str = "") -> str:
    blocks = "\n\n".join(
        f"[{i+1}] {h.name}\nMechanism: {h.mechanism}\nRationale: {h.rationale}\n"
        f"Distinctiveness: {h.distinctiveness}"
        for i, h in enumerate(hypotheses)
    )
    history_section = (
        f"\n{knowledge_base_summary}\n\nPenalize any candidate below whose core mechanism is "
        f"still substantially similar to a REJECTed/SEEDED-negative entry above, even if it "
        f"survived the automated pre-filter -- use your own judgment on similarity, not just "
        f"exact wording.\n"
        if knowledge_base_summary else ""
    )
    return f"""Rank the following {len(hypotheses)} intraday trading hypotheses by theoretical \
rationale and uniqueness -- NOT by predicting backtest performance (none have been tested yet). \
Favor a hypothesis with a genuine, specific behavioral/informational mechanism over a vague or \
generic one, and favor one that's clearly distinct from standard textbook patterns.
{history_section}
{blocks}

Respond in EXACTLY this format:
RANKING:
1. <name> -- <one sentence why it ranks here>
2. <name> -- <one sentence>
(continue for all {len(hypotheses)})

SELECTED: <name of your #1>
SELECTION_REASONING: <2-3 sentences on why this one specifically, referencing its rationale and uniqueness>"""


def parse_ranking_response(raw_response: str, hypotheses: list) -> dict:
    selected_match = re.search(r"SELECTED:\s*(.+)", raw_response)
    reasoning_match = re.search(r"SELECTION_REASONING:\s*(.+)", raw_response, re.DOTALL)
    ranking_match = re.search(r"RANKING:\s*(.+?)(?=\nSELECTED:|\Z)", raw_response, re.DOTALL)

    if not selected_match:
        raise RuntimeError(f"Could not find a SELECTED: line in the ranking response: "
                            f"{raw_response[:300]}")

    selected_name = selected_match.group(1).strip()
    winner = next((h for h in hypotheses if h.name.strip().lower() in selected_name.lower()
                   or selected_name.lower() in h.name.strip().lower()), None)
    if winner is None:
        raise RuntimeError(f"SELECTED name '{selected_name}' didn't match any proposed "
                            f"hypothesis name: {[h.name for h in hypotheses]}")

    return {
        "winner": winner,
        "selection_reasoning": reasoning_match.group(1).strip() if reasoning_match else "",
        "full_ranking_text": ranking_match.group(1).strip() if ranking_match else raw_response,
    }


def rank_and_select(hypotheses: list, knowledge_base_path: Optional[str] = None,
                     api_key: str = "", call_fn: Optional[Callable[[str], str]] = None) -> dict:
    """Two layers: (1) deterministic hard_filter, (2) Claude ranks the
    survivors by rationale/uniqueness. Raises if the hard filter rejects
    everything -- the pipeline can't proceed with zero candidates, and
    that's worth surfacing loudly, not silently proposing a fallback."""
    survivors, rejected = hard_filter(hypotheses, knowledge_base_path)
    if not survivors:
        raise RuntimeError(
            f"All {len(hypotheses)} proposed hypotheses were rejected by the hard filter: "
            f"{[(h.name, reason) for h, reason in rejected]}. Re-run propose_hypotheses() "
            f"for a fresh batch."
        )

    from news.news_agent import call_claude
    from research_lab.knowledge_base import render_for_prompt
    kb_summary = render_for_prompt(knowledge_base_path) if knowledge_base_path else render_for_prompt()
    prompt = build_ranking_prompt(survivors, kb_summary)
    call = call_fn or (lambda p: call_claude(p, api_key, max_tokens=2048))
    raw_response = call(prompt)
    result = parse_ranking_response(raw_response, survivors)
    result["rejected_by_hard_filter"] = rejected
    result["survivors"] = survivors
    return result


def run_backtest_with_audit(strategy: Strategy, data: dict, capital_per_symbol: float,
                             risk_params: RiskParameters, start_date, end_date,
                             n_walk_forward_windows: int = 4) -> dict:
    """
    Splits [start_date, end_date] into n_walk_forward_windows sequential
    windows via backtesting_engineer.walk_forward_split(). The LAST window
    is the true out-of-sample holdout -- never touched while selecting or
    describing the hypothesis, only evaluated once here. The rest are the
    walk-forward consistency windows the Statistical Auditor checks.
    """
    windows = backtesting_engineer.walk_forward_split(start_date, end_date, n_walk_forward_windows)
    walk_forward_metrics = []
    all_trades_by_window = []

    for w_start, w_end in windows:
        windowed_data = {
            sym: df[(df.index.date >= w_start) & (df.index.date <= w_end)]
            for sym, df in data.items()
        }
        result = backtesting_engineer.run_backtest(
            strategy, windowed_data, capital_per_symbol, risk_params.risk_per_trade_pct, risk_params,
        )
        metrics = backtesting_engineer.compute_metrics(
            result["trades"], capital_per_symbol * len(data), result["trading_calendar"],
        )
        walk_forward_metrics.append(metrics)
        all_trades_by_window.append(result["trades"])

    out_of_sample_metrics = walk_forward_metrics[-1] if walk_forward_metrics else {}
    consistency_metrics = walk_forward_metrics[:-1]
    out_of_sample_trades = all_trades_by_window[-1] if all_trades_by_window else []
    all_trades = [t for trades in all_trades_by_window for t in trades]

    verdict = statistical_auditor.audit(consistency_metrics, out_of_sample_metrics)

    return {
        "verdict": verdict, "walk_forward_metrics": consistency_metrics,
        "out_of_sample_metrics": out_of_sample_metrics, "out_of_sample_trades": out_of_sample_trades,
        "all_trades": all_trades, "windows": windows,
    }


def run_experiment_phase2(hypothesis: Hypothesis, strategy: Strategy, data: dict,
                           capital_per_symbol: float, start_date, end_date,
                           selection_reasoning: str = "", risk_params: Optional[RiskParameters] = None,
                           n_walk_forward_windows: int = 4, narrative_api_key: str = "",
                           narrative_call_fn: Optional[Callable[[str], str]] = None,
                           experiments_dir: Optional[str] = None, knowledge_base_path: Optional[str] = None,
                           skip_regime_breakdown: bool = False) -> str:
    """
    Phase 2: given an already-implemented Strategy for the already-selected
    hypothesis, runs the full backtest -> audit -> narrative -> save
    pipeline. Returns the exp_id it was saved under.

    experiments_dir/knowledge_base_path: override where results get saved
    -- defaults to the real research_lab/experiments/ and
    knowledge_base.jsonl if not given. Tests MUST pass explicit temp paths
    here rather than relying on monkeypatching experiment_manager's module
    constants, since those are already bound as this function's own
    (transitively, via experiment_manager's) default argument values at
    import time and won't pick up a later patch.
    skip_regime_breakdown: for tests -- avoids a real network call to
    fetch_nifty() when regime breakdown isn't what's being tested.
    """
    risk_params = risk_params or RiskParameters()
    backtest_result = run_backtest_with_audit(
        strategy, data, capital_per_symbol, risk_params, start_date, end_date, n_walk_forward_windows,
    )
    verdict = backtest_result["verdict"]

    combined_metrics = backtesting_engineer.compute_metrics(
        backtest_result["all_trades"], capital_per_symbol * len(data),
        sorted({d for df in data.values() for d in df.index.date}),
    )

    sector_map = load_sector_map()
    sector_breakdown = compute_sector_breakdown(backtest_result["all_trades"], sector_map)
    time_of_day_breakdown = compute_time_of_day_breakdown(backtest_result["all_trades"])
    regime_breakdown = {}
    if not skip_regime_breakdown:
        try:
            from data.fetch_historical import fetch_nifty
            from strategies.market_regime import build_regime_series
            nifty = fetch_nifty(period="2y")
            regime_series = build_regime_series(nifty)
            regime_breakdown = compute_regime_breakdown(backtest_result["all_trades"], regime_series)
        except Exception as e:
            print(f"WARNING: could not compute regime breakdown: {e}")
            regime_breakdown = {}

    narrative = performance_analyst.explain(
        hypothesis.name, {"decision": verdict.decision, "reasoning": verdict.reasoning},
        combined_metrics, sector_breakdown, time_of_day_breakdown, regime_breakdown,
        api_key=narrative_api_key, call_fn=narrative_call_fn,
    )

    save_kwargs = {}
    if experiments_dir is not None:
        save_kwargs["experiments_dir"] = experiments_dir
    if knowledge_base_path is not None:
        save_kwargs["knowledge_base_path"] = knowledge_base_path

    exp_id = experiment_manager.next_experiment_id(
        experiments_dir if experiments_dir is not None else experiment_manager.EXPERIMENTS_DIR
    )
    experiment_manager.save_experiment(
        exp_id=exp_id,
        hypothesis={
            "name": hypothesis.name, "mechanism": hypothesis.mechanism,
            "rationale": hypothesis.rationale, "rules": hypothesis.rules,
            "selection_reasoning": selection_reasoning,
        },
        parameters={"risk_per_trade_pct": risk_params.risk_per_trade_pct,
                    "max_trades_per_day": risk_params.max_trades_per_day,
                    "daily_loss_limit_pct": risk_params.daily_loss_limit_pct,
                    "capital_per_symbol": capital_per_symbol, "symbols": list(data.keys()),
                    "n_walk_forward_windows": n_walk_forward_windows},
        data_period=f"{start_date} to {end_date}",
        metrics={**combined_metrics, "walk_forward_metrics": backtest_result["walk_forward_metrics"],
                 "out_of_sample_metrics": backtest_result["out_of_sample_metrics"],
                 "audit_checks": verdict.checks,
                 "sector_breakdown": sector_breakdown, "time_of_day_breakdown": time_of_day_breakdown,
                 "regime_breakdown": regime_breakdown},
        observations=narrative,
        verdict={"decision": verdict.decision, "reasoning": verdict.reasoning},
        **save_kwargs,
    )
    return exp_id
