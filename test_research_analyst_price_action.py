"""
Mock-based unit tests for research/research_analyst.py's price-action
integration -- the fix for a real gap where PATANJALI.NS slid double-digit %
off its highs while the Research Analyst's prompt never saw the move at
all, only "no technical signal today" from either strategy. Uses call_fn to
avoid any real Anthropic API call. Run with:

    python test_research_analyst_price_action.py
"""

import unittest

from research.research_analyst import build_synthesis_prompt, analyze_stock, _describe_price_action
from strategies.price_action import PriceAction
from fundamentals.fundamental_agent import FundamentalsResult
from news.news_agent import NewsAssessment


def _fundamentals(passed=True):
    return FundamentalsResult(symbol="TEST.NS", passed=passed, reasons=["EPS positive -- OK"])


def _news(sentiment="bullish", confidence=0.6):
    return NewsAssessment(symbol="TEST.NS", sentiment=sentiment, confidence=confidence,
                           reasoning="test reasoning")


class TestDescribePriceAction(unittest.TestCase):
    def test_none_returns_empty_string(self):
        self.assertEqual(_describe_price_action(None), "")

    def test_includes_pct_off_high_and_ma_status(self):
        pa = PriceAction(price=339.20, pct_off_high=-19.2, high_lookback_days=20,
                          above_20ma=False, above_50ma=False, above_200ma=False,
                          volume_ratio=1.8, is_down_day=True, pct_since_entry=-3.21)
        text = _describe_price_action(pa)
        self.assertIn("-19.2%", text)
        self.assertIn("BELOW", text)
        self.assertIn("1.8x", text)
        self.assertIn("DOWN day", text)
        self.assertIn("-3.2%", text)

    def test_insufficient_ma_history_reported_not_silently_dropped(self):
        pa = PriceAction(price=100.0, pct_off_high=-5.0, high_lookback_days=20,
                          above_20ma=True, above_50ma=None, above_200ma=None,
                          volume_ratio=None, is_down_day=False, pct_since_entry=None)
        text = _describe_price_action(pa)
        self.assertIn("insufficient history", text)

    def test_no_entry_pct_line_for_fresh_candidate(self):
        pa = PriceAction(price=100.0, pct_off_high=-5.0, high_lookback_days=20,
                          above_20ma=True, above_50ma=True, above_200ma=True,
                          volume_ratio=1.0, is_down_day=False, pct_since_entry=None)
        text = _describe_price_action(pa)
        self.assertNotIn("HELD position", text)


class TestBuildSynthesisPromptIncludesPriceAction(unittest.TestCase):
    def test_price_action_section_present_when_given(self):
        pa = PriceAction(price=339.20, pct_off_high=-19.2, high_lookback_days=20,
                          above_20ma=False, above_50ma=False, above_200ma=False,
                          volume_ratio=2.1, is_down_day=True, pct_since_entry=-3.21)
        prompt = build_synthesis_prompt("PATANJALI.NS", {}, _fundamentals(), _news(), price_action=pa)
        self.assertIn("PRICE ACTION", prompt)
        self.assertIn("-19.2%", prompt)

    def test_price_action_section_absent_when_none(self):
        prompt = build_synthesis_prompt("TEST.NS", {}, _fundamentals(), _news(), price_action=None)
        self.assertNotIn("PRICE ACTION", prompt)


class TestAnalyzeStockWiresPriceAction(unittest.TestCase):
    def test_price_action_facts_reach_the_prompt(self):
        pa = PriceAction(price=339.20, pct_off_high=-19.2, high_lookback_days=20,
                          above_20ma=False, above_50ma=False, above_200ma=False,
                          volume_ratio=2.1, is_down_day=True, pct_since_entry=-3.21)
        captured_prompt = {}

        def fake_call(prompt):
            captured_prompt["value"] = prompt
            return "VERDICT: unfavorable\nCONFIDENCE: 0.6\nREASONING: price broke down hard"

        result = analyze_stock("PATANJALI.NS", {}, _fundamentals(), _news(),
                                api_key="unused", call_fn=fake_call, price_action=pa)

        self.assertIn("PRICE ACTION", captured_prompt["value"])
        self.assertEqual(result.verdict, "unfavorable")
        self.assertEqual(result.inputs_summary["pct_off_high"], -19.2)
        self.assertEqual(result.inputs_summary["above_50ma"], False)
        self.assertEqual(result.inputs_summary["pct_since_entry"], -3.21)

    def test_no_price_action_key_in_inputs_summary_when_none(self):
        result = analyze_stock("TEST.NS", {}, _fundamentals(), _news(), api_key="unused",
                                call_fn=lambda p: "VERDICT: neutral\nCONFIDENCE: 0.5\nREASONING: x",
                                price_action=None)
        self.assertNotIn("pct_off_high", result.inputs_summary)


if __name__ == "__main__":
    unittest.main()
