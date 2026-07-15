"""
Mock-based unit tests for macro/macro_strategist.py -- the daily,
once-a-day (not per-stock) macro/geopolitical risk read that can throttle
or skip new trades in run_daily.py. Run with:

    python test_macro_strategist.py
"""

import unittest
from unittest.mock import patch

from macro.macro_strategist import (
    fetch_general_headlines, parse_macro_response, assess_macro_conditions, MacroAssessment,
)


def _articles():
    return [{"title": "Company reports strong quarterly profit", "publisher": "ET"}]


class TestFetchGeneralHeadlines(unittest.TestCase):
    @patch("macro.macro_strategist.fetch_zerodha_pulse_articles", return_value=[
        {"title": "Global oil prices spike after Middle East tensions", "publisher": "ZerodhaPulse"},
    ])
    @patch("macro.macro_strategist.fetch_economic_times_articles", return_value=[
        {"title": "Global oil prices spike after Middle East tensions", "publisher": "ET"},  # duplicate title
        {"title": "RBI holds rates steady", "publisher": "ET"},
    ])
    @patch("macro.macro_strategist.fetch_moneycontrol_articles", return_value=[
        {"title": "Sensex opens flat", "publisher": "Moneycontrol"},
    ])
    def test_combines_and_dedupes_across_sources(self, mock_mc, mock_et, mock_zp):
        headlines = fetch_general_headlines(max_items=20)

        titles = [h["title"] for h in headlines]
        self.assertEqual(len(titles), len(set(t.lower() for t in titles)), "should be deduplicated")
        self.assertIn("Sensex opens flat", titles)
        self.assertIn("RBI holds rates steady", titles)
        self.assertEqual(titles.count("Global oil prices spike after Middle East tensions"), 1)

    @patch("macro.macro_strategist.fetch_zerodha_pulse_articles", return_value=[])
    @patch("macro.macro_strategist.fetch_economic_times_articles", return_value=[])
    @patch("macro.macro_strategist.fetch_moneycontrol_articles", return_value=[])
    def test_no_headlines_returns_empty_list(self, mock_mc, mock_et, mock_zp):
        self.assertEqual(fetch_general_headlines(), [])

    @patch("macro.macro_strategist.fetch_zerodha_pulse_articles", return_value=[])
    @patch("macro.macro_strategist.fetch_economic_times_articles", return_value=[])
    @patch("macro.macro_strategist.fetch_moneycontrol_articles",
           return_value=[{"title": f"Story {i}", "publisher": "Moneycontrol"} for i in range(30)])
    def test_respects_max_items(self, mock_mc, mock_et, mock_zp):
        self.assertEqual(len(fetch_general_headlines(max_items=5)), 5)

    @patch("macro.macro_strategist.fetch_zerodha_pulse_articles", return_value=[
        {"title": "Iran oil supply disruption risk weighs on markets", "publisher": "ZerodhaPulse"},
    ])
    @patch("macro.macro_strategist.fetch_economic_times_articles", return_value=[
        {"title": "US Fed holds rates steady", "publisher": "ET"},
    ])
    @patch("macro.macro_strategist.fetch_moneycontrol_articles",
           return_value=[{"title": f"Q4 earnings story {i}", "publisher": "Moneycontrol"} for i in range(49)])
    def test_moneycontrol_volume_does_not_crowd_out_other_sources(self, mock_mc, mock_et, mock_zp):
        # Regression test: a real production day had Moneycontrol alone
        # return 49 articles, which -- under the old concatenate-then-
        # truncate logic -- filled the entire 20-item cap before Economic
        # Times or Zerodha Pulse were ever considered. A genuine
        # geopolitical story (Iran oil supply disruption) sitting in both
        # of those feeds was never read as a result. Interleaving must
        # guarantee every source gets representation regardless of how
        # many articles Moneycontrol alone returns.
        headlines = fetch_general_headlines(max_items=20)
        titles = [h["title"] for h in headlines]

        self.assertIn("Iran oil supply disruption risk weighs on markets", titles)
        self.assertIn("US Fed holds rates steady", titles)


class TestParseMacroResponse(unittest.TestCase):
    def test_normal_risk_parsed(self):
        response = "RISK_LEVEL: normal\nREASONING: Routine headlines, nothing unusual."
        assessment = parse_macro_response(response, _articles())
        self.assertEqual(assessment.risk_level, "normal")
        self.assertIn("Routine", assessment.reasoning)

    def test_elevated_risk_parsed(self):
        response = "RISK_LEVEL: elevated\nREASONING: Escalating regional conflict raises near-term uncertainty."
        assessment = parse_macro_response(response, _articles())
        self.assertEqual(assessment.risk_level, "elevated")

    def test_high_risk_parsed(self):
        response = "RISK_LEVEL: high\nREASONING: Major ceasefire has collapsed, markets likely to react sharply."
        assessment = parse_macro_response(response, _articles())
        self.assertEqual(assessment.risk_level, "high")

    def test_unparseable_response_defaults_to_normal_not_high(self):
        """A parsing failure must never silently block trading -- defaulting
        to 'high' on ambiguity would be worse than defaulting to 'normal'."""
        assessment = parse_macro_response("garbled nonsense response", _articles())
        self.assertEqual(assessment.risk_level, "normal")
        self.assertIn("Could not parse", assessment.reasoning)

    def test_case_insensitive_risk_level(self):
        response = "RISK_LEVEL: ELEVATED\nREASONING: test"
        assessment = parse_macro_response(response, _articles())
        self.assertEqual(assessment.risk_level, "elevated")


class TestAssessMacroConditions(unittest.TestCase):
    @patch("macro.macro_strategist.fetch_general_headlines", return_value=[])
    def test_no_headlines_skips_claude_call_and_defaults_normal(self, mock_fetch):
        call_count = {"n": 0}

        def counting_call(prompt):
            call_count["n"] += 1
            return "RISK_LEVEL: high\nREASONING: should not be reached"

        assessment = assess_macro_conditions(api_key="key", call_fn=counting_call)

        self.assertEqual(call_count["n"], 0)
        self.assertEqual(assessment.risk_level, "normal")

    @patch("macro.macro_strategist.fetch_general_headlines", return_value=_articles())
    def test_headlines_present_calls_claude_and_returns_parsed_result(self, mock_fetch):
        def fake_call(prompt):
            self.assertIn("Macro Strategist", prompt)
            return "RISK_LEVEL: elevated\nREASONING: Some caution warranted."

        assessment = assess_macro_conditions(api_key="key", call_fn=fake_call)

        self.assertEqual(assessment.risk_level, "elevated")
        self.assertEqual(assessment.headlines_considered, _articles())


if __name__ == "__main__":
    unittest.main()
