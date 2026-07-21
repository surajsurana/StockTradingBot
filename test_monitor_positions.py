"""
Mock-based unit tests for monitor_positions.py's price_pnl_text() -- the
short "current price (P&L Rs., P&L %)" fragment shown on every
position-check Telegram line -- _highest_high_since(), which arms the
trailing stop (risk/trailing_stop.py) -- and
_trailing_stop_would_be_rejected(), added after a real live failure:
Kite rejected 3 real trailing-stop GTT placements ("Trigger prices must
bracket current price") because the new stop (computed from the highest
price since entry, a past peak) had ended up above where price had since
pulled back to -- Kite requires a fresh GTT's stop-loss to sit below
current price when placed. Run with:

    python test_monitor_positions.py
"""

import unittest
import pandas as pd
from unittest.mock import patch, MagicMock

from execution.positions import Holding
from execution.position_state import KnownPosition
from research.research_analyst import ResearchAssessment
from monitor_positions import (price_pnl_text, _highest_high_since, _trailing_stop_would_be_rejected,
                                check_holding)


class TestPricePnlText(unittest.TestCase):
    def test_profit_shows_positive_pnl_and_pct(self):
        holding = Holding(symbol="NTPC.NS", quantity=15, average_price=344.10, last_price=345.60)

        text = price_pnl_text(holding)

        self.assertIn("Rs.345.60", text)
        self.assertIn("+0.44%", text)
        self.assertIn("Rs.+22.50", text)

    def test_loss_shows_negative_pnl_and_pct(self):
        holding = Holding(symbol="NTPC.NS", quantity=15, average_price=344.10, last_price=330.00)

        text = price_pnl_text(holding)

        self.assertIn("Rs.330.00", text)
        self.assertIn("-4.10%", text)
        self.assertIn("Rs.-211.50", text)

    def test_missing_last_price_returns_empty_string(self):
        holding = Holding(symbol="NTPC.NS", quantity=15, average_price=344.10, last_price=None)
        self.assertEqual(price_pnl_text(holding), "")

    def test_zero_average_price_returns_empty_string(self):
        holding = Holding(symbol="NTPC.NS", quantity=15, average_price=0.0, last_price=345.60)
        self.assertEqual(price_pnl_text(holding), "")


class TestHighestHighSince(unittest.TestCase):
    def _history(self, highs):
        idx = pd.date_range("2026-07-01", periods=len(highs), freq="D")
        return pd.DataFrame({"High": highs}, index=idx)

    def test_takes_max_high_from_entry_date_onward(self):
        df = self._history([100, 105, 98, 110, 102])  # 2026-07-01 .. 2026-07-05
        opened_at = "2026-07-03T09:15:00"
        self.assertEqual(_highest_high_since(df, opened_at), 110)

    def test_ignores_highs_before_entry_date(self):
        df = self._history([500, 105, 98, 110, 102])  # 500 is BEFORE entry, must be excluded
        opened_at = "2026-07-02T09:15:00"
        self.assertEqual(_highest_high_since(df, opened_at), 110)

    def test_no_rows_on_or_after_entry_returns_none(self):
        df = self._history([100, 105])  # 2026-07-01, 2026-07-02
        opened_at = "2026-08-01T09:15:00"
        self.assertIsNone(_highest_high_since(df, opened_at))


class TestTrailingStopWouldBeRejected(unittest.TestCase):
    def test_real_live_case_geship(self):
        # actual values from the live GESHIP.NS failure
        self.assertTrue(_trailing_stop_would_be_rejected(1351.50, 1338.10))

    def test_new_stop_below_current_price_is_placeable(self):
        self.assertFalse(_trailing_stop_would_be_rejected(1351.50, 1400.00))

    def test_new_stop_exactly_equal_to_current_price_is_rejected(self):
        # Kite requires the stop to be strictly below current price
        self.assertTrue(_trailing_stop_would_be_rejected(100.0, 100.0))

    def test_missing_current_price_assumes_placeable(self):
        # last_price can be None -- the real GTT call is still the
        # authoritative check either way, this is a best-effort pre-check
        self.assertFalse(_trailing_stop_would_be_rejected(1351.50, None))


class TestCheckHoldingExitVerification(unittest.TestCase):
    """
    Regression tests for a real incident (PATANJALI.NS): monitor_positions.py
    treated a merely-PLACED SELL order (Kite's order-placement response
    "status": "success", meaning only "accepted") as proof the position had
    actually closed, cancelled its GTT immediately, and the LIMIT order
    (separately mispriced off cost basis instead of current market -- see
    the entry_price test below) then sat open/unfilled for hours with the
    position completely unprotected. check_holding() must only cancel the
    GTT once the order is confirmed COMPLETE via is_order_complete().
    """

    def _holding(self, average_price=350.45, last_price=338.05, quantity=17):
        return Holding(symbol="PATANJALI.NS", quantity=quantity,
                        average_price=average_price, last_price=last_price)

    def _unfavorable_assessment(self):
        return ResearchAssessment(symbol="PATANJALI.NS", verdict="unfavorable",
                                   confidence=0.62, reasoning="test reasoning")

    def _known(self, gtt_id=12345):
        return {"PATANJALI.NS": KnownPosition(symbol="PATANJALI.NS", quantity=17, entry_price=350.45,
                                               gtt_id=gtt_id, opened_at="2026-07-01T00:00:00")}

    @patch("monitor_positions.evaluate_holding")
    def test_order_placed_but_not_filled_leaves_gtt_in_place(self, mock_evaluate):
        mock_evaluate.return_value = (self._unfavorable_assessment(), None)
        engine = MagicMock()
        engine.place_order.return_value = {"status": "success", "data": {"order_id": "999"}}
        engine.is_order_complete.return_value = False  # the real-incident case

        line, exited = check_holding(self._holding(), regime_series=None, active_strategies=[],
                                      known_positions=self._known(), execution_engine=engine)

        engine.cancel_gtt.assert_not_called()
        self.assertFalse(exited)
        self.assertIn("not yet filled", line)

    @patch("monitor_positions.evaluate_holding")
    def test_order_confirmed_filled_cancels_gtt_and_reports_exited(self, mock_evaluate):
        mock_evaluate.return_value = (self._unfavorable_assessment(), None)
        engine = MagicMock()
        engine.place_order.return_value = {"status": "success", "data": {"order_id": "999"}}
        engine.is_order_complete.return_value = True

        line, exited = check_holding(self._holding(), regime_series=None, active_strategies=[],
                                      known_positions=self._known(), execution_engine=engine)

        engine.cancel_gtt.assert_called_once_with(12345)
        self.assertTrue(exited)
        self.assertIn("EXITED EARLY", line)

    @patch("monitor_positions.evaluate_holding")
    def test_placement_failure_outright_leaves_gtt_in_place(self, mock_evaluate):
        mock_evaluate.return_value = (self._unfavorable_assessment(), None)
        engine = MagicMock()
        engine.place_order.return_value = {"status": "error", "message": "insufficient margin"}

        line, exited = check_holding(self._holding(), regime_series=None, active_strategies=[],
                                      known_positions=self._known(), execution_engine=engine)

        engine.cancel_gtt.assert_not_called()
        engine.is_order_complete.assert_not_called()  # nothing to check -- it was never placed
        self.assertFalse(exited)
        self.assertIn("exit order failed", line)

    @patch("monitor_positions.evaluate_holding")
    def test_exit_priced_off_current_market_not_cost_basis(self, mock_evaluate):
        # Real incident: PATANJALI's average_price (350.45) was well above
        # its current market price (338.05) after a decline -- pricing the
        # SELL limit off average_price put it ABOVE current market, so it
        # never filled. entry_price must be holding.last_price.
        mock_evaluate.return_value = (self._unfavorable_assessment(), None)
        engine = MagicMock()
        engine.place_order.return_value = {"status": "success", "data": {"order_id": "999"}}
        engine.is_order_complete.return_value = True

        check_holding(self._holding(average_price=350.45, last_price=338.05), regime_series=None,
                      active_strategies=[], known_positions=self._known(), execution_engine=engine)

        placed_trade = engine.place_order.call_args[0][0]
        self.assertEqual(placed_trade.signal.entry_price, 338.05)

    @patch("monitor_positions.evaluate_holding")
    def test_exit_falls_back_to_average_price_when_last_price_missing(self, mock_evaluate):
        mock_evaluate.return_value = (self._unfavorable_assessment(), None)
        engine = MagicMock()
        engine.place_order.return_value = {"status": "success", "data": {"order_id": "999"}}
        engine.is_order_complete.return_value = True

        check_holding(self._holding(average_price=350.45, last_price=None), regime_series=None,
                      active_strategies=[], known_positions=self._known(), execution_engine=engine)

        placed_trade = engine.place_order.call_args[0][0]
        self.assertEqual(placed_trade.signal.entry_price, 350.45)


if __name__ == "__main__":
    unittest.main()
