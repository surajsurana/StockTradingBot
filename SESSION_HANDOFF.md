# Session Handoff — 2026-08-06

Purpose: a new Claude Code session (e.g. opened in VS Code) can read this to pick up exactly where this session left off, without re-deriving context. Written at the end of a session that covered PEAD deferral, Short-Term Reversal (SW-008) research + deployment, MA Crossover/Mean Reversion retirement, and paper-trading platform hardening.

## Where things stand right now

**Live trading strategies:** none. SW-004 (MA Crossover) and SW-005 (Mean Reversion) were retired 2026-08-06 (governance retirement after formal REJECT verdicts — not a bug). `config/settings.py`'s `ACTIVE_STRATEGIES` is now `[]`, `cio/chief_investment_ai.py`'s `KNOWN_STRATEGIES` is now `set()`, and both are `ARCHIVED` in the deployment registry. Their code is untouched (`strategies/ma_crossover.py`, `strategies/mean_reversion.py` still exist).

**Paper trading strategies:** SW-003 (52-Week High Momentum) and SW-008 (Short-Term Reversal, Jegadeesh 1990), both `PAPER_TRADING` / Research Verdict `PASS`, both running daily via the VPS cron entry `35 15 * * 1-5 ... run_paper_trading.py --all-due`.

**VPS crontab (current, confirmed via `crontab -l`):**
```
0 8 1 * * ... monthly_review.py
35 15 * * 1-5 ... run_paper_trading.py --all-due
```
All four `run_daily.py` entries and all four `monitor_positions.py` entries were removed (no live strategies left to run them for). Re-add these only when a strategy is promoted back to live/production — see `AGENT_STRUCTURE.md` for the originally-documented schedule if you need to reconstruct it.

**Research-only:** Minervini Trend Template Filter (INCONCLUSIVE), Cross-Sectional Momentum (SW-006, PASS but RESEARCH/HOLD — underperformed SW-003, insufficient diversification alone to justify paper trading).

**Deferred:** PEAD (SW-007) — no usable historical earnings-surprise dataset via yfinance. Revisit if a real dataset becomes available. See `swing_research/strategy_library/pead.md`.

**Archived:** Turtle System 2 (REJECT), MA Crossover (SW-004, REJECT), Mean Reversion (SW-005, REJECT).

## What changed this session (chronological)

1. **PEAD deferred** — documented in `swing_research/strategy_library/pead.md`, registered SW-007, no code written, framework untouched.
2. **Short-Term Reversal (SW-008) researched and approved** — full frozen-framework pipeline (base 10yr, 2yr validation, recent-period, post-COVID robustness — all PASS, HIGH evidence quality). New files: `swing_research/strategies/short_term_reversal.py`, additions to `cross_sectional.py`/`published_research_analyst.py`/`research_director.py`, `swing_research/strategy_library/short_term_reversal.md`. Compared against every prior strategy, portfolio impact analysis, paper-trading expectation report — all in the strategy library doc.
3. **SW-008 registered PAPER_TRADING**, added to `run_paper_trading.py`'s `_STRATEGY_FACTORIES`, paper portfolio built (`paper_portfolio.py`, blended 60/40 SW-003/SW-008 dashboard — corrected a real bug in the blending math the same day, documented in the dashboard report itself). Execution Realism Study written (`swing_research/execution_realism_study.md`) — research only, no framework change.
4. **SW-004/SW-005 retired from live trading** (see "current state" above). New `deployment/reports/retirement/RETIREMENT_REPORT.md`.
5. **VPS crontab cleaned up** — `run_daily.py` and `monitor_positions.py` entries removed after confirming zero open live positions (`data/known_positions.json` was `{}` on the VPS).
6. **Deployment gap found and fixed**: SW-008 had been approved/registered *locally* on 2026-08-05 but the code never reached the VPS (still at an older commit) until this session pushed/pulled it on 2026-08-06 — a full trading day of silently missing SW-008 signals and Telegram messages, with nothing catching it automatically. Diagnosed via SSH, fixed by pushing the missing commit and pulling it onto the VPS, then running a one-time catch-up for SW-008 for today's date only (did not touch SW-003).
7. **Platform hardened** against a repeat of #6:
   - `deployment/PROMOTION_CHECKLIST.md` — standing manual checklist for any future `PAPER_TRADING` promotion.
   - `run_paper_trading.py` — `_run_one()` now wraps its work in try/except; one strategy's failure sends its own error Telegram message and can no longer stop other strategies or the daily summary. `test_run_paper_trading.py` covers this.
   - `verify_deployment.py` (new root-level script) — automated, read-only check of every checklist item (git sync, strategy file, registry, factory, scheduler, first run, Telegram config, report) for a given `--strategy=<key>`. Exits nonzero with a "STOP" message if anything's incomplete. `test_verify_deployment.py` covers this.
   - All pushed and pulled onto the VPS; full test suite green there (610 tests) and locally (619 — the extra 9 are unrelated local-only work, see below).

## Important: uncommitted local work NOT part of this session

`git status` on the local machine shows several modified/untracked files that predate this session and were **deliberately left untouched** (not mine to commit without being asked):
- `backtest_trailing_stop.py`, `monitor_positions.py`, `risk/trailing_stop.py`, `test_trailing_stop.py` (modified) — an in-progress trailing-stop feature.
- `backtest_pullback_continuation.py`, `backtest_pullback_continuation_portfolio.py`, `strategies/pullback_continuation.py`, `test_kite_connection.py` (untracked) — in-progress pullback-continuation strategy work.
- `cio/chief_investment_ai.py` — **this one IS part of this session** (the `KNOWN_STRATEGIES = set()` retirement edit), but it's still uncommitted locally. Check `git diff cio/chief_investment_ai.py` before doing anything else with git in this repo, so you don't lose it.

**First thing to do in the new session:** run `git status` and `git diff cio/chief_investment_ai.py` to see this uncommitted change, and decide whether to commit it (it was never pushed to GitHub or the VPS — the VPS's `KNOWN_STRATEGIES` still has the old two-strategy set, though this is inert now since there's no live cron to use it anyway).

## Standing rules to keep following (from memory, still in force)

- **Frozen framework**: `swing_research/acceptance_criteria.py`, `evidence_quality.py`, `cross_strategy_review.py`, and the entire `deployment/` package (except genuine reproducible bugs) must never be modified. New strategies/scripts are added via new files + the established root-level extension points (`run_paper_trading.py`, `run_swing_experiment.py`, `run_certification.py`).
- **Research Verdict vs Deployment Status are always independent**, set via two separate `deployment_manager` calls, never automatically linked.
- **No optimization/parameter tuning** in strategy research — faithful implementation of published rules only, full rigor sequence (2yr validation → 10yr base → recent-period check → robustness if ambiguous → Knowledge Base + Strategy Library entry) for every strategy.
- **Cross-strategy review** is due after every 3rd strategy evaluated (last one: after Turtle + Minervini + 52-Week High).

## VPS access (for the new session)

- Host: `168.144.66.161`, user `tradingbot`, repo at `/home/tradingbot/StockTradingBot`.
- SSH key that works: `~/.ssh/id_ed25519_stockbot` (also `~/.ssh/stocktradingbot_vps` — both were found to work this session; the default `ssh` invocation with no `-i` does NOT work, you must pass one of these explicitly).
- `config/settings.py` is gitignored on both ends — real secrets never sync via git. If you need to check/change it on the VPS, SSH in directly.
- Python: use `venv/bin/python` on the VPS (not bare `python3`), and `py -3` locally (bare `python`/`python3` are not on PATH in this Windows environment).

## Suggested next steps (not started, your call)

- Decide what to do with the uncommitted `cio/chief_investment_ai.py` change (see above).
- Decide whether/when to pick the next published strategy to research (roadmap so far: Turtle → Minervini → 52-Week High → Cross-Sectional Momentum → PEAD deferred → Short-Term Reversal). No next strategy has been chosen yet.
- The Execution Realism Study recommended a future framework update (model realistic next-day-open fills instead of same-day close) before any strategy goes LIVE — not implemented, flagged for a future decision.
- The unrelated in-progress trailing-stop and pullback-continuation work (see above) is still sitting uncommitted locally — worth asking the user whether that's still wanted before it's at risk of being lost or overwritten.
