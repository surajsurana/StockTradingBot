"""
Experiment Manager -- gives every experiment a unique ID and a permanent,
never-overwritten folder under research_lab/experiments/. Distinct from
the Knowledge Base (research_lab/knowledge_base.py): this holds the FULL
detailed record of one experiment (hypothesis text, exact parameters,
full metrics, narrative, verdict); the Knowledge Base holds a compact
cross-experiment summary Quant Researcher can paste into a prompt.
save_experiment() writes both in one call so they can never drift apart --
callers never have to remember a separate "now update the knowledge base"
step.
"""

import json
import os
import re

from research_lab import knowledge_base

EXPERIMENTS_DIR = os.path.join(os.path.dirname(__file__), "experiments")

_EXP_ID_PATTERN = re.compile(r"^EXP-(\d+)$")


def next_experiment_id(experiments_dir: str = EXPERIMENTS_DIR) -> str:
    """Scans for existing EXP-NNN folders (ignores any other naming, e.g.
    the knowledge base's SEED-* entries live in a different namespace
    entirely) and returns the next sequential ID. Never reuses one, even
    if an earlier one was somehow removed."""
    if not os.path.isdir(experiments_dir):
        return "EXP-001"
    highest = 0
    for name in os.listdir(experiments_dir):
        match = _EXP_ID_PATTERN.match(name)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"EXP-{highest + 1:03d}"


def save_experiment(exp_id: str, hypothesis: dict, parameters: dict, data_period: str,
                     metrics: dict, observations: str, verdict: dict,
                     experiments_dir: str = EXPERIMENTS_DIR,
                     knowledge_base_path: str = knowledge_base.KNOWLEDGE_BASE_PATH) -> str:
    """
    hypothesis: {"name", "mechanism", "rationale", "rules"} from Quant
      Researcher (plus the Research Director's ranking reasoning for why
      this one was selected over the rest of its batch, if applicable).
    verdict: {"decision": "PASS"|"REJECT", "reasoning": str, ...} from the
      Statistical Auditor -- this is what actually lands in the Knowledge
      Base's verdict field, never anything from an LLM call.

    Raises if this exp_id's folder already exists -- never overwrite past
    research history, structurally enforced.
    """
    exp_dir = os.path.join(experiments_dir, exp_id)
    if os.path.exists(exp_dir):
        raise ValueError(f"{exp_id} already exists -- never overwrite a past experiment. "
                          f"Use next_experiment_id() to get a fresh one.")
    os.makedirs(exp_dir)

    with open(os.path.join(exp_dir, "hypothesis.md"), "w", encoding="utf-8") as f:
        f.write(f"# {hypothesis.get('name', exp_id)}\n\n"
                f"## Mechanism\n{hypothesis.get('mechanism', '')}\n\n"
                f"## Rationale\n{hypothesis.get('rationale', '')}\n\n"
                f"## Rules\n{hypothesis.get('rules', '')}\n\n"
                f"## Why this candidate was selected\n"
                f"{hypothesis.get('selection_reasoning', '(not ranked against other candidates)')}\n")

    with open(os.path.join(exp_dir, "parameters.json"), "w", encoding="utf-8") as f:
        json.dump({"data_period": data_period, **parameters}, f, indent=2, default=str)

    with open(os.path.join(exp_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)

    with open(os.path.join(exp_dir, "observations.md"), "w", encoding="utf-8") as f:
        f.write(observations)

    with open(os.path.join(exp_dir, "verdict.md"), "w", encoding="utf-8") as f:
        f.write(f"# Verdict: {verdict.get('decision', 'UNKNOWN')}\n\n{verdict.get('reasoning', '')}\n")

    knowledge_base.record(
        exp_id=exp_id,
        hypothesis_name=hypothesis.get("name", exp_id),
        mechanism_summary=hypothesis.get("mechanism", ""),
        verdict=verdict.get("decision", "UNKNOWN"),
        key_reason=verdict.get("reasoning", ""),
        market_conditions=metrics.get("market_conditions_summary", ""),
        follow_up_ideas=observations if verdict.get("decision") != "PASS" else "",
        path=knowledge_base_path,
    )
    return exp_dir


def list_experiments(experiments_dir: str = EXPERIMENTS_DIR) -> list:
    if not os.path.isdir(experiments_dir):
        return []
    return sorted(name for name in os.listdir(experiments_dir) if _EXP_ID_PATTERN.match(name))


def load_experiment(exp_id: str, experiments_dir: str = EXPERIMENTS_DIR) -> dict:
    exp_dir = os.path.join(experiments_dir, exp_id)
    if not os.path.isdir(exp_dir):
        raise FileNotFoundError(f"No experiment folder for {exp_id} at {exp_dir}")

    result = {"exp_id": exp_id}
    for name, key in [("hypothesis.md", "hypothesis"), ("observations.md", "observations"),
                       ("verdict.md", "verdict")]:
        path = os.path.join(exp_dir, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                result[key] = f.read()
    for name, key in [("parameters.json", "parameters"), ("metrics.json", "metrics")]:
        path = os.path.join(exp_dir, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                result[key] = json.load(f)
    return result
