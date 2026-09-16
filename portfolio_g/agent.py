"""
Pool G's one AI agent: given a snapshot of the five majors and any
currently-held positions, asks Claude for a BUY / SELL / HOLD / AVOID
call on each coin, with a one-line reason. This is a live JUDGMENT call,
not a computed signal -- there is no walk-forward backtest behind it
(see portfolio_g/state.py's module docstring for why one isn't possible),
so the prompt itself carries every rule this book runs under.

Best-effort web search: if the installed anthropic SDK and the account's
plan support the server-side web_search tool, it is used so the model
can check for anything materially new since its training data ended,
rather than reasoning from price alone. NOT guaranteed to be available --
falls back to a plain (no-search) call on any error from enabling it,
and the response records which mode was actually used, so the record is
honest about whether live information was available for that run.
"""

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from portfolio_g.state import PORTFOLIO_G_STARTING_CAPITAL_USDT

STOP_LOSS_PCT = 0.18   # mechanical, engine-enforced -- see portfolio_g/daily.py's module docstring for why
                       # this is NOT the LLM's call: crypto can move far in a single day, so protection has
                       # to be immediate and rule-based, never waiting on the model's judgment or a schedule.
MAX_SLEEVE_PCT = 0.25  # at most a quarter of the book in any one coin, regardless of conviction


@dataclass
class CoinDecision:
    symbol: str
    action: str          # "BUY", "SELL", "HOLD", "AVOID"
    conviction: float    # 0-1, the model's own stated confidence
    reason: str


SYSTEM_PROMPT = f"""You are the sole trading judgment for Pool G, a small live paper-money crypto book \
(India-based). You are given today's snapshot for BTC, ETH, BNB, XRP and SOL, and whatever the book \
currently holds. For EACH of the five coins, decide one of:
  BUY   -- open a new position (only for a coin not currently held)
  SELL  -- close an existing position now, at the current price
  HOLD  -- keep an existing position open, unchanged
  AVOID -- stay in cash, do not open a position

Rules you must follow, not the book's engine -- read them carefully:
1. There is NO minimum holding period. If your conviction on a held coin genuinely changes, say SELL \
immediately -- do not hold a position you no longer believe in. A stop-loss is enforced mechanically by \
the trading engine on every position ({STOP_LOSS_PCT*100:.0f}% below its entry), independent of you, so \
downside protection is never your job.
2. But do weigh a real cost before saying SELL on a WINNING position: India taxes 31.2% of every \
profitable crypto trade, with NO relief for losing trades and no netting between them. Selling a small \
gain to bank it, then re-buying later on a similar view, pays that tax for no real change of position -- \
avoid trading in and out of the same view. Only sell a winner when your actual view of the coin has \
changed, not to lock in profit for its own sake.
3. Be selective. Five BUYs every run would mean this book is always fully committed; say AVOID for a coin \
you have no real edge on today.
4. Give a plain-English reason of one sentence per coin, specific to what you were shown -- never a \
generic line that could apply to any day.

Reply with ONLY a JSON array, one object per coin, in this exact shape, no other text:
[{{"symbol": "BTC", "action": "BUY", "conviction": 0.7, "reason": "..."}}, ...]"""


def build_snapshot_prompt(snapshot: dict, held: dict) -> str:
    """snapshot: {symbol: {price, chg_1d_pct, chg_7d_pct, chg_30d_pct, vs_300d_sma_pct}}.
    held: {symbol: {entry_price, days_held, unrealized_pct}} for what's currently open."""
    lines = [f"Book: {PORTFOLIO_G_STARTING_CAPITAL_USDT:.0f} USDT starting capital, "
             f"at most {MAX_SLEEVE_PCT*100:.0f}% of the book per coin.", "", "Today's snapshot:"]
    for symbol, s in snapshot.items():
        lines.append(f"  {symbol}: price {s['price']:.2f} USDT | 1d {s['chg_1d_pct']:+.1f}% | "
                     f"7d {s['chg_7d_pct']:+.1f}% | 30d {s['chg_30d_pct']:+.1f}% | "
                     f"vs 300-day average {s['vs_300d_sma_pct']:+.1f}%")
    lines.append("")
    if held:
        lines.append("Currently held:")
        for symbol, h in held.items():
            lines.append(f"  {symbol}: entered at {h['entry_price']:.2f}, held {h['days_held']} day(s), "
                         f"unrealized {h['unrealized_pct']:+.1f}%")
    else:
        lines.append("Currently held: nothing.")
    return "\n".join(lines)


def _parse_decisions(text: str, symbols: list) -> list:
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise ValueError(f"no JSON array found in the model's reply: {text[:200]!r}")
    rows = json.loads(match.group(0))
    decisions = []
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if symbol not in symbols:
            continue
        action = str(row.get("action", "")).upper()
        if action not in ("BUY", "SELL", "HOLD", "AVOID"):
            action = "AVOID"
        decisions.append(CoinDecision(symbol=symbol, action=action,
                                      conviction=max(0.0, min(1.0, float(row.get("conviction", 0.5) or 0.5))),
                                      reason=str(row.get("reason", ""))[:300]))
    seen = {d.symbol for d in decisions}
    for symbol in symbols:
        if symbol not in seen:
            decisions.append(CoinDecision(symbol=symbol, action="AVOID", conviction=0.0,
                                          reason="no decision returned for this coin"))
    return decisions


def get_decisions(snapshot: dict, held: dict, api_key: str, model: str = "claude-sonnet-5",
                  call_fn: Optional[Callable[..., tuple]] = None) -> dict:
    """Returns {"decisions": [CoinDecision, ...], "web_search_used": bool,
    "raw_reply": str}. call_fn (for tests): (prompt, api_key, model) ->
    (text, web_search_used); defaults to the real Claude call."""
    prompt = build_snapshot_prompt(snapshot, held)
    if call_fn is not None:
        text, web_search_used = call_fn(prompt, api_key, model)
    else:
        text, web_search_used = _call_claude_best_effort(prompt, api_key, model)
    decisions = _parse_decisions(text, list(snapshot.keys()))
    return {"decisions": decisions, "web_search_used": web_search_used, "raw_reply": text}


def _call_claude_best_effort(prompt: str, api_key: str, model: str) -> tuple:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    kwargs = dict(model=model, max_tokens=1500, system=SYSTEM_PROMPT,
                 messages=[{"role": "user", "content": prompt}])
    # Best-effort: try with server-side web search so the model isn't reasoning purely from
    # its training data (materially stale for anything crypto-specific by now); if the SDK
    # or the account's plan doesn't support it, fall back to a plain call rather than failing.
    try:
        response = client.messages.create(tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                                           **kwargs)
        web_search_used = True
    except Exception:
        response = client.messages.create(**kwargs)
        web_search_used = False
    text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    if not text_blocks:
        raise RuntimeError("Claude's response contained no text block")
    return "\n".join(text_blocks), web_search_used
