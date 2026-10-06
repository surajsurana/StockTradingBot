"""
Quick test script to confirm your Kite Connect API credentials work.

Setup:
    pip install requests

Usage:
    1. Fill in API_KEY and API_SECRET below (from developers.kite.trade/apps).
    2. Open this URL in your browser (replace API_KEY if needed):
         https://kite.zerodha.com/connect/login?api_key=YOUR_API_KEY&v=3
    3. Log in. You'll land on a "site can't be reached" page for 127.0.0.1 -
       that's expected. Copy the `request_token` value from the address bar.
    4. Paste it into REQUEST_TOKEN below.
    5. Run: python test_kite_connection.py

Note: request_token is single-use and expires within a couple of minutes,
so generate a fresh one each time you run this.
"""

import hashlib
import requests

API_KEY = "icbup2iz3sb5eote"
API_SECRET = "jtaniaq34rmjk2zblgkdzojd7bb293vy"
REQUEST_TOKEN = "fSHtjdfmd7GubyB0j3ldDXDot5KGkPWp"

def main():
    checksum = hashlib.sha256((API_KEY + REQUEST_TOKEN + API_SECRET).encode()).hexdigest()

    resp = requests.post(
        "https://api.kite.trade/session/token",
        data={
            "api_key": API_KEY,
            "request_token": REQUEST_TOKEN,
            "checksum": checksum,
        },
        headers={"X-Kite-Version": "3"},
    )

    print("Session token exchange status:", resp.status_code)
    result = resp.json()
    print(result)

    if resp.status_code != 200 or "data" not in result:
        print("\nFAILED. Common causes: request_token expired/already used, "
              "wrong api_secret, or app not active.")
        return

    access_token = result["data"]["access_token"]
    user_name = result["data"].get("user_name")
    print(f"\nSUCCESS. Logged in as: {user_name}")
    print(f"access_token: {access_token}")

    # Read-only check: fetch profile
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {API_KEY}:{access_token}",
    }
    profile_resp = requests.get("https://api.kite.trade/user/profile", headers=headers)
    print("\nProfile fetch status:", profile_resp.status_code)
    print(profile_resp.json())

    # Read-only check: fetch margins (won't show funds until account is fully active)
    margins_resp = requests.get("https://api.kite.trade/user/margins", headers=headers)
    print("\nMargins fetch status:", margins_resp.status_code)
    print(margins_resp.json())


if __name__ == "__main__":
    main()
