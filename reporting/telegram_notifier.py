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

import requests


def send_telegram_message(message: str, bot_token: str, chat_id: str) -> dict:
    """
    Sends `message` to `chat_id` via the given bot. Falls back to printing
    (rather than raising) if bot_token or chat_id aren't configured yet --
    lets the rest of the pipeline run and be tested before Telegram is set up.
    """
    if not bot_token or not chat_id:
        print("=" * 50)
        print("[TELEGRAM -- not configured, printing instead]")
        print(message)
        print("=" * 50)
        return {"status": "not_configured"}

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    })
    result = resp.json()
    if not result.get("ok"):
        print(f"WARNING: Telegram send failed (status {resp.status_code}): {result}")
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
