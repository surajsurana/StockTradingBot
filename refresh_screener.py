"""
Weekly new-ideas screen (see advice/screener.py). Slow and network-bound: about 20 minutes for the Nifty 500,
so it runs on the server, Saturday morning, and the dashboard just reads the saved file.

    python refresh_screener.py              # the whole Nifty 500 (minus financials)
    python refresh_screener.py --limit 20   # a quick trial

Cron line:
    15 8 * * 6  cd .../StockTradingBot && venv/bin/python refresh_screener.py >> logs/advice_screener.log 2>&1
"""

import argparse
from datetime import date

from advice.screener import refresh
from deployment.settings import STATE_DIR

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    out = refresh(STATE_DIR, date.today(), limit=args.limit)
    print(f"screened {out['screened']}, {out['passed']} cleared the bar; top: " + ", ".join(f"{c['symbol']} {c['score']}" for c in out["candidates"][:8]))
