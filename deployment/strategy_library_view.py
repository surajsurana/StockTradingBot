"""
Unified Strategy Library (Phase 4) -- one document aggregating every
strategy's ResearchVerdict, DeploymentStatus, evidence quality, and
performance across whichever stages it has reached (historical,
recent-period, live paper-trading, live production), pulling from the
deployment registry (deployment_manager.py) plus each strategy's own
paper-trading state (paper_trading_engine.py) where applicable.

Read-only aggregation -- writes nothing back to swing_research/'s
experiment records, the Knowledge Base, or the deployment registry
itself. Regenerable at any time from current state.
"""

import os
from datetime import datetime

from deployment.base import DeploymentStatus
from deployment.deployment_manager import list_strategies
from deployment.paper_trading_engine import compute_live_metrics, load_portfolio
from deployment.settings import DEPLOYMENT_DIR

UNIFIED_LIBRARY_PATH = os.path.join(DEPLOYMENT_DIR, "UNIFIED_STRATEGY_LIBRARY.md")


def _format_metrics_line(metrics: dict) -> str:
    if not metrics or metrics.get("total_trades", 0) == 0:
        return "(no trades recorded yet)"
    return (f"trades={metrics['total_trades']} win_rate={metrics['win_rate']} "
            f"expectancy={metrics['expectancy']} CAGR={metrics.get('cagr')}% "
            f"Sharpe={metrics.get('sharpe_ratio')} MaxDD={metrics.get('max_drawdown_pct')}%")


def generate_unified_strategy_library(registry_path=None, output_path: str = UNIFIED_LIBRARY_PATH) -> str:
    kwargs = {"registry_path": registry_path} if registry_path else {}
    strategies = list_strategies(**kwargs)

    lines = [
        "# Unified Strategy Library", "",
        f"Generated: {datetime.now().isoformat()}", "",
        "Research Verdict and Deployment Status are tracked independently for every strategy -- "
        "a REJECT or INCONCLUSIVE research verdict never automatically changes deployment status, "
        "and being deployed never changes a recorded research verdict. See deployment/base.py.",
        "",
    ]

    for record in sorted(strategies, key=lambda r: r.strategy_key):
        lines += [
            f"## {record.display_name} ({record.strategy_id or 'no id'})", "",
            f"- Strategy ID: **{record.strategy_id or 'not assigned'}**",
            f"- Strategy key: `{record.strategy_key}`",
            f"- Strategy family: {record.strategy_family}",
            f"- **Research Verdict**: {record.research_verdict.value}"
            f" (source: {record.research_verdict_source or 'not recorded'})",
            f"- **Deployment Status**: {record.deployment_status.value}",
            f"- Primary experiment: {record.primary_experiment_id or 'not set'}",
        ]
        if record.notes:
            lines.append(f"- Notes: {record.notes}")

        if record.deployment_status in (DeploymentStatus.PAPER_TRADING, DeploymentStatus.PILOT_LIVE,
                                         DeploymentStatus.PRODUCTION):
            portfolio = load_portfolio(record.strategy_key)
            if portfolio.get("last_processed_date"):
                live_metrics = compute_live_metrics(record.strategy_key)
                lines.append(f"- Live paper-trading performance (since inception): "
                             f"{_format_metrics_line(live_metrics)}")
                lines.append(f"  - Last processed trading day: {portfolio['last_processed_date']}")
            else:
                lines.append("- Live paper-trading performance: not yet started (no days processed)")

        if record.deployment_status_history:
            lines.append("- Deployment history:")
            for entry in record.deployment_status_history:
                ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%Y-%m-%d")
                lines.append(f"  - {ts}: {entry['from_status']} -> {entry['to_status']} ({entry['reason']})")

        lines.append("")

    text = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    return output_path
