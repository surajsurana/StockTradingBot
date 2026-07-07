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
MAX_OPEN_POSITIONS = 5
MAX_DEPLOYED_CAPITAL_PCT = 0.50    # never have more than 50% of capital in the market at once
DAILY_LOSS_CIRCUIT_BREAKER_PCT = 0.03   # stop opening new trades if daily loss exceeds 3%

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
