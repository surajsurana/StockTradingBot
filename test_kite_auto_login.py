"""
Mock-based unit tests for auth/kite_auto_login.py -- covers the login/2FA/
redirect-chasing logic without ever hitting the real Kite servers, since a
bad automated-login attempt against the real account could trigger Kite's
own abuse/lockout protections. Run with:

    python test_kite_auto_login.py

Real-world validation (confirming Kite's actual login flow still matches
what's mocked here) is a separate, deliberate manual step -- see the plan's
verification notes. This file only proves the parsing/error-handling logic
is correct against the *expected* shape of Kite's responses.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from auth.kite_auto_login import auto_login, _session_is_valid, ensure_fresh_kite_session


def _resp(status_code=200, json_data=None, headers=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data or {}
    m.headers = headers or {}
    return m


class TestAutoLogin(unittest.TestCase):
    @patch("auth.kite_auto_login.exchange_request_token", return_value="fresh_access_token")
    @patch("auth.kite_auto_login.requests.Session")
    def test_success_path(self, mock_session_cls, mock_exchange):
        session = MagicMock()
        mock_session_cls.return_value = session

        session.post.side_effect = [
            _resp(200, {"data": {"request_id": "req123"}}),                 # /api/login
            _resp(200, {"status": "success"}),                              # /api/twofa
        ]
        session.get.return_value = _resp(
            302, headers={"Location": "http://127.0.0.1?request_token=abc123&action=login"}
        )

        token = auto_login("api_key", "api_secret", "AB1234", "pw", "BASE32SECRET")

        self.assertEqual(token, "fresh_access_token")
        mock_exchange.assert_called_once_with("api_key", "api_secret", "abc123")

    @patch("auth.kite_auto_login.exchange_request_token", return_value="fresh_access_token")
    @patch("auth.kite_auto_login.requests.Session")
    def test_twofa_request_uses_app_code_not_totp(self, mock_session_cls, mock_exchange):
        # Regression test for a real incident: this sent "totp" as
        # twofa_type, but Kite's actual API expects "app_code" for an
        # authenticator-app code (confirmed live by inspecting a real
        # /api/login response: twofa_type="app_code", twofa_types=
        # ["app_code", "sms"]) -- "totp" was simply the wrong literal
        # string and every automated login failed with "The requested 2FA
        # type is not available" until this was fixed.
        session = MagicMock()
        mock_session_cls.return_value = session
        session.post.side_effect = [
            _resp(200, {"data": {"request_id": "req123", "twofa_type": "app_code"}}),
            _resp(200, {"status": "success"}),
        ]
        session.get.return_value = _resp(
            302, headers={"Location": "http://127.0.0.1?request_token=abc123&action=login"}
        )

        auto_login("api_key", "api_secret", "AB1234", "pw", "BASE32SECRET")

        twofa_call_kwargs = session.post.call_args_list[1][1]
        self.assertEqual(twofa_call_kwargs["data"]["twofa_type"], "app_code")

    @patch("auth.kite_auto_login.requests.Session")
    def test_missing_credentials_raises(self, mock_session_cls):
        with self.assertRaises(RuntimeError):
            auto_login("api_key", "api_secret", "", "", "")
        mock_session_cls.assert_not_called()

    @patch("auth.kite_auto_login.requests.Session")
    def test_wrong_password_raises_clearly(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        session.post.return_value = _resp(403, {"error_type": "UserException", "message": "Invalid credentials"})

        with self.assertRaises(RuntimeError) as ctx:
            auto_login("api_key", "api_secret", "AB1234", "wrong_pw", "BASE32SECRET")
        self.assertIn("login step failed", str(ctx.exception))

    @patch("auth.kite_auto_login.requests.Session")
    def test_bad_totp_raises_clearly(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        session.post.side_effect = [
            _resp(200, {"data": {"request_id": "req123"}}),
            _resp(400, {"status": "error", "message": "Invalid TOTP"}),
        ]

        with self.assertRaises(RuntimeError) as ctx:
            auto_login("api_key", "api_secret", "AB1234", "pw", "BASE32SECRET")
        self.assertIn("2FA step failed", str(ctx.exception))

    @patch("auth.kite_auto_login.requests.Session")
    def test_redirect_chain_followed_until_request_token(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        session.post.side_effect = [
            _resp(200, {"data": {"request_id": "req123"}}),
            _resp(200, {"status": "success"}),
        ]
        # First hop stays on kite.zerodha.com, second hop has the token.
        session.get.side_effect = [
            _resp(302, headers={"Location": "https://kite.zerodha.com/connect/finish?sess=1"}),
            _resp(302, headers={"Location": "http://127.0.0.1?request_token=final_token"}),
        ]
        with patch("auth.kite_auto_login.exchange_request_token", return_value="tok") as mock_exchange:
            auto_login("api_key", "api_secret", "AB1234", "pw", "BASE32SECRET")
            mock_exchange.assert_called_once_with("api_key", "api_secret", "final_token")

    @patch("auth.kite_auto_login.requests.Session")
    def test_no_request_token_after_max_redirects_raises(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        session.post.side_effect = [
            _resp(200, {"data": {"request_id": "req123"}}),
            _resp(200, {"status": "success"}),
        ]
        # Always redirects to itself, never carries a request_token -- simulates
        # Kite changing its flow in a way this code doesn't recognize.
        session.get.return_value = _resp(302, headers={"Location": "https://kite.zerodha.com/connect/loop"})

        with self.assertRaises(RuntimeError):
            auto_login("api_key", "api_secret", "AB1234", "pw", "BASE32SECRET")


class TestSessionIsValid(unittest.TestCase):
    @patch("auth.kite_auto_login.requests.get")
    def test_valid_token(self, mock_get):
        mock_get.return_value = _resp(200, {"data": {"equity": {"net": 500.0}}})
        self.assertTrue(_session_is_valid("api_key", "good_token"))

    @patch("auth.kite_auto_login.requests.get")
    def test_stale_token(self, mock_get):
        mock_get.return_value = _resp(403, {"error_type": "TokenException"})
        self.assertFalse(_session_is_valid("api_key", "stale_token"))

    def test_empty_token_short_circuits_without_network_call(self):
        with patch("auth.kite_auto_login.requests.get") as mock_get:
            self.assertFalse(_session_is_valid("api_key", ""))
            mock_get.assert_not_called()


class TestEnsureFreshKiteSession(unittest.TestCase):
    def _settings(self, **overrides):
        base = dict(
            KITE_API_KEY="api_key", KITE_API_SECRET="api_secret", KITE_ACCESS_TOKEN="token",
            KITE_USER_ID="AB1234", KITE_PASSWORD="pw", KITE_TOTP_SECRET="BASE32SECRET",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    @patch("auth.kite_auto_login._session_is_valid", return_value=True)
    def test_already_valid_skips_login(self, mock_valid):
        settings = self._settings()
        with patch("auth.kite_auto_login.auto_login") as mock_login:
            self.assertTrue(ensure_fresh_kite_session(settings))
            mock_login.assert_not_called()

    @patch("auth.kite_auto_login._session_is_valid", return_value=False)
    @patch("auth.kite_auto_login.update_settings_file")
    @patch("auth.kite_auto_login.auto_login", return_value="new_token")
    def test_stale_triggers_login_and_updates_in_memory(self, mock_login, mock_update_file, mock_valid):
        settings = self._settings(KITE_ACCESS_TOKEN="stale_token")
        result = ensure_fresh_kite_session(settings)
        self.assertTrue(result)
        self.assertEqual(settings.KITE_ACCESS_TOKEN, "new_token")
        mock_update_file.assert_called_once_with("new_token")

    @patch("auth.kite_auto_login._session_is_valid", return_value=False)
    def test_stale_without_credentials_configured_returns_false(self, mock_valid):
        settings = self._settings(KITE_USER_ID="", KITE_PASSWORD="", KITE_TOTP_SECRET="")
        with patch("auth.kite_auto_login.auto_login") as mock_login:
            self.assertFalse(ensure_fresh_kite_session(settings))
            mock_login.assert_not_called()

    @patch("auth.kite_auto_login._session_is_valid", return_value=False)
    @patch("auth.kite_auto_login.auto_login", side_effect=RuntimeError("Kite changed their flow"))
    def test_login_failure_returns_false_not_raises(self, mock_login, mock_valid):
        settings = self._settings()
        self.assertFalse(ensure_fresh_kite_session(settings))


if __name__ == "__main__":
    unittest.main()
