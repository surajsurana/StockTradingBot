"""
Fully automated Kite login -- no manual link/paste step.

refresh_kite_token.py's manual flow (open a link, log in, paste one code
back) is what a human does every morning. This module does the same three
steps programmatically: log in with your Kite ID/password, answer the TOTP
challenge with a code generated from your stored TOTP secret (pyotp), then
follow Kite's own login redirect to capture the request_token -- exactly
what your browser does, just without a human in the loop.

This was deliberately not built earlier since it means storing your actual
Kite password and TOTP secret locally (config/settings.py, git-ignored) --
a bigger security tradeoff than the semi-manual flow, consciously accepted
now in exchange for the system running unattended.

Usage:
    from auth.kite_auto_login import ensure_fresh_kite_session
    ensure_fresh_kite_session(settings)   # settings = config.settings module

ensure_fresh_kite_session() only pays the login cost once per day: it first
checks whether today's KITE_ACCESS_TOKEN still works (a cheap authenticated
call), and only runs the full login if it doesn't. This means run_daily.py
and monitor_positions.py can both call it safely -- the second call in a day
is a no-op.

Kite's actual (undocumented, web-only) login flow, reverse-engineered from
the browser's network requests:
1. POST https://kite.zerodha.com/api/login with user_id/password -> a
   request_id and the CURRENT twofa_type to submit -- this is read from the
   response and used as-is, not hardcoded, since it changes depending on
   the account's 2FA configuration (confirmed live: "app_code" with only
   Kite's native app-code 2FA set up, "totp" once External 2FA TOTP is
   also enabled on Kite's own site -- hardcoding either broke the moment
   the account's setup changed).
2. POST https://kite.zerodha.com/api/twofa with that request_id and a fresh
   TOTP code -> sets session cookies on success.
3. GET the same Connect login URL a human would open
   (https://kite.zerodha.com/connect/login?api_key=...&v=3) using those
   cookies. Kite replies with a chain of redirects that ends at the app's
   registered redirect URL with ?request_token=... in the query string --
   we follow redirects manually (never actually requesting the final,
   unreachable 127.0.0.1 URL) just to read that Location header.
This is not a documented/supported API -- if Kite changes their login pages,
this will break loudly (see the RuntimeErrors below) rather than silently
failing, and refresh_kite_token.py's manual flow remains as a fallback.
"""

import os
import sys
from urllib.parse import urlparse, parse_qs

import pyotp
import requests

# Makes "from refresh_kite_token import ..." resolve regardless of whether
# this module is imported normally (e.g. by run_daily.py, run from the
# project root) or run directly (python auth/kite_auto_login.py, where
# Python would otherwise only put auth/ itself on the path) -- same fix
# reporting/telegram_notifier.py uses for the same reason.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from refresh_kite_token import exchange_request_token, update_settings_file

LOGIN_URL = "https://kite.zerodha.com/api/login"
TWOFA_URL = "https://kite.zerodha.com/api/twofa"
CONNECT_LOGIN_URL_TEMPLATE = "https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"
MAX_REDIRECTS_TO_FOLLOW = 5


def _extract_request_token(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    values = query.get("request_token")
    if not values:
        raise RuntimeError(
            f"Reached a redirect that isn't Kite's and has no request_token: {url}. "
            f"Kite's login flow may have changed -- fall back to refresh_kite_token.py."
        )
    return values[0]


def auto_login(api_key: str, api_secret: str, user_id: str, password: str, totp_secret: str) -> str:
    """
    Full programmatic login: returns a fresh access_token. Raises RuntimeError
    with a clear message on any unexpected response shape, rather than
    guessing -- an unattended run must never trade on a session that silently
    failed to establish.
    """
    if not user_id or not password or not totp_secret:
        raise RuntimeError(
            "KITE_USER_ID/KITE_PASSWORD/KITE_TOTP_SECRET aren't filled in in config/settings.py -- "
            "automated login can't run. Use refresh_kite_token.py's manual flow instead, or fill "
            "those in to enable auto-login."
        )

    session = requests.Session()

    login_resp = session.post(LOGIN_URL, data={"user_id": user_id, "password": password})
    login_data = login_resp.json()
    if login_resp.status_code != 200 or "data" not in login_data or "request_id" not in login_data["data"]:
        raise RuntimeError(
            f"Kite login step failed (status {login_resp.status_code}): {login_data}. "
            f"Common causes: wrong KITE_USER_ID/KITE_PASSWORD, or Kite added a new login step."
        )
    request_id = login_data["data"]["request_id"]
    # Read the required twofa_type from THIS response rather than hardcoding
    # a guess -- confirmed live that it changes depending on account state:
    # with only Kite's native app-code 2FA configured it was "app_code"
    # (twofa_types: ["app_code", "sms"]); after enabling External 2FA TOTP
    # on Kite's own site, it became "totp" (twofa_types: ["totp",
    # "app_code"]). Hardcoding either value breaks the moment the account's
    # 2FA configuration changes -- using what Kite itself reports is the
    # only version of this that isn't guaranteed to go stale again.
    twofa_type = login_data["data"].get("twofa_type")
    if not twofa_type:
        raise RuntimeError(
            f"Kite's login response didn't include a twofa_type to submit: {login_data}. "
            f"Kite's login flow may have changed -- fall back to refresh_kite_token.py."
        )

    totp_code = pyotp.TOTP(totp_secret).now()
    twofa_resp = session.post(TWOFA_URL, data={
        "user_id": user_id,
        "request_id": request_id,
        "twofa_value": totp_code,
        "twofa_type": twofa_type,
    })
    twofa_data = twofa_resp.json()
    if twofa_resp.status_code != 200 or not twofa_data.get("status") == "success":
        raise RuntimeError(
            f"Kite 2FA step failed (status {twofa_resp.status_code}): {twofa_data}. "
            f"Common causes: KITE_TOTP_SECRET is wrong (it must be the base32 setup secret, not a "
            f"6-digit code), or your machine's clock is off (TOTP codes are time-based)."
        )

    next_url = CONNECT_LOGIN_URL_TEMPLATE.format(api_key=api_key)
    for _ in range(MAX_REDIRECTS_TO_FOLLOW):
        resp = session.get(next_url, allow_redirects=False)
        if resp.status_code not in (301, 302, 303, 307, 308):
            raise RuntimeError(
                f"Expected a redirect from {next_url} but got status {resp.status_code} instead. "
                f"Kite's Connect login flow may have changed."
            )
        location = resp.headers.get("Location")
        if not location:
            raise RuntimeError(f"Redirect from {next_url} had no Location header.")
        if "request_token" in location:
            request_token = _extract_request_token(location)
            return exchange_request_token(api_key, api_secret, request_token)
        next_url = location

    raise RuntimeError(
        f"Followed {MAX_REDIRECTS_TO_FOLLOW} redirects without finding a request_token -- "
        f"Kite's login flow may have changed."
    )


def _session_is_valid(api_key: str, access_token: str) -> bool:
    """Cheap check: does today's access_token still work? Reuses the margins
    endpoint (already called every run for capital sizing) rather than adding
    a new API dependency just for this check."""
    if not access_token:
        return False
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }
    try:
        resp = requests.get("https://api.kite.trade/user/margins", headers=headers, timeout=15)
    except requests.RequestException:
        return False
    return resp.status_code == 200 and "data" in resp.json()


def ensure_fresh_kite_session(settings) -> bool:
    """
    Makes sure settings.KITE_ACCESS_TOKEN is valid for the current process,
    logging in automatically if it isn't. Mutates settings.KITE_ACCESS_TOKEN
    in place (Python modules are singletons, so every other module that did
    `from config import settings` sees the update immediately) and persists
    it to config/settings.py the same way refresh_kite_token.py does, so a
    second script run later the same day doesn't need to log in again.

    Returns True if the session is confirmed usable, False if auto-login
    isn't configured or failed (callers should treat False as "abort live
    trading for this run" -- never guess with a possibly-stale token).
    """
    if _session_is_valid(settings.KITE_API_KEY, settings.KITE_ACCESS_TOKEN):
        return True

    if not (settings.KITE_USER_ID and settings.KITE_PASSWORD and settings.KITE_TOTP_SECRET):
        print("KITE_ACCESS_TOKEN is stale/missing and auto-login isn't configured "
              "(KITE_USER_ID/KITE_PASSWORD/KITE_TOTP_SECRET) -- run refresh_kite_token.py manually.")
        return False

    print("KITE_ACCESS_TOKEN is stale -- logging in automatically...")
    try:
        new_access_token = auto_login(
            settings.KITE_API_KEY, settings.KITE_API_SECRET,
            settings.KITE_USER_ID, settings.KITE_PASSWORD, settings.KITE_TOTP_SECRET,
        )
    except Exception as e:
        print(f"Automated Kite login failed: {e}")
        return False

    settings.KITE_ACCESS_TOKEN = new_access_token
    update_settings_file(new_access_token)
    print("Automated Kite login succeeded -- KITE_ACCESS_TOKEN refreshed.")
    return True


if __name__ == "__main__":
    # Standalone validation: run this by itself first (python auth/kite_auto_login.py)
    # to confirm the programmatic login actually works against your real
    # account BEFORE relying on it inside run_daily.py/monitor_positions.py.
    # This is an unofficial, reverse-engineered flow (see the module
    # docstring) -- it needs real confirmation, not just the mocked tests in
    # test_kite_auto_login.py.
    from config import settings as live_settings

    print("Forcing a fresh login (ignoring today's existing KITE_ACCESS_TOKEN, if any)...")
    try:
        token = auto_login(
            live_settings.KITE_API_KEY, live_settings.KITE_API_SECRET,
            live_settings.KITE_USER_ID, live_settings.KITE_PASSWORD, live_settings.KITE_TOTP_SECRET,
        )
    except Exception as e:
        print(f"\nFAILED: {e}")
        sys.exit(1)

    update_settings_file(token)
    print(f"\nSUCCESS. New access_token received and saved to config/settings.py.")
