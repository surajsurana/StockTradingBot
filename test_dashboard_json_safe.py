"""dashboard/server.json_safe -- /api/state must be STRICT JSON (2026-10-22: one NaN S&P 500 quote made
every response unreadable in the browser, so switching to Live kept showing Paper's numbers)."""

import json
import math
import unittest

from dashboard.server import json_safe


class TestJsonSafe(unittest.TestCase):
    def test_nan_and_infinity_become_null_at_any_depth(self):
        state = {"markets": [{"index_name": "S&P 500", "price": float("nan"), "change_pct": float("inf")}],
                 "nested": {"deep": (1.5, float("-inf"))}}
        out = json_safe(state)
        self.assertIsNone(out["markets"][0]["price"])
        self.assertIsNone(out["markets"][0]["change_pct"])
        self.assertEqual(out["nested"]["deep"], [1.5, None])

    def test_the_result_is_strict_json(self):
        text = json.dumps(json_safe({"a": float("nan"), "b": [float("inf")]}), allow_nan=False)
        self.assertNotIn("NaN", text)
        self.assertNotIn("Infinity", text)

    def test_ordinary_values_are_untouched(self):
        state = {"s": "NaN is just text here", "i": 3, "f": 2.25, "b": True, "n": None, "l": [0.0, -1.0]}
        self.assertEqual(json_safe(state), state)
        self.assertTrue(all(math.isfinite(v) for v in json_safe(state)["l"]))

    def test_build_state_serialises_through_it(self):
        import inspect
        from dashboard import server
        self.assertIn("json_safe(state), allow_nan=False", inspect.getsource(server.DashboardHandler._build_state))


if __name__ == "__main__":
    unittest.main()
