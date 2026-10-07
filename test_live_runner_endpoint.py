"""
dashboard/server.py's LiveRunner -- running the live book from the page instead of a terminal.

The properties that matter: two runs can never overlap, a dry run is genuinely dry, a failure never
leaves it stuck "running", and the live flag is carried from the request rather than defaulted
anywhere in the server.
"""
import threading
import time
import unittest
from unittest.mock import patch

from dashboard.server import LiveRunner


class TestOnlyOneRunAtATime(unittest.TestCase):
    def test_a_second_start_is_refused_while_one_is_going(self):
        # two concurrent runs would both read the same book, both decide, and both place orders
        # against a balance each thought it had to itself -- a doubled position with no single
        # check failing
        runner = LiveRunner()
        release = threading.Event()

        def slow(*a, **k):
            release.wait(5)
            return {"status": "dry_run", "orders": []}

        with patch.object(runner, "_run", side_effect=lambda live: slow()):
            first = runner.start(live=False)
            second = runner.start(live=False)
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("already going", second["error"])
        release.set()

    def test_the_mode_of_the_running_job_is_reported(self):
        runner = LiveRunner()
        with patch.object(runner, "_run", side_effect=lambda live: time.sleep(0.3)):
            runner.start(live=True)
            status = runner.status()
        self.assertTrue(status["running"])
        self.assertEqual(status["mode"], "live")


class TestItAlwaysFinishes(unittest.TestCase):
    def _wait(self, runner, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not runner.status()["running"]:
                return runner.status()
            time.sleep(0.02)
        self.fail("runner never finished")

    def test_a_crash_inside_the_run_clears_the_running_flag(self):
        # a run stuck "running" would block every later run with no way to clear it but a restart
        runner = LiveRunner()
        with patch("run_pool_g_live.run_live", side_effect=RuntimeError("boom")),              patch("data.fetch_crypto.fetch_all_crypto_daily", return_value={}),              patch("data.fetch_crypto.fetch_usdinr_rate", return_value=96.42):
            runner.start(live=False)
            status = self._wait(runner)
        self.assertFalse(status["running"])
        self.assertEqual(status["result"]["status"], "error")
        self.assertIn("boom", status["result"]["reason"])

    def test_a_crash_leaves_it_startable_again(self):
        runner = LiveRunner()
        with patch("run_pool_g_live.run_live", side_effect=RuntimeError("boom")),              patch("data.fetch_crypto.fetch_all_crypto_daily", return_value={}),              patch("data.fetch_crypto.fetch_usdinr_rate", return_value=96.42):
            runner.start(live=False)
            self._wait(runner)
        self.assertTrue(runner.start(live=False)["ok"])

    def test_the_result_is_kept_for_the_page_to_read(self):
        runner = LiveRunner()
        payload = {"status": "dry_run", "orders": [{"side": "BUY", "symbol": "BTC"}]}
        with patch("run_pool_g_live.run_live", return_value=payload), \
             patch("data.fetch_crypto.fetch_all_crypto_daily", return_value={}), \
             patch("data.fetch_crypto.fetch_usdinr_rate", return_value=96.42):
            runner.start(live=False)
            status = self._wait(runner)
        self.assertEqual(status["result"]["status"], "dry_run")


class TestTheLiveFlagIsCarriedNotDefaulted(unittest.TestCase):
    def test_a_dry_start_runs_run_live_with_dry_run_true(self):
        runner = LiveRunner()
        seen = {}

        def capture(*a, **kw):
            seen.update(kw)
            return {"status": "dry_run", "orders": []}

        with patch("run_pool_g_live.run_live", side_effect=capture), \
             patch("data.fetch_crypto.fetch_all_crypto_daily", return_value={}), \
             patch("data.fetch_crypto.fetch_usdinr_rate", return_value=96.42):
            runner.start(live=False)
            for _ in range(250):
                if not runner.status()["running"]:
                    break
                time.sleep(0.02)
        self.assertIs(seen.get("dry_run"), True)

    def test_a_live_start_runs_it_with_dry_run_false(self):
        runner = LiveRunner()
        seen = {}

        def capture(*a, **kw):
            seen.update(kw)
            return {"status": "processed", "orders": [], "placed": []}

        with patch("run_pool_g_live.run_live", side_effect=capture), \
             patch("data.fetch_crypto.fetch_all_crypto_daily", return_value={}), \
             patch("data.fetch_crypto.fetch_usdinr_rate", return_value=96.42):
            runner.start(live=True)
            for _ in range(250):
                if not runner.status()["running"]:
                    break
                time.sleep(0.02)
        self.assertIs(seen.get("dry_run"), False)

    def test_the_endpoint_only_goes_live_on_the_boolean_true(self):
        # a truthy string or 1 arriving from anywhere must not be read as "yes, place real orders"
        import dashboard.server as srv
        with open(srv.__file__, encoding="utf-8") as f:
            source = f.read()
        block = source[source.index('if parsed.path == "/api/live/run":   # Live view'):]
        block = block[:block.index('if parsed.path == "/api/live-settings":')]
        self.assertIn('body.get("live") is True', block)

    def test_the_server_never_passes_a_default_for_live(self):
        import dashboard.server as srv
        with open(srv.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("start(live=True)", source)      # only the request may ask for that


if __name__ == "__main__":
    unittest.main()
