# Stock Trading Bot

Swing-trading system for Indian equity markets, built around your Zerodha
Kite Connect account. See `ARCHITECTURE.md` for the full design.

## Quick start (run this on your own machine, not in a sandboxed environment)

```bash
pip install -r requirements.txt
python main.py
```

By default this runs in **paper mode** using historical data — no real
money moves and no live order is placed. Check `config/settings.py` before
doing anything else.

## Going live: `run_daily.py`

Once you're ready to let the system act for real, **`run_daily.py` (at the
project root) is the entry point you run every morning** — not `main.py`,
which is the backtest/manual-run tool. See the pre-flight checklist below
before your first live run.

```bash
python run_daily.py                # full Nifty 500 scan
python run_daily.py --limit=50      # only scan the first 50 symbols (fast test)
python run_daily.py --paper         # force paper mode, ignoring config.LIVE_TRADING
```

It runs as a **two-stage funnel**:
- **Stage 1 (cheap, no LLM cost):** Technical Agent + Fundamental Agent scan
  the full universe using free yfinance data. Only symbols that pass
  fundamentals AND have an active technical signal move on.
- **Stage 2 (paid, calls Claude):** News Agent + Research Analyst run only
  on Stage 1 survivors, keeping Claude API cost tied to actual
  opportunities instead of universe size.

Then Portfolio Manager sizes/approves trades, Execution Engine places them
(paper-logs by default, or real Kite orders if `LIVE_TRADING = True`), and a
summary is sent to Telegram. If nothing survives Stage 1, you get a clean
"no trades today" message instead of an error.

## Before you go live — pre-flight checklist

Run through this every morning, in order, before trusting a live run:

1. **Refresh `KITE_ACCESS_TOKEN`.** Kite's access_token expires every single
   day (a Zerodha/SEBI rule, not something we can bypass). Run
   `python refresh_kite_token.py` — it prints a login link, you log in with
   your usual Kite ID/password/TOTP, paste back one code, and it updates
   `config.settings.KITE_ACCESS_TOKEN` for you automatically. About 30
   seconds. Skipping this makes every live order attempt fail.
2. **Confirm NSE/BSE segments are active** on your Kite account (already
   done for this account — just re-check if anything changes).
3. **Start small.** Run `python run_daily.py --paper` or
   `python run_daily.py --limit=50` before trusting a full live run.
4. **Fill in Telegram credentials** (`TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID` in `config/settings.py`) if you want summaries sent
   to your phone — see Telegram setup below.

## Telegram setup (two minutes, one-time)

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts.
   Copy the bot token it gives you into `config.settings.TELEGRAM_BOT_TOKEN`.
2. Send your new bot any message (e.g. "hi").
3. Run `python reporting/telegram_notifier.py` — it prints your `chat_id`.
   Copy that into `config.settings.TELEGRAM_CHAT_ID`.

Until both are filled in, reports just print to the console instead of
sending — the pipeline still runs fine either way.

## Agent -> File mapping
See `ARCHITECTURE.md` for full details. Quick reference:

| Agent | File(s) |
|---|---|
| News Agent | `news/news_agent.py` |
| Technical Agent | `strategies/technical_agent.py` (orchestrates `strategies/ma_crossover.py`, `strategies/mean_reversion.py`) |
| Fundamental Agent | `fundamentals/fundamental_agent.py` |
| Risk Manager | `risk/risk_manager.py` |
| Research Analyst | `research/research_analyst.py` |
| Portfolio Manager | `portfolio/portfolio_manager.py` |
| Chief Investment AI | `cio/chief_investment_ai.py` |
| Execution Engine | `execution/execution_engine.py` |

## Folder guide
- `run_daily.py` — **the daily entry point once live**: two-stage funnel over the Nifty 500, Portfolio Manager sizing, Execution Engine, Telegram summary
- `config/` — all settings: API keys, risk limits, which strategies are active, paper/live toggle, `USE_NIFTY500_UNIVERSE`, `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
- `data/` — historical + live price data fetching, plus `nifty500_universe.py` / `nifty500_constituents.csv` (the Nifty 500 scan universe)
- `strategies/` — pluggable strategy modules (one file per strategy), plus `technical_agent.py` (Technical Agent)
- `fundamentals/` — `fundamental_agent.py` (Fundamental Agent)
- `risk/` — position sizing and exposure limits
- `backtest/` — simulates strategies against historical data
- `execution/` — `execution_engine.py` (Execution Engine, class `ExecutionEngine`): paper logger now, live Kite order placement later
- `reporting/` — P&L summaries, `telegram_notifier.py` (Telegram send — replaces the earlier WhatsApp plan)

## Status
- [x] Zerodha Kite Connect API key/secret verified (connection test passed)
- [x] NSE trading access confirmed active
- [x] First strategy (moving-average crossover) implemented
- [x] Backtester implemented
- [ ] Backtest run on real historical data (needs to be run on your machine — see below)
- [x] Telegram reporting wired up (`reporting/telegram_notifier.py`)
- [x] Nifty 500 universe expansion (`data/nifty500_universe.py`, `USE_NIFTY500_UNIVERSE`)
- [x] Daily live orchestrator built (`run_daily.py`) — validated with mocked data
- [ ] Live order placement tested with a small real trade
- [ ] Deployed to an always-on server with a schedule

## Why you need to run parts of this yourself
The environment used to build this code has restricted network access (by
design, for security) — it can't reach Yahoo Finance, Zerodha, or Telegram
APIs directly. Everything here was built and logic-tested with synthetic
data. Run `python main.py` on your own machine to pull real historical data
and see real backtest numbers, and `python run_daily.py` for the real daily
live pass once you've completed the pre-flight checklist above.
