"""
Is each broker still accepting our key? Asked ~30 minutes before the live runs, so a dead key is a
Telegram before the run rather than a silent halt during it. See deployment/broker_auth_watch.py.

Read-only: CoinDCX is asked for the balances; Kite gets the same session refresh the run itself does.
Only venues with a strategy that is promoted AND funded are asked -- nobody needs to hear that the
key of an account nothing trades from has lapsed.

    python check_broker_auth.py                       # print what it sees; changes and sends nothing
    python check_broker_auth.py --venue coindcx --send

Cron (IST):
    5 8,20 * * *  ... check_broker_auth.py --venue coindcx --send   (crypto runs at 08:35 / 20:35)
    5 9 * * 1-5   ... check_broker_auth.py --venue kite --send      (equity runs at 09:36 / 15:41)
"""

import argparse
import os

from deployment import broker_auth_watch
from deployment.settings import STATE_DIR
from deployment.venues import COINDCX, KITE, venue_of


def venues_in_use(state_dir: str = STATE_DIR) -> set:
    from run_live import eligible
    return {venue_of(record) for record, _ in eligible(state_dir)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Check the live brokers still accept our key/login.")
    ap.add_argument("--venue", choices=["all", COINDCX, KITE], default="all")
    ap.add_argument("--send", action="store_true", help="record the result and send any alert")
    args = ap.parse_args()

    from config import settings
    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
    wanted = {COINDCX, KITE} if args.venue == "all" else {args.venue}
    in_use = venues_in_use()
    for venue in sorted(wanted):
        if venue not in in_use:
            print(f"{venue}: no promoted, funded strategy trades here -- not checked.")
            continue
        ok, detail = broker_auth_watch.probe(venue, settings, config_dir)
        print(f"{venue}: {'OK' if ok else 'FAILED' if ok is False else 'could not tell'}"
              + (f" -- {detail}" if detail else ""))
        if args.send:
            sent = broker_auth_watch.record(venue, ok, detail, STATE_DIR, source="pre-run check")
            if sent:
                print(f"  sent: {sent}")


if __name__ == "__main__":
    main()
