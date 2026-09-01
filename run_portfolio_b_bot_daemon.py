"""
Portfolio B's Telegram bot -- long-running process, kept alive by
systemd (see deployment/systemd/portfolio-b-bot.service). Replaces the
earlier 1-2 minute cron-polling design with instant replies: Telegram's
getUpdates supports long polling, where the connection itself is held
open server-side until a message arrives, which only a persistent
process can take advantage of.

Deployed 2026-09-01 as a USER-level systemd unit
(~/.config/systemd/user/portfolio-b-bot.service via `systemctl --user`),
not a system-wide one under /etc/systemd/system/ -- installing a
system-wide unit needs root, which the account running this bot doesn't
have. A user unit needs no root at all, but normally only runs while
that user has an active login session; `loginctl enable-linger
<user>` (itself callable by the user on their own account, no root
needed either) is what makes it survive with no session logged in,
exactly like cron already does.

Not meant to be run directly under cron -- run_portfolio_b_bot.py (the
original one-shot, cron-friendly poller) still exists separately for a
manual one-off check.
"""

from deployment.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from portfolio_b.telegram_bot import run_long_polling_loop


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured -- nothing to run.")
        return
    run_long_polling_loop(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    main()
