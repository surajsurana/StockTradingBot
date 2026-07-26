"""
Unit tests for research_lab/market_state.py -- the cross-sectional
MarketState snapshot. Expected values are hand-calculated on small
synthetic multi-symbol bar sets, matching this project's convention. Run
with:

    python test_market_state.py
"""

import unittest

import pandas as pd

from research_lab.market_state import compute_market_state, return_over_lookback_minutes_pct


def _bars(closes, opens=None, volume=1000):
    n = len(closes)
    idx = pd.date_range("2026-01-05 09:15", periods=n, freq="5min")
    opens = opens or [closes[0]] + closes[:-1]
    rows = [{"Open": o, "High": max(o, c) + 0.1, "Low": min(o, c) - 0.1, "Close": c, "Volume": volume}
            for o, c in zip(opens, closes)]
    return pd.DataFrame(rows, index=idx)


class TestComputeMarketState(unittest.TestCase):
    def test_breadth_hand_calculated(self):
        # A: monotonically rising -> ends above its own VWAP.
        # B: monotonically falling -> ends below its own VWAP.
        # C: monotonically rising -> ends above its own VWAP.
        bars = {
            "A": _bars([100, 101, 102, 103]),
            "B": _bars([100, 99, 98, 97]),
            "C": _bars([50, 50.5, 51, 51.5]),
        }
        sector_map = {"A": "IT", "B": "IT", "C": "Auto"}
        ts = bars["A"].index[-1]
        ms = compute_market_state(bars, sector_map, ts)

        self.assertAlmostEqual(ms.breadth_pct_above_vwap, 2 / 3 * 100)
        self.assertEqual(ms.universe_size, 3)

    def test_sector_breadth_hand_calculated(self):
        bars = {
            "A": _bars([100, 101, 102, 103]),   # IT, above VWAP
            "B": _bars([100, 99, 98, 97]),       # IT, below VWAP
            "C": _bars([50, 50.5, 51, 51.5]),     # Auto, above VWAP
        }
        sector_map = {"A": "IT", "B": "IT", "C": "Auto"}
        ts = bars["A"].index[-1]
        ms = compute_market_state(bars, sector_map, ts)

        self.assertAlmostEqual(ms.sector_breadth_pct_above_vwap["IT"], 50.0)
        self.assertAlmostEqual(ms.sector_breadth_pct_above_vwap["Auto"], 100.0)

    def test_relative_strength_and_leaders_laggards_ranked_correctly(self):
        bars = {
            "A": _bars([100, 101, 102, 103]),    # +3% since open
            "B": _bars([100, 97, 96, 94]),         # -6% since open
            "C": _bars([100, 106, 108, 112]),      # +12% since open -- top leader
        }
        sector_map = {"A": "IT", "B": "IT", "C": "Auto"}
        ts = bars["A"].index[-1]
        ms = compute_market_state(bars, sector_map, ts, leader_laggard_n=2)

        self.assertAlmostEqual(ms.relative_strength["A"], 3.0)
        self.assertAlmostEqual(ms.relative_strength["B"], -6.0)
        self.assertAlmostEqual(ms.relative_strength["C"], 12.0)
        self.assertEqual(ms.leaders, ["C", "A"])
        self.assertEqual(ms.laggards, ["A", "B"])

    def test_no_lookahead_only_uses_bars_up_to_and_including_timestamp(self):
        full = _bars([100, 101, 102, 999])  # a huge spike on the LAST bar
        bars_so_far = full.iloc[:3]  # caller only passes bars up to "now" -- spike excluded
        sector_map = {"A": "IT"}
        ts = bars_so_far.index[-1]
        ms = compute_market_state({"A": bars_so_far}, sector_map, ts)
        self.assertAlmostEqual(ms.relative_strength["A"], 2.0)  # (102-100)/100*100, not affected by the 999 spike

    def test_caller_omitting_a_stale_symbol_excludes_it_cleanly(self):
        # Per compute_market_state's own docstring, a symbol with no bar
        # yet at this timestamp must simply be OMITTED from the dict by
        # the caller (market_simulator.py does this) -- not passed in with
        # stale/older data. Confirms omission works cleanly, not that this
        # function itself detects staleness (it can't -- it only sees
        # whatever the caller hands it).
        bars = {"A": _bars([100, 101, 102, 103])}  # B omitted entirely, as a stale-data caller would do
        sector_map = {"A": "IT", "B": "IT"}
        ts = bars["A"].index[-1]
        ms = compute_market_state(bars, sector_map, ts)
        self.assertEqual(ms.universe_size, 1)
        self.assertNotIn("B", ms.relative_strength)

    def test_nifty_return_since_open_and_last_15min(self):
        bars = {"A": _bars([100, 101, 102, 103])}
        sector_map = {"A": "IT"}
        # Nifty: 09:15=20000, 09:20=20010, 09:25=20020, 09:30=20040
        nifty = _bars([20000, 20010, 20020, 20040])
        ts = bars["A"].index[-1]
        ms = compute_market_state(bars, sector_map, ts, nifty_bars_so_far=nifty)

        self.assertAlmostEqual(ms.nifty_return_since_open_pct, (20040 - 20000) / 20000 * 100)
        # Trailing 15 min ending at 09:30 covers back to (>=) 09:15 -- the day's open bar
        self.assertAlmostEqual(ms.nifty_return_last_15min_pct, (20040 - 20000) / 20000 * 100)

    def test_nifty_data_absent_gives_none_not_a_crash(self):
        bars = {"A": _bars([100, 101, 102, 103])}
        ms = compute_market_state(bars, {"A": "IT"}, bars["A"].index[-1], nifty_bars_so_far=None)
        self.assertIsNone(ms.nifty_return_since_open_pct)
        self.assertIsNone(ms.nifty_return_last_15min_pct)

    def test_empty_universe_gives_none_breadth_not_a_crash(self):
        ms = compute_market_state({}, {}, pd.Timestamp("2026-01-05 09:15"))
        self.assertIsNone(ms.breadth_pct_above_vwap)
        self.assertEqual(ms.universe_size, 0)
        self.assertEqual(ms.leaders, [])
        self.assertEqual(ms.laggards, [])


class TestReturnOverLookbackMinutes(unittest.TestCase):
    def test_hand_calculated_return(self):
        # 09:15=100, 09:20=101, 09:25=102, 09:30=103, 09:35=104 -- 5 bars, 5-min interval
        bars = _bars([100, 101, 102, 103, 104])
        result = return_over_lookback_minutes_pct(bars, lookback_minutes=15)
        # last bar 09:35; cutoff = 09:20; last bar AT OR BEFORE 09:20 is the 09:20 bar (close=101)
        self.assertAlmostEqual(result, (104 - 101) / 101 * 100)

    def test_none_when_not_enough_history_yet_today(self):
        bars = _bars([100, 101])  # only 09:15, 09:20 -- no bar 15 min before 09:20
        result = return_over_lookback_minutes_pct(bars, lookback_minutes=15)
        self.assertIsNone(result)

    def test_none_on_empty_or_none_input(self):
        self.assertIsNone(return_over_lookback_minutes_pct(None))
        self.assertIsNone(return_over_lookback_minutes_pct(pd.DataFrame()))

    def test_interval_agnostic_works_with_15min_candles(self):
        idx = pd.date_range("2026-01-05 09:15", periods=3, freq="15min")  # 09:15, 09:30, 09:45
        df = pd.DataFrame({"Open": [100, 102, 104], "High": [100.5, 102.5, 104.5],
                            "Low": [99.5, 101.5, 103.5], "Close": [100, 102, 104],
                            "Volume": [1000, 1000, 1000]}, index=idx)
        result = return_over_lookback_minutes_pct(df, lookback_minutes=15)
        # last bar 09:45; cutoff = 09:30; bar at 09:30 has close=102
        self.assertAlmostEqual(result, (104 - 102) / 102 * 100)


if __name__ == "__main__":
    unittest.main()
