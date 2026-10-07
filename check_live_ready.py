"""
Why isn't it trading? -- one command, every promoted strategy, and a straight answer.

    python check_live_ready.py

Reads only. It places no orders, writes no files, changes no settings and refreshes no session.

It exists because "it isn't trading" used to take six files and a crontab to explain, and because the
distinction that matters -- is this MONEY, or is this a mistake -- was invisible from outside. A
stale access token and an empty account produce exactly the same silence.
"""

import sys

from deployment.live_readiness import CONFIG, MONEY, check_all


def _broker_cash(settings) -> dict:
    """What each venue's account actually holds. A venue that cannot be read maps to None, which the
    report shows as "not known" rather than quietly treating as fine."""
    from deployment.venues import COINDCX, KITE
    cash = {}
    for venue, reader in ((KITE, _kite_cash), (COINDCX, _coindcx_cash)):
        try:
            cash[venue] = reader(settings)
        except Exception:
            cash[venue] = None
    return cash


def _kite_cash(settings):
    from deployment.credential_store import credential
    from execution.kite_positions import KiteHoldingsClient      # noqa: F401 (presence check)
    import os
    import requests
    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
    key = credential("KITE_API_KEY", config_dir, settings)
    token = credential("KITE_ACCESS_TOKEN", config_dir, settings)
    if not (key and token):
        return None
    r = requests.get("https://api.kite.trade/user/margins/equity", timeout=20,
                     headers={"X-Kite-Version": "3", "Authorization": f"token {key}:{token}"})
    if r.status_code != 200:
        return None
    return float(((r.json() or {}).get("data") or {}).get("available", {}).get("live_balance", 0) or 0)


def _coindcx_cash(settings):
    import os

    from deployment.credential_store import credential
    from execution.coindcx_client import CoinDCXClient
    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
    key = credential("COINDCX_API_KEY", config_dir, settings)
    secret = credential("COINDCX_API_SECRET", config_dir, settings)
    if not (key and secret):
        return None
    return CoinDCXClient(key, secret).balance_of("INR")


def main() -> int:
    from config import settings

    cash = _broker_cash(settings)
    print("Broker cash:", ", ".join(
        f"{v}: " + (f"Rs{c:,.0f}" if c is not None else "not readable") for v, c in sorted(cash.items()))
        or "none readable")
    print()

    reports = check_all(settings=settings, broker_cash=cash)
    if not reports:
        print("No strategy is promoted to live, so nothing is expected to trade.")
        return 0

    money_only, blocked, ready = [], [], []
    for r in reports:
        (ready if r.ready else money_only if r.money_only else blocked).append(r)

    for r in reports:
        state = "READY" if r.ready else ("WAITING ON MONEY" if r.money_only else "BLOCKED")
        print(f"[{state}] {r.strategy_key}  ({r.venue}, Rs{r.allocated:,.0f} assigned)")
        for b in r.of_kind(CONFIG):
            print(f"    config: {b.detail}")
            if b.fix:
                print(f"            -> {b.fix}")
        for b in r.of_kind(MONEY):
            print(f"    money : {b.detail}")
            if b.fix:
                print(f"            -> {b.fix}")
        print()

    print("-" * 70)
    if blocked:
        print(f"{len(blocked)} strategy(s) have something set up wrong. Funding them would not help.")
    elif money_only:
        print(f"Everything is configured. The only thing stopping {len(money_only)} strategy(s) "
              f"is money.")
    else:
        print("Every promoted strategy is ready to trade.")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
