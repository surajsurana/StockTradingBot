# Stock Trading Bot — Agent Structure & Strategy

A quick reference for how the system is put together: who does what, how a
trading day flows end to end, and the strategy/risk rules underneath it.

## The agent team

| Agent | File(s) | What it does |
|---|---|---|
| Macro Strategist | `macro/macro_strategist.py` | Runs once a day (not per-stock), before the scan even starts — reads general world/market headlines (geopolitics, wars/ceasefires, natural disasters, central bank moves) and can skip new entries entirely (high risk) or halve risk-per-trade for the day (elevated risk) |
| Technical Agent | `strategies/technical_agent.py` (+ `ma_crossover.py`, `mean_reversion.py`) | Reads price charts, proposes BUY signals with entry/stop-loss/target |
| Fundamental Agent | `fundamentals/fundamental_agent.py` | Checks the company is financially healthy (debt, ROE, revenue growth) before it's even considered |
| News Agent | `news/news_agent.py` | Reads recent headlines (yfinance + Moneycontrol + Economic Times RSS), asks Claude for a bullish/bearish/neutral read — *per stock*, unlike Macro Strategist |
| Research Analyst | `research/research_analyst.py` | Combines Technical + Fundamental + News into one verdict per stock: favorable / unfavorable / neutral, with a confidence score |
| Portfolio Manager | `portfolio/portfolio_manager.py` | Confidence-weighted position sizing; when capital is limited, decides which candidates get funded |
| Risk Manager | `risk/risk_manager.py` | Hard, non-negotiable safety limits — max risk per trade, max open positions, max capital deployed, daily loss circuit breaker |
| Execution Engine | `execution/execution_engine.py` | Places real orders via Kite (LIMIT orders), and a GTT stop-loss/target on every buy |
| Chief Investment AI | `cio/chief_investment_ai.py` | The only agent that runs monthly, not daily — reviews last month's result and sets next month's capital/target/active-strategies/risk-per-trade envelope |

**Supporting infrastructure** (not "agents" in the trading-decision sense, but what makes the above run unattended):

| Piece | File(s) | Purpose |
|---|---|---|
| Automated login | `auth/kite_auto_login.py` | Logs into Kite every morning on its own (TOTP-based) — no manual step |
| Position tracking | `execution/positions.py`, `execution/position_state.py` | Knows what's actually held in the real account across days; reconciles closed positions and logs them |
| Position Monitor | `monitor_positions.py` | Re-runs the same Technical + Fundamental + News + Research Analyst pipeline against *already-open* positions a few times a day, and can exit early if the picture turns unfavorable |
| Scheduler | DigitalOcean VPS, cron | Runs everything automatically — no need for your PC to be on |

## Daily flow

```mermaid
flowchart TD
    MACRO["Macro Strategist<br/>daily, before the scan"]
    MACRO -->|high risk| SKIP[("No new trades today<br/>existing GTTs unaffected")]
    MACRO -->|normal or elevated risk<br/>elevated halves risk/trade| U["Nifty 500 universe<br/>~500 stocks, 9:20am IST"]
    U --> TECH[Technical Agent]
    U --> FUND[Fundamental Agent]
    TECH --> S1{"Stage 1 filter<br/>signal AND healthy?"}
    FUND --> S1
    S1 -->|survivors only, free scan ends here| NEWS[News Agent]
    S1 --> RESEARCH[Research Analyst]
    NEWS --> RESEARCH
    RESEARCH --> PM[Portfolio Manager]
    PM --> RM[Risk Manager]
    CIO["Chief Investment AI<br/>(monthly)"] -.sets envelope.-> RM
    RM --> EXEC[Execution Engine]
    EXEC --> KITE[("Zerodha Kite<br/>LIMIT order + GTT")]
    EXEC --> TG[("Telegram summary")]

    MON["Position Monitor<br/>11:15am / 1:15pm / 3:00pm IST"]
    MON -.re-runs Technical+Fundamental+News+Research on open positions.-> RESEARCH
    MON -->|unfavorable verdict| EXEC
    EXEC -.exits early, cancels GTT.-> KITE
```

**Two schedules run on the VPS, both unattended:**
- `run_daily.py` — once each morning at **9:20am IST**. Full pipeline above: scans the universe, researches survivors, sizes and places new trades.
- `monitor_positions.py` — three times during market hours (**11:15am, 1:15pm, 3:00pm IST**). Re-checks everything currently held and can exit early on bad news/fundamentals/technicals, on top of the automatic GTT stop-loss/target that's already sitting on every position.

## Strategy & risk rules

**Active strategies** (`config/settings.py` → `ACTIVE_STRATEGIES`):
- `ma_crossover` — trend-following, moving-average crossover. Only takes BUY signals when the broader market (Nifty 50) is itself in an uptrend (the "market regime filter").
- `mean_reversion` — buys dips in range-bound stocks; deliberately *not* gated by the market-regime filter, since it wants choppy/declining conditions.

**Risk limits**:
- Risk per trade (distance from entry to stop-loss) — starts at 1% of capital, now a **Chief Investment AI monthly decision** (see below): can drift up in a strong/bullish month or down after a weak one, clamped to a 0.5%–2% band and at most ±15% relative change per month. Falls back to `config/settings.py`'s `RISK_PER_TRADE_PCT` until a plan exists.
- Max 5 concurrent open positions (`config/settings.py`, still static)
- Max 50% of capital deployed at once (`config/settings.py`, still static)
- Daily loss circuit breaker at 3% — stops opening new positions for the day if hit (`config/settings.py`, still static)

**Why Research Analyst exists**: Technical, Fundamental, and News agents can disagree (e.g. good chart, bad news). Research Analyst is the one place that weighs all three into a single call, rather than each specialist agent acting alone.

**Why Macro Strategist is separate from News Agent**: News Agent only ever reads headlines about one company at a time, and only for stocks that already passed Stage 1 — it has no way to catch something like a geopolitical shock that would matter *before* any individual stock's chart reacts to it. Macro Strategist reads general world/market headlines once a day (not per-stock, so its cost is fixed regardless of how many candidates Stage 1 finds) and acts as a portfolio-wide brake, not a per-stock opinion. It's also distinct from Chief Investment AI: CIO decides how much to risk *this month*, on a slow, reviewed cadence; Macro Strategist decides whether *today specifically* calls for less than that — a same-day-only adjustment that's never persisted to the monthly plan.

**Why Chief Investment AI is separate from Portfolio Manager**: Portfolio Manager makes a decision every time there's a candidate trade (daily cadence). Chief Investment AI runs once a month and sets the *envelope* — how much capital, what target return, which strategies are active — that Portfolio Manager then operates inside. Keeping them separate means one bad month's reasoning can't cause a runaway swing in day-to-day sizing (capital allocation is capped at ±20% month over month).

**Chief Investment AI is now wired up for real** (`monthly_review.py`, scheduled 1st of each month): it reviews last month's *actual* closed trades (`closed_trades_log.csv`, not a backtest stand-in), sets next month's capital cap/target/active strategies/**risk per trade**, persists that to `data/monthly_plan.json`, and `run_daily.py`/`monitor_positions.py` actually read it — `active_strategies` comes from the plan, trades are sized against `min(real Kite capital, plan.capital_allocated)`, and `RiskManager` uses the plan's risk-per-trade figure, never past any of these limits. Sends both the review and the new plan to Telegram.

## Known gaps (honest, not hidden)

- Chief Investment AI's plan now covers capital cap, active strategies, and risk-per-trade — but not `MAX_OPEN_POSITIONS`/`MAX_DEPLOYED_CAPITAL_PCT`/`DAILY_LOSS_CIRCUIT_BREAKER_PCT`, which are still static `config/settings.py` values.
- The Nifty 500 universe list is a point-in-time snapshot (`data/nifty500_constituents.csv`), re-downloaded every few months, not a live feed.
