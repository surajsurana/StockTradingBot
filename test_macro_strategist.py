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


def _patch_all_sources(**overrides):
    """
    Decorator stacking @patch for all seven fetch_*_articles() functions
    fetch_general_headlines() reads from, defaulting each to [] unless
    overridden by name (e.g. _patch_all_sources(zerodha_pulse=[...])).
    Keeps the seven-source test list from needing seven @patch lines
    repeated on every single test.
    """
    names = {
        "moneycontrol": "fetch_moneycontrol_articles",
        "economic_times": "fetch_economic_times_articles",
        "zerodha_pulse": "fetch_zerodha_pulse_articles",
        "bbc": "fetch_bbc_articles",
        "aljazeera": "fetch_aljazeera_articles",
        "cnn": "fetch_cnn_articles",
        "times_of_india": "fetch_times_of_india_articles",
    }

    def decorator(func):
        for key, func_name in names.items():
            func = patch(f"macro.macro_strategist.{func_name}", return_value=overrides.get(key, []))(func)
        return func

    return decorator


class TestFetchGeneralHeadlines(unittest.TestCase):
    @_patch_all_sources(
        zerodha_pulse=[{"title": "Global oil prices spike after Middle East tensions", "publisher": "ZerodhaPulse"}],
        economic_times=[
            {"title": "Global oil prices spike after Middle East tensions", "publisher": "ET"},  # duplicate title
            {"title": "RBI holds rates steady", "publisher": "ET"},
        ],
        moneycontrol=[{"title": "Sensex opens flat", "publisher": "Moneycontrol"}],
    )
    def test_combines_and_dedupes_across_sources(self, *mocks):
        headlines = fetch_general_headlines(max_items=28)

        titles = [h["title"] for h in headlines]
        self.assertEqual(len(titles), len(set(t.lower() for t in titles)), "should be deduplicated")
        self.assertIn("Sensex opens flat", titles)
        self.assertIn("RBI holds rates steady", titles)
        self.assertEqual(titles.count("Global oil prices spike after Middle East tensions"), 1)

    @_patch_all_sources()
    def test_no_headlines_returns_empty_list(self, *mocks):
        self.assertEqual(fetch_general_headlines(), [])

    @_patch_all_sources(
        moneycontrol=[{"title": f"Story {i}", "publisher": "Moneycontrol"} for i in range(30)],
    )
    def test_respects_max_items(self, *mocks):
        self.assertEqual(len(fetch_general_headlines(max_items=5)), 5)

    @_patch_all_sources(
        zerodha_pulse=[{"title": "Iran oil supply disruption risk weighs on markets", "publisher": "ZerodhaPulse"}],
        economic_times=[{"title": "US Fed holds rates steady", "publisher": "ET"}],
        moneycontrol=[{"title": f"Q4 earnings story {i}", "publisher": "Moneycontrol"} for i in range(49)],
    )
    def test_moneycontrol_volume_does_not_crowd_out_other_sources(self, *mocks):
        # Regression test: a real production day had Moneycontrol alone
        # return 49 articles, which -- under the old concatenate-then-
        # truncate logic -- filled the entire max_items cap before any
        # other source was ever considered. A genuine geopolitical story
        # (Iran oil supply disruption) sitting in both ET and Zerodha
        # Pulse was never read as a result. Interleaving must guarantee
        # every source gets representation regardless of how many
        # articles Moneycontrol alone returns.
        headlines = fetch_general_headlines(max_items=28)
        titles = [h["title"] for h in headlines]

        self.assertIn("Iran oil supply disruption risk weighs on markets", titles)
        self.assertIn("US Fed holds rates steady", titles)

    @_patch_all_sources(
        bbc=[{"title": "Trump threatens to bomb bridges unless Iran resumes talks", "publisher": "BBC"}],
        aljazeera=[{"title": "Iran war live: US carries out strikes on Gulf bases", "publisher": "AlJazeera"}],
    )
    def test_global_sources_included(self, *mocks):
        # The whole point of adding BBC/Al Jazeera/CNN/Times of India:
        # geopolitical stories that would never appear in Indian financial
        # RSS feeds must actually reach the headline list.
        headlines = fetch_general_headlines(max_items=28)
        titles = [h["title"] for h in headlines]

        self.assertIn("Trump threatens to bomb bridges unless Iran resumes talks", titles)
        self.assertIn("Iran war live: US carries out strikes on Gulf bases", titles)


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
