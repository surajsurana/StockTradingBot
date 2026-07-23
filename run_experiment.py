"""
CLI entry point for research_lab/ -- the NSE Cash Intraday Research Lab.

Runs in two phases, because converting a hypothesis's plain-language rules
into an actual Strategy subclass (research_lab/strategies/) is a genuine
code-authoring step, not push-button automation (see
research_lab/research_director.py's module docstring for why):

    python run_experiment.py --propose [--n=8]
        Quant Researcher proposes N hypotheses -> hard filter -> Claude
        ranks the survivors -> reports the winner. Saves the winning
        hypothesis (and the full ranked/rejected list) to
        research_lab/pending_proposal.json for --continue to pick up.
        Makes NO experiment folder yet -- nothing is recorded until a
        real backtest actually runs.

    (Strategy Developer implements the winning hypothesis as a Strategy
    subclass in research_lab/strategies/<name>.py)

    python run_experiment.py --continue --strategy-module=research_lab.strategies.<name> \\
        --strategy-class=<ClassName> [--days=180] [--limit=30] [--windows=4]
        Fetches real Kite intraday data for the already-implemented
        strategy, runs the full backtest -> walk-forward -> Statistical
        Audit -> Performance Analyst narrative -> Experiment Manager save
        pipeline, reports the real verdict (pass or reject).
"""

import argparse
import dataclasses
import importlib
import json
import os
import sys
from datetime import date, timedelta

from config import settings

PENDING_PROPOSAL_PATH = os.path.join(os.path.dirname(__file__), "research_lab", "pending_proposal.json")

# Same curated liquid large-cap universe used for the earlier (now-deleted)
# ad hoc ORB backtest -- intraday strategies specifically need real
# liquidity to execute without heavy slippage, unlike the daily-bar swing
# strategies which scan the whole Nifty 500.
LIQUID_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "SBIN", "BHARTIARTL",
    "KOTAKBANK", "LT", "AXISBANK", "BAJFINANCE", "HINDUNILVR", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "WIPRO", "TATASTEEL",
    "JSWSTEEL", "NTPC", "POWERGRID", "HCLTECH", "BAJAJFINSV", "INDUSINDBK", "GRASIM",
]


def run_propose(n: int):
    from research_lab.quant_researcher import propose_hypotheses
    from research_lab.research_director import rank_and_select

    print(f"Quant Researcher: proposing {n} hypotheses (reading Knowledge Base history first)...")
    hypotheses = propose_hypotheses(settings.ANTHROPIC_API_KEY, n=n)
    print(f"Proposed {len(hypotheses)} hypotheses:")
    for h in hypotheses:
        print(f"  - {h.name}")

    print("\nResearch Director: hard-filtering against Knowledge Base history + data feasibility...")
    result = rank_and_select(hypotheses, api_key=settings.ANTHROPIC_API_KEY)

    if result["rejected_by_hard_filter"]:
        print(f"Rejected by hard filter ({len(result['rejected_by_hard_filter'])}):")
        for h, reason in result["rejected_by_hard_filter"]:
            print(f"  - {h.name}: {reason}")

    print(f"\nClaude ranking of {len(result['survivors'])} survivor(s):")
    print(result["full_ranking_text"])

    winner = result["winner"]
    print(f"\n{'=' * 70}\nSELECTED: {winner.name}\n{'=' * 70}")
    print(f"Mechanism: {winner.mechanism}")
    print(f"Rationale: {winner.rationale}")
    print(f"Rules: {winner.rules}")
    print(f"\nWhy selected: {result['selection_reasoning']}")

    pending = {
        "winner": dataclasses.asdict(winner),
        "selection_reasoning": result["selection_reasoning"],
        "all_proposed": [dataclasses.asdict(h) for h in hypotheses],
        "rejected_by_hard_filter": [(dataclasses.asdict(h), reason)
                                     for h, reason in result["rejected_by_hard_filter"]],
    }
    os.makedirs(os.path.dirname(PENDING_PROPOSAL_PATH), exist_ok=True)
    with open(PENDING_PROPOSAL_PATH, "w", encoding="utf-8") as f:
        json.dump(pending, f, indent=2)

    print(f"\nSaved to {PENDING_PROPOSAL_PATH}.")
    print(f"Next: implement '{winner.name}' as a Strategy subclass in research_lab/strategies/, "
          f"then run:\n  python run_experiment.py --continue "
          f"--strategy-module=research_lab.strategies.<name> --strategy-class=<ClassName>")


def run_continue(strategy_module: str, strategy_class: str, days: int, limit: int, windows: int):
    from research_lab.research_director import run_experiment_phase2
    from research_lab.risk_manager_research import RiskParameters
    from data.fetch_kite_intraday import fetch_all_intraday

    if not os.path.exists(PENDING_PROPOSAL_PATH):
        print(f"No pending proposal found at {PENDING_PROPOSAL_PATH} -- run "
              f"'python run_experiment.py --propose' first.")
        sys.exit(1)
    with open(PENDING_PROPOSAL_PATH, encoding="utf-8") as f:
        pending = json.load(f)

    from research_lab.quant_researcher import Hypothesis
    winner = Hypothesis(**pending["winner"])
    print(f"Continuing experiment for: {winner.name}")

    module = importlib.import_module(strategy_module)
    strategy_cls = getattr(module, strategy_class)
    strategy = strategy_cls()

    symbols = LIQUID_UNIVERSE[:limit]
    to_date = date.today()
    from_date = to_date - timedelta(days=days)
    print(f"Fetching {days} days of 5-minute intraday candles for {len(symbols)} symbol(s) via Kite...")
    data = fetch_all_intraday(symbols, "5minute", from_date, to_date, settings)
    if not data:
        print("No data fetched -- aborting.")
        sys.exit(1)
    print(f"Data available for {len(data)} symbol(s)")

    start_date = min(df.index.date.min() for df in data.values())
    end_date = max(df.index.date.max() for df in data.values())

    exp_id = run_experiment_phase2(
        hypothesis=winner, strategy=strategy, data=data,
        capital_per_symbol=settings.RESEARCH_LAB_VIRTUAL_CAPITAL,
        start_date=start_date, end_date=end_date,
        selection_reasoning=pending["selection_reasoning"],
        risk_params=RiskParameters(), n_walk_forward_windows=windows,
        narrative_api_key=settings.ANTHROPIC_API_KEY,
    )

    from research_lab.experiment_manager import load_experiment
    loaded = load_experiment(exp_id)
    print(f"\n{'=' * 70}\nSaved as {exp_id}\n{'=' * 70}")
    print(loaded["verdict"])

    os.remove(PENDING_PROPOSAL_PATH)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--propose", action="store_true")
    parser.add_argument("--continue", dest="do_continue", action="store_true")
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--strategy-module", type=str)
    parser.add_argument("--strategy-class", type=str)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--limit", type=int, default=27)
    parser.add_argument("--windows", type=int, default=4)
    args = parser.parse_args()

    if args.propose:
        run_propose(args.n)
    elif args.do_continue:
        if not args.strategy_module or not args.strategy_class:
            print("--continue requires --strategy-module and --strategy-class")
            sys.exit(1)
        run_continue(args.strategy_module, args.strategy_class, args.days, args.limit, args.windows)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
