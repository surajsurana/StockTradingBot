"""
Charges and tax for the rupee stock pools, for the dashboard's P&L tab only. Paper trading itself
still fills at raw prices; nothing here changes a fill, a position or a stored number.

ASSUMPTIONS (all in one place so they are easy to change; checked against Zerodha's published
equity rates and the Income Tax Act's listed-equity rates on 2026-09-19 -- verify against a real
contract note before relying on the rupee amounts):

Broker charges, per executed leg (Zerodha's published equity schedule):
  delivery   brokerage Rs.0; STT 0.1% of value on buy AND sell; stamp duty 0.015% on buy;
             DP charge Rs.13.5 per scrip on each sell
  intraday   brokerage 0.03% of value or Rs.20, whichever is lower; STT 0.025% on the sell leg;
             stamp duty 0.003% on buy
  both       NSE transaction charge 0.00297% of value; SEBI turnover fee Rs.10 per crore;
             GST 18% on brokerage + transaction charge + SEBI fee + DP charge

Tax on the gain, worked out per BOOK (one strategy in one pool):
  delivery (Pools A, B, C, F)   short-term capital gains 20% + 4% cess = 20.8%, on the book's net gain
                                after charges; gains and losses inside the same book offset each
                                other, but there is NO offset across books (a loss-making book gets
                                no credit against a profitable one -- a slight overstatement of tax)
  intraday (Pool D)             speculative business income, taxed at the slab rate. ASSUMED top slab
                                30% + 4% cess = 31.2% (INTRADAY_INCOME_TAX_RATE) -- change it to yours
  Open positions are treated as if sold today. Nothing is held anywhere near 12 months, so no
  long-term gains. Surcharge is ignored.
TDS: a resident investor has no TDS on equity gains, so the P&L tab shows none for these pools.
"""

from typing import Iterable, Optional

STT_DELIVERY = 0.001
STT_INTRADAY_SELL = 0.00025
EXCHANGE_TRANSACTION = 0.0000297
SEBI_FEE = 0.000001
STAMP_DELIVERY_BUY = 0.00015
STAMP_INTRADAY_BUY = 0.00003
DP_CHARGE_PER_SELL = 13.5
INTRADAY_BROKERAGE_PCT = 0.0003
INTRADAY_BROKERAGE_CAP = 20.0
GST_RATE = 0.18

STCG_RATE = 0.20 * 1.04                 # 20% + 4% cess
INTRADAY_INCOME_TAX_RATE = 0.30 * 1.04  # assumed top slab + cess

COMPONENTS = ("brokerage", "stt", "exchange", "sebi", "stamp", "dp")


def leg_charges(value: float, side: str, intraday: bool) -> dict:
    """Charges on ONE executed leg of `value` rupees. side is "BUY" or "SELL". Returns the
    components plus "gst" (on brokerage, exchange, SEBI and DP) and "total"."""
    value = abs(float(value))
    brokerage = min(INTRADAY_BROKERAGE_PCT * value, INTRADAY_BROKERAGE_CAP) if intraday else 0.0
    if intraday:
        stt = STT_INTRADAY_SELL * value if side == "SELL" else 0.0
    else:
        stt = STT_DELIVERY * value
    exchange = EXCHANGE_TRANSACTION * value
    sebi = SEBI_FEE * value
    stamp = (STAMP_INTRADAY_BUY if intraday else STAMP_DELIVERY_BUY) * value if side == "BUY" else 0.0
    dp = DP_CHARGE_PER_SELL if (not intraday and side == "SELL" and value > 0) else 0.0
    gst = GST_RATE * (brokerage + exchange + sebi + dp)
    out = {"brokerage": brokerage, "stt": stt, "exchange": exchange, "sebi": sebi, "stamp": stamp, "dp": dp, "gst": gst}
    out["total"] = sum(out.values())
    return out


def _add(into: dict, leg: dict) -> None:
    for k, v in leg.items():
        into[k] = into.get(k, 0.0) + v


def book_costs(trades: Iterable[dict], open_positions: Iterable[dict], intraday: bool, gross: float) -> dict:
    """Charges, GST and tax for one book.

    trades: closed trades, each with entry_price, exit_price, quantity and optional direction
    ("SELL" = a short, entered by selling). open_positions: dicts with entry_price, price (latest
    quote, used as the assumed exit) and quantity. gross: the book's realised + unrealised P&L
    before charges. Returns {"detail": {component: amount}, "charges": everything except GST,
    "gst", "tax", "tax_rate"}, all in rupees."""
    detail: dict = {}
    for t in trades:
        q = float(t.get("quantity", 0) or 0)
        entry_value = float(t.get("entry_price", 0) or 0) * q
        exit_value = float(t.get("exit_price", 0) or 0) * q
        short = str(t.get("direction", "BUY")).upper() == "SELL"
        _add(detail, leg_charges(entry_value, "SELL" if short else "BUY", intraday))
        _add(detail, leg_charges(exit_value, "BUY" if short else "SELL", intraday))
    for p in open_positions:
        q = float(p.get("quantity", 0) or 0)
        short = str(p.get("direction", "BUY")).upper() == "SELL"
        _add(detail, leg_charges(float(p.get("entry_price", 0) or 0) * q, "SELL" if short else "BUY", intraday))
        _add(detail, leg_charges(float(p.get("price", p.get("entry_price", 0)) or 0) * q, "BUY" if short else "SELL", intraday))
    gst = detail.get("gst", 0.0)
    charges = sum(detail.get(k, 0.0) for k in COMPONENTS)
    rate = INTRADAY_INCOME_TAX_RATE if intraday else STCG_RATE
    tax = max(0.0, gross - charges - gst) * rate
    return {"detail": detail, "charges": charges, "gst": gst, "tax": tax, "tax_rate": rate}
