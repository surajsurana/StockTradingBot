"""
Regression tests for swing_research/candidate_ranking.py -- the central
claims to prove are that (1) the caller's own iteration/insertion order
can NEVER affect the ranked result, (2) real confidence differences
always win over the tie-break, (3) the tie-break is reproducible per
date but genuinely varies across dates, and (4) the tie-break has NO
measurable correlation with alphabetical symbol position -- the exact
defect this module exists to eliminate (see module docstring). Run with:

    python test_candidate_ranking.py
"""

import datetime
import string
import unittest

from swing_research.candidate_ranking import rank_candidate_symbols


class TestOrderIndependence(unittest.TestCase):
    def test_result_is_identical_regardless_of_input_order(self):
        pairs_alphabetical = [("AAA.NS", 1.0), ("BBB.NS", 1.0), ("CCC.NS", 1.0), ("DDD.NS", 1.0)]
        pairs_reversed = list(reversed(pairs_alphabetical))
        pairs_shuffled = [pairs_alphabetical[2], pairs_alphabetical[0], pairs_alphabetical[3], pairs_alphabetical[1]]

        d = datetime.date(2024, 3, 15)
        result_a = rank_candidate_symbols(pairs_alphabetical, d)
        result_b = rank_candidate_symbols(pairs_reversed, d)
        result_c = rank_candidate_symbols(pairs_shuffled, d)

        self.assertEqual(result_a, result_b)
        self.assertEqual(result_a, result_c)

    def test_alphabetically_last_symbol_is_not_systematically_disadvantaged(self):
        # Build a large, alphabetically-ordered universe, all tied at the
        # same neutral confidence -- exactly the Turtle/Turn-of-Month
        # shape. Across many distinct dates, the LAST symbol alphabetically
        # should land in the front half of the ranking roughly as often as
        # any other symbol -- proving there is no residual alphabetical bias.
        symbols = [f"{c}{c}{c}.NS" for c in string.ascii_uppercase]  # AAA.NS .. ZZZ.NS, alphabetical
        pairs = [(s, 1.0) for s in symbols]
        last_symbol = symbols[-1]

        front_half_count = 0
        n_dates = 200
        for i in range(n_dates):
            d = datetime.date(2020, 1, 1) + datetime.timedelta(days=i)
            ranked = rank_candidate_symbols(pairs, d)
            if ranked.index(last_symbol) < len(ranked) // 2:
                front_half_count += 1

        # With genuine unbiased shuffling, expect ~50% -- allow a generous
        # band (30%-70%) so this isn't a flaky test, while still failing
        # loudly if the last symbol is ALWAYS or NEVER in the front half
        # (which is exactly what the old alphabetical-order bug produced).
        front_half_fraction = front_half_count / n_dates
        self.assertGreater(front_half_fraction, 0.30)
        self.assertLess(front_half_fraction, 0.70)

    def test_no_correlation_between_alphabetical_position_and_average_rank_at_scale(self):
        # The exact diagnostic that originally caught SW-013's bug (a
        # Pearson correlation between universe position and outcome) --
        # run here at a comparable scale (457 symbols) across many dates,
        # tied confidence throughout, asserting the correlation is
        # negligible. This is the strongest single proof this module
        # actually fixes the defect it exists to fix.
        symbols = [f"SYM{i:03d}.NS" for i in range(457)]  # already in "alphabetical" (numeric) order
        pairs = [(s, 1.0) for s in symbols]
        positions = list(range(457))

        n_dates = 60
        rank_sums = [0] * 457
        for i in range(n_dates):
            d = datetime.date(2019, 1, 1) + datetime.timedelta(days=i * 7)  # weekly, spread over ~1 year
            ranked = rank_candidate_symbols(pairs, d)
            rank_of = {sym: r for r, sym in enumerate(ranked)}
            for idx, sym in enumerate(symbols):
                rank_sums[idx] += rank_of[sym]
        avg_rank = [total / n_dates for total in rank_sums]

        # Pearson correlation between alphabetical position and average
        # rank, computed with no external dependency (stdlib only).
        n = len(positions)
        mean_x = sum(positions) / n
        mean_y = sum(avg_rank) / n
        cov = sum((positions[i] - mean_x) * (avg_rank[i] - mean_y) for i in range(n))
        var_x = sum((positions[i] - mean_x) ** 2 for i in range(n))
        var_y = sum((avg_rank[i] - mean_y) ** 2 for i in range(n))
        correlation = cov / ((var_x * var_y) ** 0.5)

        # The original bug measured -0.72. A genuine fix should be an
        # order of magnitude smaller than that, not just "less than 1".
        self.assertLess(abs(correlation), 0.15)


class TestConfidenceOrdering(unittest.TestCase):
    def test_higher_confidence_always_ranks_first(self):
        pairs = [("LOW.NS", 10.0), ("HIGH.NS", 90.0), ("MID.NS", 50.0)]
        ranked = rank_candidate_symbols(pairs, datetime.date(2024, 6, 1))
        self.assertEqual(ranked, ["HIGH.NS", "MID.NS", "LOW.NS"])

    def test_real_confidence_difference_beats_the_tie_break_every_time(self):
        # Even across many different dates (many different shuffles), a
        # genuine confidence edge must never lose to the tie-break.
        for i in range(50):
            d = datetime.date(2021, 1, 1) + datetime.timedelta(days=i)
            ranked = rank_candidate_symbols([("WORSE.NS", 1.0), ("BETTER.NS", 2.0)], d)
            self.assertEqual(ranked[0], "BETTER.NS")


class TestTieBreakReproducibility(unittest.TestCase):
    def test_same_date_produces_the_same_order_every_call(self):
        pairs = [("AAA.NS", 1.0), ("BBB.NS", 1.0), ("CCC.NS", 1.0), ("DDD.NS", 1.0), ("EEE.NS", 1.0)]
        d = datetime.date(2025, 7, 4)
        first = rank_candidate_symbols(pairs, d)
        second = rank_candidate_symbols(pairs, d)
        third = rank_candidate_symbols(list(reversed(pairs)), d)
        self.assertEqual(first, second)
        self.assertEqual(first, third)

    def test_different_dates_produce_different_orders(self):
        pairs = [("AAA.NS", 1.0), ("BBB.NS", 1.0), ("CCC.NS", 1.0), ("DDD.NS", 1.0), ("EEE.NS", 1.0)]
        orders = {tuple(rank_candidate_symbols(pairs, datetime.date(2022, 1, 1) + datetime.timedelta(days=i)))
                  for i in range(30)}
        # 30 distinct dates over 5 symbols (120 possible permutations) should
        # not all collapse onto the same single order.
        self.assertGreater(len(orders), 1)


class TestEdgeCases(unittest.TestCase):
    def test_empty_input_returns_empty_list(self):
        self.assertEqual(rank_candidate_symbols([], datetime.date(2024, 1, 1)), [])

    def test_single_candidate_returns_that_candidate(self):
        self.assertEqual(rank_candidate_symbols([("ONLY.NS", 1.0)], datetime.date(2024, 1, 1)), ["ONLY.NS"])

    def test_does_not_mutate_the_caller_list(self):
        pairs = [("AAA.NS", 1.0), ("BBB.NS", 2.0)]
        original = list(pairs)
        rank_candidate_symbols(pairs, datetime.date(2024, 1, 1))
        self.assertEqual(pairs, original)


if __name__ == "__main__":
    unittest.main()
