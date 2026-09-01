"""
Portfolio B's interactive Telegram command handler -- /watchlist,
/addstock, /removestock, /help. Polls Telegram's getUpdates API rather
than a webhook: this VPS runs everything via cron (short-lived
invocations), never a long-running server, so polling (a new ~2-minute
cron entry, see run_portfolio_b_bot.py) fits the existing architecture
without needing a new always-on process, port, or SSL certificate.
Confirmed 2026-09-01, per explicit direction.

SECURITY: every incoming message is checked against the configured
TELEGRAM_CHAT_ID before being acted on -- a message from any other chat
is silently ignored (never processed, never replied to, never even
logged with its content), since this bot's username is, in principle,
discoverable/reachable by anyone on Telegram. This is the ONLY access
control -- there is no additional password/PIN, so anyone with access
to the configured chat can add or remove watchlist symbols.
"""

import re
from typing import Callable, Optional

import requests

from data.fetch_historical import fetch_daily_candles
from portfolio_b import state as pbs
from portfolio_b.engine import get_watchlist
from reporting.telegram_notifier import send_telegram_message

_WATCHLIST_PATTERN = re.compile(r"^/watchlist\b", re.IGNORECASE)
_HELP_PATTERN = re.compile(r"^/help\b", re.IGNORECASE)
_ADD_PATTERN = re.compile(r"^/addstock\s+(\S+)", re.IGNORECASE)
_REMOVE_PATTERN = re.compile(r"^/removestock\s+(\S+)", re.IGNORECASE)

# Alphanumeric plus '&' and '-' (both appear in real NSE tickers, e.g.
# "M&M.NS", "L&TFH.NS") and a reasonable length -- a fast, friendly
# rejection of obvious typos/garbage BEFORE a slow yfinance round-trip.
# Never a security boundary by itself: this data only ever lands in a
# plain JSON array, never executed.
_VALID_SYMBOL_FORMAT = re.compile(r"[A-Z0-9&\-]{1,20}\.NS")


def _normalize_symbol(raw: str) -> str:
    """Symbols are typed by hand in Telegram -- normalize casing and the
    common ".NS" omission before validating, so "/addstock tatasteel"
    and "/addstock TATASTEEL.NS" both resolve the same way."""
    symbol = raw.strip().upper()
    if not symbol.endswith(".NS"):
        symbol = f"{symbol}.NS"
    return symbol


def validate_symbol_is_tradeable(symbol: str, fetch_fn: Callable = fetch_daily_candles) -> bool:
    """Confirms yfinance actually has recent data for this ticker -- the
    same check performed manually before every symbol in
    portfolio_b/engine.py's DEFAULT_WATCHLIST was accepted. Returns
    False (never raises) on any fetch failure or empty result."""
    try:
        df = fetch_fn(symbol, period="5d")
        return df is not None and not df.empty
    except Exception:
        return False


def _handle_command(text: str, validate_fn: Callable = validate_symbol_is_tradeable) -> Optional[str]:
    """
    Returns the reply text for one command message, or None if `text`
    isn't a recognized command (so the bot stays silent rather than
    replying to every unrelated message in a chat that also receives
    regular trading notifications). Never raises -- an unexpected error
    is the caller's (poll_and_process_commands()) responsibility to
    catch, so one bad message can never abort the rest of a poll batch.
    """
    text = text.strip()

    if _WATCHLIST_PATTERN.match(text):
        watchlist = get_watchlist()
        if not watchlist:
            return "Portfolio B's watchlist is currently empty."
        return "Portfolio B watchlist:\n" + "\n".join(f"- {s}" for s in watchlist)

    if _HELP_PATTERN.match(text):
        return ("Portfolio B commands:\n"
                "/watchlist -- show the current list\n"
                "/addstock SYMBOL -- add a symbol (e.g. /addstock TATASTEEL)\n"
                "/removestock SYMBOL -- remove a symbol")

    add_match = _ADD_PATTERN.match(text)
    if add_match:
        symbol = _normalize_symbol(add_match.group(1))
        if not _VALID_SYMBOL_FORMAT.fullmatch(symbol):
            return f"'{symbol}' doesn't look like a valid NSE ticker -- not added."
        watchlist = get_watchlist()
        if symbol in watchlist:
            return f"{symbol} is already on the watchlist."
        if not validate_fn(symbol):
            return f"Could not find recent trading data for {symbol} -- not added. Check the ticker and try again."
        watchlist.append(symbol)
        pbs.save_watchlist(watchlist)
        return f"Added {symbol} to Portfolio B's watchlist ({len(watchlist)} symbols now)."

    remove_match = _REMOVE_PATTERN.match(text)
    if remove_match:
        symbol = _normalize_symbol(remove_match.group(1))
        watchlist = get_watchlist()
        if symbol not in watchlist:
            return f"{symbol} isn't on the watchlist."
        watchlist.remove(symbol)
        pbs.save_watchlist(watchlist)
        return f"Removed {symbol} from Portfolio B's watchlist ({len(watchlist)} symbols now)."

    return None


def poll_and_process_commands(bot_token: str, chat_id: str,
                               validate_fn: Callable = validate_symbol_is_tradeable) -> list:
    """
    Calls Telegram's getUpdates ONCE, processes any new command messages
    from the configured chat_id ONLY (see module docstring's security
    note), replies to each, and advances the persisted offset so the
    NEXT poll (a fresh cron invocation, not a long-lived loop) never
    reprocesses the same message.

    One bad/malformed update is isolated in its own try/except and never
    blocks the rest of the batch or crashes the whole poll cycle -- same
    per-item isolation discipline as run_paper_trading.py's own
    per-strategy try/except.

    Returns [(command_text, reply_text), ...] for every command actually
    processed (empty list if nothing new, or nothing from the right
    chat) -- for the caller (run_portfolio_b_bot.py) to log.
    """
    offset = pbs.load_telegram_offset()
    resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getUpdates",
                         params={"offset": offset + 1, "timeout": 0}, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        return []

    processed = []
    max_update_id = offset

    for update in result.get("result", []):
        max_update_id = max(max_update_id, update.get("update_id", max_update_id))
        message = update.get("message")
        if not message or "text" not in message or "chat" not in message:
            continue
        if str(message["chat"].get("id")) != str(chat_id):
            continue   # SECURITY: only the configured chat is ever acted on

        try:
            reply = _handle_command(message["text"], validate_fn=validate_fn)
        except Exception as e:
            reply = f"Something went wrong processing that command: {e}"

        if reply is not None:
            send_telegram_message(reply, bot_token, chat_id)
            processed.append((message["text"], reply))

    if max_update_id > offset:
        pbs.save_telegram_offset(max_update_id)

    return processed
