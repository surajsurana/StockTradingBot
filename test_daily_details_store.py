"""Tests for deployment/daily_details_store.py."""

import datetime
import shutil
import tempfile
import unittest
from unittest.mock import patch

import deployment.daily_details_store as dds


class TestDailyDetailsStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = f"{self.tmpdir}/daily_details.json"
        self.patcher = patch.object(dds, "DAILY_DETAILS_PATH", self.path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_load_details_empty_when_nothing_cached(self):
        self.assertEqual(dds.load_details(datetime.date(2024, 1, 1)), {})

    def test_save_then_load_round_trips_for_the_right_date(self):
        dds.save_detail("Minervini", "some report text", as_of_date=datetime.date(2024, 1, 1))
        self.assertEqual(dds.load_details(datetime.date(2024, 1, 1)), {"Minervini": "some report text"})

    def test_entries_on_different_dates_are_kept_separate(self):
        dds.save_detail("Minervini", "day1", as_of_date=datetime.date(2024, 1, 1))
        dds.save_detail("Minervini", "day2", as_of_date=datetime.date(2024, 1, 2))
        self.assertEqual(dds.load_details(datetime.date(2024, 1, 1)), {"Minervini": "day1"})
        self.assertEqual(dds.load_details(datetime.date(2024, 1, 2)), {"Minervini": "day2"})

    def test_multiple_keys_accumulate_on_the_same_date(self):
        dds.save_detail("Minervini", "text A", as_of_date=datetime.date(2024, 1, 1))
        dds.save_detail("Portfolio C", "text B", as_of_date=datetime.date(2024, 1, 1))
        self.assertEqual(dds.load_details(datetime.date(2024, 1, 1)),
                          {"Minervini": "text A", "Portfolio C": "text B"})

    def test_saving_the_same_key_twice_overwrites_not_duplicates(self):
        dds.save_detail("Minervini", "first", as_of_date=datetime.date(2024, 1, 1))
        dds.save_detail("Minervini", "second", as_of_date=datetime.date(2024, 1, 1))
        self.assertEqual(dds.load_details(datetime.date(2024, 1, 1)), {"Minervini": "second"})

    def test_default_date_is_today(self):
        dds.save_detail("Minervini", "text")
        self.assertEqual(dds.load_details(), {"Minervini": "text"})


if __name__ == "__main__":
    unittest.main()
