"""
Portfolio B's interactive Telegram command handler -- /watchlist,
/addstock, /removestock, /help. Runs as a long-lived, always-on process
(see run_portfolio_b_bot_daemon.py + its systemd unit) rather than a
cron-polled script: Telegram's getUpdates supports LONG polling (a
request that Telegram itself holds open for up to `timeout` seconds,
returning the instant a message actually arrives instead of the caller
having to guess a check interval) -- this needs a process that stays
running to hold that connection, not a short-lived cron invocation.
Confirmed 2026-09-01, per explicit direction (replacing the original
2-minute cron-polling design, which is still available as
poll_and_process_commands()'s default one-shot, timeout=0 behavior for
any caller that still wants it).

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
from portfolio_b.engine import get_watchlist_with_names
from reporting.telegram_notifier import send_telegram_message, set_bot_commands

_WATCHLIST_PATTERN = re.compile(r"^/watchlist\b", re.IGNORECASE)
_HELP_PATTERN = re.compile(r"^/help\b|^/start\b", re.IGNORECASE)
_ADD_PATTERN = re.compile(r"^/addstock\s+(\S+)", re.IGNORECASE)
_REMOVE_PATTERN = re.compile(r"^/removestock\s+(\S+)", re.IGNORECASE)

# Alphanumeric plus '&' and '-' (both appear in real NSE tickers, e.g.
# "M&M.NS", "L&TFH.NS") and a reasonable length -- a fast, friendly
# rejection of obvious typos/garbage BEFORE a slow yfinance round-trip.
# Never a security boundary by itself: this data only ever lands in a
# plain JSON dict, never executed.
_VALID_SYMBOL_FORMAT = re.compile(r"[A-Z0-9&\-]{1,20}\.NS")

# The "/" command menu Telegram shows next to the message box -- set
# once at bot startup (see run_portfolio_b_bot_daemon.py) via
# set_bot_commands(). No leading slash, per the Bot API's own convention.
BOT_COMMANDS = [
    {"command": "watchlist", "description": "Show the current watchlist"},
    {"command": "addstock", "description": "Add a symbol, e.g. /addstock TATASTEEL"},
    {"command": "removestock", "description": "Remove a symbol, e.g. /removestock RVNL"},
    {"command": "help", "description": "Show available commands"},
]

# A persistent, tappable keyboard for the two commands that take no
# argument -- /addstock and /removestock still need a symbol typed
# after them, so they stay in the "/" command menu (BOT_COMMANDS above)
# rather than as buttons here. resize_keyboard shrinks it to fit instead
# of Telegram's oversized default.
QUICK_ACTIONS_KEYBOARD = {
    "keyboard": [[{"text": "/watchlist"}, {"text": "/help"}]],
    "resize_keyboard": True,
}


def _normalize_symbol(raw: str) -> str:
    """Symbols are typed by hand in Telegram -- normalize casing and the
    common ".NS" omission before validating, so "/addstock tatasteel"
    and "/addstock TATASTEEL.NS" both resolve the same way."""
    symbol = raw.strip().upper()
    if not symbol.endswith(".NS"):
        symbol = f"{symbol}.NS"
    return symbol


def fetch_company_name_if_tradeable(symbol: str, fetch_price_fn: Callable = fetch_daily_candles,
                                     fetch_info_fn: Optional[Callable] = None) -> Optional[str]:
    """
    Confirms yfinance actually has recent PRICE data for this ticker --
    the same check performed manually before every symbol in
    portfolio_b/engine.py's DEFAULT_WATCHLIST was accepted -- and, if
    so, also looks up its company name. Returns the name (a possibly
    empty string if the ticker is tradeable but has no longName/
    shortName on file) if tradeable, or None if it isn't (or the price
    fetch failed) -- never raises.

    fetch_info_fn: defaults to a real yfinance .info lookup; injectable
    for tests. A name-lookup failure after a SUCCESSFUL price fetch
    still returns "" (tradeable, name just unavailable) rather than
    rejecting the symbol -- the name is a display nicety, not a
    correctness requirement.
    """
    try:
        df = fetch_price_fn(symbol, period="5d")
        if df is None or df.empty:
            return None
    except Exception:
        return None

    if fetch_info_fn is None:
        def fetch_info_fn(sym):
            import yfinance as yf
            return yf.Ticker(sym).info

    try:
        info = fetch_info_fn(symbol) or {}
        return info.get("longName") or info.get("shortName") or ""
    except Exception:
        return ""


def _handle_command(text: str, name_fn: Callable = fetch_company_name_if_tradeable) -> Optional[str]:
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
        watchlist = get_watchlist_with_names()
        if not watchlist:
            return "Portfolio B's watchlist is currently empty."
        lines = [f"- {name} ({symbol})" if name else f"- {symbol}" for symbol, name in watchlist.items()]
        return "Portfolio B watchlist:\n" + "\n".join(lines)

    if _HELP_PATTERN.match(text):
        return ("Portfolio B commands:\n"
                "/watchlist -- show the current list\n"
                "/addstock SYMBOL -- add a symbol (e.g. /addstock TATASTEEL)\n"
                "/removestock SYMBOL -- remove a symbol")

    # Matched BEFORE the argument-capturing patterns below, on just the
    # command word -- so "/addstock" sent alone (no symbol typed after
    # it, e.g. tapped straight from Telegram's "/" menu and sent as-is)
    # gets a clear usage reply instead of silently doing nothing. This
    # was a real, reported gap: a bare "/addstock" produced no reply, no
    # log entry, and no error -- indistinguishable from the message
    # never having arrived at all.
    if re.match(r"^/addstock\b", text, re.IGNORECASE):
        add_match = _ADD_PATTERN.match(text)
        if not add_match:
            return "Usage: /addstock SYMBOL (e.g. /addstock TATASTEEL)"
        symbol = _normalize_symbol(add_match.group(1))
        if not _VALID_SYMBOL_FORMAT.fullmatch(symbol):
            return f"'{symbol}' doesn't look like a valid NSE ticker -- not added."
        watchlist = get_watchlist_with_names()
        if symbol in watchlist:
            return f"{symbol} is already on the watchlist."
        name = name_fn(symbol)
        if name is None:
            return f"Could not find recent trading data for {symbol} -- not added. Check the ticker and try again."
        watchlist[symbol] = name
        pbs.save_watchlist(watchlist)
        label = f"{name} ({symbol})" if name else symbol
        return f"Added {label} to Portfolio B's watchlist ({len(watchlist)} symbols now)."

    if re.match(r"^/removestock\b", text, re.IGNORECASE):
        remove_match = _REMOVE_PATTERN.match(text)
        if not remove_match:
            return "Usage: /removestock SYMBOL (e.g. /removestock RVNL)"
        symbol = _normalize_symbol(remove_match.group(1))
        watchlist = get_watchlist_with_names()
        if symbol not in watchlist:
            return f"{symbol} isn't on the watchlist."
        del watchlist[symbol]
        pbs.save_watchlist(watchlist)
        return f"Removed {symbol} from Portfolio B's watchlist ({len(watchlist)} symbols now)."

    return None


def poll_and_process_commands(bot_token: str, chat_id: str,
                               name_fn: Callable = fetch_company_name_if_tradeable,
                               long_poll_timeout: int = 0) -> list:
    """
    Calls Telegram's getUpdates ONCE, processes any new command messages
    from the configured chat_id ONLY (see module docstring's security
    note), replies to each (with QUICK_ACTIONS_KEYBOARD attached so the
    reply itself offers tappable next actions), and advances the
    persisted offset so the NEXT call never reprocesses the same message.

    long_poll_timeout: 0 (the default) returns immediately with whatever
    is already waiting -- the original one-shot behavior, still useful
    for a manual check or a test. run_portfolio_b_bot_daemon.py's loop
    passes 30 (Telegram's own recommended long-poll window): the HTTP
    request itself blocks server-side for up to 30s, returning the
    INSTANT a new message arrives rather than after a fixed local sleep
    -- this is what makes replies near-instant without hammering
    Telegram's API in a tight loop.

    One bad/malformed update is isolated in its own try/except and never
    blocks the rest of the batch or crashes the whole poll cycle -- same
    per-item isolation discipline as run_paper_trading.py's own
    per-strategy try/except.

    Returns [(command_text, reply_text), ...] for every command actually
    processed (empty list if nothing new, or nothing from the right
    chat) -- for the caller to log.
    """
    offset = pbs.load_telegram_offset()
    # requests' own timeout must exceed Telegram's long_poll_timeout, or
    # the client would give up before Telegram's own hold-open window
    # elapses -- +10s of slack for network/response overhead.
    resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getUpdates",
                         params={"offset": offset + 1, "timeout": long_poll_timeout},
                         timeout=long_poll_timeout + 10)
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
            reply = _handle_command(message["text"], name_fn=name_fn)
        except Exception as e:
            reply = f"Something went wrong processing that command: {e}"

        if reply is not None:
            send_telegram_message(reply, bot_token, chat_id, reply_markup=QUICK_ACTIONS_KEYBOARD)
            processed.append((message["text"], reply))

    if max_update_id > offset:
        pbs.save_telegram_offset(max_update_id)

    return processed


def run_long_polling_loop(bot_token: str, chat_id: str) -> None:
    """
    Runs forever, processing Telegram commands as they arrive -- the
    body of run_portfolio_b_bot_daemon.py's systemd service. Registers
    the command menu once at startup (idempotent -- also re-runs safely
    on every service restart), then loops poll_and_process_commands()
    with long_poll_timeout=30 -- each call already blocks server-side
    until a message arrives or 30s elapses, so no additional sleep()
    between iterations is needed or wanted.

    A single poll's own network error (Telegram briefly unreachable,
    DNS hiccup, ...) is logged and the loop continues on the NEXT
    iteration rather than crashing the whole service -- systemd's
    Restart=always is the outer safety net for anything this inner
    catch doesn't handle.
    """
    set_bot_commands(bot_token, BOT_COMMANDS)
    print("Portfolio B Telegram bot: long-polling started.")
    while True:
        try:
            poll_and_process_commands(bot_token, chat_id, long_poll_timeout=30)
        except Exception as e:
            print(f"WARNING: poll cycle failed (non-fatal, retrying): {type(e).__name__}: {e}")
