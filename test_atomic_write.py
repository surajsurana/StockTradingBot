"""
deployment/atomic_write.py -- replacing a file without ever leaving a half-written one.

This is the fix for a real incident on 2026-10-06: portfolio.json was written with open(path,"w"),
which truncates first and fills afterwards. The dashboard, reading several times a minute, caught
that window and raised JSONDecodeError through every caller, blanking the page. The same window
would have left a book's positions and cash permanently gone had the process died inside it.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from deployment.atomic_write import write_json


class TestItReplacesCleanly(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "portfolio.json")

    def test_it_writes_and_reads_back(self):
        write_json(self.p, {"cash": 1234.5, "positions": {"X": {"quantity": 2}}})
        with open(self.p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["cash"], 1234.5)

    def test_it_creates_missing_directories(self):
        deep = os.path.join(self.d, "pool_a", "alpha", "portfolio.json")
        write_json(deep, {"cash": 1.0})
        self.assertTrue(os.path.exists(deep))

    def test_it_leaves_no_temp_file_behind(self):
        write_json(self.p, {"cash": 1.0})
        self.assertEqual(os.listdir(self.d), ["portfolio.json"])

    def test_a_reader_never_sees_a_partial_file(self):
        # the whole point: at the moment os.replace runs, the target is either entirely the old
        # content or entirely the new one. Verified by reading the target from inside the write.
        write_json(self.p, {"cash": 100.0})
        seen = {}
        real_replace = os.replace

        def spy(src, dst):
            with open(dst, encoding="utf-8") as f:
                seen["before_rename"] = json.load(f)      # still fully the OLD file, not a prefix
            return real_replace(src, dst)

        with patch("deployment.atomic_write.os.replace", side_effect=spy):
            write_json(self.p, {"cash": 200.0})
        self.assertEqual(seen["before_rename"]["cash"], 100.0)
        with open(self.p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["cash"], 200.0)


class TestItFailsSafely(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "portfolio.json")
        write_json(self.p, {"cash": 100.0, "positions": {"KEEP": {}}})

    def test_an_unserialisable_payload_leaves_the_existing_file_intact(self):
        with self.assertRaises(TypeError):
            write_json(self.p, {"cash": object()})
        with open(self.p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["cash"], 100.0)     # the book survived the bad write

    def test_a_failed_write_leaves_no_temp_file(self):
        with self.assertRaises(TypeError):
            write_json(self.p, {"cash": object()})
        self.assertEqual(os.listdir(self.d), ["portfolio.json"])


class TestTheCallersUseIt(unittest.TestCase):
    def test_the_paper_book_and_the_registry_are_written_atomically(self):
        # structural, because the failure mode only appears under a race: these two files are
        # rewritten constantly and losing either one is unrecoverable
        import deployment.deployment_manager as dm
        import deployment.paper_trading_engine as pte
        for module, fn in ((pte, "_save_portfolio"), (dm, "_save_registry")):
            with open(module.__file__, encoding="utf-8") as f:
                source = f.read()
            body = source[source.index("def %s" % fn):]
            body = body[:body.index("\ndef ", 1)]
            self.assertIn("write_json", body, fn)
            self.assertNotIn('open(path, "w"', body, fn)

    def test_a_corrupt_book_degrades_to_no_data_instead_of_raising(self):
        from reporting.pool_summary import _read_json
        d = tempfile.mkdtemp()
        p = os.path.join(d, "portfolio.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"cash": 100, "positions": {')      # exactly the truncation seen in the log
        self.assertIsNone(_read_json(p))
        self.assertIsNone(_read_json(os.path.join(d, "absent.json")))


if __name__ == "__main__":
    unittest.main()
