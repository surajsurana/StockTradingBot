"""
Research notes on the stocks you hold: my reading of the latest results, news, valuation and trend for each
company, written on the date below. These are judgements, not automated signals. The Advice page shows the
date on every note and warns when one is more than 90 days old, so a stale view is never passed off as current.

Stances:
  Sell   -- act on it
  Watch  -- hold, but there is a specific trigger that would turn it into a sell
  Hold   -- nothing to do
(Buy comes from the monthly deposit plan, not from these notes.)

Keys are the company (demerged units are folded into their parent: TMPV covers TMCV, VEDL covers VAML,
VOGL, VEDPOWER and VISL).
"""

NOTES_AS_OF = "2026-09-19"

NOTES = {
    "UNIECOM": {
        "stance": "Sell", "sell_limit": 89.0, "review": "2026-11-05",   # fixed on 19 Sep (about 7% above the price then) so the order does not move every day
        "headline": "Sell. Place a limit order at ₹89; if it has not filled by 5 Nov, sell at market.",
        "why": ["Q1 revenue grew 14% but operating profit fell 14.5% as spending rose.",
                "About 45 times earnings for a company worth about ₹1,000 crore that few analysts follow.",
                "Trading below its 200-day average, with no clear reason for a turnaround."],
    },
    "RVNL": {
        "stance": "Watch", "exit_below": 205, "review": "2026-11-05",
        "trigger": "Sell if the price falls below \u20b9205 (its September low).",
        "headline": "Hold. Sell if it closes clearly below ₹205. Don't add.",
        "why": ["Q1 profit rose 18%, operating margin improved to 4.4%, and the order book is about ₹93,000 crore.",
                "But it is still about 50 times earnings on a thin margin, and payments from railways are slow.",
                "₹205 is its September low; closing below it would mean the downtrend has resumed.",
                "It tends to move together with IRFC and IRCTC."],
    },
    "OLECTRA": {
        "stance": "Watch", "review": "2026-11-10",
        "trigger": "Sell if next quarter's margin is still near 12% (results due around 10 Nov).",
        "results_rule": {"symbol": "OLECTRA", "metric": "ebitda_margin", "baseline_period": "2026-06-30", "need_gain_pts": 1.0, "if_fail": "sell", "label": "EBITDA margin"},
        "headline": "Hold. Sell if next quarter's margin is still near 12%.",
        "why": ["Q1 revenue rose 66% but profit only 3%, and margin fell to 12% from 13.6%.",
                "Its customers are state bus operators, so cash comes in slowly.",
                "About 55 times trailing earnings; the order book of about 8,000 buses gives visibility."],
    },
    "TMPV": {
        "stance": "Watch", "review": "2026-11-12",
        "trigger": "Sell part if Jaguar Land Rover has not improved at the November results (around 12 Nov).",
        "results_rule": {"symbol": "TMPV", "metric": "ebitda_margin", "baseline_period": "2026-06-30", "need_gain_pts": 1.0, "if_fail": "trim_half", "label": "EBITDA margin"},
        "headline": "Hold. Reduce if Jaguar Land Rover has not improved by the November results.",
        "why": ["Q1 profit fell 78% on weakness at Jaguar Land Rover; Jefferies flags competition, discounts and ageing models.",
                "The India car business is growing strongly and the commercial-vehicle unit (TMCV) looks healthier.",
                "Cheap at about 6 times forward earnings, which is why this is a watch and not a sell."],
    },
    "ELECON": {
        "stance": "Watch", "review": "2026-11-10",
        "trigger": "Sell if margins have not recovered at the November results (around 10 Nov).",
        "results_rule": {"symbol": "ELECON", "metric": "op_margin", "baseline_period": "2026-06-30", "need_gain_pts": 1.0, "if_fail": "sell", "label": "operating margin"},
        "headline": "Hold. Review after the November results.",
        "why": ["Q1 profit fell 60% and operating margin dropped from 26.6% to 21%.",
                "Over four years revenue grew about 16% a year, debt is low and free cash flow has been positive every year.",
                "Sell if margins have not recovered by the next results."],
    },
    "IRFC": {"stance": "Hold", "headline": "Hold.",
             "why": ["Steady: Q1 profit up 10%, about 15 times earnings, the cheapest of your railway stocks.",
                     "Growth is slow (about 5% a year), so expect modest returns plus a regular dividend."]},
    "IRCTC": {"stance": "Hold", "headline": "Hold.",
              "why": ["A monopoly business: return on equity about 32%, no debt, positive cash flow every year.",
                      "Q1 profit was flat and the stock is at a 52-week low, but the business itself is high quality."]},
    "LT": {"stance": "Hold", "headline": "Hold.",
           "why": ["Your best-quality holding: order book ₹7.8 lakh crore (up 27%), Q1 profit up 14%, revenue guidance 10-12%."]},
    "ETERNAL": {"stance": "Hold", "headline": "Hold. Trim if it grows above 8% of the portfolio.",
                "why": ["Growth is strong (order value up 54%), but the price assumes flawless execution: about 700 times trailing earnings and Q1 profit missed estimates."]},
    "HSCL": {"stance": "Hold", "headline": "Hold.",
             "why": ["Profit is growing fast and the stock is well up, but do not sell just because it is up.", "Valuation is rich at about 42 times earnings."]},
    "HONASA": {"stance": "Hold", "headline": "Hold.",
               "why": ["Growing about 17% a year; brand risk and a rich valuation (about 63 times earnings)."]},
    "EXIDEIND": {"stance": "Hold", "headline": "Hold.",
                 "why": ["Q1 profit up 27% and revenue up 18%; the lithium-ion factory should start earning revenue this financial year.", "Not cheap at about 40 times earnings."]},
    "OBEROIRLTY": {"stance": "Hold", "headline": "Hold.",
                   "why": ["Low debt and good cash flow at about 25 times earnings; property-cycle risk."]},
    "WAAREEENER": {"stance": "Hold", "headline": "Hold.",
                   "why": ["Very fast growth at about 19 times earnings, but solar is cyclical and policy-driven, and Q1 margins fell."]},
    "JIOFIN": {"stance": "Hold", "headline": "Hold.",
               "why": ["Early-stage lender with about a 1% return on equity; Q1 profit was helped by dividend income from group companies.", "Analysts are bullish but it still has to prove itself."]},
    "GROWW": {"stance": "Hold", "headline": "Hold.", "why": ["Growing quickly but tied to market activity and regulation."]},
    "VEDL": {"stance": "Hold", "headline": "Hold.",
             "why": ["Cheap (about 8 times forward earnings) and your biggest dividend payer; commodity-cycle risk.",
                     "The demerged units (VAML, VOGL, VEDPOWER, VISL) are held together with it."]},
    "MON100": {"stance": "Hold", "headline": "Hold.", "why": ["Within its range in your plan."]},
    "ITBEES": {"stance": "Hold", "headline": "Hold.", "why": ["Small IT-sector fund; counted with US tech in your plan."]},
    "GROWWDEFNC": {"stance": "Hold", "headline": "Hold.", "why": ["Small defence-sector fund."]},
    "GOLDBEES": {"stance": "Hold", "headline": "Hold.", "why": []},
    "SILVERBEES": {"stance": "Hold", "headline": "Hold.", "why": ["Silver has run up a lot and is volatile."]},
}
