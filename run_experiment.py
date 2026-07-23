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

# Curated liquid large/mid-cap universe -- intraday strategies specifically
# need real liquidity to execute without heavy slippage, unlike the
# daily-bar swing strategies which scan the whole Nifty 500. Widened from
# the original 27 to ~150 (2026-07-24, per Suraj's request) specifically to
# get EXP-001's sample size up toward 200-300 trades -- same curation
# approach (manually selected well-known liquid names), just more of them.
# Not a methodology change: still hand-picked for genuine liquidity, not
# switched to an automated/computed selection method.
LIQUID_UNIVERSE = [
    # Original 27
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "SBIN", "BHARTIARTL",
    "KOTAKBANK", "LT", "AXISBANK", "BAJFINANCE", "HINDUNILVR", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "WIPRO", "TATASTEEL",
    "JSWSTEEL", "NTPC", "POWERGRID", "HCLTECH", "BAJAJFINSV", "INDUSINDBK", "GRASIM",
    # Banks / financials
    "BAJAJ-AUTO", "BAJAJHLDNG", "SBILIFE", "HDFCLIFE", "ICICIPRULI", "ICICIGI",
    "SHRIRAMFIN", "CHOLAFIN", "PNB", "BANKBARODA", "CANBK", "IDFCFIRSTB", "AUBANK",
    "FEDERALBNK", "RECLTD", "PFC", "MUTHOOTFIN", "LICHSGFIN",
    # IT
    "TECHM", "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "OFSS",
    # Auto / auto ancillary
    "M&M", "TATAMOTORS", "EICHERMOT", "HEROMOTOCO", "TVSMOTOR", "BOSCHLTD",
    "MOTHERSON", "BALKRISIND", "ASHOKLEY", "BHARATFORG",
    # Pharma / healthcare
    "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP", "LUPIN", "AUROPHARMA", "TORNTPHARM",
    "ALKEM", "MAXHEALTH", "SYNGENE",
    # FMCG / consumer
    "BRITANNIA", "TATACONSUM", "DABUR", "GODREJCP", "MARICO", "COLPAL", "UBL", "VBL",
    # Metals / mining / energy
    "HINDALCO", "VEDL", "COALINDIA", "ADANIENT", "ADANIPORTS", "ONGC", "BPCL",
    "GAIL", "IOC", "SAIL", "NMDC", "JINDALSTEL", "HINDZINC",
    # Cement / construction materials
    "SHREECEM", "AMBUJACEM", "ACC", "DALBHARAT",
    # Capital goods / industrials
    "SIEMENS", "ABB", "CUMMINSIND", "HAVELLS", "POLYCAB", "DIXON", "SUPREMEIND",
    "ASTRAL", "AIAENG", "SKFINDIA",
    # Telecom / media
    "IDEA", "INDUSTOWER", "PVRINOX",
    # Real estate / infra
    "DLF", "GODREJPROP", "OBEROIRLTY", "IRB", "GMRAIRPORT",
    # Retail / consumer durables
    "TRENT", "DMART", "PAGEIND", "VOLTAS", "WHIRLPOOL", "BLUESTARCO",
    # Chemicals
    "PIDILITIND", "SRF", "UPL", "AARTIIND", "DEEPAKNTR", "ATUL",
    # Diversified / conglomerates
    "ADANIPOWER", "ADANIGREEN", "TATAPOWER", "TATACHEM", "TATAELXSI",
    # Financial services (NBFC/AMC/exchange)
    "HDFCAMC", "NIPPONLIFE", "BSE", "MCX", "ANGELONE", "CDSL", "IEX",
    # Insurance / others
    "GICRE", "NIACL", "STARHEALTH",
    # Additional liquid mid/largecaps
    "ZOMATO", "NYKAA", "POLICYBZR", "PAYTM", "IRCTC", "INDIGO", "NAUKRI",
    "LTF", "SONACOMS", "KPITTECH", "TATATECH",
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


def run_continue(strategy_module: str, strategy_class: str, days: int, limit: int, windows: int,
                  from_experiment: str = None):
    from research_lab.research_director import run_experiment_phase2
    from research_lab.risk_manager_research import RiskParameters
    from data.fetch_kite_intraday import fetch_all_intraday
    from research_lab.quant_researcher import Hypothesis

    selection_reasoning = ""
    if from_experiment:
        # Re-validate an ALREADY-SELECTED hypothesis against new data
        # (wider universe/lookback, etc.) rather than re-proposing --
        # loads the exact saved hypothesis text from a prior experiment
        # instead of pending_proposal.json (which is deleted after its
        # own successful run, by design -- see save_experiment()).
        from research_lab.experiment_manager import load_experiment
        prior = load_experiment(from_experiment)
        hyp_text = prior["hypothesis"]
        name = hyp_text.split("\n")[0].lstrip("# ").strip()
        mechanism = hyp_text.split("## Mechanism\n")[1].split("\n\n##")[0].strip()
        rationale = hyp_text.split("## Rationale\n")[1].split("\n\n##")[0].strip()
        rules = hyp_text.split("## Rules\n")[1].split("\n\n##")[0].strip()
        winner = Hypothesis(name=name, mechanism=mechanism, rationale=rationale, rules=rules,
                             distinctiveness="")
        selection_reasoning = f"Same hypothesis as {from_experiment} -- re-validated on a wider sample."
        print(f"Re-validating hypothesis from {from_experiment}: {winner.name}")
    else:
        if not os.path.exists(PENDING_PROPOSAL_PATH):
            print(f"No pending proposal found at {PENDING_PROPOSAL_PATH} -- run "
                  f"'python run_experiment.py --propose' first, or pass --from-experiment=EXP-NNN "
                  f"to re-validate an already-selected hypothesis.")
            sys.exit(1)
        with open(PENDING_PROPOSAL_PATH, encoding="utf-8") as f:
            pending = json.load(f)
        winner = Hypothesis(**pending["winner"])
        selection_reasoning = pending["selection_reasoning"]
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
        selection_reasoning=selection_reasoning,
        risk_params=RiskParameters(), n_walk_forward_windows=windows,
        narrative_api_key=settings.ANTHROPIC_API_KEY,
    )

    from research_lab.experiment_manager import load_experiment
    loaded = load_experiment(exp_id)
    print(f"\n{'=' * 70}\nSaved as {exp_id}\n{'=' * 70}")
    print(loaded["verdict"])

    if not from_experiment and os.path.exists(PENDING_PROPOSAL_PATH):
        os.remove(PENDING_PROPOSAL_PATH)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--propose", action="store_true")
    parser.add_argument("--continue", dest="do_continue", action="store_true")
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--strategy-module", type=str)
    parser.add_argument("--strategy-class", type=str)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--limit", type=int, default=len(LIQUID_UNIVERSE))
    parser.add_argument("--windows", type=int, default=4)
    parser.add_argument("--from-experiment", type=str, default=None,
                         help="Re-validate an already-selected hypothesis (e.g. EXP-001) against "
                              "new data, instead of consuming pending_proposal.json.")
    args = parser.parse_args()

    if args.propose:
        run_propose(args.n)
    elif args.do_continue:
        if not args.strategy_module or not args.strategy_class:
            print("--continue requires --strategy-module and --strategy-class")
            sys.exit(1)
        run_continue(args.strategy_module, args.strategy_class, args.days, args.limit, args.windows,
                     from_experiment=args.from_experiment)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
