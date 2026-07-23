"""
NSE Cash Intraday Research Lab -- a completely independent research
platform for discovering, validating, and tracking intraday strategy
candidates. Deliberately isolated from the live swing trading system
(run_daily.py, monitor_positions.py, strategies/, risk/, portfolio/,
research/, news/, macro/, cio/) -- nothing in this package is imported by,
or imports from, that production code. See PROJECT_CONTEXT.md and the
approved plan for the full rationale.

Cash equity research only, virtual capital only, no live trading -- this
package cannot place a real order or touch real money in its current form.
"""
