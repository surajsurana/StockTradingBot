"""
Unit tests for research_lab/strategies/end_of_day_reversal.py, the
MarketState.return_since_prior_close field it depends on, and one
end-to-end pass through market_simulator with hand-calculated trades.
Run with:

    python test_end_of_day_reversal.py
"""

import unittest
from datetime import date, time

import pandas as pd

from research_lab.market_simulator import simulate_universe_cross_sectional
from research_lab.market_state import MarketState, compute_market_state
from research_lab.strategies.end_of_day_reversal import EndOfDayReversalStrategy


def _bars(date_str, times, closes):
    idx = pd.DatetimeIndex([pd.Timestamp(f"{date_str} {t}") for t in times])
    return pd.DataFrame([{"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1000} for c in closes], index=idx)


def _state(returns):
    return MarketState(timestamp=None, breadth_pct_above_vwap=50.0, universe_size=len(returns),
                       return_since_prior_close=returns)


def _rod(close, prior_close=100.0):
    # the SAME arithmetic compute_market_state() uses, so a symbol's own
    # value and its universe entry agree to the last floating-point bit
    return (close / prior_close - 1) * 100


UNIVERSE = {"S1": _rod(97), "S2": _rod(99), "S3": _rod(100), "S4": _rod(101), "S5": _rod(103)}


class TestReturnSincePriorClose(unittest.TestCase):
    def test_populated_from_prior_closes_and_skips_missing_ones(self):
        bars = {"A": _bars("2026-02-03", ["09:15", "09:20"], [100, 102]),
                "B": _bars("2026-02-03", ["09:15", "09:20"], [50, 49]),
                "C": _bars("2026-02-03", ["09:15", "09:20"], [10, 11])}
        state = compute_market_state(bars, {}, bars["A"].index[-1],
                                     prior_close_by_symbol={"A": 100.0, "B": 50.0, "C": None})
        self.assertAlmostEqual(state.return_since_prior_close["A"], 2.0)
        self.assertAlmostEqual(state.return_since_prior_close["B"], -2.0)
        self.assertNotIn("C", state.return_since_prior_close)

    def test_empty_when_no_prior_closes_given(self):
        bars = {"A": _bars("2026-02-03", ["09:15"], [100])}
        self.assertEqual(compute_market_state(bars, {}, bars["A"].index[-1]).return_since_prior_close, {})


class TestEndOfDayReversalStrategy(unittest.TestCase):
    def setUp(self):
        self.strategy = EndOfDayReversalStrategy(min_universe=5)
        self.context = {"prior_close": 100.0}

    def test_bottom_quintile_loser_is_bought_with_symmetric_five_pct_stop_and_target(self):
        bars = _bars("2026-02-03", ["09:15", "14:55"], [100, 97])
        signal = self.strategy.generate_signal(bars, self.context, _state(UNIVERSE))
        self.assertEqual(signal.direction, "BUY")
        self.assertAlmostEqual(signal.entry_price, 97.0)
        self.assertAlmostEqual(signal.stop_loss, 97 * 0.95)
        self.assertAlmostEqual(signal.target, 97 * 1.05)

    def test_top_quintile_winner_is_shorted(self):
        bars = _bars("2026-02-03", ["09:15", "14:55"], [100, 103])
        signal = self.strategy.generate_signal(bars, self.context, _state(UNIVERSE))
        self.assertEqual(signal.direction, "SELL")
        self.assertAlmostEqual(signal.stop_loss, 103 * 1.05)
        self.assertAlmostEqual(signal.target, 103 * 0.95)

    def test_middle_of_the_pack_does_not_trade(self):
        for close in (99.0, 100.0, 101.0):
            bars = _bars("2026-02-03", ["09:15", "14:55"], [100, close])
            self.assertIsNone(self.strategy.generate_signal(bars, self.context, _state(UNIVERSE)))

    def test_only_fires_in_the_entry_window(self):
        for t in ("12:00", "14:50", "15:00", "15:25"):
            bars = _bars("2026-02-03", ["09:15", t], [100, 97])
            self.assertIsNone(self.strategy.generate_signal(bars, self.context, _state(UNIVERSE)), msg=t)
        bars = _bars("2026-02-03", ["09:15"], [97])
        self.assertIsNone(self.strategy.generate_signal(bars, self.context, _state(UNIVERSE)))

    def test_none_without_market_state_context_or_enough_universe(self):
        bars = _bars("2026-02-03", ["09:15", "14:55"], [100, 97])
        self.assertIsNone(self.strategy.generate_signal(bars, self.context, None))
        self.assertIsNone(self.strategy.generate_signal(bars, None, _state(UNIVERSE)))
        self.assertIsNone(self.strategy.generate_signal(bars, {"prior_close": None}, _state(UNIVERSE)))
        self.assertIsNone(self.strategy.generate_signal(bars, self.context, _state({"S1": _rod(97), "S2": _rod(103)})))


class TestEndToEndThroughMarketSimulator(unittest.TestCase):
    """Five symbols, all closed at 100 yesterday. At 14:55 today they sit
    at -3/-1/0/+1/+3% -> S1 (bottom quintile) is bought at 97, S5 (top
    quintile) shorted at 103, both squared off on the 15:25 bar. Sizing:
    100000 x 1% risk / (5% x entry)."""

    def test_two_trades_with_hand_calculated_pnl(self):
        times = ["09:15", "14:50", "14:55", "15:25"]
        prior = {s: _bars("2026-02-02", times, [100, 100, 100, 100]) for s in UNIVERSE}
        today_closes = {"S1": [100, 98, 97, 98], "S2": [100, 99, 99, 99], "S3": [100, 100, 100, 100],
                        "S4": [100, 101, 101, 101], "S5": [100, 102, 103, 102]}
        data = {s: pd.concat([prior[s], _bars("2026-02-03", times, today_closes[s])]) for s in UNIVERSE}

        result = simulate_universe_cross_sectional(
            data, EndOfDayReversalStrategy(min_universe=5), capital_per_symbol=100000, risk_per_trade_pct=0.01,
            sector_map={},
        )
        trades = {t.symbol: t for t in result["trades"]}
        self.assertEqual(set(trades), {"S1", "S5"})

        s1 = trades["S1"]
        self.assertEqual(s1.direction, "BUY")
        self.assertEqual(s1.exit_reason, "eod_square_off")
        self.assertAlmostEqual(s1.entry_hour, 14 + 55 / 60)
        self.assertEqual(s1.quantity, int(1000 / (97 * 0.05)))        # 206
        self.assertAlmostEqual(s1.pnl, (98 - 97) * s1.quantity)         # +206

        s5 = trades["S5"]
        self.assertEqual(s5.direction, "SELL")
        self.assertEqual(s5.exit_reason, "eod_square_off")
        self.assertEqual(s5.quantity, int(1000 / (103 * 0.05)))        # 194
        self.assertAlmostEqual(s5.pnl, (103 - 102) * s5.quantity)       # +194
        self.assertEqual(result["trading_calendar"], [date(2026, 2, 2), date(2026, 2, 3)])


if __name__ == "__main__":
    unittest.main()
