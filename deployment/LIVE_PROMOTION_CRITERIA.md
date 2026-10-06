# Live Promotion Criteria (paper → real money)

Standing procedure — run through every gate below **before any strategy moves from `PAPER_TRADING` to `PILOT_LIVE`**. Sibling to `PROMOTION_CHECKLIST.md`, which covers research → paper and stops there; nothing in this repo previously defined what earns real money.

**Rule: every gate must pass. A gate that cannot be evaluated has not passed.** "Not enough data yet" is a FAIL, not a pending.

Written 2026-10-06 after the question "is anything ready to go live?" could not be answered objectively, because no bar existed to answer it against.

---

## Who decides what

Gates A–H are **factual** — they are either true or not, and most are already computable from code in this repo. Gates I–J are **risk-appetite choices**: the numbers in them are placeholders marked `← YOUR CALL`, and they are yours to set, not mine. Nothing in this document is a recommendation to commit capital to any strategy.

---

## Gate A — The research verdict permits it

- [ ] `research_verdict` is `PASS` — **or** `INCONCLUSIVE`, under the higher bar below.
- [ ] The verdict is NOT `REJECT` or `NOT_YET_EVALUATED`.
- [ ] The registry verdict **agrees with the experiment's own `verdict.md`**. Where they differ, the disagreement is explained in writing before promotion.

### INCONCLUSIVE is eligible, at roughly double the evidence

`INCONCLUSIVE` in this program is not "weak evidence". `swing_research/acceptance_criteria.py` returns it for one specific situation: the base run **passed** over full history, the recent-period check **rejected**, and an independent robustness study **materially disagreed with that reject** — two methodologically valid tests pointing opposite ways, with the framework declining to invent a winner. That module's own docstring says such a strategy is "eligible for reconsideration if future evidence … resolves the conflict either way."

Paper trading in the recent period *is* that kind of evidence, and it bears directly on the dimension the conflict is about. Blocking INCONCLUSIVE from ever going live would make paper-trading those strategies pointless, which is the opposite of why they are in paper.

But the recent-period check that rejected used **years** of data. A few months of paper does not overturn it; it adds one data point on the robustness study's side. So:

- [ ] For an `INCONCLUSIVE` strategy, Gates B and C are **doubled**: 6 complete holding cycles and 60 closed trades `← YOUR CALL`, rather than 3 and 30.
- [ ] The paper result points the same way as the robustness study that created the conflict — i.e. it resolves the disagreement rather than sitting inside it.
- [ ] Which test the paper evidence supports, and why, is written down at promotion.

*Note:* `acceptance_criteria.py`'s docstring also says INCONCLUSIVE means "neither approved for paper trading nor permanently rejected", yet five INCONCLUSIVE strategies are in paper today. Practice has already — reasonably — diverged from that line. The docstring should be updated to match what is actually being done, rather than left contradicting it.

*Why:* as of 2026-10-06 three `REJECT` strategies are still running in paper (Moving Average Pullback, Volume-Backed Breakout, VWAP Extension Exhaustion Fade), and the single best-looking paper number in the whole book — Portfolio G at +25.1% — has `NOT_YET_EVALUATED`, i.e. no backtest verdict at all. Attractive paper P&L and evidence are different things, and this gate is what keeps them apart.

*Also:* 10 of 29 registry verdicts currently disagree with their own experiment's `verdict.md` (all early experiments whose acceptance criteria said PASS while the recorded verdict is REJECT or INCONCLUSIVE). The registry is authoritative. An unexplained disagreement means nobody knows which is right, which is not a state to risk money from.

## Gate B — Enough COMPLETED holding cycles, not enough calendar days

- [ ] Paper trading has run for at least **3 complete holding periods** at this strategy's own `holding_days_max` (from its `CandidateProfile` in `swing_research/research_roadmap.py`), **and** at least **90 calendar days** `← YOUR CALL`.

*Why:* "30 days of paper trading" means something completely different for Turn-of-the-Month (3–8 day holds) than for Size Premium (11–13 months). A strategy that has not completed a single holding cycle has not been tested at all — it has only been observed mid-position. As of 2026-10-06 the longest paper record in the book is 50 days and most are 15–35, so **no strategy currently clears this gate**, including the two US ones at 6 days.

## Gate C — A real sample of closed trades

- [ ] At least **30 closed trades** `← YOUR CALL` in paper trading.

*Why:* seven strategies currently have **zero** closed trades (PEAD, Turn-of-the-Month, Amihud Illiquidity, Crypto TSMOM, Crypto Weekly Trend Timing, and both US strategies). Their P&L is unrealised or nil — there is no realised track record to judge. Be honest about what 30 buys you: it bounds gross error, not precision. A 30-trade sample still cannot distinguish a mediocre edge from a good one; it can only catch a strategy that is plainly broken.

## Gate D — No MATERIAL drift from the backtest

- [ ] `deployment/drift_report.py`'s `compute_drift(strategy_key, historical_exp_id, historical_experiments_dir)` returns **no MATERIAL flag** in its `flags` dict for any of: `expectancy`, `win_rate`, `max_drawdown_pct`, `avg_holding_period_days`, or trade frequency.

*Why:* this is the strongest gate available, and it already exists and is already tested. It answers the only question that matters — *is the strategy behaving in reality the way the backtest said it would?* — using the same `compute_metrics()` for both sides. Its thresholds (50% relative, 20 percentage points absolute) are deliberately generous for small samples, so a MATERIAL flag is a genuine behavioural change, not noise.

A drift flag does not necessarily mean "never promote". It means the difference must be understood and written down first.

## Gate E — Positive expectancy net of real costs

- [ ] Paper expectancy over the window is **positive after costs and taxes** as modelled for that pool (`swing_research/execution_realism_engine.py`, plus `crypto_costs.py` / `us_equity_costs.py` where they apply).
- [ ] The paper result is not being flattered by an unmodelled cost. For a pool whose costs are a disclosed placeholder, that placeholder is replaced with a real model before promotion, not after.

*Why:* a gross-positive, net-negative strategy is a way to lose money slowly while watching a green number.

## Gate F — Drawdown within tolerance

- [ ] Paper `max_drawdown_pct` does not exceed the backtest's `max_drawdown_pct` by more than the drift report's absolute threshold (20 percentage points).
- [ ] Paper max drawdown is within the absolute ceiling you are willing to take live: **___%** `← YOUR CALL`.

## Gate G — The backtest itself is credible

- [ ] The backtest's Sharpe ratio is **below 3.0** `← YOUR CALL`, or the reason it is higher has been investigated and written down.
- [ ] The experiment's disclosed simplifications have been re-read with real money in mind, and none of them is load-bearing for the result.

*Why:* Overnight Return Anomaly's experiment reports a **Sharpe of 6.36** across 11,821 trades. That is not a plausible number for a real equity strategy, and in practice it almost always means the backtest captured something execution cannot — here, most likely the close-to-open gap being modelled more favourably than it could be traded. It is simultaneously the most convincing paper result in the book (+1.6%, 170 closed trades, PASS) and the one with the biggest unanswered question. Both facts are true; this gate stops the first from burying the second.

Related, and already flagged in `swing_research/research_roadmap.py`: the US Overnight Return Anomaly candidate has the same fill-timing question, since Pool I fills at `next_day_open`.

## Gate H — The live path actually exists and is safe

**None of this exists today.** `deployment/paper_trading_engine.py` — which runs all 24 paper strategies — contains no order-placement code at all. The real Kite path (`execution/execution_engine.py`, `LIVE_TRADING`) is wired only into `run_daily.py`, the older intraday pipeline whose strategies are all REJECT/ARCHIVED. `deployment/state/live/` does not exist on the VPS, and no strategy has ever held `PILOT_LIVE` or `PRODUCTION`.

Before any real order:

- [ ] A live order path exists from the paper pools, with the same signal → sizing → order flow as paper.
- [ ] **Reconciliation**: broker positions and cash are compared against internal state every run, and a mismatch halts trading and alerts rather than continuing.
- [ ] **Partial fills and rejections** are handled explicitly, not assumed away.
- [ ] **Kill switch**: a single documented action that stops all live order placement without needing a code change or a working VPS session.
- [ ] **Hard caps** enforced in code, not convention: max position size, max open exposure, max orders per day.
- [ ] **Alerting** on every live order, every rejection, and every reconciliation mismatch.
- [ ] A dry run against the live path with `LIVE_TRADING = False` reproduces the paper engine's decisions exactly.
- [ ] **Paper and live can run side by side for the same strategy.** They cannot today: `deployment_status` is a single value, so a strategy is either `PAPER_TRADING` or `PILOT_LIVE`, never both, and there is one state tree per strategy. `deployment/scheduler.py`'s `is_due_now()` already treats `PILOT_LIVE` as an active trading status, so the strategy keeps getting its daily run after promotion — but it runs one book, not two. Running both needs either a `live_enabled` flag alongside the status, or a second book under `deployment/state/live/<strategy>/`.
- [ ] Broker credentials and token refresh are verified working that morning (see the Kite subscription watchdog — the paid market-data app has lapsed before, on 2026-09-23).

## Gate I — Pilot sizing `← YOUR CALL`

- [ ] Promote to `PILOT_LIVE`, never straight to `PRODUCTION`.
- [ ] **The paper book keeps running in parallel.** The live book is not a replacement for it, it is measured against it: if live underperforms paper on the same signals, the gap is slippage and execution cost, which is exactly what paper cannot model and what most often kills a live strategy. Without the parallel paper book there is no way to tell "the edge decayed" from "our fills are bad". Note this does not work as built — see below.
- [ ] Starting capital: **₹_____** or **___%** of that strategy's paper book, whichever is lower.
- [ ] One strategy at a time. A second only after the first has cleared Gate J's review.
- [ ] A written maximum total live exposure across all strategies: **₹_____**.

*Suggested shape, not a recommendation:* an amount whose complete loss would be annoying rather than damaging, held there until the live record is long enough to re-run Gates B–F on live data rather than paper.

## Gate J — Decision recorded, and a defined way back

- [ ] The promotion decision is recorded in the registry with: experiment id, the drift report date and result, paper metrics at promotion (days, closed trades, expectancy, max drawdown), and the capital allocated.
- [ ] **Demotion rule written before going live**, so it is not negotiated while losing money. It should name: a drawdown that returns the strategy to paper, a MATERIAL drift flag that does the same, and a review date.
- [ ] Review scheduled at **___ weeks or ___ closed trades** `← YOUR CALL`, whichever comes first, before any increase in allocation.

---

## Current state against these gates (2026-10-06)

No strategy passes. The binding constraints are Gate H (no live path exists) and Gate B (longest paper record is 50 days; most 15–35; US pair 6 days). Gates C–G cannot yet be meaningfully evaluated for most of the book, which under this document's own rule is a FAIL rather than a pending.

The closest thing to a candidate is **Overnight Return Anomaly** — PASS, 170 closed trades, +1.6% over 30 days, the only strategy with a credible verdict, a real sample and a positive result at once — and it is blocked on Gate B (30 days, needs 90), Gate G (Sharpe 6.36 unexplained) and Gate H (no live path).
