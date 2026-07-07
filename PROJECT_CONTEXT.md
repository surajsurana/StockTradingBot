# Stock Trading Bot — Project Context (for Claude Code handoff)

This document summarizes everything built so far in this project, so a fresh
Claude Code session can pick up where the previous (Cowork) session left off
without re-deriving context. Paste this whole file into your first Claude
Code message in this project folder.

## What this is

A multi-agent AI trading system for the Indian stock market (NSE), trading
through a Zerodha Kite Connect account. Goal: full autonomy — the user
(Suraj) reviews strategy/profit/risk periodically, not daily. Not a
day-trading bot; it does swing trading (CNC/delivery product, not
intraday/MIS).

## Agent -> file mapping

| Agent | File(s) |
|---|---|
| News Agent | `news/news_agent.py` |
| Technical Agent | `strategies/technical_agent.py` (orchestrates `strategies/ma_crossover.py`, `strategies/mean_reversion.py`) |
| Fundamental Agent | `fundamentals/fundamental_agent.py` |
| Risk Manager | `risk/risk_manager.py` |
| Research Analyst | `research/research_analyst.py` (synthesizes Technical + Fundamental + News into one verdict per symbol) |
| Portfolio Manager | `portfolio/portfolio_manager.py` (confidence-weighted position sizing, capital allocation across candidates) |
| Chief Investment AI | `cio/chief_investment_ai.py` (monthly review + plan: risk-per-trade, max positions, capital deployment -- with hard guardrails against runaway changes) |
| Execution Engine | `execution/execution_engine.py` (paper-logs by default, real Kite orders if `LIVE_TRADING=True`) |

Architecture reasoning: Research Analyst resolves per-symbol conflicts
between specialist agents; Portfolio Manager handles position sizing /
capital allocation across multiple candidates; Chief Investment AI is a
separate, slower-cadence (monthly) layer that adjusts risk parameters --
these were deliberately kept as three distinct agents rather than merged
into one "CIO" agent.

## Daily entry point

`run_daily.py` at the project root is THE script to run each morning once
live. Two-stage funnel:

- **Stage 1** (free, yfinance only): scans the full universe (Nifty 500 by
  default) for Technical signal + Fundamentals pass. Only survivors move on.
- **Stage 2** (paid, calls Claude): News Agent + Research Analyst run only
  on Stage 1 survivors -- keeps API cost tied to actual opportunities.
- **Stage 3**: Portfolio Manager sizes/approves trades, Execution Engine
  places them, Telegram gets a summary.

CLI flags: `--limit=N` (test with fewer symbols), `--paper` (force paper
mode regardless of config).

`main.py` is the older backtest/manual-run tool, not the daily entry point.

## Current status (what's done)

- All agents built and validated with mocked scenarios (see individual
  `test_*.py` files at project root and `backtest/`).
- Nifty 500 universe expansion done: `data/nifty500_constituents.csv` (real
  snapshot from NSE's official archive) + `data/nifty500_universe.py`.
- Telegram reporting wired up and confirmed working with a real message
  (`reporting/telegram_notifier.py`). Bot token/chat ID are in
  `config/settings.py` (not committed -- see Secrets section below).
- Real-capital-driven position sizing: `execution/execution_engine.py`'s
  `fetch_available_capital()` reads Kite's live margins API instead of a
  static config number. Mandatory (abort-on-failure) in live mode,
  best-effort fallback to `config.STARTING_CAPITAL` in paper mode.
- Kite token refresh: `refresh_kite_token.py` is a semi-manual daily flow
  (~30 sec: open a login link, log in, paste one code back). Kite's
  `access_token` expires every single day (SEBI/Zerodha rule, not
  bypassable) -- this must be run before any live run.
- **Live order placement confirmed working for real** via
  `test_live_order.py` -- a standalone script (separate from the main
  pipeline) that places one real BUY then a matching SELL, to validate Kite
  order placement in isolation. Successfully tested: bought 1 share
  GOLDBEES at Rs.118.71, sold at Rs.118.72, round-trip P&L Rs.0.01.
- `execution/execution_engine.py`'s live order placement was fixed to use
  LIMIT orders instead of plain MARKET orders (see Known Issues below for
  why), priced off `signal.entry_price` with a configurable buffer
  (`config.settings.LIMIT_ORDER_BUFFER_PCT`, default 1.5%).
- Local git repo initialized and committed (35 files). Still needs: create
  the actual GitHub repository (private) and push -- last known state was
  mid-way through this (user needs to grab the real repo URL from GitHub
  and run `git remote set-url origin <url>` then `git push -u origin main`).

## Known issues / gotchas (important -- read before touching execution code)

1. **Kite's access_token expires daily.** Must run `python
   refresh_kite_token.py` each morning before any live run. Fully automated
   (TOTP-based) login was explicitly deferred by the user -- flagged as a
   future task requiring storing Kite password + TOTP secret locally (a
   security tradeoff needing separate explicit consent).

2. **Kite account is on the free "Personal" API tier**, not the paid
   Connect tier. This means: order placement works fine, but
   `/quote/ltp` (live market quotes) returns a `PermissionException` --
   "Insufficient permission for that call." This is why
   `execution_engine.py` prices live LIMIT orders off `signal.entry_price`
   (from the Technical Agent's own historical-data computation) rather than
   a fresh live quote, and why `test_live_order.py` asks the user to type in
   the current price manually instead of fetching it.

3. **Kite rejects plain MARKET orders via API** unless "market protection"
   is configured on the account -- confirmed via real testing (error:
   "Market orders without market protection are not allowed via API."). Fix
   applied throughout: use LIMIT orders priced slightly through the market
   (above entry price for BUY, below for SELL) so they still fill
   immediately for a liquid, small-quantity trade. Buffer is
   `config.settings.LIMIT_ORDER_BUFFER_PCT` (1.5% in the main engine, the
   manual test script used 1%).

4. **IP whitelisting is required** on the Kite developer console (this
   became mandatory after a SEBI static-IP rule that took effect April
   2026). This is set under Profile (not the individual app page) at
   developers.kite.trade/profile -- "IP Whitelist" field, up to 2 IPs.
   Requests can come from either an IPv4 or IPv6 address depending on the
   network path, so both were added. If the home ISP's IP changes
   (dynamic IP), live orders will start failing again with "IP not allowed"
   until the new IP is added there.

5. **Real trading account currently has very little capital** (~Rs.500
   available as of this session) -- position sizing will be tiny until more
   capital is added. The system reads real balance live via
   `fetch_available_capital()`, so this updates automatically as the user
   adds funds -- no code change needed.

6. **Known simplification**: risk-rule settings (RISK_PER_TRADE_PCT,
   MAX_OPEN_POSITIONS, etc.) still come straight from `config/settings.py`,
   not from whatever Chief Investment AI decided in its last monthly
   review/plan -- there's no persistence of its monthly plan yet. This is a
   documented gap, not a bug.

## Secrets / config

`config/settings.py` holds all API keys/tokens and is **git-ignored** (see
`.gitignore`) so it never gets committed. `config/settings.example.py` is
the template that IS committed -- copy it to `settings.py` and fill in real
values on any new machine/clone:

```
cp config/settings.example.py config/settings.py
```

Values needed: `KITE_API_KEY`, `KITE_API_SECRET`, `KITE_ACCESS_TOKEN`
(regenerated daily), `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`.

## Pending / next steps

- Push the local git repo to an actual GitHub remote (private repo) --
  in progress when this handoff happened.
- Task: fully automated Kite login (TOTP-based) -- explicitly deferred,
  needs separate consent since it means storing the Kite password + TOTP
  secret locally.
- Persist Chief Investment AI's monthly plan so `run_daily.py` reads live
  risk settings from it instead of static `config/settings.py` values.
- Build a dashboard (web/mobile-viewable) -- explicitly deferred until
  Portfolio Manager + Chief Investment AI were both done; both are done now,
  so this is unblocked whenever the user wants it.
- Eventually: deploy to an always-on server (VPS) so `run_daily.py` runs
  automatically every morning without the user's PC needing to be on. User
  explicitly said to do GitHub + Claude Code first, and treat this as a
  later step. This needs its own plan: a paid VPS, a way to still refresh
  the Kite token daily (currently a manual/semi-manual flow that opens a
  browser login), and a cron-style scheduler.

## Working style established so far

- Every new function/script gets mock-based validation (success paths,
  failure paths, edge cases) before being handed off for real-world testing.
- Real-world testing (actual Kite orders, actual Telegram messages, actual
  market data) happens on the user's own machine via Command Prompt --
  screenshots are shared back to confirm results, since the dev sandbox
  used previously had restricted network access (couldn't reach Kite,
  yfinance, or Telegram directly).
- User prefers concise, direct communication -- minimal preamble, get to
  the point.
