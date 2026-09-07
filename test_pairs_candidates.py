"""
Unit tests for research_lab/pairs_candidates.py. Run with:

    python test_pairs_candidates.py
"""

import unittest

from research_lab.pairs_candidates import PAIR_CANDIDATES, pair_symbols, validate_pair_candidates


class TestValidatePairCandidates(unittest.TestCase):
    def test_passes_when_both_legs_share_a_sector(self):
        pairs = [("A", "B"), ("C", "D")]
        sector_map = {"A": "Banks", "B": "Banks", "C": "IT", "D": "IT"}
        self.assertEqual(validate_pair_candidates(pairs, sector_map), pairs)

    def test_raises_naming_every_offending_pair(self):
        pairs = [("A", "B"), ("C", "D"), ("E", "F")]
        sector_map = {"A": "Banks", "B": "Banks", "C": "IT", "D": "Pharma", "E": "Cement"}
        with self.assertRaises(ValueError) as ctx:
            validate_pair_candidates(pairs, sector_map)
        message = str(ctx.exception)
        self.assertIn("C/D", message)
        self.assertIn("E/F", message)
        self.assertNotIn("A/B", message)

    def test_real_candidates_are_same_sector_in_the_real_csv(self):
        self.assertEqual(validate_pair_candidates(), PAIR_CANDIDATES)


class TestPairSymbols(unittest.TestCase):
    def test_sorted_and_deduplicated(self):
        self.assertEqual(pair_symbols([("B", "A"), ("A", "C")]), ["A", "B", "C"])

    def test_real_candidates_have_no_symbol_in_two_pairs(self):
        self.assertEqual(len(pair_symbols()), 2 * len(PAIR_CANDIDATES))


if __name__ == "__main__":
    unittest.main()
