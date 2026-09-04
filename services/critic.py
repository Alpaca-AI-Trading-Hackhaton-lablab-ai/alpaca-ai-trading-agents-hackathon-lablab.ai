"""Deterministic critic: fills → bounded score bias. Not an LLM."""

from __future__ import annotations

BIAS_CAP = 1.0
LOOKBACK = 8
AGENT_ID = "critic"


def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _order_fields(data):
    order = getattr(data, "order", None)
    if order is None and isinstance(data, dict):
        order = data.get("order") or data
    if order is None:
        return {}
    if isinstance(order, dict):
        return order
    return {
        "symbol": getattr(order, "symbol", None),
        "side": getattr(order, "side", None),
        "filled_avg_price": getattr(order, "filled_avg_price", None),
        "filled_qty": getattr(order, "filled_qty", None),
        "qty": getattr(order, "qty", None),
    }


def _attr(data, name, default=None):
    if data is None:
        return default
    if isinstance(data, dict):
        return data.get(name, default)
    return getattr(data, name, default)


def record_fill(
    symbol,
    side=None,
    price=None,
    qty=None,
    pnl=None,
    atr=None,
):
    """Write one critic row. Fail-closed: never raises."""
    from services import logs

    symbol = str(symbol or "").upper()
    if not symbol:
        return None
    payload = {
        "symbol": symbol,
        "side": str(side).split(".")[-1].lower() if side is not None else None,
        "price": _num(price),
        "qty": _num(qty),
        "pnl": _num(pnl),
        "atr": _num(atr),
    }
    try:
        return logs.record(
            agent_id=AGENT_ID,
            kind="fill",
            symbol=symbol,
            status="ok",
            summary=f"{symbol} fill",
            payload=payload,
        )
    except Exception:
        return None


def record_from_update(data):
    order = _order_fields(data)
    symbol = order.get("symbol")
    price = _attr(data, "price") or order.get("filled_avg_price") or order.get("price")
    qty = (
        _attr(data, "qty")
        or order.get("filled_qty")
        or order.get("qty")
        or order.get("filled_qty")
    )
    pnl = _attr(data, "pnl") or _attr(data, "realized_pl") or order.get("pnl")
    atr = _attr(data, "atr") or order.get("atr")
    return record_fill(
        symbol,
        side=order.get("side"),
        price=price,
        qty=qty,
        pnl=pnl,
        atr=atr,
    )


def _row_pnl(row):
    payload = (row or {}).get("payload") or {}
    pnl = _num(payload.get("pnl"))
    if pnl is not None:
        return pnl
    price = _num(payload.get("price"))
    atr = _num(payload.get("atr"))
    if price is None or atr is None or atr <= 0:
        return None
    # No entry: treat fill vs ATR as 0 (neutral). Caller skips None.
    return None


def score_bias(symbol):
    """Last N critic fills for the symbol → clamped [-BIAS_CAP, BIAS_CAP].

    No rows / no db / errors → 0 (fail-closed).
    """
    symbol = str(symbol or "").upper()
    if not symbol:
        return 0.0
    try:
        from services import logs

        rows = logs.query_logs(symbol=symbol, agent=AGENT_ID, limit=LOOKBACK)
    except Exception:
        return 0.0
    wins = 0
    losses = 0
    for row in rows or []:
        pnl = _row_pnl(row)
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    total = wins + losses
    if not total:
        return 0.0
    raw = (wins - losses) / total
    return max(-BIAS_CAP, min(BIAS_CAP, raw))
