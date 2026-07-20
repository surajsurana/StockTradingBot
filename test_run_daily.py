"""
Mock-based unit tests for run_daily.py's exclude_held_symbols(),
format_macro_summary(), format_scan_funnel(), and run_stage2_research()'s
per-candidate error handling. Run with:

    python test_run_daily.py
"""

import unittest
from dataclasses import dataclass
from collections import defaultdict
from unittest.mock import patch

from execution.positions import Holding
from news.news_agent import ClaudeAPIError, NewsAssessment
from research.research_analyst import ResearchAssessment
from run_daily import exclude_held_symbols, format_macro_summary, format_scan_funnel, run_stage2_research


class TestExcludeHeldSymbols(unittest.TestCase):
    def test_drops_symbols_already_held(self):
        symbols = ["RELIANCE.NS", "TCS.NS", "INFY.NS"]
        holdings = [Holding(symbol="TCS.NS", quantity=5, average_price=2000.0)]

        result = exclude_held_symbols(symbols, holdings)

        self.assertEqual(result, ["RELIANCE.NS", "INFY.NS"])

    def test_no_holdings_returns_all_symbols_unchanged(self):
        symbols = ["RELIANCE.NS", "TCS.NS"]
        self.assertEqual(exclude_held_symbols(symbols, []), symbols)

    def test_all_symbols_held_returns_empty(self):
        symbols = ["RELIANCE.NS", "TCS.NS"]
        holdings = [
            Holding(symbol="RELIANCE.NS", quantity=1, average_price=1000.0),
            Holding(symbol="TCS.NS", quantity=1, average_price=2000.0),
        ]
        self.assertEqual(exclude_held_symbols(symbols, holdings), [])

    def test_holding_not_in_universe_is_ignored(self):
        symbols = ["RELIANCE.NS", "TCS.NS"]
        holdings = [Holding(symbol="SOMEOTHER.NS", quantity=1, average_price=100.0)]
        self.assertEqual(exclude_held_symbols(symbols, holdings), symbols)


@dataclass
class _FakeMacroAssessment:
    risk_level: str
    reasoning: str


class TestFormatMacroSummary(unittest.TestCase):
    def test_none_returns_empty_string(self):
        # USE_MACRO_STRATEGIST=False -- nothing to show, and callers can
        # blindly concatenate the result without a None-check.
        self.assertEqual(format_macro_summary(None), "")

    def test_includes_risk_level_and_reasoning(self):
        assessment = _FakeMacroAssessment(risk_level="normal", reasoning="Routine headlines, nothing unusual.")

        text = format_macro_summary(assessment)

        self.assertIn("NORMAL", text)
        self.assertIn("Routine headlines, nothing unusual.", text)


class TestFormatScanFunnel(unittest.TestCase):
    def test_shows_gate_counts_and_finds_the_biggest_drop(self):
        funnel = {
            "ma_crossover": defaultdict(int, {
                "sufficient_history": 456, "crossed_up": 12, "volume_confirmed": 5,
                "momentum_confirmed": 3, "valid_stop": 3, "signal": 3,
            }),
        }

        text = format_scan_funnel(funnel, total_scanned=457)

        self.assertIn("MA Crossover (20>50): 12", text)
        self.assertIn("Volume Confirmed (>=1.5x avg): 5", text)
        # biggest drop: sufficient_history(456) -> crossed_up(12) = 444,
        # bigger than any other stage-to-stage drop in this funnel
        self.assertIn("Primary bottleneck: MA Crossover (20>50) (MA Crossover) -- cut 444", text)
        # display name only, never the raw underscored strategy_key -- Telegram's
        # legacy Markdown parse_mode rejects the whole message over an unmatched
        # underscore ("ma_crossover" has exactly one)
        self.assertNotIn("ma_crossover", text)
        self.assertNotIn("mean_reversion", text)

    def test_multiple_strategies_shown_separately(self):
        funnel = {
            "ma_crossover": defaultdict(int, {"sufficient_history": 456, "crossed_up": 0}),
            "mean_reversion": defaultdict(int, {"sufficient_history": 456, "oversold_transition": 8}),
        }

        text = format_scan_funnel(funnel, total_scanned=457)

        self.assertIn("MA Crossover funnel:", text)
        self.assertIn("Mean Reversion funnel:", text)

    def test_empty_funnel_produces_no_bottleneck_line(self):
        text = format_scan_funnel({}, total_scanned=457)
        self.assertNotIn("Primary bottleneck", text)


def _survivor(symbol):
    return {"symbol": symbol, "technical_signals": {}, "fundamentals_result": None, "price_history": None}


class TestRunStage2ResearchPerCandidateErrorHandling(unittest.TestCase):
    """
    Regression tests for a real incident: one candidate's Claude call
    raising an unexpected (non-ClaudeAPIError) exception -- e.g. "Claude's
    response contained no text block to parse", seen live -- crashed the
    entire run_daily.py process, researching ZERO candidates that run
    instead of just skipping the one that failed.
    """

    @patch("run_daily.compute_price_action", return_value=None)
    @patch("run_daily.analyze_news_cached")
    @patch("run_daily.analyze_stock")
    def test_one_candidates_unexpected_error_does_not_stop_the_others(self, mock_analyze, mock_news, mock_pa):
        mock_news.return_value = NewsAssessment(symbol="X", sentiment="neutral", confidence=0.5, reasoning="x")
        good_result = ResearchAssessment(symbol="GOOD.NS", verdict="favorable", confidence=0.7, reasoning="ok")
        mock_analyze.side_effect = [
            RuntimeError("Claude's response contained no text block to parse (got block types: ['thinking'])."),
            good_result,
        ]

        candidates = run_stage2_research([_survivor("BAD.NS"), _survivor("GOOD.NS")])

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].symbol, "GOOD.NS")

    @patch("run_daily.compute_price_action", return_value=None)
    @patch("run_daily.analyze_news_cached")
    @patch("run_daily.analyze_stock")
    def test_claude_api_error_propagates_and_aborts(self, mock_analyze, mock_news, mock_pa):
        # A ClaudeAPIError means Claude is unreachable entirely -- every
        # remaining candidate would fail identically, so this should abort
        # the whole batch (unlike a candidate-specific parsing failure).
        mock_news.return_value = NewsAssessment(symbol="X", sentiment="neutral", confidence=0.5, reasoning="x")
        mock_analyze.side_effect = ClaudeAPIError("rate limited")

        with self.assertRaises(ClaudeAPIError):
            run_stage2_research([_survivor("A.NS"), _survivor("B.NS")])


if __name__ == "__main__":
    unittest.main()
