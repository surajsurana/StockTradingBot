"""
End-to-end test: runs the full pipeline across MULTIPLE symbols at once --
Technical + Fundamental + News -> Research Analyst -> Portfolio Manager --
and prints the final, auditable trade decisions.

This is the first script that shows the whole system acting like a portfolio,
not just one stock in isolation: several symbols are researched independently,
then Portfolio Manager decides which ones actually get capital today, sized
by how confident the Research Analyst was in each one, prioritizing the
strongest convictions when capital is limited.

Run this on your own machine once your Anthropic API key is set in
config/settings.py:

    python test_portfolio_manager.py

Cost note: this calls Claude twice per symbol (once for News Agent, once for
Research Analyst synthesis), so with N symbols expect roughly 2*N small
pay-per-use API calls. No real orders are placed -- this only prints the
decisions Portfolio Manager would make.

Known simplification (documented, not a bug): if more than one active
strategy proposes a signal for the same symbol on the same day, this script
only carries forward the first one found for sizing purposes. Research
Analyst still considers every strategy's signal when forming its verdict --
this simplification only affects which single signal's entry/stop/target
Portfolio Manager sizes against. Fine for now since ma_crossover and
mean_reversion rarely fire on the same symbol on the same day; revisit if
that changes.
"""

from config import settings
from data.fetch_historical import fetch_daily_candles, fetch_nifty
from strategies.market_regime import build_regime_series
from strategies.technical_agent import get_technical_signals, first_available_signal
from fundamentals.fundamental_agent import fetch_fundamentals, check_health
from news.news_agent import analyze_news
from research.research_analyst import analyze_stock
from portfolio.portfolio_manager import allocate, build_decision_log, TradeCandidate
from risk.risk_manager import RiskManager

# Keep this small while testing -- each symbol costs two real Claude calls.
TEST_SYMBOLS = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS"]


def research_symbol(symbol: str, regime_series) -> TradeCandidate:
    """Runs Technical + Fundamental + News + Research Analyst for one symbol."""
    print(f"\nResearching {symbol}...")

    price_history = fetch_daily_candles(symbol, period="2y")
    technical_signals = get_technical_signals(symbol, price_history, regime_series)
    for name, sig in technical_signals.items():
        print(f"   - {name}: {'no signal today' if sig is None else sig.reason}")

    metrics = fetch_fundamentals(symbol)
    fundamentals_result = check_health(symbol, metrics, settings.FUNDAMENTALS_CRITERIA)
    print(f"   Fundamentals passed: {fundamentals_result.passed}")

    news_assessment = analyze_news(symbol, api_key=settings.ANTHROPIC_API_KEY,
                                    max_items=settings.NEWS_MAX_ARTICLES)
    print(f"   News sentiment: {news_assessment.sentiment} ({news_assessment.confidence:.0%})")

    research_result = analyze_stock(
        symbol, technical_signals, fundamentals_result, news_assessment,
        api_key=settings.ANTHROPIC_API_KEY,
    )
    print(f"   Research verdict: {research_result.verdict.upper()} ({research_result.confidence:.0%})")

    candidate_signal = first_available_signal(technical_signals)
    return TradeCandidate(symbol=symbol, signal=candidate_signal, research_assessment=research_result)


def main():
    print("Fetching Nifty regime...")
    nifty = fetch_nifty(period="2y")
    regime_series = build_regime_series(nifty)

    candidates = [research_symbol(symbol, regime_series) for symbol in TEST_SYMBOLS]

    risk_manager = RiskManager(
        capital=settings.STARTING_CAPITAL,
        risk_per_trade_pct=settings.RISK_PER_TRADE_PCT,
        max_open_positions=settings.MAX_OPEN_POSITIONS,
        max_deployed_capital_pct=settings.MAX_DEPLOYED_CAPITAL_PCT,
        daily_loss_circuit_breaker_pct=settings.DAILY_LOSS_CIRCUIT_BREAKER_PCT,
    )

    decisions = allocate(candidates, risk_manager)

    print("\n" + "=" * 60)
    print(build_decision_log(decisions))
    print("=" * 60)


if __name__ == "__main__":
    main()
