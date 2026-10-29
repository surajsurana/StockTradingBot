"""
Daily Paper Trading Runner -- the script referenced by
deployment/paper_trading_engine.py's module docstring. Safe to run
manually, or from a scheduled task (Windows Task Scheduler or equivalent)
once per trading day, AFTER market close.

    python run_paper_trading.py --strategy=fifty_two_week_high_momentum
    python run_paper_trading.py --all-due
    python run_paper_trading.py --resolve-at-open

--all-due iterates every registered strategy currently due per
deployment/scheduler.py's metadata-driven logic (deployment_status is an
active trading status, and -- for End-of-Day strategies -- the market is
currently closed). A single --strategy invocation is ALSO subject to the
same scheduler guard: it refuses to run an End-of-Day strategy while the
market is open, per explicit requirement. After ALL strategies run in
--all-due mode, ONE additional daily summary Telegram message is sent
(in addition to, never replacing, each strategy's own message).

--resolve-at-open (added 2026-08-18) is a SEPARATE, near-market-open
pass -- it resolves entries/exits queued by a PRIOR day's --all-due run
(fill_timing="next_day_open") against TODAY's real market Open, and
sends an immediate Telegram execution confirmation for anything that
actually fills, instead of waiting until the end-of-day run reports it
hours later. It does NOT detect new signals (End-of-Day strategies need
the full day's close for that) and does NOT touch last_processed_date,
so the SAME day's later --all-due run is completely unaffected and still
required -- these two modes are complementary, not alternatives. See
deployment/paper_trading_engine.py's resolve_pending_fills_at_open() for
the full mechanism.

IDEMPOTENT: safe to run more than once for the same day (a repeat call is
a no-op, see deployment/paper_trading_engine.py's run_daily()). Safe to
run from Task Scheduler with no additional duplicate-run protection of
its own. --resolve-at-open is ALSO safe to run repeatedly or when nothing
is pending (a fast no-op per strategy).

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
manually): TWO separate triggers per NSE trading day.
  1. --resolve-at-open around 9:30 IST -- 15 minutes after the 9:15 IST
     market open, as a buffer for the data provider (yfinance -- an
     unofficial, sometimes-delayed source, same disclosed caveat as
     data/fetch_earnings_calendar.py) to have that morning's Open
     actually populated.
  2. --all-due after 15:30 IST market close, e.g. 16:30 IST to allow for
     EOD data availability.
Example Windows Task Scheduler actions:
    Program:   C:\\path\\to\\python.exe
    Arguments: C:\\path\\to\\run_paper_trading.py --resolve-at-open
    Trigger:   Daily, 09:30, weekdays only

    Program:   C:\\path\\to\\python.exe
    Arguments: C:\\path\\to\\run_paper_trading.py --all-due
    Trigger:   Daily, 16:30, weekdays only (the scheduler guard makes a
               spurious weekend/holiday trigger harmlessly no-op)

NEVER sends a broker order, NEVER touches execution/ or cio/ -- see
deployment/paper_trading_engine.py's module docstring for the full
isolation/execution-assumption disclosure.

POOLS A2 AND A3 (2026-10-19, per explicit direction -- "same pools of 2
lakh and 3 lakhs for india strategies also", the India counterpart of
Pools I2/I3): every Pool A strategy also runs on its own fresh book of
Rs.2,00,000 (deployment/state/pool_a2/<key>/) and Rs.3,00,000
(pool_a3/<key>/). Capital is the ONLY difference -- same Strategy
instance type, universe, data, execution config, fill timing, notional
floor and engine call as Pool A -- with sizing_capital_cap, and the
capital wind-down's target and reduced floor, each scaled to the book's
own capital (FUND ALLOCATION in ARCHITECTURE.md: size is a percentage of
the book's own capital), or positions would never grow past Pool A's.
They run INSIDE this script's own invocations, strategy by strategy, off
the data Pool A has just fetched for that strategy (the VPS has 458 MB of
RAM -- the Nifty universe is never fetched an extra time), so there is
no new cron line: --all-due runs them at 15:35 and --resolve-at-open at
09:30. Silent like Pool F: no report, drift report, Telegram message or
Details cache entry of their own -- Pool A's are untouched. Each pool,
and each strategy within it, fails on its own; a failure there never
reaches Pool A's run or the other pool. Only strategies the registry
has at PAPER_TRADING get books, exactly Pool F's rule.
"""

import argparse
import os
from contextlib import contextmanager
from datetime import date as date_type

from data.fetch_historical import fetch_all
from swing_research.universe import get_swing_universe
from reporting.telegram_notifier import send_telegram_message
from reporting.telegram_templates import format_daily_summary, format_execution_notification, format_strategy_notification

import deployment.capital_winddown as _cwd
import deployment.paper_trading_engine as _pte
from deployment.base import DeploymentStatus, is_pool_a_record
from deployment.capital_winddown import apply_capital_winddown, cumulative_withdrawn_up_to
from deployment.daily_details_store import save_detail
from deployment.deployment_manager import get_strategy, list_strategies
from deployment.drift_report import generate_drift_report
from deployment.paper_trading_engine import (
    ExecutionRealismConfig, compute_live_metrics, generate_report, load_portfolio,
    resolve_pending_fills_at_open, run_daily,
)
from deployment.scheduler import is_due_now, strategies_due_now
from deployment.settings import (POOL_A_CAPITAL_MULTIPLES, PAPER_TRADING_WINDDOWN_REDUCED_FLOOR_WHILE_ABOVE_TARGET,
                                 REPORTS_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_SINGLE_DAILY_SUMMARY,
                                 pool_a_subpool_capital)
from swing_research.research_director import SWING_EXPERIMENTS_DIR
from swing_research.broker_costs import FLOOR_ROUND_UP_TOLERANCE, min_viable_notional
from swing_research.strategy_catalog import PAPER_TRADING_STRATEGY_SPECS

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

# Built from swing_research/strategy_catalog.py (added 2026-08-23, "self-
# registering strategy architecture") -- adding a future cross-sectional
# strategy to paper trading means appending one entry to that catalog, not
# editing this dict directly. PEAD stays hardcoded below exactly as
# before: it is event-driven, not a cross-sectional swing_research.base.Strategy,
# and deliberately does not fit the catalog's shape -- see
# strategy_catalog.py's own module docstring for why it's excluded there.
_STRATEGY_FACTORIES = {
    spec.strategy_key: {
        "display_name": spec.display_name,
        "strategy_factory": spec.strategy_factory,
        **({"compute_extra_columns_fn": spec.compute_extra_columns_fn} if spec.compute_extra_columns_fn else {}),
        # execution_config: per-strategy override (added 2026-09-06, for Overnight Return Anomaly --
        # see strategy_catalog.py's PaperTradingStrategySpec.execution_config_factory docstring).
        # Resolved HERE (not lazily like strategy_factory) since an ExecutionRealismConfig is cheap,
        # immutable config data, not an expensive Strategy instantiation.
        **({"execution_config": spec.execution_config_factory()} if spec.execution_config_factory else {}),
    }
    for spec in PAPER_TRADING_STRATEGY_SPECS
}
_STRATEGY_FACTORIES["pead"] = {
    "display_name": "PEAD (Forward Evidence Experiment)",
    # No historical backtest exists for this strategy -- Research
    # Verdict is NOT_YET_EVALUATED, unchanged. See
    # deployment/pead_forward_engine.py's own module docstring.
    # No strategy_factory/compute_extra_columns_fn -- event-driven,
    # handled entirely by run_pead_daily(), see _run_one() below.
    "is_forward_evidence_experiment": True,
}


# Pools A2 and A3 -- label -> folder beside Pool A's paper_trading/ (see the module docstring).
# Deliberately not "pool_a1": Pool A1 is the unrelated legacy Rs.10,00,000 books in
# paper_trading_legacy/, run by run_pool_a1_legacy.py.
POOL_A_SUBPOOLS = {"Pool A2": "pool_a2", "Pool A3": "pool_a3"}


def _subpools_apply(strategy_key: str) -> bool:
    """Pool F's rule: a book only for a strategy the registry has at PAPER_TRADING, in Pool A's family."""
    record = get_strategy(strategy_key)
    return (record is not None and record.deployment_status == DeploymentStatus.PAPER_TRADING
            and is_pool_a_record(record))


@contextmanager
def _in_subpool(dirname: str):
    """Points the engine, and the wind-down's own log, at Pool A2's or A3's folder -- the sibling of
    wherever Pool A's is -- and ALWAYS puts Pool A's back, error or not, so nothing Pool A does
    afterwards in this process can land in the wrong book."""
    import deployment.pead_forward_engine as pfe
    saved = (_pte.PAPER_TRADING_STATE_DIR, _cwd.PAPER_TRADING_STATE_DIR, pfe.STATE_DIR_OVERRIDE)
    path = os.path.join(os.path.dirname(saved[0]), dirname)
    _pte.PAPER_TRADING_STATE_DIR = _cwd.PAPER_TRADING_STATE_DIR = path
    try:
        yield path
    finally:
        _pte.PAPER_TRADING_STATE_DIR, _cwd.PAPER_TRADING_STATE_DIR, pfe.STATE_DIR_OVERRIDE = saved


def _seed_if_missing(strategy_key: str, capital: float, label: str) -> None:
    """A fresh book at the pool's own capital. The engine's own fresh-book default is Pool A's
    Rs.1,00,000, so without this a new Pool A2/A3 book would simply be another Pool A book."""
    if os.path.exists(_pte._portfolio_path(strategy_key)):
        return
    _pte._save_portfolio(strategy_key, {"cash": capital, "starting_capital": capital, "positions": {},
                                        "last_processed_date": None, "pending_entries": {}, "pending_exits": {}})
    print(f"[{strategy_key}] {label}: new book seeded with Rs.{capital:,.0f}")


def _cached(fn):
    """`fn(data)` worked out at most once, for Pool A and both sub-pools: the same data gives the
    same percentile columns, and ranking the whole universe three times over is wasted time.
    `.clear()` lets the caller drop the result the moment the last pool is done with it."""
    memo = {}

    def cached(data):
        if "v" not in memo:
            memo["v"] = fn(data)
        return memo["v"]
    cached.clear = memo.clear
    return cached


def _shared_fetch(period: str):
    """fetch_all() that never asks twice for a symbol within one run: the first caller's request is
    exactly the call it always made, and a later pool only fetches what that did not cover."""
    cache, asked = {}, set()

    def fetch(symbols):
        missing = [s for s in symbols if s not in asked]
        if missing:
            cache.update(fetch_all(missing, period=period))
            asked.update(missing)
        return {s: cache[s] for s in symbols if s in cache}
    return fetch


def _shared_earnings_events():
    """PEAD's universe-wide earnings scan, run once and handed to every pool."""
    memo = {}

    def fetch(symbols, target_date, lookback_days):
        import deployment.pead_forward_engine as pfe
        k = (tuple(symbols), target_date, lookback_days)
        if k not in memo:
            memo[k] = pfe.fetch_recent_earnings_events_chunked(symbols, target_date, lookback_days=lookback_days)
        return memo[k]
    return fetch


def _run_subpools(strategy_key: str, config: dict, force: bool = False, fetch_data=None, extra_fn=None,
                  symbols=None, pead_ohlcv=None, pead_events=None) -> list:
    """Pools A2 and A3's end-of-day run for one strategy, off what Pool A has just fetched. Never
    raises: each pool fails on its own and is printed to the log, like Pool F."""
    try:
        if not _subpools_apply(strategy_key):
            return []
    except Exception as e:
        print(f"ERROR: Pools A2/A3 '{strategy_key}' registry check failed: {type(e).__name__}: {e}")
        return []
    results = []
    for label, dirname in POOL_A_SUBPOOLS.items():
        capital = pool_a_subpool_capital(dirname)
        try:
            with _in_subpool(dirname) as pool_dir:
                _seed_if_missing(strategy_key, capital, label)
                if config.get("is_forward_evidence_experiment"):
                    from deployment.pead_forward_engine import run_pead_daily
                    result = run_pead_daily(
                        symbols, fetch_ohlcv_fn=pead_ohlcv, force=force, execution_config=_DEFAULT_EXECUTION_CONFIG,
                        state_dir=pool_dir, sizing_capital_cap=capital, fetch_events_fn=pead_events,
                    )
                else:
                    result = run_daily(
                        strategy_key, config["strategy_factory"](), fetch_data_fn=fetch_data,
                        compute_extra_columns_fn=extra_fn,
                        force=force, execution_config=config.get("execution_config", _DEFAULT_EXECUTION_CONFIG),
                        min_position_value_rupees=min_viable_notional(),
                        floor_round_up=FLOOR_ROUND_UP_TOLERANCE,
                        sizing_capital_cap=capital,
                    )
                print(f"[{strategy_key}] {label}: {result}")
                if result["status"] == "processed":
                    # Pool A's own wind-down, at this book's scale: idle cash above the book's own
                    # capital is swept the way Pool A's above Rs.1,00,000 is.
                    winddown = apply_capital_winddown(
                        strategy_key, risk_pct_per_unit=_strategy_instance_for(strategy_key).risk_pct_per_unit,
                        as_of_date=date_type.fromisoformat(result["as_of_date"]),
                        current_equity=result.get("mark_to_market_equity"), target_active_capital=capital,
                        reduced_floor_while_above_target=(PAPER_TRADING_WINDDOWN_REDUCED_FLOOR_WHILE_ABOVE_TARGET
                                                          * POOL_A_CAPITAL_MULTIPLES[dirname]),
                    )
                    if winddown["withdrawn"] > 0:
                        print(f"[{strategy_key}] {label} capital wind-down: withdrew {winddown['withdrawn']}")
                results.append({"pool": label, "result": result})
        except Exception as e:
            print(f"ERROR: {label} '{strategy_key}' failed: {type(e).__name__}: {e}")
    return results


def _resolve_subpools_at_open(strategy_key: str, fetch_open) -> None:
    """Pools A2 and A3's near-market-open pass for one strategy: their own queued fills, against the
    same short window of Open prices Pool A's pass uses (only symbols Pool A did not already ask
    for are fetched). Silent like Pool F, and never raises."""
    try:
        if not _subpools_apply(strategy_key):
            return
    except Exception as e:
        print(f"ERROR: Pools A2/A3 '{strategy_key}' registry check failed at the open: {type(e).__name__}: {e}")
        return
    for label, dirname in POOL_A_SUBPOOLS.items():
        try:
            with _in_subpool(dirname):
                if not os.path.exists(_pte._portfolio_path(strategy_key)):
                    continue   # no book yet, so nothing can be queued
                portfolio = load_portfolio(strategy_key)
                pending = sorted(set(portfolio.get("pending_entries", {})) | set(portfolio.get("pending_exits", {})))
                if not pending:
                    continue
                result = resolve_pending_fills_at_open(
                    strategy_key, _strategy_instance_for(strategy_key), fetch_open_data_fn=lambda p=pending: fetch_open(p),
                    execution_config=_DEFAULT_EXECUTION_CONFIG, sizing_capital_cap=pool_a_subpool_capital(dirname),
                )
                print(f"[{strategy_key}] {label} open: {result}")
        except Exception as e:
            print(f"ERROR: {label} '{strategy_key}' failed at the open: {type(e).__name__}: {e}")


def _report_links(strategy_key: str, record, result: dict) -> dict:
    links = {}
    if record.primary_experiment_id:
        links["Experiment"] = record.primary_experiment_id
    links["Daily Report"] = f"deployment/reports/{strategy_key}/{result['as_of_date']}.md"
    links["Drift Report"] = f"deployment/reports/{strategy_key}/DRIFT_REPORT.md"
    return links


def _strategy_instance_for(strategy_key: str):
    """A Strategy instance for a given key -- purely for its
    risk_pct_per_unit attribute (resolve_pending_fills_at_open() needs it
    to size a resolved entry the same way run_daily() itself would).
    PEAD isn't in _STRATEGY_FACTORIES' strategy_factory (event-driven,
    see that dict's own comment) -- special-cased here, matching
    deployment/pead_forward_engine.py's own PEADStrategy() instantiation."""
    if strategy_key == "pead":
        from swing_research.strategies.pead import PEADStrategy
        return PEADStrategy()
    return _STRATEGY_FACTORIES[strategy_key]["strategy_factory"]()


def _send_execution_notification(strategy_key: str, record, result: dict) -> None:
    text = format_execution_notification(
        mode="PAPER", strategy_display_name=record.display_name,
        new_entries=result["new_entries"], new_exits=result["new_exits"], strategy_id=record.strategy_id,
    )
    if TELEGRAM_SINGLE_DAILY_SUMMARY:
        # Fills are no longer alerted individually (2026-09-10) -- cached for
        # the Details command and visible in the daily pool summary's numbers.
        try:
            save_detail(f"{record.display_name} fills", text)
        except Exception as cache_error:   # a Details-cache hiccup must never fail the run
            print(f"WARNING: could not cache the fill notification: {type(cache_error).__name__}: {cache_error}")
        print(f"[{strategy_key}] fills not sent to Telegram (single daily summary mode): "
              f"{len(result['new_entries'])} entr{'y' if len(result['new_entries']) == 1 else 'ies'}, "
              f"{len(result['new_exits'])} exit{'' if len(result['new_exits']) == 1 else 's'} -- cached for Details.")
        return
    send_telegram_message(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


def _resolve_at_open_one(strategy_key: str) -> None:
    """NEAR-MARKET-OPEN pass (added 2026-08-18, per direct user feedback
    -- "I should be getting a Telegram message at the live time when
    something is bought or sold"). Resolves whatever entries/exits were
    QUEUED by a prior day's EOD run (fill_timing="next_day_open") against
    TODAY's real market Open, and sends an immediate Telegram message if
    anything actually filled -- distinct from, and never a replacement
    for, that same strategy's end-of-day message later, which still
    reports these same fills again alongside the full daily report.

    Deliberately does NOT use deployment.scheduler.is_due_now() -- that
    function REFUSES to run an End-of-Day strategy while the market is
    open, which is exactly when this is meant to run (shortly after
    market open, e.g. 9:30 IST). Isolated in its own try/except (same
    discipline as _run_one()) -- one strategy's failure here must never
    stop the others, and must never affect that same strategy's later
    EOD run (resolve_pending_fills_at_open() never touches
    last_processed_date -- see that function's own docstring).

    Fetches only a SHORT window (not the full 3y EOD pull) for only the
    symbols actually pending -- this only ever needs today's Open, kept
    deliberately lightweight since this runs while the market is open,
    not after close."""
    record = get_strategy(strategy_key)
    if record is None:
        return
    fetch_open = _shared_fetch("5d")   # Pools A2/A3 below reuse whatever this fetches
    _resolve_pool_a_at_open(strategy_key, record, fetch_open)
    _resolve_subpools_at_open(strategy_key, fetch_open)


def _resolve_pool_a_at_open(strategy_key: str, record, fetch_open) -> None:
    """Pool A's own part of _resolve_at_open_one(), exactly as it always ran."""
    try:
        portfolio = load_portfolio(strategy_key)
        pending_symbols = sorted(set(portfolio.get("pending_entries", {})) | set(portfolio.get("pending_exits", {})))
        if not pending_symbols:
            print(f"[{strategy_key}] Nothing pending -- skipping the open-price fetch.")
            return
        print(f"[{strategy_key}] Resolving {len(pending_symbols)} pending fill(s) at today's real Open...")
        strategy = _strategy_instance_for(strategy_key)
        result = resolve_pending_fills_at_open(
            strategy_key, strategy, fetch_open_data_fn=lambda: fetch_open(pending_symbols),
            execution_config=_DEFAULT_EXECUTION_CONFIG,
        )
        print(f"[{strategy_key}] {result}")
        if result["new_entries"] or result["new_exits"]:
            _send_execution_notification(strategy_key, record, result)
    except Exception as e:
        print(f"ERROR: '{strategy_key}' failed during the market-open resolution pass: {type(e).__name__}: {e}")


def _send_notification(strategy_key: str, record, result: dict) -> None:
    """
    CACHES this strategy's full daily report instead of sending it to
    Telegram directly (changed 2026-09-02, per explicit direction --
    "for so many strategy im getting so many telegram messages... I
    only need summary message and if I ask I can get more details").
    Was previously one full Telegram message PER STRATEGY, every single
    day, whether or not anything happened -- on top of the combined
    format_daily_summary() message _run_all_due() already sends at the
    end (still sent, unchanged, below). The cached text is available on
    request via portfolio_b/telegram_bot.py's Details button/command
    (deployment/daily_details_store.py's load_details()).

    _send_execution_notification() (the near-market-open "something
    actually filled" alert) and _send_error_notification() (a real
    failure) are UNCHANGED -- both are already conditional/rare, exactly
    the kind of message worth an immediate Telegram send, unlike this
    one which fired unconditionally every day.
    """
    metrics = compute_live_metrics(strategy_key)

    text = format_strategy_notification(
        mode="PAPER", strategy_display_name=record.display_name,
        new_entries=result["new_entries"], new_exits=result["new_exits"],
        open_positions=result.get("open_positions_detail", []),
        pending_entries=result.get("new_pending_entries", []),
        pending_exits=result.get("new_pending_exits", []),
        daily_pnl=result.get("daily_pnl"), total_equity=result["mark_to_market_equity"],
        drawdown_pct=metrics.get("max_drawdown_pct"), win_rate=metrics.get("win_rate"),
        expectancy=metrics.get("expectancy"), strategy_id=record.strategy_id,
        report_links=_report_links(strategy_key, record, result),
    )
    save_detail(record.display_name, text)


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
    if TELEGRAM_SINGLE_DAILY_SUMMARY:
        # Surfaces in the daily pool summary as "Not updated today: <strategy>".
        print(text)
        return
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
            # Shared with Pools A2/A3 below: one earnings scan, and each symbol's prices fetched once.
            pead_ohlcv, pead_events = _shared_fetch("3y"), _shared_earnings_events()
            try:
                result = run_pead_daily(
                    symbols, fetch_ohlcv_fn=pead_ohlcv, force=force,
                    execution_config=_DEFAULT_EXECUTION_CONFIG, fetch_events_fn=pead_events,
                )
            finally:
                # Pools A2/A3 run whatever happened to Pool A's own call -- they never raise.
                _run_subpools(strategy_key, config, force=force, symbols=symbols,
                              pead_ohlcv=pead_ohlcv, pead_events=pead_events)
        else:
            print(f"[{strategy_key}] Fetching data for {len(symbols)} symbol(s)...")
            data = fetch_all(symbols, period="3y")   # >= 252-day lookback + buffer for any strategy tested so far
            print(f"[{strategy_key}] Data available for {len(data)} symbol(s)")

            strategy = config["strategy_factory"]()
            extra_fn = config.get("compute_extra_columns_fn")
            # The same data and the same percentile columns for Pool A and Pools A2/A3 (computed once).
            fetch_data = lambda: data   # noqa: E731
            shared_extra = _cached(extra_fn) if extra_fn else None

            try:
                result = run_daily(
                    strategy_key, strategy, fetch_data_fn=fetch_data,
                    compute_extra_columns_fn=shared_extra,
                    force=force, execution_config=config.get("execution_config", _DEFAULT_EXECUTION_CONFIG),
                # A position too small to carry its own flat charges is skipped. The DP charge is
                # Rs13.5 per scrip whatever the trade is worth, so the Rs3,532 median position these
                # books were taking paid 0.45% in DP alone and 0.68% all in -- against the 0.2% the
                # research that approved them assumed. Derived from the charge model, not chosen; see
                # swing_research/broker_costs.py. Crypto and US pools pass their own value.
                    min_position_value_rupees=min_viable_notional(),
                    floor_round_up=FLOOR_ROUND_UP_TOLERANCE,
                )
            finally:
                # Pools A2/A3 run whatever happened to Pool A's own call -- they never raise.
                _run_subpools(strategy_key, config, force=force, fetch_data=fetch_data, extra_fn=shared_extra)
                if shared_extra:
                    shared_extra.clear()   # the percentile columns are not needed past this point
        print(f"[{strategy_key}] {result}")

        if result["status"] != "processed":
            return None

        # Capital wind-down (added 2026-08-23, per explicit direction) --
        # gradually brings this strategy's own idle cash down toward a
        # target, WITHOUT ever touching capital reserved for currently-
        # queued next-session entries or force-selling anything. Runs
        # strictly AFTER the run_daily()/run_pead_daily() call above has
        # fully completed -- exits processed, new signals detected and
        # queued into pending_entries -- so the reservation calculation
        # below sees the FINAL, complete pending_entries for today. Runs
        # BEFORE the report/Telegram message below, so both reflect the
        # accurate, current cash figure. Isolated in its own try/except,
        # same discipline as drift-report generation below -- a wind-down
        # failure must never take down the rest of this strategy's run.
        # current_equity=result["mark_to_market_equity"] (added 2026-09-01,
        # per explicit direction): lets wind-down skim toward a much
        # lower cash floor while this strategy's total equity is still
        # above target, even on days its cash alone has already fallen
        # below target (the common case once most of the pool is
        # deployed into open positions) -- see apply_capital_winddown()'s
        # own current_equity docstring.
        # See deployment/capital_winddown.py for the full design.
        try:
            winddown = apply_capital_winddown(
                strategy_key, risk_pct_per_unit=_strategy_instance_for(strategy_key).risk_pct_per_unit,
                as_of_date=date_type.fromisoformat(result["as_of_date"]),
                current_equity=result.get("mark_to_market_equity"),
            )
            if winddown["withdrawn"] > 0:
                print(f"[{strategy_key}] Capital wind-down: withdrew {winddown['withdrawn']} "
                      f"(reserved {winddown['reserved']} for queued entries, cash now {winddown['remaining_cash']})")
        except Exception as e:
            print(f"WARNING: '{strategy_key}' capital wind-down failed (non-fatal): {type(e).__name__}: {e}")

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
    # "Signals Today" must reflect NEWLY QUEUED signals too, not just
    # actual fills -- under fill_timing="next_day_open" (the default for
    # every strategy), a signal detected during this EOD run is queued
    # for tomorrow's open, so new_entries/new_exits (ACTUAL fills) is
    # routinely empty here even on a day every strategy found a genuine
    # setup; the fills already happened via that morning's --resolve-at-open
    # call, or the newly-detected ones will fill tomorrow morning. Found
    # 2026-08-19 -- this previously showed "No Setup" for every strategy
    # on days that genuinely had signals.
    strategy_results = [{
        "display_name": r["display_name"],
        "new_entries": r["result"]["new_entries"] + r["result"].get("new_pending_entries", []),
        "new_exits": r["result"]["new_exits"] + r["result"].get("new_pending_exits", []),
    } for r in run_results]

    closed_trades_today = sum(len(r["result"]["new_exits"]) for r in run_results)
    booked_pnl_today = round(sum(x["pnl"] for r in run_results for x in r["result"]["new_exits"]), 2)
    portfolio_equity_total = round(sum(r["result"]["mark_to_market_equity"] for r in run_results), 2)

    # Sum of each strategy's OWN daily_pnl (today's mark-to-market equity
    # change, realized AND unrealized -- see run_daily()'s daily_pnl) --
    # None per-strategy on its first-ever recorded day, so those are
    # excluded from the sum rather than poisoning the whole total; if NO
    # strategy has a prior day yet, the total itself is None ("n/a").
    daily_pnls = [r["result"]["daily_pnl"] for r in run_results if r["result"].get("daily_pnl") is not None]
    daily_pnl_total = round(sum(daily_pnls), 2) if daily_pnls else None

    # Total P&L (cumulative, since each strategy's paper trading began) =
    # current equity minus starting capital -- one figure that already
    # combines everything booked historically AND today's unrealized
    # movement, per direct user feedback that a separate "unrealized"
    # figure was confusing jargon.
    #
    # WIND-DOWN-AWARE (fixed 2026-09-03, per direct user report -- Total
    # P&L was showing a huge, confusing negative number): a strategy's
    # cash withdrawn by deployment/capital_winddown.py is a deliberate
    # capital-management action, not a trading loss -- capital_winddown.py's
    # own module docstring already documents this exact "add back the
    # cumulative amount withdrawn" treatment for compute_live_metrics()'s
    # CAGR/Sharpe and run_daily()'s own daily_pnl, but this SEPARATE
    # calculation was never updated to match, so a heavily wound-down
    # strategy (e.g. SW-007 PEAD, wound from Rs.10,00,000 down to its
    # Rs.1,00,000 floor) made its own withdrawn capital look like a real
    # ~Rs.9,00,000 loss here, dragging the whole portfolio's total deeply
    # (and wrongly) negative. cumulative_withdrawn_up_to() is the exact
    # same existing, already-tested helper those other two call sites use.
    total_starting_capital = sum(load_portfolio(r["strategy_key"])["starting_capital"] for r in run_results)
    total_withdrawn = sum(cumulative_withdrawn_up_to(r["strategy_key"], date_type.today()) for r in run_results)
    total_pnl = round(portfolio_equity_total - total_starting_capital + total_withdrawn, 2)

    open_positions_total = 0
    cash_total = 0.0
    total_wins, total_trades = 0, 0
    for r in run_results:
        portfolio = load_portfolio(r["strategy_key"])
        open_positions_total += len(portfolio["positions"])
        cash_total += portfolio["cash"]
        metrics = compute_live_metrics(r["strategy_key"])
        n = metrics.get("total_trades", 0)
        if n:
            total_trades += n
            total_wins += round(metrics.get("win_rate", 0.0) * n)
    blended_win_rate = round(total_wins / total_trades, 4) if total_trades else None

    # Invested Amount = Portfolio Equity minus uninvested cash -- how much
    # capital is actually deployed into open positions right now, across
    # every strategy that ran today. Added 2026-08-26, per direct
    # feedback that Portfolio Equity alone doesn't say how much of it is
    # sitting in cash vs. actually at work.
    invested_amount_total = round(portfolio_equity_total - cash_total, 2)

    text = format_daily_summary(
        strategy_results=strategy_results, closed_trades_today=closed_trades_today,
        open_positions_total=open_positions_total, daily_pnl_total=daily_pnl_total,
        portfolio_equity_total=portfolio_equity_total, invested_amount_total=invested_amount_total,
        blended_win_rate=blended_win_rate,
        booked_pnl_today=booked_pnl_today, total_pnl=total_pnl,
    )
    if TELEGRAM_SINGLE_DAILY_SUMMARY:
        # Superseded by send_daily_pool_summary.py's all-pools message (2026-09-10).
        try:
            save_detail("Pool A summary", text)
        except Exception as cache_error:   # a Details-cache hiccup must never fail the run
            print(f"WARNING: could not cache the Pool A summary: {type(cache_error).__name__}: {cache_error}")
        print("Pool A summary not sent to Telegram (single daily summary mode) -- cached for Details.")
        return
    send_telegram_message(text, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", type=str, choices=list(_STRATEGY_FACTORIES.keys()))
    parser.add_argument("--all-due", action="store_true",
                        help="run every registered, currently-due strategy per deployment/scheduler.py, "
                             "then send one additional daily summary message")
    parser.add_argument("--force", action="store_true", help="bypass the scheduler guard and idempotency check")
    parser.add_argument("--resolve-at-open", action="store_true",
                        help="near-market-open pass (e.g. run via cron ~9:30 IST): resolves any entries/exits "
                             "queued by a prior EOD run against today's real market Open, and sends an immediate "
                             "Telegram message for anything that actually filled. Does NOT detect new signals "
                             "and does NOT touch last_processed_date -- the later --all-due/EOD run is unaffected "
                             "and still required. Safe to run even if nothing is pending (a fast no-op).")
    args = parser.parse_args()

    if args.resolve_at_open:
        active_keys = [r.strategy_key for r in list_strategies()
                       if r.strategy_key in _STRATEGY_FACTORIES
                       and r.deployment_status in (DeploymentStatus.PAPER_TRADING, DeploymentStatus.PILOT_LIVE,
                                                     DeploymentStatus.PRODUCTION)]
        for strategy_key in active_keys:
            _resolve_at_open_one(strategy_key)
    elif args.all_due:
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
        parser.error("Pass --strategy=<key>, --all-due, or --resolve-at-open")


if __name__ == "__main__":
    main()
