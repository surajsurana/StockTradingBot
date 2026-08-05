"""
Live vs. Backtest Drift Report -- compares a strategy's LIVE paper-trading
statistics (deployment/paper_trading_engine.py) against its VALIDATED
historical research expectations (the base-run experiment saved under
swing_research/experiments/, read-only), so the CIO Research Review can
monitor whether live behavior is tracking the research that justified
paper trading in the first place.

Purely a comparison/reporting tool -- never changes a strategy's recorded
Research Verdict (that stays frozen, exactly as swing_research/'s
acceptance framework produced it) and never changes Deployment Status.
Large drift is a FINDING to surface to a human via the CIO Research
Review, never an automatic trigger for anything.

Read-only: imports research_lab.experiment_manager.load_experiment() to
read a past experiment's saved metrics.json, and
deployment.paper_trading_engine.compute_live_metrics() for the live side.
Never writes to swing_research/experiments/.
"""

import json
import os
from datetime import datetime
from typing import Optional

from research_lab.experiment_manager import load_experiment

from deployment.paper_trading_engine import compute_live_metrics, load_portfolio
from deployment.settings import REPORTS_DIR

# Metrics compared, and the relative-difference threshold (fraction of the
# historical value) past which a metric is flagged as MATERIAL drift.
# Deliberately generous (50%) given live paper-trading samples are, by
# construction, far smaller than the multi-year historical backtest they're
# compared against -- a small sample naturally has more relative noise;
# this threshold is meant to catch a genuine behavioral change, not normal
# small-sample variance. win_rate and max_drawdown_pct use an ABSOLUTE
# (percentage-point) threshold instead, since a relative-difference
# comparison is not meaningful near zero for those two.
_RELATIVE_DRIFT_THRESHOLD = 0.50
_ABSOLUTE_DRIFT_THRESHOLD_PCT = 20.0

_COMPARED_METRICS = ("win_rate", "expectancy", "cagr", "sharpe_ratio", "max_drawdown_pct",
                     "avg_holding_period_days")
_ABSOLUTE_METRICS = {"win_rate", "max_drawdown_pct"}

# trade_frequency (trades per elapsed trading day so far) is compared
# separately from _COMPARED_METRICS -- it has no equivalent field in a
# compute_metrics()-shaped dict on the historical side (the historical
# experiment's own trade_frequency is derived here, not stored under that
# name in metrics.json), so it's handled by its own small helper below
# rather than folded into the generic per-metric loop.
_TRADE_FREQUENCY_RELATIVE_THRESHOLD = 0.50


def _compute_trade_frequency(total_trades: int, trading_calendar_days: Optional[int]) -> Optional[float]:
    if not trading_calendar_days or trading_calendar_days <= 0:
        return None
    return round(total_trades / trading_calendar_days, 4)


def _flag_drift(metric: str, historical_value, live_value) -> Optional[str]:
    if historical_value is None or live_value is None:
        return None
    if metric in _ABSOLUTE_METRICS:
        diff = abs(live_value - historical_value)
        threshold = _ABSOLUTE_DRIFT_THRESHOLD_PCT / 100 if metric == "win_rate" else _ABSOLUTE_DRIFT_THRESHOLD_PCT
        if diff > threshold:
            return f"MATERIAL: historical {historical_value}, live {live_value} (absolute diff {round(diff, 4)})"
        return None
    if historical_value == 0:
        return None
    relative_diff = abs(live_value - historical_value) / abs(historical_value)
    if relative_diff > _RELATIVE_DRIFT_THRESHOLD:
        return (f"MATERIAL: historical {historical_value}, live {live_value} "
                f"(relative diff {round(relative_diff * 100, 1)}%)")
    return None


def _historical_trading_calendar_days(historical_experiment: dict) -> Optional[int]:
    data_period = historical_experiment.get("parameters", {}).get("data_period", "")
    if " to " not in data_period:
        return None
    start_str, end_str = data_period.split(" to ")
    try:
        start = datetime.fromisoformat(start_str.strip()).date()
        end = datetime.fromisoformat(end_str.strip()).date()
    except ValueError:
        return None
    return (end - start).days


def compute_drift(strategy_key: str, historical_exp_id: str,
                   historical_experiments_dir: str) -> dict:
    """
    Returns {"historical": {...}, "live": {...}, "flags": {metric: reason}}.
    `flags` only contains metrics that crossed the drift threshold --
    empty dict means no material drift detected on any compared metric.
    """
    historical_experiment = load_experiment(historical_exp_id, historical_experiments_dir)
    historical_metrics = historical_experiment["metrics"]
    live_metrics = compute_live_metrics(strategy_key)

    flags = {}
    for metric in _COMPARED_METRICS:
        reason = _flag_drift(metric, historical_metrics.get(metric), live_metrics.get(metric))
        if reason:
            flags[metric] = reason

    historical_days = _historical_trading_calendar_days(historical_experiment)
    historical_trade_frequency = (_compute_trade_frequency(historical_metrics.get("total_trades", 0),
                                                             historical_days)
                                   if historical_days else None)
    live_calendar_days = _live_trading_calendar_days(strategy_key)
    live_trade_frequency = (_compute_trade_frequency(live_metrics.get("total_trades", 0), live_calendar_days)
                             if live_calendar_days else None)

    trade_frequency_flag = None
    if historical_trade_frequency and live_trade_frequency is not None:
        relative_diff = abs(live_trade_frequency - historical_trade_frequency) / historical_trade_frequency
        if relative_diff > _TRADE_FREQUENCY_RELATIVE_THRESHOLD:
            trade_frequency_flag = (f"MATERIAL: historical {historical_trade_frequency}/day, "
                                    f"live {live_trade_frequency}/day "
                                    f"(relative diff {round(relative_diff * 100, 1)}%)")
            flags["trade_frequency"] = trade_frequency_flag

    return {
        "historical": {**{m: historical_metrics.get(m) for m in _COMPARED_METRICS},
                      "trade_frequency": historical_trade_frequency},
        "live": {**{m: live_metrics.get(m) for m in _COMPARED_METRICS},
                "trade_frequency": live_trade_frequency},
        "live_total_trades": live_metrics.get("total_trades", 0), "flags": flags,
    }


def _live_trading_calendar_days(strategy_key: str) -> Optional[int]:
    from deployment.paper_trading_engine import _load_daily_equity
    daily_equity = _load_daily_equity(strategy_key)
    return len(daily_equity) if daily_equity else None


def _drift_history_path(strategy_key: str, output_dir: str) -> str:
    return os.path.join(output_dir, strategy_key, "drift_history.jsonl")


def load_drift_history(strategy_key: str, output_dir: str = REPORTS_DIR) -> list:
    """Every snapshot ever appended for this strategy -- 'grows automatically
    over time' per explicit requirement, never overwritten. The latest
    entry is what lifecycle_review.py surfaces in the monthly CIO Research
    Review; the full history stays available for trend analysis."""
    path = _drift_history_path(strategy_key, output_dir)
    if not os.path.exists(path):
        return []
    history = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                history.append(json.loads(line))
    return history


def generate_drift_report(strategy_key: str, display_name: str, historical_exp_id: str,
                           historical_experiments_dir: str, output_dir: str = REPORTS_DIR,
                           strategy_id: str = "") -> str:
    drift = compute_drift(strategy_key, historical_exp_id, historical_experiments_dir)
    generated_at = datetime.now().isoformat()

    all_metrics = list(_COMPARED_METRICS) + ["trade_frequency"]
    lines = [
        f"# Live vs. Backtest Drift Report -- {display_name}" + (f" ({strategy_id})" if strategy_id else ""), "",
        f"Generated: {generated_at}",
        f"Compared against historical experiment: {historical_exp_id}",
        f"Live trades so far: {drift['live_total_trades']}", "",
        "## Comparison", "",
        "| Metric | Historical (research) | Live (paper trading) | Drift |",
        "|---|---|---|---|",
    ]
    for metric in all_metrics:
        flag = drift["flags"].get(metric, "within threshold")
        lines.append(f"| {metric} | {drift['historical'][metric]} | {drift['live'][metric]} | {flag} |")

    lines += ["", "## Note",
              "Live paper-trading samples are, by construction, far smaller than the multi-year "
              "historical backtest -- treat any single-metric flag as a prompt for closer review "
              "(via the CIO Research Review), not as an automatic verdict change. This report never "
              "modifies the strategy's recorded Research Verdict or Deployment Status.",
              "", f"Full growing history: {_drift_history_path(strategy_key, output_dir)}"]

    text = "\n".join(lines)
    report_dir = os.path.join(output_dir, strategy_key)
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, "DRIFT_REPORT.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    # Append (never overwrite) a timestamped snapshot -- this is what
    # makes the drift report "grow automatically over time," per explicit
    # requirement, distinct from the always-latest-only markdown snapshot
    # above.
    history_path = _drift_history_path(strategy_key, output_dir)
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "generated_at": generated_at, "historical_exp_id": historical_exp_id,
            "historical": drift["historical"], "live": drift["live"],
            "live_total_trades": drift["live_total_trades"], "flags": drift["flags"],
        }) + "\n")
    return path
