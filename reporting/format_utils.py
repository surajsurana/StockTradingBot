"""
Shared DISPLAY-ONLY numeric formatting -- used by reporting/telegram_templates.py
and by deployment/paper_trading_engine.py's / deployment/drift_report.py's own
markdown report generation, so every paper-trading-facing report (Telegram
message, per-strategy .md report, drift report) shows numbers the same way.

Formats a value for a human to READ. Never used by, and never changes,
any stored or calculated value -- callers pass the real float/int in;
this returns a STRING for display only. No rounding happens anywhere
except inside the string produced here.
"""

_NUMERIC_TYPES = (int, float)


def format_metric(value, decimals: int = 2):
    """
    Formats a number with AT MOST `decimals` decimal places (comma-grouped
    for readability at large magnitudes -- a no-op for anything under
    1000). None and non-numeric values (already-formatted strings, bool
    flags, etc.) pass through completely unchanged, so existing
    None -> "n/a" handling at each call site keeps working untouched.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, _NUMERIC_TYPES):
        return value
    return f"{value:,.{decimals}f}"
