# Stock Trading Bot — Architecture

## Goal
Swing-trading system for Indian equity markets (NSE), running through your own
Zerodha Kite Connect account, executing without daily manual approval, with
multiple interchangeable strategies and Telegram reporting (daily/weekly/
monthly/quarterly).

## Design principles
1. **Paper mode by default.** Nothing places a real order until `LIVE_TRADING = True`
   is explicitly set in config, and only after NSE access is confirmed active.
2. **Strategies are pluggable.** Each strategy is a self-contained module
   implementing the same interface, so new ones can be added or removed
   without touching execution, risk, or reporting code.
3. **Risk sits between strategy and execution.** A strategy proposes a trade;
   the risk manager can shrink, reject, or approve it based on portfolio-wide
   exposure limits — no single strategy can over-leverage the account.
4. **Runs on a schedule, not a tick loop.** Since this is swing trading (multi-day
   holds), the system checks signals a few times a day (market open, midday,
   close) rather than reacting to every price tick. This means it can run as a
   scheduled job (cron / GitHub Actions / a small always-on VM) rather than
   needing a low-latency always-on process.

## Agent -> File mapping
The design conversation refers to eight logical "agents." Here's exactly where
each one lives in the codebase:

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

## Components

```
StockTradingBot/
├── run_daily.py              # THE daily entry point once live -- see "Daily live
│                             # orchestrator" section below
├── config/
│   └── settings.py          # API keys, risk limits, strategy list, LIVE_TRADING toggle,
│                             # USE_NIFTY500_UNIVERSE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
├── data/
│   ├── fetch_historical.py  # Pulls historical daily candles (yfinance) for backtesting
│   │                         # and live daily candles (Kite) for real signals
│   ├── nifty500_constituents.csv  # Snapshot of Nifty 500 constituents from NSE's
│   │                         # official archive (~457 rows)
│   └── nifty500_universe.py # get_nifty500_symbols(): reads the CSV, returns
│                             # yfinance-style ".NS" tickers for the full universe
├── strategies/
│   ├── base.py               # Strategy interface all strategies implement
│   ├── ma_crossover.py        # First strategy: moving-average crossover swing strategy
│   ├── mean_reversion.py      # Second strategy: mean-reversion swing strategy
│   └── technical_agent.py     # Technical Agent: STRATEGY_REGISTRY, get_technical_signals(),
│                             # first_available_signal() -- centralizes what used to be
│                             # duplicated inline in main.py and test scripts
├── fundamentals/
│   └── fundamental_agent.py  # Fundamental Agent: fetch_fundamentals, check_health,
│                             # filter_universe, FundamentalsResult
├── backtest/
│   └── backtester.py        # Simulates a strategy against historical data
├── risk/
│   └── risk_manager.py      # Position sizing + portfolio exposure limits
├── execution/
│   └── execution_engine.py  # Execution Engine (class ExecutionEngine): paper-mode logger
│                             # now; live Kite order placement later
├── reporting/
│   ├── report_generator.py   # Builds P&L summaries (daily/weekly/monthly/quarterly)
│   └── telegram_notifier.py  # send_telegram_message(), get_chat_id() -- Telegram
│                             # delivery, replacing the earlier WhatsApp plan
└── main.py                   # Orchestrates: fetch -> strategies -> risk -> execute -> report
                              # (backtest/manual-run entry point; run_daily.py is the
                              # live daily entry point -- see below)
```

## Data flow

```
Historical/live candles
        |
        v
  [Strategy modules]  <-- each proposes: symbol, direction, entry, stop-loss, target, confidence
        |
        v
  [Risk manager]  <-- sizes the position, rejects if breaches exposure/risk limits
        |
        v
  [Execution]  <-- paper mode: logs it. live mode: places order via Kite API
        |
        v
  [Reporting]  <-- logs trade, rolls up into daily/weekly/monthly/quarterly reports
        |
        v
     Telegram
```

## Daily live orchestrator: `run_daily.py`

This is **the entry point Suraj runs every morning** once live trading starts.
Everything else (individual agent modules, `main.py`) is either a building
block this script calls, or a manual/backtest tool. `run_daily.py` ties all
eight agents into one real pass over the market, as a two-stage funnel:

**Stage 1 -- cheap, no LLM cost (yfinance only).**
For every symbol in the universe (Nifty 500 by default when
`USE_NIFTY500_UNIVERSE = True`, or the small hand-picked `config.SYMBOLS`
list otherwise):
- Technical Agent (`strategies/technical_agent.py`) checks whether any
  active strategy proposes a trade today.
- Fundamental Agent (`fundamentals/fundamental_agent.py`) checks whether the
  company is financially healthy.

Only symbols that **both** pass fundamentals **and** have an active
technical signal survive to Stage 2. This is the cost-control mechanism:
scanning ~450+ symbols with yfinance is free and fast, but calling Claude
for each one would not be. On a typical day only a handful of symbols will
have anything going on, so gating the expensive stage behind the free stage
keeps per-day LLM cost proportional to actual opportunities, not universe
size.

**Stage 2 -- paid, calls Claude (only for Stage 1 survivors).**
- News Agent: real-time sentiment read on each survivor.
- Research Analyst: synthesizes Technical + Fundamental + News into one
  verdict per symbol.

**Stage 3 -- decision + action.**
- Portfolio Manager (`portfolio/portfolio_manager.py`): confidence-weighted
  sizing, prioritizes the strongest convictions if capital is limited,
  produces one final decision per candidate with a reason.
- Execution Engine (`execution/execution_engine.py`): places the approved
  trades -- paper-logs them by default, or real Kite orders if
  `config.LIVE_TRADING` is `True` (unless overridden, see flags below).
- Sends a summary to Telegram (falls back to printing if Telegram isn't
  configured yet).

If Stage 1 produces zero survivors, the run doesn't error -- it sends a
clear "no trades today" message and exits cleanly. This was validated
tonight with mocked data, along with the funnel gating and graceful
handling of fetch failures.

**Command-line flags:**

| Command | Behavior |
|---|---|
| `python run_daily.py` | Full Nifty 500 scan (or `config.SYMBOLS` if `USE_NIFTY500_UNIVERSE = False`) |
| `python run_daily.py --limit=50` | Only scans the first 50 symbols -- fast, for testing |
| `python run_daily.py --paper` | Forces paper mode for this run, ignoring `config.LIVE_TRADING` -- an extra safety override |

Known simplification: capital/risk settings come straight from
`config.settings` (`STARTING_CAPITAL`, `RISK_PER_TRADE_PCT`, etc.), not from
whatever the Chief Investment AI decided last -- there's no persistence of
its monthly plan yet. Update `config/settings.py` by hand to match the
latest monthly plan until that wiring exists.

Scanning the full Nifty 500 universe means roughly 450 yfinance calls for
price history plus ~450 for fundamentals, which can take well over 10
minutes depending on connection speed -- run it with enough lead time before
market open, or use `--limit=N` while testing.

## Before you go live -- pre-flight checklist

Run through this every single morning before trusting `run_daily.py` with
real money:

1. **Refresh `KITE_ACCESS_TOKEN`.** Kite's access_token expires every day --
   this is a Zerodha/SEBI security rule that applies to every Kite Connect
   app, not something this project can bypass. Run `python
   refresh_kite_token.py` each morning: it prints a login URL, you log in
   with your usual Kite ID/password/TOTP, paste back one code, and it
   exchanges the token and updates `config.settings.KITE_ACCESS_TOKEN`
   automatically -- no manual file editing needed. Takes about 30 seconds.
   Running with a stale token will make every live order attempt fail.
   This is a daily task, not a one-time setup step. (A fully automated
   version that skips even this manual login exists as a future option --
   deliberately not built yet since it requires storing your Kite password
   and TOTP secret locally, a bigger security tradeoff worth choosing
   consciously rather than defaulting into.)
2. **Confirm NSE/BSE segments are active on your Kite account.** Already
   done for this account, but re-check if anything about the account
   changes.
3. **Start small.** Run `python run_daily.py --paper` or
   `python run_daily.py --limit=50` first, and only move to a full live
   run once you trust the output.
4. **Fill in Telegram credentials if you want phone notifications.**
   Confirm `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in
   `config/settings.py` (see the Telegram setup steps below) -- otherwise
   summaries just print to the console instead of reaching your phone.

## Strategy interface (contract every strategy follows)

Each strategy is a Python class with:
- `generate_signal(price_history) -> Signal | None`
  Returns a `Signal` (symbol, direction, entry_price, stop_loss, target, confidence)
  or `None` if there's no trade to propose today.

This means `main.py` never needs to know *how* a strategy decides — it just
calls `generate_signal()` on every strategy in the active list and passes
whatever comes back to the risk manager.

## Risk rules (starting point — tune later)
- Max risk per trade: 1% of capital (distance from entry to stop-loss sized accordingly)
- Max concurrent open positions: configurable (start with 5)
- Max total capital deployed at once: configurable (start with 50%)
- Daily loss circuit breaker: if daily realized loss exceeds X%, stop opening
  new positions for the day and alert via Telegram

## Execution modes
- **Paper mode** (default): every "trade" is logged to a CSV/DB with a
  simulated fill price, no money moves. This is how we validate the whole
  pipeline before touching Zerodha's live order API.
- **Live mode**: same code path, but the Execution Engine (`ExecutionEngine`
  class in `execution/execution_engine.py`) calls Kite Connect's
  `place_order` endpoint. Switching modes is a one-line config change, not a
  rewrite — this is intentional so paper and live behave identically except
  for the final API call.

## Why the historical data source is yfinance, not Kite
Your Kite Connect app is on the free "Personal" plan, which doesn't include
historical/live market data APIs (that's the paid "Connect" tier, ₹500/month).
Since backtesting needs years of historical daily candles, we use `yfinance`
(free, no extra subscription) for that. Live signal generation later can use
either yfinance's near-real-time data or Kite's data (if you upgrade to
Connect) — this is a config choice, not an architecture change.

## Telegram reporting setup

Replaces the earlier WhatsApp plan: WhatsApp would have needed either a paid
Twilio setup or a slower Meta Business API review process, while Telegram's
bot API is free, has no review process, and delivers to your phone the same
way WhatsApp would have. Implemented in `reporting/telegram_notifier.py`
(`send_telegram_message(message, bot_token, chat_id)` and
`get_chat_id(bot_token)`).

One-time setup (about two minutes):
1. In Telegram, message **@BotFather** and send `/newbot`. Follow the
   prompts. BotFather replies with a bot token
   (looks like `123456789:AAExampleTokenHere`) -- copy it into
   `config.settings.TELEGRAM_BOT_TOKEN`.
2. Start a chat with your new bot (search its username, send it any
   message, e.g. "hi").
3. Run `python reporting/telegram_notifier.py` after filling in
   `TELEGRAM_BOT_TOKEN` -- it prints your `chat_id`. Copy that into
   `config.settings.TELEGRAM_CHAT_ID`.
4. Done. `send_telegram_message()` now delivers real messages.

Until both settings are filled in, every call just prints instead of
sending, so the rest of the pipeline (including `run_daily.py`) can be
built and tested before Telegram setup is finished.

## Nifty 500 universe

`data/nifty500_universe.py` (`get_nifty500_symbols()`) loads
`data/nifty500_constituents.csv` -- a snapshot downloaded from NSE's
official archive (`archives.nseindia.com/content/indices/ind_nifty500list.csv`)
on 2026-07-06, ~457 rows -- and returns yfinance-style `.NS` tickers.

Controlled by `config.settings.USE_NIFTY500_UNIVERSE`:
- `True` (current default): `run_daily.py` scans the full Nifty 500 list
  above via Stage 1 of the funnel.
- `False`: falls back to the smaller hand-picked `config.SYMBOLS` /
  `STRATEGY_SYMBOLS` lists used during earlier development and backtesting.

The CSV is a point-in-time snapshot, not a live feed. Nifty 500's
constituents get reshuffled periodically (usually twice a year) -- a stale
list isn't dangerous (worst case: miss a newly added stock, or scan one
that's been removed; the Fundamentals/Technical filters still apply
normally to whatever's in the file), but re-download the CSV every few
months to stay current.

## Important operational note
This code needs to run somewhere that stays on and has internet access — a
small cloud VM, or your own machine if it's reliably on during market hours.
It cannot run inside this chat session (this environment's network access is
restricted and code here can't reach Zerodha, Yahoo Finance, or Telegram APIs
directly). We build and validate the logic here using synthetic/sample data,
then you run the real data pulls and backtests on your own machine, same as
the Kite connection test.

## Roadmap after this first build
1. [done] Validate `ma_crossover` on real historical NSE data
2. [done] Add a second strategy module (`mean_reversion`), with per-strategy
   symbol universes and per-strategy market-regime filter opt-in/out
3. Add a fundamentals health-check filter (only trade financially healthy
   companies — profits, debt levels, earnings trend) — feeds the
   **Fundamental Agent**
4. Add a news/sentiment agent that can veto or favor a trade based on
   recent headlines about that stock/company — the **News Agent**
5. [done] **Research Analyst agent** — resolves the per-symbol conflict
   between Technical Agent, Fundamental Agent, and News Agent (e.g.
   Technical says BUY, Fundamental says HOLD, News says SELL) into a single
   verdict: favorable / unfavorable / neutral, with a confidence score and
   written reasoning. This answers "who breaks the tie on one symbol" —
   it does not do position sizing or cross-symbol capital allocation; that
   is the next agent below.
6. **Portfolio Manager agent (next up)** — operates one layer above the
   Research Analyst, turning per-symbol verdicts into actual per-trade
   decisions:
   - **Confidence-weighted position sizing.** Replaces the flat 1%-of-capital
     sizing rule as the final sizing decision — size is now scaled by the
     Research Analyst's confidence score, not a flat percentage. The Risk
     Manager still enforces hard safety limits (max concurrent open
     positions, max total capital deployed, daily loss circuit breaker) —
     those are non-negotiable ceilings. Portfolio Manager decides the
     *relative* sizing between candidate trades within whatever room the
     Risk Manager allows.
   - **Capital-allocation conflict resolution.** When multiple symbols get
     favorable verdicts on the same day but there isn't enough capital for
     all of them, the Portfolio Manager decides which trades get funded,
     partially funded, or skipped, and produces one final go/no-go decision
     per candidate trade.
   - **Audit trail.** Logs the full reasoning chain for every decision —
     which Research Analyst verdicts/confidences went in, what capital was
     available, what got sized up/down or rejected and why — so every
     trade decision is explainable after the fact.
   - This is a day-to-day, per-trade-decision agent (runs every time there
     are candidate trades), distinct from the Chief Investment AI below.
7. **Chief Investment AI agent** — separate from Portfolio Manager, runs on
   a **monthly cadence**, not per-trade. Sets the boundaries Portfolio
   Manager operates within for the month: how much total capital is
   deployed, the targeted return for the month, and which strategies are
   active. Portfolio Manager's day-to-day sizing and allocation decisions
   happen inside whatever envelope the Chief Investment AI sets for that
   month. (Note: this decision-making role is distinct from the monthly
   Telegram plan/review *reporting* described below, though they cover
   related ground and may end up sharing inputs.)
8. [done] Wire Telegram sending for real (`reporting/telegram_notifier.py`
   -- switched from the original WhatsApp plan since Telegram's bot API is
   free and needs no business approval process)
9. [done] Nifty 500 universe expansion (`data/nifty500_constituents.csv` +
   `data/nifty500_universe.py`, `config.USE_NIFTY500_UNIVERSE`)
10. [done] Daily live orchestrator (`run_daily.py`) -- two-stage funnel
    (cheap Technical+Fundamental scan, then paid News+Research Analyst on
    survivors only), Portfolio Manager sizing, Execution Engine, Telegram
    summary. Validated end-to-end tonight with mocked/synthetic data.
11. Confirm NSE access is live, do one small real trade end-to-end
12. Deploy to an always-on server, schedule the daily runs

## Monthly plan + review reporting (confirmed requirement)
At the start of each calendar month, send a Telegram message stating: how
much capital is being used this month, the targeted return for the month,
and which strategies are active (build_monthly_plan_text in
reporting/report_generator.py). Alongside next month's plan, also send a
review of the just-finished month comparing actual result to the target
that was promised (build_monthly_review_text) -- so every plan comes with
accountability for the previous one. Both functions are implemented and
tested; they just need to be wired into a real monthly schedule (this is a
natural fit for the schedule tool / a monthly cron job) now that Telegram
sending itself is live.
