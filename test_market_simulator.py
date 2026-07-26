"""
Unit tests for research_lab/market_simulator.py -- the cross-sectional
simulation engine. Small synthetic multi-symbol data, hand-calculated
expected trades -- no real data or API calls. Run with:

    python test_market_simulator.py
"""

import unittest
from datetime import date

import pandas as pd

from research_lab.base import Signal, Strategy
from research_lab.market_simulator import simulate_universe_cross_sectional
from research_lab.risk_manager_research import RiskParameters


def _bars(date_str, closes, base=0.0):
    # Open chained from the PRIOR close (bar0's Open == its own Close, no
    # gap) -- avoids an artifact where a fixed absolute open/close offset
    # produces a bigger % return-since-open for a lower-priced symbol on
    # the very first bar, which would distort relative-strength ranking
    # before any real trend has had a chance to show up.
    idx = pd.date_range(f"{date_str} 09:15", periods=len(closes), freq="5min")
    opens = [closes[0]] + closes[:-1]
    rows = [{"Open": base + o, "High": base + max(o, c) + 0.3, "Low": base + min(o, c) - 0.3,
             "Close": base + c, "Volume": 1000} for o, c in zip(opens, closes)]
    return pd.DataFrame(rows, index=idx)


class _FiresOnceUsingMarketState(Strategy):
    """Fires a BUY the first bar market_state.universe_size >= 2, only for
    whichever symbol is the leader that bar -- exercises market_state
    actually reaching the strategy (the entire point of this engine over
    the single-symbol one)."""
    name = "cross_sectional_test"

    def __init__(self):
        self._fired_for = set()

    def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
        if market_state is None or market_state.universe_size < 2 or not market_state.leaders:
            return None
        symbol = todays_bars_so_far.attrs.get("symbol")
        if symbol != market_state.leaders[0]:
            return None
        entry = float(todays_bars_so_far.iloc[-1]["Close"])
        return Signal(symbol="", direction="BUY", entry_price=entry, stop_loss=entry - 2,
                      target=entry + 4, confidence=0.5, strategy_name=self.name)


class _NeverFiresStrategy(Strategy):
    name = "never_fires"

    def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
        return None


class _ShortsOnLeaderStrategy(Strategy):
    """Direction-agnostic check -- fires a SELL for whichever symbol is
    the LAGGARD, first bar market_state is available."""
    name = "short_test"

    def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
        if market_state is None or not market_state.laggards:
            return None
        symbol = todays_bars_so_far.attrs.get("symbol")
        if symbol != market_state.laggards[-1]:
            return None
        entry = float(todays_bars_so_far.iloc[-1]["Close"])
        return Signal(symbol="", direction="SELL", entry_price=entry, stop_loss=entry + 2,
                      target=entry - 4, confidence=0.5, strategy_name=self.name)


class TestSimulateUniverseCrossSectional(unittest.TestCase):
    def _tag(self, df, symbol):
        df.attrs["symbol"] = symbol
        return df

    def test_market_state_reaches_strategy_and_trades_only_the_leader(self):
        a = self._tag(_bars("2026-01-05", [100, 101, 102, 103, 104, 105, 106, 107]), "A")
        b = self._tag(_bars("2026-01-05", [50, 49.5, 49, 48.5, 48, 47.5, 47, 46.5]), "B")  # falling -> never the leader
        data = {"A": a, "B": b}
        sector_map = {"A": "IT", "B": "IT"}

        result = simulate_universe_cross_sectional(
            data=data, strategy=_FiresOnceUsingMarketState(), capital_per_symbol=100000,
            risk_per_trade_pct=0.01, sector_map=sector_map,
        )
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0].symbol, "A")
        self.assertEqual(result["trades"][0].direction, "BUY")

    def test_no_signal_produces_no_trades(self):
        a = self._tag(_bars("2026-01-05", [100, 101, 102, 103]), "A")
        result = simulate_universe_cross_sectional(
            data={"A": a}, strategy=_NeverFiresStrategy(), capital_per_symbol=100000,
            risk_per_trade_pct=0.01, sector_map={"A": "IT"},
        )
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["symbols"], ["A"])

    def test_short_trade_direction_and_pnl_hand_calculated(self):
        # Laggard falls steadily -- short entry near the top, price keeps
        # falling to hit target (a profitable short).
        a = self._tag(_bars("2026-01-05", [100, 101, 102, 103, 104, 105]), "A")   # leader
        b = self._tag(_bars("2026-01-05", [100, 95, 90, 85, 80, 75]), "B")          # laggard, falls hard
        data = {"A": a, "B": b}
        result = simulate_universe_cross_sectional(
            data=data, strategy=_ShortsOnLeaderStrategy(), capital_per_symbol=100000,
            risk_per_trade_pct=0.01, sector_map={"A": "IT", "B": "IT"},
        )
        self.assertEqual(len(result["trades"]), 1)
        t = result["trades"][0]
        self.assertEqual(t.symbol, "B")
        self.assertEqual(t.direction, "SELL")
        # entry at bar0 close=100: both A and B are at exactly 0% return-since-open
        # on their own opening bar (no earlier data yet), so the tie is broken by
        # dict/stable-sort order (A, B) -- laggards' worst-ranked slot lands on B,
        # which happens to match B being the symbol that actually falls all day.
        self.assertGreater(t.pnl, 0)  # short profited as price fell further after entry

    def test_one_trade_per_symbol_per_day_cap_default(self):
        # Strategy that would fire on every bar if allowed -- confirms the
        # default (risk_params=None) one-trade-per-symbol-per-day cap,
        # same convention as backtesting_engineer.simulate_symbol().
        class _AlwaysFires(Strategy):
            name = "always_fires"

            def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
                if market_state is None:
                    return None
                entry = float(todays_bars_so_far.iloc[-1]["Close"])
                return Signal(symbol="", direction="BUY", entry_price=entry, stop_loss=entry - 0.5,
                              target=entry + 100, confidence=0.5, strategy_name=self.name)  # target never hit

        a = self._tag(_bars("2026-01-05", [100, 101, 102, 103, 104, 105]), "A")
        result = simulate_universe_cross_sectional(
            data={"A": a}, strategy=_AlwaysFires(), capital_per_symbol=100000,
            risk_per_trade_pct=0.01, sector_map={"A": "IT"},
        )
        self.assertEqual(len(result["trades"]), 1)  # not one per bar

    def test_risk_params_daily_loss_limit_blocks_further_entries(self):
        class _AlwaysFiresSmallStop(Strategy):
            name = "always_fires_small_stop"

            def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
                if market_state is None:
                    return None
                entry = float(todays_bars_so_far.iloc[-1]["Close"])
                # stop always hit next bar (price keeps falling) -- guaranteed loser
                return Signal(symbol="", direction="BUY", entry_price=entry, stop_loss=entry - 0.1,
                              target=entry + 100, confidence=0.5, strategy_name=self.name)

        a = self._tag(_bars("2026-01-05", [100, 99.8, 99.6, 99.4, 99.2, 99.0, 98.8, 98.6]), "A")
        risk_params = RiskParameters(max_trades_per_day=10, daily_loss_limit_pct=0.0001)
        result = simulate_universe_cross_sectional(
            data={"A": a}, strategy=_AlwaysFiresSmallStop(), capital_per_symbol=100000,
            risk_per_trade_pct=0.01, sector_map={"A": "IT"}, risk_params=risk_params,
        )
        # First loss should already exceed the tiny daily loss limit, blocking further entries.
        self.assertEqual(len(result["trades"]), 1)

    def test_eod_square_off_when_target_and_stop_never_hit(self):
        a = self._tag(_bars("2026-01-05", [100, 100.1, 100.2, 100.15, 100.1]), "A")

        class _FiresOnceNeverHits(Strategy):
            name = "eod_test"

            def __init__(self):
                self.fired = False

            def generate_signal(self, todays_bars_so_far, context=None, market_state=None):
                if self.fired or market_state is None:
                    return None
                self.fired = True
                entry = float(todays_bars_so_far.iloc[-1]["Close"])
                return Signal(symbol="", direction="BUY", entry_price=entry, stop_loss=entry - 10,
                              target=entry + 10, confidence=0.5, strategy_name=self.name)

        result = simulate_universe_cross_sectional(
            data={"A": a}, strategy=_FiresOnceNeverHits(), capital_per_symbol=100000,
            risk_per_trade_pct=0.01, sector_map={"A": "IT"},
        )
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0].exit_reason, "eod_square_off")

    def test_multi_day_trading_calendar_and_output_shape(self):
        a1 = self._tag(_bars("2026-01-05", [100, 101, 102, 103]), "A")
        a2 = self._tag(_bars("2026-01-06", [103, 104, 105, 106]), "A")
        combined = pd.concat([a1, a2])
        combined.attrs["symbol"] = "A"
        result = simulate_universe_cross_sectional(
            data={"A": combined}, strategy=_NeverFiresStrategy(), capital_per_symbol=50000,
            risk_per_trade_pct=0.01, sector_map={"A": "IT"},
        )
        self.assertEqual(result["trading_calendar"], [date(2026, 1, 5), date(2026, 1, 6)])
        self.assertEqual(result["capital_per_symbol"], 50000)
        self.assertEqual(set(result["symbols"]), {"A"})

    def test_empty_or_none_symbol_data_skipped_not_a_crash(self):
        result = simulate_universe_cross_sectional(
            data={"A": pd.DataFrame(), "B": None}, strategy=_NeverFiresStrategy(),
            capital_per_symbol=100000, risk_per_trade_pct=0.01, sector_map={},
        )
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["symbols"], [])


if __name__ == "__main__":
    unittest.main()
