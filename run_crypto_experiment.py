"""
CLI for the crypto research lane (Pool E) -- sibling to
run_swing_experiment.py, but fetching Binance daily candles for the
crypto universe instead of Nifty 500 data, and judging every strategy
NET of Indian crypto tax (see swing_research/crypto_lane.py).

    python run_crypto_experiment.py --strategy=crypto_trend_timing [--years=8] [--symbols=BTC,ETH] [--windows=3]
"""

import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import settings
from data.fetch_crypto import CRYPTO_MAJORS, CRYPTO_UNIVERSE, fetch_all_crypto_daily, fetch_usdinr_rate

def _lane():
    return __import__("swing_research.crypto_lane", fromlist=["x"])


# strategy -> (runner, default symbols, default years of history)
RUNNERS = {
    "crypto_xs_momentum": (lambda: _lane().run_crypto_xs_momentum_experiment, CRYPTO_UNIVERSE, 5),
    "crypto_trend_timing": (lambda: _lane().run_crypto_trend_timing_experiment, CRYPTO_MAJORS, 9),
    "crypto_tsmom": (lambda: _lane().run_crypto_tsmom_experiment, CRYPTO_MAJORS, 9),
    "crypto_vol_managed": (lambda: _lane().run_crypto_vol_managed_experiment, CRYPTO_MAJORS, 9),
    "crypto_trend_timing_weekly": (lambda: _lane().run_crypto_trend_timing_weekly_experiment, CRYPTO_MAJORS, 9),
    "crypto_trend_timing_daily": (lambda: _lane().run_crypto_trend_timing_daily_experiment, CRYPTO_MAJORS, 9),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=list(RUNNERS), default="crypto_xs_momentum")
    parser.add_argument("--years", type=float, default=0, help="0 = the strategy's default history length")
    parser.add_argument("--limit", type=int, default=0, help="0 = the strategy's full default universe")
    parser.add_argument("--symbols", default="", help="comma-separated override, e.g. BTC,ETH")
    parser.add_argument("--windows", type=int, default=3)
    args = parser.parse_args()

    runner_fn, default_symbols, default_years = RUNNERS[args.strategy]
    years = args.years or default_years
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()] or default_symbols
    symbols = symbols[:args.limit] if args.limit else symbols
    print(f"Fetching {years}y of daily candles for {len(symbols)} coin(s) from Binance...")
    data = fetch_all_crypto_daily(symbols, years=years)
    if not data:
        print("No data fetched -- aborting.")
        sys.exit(1)
    start_date = min(df.index.date.min() for df in data.values())
    end_date = max(df.index.date.max() for df in data.values())
    print(f"Data for {len(data)} coin(s); backtest period {start_date} to {end_date} (book in USDT)")

    exp_id = runner_fn()(data=data, start_date=start_date, end_date=end_date,
                                       n_walk_forward_windows=args.windows,
                                       narrative_api_key=settings.ANTHROPIC_API_KEY)

    from swing_research.research_director import SWING_EXPERIMENTS_DIR
    from research_lab.experiment_manager import load_experiment
    loaded = load_experiment(exp_id, SWING_EXPERIMENTS_DIR)
    m = loaded["metrics"]
    rate = fetch_usdinr_rate()
    print(f"\n{'=' * 70}\nSaved as {exp_id}\n{'=' * 70}")
    print(loaded["verdict"])
    eq = m.get("evidence_quality", {})
    if eq:
        print(f"Evidence quality: {eq.get('label')} ({eq.get('score')}/100)")
    pre, ledger = m.get("pre_tax", {}).get("full_period", {}), m.get("tax_ledger_full_period", {})
    print(f"\nFull period, {m.get('total_trades')} trades (USDT; Rs. at {rate:.1f}/USD):")
    print(f"  PRE-TAX  (after fees):  P&L {pre.get('total_pnl')} USDT (~Rs.{(pre.get('total_pnl') or 0) * rate:,.0f}) "
          f"| CAGR {pre.get('cagr')}% | Sharpe {pre.get('sharpe_ratio')} | MaxDD {pre.get('max_drawdown_pct')}%")
    print(f"  POST-TAX (India 31.2%): P&L {m.get('total_pnl')} USDT (~Rs.{(m.get('total_pnl') or 0) * rate:,.0f}) "
          f"| CAGR {m.get('cagr')}% | Sharpe {m.get('sharpe_ratio')} | MaxDD {m.get('max_drawdown_pct')}%")
    print(f"  Tax ledger: raw {ledger.get('raw_pnl')} - fees {ledger.get('fees_and_spread')} - tax {ledger.get('tax')} "
          f"= {ledger.get('post_tax_pnl')} USDT; TDS withheld (refundable) {ledger.get('tds_withheld_refundable')} USDT; "
          f"{ledger.get('winning_trades')} winners / {ledger.get('losing_trades')} losers")
    btc = m.get("comparison_vs_benchmarks", {}).get("btc_buy_and_hold", {})
    if btc:
        print(f"  BTC buy & hold (pre-tax): CAGR {btc.get('cagr')}% | Sharpe {btc.get('sharpe_ratio')} | MaxDD {btc.get('max_drawdown_pct')}%")


if __name__ == "__main__":
    main()
