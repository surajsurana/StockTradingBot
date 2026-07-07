"""
Company fundamentals health check.

This is the filter Suraj specifically asked for: before any strategy is even
allowed to consider trading a stock, check whether the underlying company is
financially healthy -- profitable, not drowning in debt, generating a
reasonable return on equity, and not in obvious revenue decline. This runs
independently of price charts; it's a check on the business, not the stock
price movement.

Design choice: if a metric is missing/unavailable for a stock (which happens
sometimes with yfinance's fundamentals data), we FAIL that check rather than
skip it. The reasoning: we'd rather cautiously exclude a stock we don't have
enough information on than silently trade it despite not really knowing its
financial health. This is a conservative default and can be relaxed later.

Exception: the debt-to-equity check is skipped entirely (not failed) for
banks and other financial-sector companies. Their business model runs on
leverage by design -- customer deposits and loans are core to how a bank
operates, not "debt" in the sense that applies to a manufacturing or IT
company. Yahoo Finance's data provider also frequently doesn't report a
standard debt-to-equity figure for banks at all, so treating "missing" as
"fail" was wrongly excluding financially sound banks like HDFC Bank and
ICICI Bank in initial testing. Profitability, return on equity, and revenue
growth still fully apply to financial companies and are still checked.
"""

from dataclasses import dataclass, field

import yfinance as yf

FINANCIAL_SECTORS = {"Financial Services", "Financial", "Financials"}


@dataclass
class FundamentalsResult:
    symbol: str
    passed: bool
    reasons: list = field(default_factory=list)  # human-readable explanations for each check
    metrics: dict = field(default_factory=dict)   # raw values, for the report/debugging


def fetch_fundamentals(symbol: str) -> dict:
    """
    Pulls key fundamental metrics for a symbol via yfinance. Returns a plain
    dict with whatever fields were available (missing fields are simply
    absent, not zero -- callers must check for presence, not just falsiness).
    """
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}

    fields = [
        "trailingEps",          # earnings per share -- positive means profitable
        "returnOnEquity",       # profitability relative to shareholder equity
        "debtToEquity",         # leverage -- higher means more debt-funded (not applicable to banks)
        "revenueGrowth",        # year-over-year revenue growth
        "profitMargins",        # net profit as a fraction of revenue
        "trailingPE",           # valuation context (not a health check by itself)
        "sector",               # used to detect banks/financial companies
    ]
    return {f: info[f] for f in fields if f in info and info[f] is not None}


def check_health(symbol: str, metrics: dict, criteria: dict) -> FundamentalsResult:
    """
    Applies the configured criteria to a symbol's metrics. All checks must
    pass for the overall result to pass -- one red flag is enough to exclude
    a stock, matching a cautious "don't trade companies we're unsure about"
    philosophy. Exception: debt-to-equity is skipped (not failed) for
    financial-sector companies -- see module docstring.
    """
    reasons = []
    passed = True

    is_financial_sector = metrics.get("sector") in FINANCIAL_SECTORS

    # 1. Profitability: EPS should be positive
    eps = metrics.get("trailingEps")
    if eps is None:
        passed = False
        reasons.append("EPS data unavailable -- excluded to be cautious")
    elif eps <= 0:
        passed = False
        reasons.append(f"Negative/zero EPS ({eps}) -- company isn't profitable")
    else:
        reasons.append(f"EPS positive ({eps}) -- OK")

    # 2. Debt levels: debt-to-equity should be below the configured max.
    # Skipped for banks/financial companies -- see module docstring.
    if is_financial_sector:
        reasons.append("Debt-to-equity check skipped (financial-sector company, leverage is core to the business model)")
    else:
        dte = metrics.get("debtToEquity")
        max_dte = criteria.get("max_debt_to_equity", 150)  # yfinance reports this as a percentage-like number
        if dte is None:
            passed = False
            reasons.append("Debt-to-equity data unavailable -- excluded to be cautious")
        elif dte > max_dte:
            passed = False
            reasons.append(f"Debt-to-equity too high ({dte} > {max_dte})")
        else:
            reasons.append(f"Debt-to-equity acceptable ({dte} <= {max_dte}) -- OK")

    # 3. Return on equity should be positive and above a minimum
    roe = metrics.get("returnOnEquity")
    min_roe = criteria.get("min_roe", 0.0)
    if roe is None:
        passed = False
        reasons.append("Return-on-equity data unavailable -- excluded to be cautious")
    elif roe < min_roe:
        passed = False
        reasons.append(f"Return-on-equity too low ({roe:.1%} < {min_roe:.1%})")
    else:
        reasons.append(f"Return-on-equity acceptable ({roe:.1%}) -- OK")

    # 4. Revenue growth should not be sharply negative
    rev_growth = metrics.get("revenueGrowth")
    min_rev_growth = criteria.get("min_revenue_growth", -0.10)  # allow mild decline, not a crash
    if rev_growth is None:
        passed = False
        reasons.append("Revenue growth data unavailable -- excluded to be cautious")
    elif rev_growth < min_rev_growth:
        passed = False
        reasons.append(f"Revenue declining too fast ({rev_growth:.1%} < {min_rev_growth:.1%})")
    else:
        reasons.append(f"Revenue growth acceptable ({rev_growth:.1%}) -- OK")

    return FundamentalsResult(symbol=symbol, passed=passed, reasons=reasons, metrics=metrics)


def filter_universe(symbols: list, criteria: dict) -> tuple:
    """
    Runs the health check across a list of symbols.
    Returns (eligible_symbols, all_results) where all_results is a list of
    FundamentalsResult (for both passing and failing symbols, so the reasons
    can be printed/reported either way).
    """
    eligible = []
    all_results = []

    for symbol in symbols:
        try:
            metrics = fetch_fundamentals(symbol)
        except Exception as e:
            all_results.append(FundamentalsResult(
                symbol=symbol, passed=False,
                reasons=[f"Could not fetch fundamentals: {e}"],
            ))
            continue

        result = check_health(symbol, metrics, criteria)
        all_results.append(result)
        if result.passed:
            eligible.append(symbol)

    return eligible, all_results
