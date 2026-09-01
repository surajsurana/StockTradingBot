"""
Portfolio C -- isolated agent/LLM paper-trading experiment.

See the "Candidate Priority & Portfolio C Design" review (2026-08-30) for
the full design. Summary: Portfolio A (swing_research/) is the existing
deterministic quant program. Portfolio C is a SEPARATE, fully isolated
paper-trading track that takes the SAME already-validated anchor
strategies' daily candidates and runs them through the agent/LLM decision
stack (Fundamental Agent, News Agent, Research Analyst, Portfolio
Manager, Risk Manager) instead of Portfolio A's own confidence-ranked
allocation -- to test whether that judgment layer adds anything, on its
own capital, never Portfolio A's.

Isolation, permanent unless separately re-decided:
  - Capital & state: deployment/state/portfolio_c/ -- a different
    top-level namespace than deployment/state/paper_trading/<key>/, so no
    code path here ever reads or writes Portfolio A's files.
  - Registry: Portfolio C is not a new SW-numbered strategy -- it doesn't
    go in deployment/state/strategy_registry.json at all.
  - Ranking influence: an agent output here never adjusts Portfolio A's
    candidate priority in swing_research/candidate_ranking.py.
  - Execution: paper only, forever, unless separately re-decided. No
    import of execution/ or cio/ anywhere in this package.
"""
