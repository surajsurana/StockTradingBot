"""
The portfolio rules the Advice tab checks your real Groww holdings against.

These are the defaults you approved (2026-09-20). To change one without touching code, put the same
keys in deployment/state/advice_rules.json; whatever it holds replaces the default for that key.
Everything here is ADVICE ONLY: nothing in this package places, changes or cancels an order.

Percentages are shares of the whole portfolio (holdings valued at today's price; cash excluded).
"""

import json
import os
from typing import Optional

# Which bucket each of your funds belongs to. Anything not listed here counts as an individual stock.
FUND_BUCKET = {
    "NIFTYBEES": "India index funds", "MID150BEES": "India index funds", "HDFCSML250": "India index funds",
    "MON100": "US tech and IT funds", "ITBEES": "US tech and IT funds",
    "GOLDBEES": "Gold", "SILVERBEES": "Silver",
    "GROWWDEFNC": "Sector funds",
}

DEFAULT_RULES = {
    # Bucket limits, % of the portfolio. `target` is where new money steers the bucket toward.
    "buckets": {
        "India index funds": {"min": 35, "target": 40, "max": 45},
        "US tech and IT funds": {"min": 5, "target": 8, "max": 10},
        "Gold": {"target": 10},
        "Silver": {"target": 5, "max": 7},
        "Individual stocks": {"target": 30, "max": 30},
    },
    # Limits on several buckets taken together.
    "groups": {"Gold and silver": {"members": ["Gold", "Silver"], "max": 15}},
    "stocks": {
        "max_at_buy": 5,          # % of the portfolio one stock may be when you buy it
        "trim_above": 7,          # flag for trimming above this
        "industry_max": 15,       # % of the portfolio in one industry (stocks only)
        "clusters": {"Railway PSUs": {"members": ["IRFC", "IRCTC", "RVNL"], "max": 8}},
        "count": [12, 15],        # how many separate companies to hold
        "min_position": 15000,    # rupees; smaller holdings are flagged as too small to matter
    },
    "orders": {
        "min_order": 10000,       # rupees; smaller orders are merged so brokerage stays small
        "limit_below_pct": 0.5,   # suggested limit price: this much below the last price
        "fund_split": {           # how a bucket's money is divided between its funds
            "India index funds": {"NIFTYBEES": 40, "MID150BEES": 35, "HDFCSML250": 25},
            "US tech and IT funds": {"MON100": 100},
            "Gold": {"GOLDBEES": 100},
            "Silver": {"SILVERBEES": 100},
        },
        "parts": 2,               # a deposit is bought in this many orders, about two weeks apart
    },
    # Planning assumptions for the projections: yearly return by bucket in a poor, middling and good
    # decade. They are assumptions to plan with, not forecasts, and you can override them.
    "assumed_returns": {
        "India index funds": {"low": 7, "base": 11, "high": 14},
        "US tech and IT funds": {"low": 5, "base": 10, "high": 14},
        "Gold": {"low": 3, "base": 7, "high": 10},
        "Silver": {"low": 0, "base": 6, "high": 12},
        "Individual stocks": {"low": 4, "base": 9, "high": 15},
        "Sector funds": {"low": 4, "base": 9, "high": 14},
    },
    "inflation": 5.0,
}


def load_rules(state_dir: Optional[str] = None) -> dict:
    """The default rules with any overrides from advice_rules.json applied (top-level keys replace)."""
    rules = json.loads(json.dumps(DEFAULT_RULES))
    path = os.path.join(state_dir, "advice_rules.json") if state_dir else None
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            rules.update(json.load(f))
    return rules
