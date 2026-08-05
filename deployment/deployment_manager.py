"""
Generic Research Deployment Manager (Phase 1) -- strategy-independent
registry tracking each strategy's ResearchVerdict and DeploymentStatus as
two entirely separate fields (see base.py's module docstring for why).

Persisted as a single JSON file (deployment/state/strategy_registry.json)
-- deliberately simple, human-readable, diffable, and requiring no new
runtime dependency, consistent with this whole research program's
existing convention (experiment records, knowledge_base.jsonl,
research_conclusions.jsonl are all plain JSON/JSONL files).

Every status change is an explicit, separate function call -- there is no
code path anywhere in this module that changes deployment_status as a
side effect of set_research_verdict(), or vice versa.
"""

import json
import os
import time
from typing import Optional

from deployment.base import DeploymentStatus, ResearchVerdict, StrategyRecord, is_valid_transition
from deployment.settings import REGISTRY_PATH


def _load_registry(path: str = REGISTRY_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {key: StrategyRecord.from_dict(value) for key, value in raw.items()}


def _save_registry(registry: dict, path: str = REGISTRY_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({key: record.to_dict() for key, record in registry.items()}, f, indent=2)


_STRATEGY_ID_PREFIX = "SW-"


def _next_strategy_id(registry: dict) -> str:
    """
    Permanent-ID allocator: scans every EXISTING strategy_id in the
    registry (regardless of family -- one global sequence, not per-family),
    finds the highest numeric suffix, and returns the next one. Never
    reuses a number, even if a strategy is later archived -- IDs are
    permanent and are never renumbered (see base.py's StrategyRecord
    docstring).
    """
    max_n = 0
    for record in registry.values():
        sid = record.strategy_id
        if sid.startswith(_STRATEGY_ID_PREFIX):
            try:
                max_n = max(max_n, int(sid[len(_STRATEGY_ID_PREFIX):]))
            except ValueError:
                continue
    return f"{_STRATEGY_ID_PREFIX}{max_n + 1:03d}"


def register_strategy(strategy_key: str, display_name: str, strategy_family: str,
                       timeframe: str = "Daily", execution_frequency: str = "End-of-Day",
                       registry_path: str = REGISTRY_PATH) -> StrategyRecord:
    """
    Idempotent: registering an already-registered strategy_key returns the
    EXISTING record unchanged (never resets a strategy's recorded verdict,
    deployment status, metadata, or strategy_id just because
    register_strategy() was called again -- registration is a one-time
    "this strategy exists" declaration, not a reset). To change an
    already-registered strategy's metadata, use update_strategy_metadata()
    below instead.

    Assigns a PERMANENT strategy_id (the "SW-NNN" scheme, see base.py) on
    first registration only -- this id is never reassigned or renumbered
    for the life of this registry.

    timeframe / execution_frequency: declarative scheduling metadata (see
    base.py's StrategyRecord docstring) -- read by deployment/scheduler.py
    to decide when this strategy is due to run, instead of any script
    hardcoding a per-strategy schedule.
    """
    registry = _load_registry(registry_path)
    if strategy_key in registry:
        return registry[strategy_key]
    record = StrategyRecord(strategy_key=strategy_key, display_name=display_name,
                             strategy_family=strategy_family, timeframe=timeframe,
                             execution_frequency=execution_frequency,
                             strategy_id=_next_strategy_id(registry))
    registry[strategy_key] = record
    _save_registry(registry, registry_path)
    return record


def backfill_missing_strategy_ids(registry_path: str = REGISTRY_PATH) -> dict:
    """
    One-time (but safely idempotent/re-runnable) migration: assigns a
    permanent strategy_id to any ALREADY-registered strategy that predates
    the strategy_id field (registered before 2026-08-04). Processes
    records in the registry file's own stored order (their original
    registration order, since Python dicts -- and this program's JSON
    round-trip -- preserve insertion order and re-assigning an existing
    key does not move it), so IDs are assigned in genuine historical
    order. Never touches a strategy_id that's already set. Returns
    {strategy_key: strategy_id} for every strategy, post-backfill.
    """
    registry = _load_registry(registry_path)
    for record in registry.values():
        if not record.strategy_id:
            record.strategy_id = _next_strategy_id(registry)
    _save_registry(registry, registry_path)
    return {key: record.strategy_id for key, record in registry.items()}


def set_primary_experiment_id(strategy_key: str, exp_id: str,
                               registry_path: str = REGISTRY_PATH) -> StrategyRecord:
    """Records the base-run research experiment this strategy's reports
    should reference (e.g. "EXP-013") -- shown as "Experiment: ..." in
    every Telegram message/report. Never touches research_verdict or
    deployment_status."""
    registry = _load_registry(registry_path)
    if strategy_key not in registry:
        raise KeyError(f"Strategy '{strategy_key}' is not registered -- call register_strategy() first.")
    record = registry[strategy_key]
    record.primary_experiment_id = exp_id
    _save_registry(registry, registry_path)
    return record


def update_strategy_metadata(strategy_key: str, timeframe: Optional[str] = None,
                              execution_frequency: Optional[str] = None,
                              registry_path: str = REGISTRY_PATH) -> StrategyRecord:
    """Updates an already-registered strategy's scheduling metadata only
    -- never touches research_verdict or deployment_status."""
    registry = _load_registry(registry_path)
    if strategy_key not in registry:
        raise KeyError(f"Strategy '{strategy_key}' is not registered -- call register_strategy() first.")
    record = registry[strategy_key]
    if timeframe is not None:
        record.timeframe = timeframe
    if execution_frequency is not None:
        record.execution_frequency = execution_frequency
    _save_registry(registry, registry_path)
    return record


def get_strategy(strategy_key: str, registry_path: str = REGISTRY_PATH) -> Optional[StrategyRecord]:
    return _load_registry(registry_path).get(strategy_key)


def list_strategies(registry_path: str = REGISTRY_PATH) -> list:
    return list(_load_registry(registry_path).values())


def set_research_verdict(strategy_key: str, verdict: ResearchVerdict, source: str = "",
                          registry_path: str = REGISTRY_PATH) -> StrategyRecord:
    """
    Records a strategy's research verdict -- e.g. from
    swing_research.acceptance_criteria.determine_acceptance_verdict()'s
    result, or a certification run's result (deployment/certification.py).
    NEVER touches deployment_status. `source`: e.g. "EXP-013, EXP-014" for
    traceability back to the actual experiment records this verdict came
    from.
    """
    registry = _load_registry(registry_path)
    if strategy_key not in registry:
        raise KeyError(f"Strategy '{strategy_key}' is not registered -- call register_strategy() first.")
    record = registry[strategy_key]
    record.research_verdict = verdict
    record.research_verdict_source = source
    _save_registry(registry, registry_path)
    return record


def set_deployment_status(strategy_key: str, new_status: DeploymentStatus, reason: str = "",
                           force: bool = False, registry_path: str = REGISTRY_PATH) -> StrategyRecord:
    """
    Records a strategy's deployment status. ALWAYS a manual, explicit call
    -- nothing in this program calls this automatically based on a
    research verdict, evidence-quality score, or live performance metric.
    `reason`: required context for the audit trail (e.g. "explicit human
    approval after 3 months of paper trading, see lifecycle review dated
    ..."). `force=True` bypasses the advisory transition-validity check
    (base.is_valid_transition()) for a genuinely exceptional case -- the
    default path always validates.
    """
    registry = _load_registry(registry_path)
    if strategy_key not in registry:
        raise KeyError(f"Strategy '{strategy_key}' is not registered -- call register_strategy() first.")
    record = registry[strategy_key]
    if not force and not is_valid_transition(record.deployment_status, new_status):
        raise ValueError(
            f"Invalid deployment transition for '{strategy_key}': "
            f"{record.deployment_status.value} -> {new_status.value}. "
            f"Pass force=True if this is a genuinely intentional exception."
        )
    record.deployment_status_history.append({
        "from_status": record.deployment_status.value, "to_status": new_status.value,
        "timestamp": time.time(), "reason": reason,
    })
    record.deployment_status = new_status
    _save_registry(registry, registry_path)
    return record
