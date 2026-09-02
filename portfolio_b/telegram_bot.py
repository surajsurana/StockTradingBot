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

/addstock accepts a COMPANY NAME, not just a ticker (confirmed 2026-09-01,
per explicit direction -- "I cannot remember scrip codes"): it searches
yfinance's own symbol search, filters to NSE-listed (exchange "NSI")
results, and replies with tappable INLINE buttons (one per candidate,
plus Cancel) rather than adding anything immediately -- nothing is added
to the watchlist until the user actually taps a button. Handling the tap
means processing Telegram's separate `callback_query` update type
alongside ordinary `message` updates (see poll_and_process_commands()).

SECURITY: every incoming message AND callback query is checked against
the configured TELEGRAM_CHAT_ID before being acted on -- anything else
is silently ignored (never processed, never replied to, never even
logged with its content), since this bot's username is, in principle,
discoverable/reachable by anyone on Telegram. This is the ONLY access
control -- there is no additional password/PIN, so anyone with access
to the configured chat can add or remove watchlist symbols.
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from data.fetch_historical import fetch_daily_candles
from portfolio_b import state as pbs
from portfolio_b.engine import get_watchlist_with_names
from reporting.telegram_notifier import delete_bot_commands, send_telegram_message

_WATCHLIST_PATTERN = re.compile(r"^/watchlist\b", re.IGNORECASE)
_HELP_PATTERN = re.compile(r"^/help\b|^/start\b", re.IGNORECASE)
_ADD_PATTERN = re.compile(r"^/addstock\s+(.+)", re.IGNORECASE)
_REMOVE_PATTERN = re.compile(r"^/removestock\s*(\S+)?", re.IGNORECASE)

# A ticker-shaped query (alphanumeric plus '&' and '-', both appear in
# real NSE tickers e.g. "M&M.NS", "L&TFH.NS") is tried as a DIRECT ticker
# fallback when search finds nothing -- never a security boundary by
# itself, this data only ever lands in a plain JSON dict, never executed.
_VALID_SYMBOL_FORMAT = re.compile(r"[A-Z0-9&\-]{1,20}\.NS")

# Telegram callback_data is capped at 64 bytes -- symbols are always
# short enough ("TATASTEEL.NS" etc.), so the symbol alone (never the
# company name, which can be long) is encoded; the name is re-resolved
# via name_fn at confirm time instead of being carried in the button.
_ADD_CALLBACK_PREFIX = "pbadd:"
_REMOVE_CALLBACK_PREFIX = "pbrm:"
_CANCEL_CALLBACK = "pbcancel"

# Search results are capped here -- both to keep the inline keyboard
# short enough to read on a phone and because Telegram limits how many
# buttons/how much callback_data a single message can carry.
MAX_ADD_CANDIDATES = 5

# The "/" command menu Telegram shows next to the message box is
# deliberately CLEARED (see run_long_polling_loop()'s delete_bot_commands()
# call), not populated -- confirmed 2026-09-01, per explicit direction
# ("we dont need the menu button at all, can we remove it"). Typed slash
# commands (/watchlist, /addstock NAME, /removestock [SYMBOL]) still
# work -- Telegram just no longer advertises them in that separate menu.
# MAIN_MENU_KEYBOARD below (buttons under the text box, always visible)
# is the one and only recommended interface.

# Button labels for MAIN_MENU_KEYBOARD below -- also matched directly as
# plain text in _handle_command() (tapping one of these buttons sends
# its exact label as an ordinary text message, same as typing it).
_WATCHLIST_BUTTON = "📋 Watchlist"
_ADD_STOCK_BUTTON = "➕ Add Stock"
_REMOVE_STOCK_BUTTON = "➖ Remove Stock"
_HELP_BUTTON = "❓ Help"

# A persistent, tappable keyboard covering all four actions -- always
# visible under the text box (a Telegram ReplyKeyboardMarkup), not a
# one-off inline keyboard attached to a single message. resize_keyboard
# shrinks it to fit instead of Telegram's oversized default.
MAIN_MENU_KEYBOARD = {
    "keyboard": [
        [{"text": _WATCHLIST_BUTTON}, {"text": _ADD_STOCK_BUTTON}],
        [{"text": _REMOVE_STOCK_BUTTON}, {"text": _HELP_BUTTON}],
    ],
    "resize_keyboard": True,
}

# load_pending_action()'s value while the bot is waiting for the user's
# next plain-text reply to name a stock (see _handle_command()'s ask-
# then-reply flow for Add Stock).
_PENDING_ADDSTOCK = "addstock"


@dataclass
class CommandReply:
    """text: what to send back. reply_markup: None means the caller
    (poll_and_process_commands()) attaches the default
    MAIN_MENU_KEYBOARD; an explicit dict (e.g. an inline candidate
    picker) overrides that for this one reply only."""
    text: str
    reply_markup: Optional[dict] = None


def _normalize_symbol(raw: str) -> str:
    """Symbols are typed by hand in Telegram -- normalize casing and the
    common ".NS" omission before validating, so "tatasteel" and
    "TATASTEEL.NS" both resolve the same way."""
    symbol = raw.strip().upper()
    if not symbol.endswith(".NS"):
        symbol = f"{symbol}.NS"
    return symbol


def fetch_company_name_if_tradeable(symbol: str, fetch_price_fn: Callable = fetch_daily_candles,
                                     fetch_info_fn: Optional[Callable] = None) -> Optional[str]:
    """
    Confirms yfinance actually has recent PRICE data for this ticker, and
    if so, also looks up its company name. Returns the name (a possibly
    empty string if the ticker is tradeable but has no longName/
    shortName on file) if tradeable, or None if it isn't (or the price
    fetch failed) -- never raises. Used both for the search fallback path
    and to re-resolve a candidate's name at confirm time (see
    _handle_callback_query()).

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


def search_nse_symbol_candidates(query: str, search_fn: Optional[Callable] = None,
                                  max_results: int = MAX_ADD_CANDIDATES) -> list:
    """
    Resolves a free-text query (a company name, a partial name, or a
    ticker) to real, NSE-listed candidates -- so /addstock never requires
    remembering an exact scrip code. Returns [{"symbol": "TATASTEEL.NS",
    "name": "TATA STEEL LIMITED"}, ...], filtered to exchange == "NSI"
    (yfinance's own code for NSE India) and capped at max_results.

    search_fn: defaults to a real yfinance.Search(query).quotes lookup;
    injectable for tests. Never raises -- a search failure (network,
    yfinance internals) returns an empty list, same fail-quiet
    convention as fetch_company_name_if_tradeable, so the caller falls
    back to trying the query as a literal ticker instead.
    """
    if search_fn is None:
        def search_fn(q):
            import yfinance as yf
            return yf.Search(q, max_results=10).quotes

    try:
        quotes = search_fn(query) or []
    except Exception:
        return []

    candidates = []
    seen_symbols = set()
    for quote in quotes:
        if quote.get("exchange") != "NSI":
            continue
        symbol = quote.get("symbol")
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        name = quote.get("longname") or quote.get("shortname") or ""
        candidates.append({"symbol": symbol, "name": name})
        if len(candidates) >= max_results:
            break

    return candidates


def _build_candidate_keyboard(candidates: list) -> dict:
    """One inline button per candidate (label: 'Name (SYMBOL)', or just
    the symbol if no name), each row on its own line for readability on
    a phone, plus a final Cancel row."""
    rows = []
    for c in candidates:
        label = f"{c['name']} ({c['symbol']})" if c["name"] else c["symbol"]
        rows.append([{"text": label, "callback_data": f"{_ADD_CALLBACK_PREFIX}{c['symbol']}"}])
    rows.append([{"text": "Cancel", "callback_data": _CANCEL_CALLBACK}])
    return {"inline_keyboard": rows}


def _build_removal_keyboard(watchlist: dict) -> dict:
    """One inline button per CURRENT watchlist entry -- so removing
    never requires remembering/typing the exact ticker either."""
    rows = []
    for symbol, name in watchlist.items():
        label = f"{name} ({symbol})" if name else symbol
        rows.append([{"text": label, "callback_data": f"{_REMOVE_CALLBACK_PREFIX}{symbol}"}])
    rows.append([{"text": "Cancel", "callback_data": _CANCEL_CALLBACK}])
    return {"inline_keyboard": rows}


def _search_and_offer_addstock(query: str, name_fn: Callable = fetch_company_name_if_tradeable,
                                search_fn: Optional[Callable] = None) -> CommandReply:
    """
    Shared by both addstock paths -- typed inline ("/addstock Tata
    Steel" in one message) and ask-then-reply (Add Stock tapped, THEN
    the stock named in a follow-up message). Searches, builds the
    confirm keyboard; never adds anything itself -- that only happens
    when a button is actually tapped (_handle_callback_query()).
    """
    candidates = search_nse_symbol_candidates(query, search_fn=search_fn)
    if not candidates:
        # Fall back to treating the query as a literal ticker -- still
        # always a confirm step (one candidate), never an immediate add,
        # so typing an exact scrip code behaves the same way as
        # searching by name.
        symbol = _normalize_symbol(query)
        if _VALID_SYMBOL_FORMAT.fullmatch(symbol):
            name = name_fn(symbol)
            if name is not None:
                candidates = [{"symbol": symbol, "name": name}]

    if not candidates:
        return CommandReply(f"Couldn't find any NSE-listed match for '{query}'. "
                             f"Try a different name, or the exact ticker (e.g. TATASTEEL).")

    watchlist = get_watchlist_with_names()
    candidates = [c for c in candidates if c["symbol"] not in watchlist]
    if not candidates:
        return CommandReply(f"'{query}' matches a symbol already on the watchlist.")

    plural = "es" if len(candidates) > 1 else ""
    return CommandReply(f"Found {len(candidates)} match{plural} for '{query}' -- tap the one you mean:",
                         reply_markup=_build_candidate_keyboard(candidates))


def _handle_command(text: str, name_fn: Callable = fetch_company_name_if_tradeable,
                     search_fn: Optional[Callable] = None) -> Optional[CommandReply]:
    """
    Returns the CommandReply for one command message, or None if `text`
    isn't a recognized command (so the bot stays silent rather than
    replying to every unrelated message in a chat that also receives
    regular trading notifications). Never raises -- an unexpected error
    is the caller's (poll_and_process_commands()) responsibility to
    catch, so one bad message can never abort the rest of a poll batch.

    ASK-THEN-REPLY for Add Stock (confirmed 2026-09-01, per explicit
    direction -- tapping Add Stock was immediately sending a bare
    "/addstock" with no chance to type a name first): tapping
    _ADD_STOCK_BUTTON (or sending bare "/addstock") does NOT search
    anything yet -- it asks what to add and records that in
    pbs.load_pending_action()/save_pending_action(). The NEXT message
    is then checked FIRST, before any command pattern: if a stock name
    is pending and this text isn't itself a recognized command/button,
    it's treated as that stock's name. Sending any recognized command
    or button while a reply is pending clears the pending state instead
    of misreading it as a stock name (e.g. changing your mind and
    tapping Watchlist instead).
    """
    text = text.strip()

    pending = pbs.load_pending_action()
    is_known_trigger = (text.startswith("/") or
                         text in (_WATCHLIST_BUTTON, _ADD_STOCK_BUTTON, _REMOVE_STOCK_BUTTON, _HELP_BUTTON))
    if pending == _PENDING_ADDSTOCK and not is_known_trigger:
        pbs.save_pending_action(None)
        return _search_and_offer_addstock(text, name_fn=name_fn, search_fn=search_fn)

    if text == _WATCHLIST_BUTTON or _WATCHLIST_PATTERN.match(text):
        pbs.save_pending_action(None)
        watchlist = get_watchlist_with_names()
        if not watchlist:
            return CommandReply("Portfolio B's watchlist is currently empty.")
        lines = [f"- {name} ({symbol})" if name else f"- {symbol}" for symbol, name in watchlist.items()]
        return CommandReply("Portfolio B watchlist:\n" + "\n".join(lines))

    if text == _HELP_BUTTON or _HELP_PATTERN.match(text):
        pbs.save_pending_action(None)
        return CommandReply(
            "Portfolio B commands:\n"
            "Add Stock -- name a company and I'll find it on the NSE for you to confirm\n"
            "Remove Stock -- tap a symbol from the current list to remove it\n"
            "Watchlist -- show the current list\n"
            "(typed equivalents: /addstock NAME, /removestock [SYMBOL], /watchlist)")

    # Matched on just the command word/button, separately from the
    # argument-capturing pattern below -- covers both the button (which
    # never carries an inline argument) and a bare "/addstock" (e.g.
    # tapped straight from Telegram's "/" menu and sent as-is, a real
    # reported gap: it used to produce no reply, no log entry, nothing
    # at all).
    if text == _ADD_STOCK_BUTTON or re.match(r"^/addstock\b", text, re.IGNORECASE):
        add_match = _ADD_PATTERN.match(text)
        if not add_match:
            pbs.save_pending_action(_PENDING_ADDSTOCK)
            return CommandReply("What stock would you like to add? Type the company name or ticker "
                                 "(e.g. Tata Steel).")
        pbs.save_pending_action(None)
        return _search_and_offer_addstock(add_match.group(1).strip(), name_fn=name_fn, search_fn=search_fn)

    if text == _REMOVE_STOCK_BUTTON or re.match(r"^/removestock\b", text, re.IGNORECASE):
        pbs.save_pending_action(None)
        remove_match = _REMOVE_PATTERN.match(text)
        symbol_arg = remove_match.group(1) if remove_match else None

        if not symbol_arg:
            # Sent alone (or tapped as a button) -- show a tap-to-remove
            # picker instead of requiring the exact symbol typed/remembered.
            watchlist = get_watchlist_with_names()
            if not watchlist:
                return CommandReply("Portfolio B's watchlist is currently empty -- nothing to remove.")
            return CommandReply("Tap a symbol to remove it:", reply_markup=_build_removal_keyboard(watchlist))

        symbol = _normalize_symbol(symbol_arg)
        watchlist = get_watchlist_with_names()
        if symbol not in watchlist:
            return CommandReply(f"{symbol} isn't on the watchlist.")
        del watchlist[symbol]
        pbs.save_watchlist(watchlist)
        return CommandReply(f"Removed {symbol} from Portfolio B's watchlist ({len(watchlist)} symbols now).")

    return None


def _handle_callback_query(data: str, name_fn: Callable = fetch_company_name_if_tradeable) -> str:
    """
    Handles a tap on one of _build_candidate_keyboard()'s or
    _build_removal_keyboard()'s inline buttons. Returns the reply text
    (never None -- a callback always gets a reply, even Cancel, so the
    tap always visibly does something). Never raises -- same isolation
    discipline as _handle_command(), enforced by the caller
    (poll_and_process_commands()).
    """
    # Defensive: a button tap always resolves or cancels the add/remove
    # flow it belongs to, so any stale "waiting for a stock name" state
    # (there shouldn't be one at this point, but never leave it dangling
    # if there somehow is) is cleared here too.
    pbs.save_pending_action(None)

    if data == _CANCEL_CALLBACK:
        return "Cancelled."

    if data.startswith(_ADD_CALLBACK_PREFIX):
        symbol = data[len(_ADD_CALLBACK_PREFIX):]
        watchlist = get_watchlist_with_names()
        if symbol in watchlist:
            return f"{symbol} is already on the watchlist."
        # Re-resolved rather than carried in callback_data (which only
        # ever holds the symbol, per the 64-byte limit -- see module
        # docstring) -- also re-confirms it's STILL tradeable at the
        # moment of confirmation, not just at search time.
        name = name_fn(symbol)
        if name is None:
            return f"Could not find recent trading data for {symbol} -- not added."
        watchlist[symbol] = name
        pbs.save_watchlist(watchlist)
        label = f"{name} ({symbol})" if name else symbol
        return f"Added {label} to Portfolio B's watchlist ({len(watchlist)} symbols now)."

    if data.startswith(_REMOVE_CALLBACK_PREFIX):
        symbol = data[len(_REMOVE_CALLBACK_PREFIX):]
        watchlist = get_watchlist_with_names()
        if symbol not in watchlist:
            return f"{symbol} isn't on the watchlist."
        del watchlist[symbol]
        pbs.save_watchlist(watchlist)
        return f"Removed {symbol} from Portfolio B's watchlist ({len(watchlist)} symbols now)."

    return "Unrecognized action."


def _answer_callback_query(bot_token: str, callback_query_id: str) -> None:
    """Required by the Bot API to stop a tapped button's loading spinner
    -- has no visible text of its own (the actual confirmation is a
    normal sendMessage, see poll_and_process_commands()). Best-effort:
    a failure here is logged, never raised, since the real user-visible
    reply has already been sent by the time this runs."""
    try:
        resp = requests.post(f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                              json={"callback_query_id": callback_query_id}, timeout=10)
        result = resp.json()
        if not result.get("ok"):
            print(f"WARNING: answerCallbackQuery failed: {result}")
    except Exception as e:
        print(f"WARNING: answerCallbackQuery request failed: {type(e).__name__}: {e}")


def poll_and_process_commands(bot_token: str, chat_id: str,
                               name_fn: Callable = fetch_company_name_if_tradeable,
                               search_fn: Optional[Callable] = None,
                               long_poll_timeout: int = 0) -> list:
    """
    Calls Telegram's getUpdates ONCE, processes any new command messages
    OR inline-button taps (callback_query updates) from the configured
    chat_id ONLY (see module docstring's security note), replies to each,
    and advances the persisted offset so the NEXT call never reprocesses
    the same update.

    A plain command message gets MAIN_MENU_KEYBOARD attached unless
    the CommandReply specifies its own reply_markup (e.g. the inline
    candidate/removal pickers). A callback query's reply always carries
    MAIN_MENU_KEYBOARD (the inline picker that triggered it has
    already served its purpose and isn't re-sent).

    long_poll_timeout: 0 (the default) returns immediately with whatever
    is already waiting -- the original one-shot behavior, still useful
    for a manual check or a test. run_portfolio_b_bot_daemon.py's loop
    passes 30 (Telegram's own recommended long-poll window): the HTTP
    request itself blocks server-side for up to 30s, returning the
    INSTANT a new update arrives rather than after a fixed local sleep.

    One bad/malformed update is isolated in its own try/except and never
    blocks the rest of the batch or crashes the whole poll cycle -- same
    per-item isolation discipline as run_paper_trading.py's own
    per-strategy try/except.

    Returns [(update_description, reply_text), ...] for every update
    actually processed -- for the caller to log.
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

        callback_query = update.get("callback_query")
        if callback_query is not None:
            message = callback_query.get("message") or {}
            if str(message.get("chat", {}).get("id")) != str(chat_id):
                continue   # SECURITY: only the configured chat is ever acted on
            data = callback_query.get("data", "")
            try:
                reply = _handle_callback_query(data, name_fn=name_fn)
            except Exception as e:
                reply = f"Something went wrong processing that: {e}"
            send_telegram_message(reply, bot_token, chat_id, reply_markup=MAIN_MENU_KEYBOARD)
            _answer_callback_query(bot_token, callback_query.get("id", ""))
            processed.append((f"[button] {data}", reply))
            continue

        message = update.get("message")
        if not message or "text" not in message or "chat" not in message:
            continue
        if str(message["chat"].get("id")) != str(chat_id):
            continue   # SECURITY: only the configured chat is ever acted on

        try:
            reply = _handle_command(message["text"], name_fn=name_fn, search_fn=search_fn)
        except Exception as e:
            reply = CommandReply(f"Something went wrong processing that command: {e}")

        if reply is not None:
            send_telegram_message(reply.text, bot_token, chat_id,
                                   reply_markup=reply.reply_markup or MAIN_MENU_KEYBOARD)
            processed.append((message["text"], reply.text))

    if max_update_id > offset:
        pbs.save_telegram_offset(max_update_id)

    return processed


def run_long_polling_loop(bot_token: str, chat_id: str) -> None:
    """
    Runs forever, processing Telegram commands as they arrive -- the
    body of run_portfolio_b_bot_daemon.py's systemd service. Clears the
    "/" command menu once at startup (idempotent -- also re-runs safely
    on every service restart; see delete_bot_commands()'s own docstring
    for why this bot deliberately has none), then loops
    poll_and_process_commands() with long_poll_timeout=30 -- each call
    already blocks server-side until a message arrives or 30s elapses,
    so no additional sleep() between iterations is needed or wanted.

    A single poll's own network error (Telegram briefly unreachable,
    DNS hiccup, ...) is logged and the loop continues on the NEXT
    iteration rather than crashing the whole service -- systemd's
    Restart=always is the outer safety net for anything this inner
    catch doesn't handle.
    """
    delete_bot_commands(bot_token)
    print("Portfolio B Telegram bot: long-polling started.")
    while True:
        try:
            poll_and_process_commands(bot_token, chat_id, long_poll_timeout=30)
        except Exception as e:
            print(f"WARNING: poll cycle failed (non-fatal, retrying): {type(e).__name__}: {e}")
