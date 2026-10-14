"""
Tests for deployment/daily_details_store.py -- the per-day text behind each summary.

WHAT THESE EXIST TO PREVENT. deployment/state/daily_details.json was left truncated mid-value by an
interrupted write on 2026-10-07. save_detail() reads the whole cache before it writes, and the read
raised, so every Portfolio B and Portfolio C run from that day onward ended in a JSONDecodeError --
after the book had already traded and saved itself. The trading was fine; the note about the trading
killed the process, nothing could ever rewrite the file to repair it, and a week later the resulting
tracebacks were being read elsewhere as the AI books having run out of credit.

Two faults, one week apart in their consequences: the write was not atomic, and the read was not
forgiving.

    python test_daily_details_store.py
"""

import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import deployment.daily_details_store as store


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "daily_details.json")
        self.patcher = patch.object(store, "DAILY_DETAILS_PATH", self.path)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)


class TestADamagedCacheNeverStopsARun(_Tmp):
    TRUNCATED = '{\n  "2026-09-10": {\n    "Pool A summary":'

    def test_a_truncated_file_reads_as_empty_instead_of_raising(self):
        """This is the exact shape the real file was left in: valid up to a point, then nothing."""
        self._write(self.TRUNCATED)
        self.assertEqual(store._load_all(), {})

    def test_a_run_can_still_save_its_note_afterwards(self):
        """THE WHOLE POINT. The old behaviour meant nothing could ever rewrite the file, so one
        interrupted write disabled two books indefinitely."""
        self._write(self.TRUNCATED)
        store.save_detail("Portfolio B", "it traded", as_of_date=date(2026, 10, 14))
        self.assertEqual(store.load_details(date(2026, 10, 14)), {"Portfolio B": "it traded"})

    def test_the_damaged_file_is_kept_not_deleted(self):
        """Somebody may want to look at what was in it."""
        self._write(self.TRUNCATED)
        store._load_all()
        kept = [f for f in os.listdir(self.dir) if ".corrupt-" in f]
        self.assertEqual(len(kept), 1)
        with open(os.path.join(self.dir, kept[0]), encoding="utf-8") as f:
            self.assertEqual(f.read(), self.TRUNCATED)

    def test_the_damaged_file_is_moved_out_of_the_way(self):
        """Left in place it would be re-quarantined on every single read."""
        self._write(self.TRUNCATED)
        store._load_all()
        self.assertFalse(os.path.exists(self.path))

    def test_an_empty_file_is_not_treated_as_damage(self):
        """Empty is a legitimate starting state, not a corruption."""
        self._write("")
        store._load_all()
        self.assertEqual([f for f in os.listdir(self.dir) if ".corrupt-" in f], [])

    def test_a_missing_file_is_not_treated_as_damage(self):
        self.assertEqual(store._load_all(), {})
        self.assertEqual(os.listdir(self.dir), [])

    def test_good_content_is_left_entirely_alone(self):
        self._write(json.dumps({"2026-10-14": {"Pool A summary": "fine"}}))
        self.assertEqual(store._load_all(), {"2026-10-14": {"Pool A summary": "fine"}})
        self.assertEqual([f for f in os.listdir(self.dir) if ".corrupt-" in f], [])


class TestTheWriteIsAtomic(_Tmp):
    """How the file got truncated: the old version emptied the real file and then serialised into
    it, so anything that stopped the process in between left a half-written file."""

    def test_an_interrupted_save_leaves_the_previous_file_intact(self):
        store.save_detail("Pool A summary", "the good one", as_of_date=date(2026, 10, 13))
        with patch("json.dump", side_effect=RuntimeError("killed mid-write")):
            with self.assertRaises(RuntimeError):
                store.save_detail("Pool A summary", "never written", as_of_date=date(2026, 10, 14))
        self.assertEqual(store.load_details(date(2026, 10, 13)), {"Pool A summary": "the good one"})

    def test_it_goes_through_the_shared_atomic_writer(self):
        """Rather than a second hand-rolled one that can drift from the first."""
        import inspect
        self.assertIn("write_json", inspect.getsource(store._save_all))

    def test_nothing_writes_the_real_path_directly_any_more(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(store._save_all))
        opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "open"]
        self.assertEqual(opens, [])


class TestOrdinaryUse(_Tmp):
    def test_a_later_save_for_the_same_key_and_day_replaces_the_first(self):
        store.save_detail("Pool A summary", "first", as_of_date=date(2026, 10, 14))
        store.save_detail("Pool A summary", "second", as_of_date=date(2026, 10, 14))
        self.assertEqual(store.load_details(date(2026, 10, 14)), {"Pool A summary": "second"})

    def test_days_are_kept_apart(self):
        store.save_detail("Pool A summary", "monday", as_of_date=date(2026, 10, 13))
        store.save_detail("Pool A summary", "tuesday", as_of_date=date(2026, 10, 14))
        self.assertEqual(store.load_details(date(2026, 10, 13))["Pool A summary"], "monday")
        self.assertEqual(store.load_details(date(2026, 10, 14))["Pool A summary"], "tuesday")


if __name__ == "__main__":
    unittest.main()
