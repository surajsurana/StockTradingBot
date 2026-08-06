# Paper-Trading Promotion Checklist

Standing procedure — run through every item below **whenever any strategy is promoted to `PAPER_TRADING`** (or any later status). Written after the SW-008 (Short-Term Reversal) deployment gap on 2026-08-06, where the strategy was registered locally and approved for paper trading a day before its code actually reached the VPS, causing a silent missed Telegram message.

**Rule: if any item fails, stop and report the failure. Do not mark the strategy "deployed" until every item below is checked.**

---

## 1. Local commit

- [ ] `git status` reviewed — only the intended strategy's files staged (strategy module, `cross_sectional.py`/similar signal additions, `published_research_analyst.py` record, `research_director.py` wrapper, CLI choice, tests, registry, strategy library doc, experiment records). No unrelated in-progress work swept in.
- [ ] `git commit` created with a descriptive message.

## 2. Git pushed

- [ ] `git push origin main` succeeds.
- [ ] Confirm the commit hash appears on the remote (`git log origin/main -1` or the push output's `<old>..<new>` range).

## 3. VPS updated

- [ ] `ssh ... "git pull origin main"` on the VPS completes as a fast-forward with no conflicts.
- [ ] `ssh ... "git log --oneline -1"` on the VPS matches the pushed commit hash.

## 4. Strategy file exists (on the VPS, not just locally)

- [ ] `ssh ... "ls swing_research/strategies/<strategy_key>.py"` — file present.
- [ ] `ssh ... "venv/bin/python -c 'from swing_research.strategies.<strategy_key> import <ClassName>; print(<ClassName>.name)'"` — imports cleanly.

## 5. Registry updated (on the VPS)

- [ ] `deployment/state/strategy_registry.json` on the VPS contains the strategy key.
- [ ] `research_verdict == "PASS"`.
- [ ] `deployment_status == "PAPER_TRADING"`.
- [ ] `strategy_id` assigned (e.g. `SW-00N`).

## 6. Factory updated (on the VPS)

- [ ] `run_paper_trading.py`'s `_STRATEGY_FACTORIES` dict on the VPS contains the strategy key, with a working `strategy_factory` and (if the strategy needs one) `compute_extra_columns_fn`.

## 7. Scheduler recognises the strategy

- [ ] `deployment.scheduler.strategies_due_now(list_strategies())` run on the VPS includes the new strategy key (after market close, or with a simulated `now` during market hours).

## 8. First paper-trading run completed

- [ ] `run_paper_trading.py --strategy=<strategy_key>` (or the next scheduled `--all-due`) returns `status: processed` for today's (or the intended catch-up) date.
- [ ] No exception raised during the run.

## 9. Telegram received

- [ ] Confirm `deployment.settings.TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are configured on the VPS (not the "not configured, printing instead" fallback).
- [ ] Confirm the Telegram message actually arrived (ask the user, or check `send_telegram_message()`'s return value for `"ok": true`).

## 10. Report generated

- [ ] `deployment/reports/<strategy_key>/<as_of_date>.md` exists on the VPS.
- [ ] `deployment/reports/<strategy_key>/LATEST.md` exists and matches the dated report.

## 11. Portfolio updated

- [ ] `paper_portfolio.build_dashboard()` (or the equivalent portfolio report) includes the new strategy in its `live_paper_trading` section.
- [ ] If the strategy is meant to be part of a blended weight allocation, `paper_portfolio.py`'s weight constants are updated accordingly.

## 12. Cron unaffected for existing strategies

- [ ] Confirm no *other* already-running strategy was skipped, rerun, or double-notified as a side effect of this promotion (check `logs/paper_trading.log` for duplicate or missing entries for existing strategy keys on the day of promotion).

---

## Known gap this checklist exists to prevent

On 2026-08-05, SW-008 (Short-Term Reversal) was approved and registered for `PAPER_TRADING` **locally only**. The commit was never pushed/pulled to the VPS until 2026-08-06, so the VPS's `run_paper_trading.py` had no factory entry and no registry record for it — `--all-due` silently never considered it, with no error, no crash, and no Telegram message, for a full trading day. This checklist's items 2–3 (git pushed + VPS updated, verified independently of "I committed it") are what would have caught this immediately.
