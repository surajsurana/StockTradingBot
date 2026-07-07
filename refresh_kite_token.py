"""
Refreshes your daily Kite access token -- semi-automated version.

Kite's access_token expires every day (a Zerodha/SEBI security rule that
applies to every Kite Connect app, not something this project can bypass --
see run_daily.py's docstring for why). This script cuts the daily refresh
down to about 30 seconds: open one link, log in as you normally would, paste
one code back here. It handles the token exchange itself and writes the new
access_token into config/settings.py automatically -- you never need to open
Notepad for this.

Usage, each morning before running run_daily.py with LIVE_TRADING = True:

    python refresh_kite_token.py

Steps it walks you through:
1. Prints a login URL. Open it in your browser and log in with your usual
   Kite ID / password / TOTP -- exactly like logging into the Kite app.
2. You'll land on a "site can't be reached" page for 127.0.0.1 -- that's
   expected, ignore it. Copy the `request_token` value from the browser's
   address bar.
3. Paste it here when prompted.
4. It exchanges the token with Zerodha and updates config/settings.py's
   KITE_ACCESS_TOKEN for you.

A fully automated version (no manual login step at all -- the script logs
in for you using your Kite password + TOTP secret) is possible later. It's
deliberately not built yet, since storing your actual broker password and
2FA secret locally is a bigger security tradeoff than this semi-manual
approach, and that should be a deliberate choice, not a quiet default.
Revisit when you're ready to remove this last manual step.
"""

import hashlib
import os
import re

import requests

from config import settings

LOGIN_URL_TEMPLATE = "https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "config", "settings.py")


def exchange_request_token(api_key: str, api_secret: str, request_token: str) -> str:
    """Exchanges a single-use request_token for a same-day access_token."""
    checksum = hashlib.sha256((api_key + request_token + api_secret).encode()).hexdigest()
    resp = requests.post(
        "https://api.kite.trade/session/token",
        data={"api_key": api_key, "request_token": request_token, "checksum": checksum},
        headers={"X-Kite-Version": "3"},
    )
    result = resp.json()
    if resp.status_code != 200 or "data" not in result:
        raise RuntimeError(
            f"Token exchange failed (status {resp.status_code}): {result}. "
            f"Common causes: request_token expired/already used (they're single-use and "
            f"expire within minutes), or KITE_API_SECRET is wrong."
        )
    return result["data"]["access_token"]


def update_settings_file(new_access_token: str, settings_path: str = SETTINGS_PATH):
    """
    Rewrites config/settings.py's KITE_ACCESS_TOKEN line in place, leaving
    everything else in the file untouched. Raises clearly (rather than
    silently doing nothing) if the line can't be found, so a settings.py
    reformat doesn't cause a silent failure to update the token.
    """
    with open(settings_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_content, count = re.subn(
        r'KITE_ACCESS_TOKEN\s*=\s*".*?"',
        f'KITE_ACCESS_TOKEN = "{new_access_token}"',
        content,
        count=1,
    )
    if count == 0:
        raise RuntimeError(
            f"Could not find a KITE_ACCESS_TOKEN line to update in {settings_path}. "
            f"Update it by hand instead: KITE_ACCESS_TOKEN = \"{new_access_token}\""
        )

    with open(settings_path, "w", encoding="utf-8") as f:
        f.write(new_content)


def main():
    print("Step 1: open this URL in your browser and log in with your Kite ID/password/TOTP:\n")
    print(LOGIN_URL_TEMPLATE.format(api_key=settings.KITE_API_KEY))
    print("\nStep 2: you'll land on a 'site can't be reached' page for 127.0.0.1 -- that's expected.")
    print("Copy the `request_token` value from the browser's address bar.\n")

    request_token = input("Step 3: paste the request_token here and press Enter: ").strip()
    if not request_token:
        print("No token entered, aborting.")
        return

    if not settings.KITE_API_SECRET:
        print("KITE_API_SECRET is empty in config/settings.py -- fill that in first, then re-run this.")
        return

    print("\nExchanging for an access_token...")
    access_token = exchange_request_token(settings.KITE_API_KEY, settings.KITE_API_SECRET, request_token)
    print(f"SUCCESS. New access_token received.")

    update_settings_file(access_token)
    print("\nconfig/settings.py has been updated automatically -- you're ready to run run_daily.py.")


if __name__ == "__main__":
    main()
