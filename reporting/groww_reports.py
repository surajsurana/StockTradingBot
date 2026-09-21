"""
Return reports for the real Groww account, built from the reports you download from Groww
(Profile -> Reports): Stocks order history, Groww balance statement, Stocks capital gains (one
per financial year). Nothing here talks to Groww or places anything; it reads the .xlsx files.

`build_reports(folder)` turns them into one small JSON-able dict (money flows, yearly and monthly
totals, charges, dividends, realised P&L, cash movements, and a "same money in Nifty 50" benchmark).
`python -m reporting.groww_reports <folder> <out.json>` writes it to deployment/state/groww_reports.json,
which is git-ignored (it is your personal financial history). The dashboard adds today's live value
on top to work out returns.

KNOWN LIMITS (shown on the Reports tab too):
  * Order history starts March 2021 and the balance statement July 2023 -- that is all Groww returns.
  * Year-end values are rebuilt from the orders and Yahoo prices, split-adjusted. Shares received free
    (demergers such as Tata Motors / Vedanta) are not in the orders, so year-end values before today
    leave them out (about 3% of today's value).
  * Reports are as of their download date; the dashboard treats any later change in your holdings'
    cost as new money added.
  * Dividends: Groww's Dividend_Report_*.pdf (Profile -> Reports -> Dividend report) is the source for
    everything it covers (it starts April 2023). Before that, and for any payout whose ex-date has passed
    but Groww has not listed yet, each dividend is rebuilt as (dividend per share on the ex-date, from
    Yahoo) x (shares held the day before). That rebuild matched 66 of Groww's 67 payouts to the rupee
    and the yearly totals in Groww's tax reports, so the estimates are reliable. The date shown is the
    ex-date; the money arrives two to four weeks later.
"""

import glob
import json
import os
import re
import sys
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

BENCHMARK = "NIFTYBEES"
DEPOSIT_SEGMENTS = {"UPI", "GROWW_UPI", "PAYU_DEPOSIT", "RAZORPAY_DEPOSIT", "STOCKS_SIP"}
WITHDRAW_SEGMENTS = {"STOCK_PAYOUT", "GROWW_WITHDRAW"}


def xirr(flows: List[Tuple[date, float]]) -> Optional[float]:
    """Annualised money-weighted return for dated cash flows (money in negative, money out and the
    final value positive). None if it cannot be solved (needs both signs)."""
    if not flows or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)

    def npv(r):
        return sum(a / ((1 + r) ** ((d - t0).days / 365.0)) for d, a in flows)

    lo, hi = -0.99, 50.0
    flo = npv(lo)
    if (flo > 0) == (npv(hi) > 0):
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def modified_dietz(start_value: float, end_value: float, net_flow: float, weighted_flow: float) -> Optional[float]:
    """Period return: gain divided by the money that was actually at work (start value plus new money
    weighted by how long it was invested)."""
    base = start_value + weighted_flow
    return (end_value - start_value - net_flow) / base if base > 0 else None


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _read_rows(path: str, sheet=None) -> list:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.active
    return [[c for c in r] for r in ws.iter_rows(values_only=True)]


def read_orders(folder: str) -> List[dict]:
    path = (glob.glob(os.path.join(folder, "Stocks_Order_History*.xlsx")) or [None])[0]
    if not path:
        raise FileNotFoundError("Stocks_Order_History*.xlsx not found in " + folder)
    out, header = [], None
    for r in _read_rows(path):
        if r and r[0] == "Stock name":
            header = r
            continue
        if header and r[0] and r[3] in ("BUY", "SELL"):
            d = dict(zip(header, r))
            if d.get("Order status") != "Executed":
                continue
            out.append({"symbol": d["Symbol"], "isin": d.get("ISIN"), "name": str(d.get("Stock name") or d["Symbol"]), "type": d["Type"], "qty": _num(d["Quantity"]), "value": _num(d["Value"]),
                        "ts": datetime.strptime(d["Execution date and time"], "%d-%m-%Y %I:%M %p")})
    return sorted(out, key=lambda x: x["ts"])


def read_fy_reports(folder: str) -> List[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(folder, "Stocks_Capital_Gains_Report*.xlsx"))):
        m = re.search(r"(\d{2})-(\d{2})-(\d{4})_(\d{2})-(\d{2})-(\d{4})", os.path.basename(path))
        fy = f"FY{m.group(3)[2:]}-{m.group(6)[2:]}" if m else os.path.basename(path)
        rec = {"fy": fy, "charges": 0.0, "dividends": 0.0, "intraday": 0.0, "short_term": 0.0, "long_term": 0.0, "brokerage": 0.0, "gst": 0.0, "stt": 0.0, "dp": 0.0, "exchange": 0.0, "sebi": 0.0, "stamp": 0.0}
        in_charges = False
        for r in _read_rows(path):
            label = str(r[0]) if r and r[0] is not None else ""
            if label == "Charges":
                in_charges = True
            elif label.startswith("Realised P&L"):
                in_charges = False
            if label == "Total" and in_charges and not rec["charges"]:
                rec["charges"] = _num(r[1])
            elif label == "Brokerage" and in_charges:
                rec["brokerage"] = _num(r[1])
            elif label == "Total GST":
                rec["gst"] = _num(r[1])
            elif label.startswith("STT"):
                rec["stt"] = _num(r[1])
            elif label == "DP Charges":
                rec["dp"] = _num(r[1])
            elif label == "Exchange Transaction Charges" and in_charges:
                rec["exchange"] = _num(r[1])
            elif label == "SEBI Charges" and in_charges:
                rec["sebi"] = _num(r[1])
            elif label == "Stamp Duty" and in_charges:
                rec["stamp"] = _num(r[1])
            elif label == "Dividends":
                rec["dividends"] = _num(r[1])
            elif label == "Intraday P&L":
                rec["intraday"] = _num(r[1])
            elif label == "Short Term P&L":
                rec["short_term"] = _num(r[1])
            elif label == "Long Term P&L":
                rec["long_term"] = _num(r[1])
        rows.append(rec)
    return rows


def read_ledger(folder: str) -> dict:
    path = (glob.glob(os.path.join(folder, "Groww_Balance_Statement*.xlsx")) or [None])[0]
    years: Dict[int, dict] = {}
    months: Dict[str, dict] = {}
    first = None
    if not path:
        return {"years": [], "months": [], "first_date": None}
    header = None
    for r in _read_rows(path):
        if r and r[0] == "Transaction Date":
            header = r
            continue
        if not header or not r or not r[0]:
            continue
        try:
            d = datetime.strptime(str(r[0]), "%d-%m-%Y").date()
        except ValueError:
            continue
        row = dict(zip(header, r))
        seg = row.get("Segment Type")
        y = years.setdefault(d.year, {"year": d.year, "deposits": 0.0, "sip": 0.0, "withdrawals": 0.0})
        mo = months.setdefault(d.strftime("%Y-%m"), {"month": d.strftime("%Y-%m"), "deposited": 0.0, "withdrawn": 0.0})
        if seg in DEPOSIT_SEGMENTS:
            y["sip" if seg == "STOCKS_SIP" else "deposits"] += _num(row.get("Credit (Rs.)"))
            mo["deposited"] += _num(row.get("Credit (Rs.)"))
        elif seg in WITHDRAW_SEGMENTS:
            y["withdrawals"] += _num(row.get("Debit (Rs.)"))
            mo["withdrawn"] += _num(row.get("Debit (Rs.)"))
        first = d if first is None or d < first else first
    return {"years": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in y.items()} for _, y in sorted(years.items())],
            "months": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()} for _, m in sorted(months.items())],
            "first_date": first.isoformat() if first else None}


_DIV_LINE = re.compile(r"^(.+?) (INE\w{9}) (\d{2}-\d{2}-\d{4}) (\d+(?:\.\d+)?) Rs\. ([\d.]+) Rs\. ([\d,.]+)$")


def parse_dividend_lines(lines: List[str], isin_map: Dict[str, str]) -> List[dict]:
    """Rows of Groww's dividend report: company, ISIN, ex-date, shares, dividend per share, amount."""
    rows = []
    for line in lines:
        m = _DIV_LINE.match(line.strip())
        if not m:
            continue
        rows.append({"symbol": isin_map.get(m.group(2), m.group(1)), "name": m.group(1), "ex": datetime.strptime(m.group(3), "%d-%m-%Y").date().isoformat(),
                     "qty": float(m.group(4)), "dps": float(m.group(5)), "gross": float(m.group(6).replace(",", ""))})
    return rows


def read_dividend_report(folder: str, isin_map: Dict[str, str]) -> Tuple[List[dict], Optional[str]]:
    """Groww's dividend report (PDF) as rows, plus the first day it covers; ([], None) if it is absent or
    the PDF reader is not installed."""
    path = (glob.glob(os.path.join(folder, "Dividend_Report*.pdf")) or [None])[0]
    if not path:
        return [], None
    try:
        import pypdf
    except ImportError:
        return [], None
    lines = [ln for page in pypdf.PdfReader(path).pages for ln in (page.extract_text() or "").splitlines()]
    m = re.search(r"(\d{2})-(\d{2})-(\d{4})_\d{2}-\d{2}-\d{4}\.pdf$", os.path.basename(path))
    start = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else "2023-04-01"
    return parse_dividend_lines(lines, isin_map), start


def _price_history(symbols: List[str]) -> Tuple[dict, dict, dict]:
    """Split-adjusted Yahoo closes, split events and dividends for each symbol (NSE, else BSE)."""
    import yfinance as yf
    px, spl, divs = {}, {}, {}
    for s in symbols:
        for suffix in (".NS", ".BO"):
            t = yf.Ticker(s.replace("$", "") + suffix)
            try:
                h = t.history(start="2021-01-01", auto_adjust=False)
            except Exception:
                continue
            if not h.empty:
                h.index = h.index.tz_localize(None)
                px[s] = h["Close"].dropna()
                sp = t.splits
                if len(sp):
                    sp.index = sp.index.tz_localize(None) if sp.index.tz is not None else sp.index
                spl[s] = sp
                dv = t.dividends
                if dv is not None and len(dv):
                    dv.index = dv.index.tz_localize(None) if dv.index.tz is not None else dv.index
                    divs[s] = dv
                break
    return px, spl, divs


def _qty_in_todays_shares(orders, symbol, cutoff, splits) -> float:
    total = 0.0
    for o in orders:
        if o["symbol"] != symbol or o["ts"] > cutoff:
            continue
        factor = 1.0
        if splits is not None and len(splits):
            later = splits[splits.index > o["ts"]]
            if len(later):
                factor = float(later.prod())
        total += (o["qty"] if o["type"] == "BUY" else -o["qty"]) * factor
    return total


FREE_SHARES_FROM = date(2025, 10, 1)   # demergers (Tata Motors, Vedanta) that put extra shares in the account


def dividend_rows(orders: List[dict], dividends: dict, splits: dict, holdings: Optional[Dict[str, float]] = None) -> List[dict]:
    """Every dividend that fell due on shares held: dividend per share on the ex-date x shares held the
    day before. Shares that arrived free in a demerger (holdings above what the orders explain) count
    from FREE_SHARES_FROM; a company with no orders at all (a demerged unit) uses the current holding."""
    holdings = holdings or {}
    rows = []
    for sym, series in dividends.items():
        own = [o for o in orders if o["symbol"] == sym]
        sp = splits.get(sym)

        def qty_before(day):
            q = 0.0
            for o in own:
                if o["ts"].date() < day:
                    f = 1.0
                    if sp is not None and len(sp):
                        later = sp[sp.index > o["ts"]]
                        f = float(later.prod()) if len(later) else 1.0
                    q += (o["qty"] if o["type"] == "BUY" else -o["qty"]) * f
            return q
        extra = 0.0
        if own and sym in holdings:
            extra = max(0.0, holdings[sym] - qty_before(date(9999, 12, 31)))
        for ex, dps in series.items():
            day = ex.date()
            if day < date(2021, 3, 1):
                continue
            q = holdings.get(sym, 0.0) if not own else qty_before(day) + (extra if day >= FREE_SHARES_FROM else 0.0)
            if q > 0.0001 and dps > 0:
                rows.append({"symbol": sym, "ex": day.isoformat(), "dps": round(float(dps), 4), "qty": round(q, 2), "gross": round(q * float(dps), 2)})
    return sorted(rows, key=lambda r: (r["ex"], r["symbol"]))


def merged_dividends(folder: str, orders: List[dict], estimated: List[dict], isins: Optional[Dict[str, str]]) -> List[dict]:
    """Groww's own dividend report wherever it covers, the rebuild before it and for payouts it has not
    listed yet. Each row says which it is: groww, estimated or due."""
    isin_map = {o["isin"]: o["symbol"] for o in orders if o.get("isin")}
    isin_map.update(isins or {})
    groww, start = read_dividend_report(folder, isin_map)
    if not groww:
        return [{**r, "source": "estimated"} for r in estimated]
    last = max(r["ex"] for r in groww)
    rows = [{**r, "source": "estimated"} for r in estimated if r["ex"] < start]
    rows += [{"symbol": r["symbol"], "ex": r["ex"], "dps": r["dps"], "qty": r["qty"], "gross": r["gross"], "source": "groww"} for r in groww]
    rows += [{**r, "source": "due"} for r in estimated if r["ex"] > last]
    return sorted(rows, key=lambda r: (r["ex"], r["symbol"]))


def build_reports(folder: str, prices=None, holdings: Optional[Dict[str, float]] = None, isins: Optional[Dict[str, str]] = None) -> dict:
    """`prices` = (px, spl, divs) from _price_history, injectable for tests. `holdings` = {symbol: quantity}
    from the live Groww holdings, used for shares that arrived without an order (demergers). `isins` =
    {isin: symbol} for holdings that have no orders, to match rows of Groww's dividend report."""
    orders = read_orders(folder)
    holdings = holdings or {}
    symbols = sorted({o["symbol"] for o in orders} | set(holdings))
    px, spl, *rest = prices if prices is not None else _price_history(symbols)
    divs = rest[0] if rest else {}
    end = orders[-1]["ts"].date()

    def last_price(sym, day):
        s = px.get(sym)
        if s is None:
            return None
        s = s[s.index <= str(day) + " 23:59:59"]
        return float(s.iloc[-1]) if len(s) else None

    flows: Dict[str, float] = {}
    monthly: Dict[str, dict] = {}
    yearly: Dict[int, dict] = {}
    bench_units_by_day: Dict[str, float] = {}
    for o in orders:
        d = o["ts"].date()
        sign = -1 if o["type"] == "BUY" else 1
        flows[d.isoformat()] = flows.get(d.isoformat(), 0.0) + sign * o["value"]
        mo = monthly.setdefault(d.strftime("%Y-%m"), {"month": d.strftime("%Y-%m"), "buys": 0.0, "sells": 0.0})
        mo["buys" if o["type"] == "BUY" else "sells"] += o["value"]
        yr = yearly.setdefault(d.year, {"year": d.year, "buys": 0.0, "sells": 0.0, "n_buys": 0, "n_sells": 0})
        yr["buys" if o["type"] == "BUY" else "sells"] += o["value"]
        yr["n_buys" if o["type"] == "BUY" else "n_sells"] += 1
        bp = last_price(BENCHMARK, d)
        if bp:
            bench_units_by_day[d.isoformat()] = bench_units_by_day.get(d.isoformat(), 0.0) + (-sign) * o["value"] / bp

    # year-end values (own portfolio and the same money in the benchmark)
    ye = {}
    for y in sorted(yearly):
        if y >= end.year:
            continue
        cutoff = datetime(y, 12, 31, 23, 59)
        own = sum(_qty_in_todays_shares(orders, s, cutoff, spl.get(s)) * (last_price(s, cutoff.date()) or 0.0) for s in symbols)
        bunits = sum(u for d, u in bench_units_by_day.items() if d <= cutoff.date().isoformat())
        ye[y] = {"own": round(own, 2), "bench": round(bunits * (last_price(BENCHMARK, cutoff.date()) or 0.0), 2)}

    yearly_rows = []
    for y, r in sorted(yearly.items()):
        start, stop = date(y, 1, 1), date(y, 12, 31) if y < end.year else end
        span = (stop - start).days + 1
        net = wnet = bnet = bw = 0.0
        for d, a in flows.items():
            dd = date.fromisoformat(d)
            if start <= dd <= stop:
                net += -a
                wnet += -a * (1 - (dd - start).days / span)
        for d, u in bench_units_by_day.items():
            dd = date.fromisoformat(d)
            if start <= dd <= stop:
                bw += u * (1 - (dd - start).days / span)
        yearly_rows.append({**{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()},
                            "net_added": round(net, 2), "weighted_net": round(wnet, 2),
                            "start_own": ye.get(y - 1, {}).get("own", 0.0), "end_own": ye.get(y, {}).get("own"),
                            "start_bench": ye.get(y - 1, {}).get("bench", 0.0), "end_bench": ye.get(y, {}).get("bench"),
                            "bench_units_added": round(sum(u for d, u in bench_units_by_day.items() if start <= date.fromisoformat(d) <= stop), 6)})

    bench_px = last_price(BENCHMARK, end)
    names = {}
    for o in orders:
        names[o["symbol"]] = o["name"]
    company_flows: Dict[str, Dict[str, float]] = {}
    for o in orders:
        company_flows.setdefault(o["symbol"], {})
        d = o["ts"].date().isoformat()
        company_flows[o["symbol"]][d] = company_flows[o["symbol"]].get(d, 0.0) + (-o["value"] if o["type"] == "BUY" else o["value"])
    return {
        "names": names,
        "orders": [[o["ts"].date().isoformat(), o["symbol"], "B" if o["type"] == "BUY" else "S", o["qty"], round(o["value"], 2)] for o in orders],
        "net_qty": {sym: round(_qty_in_todays_shares(orders, sym, datetime(9999, 12, 31), spl.get(sym)), 4) for sym in sorted({o["symbol"] for o in orders})},
        "dividends": merged_dividends(folder, orders, dividend_rows(orders, divs, spl, holdings), isins),
        "company_flows": {k: [[d, round(a, 2)] for d, a in sorted(v.items())] for k, v in company_flows.items()},
        "as_of": end.isoformat(), "benchmark": BENCHMARK, "benchmark_price_at_report": bench_px,
        "flows": [[d, round(a, 2)] for d, a in sorted(flows.items())],
        "bench_units": [[d, round(u, 6)] for d, u in sorted(bench_units_by_day.items())],
        "bench_units_total": round(sum(bench_units_by_day.values()), 6),
        "net_invested": round(-sum(flows.values()), 2),
        "monthly": [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()} for _, m in sorted(monthly.items())],
        "yearly": yearly_rows, "fy": read_fy_reports(folder), "ledger": read_ledger(folder),
        "symbols_priced": len(px), "symbols": len(symbols),
    }


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    held, isins = {}, {}
    if len(sys.argv) > 3:   # optional: a groww_holdings.json snapshot, for shares received free in demergers
        snap = json.load(open(sys.argv[3], encoding="utf-8"))["holdings"]
        held = {h["symbol"]: h["quantity"] for h in snap}
        isins = {h["isin"]: h["symbol"] for h in snap if h.get("isin")}
    data = build_reports(src, holdings=held, isins=isins)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    print("wrote", dst, "orders through", data["as_of"], "net invested", data["net_invested"])
