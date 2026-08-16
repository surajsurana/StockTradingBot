# Execution Realism Framework Proposal (Design Only — Not Implemented)

**Status: Proposal for review. No code has been written or modified. Nothing in this document has been implemented — per explicit direction, this is scoped as its own separate, explicitly-approved framework change, not bundled into Amihud's or any other strategy's evaluation.**

Generated: 2026-08-15. Builds directly on `swing_research/execution_realism_study.md` (2026-08-05), which recommended exactly this kind of change but implemented nothing, and on the execution-realism gap analysis that found `swing_research/backtesting_engine.py`'s position sizing has zero awareness of a stock's own trading volume and zero cost/slippage model anywhere in the pipeline.

## Objective

Close three confirmed gaps, as three **independent, individually toggleable** additions — not a rewrite of the existing engine, and not a change to any existing experiment's default behavior:

1. A stock's own trading volume has no bearing on how large a position the engine is willing to simulate.
2. No cost is charged for the fact that trading a large position in a thin stock moves the price.
3. Fills happen at a price (same-day close) that isn't achievable by a real order reacting to that same day's completed candle.

## Guiding constraint: backward compatibility is non-negotiable

Eight strategies' worth of experiment records (`swing_research/experiments/EXP-001` through `EXP-026`) already exist, each reproducible from its own recorded manifest under the CURRENT engine behavior. Every change below is proposed as an **opt-in parameter, defaulting to the current behavior exactly** — no existing experiment's recorded numbers would change if re-run, because nothing is enabled unless a caller explicitly turns it on. This mirrors how `min_lookback_days`, `extra_columns_by_symbol`, and every other engine extension in this program's history has been added: additive, never a silent default change.

---

## Proposed Change 1: Volume-relative position-sizing cap

**Current behavior (confirmed in code):** `quantity = int((equity * strategy.risk_pct_per_unit) / risk_per_share)` — a function of equity, risk percentage, and stop distance only. No reference to `Volume` anywhere in `backtesting_engine.py`.

**Proposal:** an optional `max_participation_pct_of_adv` parameter (both on `simulate_portfolio()`/`simulate_portfolio_single_unit()` and as a `Strategy` class attribute, following the same override pattern `risk_pct_per_unit`/`max_units` already use). When set, every computed `quantity` is additionally capped:

```
adv = average daily Volume over the trailing N days (proposed N=20, matching this
      program's other 20/21-day conventions -- e.g. BETA_LOOKBACK_DAYS-adjacent scale)
quantity = min(quantity, int(max_participation_pct_of_adv * adv))
```

**Decision needed:** what cap percentage counts as "safe." Common practitioner heuristics range 5-10% of ADV as a rough ceiling before assuming meaningful price impact; academic market-impact literature (Kyle 1985-style models) would suggest something calibrated rather than a flat rule, but calibration requires order-book data this platform doesn't have (see Change 2's own caveat). **Recommendation: start conservative — 5% of trailing-20-day ADV — as a disclosed, round-number heuristic, not a fitted parameter**, consistent with this program's existing convention of disclosed, simple conventions (8% stop-loss, 1% risk-per-unit) over fitted ones.

**A trade that would exceed even this reduced size at `quantity=0`** should be skipped entirely (same soft-fail convention the engine already uses when capital can't cover a position), not forced to a token size.

---

## Proposed Change 2: Illiquidity-linked slippage/impact cost

**Current behavior (confirmed in code and module docstring):** zero transaction costs, zero slippage, anywhere in the pipeline.

**Proposal:** an optional `apply_illiquidity_cost` flag. When enabled, each trade's realized fill price is adjusted against the position by an amount derived from the **same ILLIQ measure Amihud's own signal already computes** (`swing_research/cross_sectional.py`'s `compute_shrunk_beta_score`-adjacent machinery would need an Amihud-side equivalent — see the earlier Amihud implementation plan): `cost_pct = k × ILLIQ_symbol × (trade_dollar_value)`, capped at some maximum (e.g. 3-5%) to avoid an unbounded cost on an extreme-tail illiquidity reading.

**This is the single most consequential — and most honestly uncertain — piece of this proposal.** Two real options:

- **Option A (recommended): derive the cost from ILLIQ itself.** Principled — it reuses a real, disclosed, already-computed measure of exactly "how much does trading this stock move its price," rather than inventing an unrelated cost model. Directly closes the specific gap flagged for Amihud (a backtest that assumes zero cost for the thing the strategy is theorized to be compensation for). Weakness: the calibration constant `k` cannot be fit against real historical spread/impact data (no order-book or historical-spread data exists in this platform — a confirmed gap, `order_book_data: False`), so `k` would necessarily be a disclosed, reasoned assumption, not an empirically-derived one.
- **Option B: a simpler tiered flat-cost table** (e.g., by market-cap or ADV bucket: large-cap = 10bps, mid-cap = 30bps, small-cap = 75bps, illustrative only). Easier to implement and explain, but disconnected from each individual stock's own measured liquidity — would apply the same cost to two very differently-liquid stocks in the same bucket.

**Decision needed:** Option A vs B, and if A, what calibration constant `k` and cap to use (this document does not propose a specific number — that's a genuine judgment call, not a technical one, and should not be silently defaulted).

---

## Proposed Change 3: Realistic fill timing

**Already studied and recommended** by the Execution Realism Study (2026-08-05) — this proposal doesn't redesign it, just proposes formally implementing what was already found and recommended there: an optional `fill_timing` parameter on the simulation functions, with two values:

- `"same_day_close"` (current, unconditional default — unchanged) — fills at the signal-generating day's own Close.
- `"next_day_open"` (new) — fills at the next trading day's Open, for both entries and exits, matching exactly what the Execution Realism Study already simulated by direct substitution (not a new mechanism, just promoting that study's own methodology into a real, reusable engine parameter instead of a one-off analysis script).

The Study's own finding directly applies: headline returns won't move much for price-pattern strategies, but tail-risk metrics (Sortino, Max Drawdown) will — and for Amihud specifically, this hasn't been measured at all yet (the Study only covered SW-003/SW-008, neither of which selects on illiquidity). **This should be measured for Amihud's actual selected population once feasible, not assumed to match SW-003/SW-008's numbers.**

---

## Validation plan (before trusting this for Amihud specifically)

Before applying any of this to Amihud, re-run SW-003 and SW-008 — the Execution Realism Study's own original two strategies — under Changes 1-3 combined, and compare against:
(a) their existing close-fill, no-cost, no-cap baseline (already recorded), and
(b) the Study's own next-day-open-only alternate (already recorded).

This checks that the new mechanism behaves sensibly (e.g., does the volume cap actually bind meaningfully for liquid Nifty 500 large/mid-caps, or is it a no-op there as expected — it SHOULD be close to a no-op for SW-003/SW-008's typical selections, which would be a useful sanity check that the cap isn't silently distorting results for strategies that don't need it). Only after this sanity check would Amihud itself be re-scoped under the new mechanism.

---

## What this proposal deliberately does NOT include

- No change to `acceptance_criteria.py`, `evidence_quality.py`, `cross_strategy_review.py`, or anything under `deployment/`.
- No change to any existing strategy's recorded experiments or verdicts — SW-001 through SW-009 stand as recorded, under the engine's current (unchanged) default behavior.
- No real order-book/historical-spread data acquisition — that would be a separate, larger data-infrastructure decision (see the roadmap's own "Securities lending/borrow + execution infrastructure" dataset recommendation), not assumed here.
- No implementation of Amihud itself — this is purely the modeling-capability proposal that would need to exist first.

## Decisions needed from you before any implementation

1. **Participation cap**: 5% of trailing-20-day ADV as proposed, or a different figure?
2. **Cost model**: Option A (ILLIQ-derived, recommended) or Option B (simpler flat tiers)? If A, what calibration constant/cap?
3. **Scope of the validation re-run**: proceed with re-running SW-003/SW-008 under the new mechanism once built, as proposed?
4. **Where this code should live**: as new optional parameters directly on `swing_research/backtesting_engine.py`'s existing functions (additive, same file), or as a new sibling module that wraps/extends it (more isolated, zero risk of an accidental edit to the existing, already-validated logic, at the cost of some duplication)?

Nothing proceeds until these are answered.
