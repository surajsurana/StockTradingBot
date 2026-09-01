"""
Portfolio B -- a fixed-watchlist, agent-driven paper-trading experiment,
separate from both Portfolio A (swing_research/, deterministic) and
Portfolio C (portfolio_c/, agent overlay anchored on validated Swing
Research signals). Confirmed 2026-09-01, per explicit direction.

Where Portfolio C's candidates come from an already-researched strategy's
own signal, Portfolio B has NO strategy anchor at all: it watches a
small, fixed, hand-picked list of names (see portfolio_b/engine.py's
WATCHLIST) and lets the SAME agent stack (Fundamental Agent, News Agent,
Research Analyst, Portfolio Manager, Risk Manager) decide, purely from
each name's own recent price action plus fundamentals/news, whether
today looks like a buy -- fully autonomous within that fixed universe,
never a systematic entry rule.

Isolation, same rules as Portfolio C, permanent unless separately
re-decided:
  - Capital & state: deployment/state/portfolio_b/ -- a third, separate
    top-level namespace from both deployment/state/paper_trading/ and
    deployment/state/portfolio_c/.
  - Registry: not a new SW-numbered strategy.
  - Ranking influence: never adjusts Portfolio A's or Portfolio C's own
    candidate priority.
  - Execution: paper only, forever, unless separately re-decided.
"""
