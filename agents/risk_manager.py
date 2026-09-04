"""Risk node — deterministic position sizing. Never an LLM.

Base % from sentiment confidence, then scaled by setup-score conviction and
ATR as a percent of price. Domain logic above; Agent wrapper at the bottom.
"""

from agents.base import Agent
from agents.decision_agent import score_setup

_REF_ATR_PCT = 0.01
_MAX_EQUITY_PCT = 0.10


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _num(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def signed_qty(positions, symbol):
    """+qty long, −qty short. Missing position → 0."""
    symbol = str(symbol or "").upper()
    if not symbol:
        return 0.0
    for pos in positions or []:
        if str((pos or {}).get("symbol") or "").upper() != symbol:
            continue
        qty = _num((pos or {}).get("qty"), 0.0) or 0.0
        side = str((pos or {}).get("side") or "").lower()
        if side in ("short", "sell") or qty < 0:
            return -abs(qty)
        return abs(qty)
    return 0.0


def calculate_risk(
    account_balance,
    confidence,
    atr=None,
    price=None,
    scores=None,
    max_credit=None,
    positions=None,
    symbol=None,
    intended_action=None,
):
    account_balance = float(account_balance or 100000)
    confidence = float(confidence or 0)
    scores = scores or {"buy": 0.0, "sell": 0.0}
    existing = signed_qty(positions, symbol)
    blocked_reason = None
    action = str(intended_action or "").upper()
    if action == "BUY" and existing > 0:
        blocked_reason = "already long"
    elif action == "SELL" and existing < 0:
        blocked_reason = "already short"

    if confidence >= 85:
        risk_percent = 0.03
        risk_level = "HIGH"
    elif confidence >= 75:
        risk_percent = 0.02
        risk_level = "MEDIUM"
    else:
        risk_percent = 0.01
        risk_level = "LOW"

    buy = float(scores.get("buy") or 0)
    sell = float(scores.get("sell") or 0)
    confidence_factor = _clamp(abs(buy - sell) / 5.0, 0.5, 1.5)

    volatility_factor = 1.0
    atr_n = _num(atr)
    price_n = _num(price)
    if atr_n is not None and price_n and price_n > 0 and atr_n > 0:
        atr_pct = atr_n / price_n
        volatility_factor = _clamp(_REF_ATR_PCT / atr_pct, 0.5, 1.5)

    if blocked_reason:
        position_size = 0.0
    else:
        position_size = account_balance * risk_percent * confidence_factor * volatility_factor
        position_size = min(position_size, account_balance * _MAX_EQUITY_PCT)
        cap = _num(max_credit)
        if cap is not None and cap > 0:
            position_size = min(position_size, cap)
        position_size = round(position_size, 2)

    max_loss = round(position_size * 0.05, 2)
    take_profit = round(position_size * 0.10, 2)

    return {
        "account_balance": account_balance,
        "confidence": confidence,
        "risk_level": risk_level,
        "position_size": position_size,
        "max_loss": max_loss,
        "take_profit": take_profit,
        "confidence_factor": round(confidence_factor, 4),
        "volatility_factor": round(volatility_factor, 4),
        "existing_qty": existing,
        "blocked_reason": blocked_reason,
    }


class RiskAgent(Agent):
    node = "risk"

    def run(self, ctx):
        ms = ctx.get("market_state") or {}
        return calculate_risk(
            (ctx.get("account") or {}).get("equity", 100000),
            (ctx.get("sentiment") or {}).get("confidence", 0),
            atr=ms.get("atr"),
            price=ms.get("price"),
            scores=score_setup(ms),
            max_credit=ctx.get("max_credit"),
            positions=ctx.get("positions") or [],
            symbol=ctx.get("symbol") or ms.get("symbol"),
        )

    def message(self, out):
        return self._err(out) or f"{out.get('risk_level')} · ${out.get('position_size')}"
