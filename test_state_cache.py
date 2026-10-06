"""
dashboard/server.py's StateCache.

build_dashboard_state() re-reads every book's trades.jsonl in full and serialises ~2MB of JSON. The
page polls every 15-60s and each open tab polls independently, on a VPS with 458MB of RAM shared
with several other services. Collapsing duplicate rebuilds is the point.
"""
import json
import threading
import time
import unittest

from dashboard.server import StateCache


class TestItCollapsesDuplicateBuilds(unittest.TestCase):
    def setUp(self):
        self.builds = 0

    def build(self):
        self.builds += 1
        return b'{"n": %d}' % self.builds

    def test_a_second_request_within_the_window_does_not_rebuild(self):
        c = StateCache(ttl_seconds=60)
        first = c.get(("paper", ""), self.build)
        for _ in range(20):
            self.assertEqual(c.get(("paper", ""), self.build), first)
        self.assertEqual(self.builds, 1)          # twenty polls, one rebuild

    def test_it_rebuilds_once_the_window_passes(self):
        c = StateCache(ttl_seconds=0.05)
        c.get(("paper", ""), self.build)
        time.sleep(0.08)
        c.get(("paper", ""), self.build)
        self.assertEqual(self.builds, 2)

    def test_different_keys_do_not_share_a_payload(self):
        c = StateCache(ttl_seconds=60)
        paper = c.get(("paper", ""), self.build)
        live = c.get(("live", ""), self.build)
        self.assertNotEqual(paper, live)
        self.assertEqual(self.builds, 2)

    def test_the_advice_inputs_are_part_of_the_key(self):
        # the Advice tab's what-if numbers change the answer, so two different inputs must not
        # be served each other's result
        c = StateCache(ttl_seconds=60)
        a = c.get(("live", json.dumps({"monthly": 1000})), self.build)
        b = c.get(("live", json.dumps({"monthly": 5000})), self.build)
        self.assertNotEqual(a, b)


class TestInvalidation(unittest.TestCase):
    def test_an_action_is_never_answered_from_a_copy_taken_before_it(self):
        builds = []
        c = StateCache(ttl_seconds=60)

        def build():
            builds.append(1)
            return b'{"v": %d}' % len(builds)

        self.assertEqual(c.get(("paper", ""), build), b'{"v": 1}')
        c.invalidate()                            # e.g. a promotion just happened
        self.assertEqual(c.get(("paper", ""), build), b'{"v": 2}')

    def test_invalidate_clears_every_key_not_just_one(self):
        c = StateCache(ttl_seconds=60)
        c.get(("paper", ""), lambda: b"a")
        c.get(("live", ""), lambda: b"b")
        c.invalidate()
        self.assertEqual(c.get(("paper", ""), lambda: b"rebuilt"), b"rebuilt")
        self.assertEqual(c.get(("live", ""), lambda: b"rebuilt"), b"rebuilt")


class TestItStaysBounded(unittest.TestCase):
    def test_the_cache_cannot_grow_without_limit(self):
        # one entry per mode is the real shape; a crafted query must not be able to fill memory,
        # which on a 458MB box is the very failure this cache exists to prevent
        c = StateCache(ttl_seconds=60)
        for i in range(200):
            c.get(("live", "params-%d" % i), lambda i=i: b'{"i": %d}' % i)
        self.assertLessEqual(len(c._entries), 8)


class TestItDoesNotSerialiseRequests(unittest.TestCase):
    def test_a_slow_build_does_not_block_other_keys(self):
        # the build runs outside the lock: one slow rebuild must not stall every other request,
        # or a stuck yfinance call would hang the whole dashboard
        c = StateCache(ttl_seconds=60)
        started = threading.Event()
        release = threading.Event()

        def slow():
            started.set()
            release.wait(5)
            return b"slow"

        t = threading.Thread(target=lambda: c.get(("live", ""), slow), daemon=True)
        t.start()
        self.assertTrue(started.wait(5))
        fast = c.get(("paper", ""), lambda: b"fast")      # must not wait for the slow build
        self.assertEqual(fast, b"fast")
        release.set()
        t.join(5)


if __name__ == "__main__":
    unittest.main()
