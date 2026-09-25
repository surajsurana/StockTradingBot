"""check_kite_subscription.py -- when the Kite renewal alerts fire (pure planning logic, no network)."""
import unittest
from datetime import date, datetime
from unittest import mock

import check_kite_subscription as cks

STATE = {"expires": "2026-10-26", "fee": 1000, "sent": {}}


def run(today, state=STATE, alive=True, balance=5000.0, hour=10):
    return cks.plan(today, datetime(today.year, today.month, today.day, hour), state, alive, balance)


class TestPlan(unittest.TestCase):
    def test_quiet_when_far_from_expiry(self):
        self.assertEqual(run(date(2026, 10, 1))[0], [])

    def test_seven_day_reminder_says_the_balance_is_enough(self):
        due, _ = run(date(2026, 10, 19))
        self.assertEqual(len(due), 1)
        self.assertIn("covers", due[0][1])
        self.assertIn("in 7 days", due[0][1])

    def test_no_reminder_between_reminder_days_when_balance_is_fine(self):
        self.assertEqual(run(date(2026, 10, 21))[0], [])          # 5 days left

    def test_low_balance_warns_with_the_amount_to_add_and_repeats_daily(self):
        due, _ = run(date(2026, 10, 21), balance=400.0)
        self.assertEqual(len(due), 1)
        self.assertIn("Add at least Rs 600", due[0][1])
        nxt, _ = run(date(2026, 10, 22), balance=400.0)
        self.assertNotEqual(due[0][0], nxt[0][0])                 # a new id each day, so it is sent again

    def test_unreadable_balance_still_warns(self):
        due, _ = run(date(2026, 10, 24), balance=None)
        self.assertIn("could not read", due[0][1])

    def test_an_alert_already_sent_is_not_sent_again(self):
        due, _ = run(date(2026, 10, 19))
        again, _ = run(date(2026, 10, 19), state={**STATE, "sent": {due[0][0]: "x"}})
        self.assertEqual(again, [])

    def test_dead_key_alerts_once_per_six_hours_and_ignores_the_calendar(self):
        due, _ = run(date(2026, 10, 1), alive=False)
        self.assertIn("NOT working", due[0][1])
        same_block, _ = run(date(2026, 10, 1), alive=False, hour=11)
        later_block, _ = run(date(2026, 10, 1), alive=False, hour=17)
        self.assertEqual(due[0][0], same_block[0][0])
        self.assertNotEqual(due[0][0], later_block[0][0])

    def test_unknown_key_status_never_raises_an_alarm(self):
        self.assertEqual(run(date(2026, 10, 1), alive=None)[0], [])

    def test_renewal_rolls_the_expiry_forward_and_clears_old_alerts(self):
        due, state = run(date(2026, 10, 27), state={**STATE, "sent": {"remind-x": "y"}})
        self.assertEqual(state["expires"], "2026-11-26")
        self.assertEqual(state["sent"], {})
        self.assertIn("26 Nov 2026", due[0][1])

    def test_month_end_expiry_does_not_break(self):
        self.assertEqual(cks.add_month(date(2026, 1, 31)), date(2026, 2, 28))
        self.assertEqual(cks.add_month(date(2026, 12, 15)), date(2027, 1, 15))


class TestKeyCheck(unittest.TestCase):
    def test_redirect_is_alive_and_invalid_key_is_dead(self):
        with mock.patch.object(cks.requests, "get", return_value=mock.Mock(status_code=302, text="")):
            self.assertTrue(cks.key_is_alive("k"))
        with mock.patch.object(cks.requests, "get", return_value=mock.Mock(status_code=400, text='{"message":"Invalid `api_key`."}')):
            self.assertFalse(cks.key_is_alive("k"))
        with mock.patch.object(cks.requests, "get", return_value=mock.Mock(status_code=500, text="oops")):
            self.assertIsNone(cks.key_is_alive("k"))
        with mock.patch.object(cks.requests, "get", side_effect=cks.requests.ConnectionError()):
            self.assertIsNone(cks.key_is_alive("k"))


if __name__ == "__main__":
    unittest.main()
