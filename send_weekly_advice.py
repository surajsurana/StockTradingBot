"""
The weekly portfolio advice message for your real Groww holdings (advice only, nothing is ever ordered).

    python send_weekly_advice.py            # build and PRINT (safe default)
    python send_weekly_advice.py --send     # build and send by Telegram

Scheduled cron line (NOT installed until you ask for it), Sundays 09:00 IST:
    0 9 * * 0  cd .../StockTradingBot && venv/bin/python send_weekly_advice.py --send

It reads the same files the dashboard's Advice tab uses: the Groww holdings snapshot, the return reports
(deployment/state/groww_reports.json) and your rules (advice/rules.py plus advice_rules.json overrides).
"""

import argparse
import json
import os
from datetime import date

from advice.view import build_advice
from dashboard.state_view import FAMILY, FAMILY_NAME, _company_name, _segment_of, portfolio_view, reports_view
from data.fetch_groww import load_snapshot


def _prices(symbols: list) -> dict:
    import yfinance as yf
    out = {}
    for s in symbols:
        try:
            out[s + ".NS"] = float(yf.Ticker(s + ".NS").fast_info["last_price"])
        except Exception:
            try:
                out[s + ".NS"] = float(yf.Ticker(s + ".NS").history(period="5d")["Close"].dropna().iloc[-1])
            except Exception:
                pass
    return out


def _lakh(v: float) -> str:
    return f"Rs {v / 1e7:.2f} Cr" if abs(v) >= 1e7 else f"Rs {v / 1e5:.1f} L"


def load_advice(state_dir: str, today: date):
    """(advice, reports, snapshot) for the live Groww holdings, or (None, None, snapshot) if not connected."""
    from advice.results import load_results
    from advice.tasks import load_done
    snap = load_snapshot(state_dir)
    if snap.get("status") != "connected" or not snap.get("holdings"):
        return None, None, snap
    mine = portfolio_view(snap, _prices([h["symbol"] for h in snap["holdings"]]), {}, session_today=False)
    path = os.path.join(state_dir, "groww_reports.json")
    rep = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    reports = reports_view(rep, mine, snap.get("cash"), today) if rep else None
    from advice.screener import load_screener
    from advice.track import load_log
    a = build_advice(mine, reports, today, None, lambda s: FAMILY.get(s, s), _segment_of, state_dir,
                     lambda fam: FAMILY_NAME.get(fam) or _company_name(rep or {}, fam), cash=snap.get("cash"), done=load_done(state_dir),
                     results=load_results(state_dir), raw=rep, screener=load_screener(state_dir), log=load_log(state_dir))
    return a, reports, snap


def build_message(state_dir: str, today: date) -> str:
    """A once-a-week overview (optional): the numbers, the tasks, and where the money could be."""
    a, reports, snap = load_advice(state_dir, today)
    if a is None:
        return f"Weekly portfolio advice: Groww is not connected right now (status {snap.get('status')}). Nothing to report."
    t, h = a["targets"], (reports or {}).get("headline") or {}
    lines = ["Long term advice", f"Weekly overview, {today.strftime('%d %b %Y')}", ""]
    if h:
        lines.append(f"Worth {_lakh(h['value'])}. Gain {_lakh(h['gain'])} ({h['gain_pct']:+.1f}%). Per year since 2021: {h['xirr_pct']}% against Nifty 50 {h['bench_xirr_pct']}%.")
    lines.append(f"Aim: about {t['yearly']['base']}% a year (range {t['yearly']['low']} to {t['yearly']['high']}%), about {t['quarterly']['base']}% a quarter.")
    lines += ["", f"To do on {a['when_label']}:"] + ([f"- {x['name']}: {x['title']}" for x in a["tasks"]] or ["- Nothing. The system is watching everything."])
    p = a["projection"]["horizons"]
    lines += ["", "If you keep adding Rs {:,} a month, middling case: ".format(a["params"]["monthly"]) + ", ".join(f"{x['years']}y {_lakh(x['base'])}" for x in p)]
    lines += ["", "Advice only. Nothing has been ordered."]
    return "\n".join(lines).replace("_", " ").replace("*", "")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="send by Telegram (default: just print)")
    ap.add_argument("--state-dir", default=None)
    args = ap.parse_args()
    from deployment.settings import STATE_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    msg = build_message(args.state_dir or STATE_DIR, date.today())
    print(msg)
    if args.send:
        from reporting.telegram_notifier import send_telegram_message
        send_telegram_message(msg, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
