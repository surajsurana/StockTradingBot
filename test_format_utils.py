"""
Tests for reporting/format_utils.py -- the shared display-only numeric
formatter fixing the 2026-08-17 bug where Telegram/report messages showed
raw many-decimal floats (e.g. "203.55999755859375") instead of a readable
"203.56". Display-formatting only: these tests confirm the STRING output,
never touch or assert on any underlying stored/calculated value.
"""

import unittest

from reporting.format_utils import format_metric


class TestFormatMetric(unittest.TestCase):
    def test_many_decimal_price_is_truncated_to_two_places(self):
        self.assertEqual(format_metric(203.55999755859375), "203.56")

    def test_rounds_not_just_truncates(self):
        self.assertEqual(format_metric(1.999), "2.00")

    def test_percentage_style_value(self):
        self.assertEqual(format_metric(14.2546), "14.25")

    def test_ratio_style_value(self):
        self.assertEqual(format_metric(1.03217), "1.03")

    def test_large_value_is_comma_grouped(self):
        self.assertEqual(format_metric(1000000.0), "1,000,000.00")

    def test_integer_input_still_gets_two_decimals(self):
        self.assertEqual(format_metric(5), "5.00")

    def test_none_passes_through_unchanged(self):
        self.assertIsNone(format_metric(None))

    def test_non_numeric_string_passes_through_unchanged(self):
        self.assertEqual(format_metric("n/a"), "n/a")

    def test_bool_passes_through_unchanged(self):
        # bool is technically an int subclass in Python -- must not be
        # coerced into "1.00"/"0.00".
        self.assertIs(format_metric(True), True)

    def test_negative_value(self):
        self.assertEqual(format_metric(-123.456), "-123.46")

    def test_custom_decimals(self):
        self.assertEqual(format_metric(1.23456, decimals=4), "1.2346")


if __name__ == "__main__":
    unittest.main()
