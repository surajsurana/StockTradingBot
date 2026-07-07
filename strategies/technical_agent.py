"""
Technical Agent -- the chart-based specialist.

Unlike News Agent, Fundamental Agent, Research Analyst, Portfolio Manager,
and Chief Investment AI, this one is rule-based rather than an LLM judgment
call (same as Risk Manager) -- it runs every registered price-chart strategy
(strategies/ma_crossover.py, strategies/mean_reversion.py, ...) against a
symbol's price history and reports back what each one thinks about TODAY
specifically, applying the Nifty market-regime filter to whichever
strategies opt into it (strategy.uses_regime_filter -- see strategies/base.py).

This is the single place that logic lives, so every script that needs
"what does the Technical Agent think about this symbol today" (test scripts,
and eventually main.py's live/daily run) calls the same function instead of
each re-implementing it slightly differently.
"""

from config import settings
from strategies.ma_crossover import MACrossoverStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.market_regime import is_bullish_on

# Every strategy module gets registered here -- this is the Technical Agent's
# full toolkit. Add new strategy classes to this dict as they're built; the
# rest of the system never needs to change.
STRATEGY_REGISTRY = {
    "ma_crossover": MACrossoverStrategy,
    "mean_reversion": MeanReversionStrategy,
}


def get_technical_signals(symbol: str, price_history, regime_series) -> dict:
    """
    Runs every active strategy against price_history and returns today's
    signal for each (or None if that strategy isn't proposing a trade today).
    Applies the market-regime filter only to strategies that opt into it
    (strategy.uses_regime_filter), exactly like the backtest in main.py does.

    Returns: dict of strategy_key -> Signal or None.
    """
    today_date = price_history.index[-1]
    market_is_bullish = is_bullish_on(regime_series, today_date)

    signals = {}
    for strategy_key in settings.ACTIVE_STRATEGIES:
        strategy_cls = STRATEGY_REGISTRY.get(strategy_key)
        if strategy_cls is None:
            continue
        strategy = strategy_cls()

        signal = strategy.generate_signal(price_history)

        if signal is not None:
            signal.symbol = symbol
            if strategy.uses_regime_filter and settings.USE_MARKET_REGIME_FILTER and not market_is_bullish:
                # strategy wanted to fire, but the market-regime filter blocks it today
                signal = None

        signals[strategy_key] = signal

    return signals


def first_available_signal(technical_signals: dict):
    """
    Picks the first non-None signal across strategies for callers (like
    Portfolio Manager) that need one concrete signal to size against.
    Known simplification: if more than one strategy fires for the same
    symbol on the same day, only the first one found is used here -- Research
    Analyst still considers every strategy's signal when forming its verdict,
    this only affects which single signal gets sized. Revisit if simultaneous
    multi-strategy signals on the same symbol become common.
    """
    for signal in technical_signals.values():
        if signal is not None:
            return signal
    return None
