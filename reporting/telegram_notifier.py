"""
Sends report text to Telegram -- the replacement for the original WhatsApp
plan (WhatsApp needed either a paid Twilio setup or a slower Meta Business
API review; Telegram's bot API is free, has no review process, and sends
instantly to your phone the same way WhatsApp would have).

One-time setup (takes about two minutes):
1. In Telegram, message @BotFather and send /newbot. Follow the prompts
   (pick any name/username). BotFather replies with a bot token that looks
   like "123456789:AAExampleTokenHere" -- copy it into
   config.settings.TELEGRAM_BOT_TOKEN.
2. Start a chat with your new bot (search for its username, send it any
   message, e.g. "hi").
3. Run this file directly (python reporting/telegram_notifier.py) after
   filling in TELEGRAM_BOT_TOKEN -- it'll print your chat_id. Copy that into
   config.settings.TELEGRAM_CHAT_ID.
4. Done -- send_telegram_message() will now deliver real messages.

Until both settings are filled in, every call here just prints instead of
sending -- so the reporting pipeline can be built/tested before you finish
the Telegram setup, matching the same fail-safe fallback pattern used
throughout this project.
"""

import json
import os
from datetime import datetime

import requests

# THE ONE GATE (2026-10-19, per explicit direction: "i think the daily telegram is not making sense
# ever since the dashboard is there. so lets remove the telegram messgas for now. infact we can just
# put messgase on telegram upon reseach completed and result of the research weather its promoted
# to paper trade or retired. and just a small message. not a long message. rest all messages on
# telegram lets stop.").
#
# Every send names its `kind`; only the kinds in deployment.settings.TELEGRAM_ALLOWED_KINDS reach
# Telegram. Everything else is muted: not sent, one line appended to MUTED_LOG_PATH instead, so what
# would have gone out is still findable. The scripts that used to send keep running and keep doing
# their real work (writing the caches the dashboard reads) -- only the push is gone. Undoing this is
# one setting, not a code change.
DEFAULT_ALLOWED_KINDS = frozenset({"research_outcome", "bot_reply", "kite_renewal", "research_alert"})
MUTED_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "logs", "telegram_muted.log")


def allowed_kinds() -> set:
    """The kinds that may reach Telegram right now. Read at call time, so the setting (and a test's
    patch of it) always wins; falls back to the default if deployment.settings can't be imported."""
    try:
        from deployment import settings as deployment_settings
        return set(getattr(deployment_settings, "TELEGRAM_ALLOWED_KINDS", DEFAULT_ALLOWED_KINDS))
    except Exception:
        return set(DEFAULT_ALLOWED_KINDS)


def kind_allowed(kind: str) -> bool:
    """True if a message of this kind may be pushed to Telegram. Also the check for any other
    Telegram API call that answers someone (portfolio_b/telegram_bot.py's answerCallbackQuery), so
    nothing reaches the API around this gate."""
    return kind in allowed_kinds()


def _log_muted(kind: str, message: str) -> None:
    """One line per muted message. Never raises: a full disk or a read-only checkout must not turn
    a muted notification into a crashed job."""
    try:
        snippet = " ".join(str(message or "").split())[:200]
        os.makedirs(os.path.dirname(MUTED_LOG_PATH), exist_ok=True)
        with open(MUTED_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{kind}\t{snippet}\n")
    except Exception:
        pass


def send_telegram_message(message: str, bot_token: str, chat_id: str, reply_markup: dict = None,
                          kind: str = "general", parse_mode: str = "Markdown") -> dict:
    """
    Sends `message` to `chat_id` via the given bot. Falls back to printing
    (rather than raising) if bot_token or chat_id aren't configured yet --
    lets the rest of the pipeline run and be tested before Telegram is set up.

    reply_markup (added 2026-09-01, per explicit direction -- Portfolio B's
    interactive commands): an optional Telegram Bot API reply_markup dict
    (e.g. a ReplyKeyboardMarkup for tappable buttons -- see
    portfolio_b/telegram_bot.py's WATCHLIST_KEYBOARD) -- sent as-is,
    JSON-encoded per the Bot API's own requirement for this field. None
    (the default) omits it entirely -- a plain-text message with no
    keyboard change, exactly today's existing behavior for every other
    caller (run_paper_trading.py, run_portfolio_c.py, ...).

    kind (2026-10-19): what this message is. Only kinds in TELEGRAM_ALLOWED_KINDS are sent; anything
    else -- including every caller that doesn't say, which defaults to "general" -- is muted and
    logged, returning {"status": "muted", "kind": kind}. See the gate's note at the top of this file.

    parse_mode: "Markdown" (the long-standing default) or None for plain text -- a short message that
    may carry a raw strategy key needs plain text, since Markdown rejects an unbalanced "_".
    """
    if not kind_allowed(kind):
        _log_muted(kind, message)
        return {"status": "muted", "kind": kind}

    if not bot_token or not chat_id:
        print("=" * 50)
        print("[TELEGRAM -- not configured, printing instead]")
        print(message)
        print("=" * 50)
        return {"status": "not_configured"}

    data = {"chat_id": chat_id, "text": message}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(url, data=data)
    result = resp.json()
    if not result.get("ok"):
        print(f"WARNING: Telegram send failed (status {resp.status_code}): {result}")
    return result


def set_bot_commands(bot_token: str, commands: list) -> dict:
    """
    Registers the bot's command menu (the list shown when a user taps the
    "/" icon next to Telegram's message box, each with a short
    description) via the Bot API's setMyCommands. `commands`: a list of
    {"command": "watchlist", "description": "..."} dicts (no leading
    slash in "command", per the Bot API's own convention). Idempotent --
    safe to call every time the bot starts, always overwrites with the
    current list rather than appending. No-ops (prints, like
    send_telegram_message) if bot_token isn't configured yet.
    """
    if not bot_token:
        print("[TELEGRAM -- not configured, skipping setMyCommands]")
        return {"status": "not_configured"}

    url = f"https://api.telegram.org/bot{bot_token}/setMyCommands"
    resp = requests.post(url, json={"commands": commands})
    result = resp.json()
    if not result.get("ok"):
        print(f"WARNING: setMyCommands failed (status {resp.status_code}): {result}")
    return result


def delete_bot_commands(bot_token: str) -> dict:
    """
    Clears the bot's command menu (the "/" icon next to Telegram's
    message box) via the Bot API's deleteMyCommands -- the counterpart
    to set_bot_commands() above, for a bot that wants ONLY its own
    custom reply-keyboard buttons (see portfolio_b/telegram_bot.py's
    MAIN_MENU_KEYBOARD) as its interface, not Telegram's separate
    built-in command list. No-ops (prints) if bot_token isn't configured
    yet, same fallback convention as every other function here.
    """
    if not bot_token:
        print("[TELEGRAM -- not configured, skipping deleteMyCommands]")
        return {"status": "not_configured"}

    url = f"https://api.telegram.org/bot{bot_token}/deleteMyCommands"
    resp = requests.post(url)
    result = resp.json()
    if not result.get("ok"):
        print(f"WARNING: deleteMyCommands failed (status {resp.status_code}): {result}")
    return result


def get_chat_id(bot_token: str):
    """
    Helper for one-time setup: after you've messaged your bot at least once,
    this fetches recent updates and prints the chat_id(s) found -- use
    whichever one matches your own account.
    """
    if not bot_token:
        print("Fill in TELEGRAM_BOT_TOKEN in config/settings.py first, then re-run this.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    resp = requests.get(url)
    data = resp.json()

    if not data.get("ok"):
        print(f"Request failed: {data}")
        return

    results = data.get("result", [])
    if not results:
        print("No messages found yet. Send your bot any message on Telegram first, then re-run this.")
        return

    seen = set()
    for update in results:
        message = update.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        name = chat.get("first_name") or chat.get("username") or "(unknown)"
        if chat_id is not None and chat_id not in seen:
            seen.add(chat_id)
            print(f"chat_id: {chat_id}  (from: {name})")


if __name__ == "__main__":
    # Running this file directly (python reporting/telegram_notifier.py) means
    # Python only knows about the reporting/ folder, not the project root --
    # so "from config import settings" fails with "No module named 'config'".
    # This adds the project root to the path so the script works either way:
    # both "python reporting/telegram_notifier.py" and
    # "python -m reporting.telegram_notifier" from the project root.
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from config import settings
    get_chat_id(settings.TELEGRAM_BOT_TOKEN)
