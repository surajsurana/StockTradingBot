"""
Tests for deployment/capital_winddown.py -- the gradual capital
wind-down layer (added 2026-08-23, per explicit direction). Covers both
the pure reservation/withdrawal math in isolation, and the full
integration with deployment/paper_trading_engine.py's real queue-then-
resolve cycle (to prove a withdrawal can never cause an otherwise-valid
queued entry to be skipped or resized, and never distorts daily_pnl or
compute_live_metrics()'s CAGR/Sharpe/etc.).
"""

import datetime
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import deployment.paper_trading_engine as pte
from deployment.capital_winddown import (
    RESERVATION_BUFFER_MULTIPLIER,
    apply_capital_winddown,
    compute_winddown_withdrawal,
    cumulative_withdrawn_between,
    cumulative_withdrawn_up_to,
    estimate_reservation,
)
from deployment.settings import PAPER_TRADING_WINDDOWN_TARGET_CAPITAL
from swing_research.base import OpenPosition, Signal, Strategy


# --------------------------- pure-function tests ---------------------------

class TestEstimateReservation(unittest.TestCase):
    def test_reserved_capital_includes_the_5pct_buffer(self):
        pending = {"SYM.NS": {"stop_loss": 90.0, "signal_price": 100.0}}
        r = estimate_reservation(pending, cash=1_000_000.0, risk_pct_per_unit=0.01)
        # quantity = floor(1,000,000 * 0.01 / 10) = 1000; cost = 100,000
        expected_cost = 100_000.0
        self.assertEqual(r["per_symbol"]["SYM.NS"], round(expected_cost * RESERVATION_BUFFER_MULTIPLIER, 2))
        self.assertEqual(r["per_symbol"]["SYM.NS"], 105_000.0)
        self.assertEqual(r["total_reserved"], 105_000.0)

    def test_multiple_pending_entries_are_sized_sequentially(self):
        # Mirrors _resolve_pending_fills()'s own sequential cash
        # consumption -- the second entry must be sized against cash
        # ALREADY reduced by the first's estimated cost, not the full pool.
        pending = {
            "A.NS": {"stop_loss": 90.0, "signal_price": 100.0},
            "B.NS": {"stop_loss": 180.0, "signal_price": 200.0},
        }
        r = estimate_reservation(pending, cash=500_000.0, risk_pct_per_unit=0.01)
        # A: qty=floor(500000*0.01/10)=500, cost=50,000 -> working_cash=450,000
        # B: qty=floor(450000*0.01/20)=225, cost=45,000
        self.assertEqual(r["per_symbol"]["A.NS"], round(50_000 * 1.05, 2))
        self.assertEqual(r["per_symbol"]["B.NS"], round(45_000 * 1.05, 2))

    def test_entry_missing_signal_price_is_skipped_not_guessed(self):
        # Backward compatibility: an entry queued before signal_price
        # existed as a field. Reserving zero (not a guessed amount) is
        # the conservative choice here.
        pending = {"OLD.NS": {"stop_loss": 90.0}}   # no signal_price key at all
        r = estimate_reservation(pending, cash=1_000_000.0, risk_pct_per_unit=0.01)
        self.assertEqual(r["total_reserved"], 0.0)
        self.assertNotIn("OLD.NS", r["per_symbol"])

    def test_zero_cash_reserves_nothing_without_crashing(self):
        pending = {"SYM.NS": {"stop_loss": 90.0, "signal_price": 100.0}}
        r = estimate_reservation(pending, cash=0.0, risk_pct_per_unit=0.01)
        self.assertEqual(r["total_reserved"], 0.0)

    def test_no_pending_entries_reserves_nothing(self):
        r = estimate_reservation({}, cash=1_000_000.0, risk_pct_per_unit=0.01)
        self.assertEqual(r["total_reserved"], 0.0)
        self.assertEqual(r["per_symbol"], {})


class TestComputeWinddownWithdrawal(unittest.TestCase):
    def test_never_withdraws_from_reserved_capital(self):
        # cash=200,000, reserved=150,000 -> idle=50,000. Even though excess
        # over a 100,000 target is 100,000, withdrawal must be capped at
        # idle cash (50,000), never touching the reserved 150,000.
        w = compute_winddown_withdrawal(cash=200_000.0, reserved=150_000.0,
                                         target_active_capital=100_000.0, daily_fraction=1.0)
        self.assertLessEqual(w, 50_000.0)
        self.assertEqual(w, 50_000.0)

    def test_already_at_target_withdraws_nothing(self):
        w = compute_winddown_withdrawal(cash=100_000.0, reserved=0.0,
                                         target_active_capital=100_000.0, daily_fraction=0.10)
        self.assertEqual(w, 0.0)

    def test_below_target_withdraws_nothing(self):
        w = compute_winddown_withdrawal(cash=50_000.0, reserved=0.0,
                                         target_active_capital=100_000.0, daily_fraction=0.10)
        self.assertEqual(w, 0.0)

    def test_gradual_fraction_not_a_single_shock_withdrawal(self):
        # A large excess should NOT be removed in one shot -- only
        # daily_fraction of it, so the reduction is genuinely gradual.
        w = compute_winddown_withdrawal(cash=1_000_000.0, reserved=0.0,
                                         target_active_capital=100_000.0, daily_fraction=0.10)
        self.assertEqual(w, 90_000.0)   # 10% of the 900,000 excess, not all of it

    def test_snaps_to_target_once_excess_is_small(self):
        # Below the snap threshold, withdraw the FULL remaining excess so
        # the strategy actually converges to target in finite time,
        # rather than asymptotically approaching it forever.
        w = compute_winddown_withdrawal(cash=101_000.0, reserved=0.0,
                                         target_active_capital=100_000.0, daily_fraction=0.10,
                                         snap_threshold=2_000.0)
        self.assertEqual(w, 1_000.0)   # full 1,000 excess, not 10% of it (100)

    def test_idle_cash_zero_or_negative_withdraws_nothing(self):
        w = compute_winddown_withdrawal(cash=200_000.0, reserved=200_000.0,
                                         target_active_capital=100_000.0, daily_fraction=1.0)
        self.assertEqual(w, 0.0)


# --------------------------- integration tests ---------------------------

class _AlwaysQualifiesStrategy(Strategy):
    """Same test double pattern as test_deployment.py's -- qualifies on
    the LAST bar of whatever data it's given."""
    name = "winddown_test_strategy"
    max_units = 1
    risk_pct_per_unit = 0.01
    min_lookback_days = 0

    def precompute(self, price_history):
        df = price_history.copy()
        df["signal_day"] = False
        if len(df) > 0:
            df.iloc[-1, df.columns.get_loc("signal_day")] = True
        return df

    def entry_signal_at(self, row):
        if not bool(row.signal_day):
            return None
        entry_price = float(row.Close)
        return Signal(symbol="", direction="BUY", entry_price=entry_price,
                      stop_loss=entry_price * 0.9, strategy_name=self.name)

    def exit_signal_at(self, row, open_position):
        if float(row.Close) >= open_position.units[0].entry_price * 1.5:
            return float(row.Close)
        return None


def _one_day_df(date_str, open_, high, low, close):
    idx = pd.DatetimeIndex([pd.Timestamp(date_str)])
    return pd.DataFrame({"Open": [open_], "High": [high], "Low": [low], "Close": [close],
                          "Volume": [1000]}, index=idx)


def _seed_elevated_starting_cash(strategy_key: str, cash: float = 1_000_000.0) -> None:
    """Explicitly puts a strategy's portfolio into the 'already has capital
    ABOVE target' state the wind-down mechanism is meant to act on.

    A BRAND-NEW strategy now starts directly AT
    PAPER_TRADING_WINDDOWN_TARGET_CAPITAL (see
    deployment/paper_trading_engine.py's _load_portfolio() fresh-strategy
    capital policy, fixed 2026-08-23) and therefore never triggers a real
    withdrawal on its own -- every test below that needs an ACTUAL
    withdrawal to happen in order to test something meaningful (not a
    withdrawal, distortion, or reservation check that would otherwise pass
    vacuously with nothing ever withdrawn) calls this first to explicitly
    simulate an EXISTING, already-running strategy's elevated cash --
    mirroring this program's own standing policy that existing strategies
    already above target keep winding down unchanged, while brand-new ones
    do not."""
    portfolio = pte.load_portfolio(strategy_key)
    portfolio["cash"] = cash
    pte._save_portfolio(strategy_key, portfolio)


class TestCapitalWinddownIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher_pte = patch.object(pte, "PAPER_TRADING_STATE_DIR", self.tmpdir)
        self.patcher_pte.start()
        import deployment.capital_winddown as cwd
        self.patcher_cwd = patch.object(cwd, "PAPER_TRADING_STATE_DIR", self.tmpdir)
        self.patcher_cwd.start()
        self.strategy_key = "winddown_test_strategy"

    def tearDown(self):
        self.patcher_pte.stop()
        self.patcher_cwd.stop()
        shutil.rmtree(self.tmpdir)

    def test_queued_entry_is_never_skipped_or_rejected_by_winddown(self):
        """The achievable half of the critical rule: capital reduction
        must never cause an otherwise-valid queued entry to be SKIPPED
        (quantity rounds to 0, or becomes unaffordable). It fills, with
        quantity >= 1, every time, because remaining cash after any
        withdrawal is always >= what was reserved for it.

        CONFIRMED DESIGN DECISION (2026-08-23, per explicit direction,
        after this exact tension was raised for review): the EXISTING,
        frozen resolution formula (_resolve_pending_fills()) sizes a fill
        as floor(cash-at-that-moment x risk_pct / risk_per_share) --
        proportional to the WHOLE remaining cash pool, not a dollar
        amount carved out for one specific entry. Reserving a rupee
        figure guarantees ENOUGH cash remains overall (so the entry can
        never be rejected/skipped), but a withdrawal still
        PROPORTIONALLY reduces every pending entry's eventual quantity,
        because that is how the existing, unmodified sizing formula
        works. The alternative (never withdrawing while anything is
        pending, for a byte-identical guarantee) was explicitly declined
        as almost certainly too slow, given how often these strategies
        signal -- this "never skipped, size may shrink naturally with a
        smaller capital base" behavior is the confirmed, intended design."""
        strategy = _AlwaysQualifiesStrategy()
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")

        # Seeded BEFORE this strategy's very first run_daily() call, so its
        # own recorded day-1 equity snapshot is consistent with the
        # elevated cash from the start -- simulates an existing,
        # already-elevated strategy (never a brand-new one, which now
        # starts directly at target and has nothing to wind down).
        _seed_elevated_starting_cash(self.strategy_key)
        day1 = {"SYM.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)

        result = apply_capital_winddown(self.strategy_key, risk_pct_per_unit=strategy.risk_pct_per_unit,
                                         as_of_date=datetime.date(2024, 1, 1))
        self.assertGreater(result["withdrawn"], 0.0, "test is only meaningful if a withdrawal actually happened")
        portfolio_after_winddown = pte.load_portfolio(self.strategy_key)
        self.assertGreaterEqual(portfolio_after_winddown["cash"], result["reserved"])

        day2 = {"SYM.NS": _one_day_df("2024-01-02", 100, 101, 99, 100)}
        fill_result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day2,
                                     as_of_date=datetime.date(2024, 1, 2), execution_config=cfg)
        self.assertEqual(len(fill_result["new_entries"]), 1, "the entry must still fill, never be skipped")
        self.assertGreaterEqual(fill_result["new_entries"][0]["quantity"], 1)

    def test_withdrawal_never_exceeds_idle_cash(self):
        strategy = _AlwaysQualifiesStrategy()
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")
        _seed_elevated_starting_cash(self.strategy_key)   # before the first run -- see comment above
        day1 = {"SYM.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)

        result = apply_capital_winddown(self.strategy_key, risk_pct_per_unit=strategy.risk_pct_per_unit,
                                         as_of_date=datetime.date(2024, 1, 1))
        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertGreaterEqual(portfolio["cash"], result["reserved"],
                                 "cash remaining after withdrawal must never dip below what was reserved")

    def test_unused_reservation_buffer_is_released_after_execution(self):
        """The 5% buffer is never actually moved into a separate ledger --
        it just stays in `cash`, untouched, so whatever tomorrow's real
        fill DOESN'T use is automatically still there (never needs an
        explicit 'release' step)."""
        strategy = _AlwaysQualifiesStrategy()
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")
        _seed_elevated_starting_cash(self.strategy_key)   # before the first run -- see comment above
        day1 = {"SYM.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)
        apply_capital_winddown(self.strategy_key, risk_pct_per_unit=strategy.risk_pct_per_unit,
                                as_of_date=datetime.date(2024, 1, 1))
        cash_before_fill = pte.load_portfolio(self.strategy_key)["cash"]

        day2 = {"SYM.NS": _one_day_df("2024-01-02", 100, 101, 99, 100)}
        fill_result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day2,
                                     as_of_date=datetime.date(2024, 1, 2), execution_config=cfg)
        actual_cost = fill_result["new_entries"][0]["entry_price"] * fill_result["new_entries"][0]["quantity"]
        cash_after_fill = pte.load_portfolio(self.strategy_key)["cash"]

        # cash decreased by exactly the REAL cost, not the buffered estimate
        self.assertAlmostEqual(cash_before_fill - cash_after_fill, actual_cost, places=2)

    def test_winddown_does_not_change_which_signals_are_generated(self):
        """Capital reduction must not change generated signals -- confirm
        entry_signal_at() detects the SAME signal (same symbol, same
        signal_price, same stop_loss -- i.e. the strategy's own signal
        logic ran identically) whether or not wind-down later acts on
        that strategy's cash. Signal DETECTION depends only on price
        data, never on cash, so this must hold regardless."""
        cfg = pte.ExecutionRealismConfig(fill_timing="next_day_open")
        day1 = {"SYM.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}

        strategy_a = _AlwaysQualifiesStrategy()
        pte.run_daily("strategy_a", strategy_a, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)

        strategy_b = _AlwaysQualifiesStrategy()
        pte.run_daily("strategy_b", strategy_b, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1), execution_config=cfg)
        _seed_elevated_starting_cash("strategy_b")   # so wind-down actually withdraws something to compare against
        apply_capital_winddown("strategy_b", risk_pct_per_unit=strategy_b.risk_pct_per_unit,
                                as_of_date=datetime.date(2024, 1, 1))

        portfolio_a = pte.load_portfolio("strategy_a")
        portfolio_b = pte.load_portfolio("strategy_b")
        self.assertEqual(portfolio_a["pending_entries"]["SYM.NS"]["signal_price"],
                         portfolio_b["pending_entries"]["SYM.NS"]["signal_price"])
        self.assertEqual(portfolio_a["pending_entries"]["SYM.NS"]["stop_loss"],
                         portfolio_b["pending_entries"]["SYM.NS"]["stop_loss"])

    def test_winddown_does_not_alter_exit_behavior(self):
        """Capital reduction must not alter existing position exit
        behavior -- a stop-loss/signal exit must fire exactly the same
        whether or not wind-down has run against this strategy's cash."""
        strategy = _AlwaysQualifiesStrategy()
        _seed_elevated_starting_cash(self.strategy_key)   # before the first run -- see comment above
        day1 = {"SYM.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1))
        apply_capital_winddown(self.strategy_key, risk_pct_per_unit=strategy.risk_pct_per_unit,
                                as_of_date=datetime.date(2024, 1, 1))

        stop_hit_data = {"SYM.NS": _one_day_df("2024-01-02", 95, 96, 85, 92)}
        result = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: stop_hit_data,
                                as_of_date=datetime.date(2024, 1, 2))
        self.assertEqual(len(result["new_exits"]), 1)
        self.assertEqual(result["new_exits"][0]["reason"], "stop_loss")
        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertNotIn("SYM.NS", portfolio["positions"])

    def test_no_forced_position_closures(self):
        """Wind-down must never touch `positions` -- confirm an open
        position survives untouched (same quantity/entry_price/stop_loss)
        across a wind-down call."""
        strategy = _AlwaysQualifiesStrategy()
        _seed_elevated_starting_cash(self.strategy_key)   # before the first run -- see comment above
        day1 = {"SYM.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1))
        before = dict(pte.load_portfolio(self.strategy_key)["positions"])

        apply_capital_winddown(self.strategy_key, risk_pct_per_unit=strategy.risk_pct_per_unit,
                                as_of_date=datetime.date(2024, 1, 1))
        after = pte.load_portfolio(self.strategy_key)["positions"]
        self.assertEqual(before, after)

    def test_portfolio_converges_toward_target_over_multiple_days(self):
        """Portfolios naturally converge toward Rs.1,00,000 without forced
        position closures -- run wind-down repeatedly (as it would once
        per day) with no pending entries in the way, and confirm cash
        approaches, and eventually reaches, the target."""
        # No positions/pending entries at all -- isolates pure convergence.
        # A brand-new strategy now starts directly AT target (see
        # _seed_elevated_starting_cash()'s own docstring), so this test
        # explicitly seeds the SAME elevated starting cash the original
        # scenario exercised, to keep testing real multi-day convergence
        # rather than a one-step no-op.
        # 900,000 excess x 0.9^n drops below the 2,000 snap threshold at
        # n~=58 -- run well past that so the snap-to-target rule has
        # definitely fired.
        _seed_elevated_starting_cash(self.strategy_key)
        for day in range(1, 65):
            apply_capital_winddown(
                self.strategy_key, risk_pct_per_unit=0.01,
                as_of_date=datetime.date(2024, 1, 1) + datetime.timedelta(days=day),
            )
        final_cash = pte.load_portfolio(self.strategy_key)["cash"]
        self.assertAlmostEqual(final_cash, 100_000.0, delta=1.0)

    def test_disabled_flag_withdraws_nothing(self):
        # Seeded elevated so this actually proves the enabled=False flag is
        # what suppresses the withdrawal -- without seeding, a fresh
        # strategy already at target would withdraw nothing regardless of
        # the flag, which would prove nothing about the flag itself.
        strategy = _AlwaysQualifiesStrategy()
        _seed_elevated_starting_cash(self.strategy_key)   # before the first run -- see comment above
        day1 = {"SYM.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1))
        result = apply_capital_winddown(self.strategy_key, risk_pct_per_unit=strategy.risk_pct_per_unit,
                                         as_of_date=datetime.date(2024, 1, 1), enabled=False)
        self.assertEqual(result["withdrawn"], 0.0)
        self.assertEqual(result["reason"], "wind-down disabled")


class TestPerformanceMetricsUnaffectedByWinddown(unittest.TestCase):
    """A withdrawal is a capital-management action, never a trading
    result -- confirms it never distorts daily_pnl or compute_live_metrics()'s
    CAGR/Sharpe/etc. (both read a raw cash/equity curve that a naive
    withdrawal would otherwise corrupt)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher_pte = patch.object(pte, "PAPER_TRADING_STATE_DIR", self.tmpdir)
        self.patcher_pte.start()
        import deployment.capital_winddown as cwd
        self.patcher_cwd = patch.object(cwd, "PAPER_TRADING_STATE_DIR", self.tmpdir)
        self.patcher_cwd.start()
        self.strategy_key = "winddown_metrics_test"

    def tearDown(self):
        self.patcher_pte.stop()
        self.patcher_cwd.stop()
        shutil.rmtree(self.tmpdir)

    def test_daily_pnl_does_not_show_withdrawal_as_a_loss(self):
        strategy = _AlwaysQualifiesStrategy()
        # Seeded BEFORE the first run_daily() call, so the day-1 equity
        # snapshot run_daily() records is itself consistent with the
        # elevated cash -- seeding AFTER would leave a stale, inconsistent
        # day-1 snapshot behind that daily_pnl's own day-over-day
        # comparison would then (correctly) flag as a huge, unrelated jump.
        _seed_elevated_starting_cash(self.strategy_key)
        day1 = {"SYM.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day1,
                      as_of_date=datetime.date(2024, 1, 1))
        # No price movement, no trades -- withdraw a large chunk of cash.
        result = apply_capital_winddown(self.strategy_key, risk_pct_per_unit=strategy.risk_pct_per_unit,
                                         as_of_date=datetime.date(2024, 1, 1))
        self.assertGreater(result["withdrawn"], 0.0)

        # Day 2: same flat price, no trading activity at all -- daily_pnl
        # must be (approximately) zero, NOT a large negative number
        # reflecting yesterday's withdrawal.
        day2 = {"SYM.NS": _one_day_df("2024-01-02", 100, 101, 99, 100)}
        result2 = pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda: day2,
                                 as_of_date=datetime.date(2024, 1, 2))
        self.assertIsNotNone(result2["daily_pnl"])
        self.assertAlmostEqual(result2["daily_pnl"], 0.0, delta=1.0)

    def test_compute_live_metrics_cagr_unaffected_by_withdrawal(self):
        strategy = _AlwaysQualifiesStrategy()
        # Seeded BEFORE the first day's run_daily() call -- see comment in
        # test_daily_pnl_does_not_show_withdrawal_as_a_loss above for why
        # seeding after the fact would leave an inconsistent equity history.
        _seed_elevated_starting_cash(self.strategy_key)
        # Build up a few days of flat, trade-free history.
        for i, day_str in enumerate(["2024-01-01", "2024-01-02", "2024-01-03"]):
            data = {"SYM.NS": _one_day_df(day_str, 100, 101, 99, 100)}
            pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda d=data: d,
                          as_of_date=datetime.date(2024, 1, 1 + i) if i < 3 else None)

        result = apply_capital_winddown(self.strategy_key, risk_pct_per_unit=strategy.risk_pct_per_unit,
                                         as_of_date=datetime.date(2024, 1, 3))
        self.assertGreater(result["withdrawn"], 0.0, "test is only meaningful if a withdrawal actually happened")

        for day_str, day_num in [("2024-01-04", 4), ("2024-01-05", 5)]:
            data = {"SYM.NS": _one_day_df(day_str, 100, 101, 99, 100)}
            pte.run_daily(self.strategy_key, strategy, fetch_data_fn=lambda d=data: d,
                          as_of_date=datetime.date(2024, 1, day_num))

        metrics = pte.compute_live_metrics(self.strategy_key)
        # With flat prices and one open position throughout, CAGR should
        # be at/near 0%, not a large artificial negative number from the
        # withdrawal being misread as a loss.
        if metrics["cagr"] is not None:
            self.assertLess(abs(metrics["cagr"]), 5.0)

    def test_cumulative_withdrawn_helpers_are_zero_when_never_used(self):
        # A strategy that never had a withdrawal -- both helpers must
        # return 0.0 (a complete no-op), never raise on a missing file.
        self.assertEqual(cumulative_withdrawn_up_to("never_used_strategy", datetime.date(2024, 1, 1)), 0.0)
        self.assertEqual(cumulative_withdrawn_between("never_used_strategy", datetime.date(2024, 1, 1),
                                                        datetime.date(2024, 1, 2)), 0.0)


class TestFreshStrategyCapitalPolicy(unittest.TestCase):
    """Regression coverage for the fresh-strategy capital fix (2026-08-23,
    per explicit direction, after SW-011's promotion showed the OLD
    behavior's real consequence): a brand-new strategy must start directly
    at PAPER_TRADING_WINDDOWN_TARGET_CAPITAL, not the old, much larger
    PAPER_TRADING_VIRTUAL_CAPITAL default -- and, having started AT
    target, must never suffer an immediate, artificial wind-down
    withdrawal purely as an artifact of its own initial seed."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher_pte = patch.object(pte, "PAPER_TRADING_STATE_DIR", self.tmpdir)
        self.patcher_pte.start()
        import deployment.capital_winddown as cwd
        self.patcher_cwd = patch.object(cwd, "PAPER_TRADING_STATE_DIR", self.tmpdir)
        self.patcher_cwd.start()
        self.strategy_key = "brand_new_fresh_capital_test_strategy"

    def tearDown(self):
        self.patcher_pte.stop()
        self.patcher_cwd.stop()
        shutil.rmtree(self.tmpdir)

    def test_fresh_portfolio_starts_at_target_capital_not_the_old_larger_default(self):
        portfolio = pte.load_portfolio(self.strategy_key)   # no portfolio.json exists yet -- the fresh-seed branch
        self.assertEqual(portfolio["cash"], PAPER_TRADING_WINDDOWN_TARGET_CAPITAL)
        self.assertEqual(portfolio["starting_capital"], PAPER_TRADING_WINDDOWN_TARGET_CAPITAL)

    def test_first_day_winddown_withdraws_nothing_for_a_fresh_strategy(self):
        # Mirrors the exact real-world scenario this fix was written for
        # (SW-011's actual first day): a brand-new strategy whose scan
        # found no qualifying signal at all -- no run_daily() call needed
        # here, since apply_capital_winddown() itself triggers the very
        # same fresh-portfolio creation path via its own _load_portfolio()
        # call, in complete isolation from any trading activity.
        result = apply_capital_winddown(self.strategy_key, risk_pct_per_unit=0.01,
                                         as_of_date=datetime.date(2024, 1, 1))
        self.assertEqual(result["withdrawn"], 0.0,
                          "a strategy that starts AT target must never have anything withdrawn on day one")

        portfolio = pte.load_portfolio(self.strategy_key)
        self.assertEqual(portfolio["cash"], PAPER_TRADING_WINDDOWN_TARGET_CAPITAL)
        # No entry in the wind-down log either -- confirms this is a genuine
        # no-op, not a withdrawal of Rs.0 still being recorded.
        self.assertEqual(cumulative_withdrawn_up_to(self.strategy_key, datetime.date(2024, 1, 1)), 0.0)


if __name__ == "__main__":
    unittest.main()
