"""
Mock-based unit tests for news/news_agent.py's analyze_news_cached() --
confirms it skips the Claude call (and its cost) when headlines haven't
changed since the last check, and still calls Claude when they have. Run
with:

    python test_news_agent_cache.py
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from news.news_agent import analyze_news_cached


HEADLINES_V1 = [{"title": "Company reports strong quarterly profit", "publisher": "ET"}]
HEADLINES_V2 = [{"title": "Company faces regulatory probe", "publisher": "ET"}]


def _fake_bullish_call(prompt: str) -> str:
    return "SENTIMENT: bullish\nCONFIDENCE: 0.8\nREASONING: Strong earnings beat."


def _fake_bearish_call(prompt: str) -> str:
    return "SENTIMENT: bearish\nCONFIDENCE: 0.7\nREASONING: Regulatory risk."


class TestAnalyzeNewsCached(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)  # start from "no cache file" each test
        self.cache_path = self.tmp.name

    def tearDown(self):
        if os.path.exists(self.cache_path):
            os.unlink(self.cache_path)

    @patch("news.news_agent.fetch_recent_news", return_value=HEADLINES_V1)
    def test_first_check_calls_claude_and_caches(self, mock_fetch):
        call_count = {"n": 0}

        def counting_call(prompt):
            call_count["n"] += 1
            return _fake_bullish_call(prompt)

        result = analyze_news_cached("INFY.NS", api_key="key", cache_path=self.cache_path,
                                      call_fn=counting_call)

        self.assertEqual(call_count["n"], 1)
        self.assertEqual(result.sentiment, "bullish")
        self.assertTrue(os.path.exists(self.cache_path))

    @patch("news.news_agent.fetch_recent_news", return_value=HEADLINES_V1)
    def test_unchanged_headlines_skip_claude_on_second_check(self, mock_fetch):
        call_count = {"n": 0}

        def counting_call(prompt):
            call_count["n"] += 1
            return _fake_bullish_call(prompt)

        first = analyze_news_cached("INFY.NS", api_key="key", cache_path=self.cache_path,
                                     call_fn=counting_call)
        second = analyze_news_cached("INFY.NS", api_key="key", cache_path=self.cache_path,
                                      call_fn=counting_call)

        self.assertEqual(call_count["n"], 1, "Claude should only be called once for identical headlines")
        self.assertEqual(second.sentiment, first.sentiment)
        self.assertIn("cached", second.reasoning.lower())

    def test_changed_headlines_trigger_a_fresh_claude_call(self):
        call_count = {"n": 0}

        def counting_call(prompt):
            call_count["n"] += 1
            return _fake_bullish_call(prompt) if call_count["n"] == 1 else _fake_bearish_call(prompt)

        with patch("news.news_agent.fetch_recent_news", return_value=HEADLINES_V1):
            first = analyze_news_cached("INFY.NS", api_key="key", cache_path=self.cache_path,
                                         call_fn=counting_call)
        with patch("news.news_agent.fetch_recent_news", return_value=HEADLINES_V2):
            second = analyze_news_cached("INFY.NS", api_key="key", cache_path=self.cache_path,
                                          call_fn=counting_call)

        self.assertEqual(call_count["n"], 2, "New headlines should trigger a fresh Claude call")
        self.assertEqual(first.sentiment, "bullish")
        self.assertEqual(second.sentiment, "bearish")
        self.assertNotIn("cached", second.reasoning.lower())

    @patch("news.news_agent.fetch_recent_news", return_value=[])
    def test_no_articles_skips_claude_without_caching_a_call_count(self, mock_fetch):
        call_count = {"n": 0}

        def counting_call(prompt):
            call_count["n"] += 1
            return _fake_bullish_call(prompt)

        result = analyze_news_cached("INFY.NS", api_key="key", cache_path=self.cache_path,
                                      call_fn=counting_call)

        self.assertEqual(call_count["n"], 0)
        self.assertEqual(result.sentiment, "neutral")

    @patch("news.news_agent.fetch_recent_news", return_value=HEADLINES_V1)
    def test_different_symbols_cached_independently(self, mock_fetch):
        call_count = {"n": 0}

        def counting_call(prompt):
            call_count["n"] += 1
            return _fake_bullish_call(prompt)

        analyze_news_cached("INFY.NS", api_key="key", cache_path=self.cache_path, call_fn=counting_call)
        analyze_news_cached("TCS.NS", api_key="key", cache_path=self.cache_path, call_fn=counting_call)

        self.assertEqual(call_count["n"], 2, "A different symbol's cache entry shouldn't be reused for another symbol")


if __name__ == "__main__":
    unittest.main()
