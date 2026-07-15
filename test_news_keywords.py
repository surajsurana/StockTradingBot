"""
Mock-based unit tests for news/rss_sources.py's auto-derived symbol
keywords -- the fix for the "no keyword mapping for PATANJALI.NS" gap where
only 5 hardcoded symbols ever got Moneycontrol/Economic Times RSS coverage
and the other ~450 scanned stocks silently got zero articles from those
sources. Keywords are now derived from the Company Name column of
data/nifty500_constituents.csv for the entire universe. Run with:

    python test_news_keywords.py
"""

import unittest

import news.rss_sources as rss
from news.rss_sources import _derive_keywords, keywords_for_symbol


class TestDeriveKeywords(unittest.TestCase):
    def test_strips_ltd_suffix_and_adds_first_word(self):
        self.assertEqual(_derive_keywords("Patanjali Foods Ltd."),
                         ["Patanjali Foods", "Patanjali"])

    def test_strips_limited_suffix(self):
        self.assertEqual(_derive_keywords("Havells India Limited")[0], "Havells India")

    def test_generic_first_word_not_used_alone(self):
        # "Indian" must not become a standalone keyword -- it would match
        # nearly every headline in an Indian financial feed.
        variants = _derive_keywords("Indian Oil Corporation Ltd.")
        self.assertEqual(variants, ["Indian Oil Corporation"])

    def test_short_first_word_not_used_alone(self):
        # "ACC Ltd." -> first word IS the whole cleaned name, no duplicate
        variants = _derive_keywords("ACC Ltd.")
        self.assertEqual(variants, ["ACC"])

    def test_group_name_kept_as_variant(self):
        # Group-level news (e.g. a governance scandal) genuinely affects
        # each member stock -- "Adani" alone is a deliberate variant.
        variants = _derive_keywords("Adani Enterprises Ltd.")
        self.assertIn("Adani Enterprises", variants)
        self.assertIn("Adani", variants)


class TestKeywordsForSymbol(unittest.TestCase):
    def setUp(self):
        # isolate from the real CSV -- each test installs its own cache
        self._saved_cache = rss._auto_keywords_cache

    def tearDown(self):
        rss._auto_keywords_cache = self._saved_cache

    def test_manual_override_wins(self):
        rss._auto_keywords_cache = {"RELIANCE.NS": ["Wrong Name"]}
        self.assertEqual(keywords_for_symbol("RELIANCE.NS"),
                         ["Reliance", "Reliance Industries", "RIL"])

    def test_auto_derived_used_when_no_manual_entry(self):
        rss._auto_keywords_cache = {"PATANJALI.NS": ["Patanjali Foods", "Patanjali"]}
        self.assertEqual(keywords_for_symbol("PATANJALI.NS"),
                         ["Patanjali Foods", "Patanjali"])

    def test_unknown_symbol_returns_empty(self):
        rss._auto_keywords_cache = {}
        self.assertEqual(keywords_for_symbol("NOTREAL.NS"), [])


class TestRealCsvCoverage(unittest.TestCase):
    """Sanity checks against the actual shipped CSV -- catches the exact
    live gap (PATANJALI missing) ever reappearing."""

    def setUp(self):
        self._saved_cache = rss._auto_keywords_cache
        rss._auto_keywords_cache = None  # force a real CSV load

    def tearDown(self):
        rss._auto_keywords_cache = self._saved_cache

    def test_patanjali_has_keywords(self):
        variants = keywords_for_symbol("PATANJALI.NS")
        self.assertIn("Patanjali Foods", variants)
        self.assertIn("Patanjali", variants)

    def test_whole_universe_is_covered(self):
        from data.nifty500_universe import get_nifty500_symbols
        missing = [s for s in get_nifty500_symbols() if not keywords_for_symbol(s)]
        self.assertEqual(missing, [],
                         f"{len(missing)} universe symbol(s) have no news keywords")


if __name__ == "__main__":
    unittest.main()
