"""
Template for config/settings.py -- copy this file to config/settings.py and
fill in your own real values there. config/settings.py is git-ignored (see
.gitignore) so your API keys, secrets, and tokens never get committed or
pushed to GitHub. This template file IS committed, so anyone who clones the
repo (including you, on a new machine) knows exactly what to fill in.

    cp config/settings.example.py config/settings.py      (macOS/Linux)
    copy config\\settings.example.py config\\settings.py    (Windows)
"""

# --- Mode toggle ---
# This is the single switch between paper trading and real money. Never flip
# this to True until: (1) NSE shows in your Kite profile's exchanges list,
# (2) you've reviewed backtest results, (3) you've tested with a tiny position.
LIVE_TRADING = False

# --- Zerodha Kite Connect ---
KITE_API_KEY = ""          # from developers.kite.trade
KITE_API_SECRET = ""       # from developers.kite.trade -- never commit this
KITE_ACCESS_TOKEN = ""     # regenerated daily -- run refresh_kite_token.py each morning
# A separate, paid Kite Connect "Connect"-type app (distinct from the free
# "Personal" app above) -- used ONLY for market data (live quotes,
# historical candles), never for order placement. Kept deliberately apart
# from the free app's credentials so the live trading bot's real orders
# never depend on this paid subscription staying active.
KITE_MARKET_DATA_API_KEY = ""
KITE_MARKET_DATA_API_SECRET = ""

# --- Automated login (auth/kite_auto_login.py) ---
# Lets the bot log itself in every morning with no manual step. Security
# tradeoff accepted consciously: your actual Kite password + TOTP secret are
# stored here, in this git-ignored file. Never share this file or commit it.
# KITE_TOTP_SECRET is the base32 secret from when you first set up 2FA (the
# same one an authenticator app would be seeded with) -- NOT a 6-digit code,
# since those expire every 30 seconds. Leave these blank to keep using the
# semi-manual refresh_kite_token.py flow instead.
KITE_USER_ID = ""       # your Kite login ID (e.g. "AB1234")
KITE_PASSWORD = ""      # your Kite login password
KITE_TOTP_SECRET = ""   # base32 TOTP secret, not a 6-digit code

# --- Default universe: symbols traded if a strategy doesn't specify its own ---
SYMBOLS = [
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
]

# --- Active strategies ---
# Each string maps to a strategy class in strategies/. Add more here as new
# strategy modules are built -- nothing else in the codebase needs to change.
ACTIVE_STRATEGIES = [
    "ma_crossover",
    "mean_reversion",
]

# --- Per-strategy universes ---
# Not every strategy needs to trade the same stocks. A trend-following
# strategy (ma_crossover) wants stocks that trend cleanly; a mean-reversion
# strategy wants stocks that oscillate in a range.
#
# If a strategy key isn't listed here, it falls back to the default SYMBOLS
# list above. Note: once USE_NIFTY500_UNIVERSE is True, run_daily.py scans
# the broader Nifty 500 list instead of these -- these stay as the fallback
# used by main.py's backtest and the smaller test_*.py scripts.
STRATEGY_SYMBOLS = {
    "ma_crossover": [
        "RELIANCE.NS",
        "TCS.NS",
        "HDFCBANK.NS",
        "INFY.NS",
    ],
    "mean_reversion": [
        "RELIANCE.NS",
        "TCS.NS",
        "HDFCBANK.NS",
        "INFY.NS",
        "ICICIBANK.NS",
    ],
}

# --- Risk limits ---
STARTING_CAPITAL = 100000          # paper capital for backtests; replace with real capital later
RISK_PER_TRADE_PCT = 0.01          # risk 1% of capital per trade (entry-to-stop distance)
MAX_OPEN_POSITIONS = 10
MAX_DEPLOYED_CAPITAL_PCT = 0.60    # never have more than 60% of capital in the market at once
MAX_CAPITAL_PER_TRADE_PCT = 0.12   # explicit -- kept at 12% even though 60%/10 slots would default to 6%,
                                    # so a single high-confidence trade can still use a meaningful chunk of
                                    # capital; 10 slots mainly gives room for smaller/lower-confidence trades
                                    # to also get taken rather than guaranteeing 10 similarly-sized positions
DAILY_LOSS_CIRCUIT_BREAKER_PCT = 0.03   # stop opening new trades if daily loss exceeds 3%
STOP_LOSS_COOLDOWN_DAYS = 3        # trading days to wait before re-entering a symbol closed at a loss
TRAILING_STOP_ACTIVATION_FRACTION = 0.8   # arm the trailing stop once 80% of the way from entry to target
TRAILING_STOP_LOCK_IN_FRACTION = 0.7      # once armed, lock in 70% of the gain made so far
USE_PARTIAL_PROFIT_BOOKING = False  # OFF -- backtested (91 symbols, 3mo) against the already-deployed
                                     # trailing stop and every activation/booking/extension combination
                                     # tried underperformed just leaving the trailing stop alone (best case
                                     # +Rs.4,929 vs the trailing-stop-only baseline's +Rs.5,473). Code is
                                     # built and tested; re-enable only after finding a config that actually
                                     # beats the baseline, not just raises the win rate.
PARTIAL_PROFIT_ACTIVATION_FRACTION = 0.6   # book partial profit once 60% of the way to original target --
                                            # earlier than the trailing stop's 80%, so it has a real chance
                                            # of acting before the original GTT's target leg could fire on
                                            # its own between our periodic checks
PARTIAL_PROFIT_BOOKING_FRACTION = 0.5      # fraction of shares to sell outright when triggered
PARTIAL_PROFIT_TARGET_EXTENSION_MULTIPLE = 1.0   # runner tranche's new target extends the original
                                                  # entry-to-target distance by this multiple (1.0 = doubles it)

# --- Market-regime filter ---
USE_MARKET_REGIME_FILTER = True    # validated in backtest: improves win rate, P&L, and drawdown

# --- Fundamentals health-check filter ---
# Only financially healthy companies are allowed into the tradable universe,
# regardless of what any strategy's price chart says. See
# fundamentals/fundamental_agent.py for exactly how each check works and why
# missing data fails the check (conservative default).
USE_FUNDAMENTALS_FILTER = True
FUNDAMENTALS_CRITERIA = {
    "max_debt_to_equity": 150,     # yfinance reports this roughly as a percentage-like number
    "min_roe": 0.10,               # require at least 10% return on equity
    "min_revenue_growth": -0.10,   # allow mild revenue decline, not a crash (-10% or worse fails)
}

# --- News Agent ---
# The first genuinely "AI judgment" agent -- reads real headlines and forms
# a bullish/bearish/neutral opinion via Claude, rather than following a fixed
# rule. Needs your own Anthropic API key (from console.anthropic.com) -- this
# is separate from whatever Claude interface you're using to build this
# project. Each call costs a small, pay-per-use amount.
USE_NEWS_AGENT = True    # keep off until you've added your own ANTHROPIC_API_KEY below
ANTHROPIC_API_KEY = ""   # from console.anthropic.com -- never commit this
NEWS_MODEL = "claude-sonnet-5"
NEWS_MAX_ARTICLES = 8

# --- Macro Strategist ---
# Runs several times per day (not per-stock), before each scan -- reads
# general market/world headlines (Indian financial + BBC/Al Jazeera/CNN/
# Times of India for global/geopolitical coverage) and can throttle or
# skip new entries for the day on elevated/high geopolitical or macro
# risk. See macro/macro_strategist.py.
USE_MACRO_STRATEGIST = True
MACRO_MAX_ARTICLES = 28   # 7 sources interleaved -- was 20 for 3 sources

# --- Reporting ---
# Telegram was chosen over WhatsApp -- free, no Twilio/Meta approval process,
# messages arrive instantly on your phone the same way WhatsApp would have.
# See reporting/telegram_notifier.py for the two-minute setup steps.
TELEGRAM_BOT_TOKEN = ""   # from @BotFather -- never commit this
TELEGRAM_CHAT_ID = ""    # run reporting/telegram_notifier.py after setting the token above to find this
REPORT_SCHEDULE = ["daily", "weekly", "monthly", "quarterly"]

# --- Execution ---
# Kite rejects plain MARKET orders via API unless "market protection" is set
# up on the account (confirmed via test_live_order.py's live testing) -- so
# live orders use LIMIT orders priced this far through the market instead
# (above signal.entry_price for BUY, below for SELL), which fills the same
# way MARKET would for a liquid, small-quantity trade. Widen this if live
# orders aren't filling (price moved further than expected before the order
# reached the exchange); narrow it if you want tighter price control.
LIMIT_ORDER_BUFFER_PCT = 0.015

# --- Universe ---
# Once you're ready to scan beyond the small hand-picked watchlist above,
# run_daily.py uses this broader Nifty 500 snapshot instead of SYMBOLS/
# STRATEGY_SYMBOLS. See data/nifty500_universe.py.
USE_NIFTY500_UNIVERSE = True

# --- Research Lab (research_lab/) -- intraday strategy research only ---
# Nothing here is read by run_daily.py/monitor_positions.py/the real
# risk.risk_manager.RiskManager -- the swing strategy is unaware these
# settings exist and continues to assume 100% capital exactly as before.
# INTRADAY_CAPITAL_ALLOCATION_PCT stays 0 until a research_lab strategy is
# explicitly promoted to production in a future, separately-approved phase.
RESEARCH_LAB_VIRTUAL_CAPITAL = 100000   # never real money in this phase
SWING_CAPITAL_ALLOCATION_PCT = 100      # unchanged default -- swing still assumes 100%
INTRADAY_CAPITAL_ALLOCATION_PCT = 0     # 0 until a strategy is approved for production
