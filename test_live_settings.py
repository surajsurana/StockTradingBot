"""
deployment/live_settings.py -- the numeric live-trading limits, set from the dashboard.

The deployment cap used to be editable only by SSHing in and editing config/settings.py, while every
other live control was on the page. That was friction without a matching benefit: the cap is already
backed by three other limits (the real broker balance per venue, the per-order cap and the exposure
cap), so making it the one thing requiring server access bought nothing.

What it must NOT become is a way to turn live trading on from a browser -- see the class at the
bottom.
"""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from deployment.live_settings import KNOWN_SETTINGS, load, save, setting, status, store_path


class _Settings:
    A_TEST_SETTING = 7_500.0


# KNOWN_SETTINGS is empty since the deployment cap was removed on 2026-10-07 -- the store itself is
# still the mechanism for any future numeric limit, so it is exercised against a stub key rather than
# left untested until the next one is added.
_STUB = {"A_TEST_SETTING": ("Test setting", 0.0, 1_000_000.0, "exercises the store")}


def _with_stub(fn):
    def wrapped(*a, **kw):
        import deployment.live_settings as mod
        before = mod.KNOWN_SETTINGS
        mod.KNOWN_SETTINGS = _STUB
        try:
            return fn(*a, **kw)
        finally:
            mod.KNOWN_SETTINGS = before
    return wrapped


class TestSaveAndLoad(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    @_with_stub
    def test_a_saved_cap_reads_back(self):
        save(self.d, {"A_TEST_SETTING": 10_000})
        self.assertEqual(load(self.d)["A_TEST_SETTING"], 10_000.0)

    @_with_stub
    def test_unknown_names_are_ignored(self):
        save(self.d, {"A_TEST_SETTING": 1_000, "LIVE_TRADING": True, "EVIL": 1})
        self.assertEqual(set(load(self.d)), {"A_TEST_SETTING"})

    @_with_stub
    def test_a_value_outside_its_bounds_is_refused_not_stored(self):
        # a money ceiling with an extra zero typed in should be rejected, not accepted quietly
        for bad in (-1, 10_000_000_000, "lots", None):
            with self.assertRaises(ValueError, msg=str(bad)):
                save(self.d, {"A_TEST_SETTING": bad})
        self.assertEqual(load(self.d), {})

    @_with_stub
    def test_a_corrupt_store_is_empty_which_blocks_rather_than_permits(self):
        with open(store_path(self.d), "w", encoding="utf-8") as f:
            f.write("{ truncated")
        self.assertEqual(load(self.d), {})
        self.assertEqual(setting("A_TEST_SETTING", self.d, SimpleNamespace()), 0.0)

    @_with_stub
    def test_a_junk_value_in_the_store_is_absent_rather_than_guessed(self):
        with open(store_path(self.d), "w", encoding="utf-8") as f:
            json.dump({"A_TEST_SETTING": "ten thousand"}, f)
        self.assertEqual(load(self.d), {})


class TestResolution(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    @_with_stub
    def test_the_dashboard_value_wins_over_settings_py(self):
        save(self.d, {"A_TEST_SETTING": 10_000})
        self.assertEqual(setting("A_TEST_SETTING", self.d, _Settings), 10_000.0)

    @_with_stub
    def test_settings_py_is_the_fallback_so_nothing_has_to_be_migrated(self):
        self.assertEqual(setting("A_TEST_SETTING", self.d, _Settings), 7_500.0)

    @_with_stub
    def test_neither_set_means_zero_which_blocks_allocation(self):
        self.assertEqual(setting("A_TEST_SETTING", self.d, SimpleNamespace()), 0.0)

    @_with_stub
    def test_an_explicit_zero_on_the_dashboard_overrides_a_funded_settings_py(self):
        # setting the cap to zero is a deliberate "stop deploying", and must not be read as "unset"
        save(self.d, {"A_TEST_SETTING": 0})
        self.assertEqual(setting("A_TEST_SETTING", self.d, _Settings), 0.0)


class TestStatus(unittest.TestCase):
    @_with_stub
    def test_the_value_is_shown_unlike_a_credential(self):
        # a cap you cannot see is a cap you cannot trust
        d = tempfile.mkdtemp()
        save(d, {"A_TEST_SETTING": 10_000})
        row = status(d, _Settings)[0]
        self.assertEqual(row["value"], 10_000.0)
        self.assertEqual(row["source"], "dashboard")
        self.assertTrue(row["help"].strip())

    @_with_stub
    def test_it_reports_where_the_value_came_from(self):
        d = tempfile.mkdtemp()
        self.assertEqual(status(d, _Settings)[0]["source"], "settings.py")
        self.assertEqual(status(d, SimpleNamespace())[0]["source"], "")


class TestTheMasterSwitchIsNotSettableFromABrowser(unittest.TestCase):
    """Real orders need four independent human acts -- promote, fund, LIVE_TRADING on, no kill
    switch. The value of that is diluted if every one is a button behind the same access key. A
    capital ceiling is not worth the friction; the master switch and the kill switch are."""

    @_with_stub
    def test_live_trading_is_not_a_known_setting(self):
        self.assertNotIn("LIVE_TRADING", KNOWN_SETTINGS)

    @_with_stub
    def test_writing_it_through_this_store_does_nothing(self):
        d = tempfile.mkdtemp()
        self.assertEqual(save(d, {"LIVE_TRADING": True}), [])
        self.assertEqual(load(d), {})

    @_with_stub
    def test_no_kill_switch_or_cap_override_leaks_in_either(self):
        for forbidden in ("LIVE_TRADING", "LIVE_TRADING_HALTED", "LIVE_MAX_ORDER_VALUE_RUPEES",
                          "LIVE_MAX_EXPOSURE_RUPEES", "LIVE_MAX_ORDERS_PER_DAY"):
            self.assertNotIn(forbidden, KNOWN_SETTINGS, forbidden)

    @_with_stub
    def test_the_endpoint_does_not_import_or_touch_settings_py(self):
        import dashboard.server as srv
        with open(srv.__file__, encoding="utf-8") as f:
            source = f.read()
        block = source[source.index('if parsed.path == "/api/live-settings":'):]
        block = block[:block.index('if parsed.path == "/api/live/allocate":')]
        code = " ".join(line.split("#")[0] for line in block.splitlines())
        for forbidden in ("config/settings.py", "LIVE_TRADING", "open("):
            self.assertNotIn(forbidden, code, forbidden)


if __name__ == "__main__":
    unittest.main()
