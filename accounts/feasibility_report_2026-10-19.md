# Overseas Trading Feasibility Report v1 (2026-10-19)

Prepared by the account-readiness desk. Research and paperwork only: no account opened, no broker contacted, no money moved.
All web sources were read on **2026-10-19** unless another date is given. Source list is at the end ([S1]…).
Anything not confirmed from a primary source is marked **Unverified**, with what would confirm it.

Working assumptions used in the rupee maths (not facts, change them freely):
USD/INR = 96 (RBI reference rate was 96.29 on 18-May-2026 [S30]; no October figure retrieved), USD/KRW = 1,400, USD/JPY = 150,
average US share price = USD 50.

---

## 1. Route-level answer

**Yes.** A resident Indian individual can open an overseas brokerage account, fund it under LRS (the Liberalised Remittance Scheme, RBI's
allowance for individuals to send money abroad), and trade it through an API from the VPS. But only as a **cash account, buying and
selling listed shares, ETFs and bonds**.

| Rule | What it says | Source |
|---|---|---|
| Annual limit | USD 2,50,000 per person per financial year (April–March), all LRS purposes combined | RBI LRS FAQ Q1 [S1] |
| Investment abroad is allowed | Resident individuals may make OPI (overseas portfolio investment = buying listed foreign securities) within the LRS limit, under Schedule III of the OI Rules 2022 | RBI OI Master Direction para 1(ix)(f) [S2] |
| Opening a foreign account | No prior RBI approval needed to open/hold a foreign currency account abroad for LRS money | RBI LRS FAQ Q23 [S1] |
| **Forbidden: margin** | "Remittance from India for margins or margin calls to overseas exchanges / overseas counterparty" is not allowed | RBI LRS FAQ Q2 [S1] |
| **Forbidden: forex trading** | "Trading in foreign exchange abroad" is not allowed | RBI LRS FAQ Q2 [S1] |
| **Forbidden: derivatives** | OPI shall not be made in "any derivatives unless otherwise permitted by Reserve Bank" | OI Master Direction para 1(ix)(a)(3) [S2] |
| **Forbidden: commodities** | OPI shall not be made in "any commodities including Bullion Depository Receipts (BDRs)" | OI Master Direction para 1(ix)(a)(4) [S2] |
| Broker's own reading | IBKR: "On non-Indian markets, Indian residents are prohibited from trading futures, options or margin-based products but are permitted to trade stocks, bonds and ETFs." | IB India site [S5] |
| Idle money | Unspent foreign exchange must come back within 180 days; *income* may be retained and reinvested | RBI LRS FAQ Q4 [S1] |

**Which desks are legal by this route**

| Desk | Overseas account via LRS? | Note |
|---|---|---|
| US equity (Pool I) | **Yes** | Stocks and ETFs, cash only |
| Other countries' stocks (Japan, HK, UK, Europe, Australia) | **Yes** | Cash only. Korea: see §3 |
| Cross-market (rotating country index funds) | **Yes** | Easiest as US-listed country ETFs (EWJ, EWY, EWG, EWZ…) in a US account. No foreign exchange accounts needed |
| Gold / silver | **Only as listed ETFs** (e.g. GLD, IAU, SLV) | Physical, spot, futures and BDRs are excluded. Whether a commodity-backed ETF counts as an allowed "foreign security" or an excluded "commodity" is **Unverified**: needs CA confirmation |
| Commodities (oil, gas, metals) | **No futures.** ETFs (USO, DBC) are a grey area, same as gold ETFs | Practical route is India's MCX (§4) |
| Currencies | **No** | Forex trading abroad is barred outright |

Needs CA confirmation before money moves.

---

## 2. Broker comparison

### 2a. Who is in, who is out

| Broker | Takes Indian residents? | Usable trading API? | Verdict |
|---|---|---|---|
| **Interactive Brokers (IBKR)** | **Yes.** Special "Indian Residents (Trading overseas only)" account: cash only, 90+ markets, excludes NSE [S5] | **Yes.** TWS socket API and Web API (REST) [S8][S9] | **In** |
| **Alpaca** | **Unverified.** Says it serves "195+ countries" but publishes no list; country page says to contact support [S12][S13]. Its examples cite an Indian *citizen living in the UK*, not an Indian resident | **Yes.** REST and websocket, API keys, free paper account for anyone [S14] | **In, conditional** on Alpaca confirming India |
| Saxo | **No.** India is not on Saxo's serviced-countries list (page updated 28-Jul-2026) [S17] | Yes (OpenAPI) | **Out** |
| Vested | Yes (India-focused) | **None found.** Pricing page lists no API [S18] | **Out** for the bot. Fees: 0.25% brokerage (0.15% Premium), max USD 35 [S18] |
| INDmoney | Yes (India-focused) | **None found** [S19] | **Out** for the bot. Fees: 0.25% per trade, bank FX 0.5–1.2% [S19] |
| Firstrade | India is on its supported-regions list [S20] | **None found. Unverified**: no official API located | **Out** unless an API appears |
| Charles Schwab International | **Unverified.** Schwab's site blocked automated reading; secondary sources say India is eligible, minimum given as USD 1,000 or 25,000 depending on source | **Unverified** for international accounts | **Out** until confirmed |
| moomoo / Futu | Secondary sources say India is not an accepted country | — | **Out** |
| Tiger Brokers, Webull | **Unverified** | Tiger has an OpenAPI | Not pursued |

### 2b. Detail for the two that matter

| | IBKR (Indian-resident overseas account, IBKR Pro) | Alpaca |
|---|---|---|
| Account minimum | None to open [S6]. **USD 500** equity needed to subscribe to market data on the overseas-only Indian account [S5] | None. Fund from USD 1 [S12] |
| Account type | Cash only for Indian residents [S5]. LRS-compliant by design | **"All accounts are opened as limited purpose margin accounts"** [S11]. Must set `max_margin_multiplier = "1"` and `no_shorting = true` via the account-configuration API [S15] so the account cannot borrow. LRS compliance rests on that setting |
| API | (a) TWS API: socket connection to TWS or IB Gateway running on the VPS [S8]. Gateway can auto-restart daily, but **needs a manual login with 2FA once a week (Sunday, after the Saturday reset)** [S10]. (b) Web API: retail users authenticate through the Client Portal Gateway, a small Java program, with username and password [S9]. Rate limit 10 requests/second. Brief nightly outage around 01:00 local exchange-region time [S9] | REST and websocket with API key and secret. No gateway, so fully unattended |
| IP allow-listing | TWS/Gateway has a trusted-IP setting for who may connect to it. The Gateway runs on the VPS itself, so the bot connects to localhost. Whether IBKR can restrict *logins* to the VPS IP is **Unverified** | **Unverified**: no IP allow-list found in docs |
| Paper account | Yes, free, but the live account must be **open and funded** and on IBKR Pro [S9] | Yes, free, open to anyone globally [S14] |
| Fractional shares | US, Canadian and European stocks/ETFs [S7]. **Unverified** for Indian-resident accounts | Yes, 2,000+ US equities, by quantity or by USD amount [S16] |
| Markets | US, Canada, UK, Europe, Japan, HK, Singapore, Australia, Korea and more [S3]. Korea has a catch: see §3 | US stocks and ETFs only |
| Gold/silver/FX/futures | Not for Indian residents (stocks, bonds and ETFs only) [S5] | Not relevant |
| US commission | **Tiered:** USD 0.0035/share, **min USD 0.35**, max 1% of trade value, plus exchange, clearing (USD 0.0002/share) and pass-through fees. **Fixed:** USD 0.005/share, **min USD 1.00**, max 1% [S3]. "IBKR Lite" (USD 0) is for US residents only [S4] | USD 0 commission [S11]. Regulatory fees on sells only |
| Other markets (IBKR) | Japan fixed 0.08%, min JPY 80. Korea tiered 0.06%, min KRW 4,000 (no fixed plan). HK fixed 0.08%, min HKD 18. Australia fixed 0.08%, min AUD 6. UK tiered 0.05%, min GBP 1 [S3] | — |
| US regulatory fees | SEC fee USD 0.0000206 × value of sells. FINRA CAT USD 0.000003/share. FINRA TAF shown as "USD 0.00 × quantity sold" on the page as read [S3] | Same SEC/FINRA fees, charged on sells [S16b] |
| Currency conversion | 0.20 basis points (0.002%), **min USD 2 per conversion** [S3b] | Deposits must already be USD [S13b]. Your bank does the conversion |
| Platform, custody, inactivity fees | None: "no added spreads, ticket charges, platform fees, custody fees or account minimums" [S6] | None found. Full fee schedule **Unverified** |
| Market data | Optional. The bot's signals use yfinance. IBKR's US snapshot bundle is USD 10/month, waived once USD 30 of commission is generated in the month [S3c] | Free real-time (IEX) feed |
| Withdrawal | Two free withdrawals per calendar month, then USD wire USD 10 [S3d] | FAQ dated Nov 2022: no withdrawal fee, USD only [S13c]. International wire fee **Unverified**. Secondary sources say USD 50 |

### 2c. All-in round trip per trade: commission plus exchange/regulatory fees

This excludes the bid-ask spread (the same at every broker) and the one-time remittance cost (§5). Assumes a USD 50 share.

**Why the small rows matter:** a desk's positions are a percentage of its capital (ARCHITECTURE.md → FUND ALLOCATION). A ₹50,000 desk
holding 5–10 names takes **₹5,000–₹10,000 positions**. Pool I's paper books start at USD 1,042.26, about ₹1 lakh, with several
names at once (`reporting/pool_i.py`), so its real positions are in the ₹10k–₹25k rows, not the ₹1L row.

| Position | IBKR US Tiered | IBKR US Fixed | Alpaca US | IBKR Japan (fixed) | IBKR Korea (tiered, incl. 0.20% sell tax) |
|---|---|---|---|---|---|
| ₹10,000 | 0.69% | 1.92% | ~0.003% | 1.02% | 5.7% |
| ₹25,000 | 0.28% | 0.77% | ~0.003% | 0.41% | 2.4% |
| **₹50,000** | **0.15%** | 0.39% | ~0.003% | 0.20% | 1.30% |
| **₹1,00,000** | **0.08%** | 0.19% | ~0.003% | 0.16% | 0.75% |
| **₹3,00,000** | **0.04%** | 0.07% | ~0.003% | 0.16% | 0.38% |

How the numbers were built:
- **IBKR Tiered** = 2 × [max(USD 0.35, 0.0035 × shares) + exchange fee + 0.0002 × shares] + SEC fee. The exchange fee is taken as
  **~USD 0.003/share** for orders that take liquidity. That figure is **Unverified**: it varies by venue and IBKR's venue table was not
  parsed. Orders that add liquidity can earn a rebate instead.
- **Korea**: the securities transaction tax is **0.20% on sells** for both KOSPI and KOSDAQ in 2026. On KOSPI it is 0.05% plus a 0.15%
  rural special tax [S24]. EY is a secondary source; KRX/NTS primary text was not read, so this is **Unverified**. Korean exchange and
  clearing fees are not included (**Unverified**, small).
- **Japan**: no transaction tax is assumed (**Unverified**). TSE shares normally trade in 100-share lots. Many Japanese stocks cost
  more than ₹1L per lot, and whether IBKR allows odd lots is **Unverified**. This may matter more than the commission.
- **Currency conversion** (IBKR, Korea and Japan): USD 2 minimum per conversion. Converting per trade adds 0.38% at ₹50k but only 0.06%
  at ₹3L. **Convert a desk's money once, in one block**, and keep it in KRW or JPY cash.

**Bottom line on cost:** at ₹50k and above, IBKR Tiered is cheap for US trades (≤0.15% round trip). Below about ₹25k per position, the
USD 0.35 minimum dominates, and Alpaca's USD 0 commission is meaningfully cheaper, if Alpaca accepts Indian residents.

### 2d. Cost model for the paper books (paper ≡ live)

Shape mirrors `swing_research/broker_costs.py`: a flat part per order plus a percentage part.
Pool I currently models **USD 0 commission** (`reporting/pool_i.py`). Moving it onto an IBKR cost model is a cost-model change. Under
ARCHITECTURE.md → PAPER TO LIVE, that means the paper record restarts from the change.

```
IBKR_US_TIERED   per order: max(0.35, 0.0035*shares) capped at 1% of value; + ~0.003*shares exchange (Unverified); + 0.0002*shares clearing
                 sells only: + 0.0000206*value (SEC) + 0.000003*shares (CAT)
IBKR_US_FIXED    per order: max(1.00, 0.005*shares) capped at 1% of value; sells: + SEC fee
IBKR_KR          per order: max(KRW 4000, 0.0006*value); sells only: + 0.0020*value tax
IBKR_JP          per order: max(JPY 80, 0.0008*value)        (fixed plan)
IBKR_FX          per conversion: max(USD 2, 0.000002*value)
ALPACA_US        per order: 0; sells only: SEC + FINRA TAF (TAF rate Unverified)
```

---

## 3. Korea

- **IBKR started offering Korea Exchange (KRX) stocks on 7-May-2026.** The launch covers 2,700+ securities, with API access, through a
  Samsung Securities omnibus arrangement. Nextrade (a Korean alternative venue) was added later [S21][S22].
- **The catch:** IBKR's own Korea page says **"KRX is not available to residents of Korea, or clients of IBIN."** [S22] IBIN is
  Interactive Brokers India Pvt Ltd. Indian residents apply for the overseas-only account through IB India's website [S5]. Whether that
  account is an "IBIN client" for this rule is **Unverified**. One secondary source says India is excluded; IBKR's pages do not say so
  explicitly. **Treat Korea through IBKR as "probably not" until IBKR confirms in writing.** Two ways to confirm: Suraj asks IBKR support
  before opening, or he checks User → Settings → Trading Permissions → Stocks → "Korea (Republic of)" after opening.
- **Korea's own rules allow it.** Since 14-Dec-2023 a foreign individual can open a Korean account with just a passport number; prior
  registration was abolished [S23]. Omnibus accounts at foreign brokers were eased in 2025–26 [S23b]. So the barrier is the broker,
  not Korean law.
- **Other brokers:** none of the others in §2 offers KRX to Indian residents (Saxo is out; the rest are US-only).
- **Nearest legal alternatives, in order:**
  1. **US-listed Korea ETF (iShares MSCI South Korea, EWY)** through IBKR or Alpaca. It tracks the Korean index, not single stocks. That
     suits an index-level or cross-market desk, not a stock-picking desk.
  2. A Korean broker's non-resident account using a passport. Whether any such broker offers an English trading API to retail
     non-residents is **Unverified**, and the LRS remittance route would be the same.
  3. If neither works, a Korean stock-picking desk has **no practical route today**.

---

## 4. Gold, silver, currencies, commodities

| Asset | Abroad (LRS) | In India | Practical route |
|---|---|---|---|
| **Gold** | Physical gold, spot gold (IBKR's "US Spot Gold"), gold futures and BDRs: **not allowed** (commodities and derivatives are excluded from OPI) [S2][S5]. US-listed gold ETFs (GLD, IAU): probably allowed as listed securities, **CA to confirm** | NSE gold ETFs; **MCX gold futures/options**; existing Sovereign Gold Bonds on the secondary market (no new SGB issue since Feb 2024, per secondary sources, **Unverified**) | **Kite (Zerodha), already integrated.** MCX gold futures or NSE gold ETFs. No LRS, no TCS, no foreign-asset reporting, rupee P&L |
| **Silver** | Same as gold (SLV abroad, CA to confirm) | NSE silver ETFs; MCX silver futures | **Kite.** MCX silver or NSE silver ETFs |
| **Commodities** (crude, natural gas, base metals) | Futures: **not allowed**. Futures-based ETFs (USO, DBC): grey area, CA to confirm | **MCX futures**. Zerodha brokerage: 0.03% or ₹20 per executed order, whichever is lower [S25] | **Kite / MCX** |
| **Currencies** | **Not allowed** ("trading in foreign exchange abroad" is barred) [S1] | Exchange-traded rupee currency futures may be used **only to hedge a real exposure you actually have**. Below a threshold you need not *produce* documents, but you must still *have* the exposure [S26]. On **10-Oct-2026** RBI cut that threshold from USD 100 million to **USD 5 million** [S27]. Zerodha charges 0.03% or ₹20 [S25] | **None for a speculative desk.** Cross-currency pairs that don't involve INR (EUR-USD etc. on NSE) are **Unverified** under the same rules. Currency ETFs abroad (e.g. UUP) are **Unverified**/CA |

**Recommendation:** run gold, silver and commodity desks on **Kite/MCX in India**, not abroad. It is legal, simpler, already has a working
API in this system, and avoids LRS, TCS, Schedule FA and FX costs. Do not build a currency desk unless a CA confirms a lawful route.

---

## 5. Money in and money out

| Item | Fact | Source |
|---|---|---|
| LRS limit | USD 2,50,000 per FY per person | [S1] |
| **TCS** (tax collected at source = the bank keeps part of what you send, as an advance against your income tax) | Investment remittances: **nil up to ₹10 lakh** in the FY, then **20% on the amount above ₹10 lakh**. Law: s.506 of Income Tax Act 2025 (formerly s.206C(1G)), as amended by Finance Act 2026, from 1-Apr-2026 | SC Bank notice [S28] |
| Getting TCS back | Shows in Form 26AS. Claimed against that year's tax in the ITR; any excess is refunded. **Unverified** from a primary ITD page (standard mechanism) | — |
| Form at bank | Form A2 plus LRS declaration; purpose code **S0001** (portfolio investment abroad in equity shares). Code taken from an IBKR webinar (2020) [S29]: **confirm with the bank** | [S29] |

**Bank cost, worked example (HDFC, official schedule [S31])**

HDFC's own charges: commission ₹500 (up to USD 500) or ₹1,000 (above USD 500), plus 18% GST. GST on the currency converted: 0.18% up to
₹1 lakh (min ₹45, max ₹180); from ₹1L to ₹10L it is ₹180 + 0.09%; above ₹10L it is ₹990 + 0.018%. Correspondent and intermediary bank
charges are **not included**.

Below, the **FX markup** (how much worse than mid-market the bank's rate is) is an **assumption of 0.5%**. INDmoney's own page puts bank
conversion at 0.5–1.2% [S19]; a secondary rate sheet from 8-Oct showed SBI's TT spread near ₹0.85 and ICICI's near ₹3.03 per dollar.
The intermediary-bank deduction of **USD 15** is also an assumption (**Unverified**; secondary sources say USD 10–30).

| Remit | HDFC commission + GST | GST on conversion | FX markup @0.5% | Intermediary ~USD 15 | **Total** | **% of amount** |
|---|---|---|---|---|---|---|
| ₹50,000 | ₹1,180 | ₹90 | ₹250 | ₹1,440 | ₹2,960 | **5.9%** |
| ₹1,00,000 | ₹1,180 | ₹180 | ₹500 | ₹1,440 | ₹3,300 | **3.3%** |
| ₹3,00,000 | ₹1,180 | ₹360 | ₹1,500 | ₹1,440 | ₹4,480 | **1.5%** |
| ₹5,00,000 | ₹1,180 | ₹540 | ₹2,500 | ₹1,440 | ₹5,660 | **1.1%** |

These are one-time costs per remittance, not per trade. The flat parts dominate small remittances, so **fund each desk in one larger
transfer, not in instalments.** A second bank quote (SBI or ICICI) was not obtained from a primary source: **Unverified**.

| Step | Detail |
|---|---|
| Time to fund | Bank wire usually 1–3 business days. **Unverified**: IBKR/Alpaca funding-time pages were not read |
| Money back to India | Withdraw from the broker by USD wire to your Indian bank account (IBKR: 2 free per month, then USD 10 [S3d]). Your bank converts at its buying rate (another FX markup, assume ~0.5%) and may charge an inward fee (**Unverified**) |
| Withdrawals and LRS | Money coming back does not count against the LRS limit. **Unverified** from primary text |

---

## 6. Tax and compliance

| Topic | Position | Source / status |
|---|---|---|
| Short-term gains (held ≤24 months) on foreign shares | Added to income and taxed at your slab rate | Secondary [S32]; matches the repo's `us_equity_costs.py` |
| Long-term gains (>24 months) | **12.5%, no indexation**, for transfers on or after 23-Jul-2024 (Finance (No.2) Act 2024) | Deloitte / ITD guidance via search [S32]. **The repo's `swing_research/us_equity_costs.py` still uses 20%; that looks out of date.** Low impact for swing desks, but it should be corrected |
| New Act | Income Tax Act 2025 in force from 1-Apr-2026. Section numbers changed; rates reported unchanged | [S28]; secondary for "rates unchanged" |
| Dividends | Taxed in India at your slab rate | Standard |
| US withholding on dividends | **25%** under the India–US tax treaty (Article 10), once **Form W-8BEN** is on file with the broker. 30% without it | **Unverified**: IRS treaty table PDF could not be read |
| Foreign tax credit | Claim US (or Korean/Japanese) tax withheld against Indian tax by filing **Form 67** on or before the ITR due date (Rule 128) | ITD [S33] |
| Korea / Japan dividend withholding | Korea 15% under the India–Korea treaty (secondary); Japan not checked | **Unverified** |
| Return form | ITR-1 and ITR-4 have no Schedule FA, so you need ITR-2 or ITR-3 | ITD [S33] |
| **Schedule FA** (foreign assets list in the ITR) | Every foreign account and holding must be listed, even if small. Reporting period for FA is the calendar year | Disclosure duty: ITD [S33]. Calendar-year period: **Unverified** (ITD PDF unreadable) |
| **Penalty (Black Money Act ss.42/43)** | ₹10 lakh per default for missing or inaccurate disclosure. Relief: no penalty if the total of non-property foreign assets is **≤ ₹20 lakh** (Finance (No.2) Act 2024, from 1-Oct-2024). **Disclosure is still mandatory** | Secondary [S34]. **Unverified** from the Act text |
| US estate tax | US shares held by a non-US person may be exposed to US estate tax above USD 60,000; India and the US have no estate-tax treaty | **Unverified**: not researched in depth. Raise with the CA before a desk exceeds about ₹50 lakh |
| Idle cash abroad | The 180-day rule for "unspent" foreign exchange [S1]: whether uninvested cash in a broker account counts is unclear | **CA to confirm** |

**This section needs Suraj's CA's confirmation before any money moves.**

---

## 7. Recommendation and decisions for Suraj

**Recommendation: open one Interactive Brokers account** of the "Indian Residents (Trading overseas only)" type: cash account, IBKR Pro,
Tiered pricing.

- It is the only broker found that accepts Indian residents, has a server-usable API and a paper account, and reaches the US, Japan,
  Hong Kong, UK, Europe and Australia from one account.
- That unlocks the **US desk (Pool I)**, any **single-country stock desk** outside Korea, and the **cross-market desk** (cheapest as
  US-listed country ETFs).
- **Gold, silver and commodities** go on **Kite/MCX**, which you already have. **Currencies**: no desk.
- **Korea** stays open until IBKR confirms Indian residents can trade KRX (§3).
- **Alpaca** is a possible second account **only for the US desk, and only if** (a) Alpaca confirms it accepts Indian residents, and
  (b) the desk's positions stay under about ₹25k each, where Alpaca's USD 0 commission beats IBKR's USD 0.35 minimum.

**What it costs to start (IBKR):**
- Account: ₹0.
- First remittance: about ₹3,300 on ₹1 lakh, or about ₹5,700 on ₹5 lakh (HDFC example, §5).
- Monthly fees: none.
- Optional US market data: USD 10/month, waived once commission reaches USD 30.
- Keep at least USD 500 in the account if you want IBKR market data.
- No TCS while total remittances stay under ₹10 lakh in the FY.

**Decisions:**

| # | Decision | My recommendation | Why |
|---|---|---|---|
| 1 | Approve IBKR as the overseas broker? | **Yes** | Only option that ticks every box (§2). Ask IBKR about Korea and fractional shares first (one support message) |
| 2 | Minimum position size for overseas desks | Set a **paper-and-live notional floor of about ₹25,000 per position** on IBKR, or use Alpaca for the US desk | Below ₹25k, IBKR's USD 0.35 minimum costs 0.3–0.7% per round trip. A floor must go into paper too, and so must the IBKR cost model (§2d). Pool I's paper record restarts from that change |
| 3 | How much to send, and how | **One remittance per desk, ₹1L or more. Keep the FY total under ₹10L** | Flat bank fees are 3–6% on small transfers. Above ₹10L, 20% TCS locks up cash until your refund |
| 4 | Gold, silver and commodity desks: India (Kite/MCX) or abroad? | **India** | Legal, already integrated, no LRS/TCS/Schedule FA, rupee P&L |
| 5 | CA sign-off before the first rupee goes out | **Yes**: LTCG rate, US withholding, Schedule FA, idle-cash rule, gold/commodity ETFs abroad | Penalty exposure (₹10L per Schedule FA default) is bigger than any desk's starting capital |

---

## 8. Account-opening pack (draft): Interactive Brokers, Indian resident, overseas-only

**Before applying, Suraj asks IBKR support (+91-22-61289888 [S5]):**
1. Can an "Indian Residents (Trading overseas only)" account get Korea (KRX) stock permission?
2. Are US fractional shares available on this account type, including through the API?
3. Expected approval time.

**Documents (names only)**

| Document | Use | Source |
|---|---|---|
| PAN card | Identity and tax ID | [S5] |
| Address proof (matching the PIN code entered) | KYC | [S5] |
| Cancelled cheque or bank statement of the Indian account (with MICR, not a "000" universal MICR) | Bank linkage | [S5] |
| Financial details (net income, liquid net worth) | Application questions | [S5] |
| Passport | Identity. Recommended; not stated on the IB India page, so **Unverified** whether required | — |
| Webcam for in-person verification | IB India video check after the application | [S5] |
| Form W-8BEN | Completed inside the application to claim the India–US treaty rate | Standard |
| Form A2 plus bank's LRS declaration | At your bank, for each remittance | [S29] |
| Smartphone with IBKR Mobile | 2FA, including the weekly API re-login | [S10] |

**Steps**
1. Go to interactivebrokers.co.in → Open Account → Individual → **Indian Residents (Trading overseas only)** [S5].
2. Fill in the application: individual, **cash** account, **IBKR Pro**. Set Tiered pricing in settings after approval.
3. Trading permissions: **Stocks and ETFs only**, for US, Japan, HK, UK, Europe, Australia, plus Korea if offered. Do **not** request
   options, futures or forex.
4. Tax form step: W-8BEN, country of residence India, claim treaty benefits.
5. Upload documents and complete the webcam verification.
6. After approval, create a **deposit notification** for a USD wire in the Client Portal. Note IBKR's bank details and your reference.
7. At your bank (net banking or branch): outward remittance under LRS, Form A2, purpose code S0001 (confirm with the bank),
   beneficiary = IBKR's bank account with your reference. The name must match exactly.
8. Funds arrive (expect 1–3 business days, **Unverified**).
9. In the Client Portal, open the **paper trading account** (needs the funded live account [S9]).
10. On the VPS: install IB Gateway; turn on daily auto-restart; set API "trusted IPs" to localhost; start read-only. Running it
    headless on Linux (Xvfb/IBC) is **Unverified** in this setup and is the main session's engineering task.
11. Set a **weekly reminder (Sunday)** for the IBKR Mobile 2FA re-login [S10]. Without it, the bot stops placing orders each week.
12. The main session adds an `ibkr` entry to `BROKER_CREDENTIALS` in `deployment/live_guard.py` and stores the credentials in the
    dashboard credential store. Credentials never go in this repo.

**Timeline (estimate):** application about 1 hour → approval 1–3 business days (**Unverified**; IBKR cites "most within one business
day" in its Korea release [S21]) → funding 1–3 days → paper and API setup 1–2 days. **About 1–2 weeks end to end.**

---

## Sources (all read 2026-10-19)

- [S1] RBI, LRS FAQ (updated as on 06-Apr-2023): https://www.rbi.org.in/scripts/FS_FAQs.aspx?Id=115
- [S2] RBI, Master Direction: Foreign Exchange Management (Overseas Investment) Directions 2022: https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12381
- [S3] IBKR stock commissions (US, Japan, Korea, HK, UK, Australia; SEC/FINRA/clearing fees): https://www.interactivebrokers.com/en/pricing/commissions-stocks.php
- [S3b] IBKR spot currency commissions (0.20 bp, min USD 2): https://www.interactivebrokers.com/en/pricing/commissions-spot-currencies.php
- [S3c] IBKR market data pricing (USD 500 / USD 100 minimums; snapshot bundle waiver): https://www.interactivebrokers.com/en/pricing/market-data-pricing.php
- [S3d] IBKR other fees (two free withdrawals per month; USD wire USD 10): https://www.interactivebrokers.com/en/pricing/other-fees.php
- [S4] IBKR Lite (US residents only): https://www.interactivebrokers.com/en/trading/why-ibkr-lite.php
- [S5] IB India, Indian individual account types (overseas-only = cash, 90+ markets, USD 500 data minimum; document list) and IB India footer statement on what Indian residents may trade abroad: https://www.interactivebrokers.co.in/en/accounts/individual-account-india.php and https://www.interactivebrokers.co.in/en/accounts/individual.php
- [S6] IB India home ("no … platform fees, custody fees or account minimums"): https://www.interactivebrokers.co.in/en/home.php
- [S7] IBKR fractional shares (US, Canadian, European): https://www.interactivebrokers.com/en/trading/fractional-trading.php
- [S8] IBKR TWS API introduction: https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/
- [S9] IBKR Web API, trading (retail auth via Client Portal Gateway; paper needs funded IBKR Pro live account; rate limits; nightly reset): https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/
- [S10] IBKR, Daily & Weekly Reauthentication / Auto Restart: https://www.interactivebrokers.com/docs/tws-api/doc/tws-settings/daily-weekly-reauthentication and https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm (from search summary)
- [S11] Alpaca International ("limited purpose margin accounts"; USD 0 commission; from USD 1): https://alpaca.markets/international
- [S12] Alpaca, live account as non-US resident: https://alpaca.markets/learn/live-trading-account-non-us
- [S13] Alpaca, Countries Alpaca is available (Feb 2026: "contact support"): https://alpaca.markets/support/countries-alpaca-is-available
- [S13b] Alpaca, international funding (USD only, via Rapyd or USD wire): https://alpaca.markets/support/how-can-an-international-user-non-us-tax-resident-fund-their-account
- [S13c] Alpaca, withdrawal fee FAQ (Nov 2022): https://alpaca.markets/support/cost-for-withdrawing
- [S14] Alpaca, About Trading API (paper for anyone): https://docs.alpaca.markets/docs/trading-api
- [S15] Alpaca, account configurations (`max_margin_multiplier`, `no_shorting`): https://docs.alpaca.markets/reference/patchaccountconfig-1
- [S16] Alpaca, fractional trading: https://docs.alpaca.markets/docs/fractional-trading ; [S16b] regulatory fees: https://docs.alpaca.markets/docs/regulatory-fees
- [S17] Saxo, Which countries are serviced by Saxo (updated 28-Jul-2026): https://www.help.saxo/hc/en-us/articles/10611416570269-Which-countries-are-serviced-by-Saxo
- [S18] Vested pricing: https://vestedfinance.com/pricing
- [S19] INDmoney US stocks FAQ: https://www.indmoney.com/us-stocks-faq
- [S20] Firstrade international accounts: https://www.firstrade.com/accounts/international
- [S21] IBKR press release, Korean equities launch (7-May-2026): https://www.interactivebrokers.com/en/general/about/mediaRelations/5-7-26.php (from search summary)
- [S22] IBKR, Trade Korean Equities through KRX and Nextrade (disclaimer re IBIN): https://www.interactivebrokers.com/en/trading/nextrade.php
- [S23] Korea FSC, "Investing in Domestic Capital Markets Made Easier for Foreign Investors" (13-Dec-2023): https://fsc.go.kr/eng/pr010101/81237 (from search summary) ; [S23b] Korea Herald / JoongAng on omnibus easing (secondary)
- [S24] EY Global Tax News, Korea 2026 tax reform (STT rates), secondary: https://globaltaxnews.ey.com/news/2026-0126-korea-enacts-2026-tax-reform-bill
- [S25] Zerodha charges (commodity and currency futures): https://zerodha.com/charges
- [S26] RBI press release, ETCD clarification (04-Apr-2024): https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=57628 (from search summary)
- [S27] RBI press release (10-Oct-2026), ETCD threshold USD 100M → USD 5M: https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63783
- [S28] South Indian Bank, notice on revised TCS from 1-Apr-2026 (s.506 IT Act 2025): https://www.sc.bank.in/important-information/others/lrs-amendment-in-tcs/
- [S29] IBKR webinar "Investing in Global Markets through Interactive Brokers for Indian Residents" (2020; Form A2, purpose code S0001): https://www.interactivebrokers.com/webinars/2020-WB-3329-InvestinginGlobalMarketsthroughInteractiveBrokersforIndianResidents.pdf (from search summary)
- [S30] MSEI, RBI reference rate archive (18-May-2026: 96.2948): https://www.msei.in/markets/currency/historical-data/rbireferenceratearchives.aspx (from search summary)
- [S31] HDFC Bank RemitNow fees and charges: https://www.hdfc.bank.in/remitnow/remitnow-fees-and-charges
- [S32] Deloitte, India Budget 2024 capital gains changes (secondary): https://www2.deloitte.com/content/dam/Deloitte/in/Documents/tax/in-tax-gbt-india-budget-2024-changes-to-capital-gains-noexp.pdf
- [S33] Income Tax Dept: Form 67 / Rule 128, Schedule FA guide, ITR applicability: https://www.incometax.gov.in/iec/foportal/help/statutory-forms/popular-form/form67-um , https://www.incometaxindia.gov.in/w/rule-128-1 , https://www.incometax.gov.in/iec/foportal/help/individual/return-applicable-1 (from search summaries)
- [S34] Black Money Act ₹20 lakh relief, secondary: https://taxguru.in/income-tax/cbdt-amends-black-money-act-rs-20-lakh-asset-exemption.html , https://www.business-standard.com/amp/finance/personal-finance/cbdt-eases-black-money-rules-why-experts-are-calling-it-a-pragmatic-move-125090100946_1.html
