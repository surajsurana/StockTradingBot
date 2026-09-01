"""
Tests for portfolio_c/engine.py -- the adapter from swing_research.base.Signal
to strategies.base.Signal, and the anchor-candidate collection step.
"""

import datetime
import unittest
from unittest.mock import patch

import pandas as pd

from portfolio_c.engine import adapt_swing_signal, collect_anchor_candidates
from swing_research.base import Signal as SwingSignal
from swing_research.strategy_catalog import PaperTradingStrategySpec


def _one_day_df(date_str, open_, high, low, close):
    idx = pd.DatetimeIndex([pd.Timestamp(date_str)])
    return pd.DataFrame({"Open": [open_], "High": [high], "Low": [low], "Close": [close],
                          "Volume": [1000]}, index=idx)


class _FakeAnchorStrategy:
    """Qualifies BUY on the last bar, same test-double convention as
    test_capital_winddown.py's _AlwaysQualifiesStrategy -- lets these
    tests check collect_anchor_candidates()'s MECHANICS (precompute call,
    extra_columns join, per-date row lookup, dict shape) without needing
    a real cross-sectional percentile computation across a full universe."""
    name = "fake_anchor"
    min_lookback_days = 0

    def precompute(self, price_history):
        df = price_history.copy()
        df["signal_day"] = True
        return df

    def entry_signal_at(self, row):
        if not bool(row.signal_day):
            return None
        entry_price = float(row.Close)
        return SwingSignal(symbol="", direction="BUY", entry_price=entry_price,
                            stop_loss=entry_price * 0.9, confidence=77.0, strategy_name=self.name,
                            reason="fake test signal")

    def exit_signal_at(self, row, open_position):
        return None


class TestAdaptSwingSignal(unittest.TestCase):
    def test_buy_target_is_two_r_above_entry(self):
        swing_signal = SwingSignal(symbol="ABC.NS", direction="BUY", entry_price=100.0,
                                    stop_loss=90.0, confidence=0.75, strategy_name="max_effect",
                                    reason="bottom decile MAX")
        agent_signal = adapt_swing_signal(swing_signal)
        self.assertEqual(agent_signal.symbol, "ABC.NS")
        self.assertEqual(agent_signal.direction, "BUY")
        self.assertEqual(agent_signal.entry_price, 100.0)
        self.assertEqual(agent_signal.stop_loss, 90.0)
        self.assertEqual(agent_signal.target, 120.0)   # 100 + 2*(100-90)
        self.assertEqual(agent_signal.confidence, 0.75)
        self.assertEqual(agent_signal.strategy_name, "max_effect")
        self.assertEqual(agent_signal.reason, "bottom decile MAX")

    def test_sell_target_is_two_r_below_entry(self):
        swing_signal = SwingSignal(symbol="XYZ.NS", direction="SELL", entry_price=100.0,
                                    stop_loss=110.0, confidence=0.6, strategy_name="short_term_reversal")
        agent_signal = adapt_swing_signal(swing_signal)
        self.assertEqual(agent_signal.target, 80.0)   # 100 - 2*(110-100)

    def test_confidence_and_reason_pass_through_unchanged(self):
        swing_signal = SwingSignal(symbol="Q.NS", direction="BUY", entry_price=50.0, stop_loss=45.0,
                                    confidence=0.42, strategy_name="s", reason="specific reason text")
        agent_signal = adapt_swing_signal(swing_signal)
        self.assertEqual(agent_signal.confidence, 0.42)
        self.assertEqual(agent_signal.reason, "specific reason text")


class TestCollectAnchorCandidates(unittest.TestCase):
    def setUp(self):
        spec = PaperTradingStrategySpec(
            strategy_key="fake_anchor", display_name="Fake Anchor",
            strategy_factory=lambda: _FakeAnchorStrategy(),
        )
        self.patcher = patch("portfolio_c.engine._SPECS_BY_KEY", {"fake_anchor": spec})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_symbol_with_a_signal_today_is_collected(self):
        data = {"AAA.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        candidates = collect_anchor_candidates(data, datetime.date(2024, 1, 1),
                                                anchor_strategy_keys=("fake_anchor",))
        self.assertIn("AAA.NS", candidates)
        self.assertIn("fake_anchor", candidates["AAA.NS"])
        signal = candidates["AAA.NS"]["fake_anchor"]
        self.assertEqual(signal.direction, "BUY")
        self.assertEqual(signal.entry_price, 100.0)

    def test_symbol_with_no_bar_on_as_of_date_is_skipped(self):
        data = {"AAA.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
        candidates = collect_anchor_candidates(data, datetime.date(2024, 1, 2),
                                                anchor_strategy_keys=("fake_anchor",))
        self.assertEqual(candidates, {})

    def test_empty_dataframe_is_skipped_without_crashing(self):
        data = {"AAA.NS": pd.DataFrame()}
        candidates = collect_anchor_candidates(data, datetime.date(2024, 1, 1),
                                                anchor_strategy_keys=("fake_anchor",))
        self.assertEqual(candidates, {})

    def test_symbol_flagged_by_two_anchor_strategies_carries_both(self):
        spec_a = PaperTradingStrategySpec(strategy_key="anchor_a", display_name="A",
                                           strategy_factory=lambda: _FakeAnchorStrategy())
        spec_b = PaperTradingStrategySpec(strategy_key="anchor_b", display_name="B",
                                           strategy_factory=lambda: _FakeAnchorStrategy())
        with patch("portfolio_c.engine._SPECS_BY_KEY", {"anchor_a": spec_a, "anchor_b": spec_b}):
            data = {"AAA.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
            candidates = collect_anchor_candidates(data, datetime.date(2024, 1, 1),
                                                    anchor_strategy_keys=("anchor_a", "anchor_b"))
            self.assertEqual(set(candidates["AAA.NS"].keys()), {"anchor_a", "anchor_b"})

    def test_extra_columns_are_joined_before_precompute(self):
        """Reproduces the exact join deployment/paper_trading_engine.py's
        run_daily() performs (df.join(extra_columns[symbol])) -- a
        strategy needing a percentile column (like the real anchor
        strategies) must see it during precompute()."""
        class _NeedsExtraColumnStrategy(_FakeAnchorStrategy):
            def precompute(self, price_history):
                assert "some_percentile" in price_history.columns, "extra column was not joined in"
                return super().precompute(price_history)

        spec = PaperTradingStrategySpec(
            strategy_key="fake_anchor", display_name="Fake Anchor",
            strategy_factory=lambda: _NeedsExtraColumnStrategy(),
            compute_extra_columns_fn=lambda data: {
                symbol: pd.Series([50.0] * len(df), index=df.index, name="some_percentile")
                for symbol, df in data.items()
            },
        )
        with patch("portfolio_c.engine._SPECS_BY_KEY", {"fake_anchor": spec}):
            data = {"AAA.NS": _one_day_df("2024-01-01", 100, 101, 99, 100)}
            candidates = collect_anchor_candidates(data, datetime.date(2024, 1, 1),
                                                    anchor_strategy_keys=("fake_anchor",))
            self.assertIn("AAA.NS", candidates)


if __name__ == "__main__":
    unittest.main()
