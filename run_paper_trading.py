"""
Daily Paper Trading Runner -- the script referenced by
deployment/paper_trading_engine.py's module docstring. Safe to run
manually, or from a scheduled task (Windows Task Scheduler or equivalent)
once per trading day, AFTER market close.

    python run_paper_trading.py --strategy=fifty_two_week_high_momentum
    python run_paper_trading.py --all-due

--all-due iterates every registered strategy currently due per
deployment/scheduler.py's metadata-driven logic (deployment_status is an
active trading status, and -- for End-of-Day strategies -- the market is
currently closed). A single --strategy invocation is ALSO subject to the
same scheduler guard: it refuses to run an End-of-Day strategy while the
market is open, per explicit requirement. After ALL strategies run in
--all-due mode, ONE additional daily summary Telegram message is sent
(in addition to, never replacing, each strategy's own message).

IDEMPOTENT: safe to run more than once for the same day (a repeat call is
a no-op, see deployment/paper_trading_engine.py's run_daily()). Safe to
run from Task Scheduler with no additional duplicate-run protection of
its own.

TELEGRAM: sends ONE separate message per strategy (never combined),
via the shared reporting/telegram_templates.py formatter with mode="PAPER"
-- visually identical to a future LIVE message except the header, per
explicit direction. Each message includes the strategy's permanent
strategy_id and links to its generated reports. Uses
reporting.telegram_notifier.send_telegram_message() unmodified.
Credentials come from deployment.settings's narrow, explicit
TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID re-export (the only thing deployment/
ever reads from config/settings.py).

RECOMMENDED SCHEDULE (not built or enforced by this program -- set up
manually): once per NSE trading day, after 15:30 IST market close, e.g.
16:30 IST to allow for EOD data availability from the data provider.
Example Windows Task Scheduler action:
    Program:   C:\\path\\to\\python.exe
    Arguments: C:\\path\\to\\run_paper_trading.py --all-due
    Trigger:   Daily, 16:30, weekdays only (the scheduler guard makes a
               spurious weekend/holiday trigger harmlessly no-op)

NEVER sends a broker order, NEVER touches execution/ or cio/ -- see
deployment/paper_trading_engine.py's module docstring for the full
isolation/execution-assumption disclosure.
"""

import argparse

from data.fetch_historical import fetch_all
from swing_research.universe import get_swing_universe
from reporting.telegram_notifier import send_telegram_message
from reporting.telegram_templates import format_daily_summary, format_strategy_notification

from deployment.deployment_manager import get_strategy, list_strategies
from deployment.drift_report import generate_drift_report
from deployment.paper_trading_engine import (
    ExecutionRealismConfig, compute_live_metrics, generate_report, load_portfolio, run_daily,
)
from deployment.scheduler import is_due_now, strategies_due_now
from deployment.settings import REPORTS_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from swing_research.research_director import SWING_EXPERIMENTS_DIR

# All five paper-trading strategies are End-of-Day: they need the full
# day's closing bar to compute a signal at all (percentile ranks,
# formation returns), so same_day_close fills -- entering at the very
# close price the signal was just computed from -- are not something a
# live order could ever achieve (the close isn't known until the day is
# over). next_day_open is the realistic execution model: a signal
# detected after today's close is queued and filled at TOMORROW's real
# market open, a price that genuinely doesn't exist yet when the
# decision is made. Switched 2026-08-17 so paper-trading results stop
# overstating what's achievable live -- see deployment/paper_trading_engine.py's
# ExecutionRealismConfig for the mechanism (already built for Amihud's
# research, reused here unmodified). Cost/slippage modeling (ADV
# participation caps, ILLIQ-based cost) deliberately left off for now --
# a separate, later decision, not bundled into this timing fix.
_DEFAULT_EXECUTION_CONFIG = ExecutionRealismConfig(fill_timing="next_day_open")

def _renamed(extra: dict, column_name: str) -> dict:
    """compute_*_percentile_ranks() returns {symbol: Series} where each
    Series' own .name is the SYMBOL (an artifact of slicing a wide-format
    DataFrame column-wise), not the feature name each strategy's
    precompute() looks for. deployment/paper_trading_engine.py's
    df.join(extra_columns[symbol]) uses the Series' .name as the joined
    column's name, so without this rename it silently joins a column named
    after the symbol instead of e.g. "rs_percentile" -- precompute() then
    never finds its expected column, treats the percentile as always NaN,
    and entry_signal_at() can never signal. swing_research/research_director.py
    already does this same rename correctly for every backtest (see its
    own extra_columns_by_symbol construction) -- this mirrors that
    convention for the live paper-trading path. Found and fixed 2026-08-17:
    fifty_two_week_high_momentum/short_term_reversal/minervini_trend_template_filter
    had been running in PAPER_TRADING without this rename since promotion,
    meaning they were structurally unable to ever generate an entry signal."""
    return {symbol: series.rename(column_name) for symbol, series in extra.items()}


_STRATEGY_FACTORIES = {
    "fifty_two_week_high_momentum": {
        "display_name": "52-Week High Momentum",
        "strategy_factory": lambda: __import__(
            "swing_research.strategies.fifty_two_week_high_momentum", fromlist=["FiftyTwoWeekHighMomentumStrategy"]
        ).FiftyTwoWeekHighMomentumStrategy(),
        "compute_extra_columns_fn": lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_52w_high_nearness_percentile_ranks"]
        ).compute_52w_high_nearness_percentile_ranks(data), "nearness_percentile"),
    },
    "short_term_reversal": {
        "display_name": "Short-Term Reversal",
        "strategy_factory": lambda: __import__(
            "swing_research.strategies.short_term_reversal", fromlist=["ShortTermReversalStrategy"]
        ).ShortTermReversalStrategy(),
        "compute_extra_columns_fn": lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_short_term_reversal_percentile_ranks"]
        ).compute_short_term_reversal_percentile_ranks(data), "reversal_percentile"),
    },
    "minervini_trend_template_filter": {
        "display_name": "Minervini Trend Template Filter",
        "strategy_factory": lambda: __import__(
            "swing_research.strategies.minervini_trend_template_filter", fromlist=["MinerviniTrendTemplateFilterStrategy"]
        ).MinerviniTrendTemplateFilterStrategy(),
        "compute_extra_columns_fn": lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_rs_percentile_ranks"]
        ).compute_rs_percentile_ranks(data), "rs_percentile"),
    },
    "cross_sectional_momentum": {
        "display_name": "Cross-Sectional Momentum",
        "strategy_factory": lambda: __import__(
            "swing_research.strategies.cross_sectional_momentum", fromlist=["CrossSectionalMomentumStrategy"]
        ).CrossSectionalMomentumStrategy(),
        "compute_extra_columns_fn": lambda data: _renamed(__import__(
            "swing_research.cross_sectional", fromlist=["compute_momentum_percentile_ranks"]
        ).compute_momentum_percentile_ranks(data), "momentum_percentile"),
    },
    "pead": {
        "display_name": "PEAD (Forward Evidence Experiment)",
        # No historical backtest exists for this strategy -- Research
        # Verdict is NOT_YET_EVALUATED, unchanged. See
        # deployment/pead_forward_engine.py's own module docstring.
        # No strategy_factory/compute_extra_columns_fn -- event-driven,
        # handled entirely by run_pead_daily(), see _run_one() below.
        "is_forward_evidence_experiment": True,
    },
}


def _report_links(strategy_key: str, record, result: dict) -> dict:
    links = {}
    if record.primary_experiment_id:
        links["Experiment"] = record.primary_experiment_id
    links["Daily Report"] = f"deployment/reports/{strategy_key}/{result['as_of_date']}.md"
    links["Drift Report"] = f"deployment/reports/{strategy_key}/DRIFT_REPORT.md"
    return links


def _send_notification(strategy_key: str, record, result: dict) -> None:
    metrics = compute_live_metrics(strategy_key)
    portfolio = load_portfolio(strategy_key)
    daily_pnl = round(sum(x["pnl"] for x in result["new_exits"]), 2)
    open_positions = [{"symbol": sym, **pos} for sym, pos in portfolio["positions"].items()]

    text = format_strategy_notification(
        mode="PAPER", strategy_display_name=record.display_name,
        new_entries=result["new_entries"], new_exits=result["new_exits"], open_positions=open_positions,
        daily_pnl=daily_pnl, total_equity=result["mark_to_market_equity"],
        drawdown_pct=metrics.get("max_drawdown_pct"), win_rate=metrics.get("win_rate"),
        expectancy=metrics.get("expectancy"), strategy_id=record.strategy_id,
        report_links=_report_links(strategy_key, record, result),
    )
    send_telegram_message(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


def _send_error_notification(strategy_key: str, display_name: str, error: Exception) -> None:
    """Best-effort failure alert -- this itself must never raise, or a
    strategy failure could take down the run a second time on the way out."""
    text = (
        f"*PAPER strategy run FAILED -- {display_name}*\n\n"
        f"`{strategy_key}` raised an exception during today's run and was "
        f"skipped. No trades were processed for this strategy today.\n\n"
        f"Other strategies in this run are not affected.\n\n"
        f"```\n{type(error).__name__}: {error}\n```"
    )
    try:
        send_telegram_message(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    except Exception as notify_error:
        print(f"ERROR: also failed to send the failure notification for '{strategy_key}': "
              f"{type(notify_error).__name__}: {notify_error}")


def _run_one(strategy_key: str, force: bool = False) -> dict:
    """Returns a dict describing what happened, or None if skipped or failed --
    used by --all-due to build the end-of-day summary.

    Isolation: everything from data-fetch through the Telegram send is
    wrapped in a single try/except. A failure here (data provider outage,
    a bug in one strategy's precompute/signal logic, even a Telegram API
    error) is caught, reported via its own failure notification, and
    returned as None -- it must never propagate out of this function, so
    that --all-due's loop can keep going and still send the daily summary
    for whichever strategies DID succeed."""
    config = _STRATEGY_FACTORIES[strategy_key]
    record = get_strategy(strategy_key)
    if record is None:
        print(f"ERROR: '{strategy_key}' is not registered in the deployment registry -- "
              f"call deployment.deployment_manager.register_strategy() first.")
        return None

    due, reason = is_due_now(record)
    if not due and not force:
        print(f"Skipping '{strategy_key}': {reason}")
        return None

    try:
        symbols = get_swing_universe()

        if config.get("is_forward_evidence_experiment"):
            # SW-007 PEAD -- event-driven, not a swing_research.base.Strategy
            # factory (see deployment/pead_forward_engine.py's own
            # docstring for why). No compute_extra_columns_fn, no
            # strategy_factory call -- run_pead_daily() owns its own
            # earnings-detection + SUE + entry-injection flow, then
            # reuses run_daily() internally for the exit side only.
            # Unlike the cross-sectional strategies below, it does NOT
            # preload 3y OHLCV for the full universe here -- it is not
            # cross-sectional, so run_pead_daily() fetches price data only
            # for the small set of symbols it actually needs (open
            # positions + today's candidate earnings events), avoiding the
            # memory pressure of holding 3y OHLCV for ~450+ symbols
            # simultaneously with a 450+-symbol earnings-history scan.
            from deployment.pead_forward_engine import run_pead_daily
            print(f"[{strategy_key}] Scanning {len(symbols)} symbol(s) for earnings events...")
            result = run_pead_daily(
                symbols, fetch_ohlcv_fn=lambda syms: fetch_all(syms, period="3y"), force=force,
                execution_config=_DEFAULT_EXECUTION_CONFIG,
            )
        else:
            print(f"[{strategy_key}] Fetching data for {len(symbols)} symbol(s)...")
            data = fetch_all(symbols, period="3y")   # >= 252-day lookback + buffer for any strategy tested so far
            print(f"[{strategy_key}] Data available for {len(data)} symbol(s)")

            strategy = config["strategy_factory"]()
            extra_fn = config.get("compute_extra_columns_fn")

            result = run_daily(
                strategy_key, strategy, fetch_data_fn=lambda: data,
                compute_extra_columns_fn=(lambda d: extra_fn(d)) if extra_fn else None,
                force=force, execution_config=_DEFAULT_EXECUTION_CONFIG,
            )
        print(f"[{strategy_key}] {result}")

        if result["status"] != "processed":
            return None

        report_path = generate_report(strategy_key, config["display_name"], strategy_id=record.strategy_id)
        print(f"[{strategy_key}] Report saved: {report_path}")

        # Drift detection is now automated as part of the daily workflow
        # (added 2026-08-17, per explicit direction -- previously only
        # ever generated by a manual, one-off call). Skipped gracefully
        # (not an error) when a strategy has no primary_experiment_id --
        # e.g. SW-007 PEAD, which has NO historical backtest to compare
        # against at all (Research Verdict NOT_YET_EVALUATED, by design --
        # see swing_research/strategy_library/pead.md). Never allowed to
        # take down the rest of this strategy's run if it fails for any
        # other reason (a missing/corrupt historical experiment file,
        # etc.) -- same isolation discipline as everything else in _run_one().
        if record.primary_experiment_id:
            try:
                drift_path = generate_drift_report(
                    strategy_key, config["display_name"], record.primary_experiment_id,
                    SWING_EXPERIMENTS_DIR, strategy_id=record.strategy_id,
                )
                print(f"[{strategy_key}] Drift report updated: {drift_path}")
            except Exception as e:
                print(f"WARNING: '{strategy_key}' drift report generation failed (non-fatal): "
                      f"{type(e).__name__}: {e}")
        else:
            print(f"[{strategy_key}] No primary_experiment_id set -- drift report skipped "
                  f"(expected for a forward-evidence-only strategy like PEAD).")

        _send_notification(strategy_key, record, result)

        return {"strategy_key": strategy_key, "display_name": record.display_name, "result": result}
    except Exception as e:
        print(f"ERROR: '{strategy_key}' failed during today's paper trading run: {type(e).__name__}: {e}")
        _send_error_notification(strategy_key, config["display_name"], e)
        return None


def _send_daily_summary(run_results: list) -> None:
    strategy_results = [{"display_name": r["display_name"], "new_entries": r["result"]["new_entries"],
                         "new_exits": r["result"]["new_exits"]} for r in run_results]

    closed_trades_today = sum(len(r["result"]["new_exits"]) for r in run_results)
    daily_pnl_total = round(sum(x["pnl"] for r in run_results for x in r["result"]["new_exits"]), 2)
    portfolio_equity_total = round(sum(r["result"]["mark_to_market_equity"] for r in run_results), 2)

    open_positions_total = 0
    total_wins, total_trades = 0, 0
    for r in run_results:
        portfolio = load_portfolio(r["strategy_key"])
        open_positions_total += len(portfolio["positions"])
        metrics = compute_live_metrics(r["strategy_key"])
        n = metrics.get("total_trades", 0)
        if n:
            total_trades += n
            total_wins += round(metrics.get("win_rate", 0.0) * n)
    blended_win_rate = round(total_wins / total_trades, 4) if total_trades else None

    text = format_daily_summary(
        strategy_results=strategy_results, closed_trades_today=closed_trades_today,
        open_positions_total=open_positions_total, daily_pnl_total=daily_pnl_total,
        portfolio_equity_total=portfolio_equity_total, blended_win_rate=blended_win_rate,
    )
    send_telegram_message(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", type=str, choices=list(_STRATEGY_FACTORIES.keys()))
    parser.add_argument("--all-due", action="store_true",
                        help="run every registered, currently-due strategy per deployment/scheduler.py, "
                             "then send one additional daily summary message")
    parser.add_argument("--force", action="store_true", help="bypass the scheduler guard and idempotency check")
    args = parser.parse_args()

    if args.all_due:
        due_records = strategies_due_now(list_strategies())
        due_keys = [r.strategy_key for r in due_records if r.strategy_key in _STRATEGY_FACTORIES]
        if not due_keys:
            print("No registered, runnable strategies are due right now.")
            return
        run_results = []
        for strategy_key in due_keys:
            # Each strategy's own try/except lives inside _run_one() -- a
            # failure there is already caught and reported, so this loop
            # keeps going regardless of what happened to any prior strategy.
            run_result = _run_one(strategy_key, force=args.force)
            if run_result is not None:
                run_results.append(run_result)
        if run_results:
            try:
                _send_daily_summary(run_results)
            except Exception as e:
                print(f"ERROR: failed to send the daily summary: {type(e).__name__}: {e}")
        else:
            print("No strategy actually processed today (all skipped/already done) -- no summary sent.")
    elif args.strategy:
        _run_one(args.strategy, force=args.force)
    else:
        parser.error("Pass either --strategy=<key> or --all-due")


if __name__ == "__main__":
    main()
