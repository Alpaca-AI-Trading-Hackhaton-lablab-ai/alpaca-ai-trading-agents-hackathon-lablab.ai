"""Pure bracket / algo-plan helpers. No broker I/O, no LLM."""

from __future__ import annotations


def _num(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _entry_price(plan):
    entry = (plan or {}).get("entry") or {}
    return _num(entry.get("price"))


def _side(plan):
    return str((plan or {}).get("side") or "").lower()


def _notional(plan):
    size = (plan or {}).get("size") or {}
    return _num(size.get("notional"), 0.0) or 0.0


def seed_plan(decision, last_price, atr=None, fees_frac=0.0):
    """BUY/SELL + notional from the decision; SL = 1 ATR, TP1 = 2R. HOLD → None."""
    action = str((decision or {}).get("action") or "HOLD").upper()
    symbol = (decision or {}).get("symbol") or "SPY"
    notional = _num((decision or {}).get("position_size"), 0.0) or 0.0
    entry = _num(last_price)
    if action not in ("BUY", "SELL") or entry is None or entry <= 0:
        return None
    side = "buy" if action == "BUY" else "sell"
    atr_n = _num(atr)
    if atr_n is None or atr_n <= 0:
        atr_n = entry * 0.01
    if side == "buy":
        sl = round(entry - atr_n, 4)
        tp = round(entry + 2 * atr_n, 4)
    else:
        sl = round(entry + atr_n, 4)
        tp = round(entry - 2 * atr_n, 4)
    return {
        "symbol": str(symbol).upper(),
        "side": side,
        "size": {"notional": round(notional, 2)},
        "entry": {"role": "entry", "type": "market", "price": round(entry, 4)},
        "tps": [{"role": "tp", "type": "limit", "price": tp, "size_pct": 100}],
        "sl": {
            "role": "sl",
            "type": "stop",
            "price": sl,
            "mode": "fixed",
        },
        "break_even": {"on": "tp1_fill", "fees_frac": float(fees_frac or 0)},
    }


def r_multiple(plan):
    entry = _entry_price(plan)
    sl = _num(((plan or {}).get("sl") or {}).get("price"))
    tps = (plan or {}).get("tps") or []
    tp = _num((tps[0] or {}).get("price")) if tps else None
    if None in (entry, sl, tp) or entry == sl:
        return None
    return abs(tp - entry) / abs(entry - sl)


def break_even_price(plan, avg_entry=None):
    fees = _num(((plan or {}).get("break_even") or {}).get("fees_frac"), 0.0) or 0.0
    entry = _num(avg_entry) if avg_entry is not None else _entry_price(plan)
    if entry is None:
        return None
    if _side(plan) == "sell":
        return round(entry * (1 - fees), 4)
    return round(entry * (1 + fees), 4)


def max_loss(plan):
    entry = _entry_price(plan)
    sl = _num(((plan or {}).get("sl") or {}).get("price"))
    notional = _notional(plan)
    if None in (entry, sl) or entry <= 0:
        return None
    return round(notional * abs(entry - sl) / entry, 2)


def validate_plan(plan):
    errors = []
    if not plan or not isinstance(plan, dict):
        return {"ok": False, "errors": ["missing plan"], "r_multiple": None, "max_loss": None}
    side = _side(plan)
    if side not in ("buy", "sell"):
        errors.append("side must be buy or sell")
    entry = _entry_price(plan)
    if entry is None or entry <= 0:
        errors.append("entry price required")
    sl = (plan.get("sl") or {})
    sl_price = _num(sl.get("price"))
    sl_mode = str(sl.get("mode") or "fixed").lower()
    tps = list(plan.get("tps") or [])
    if sl_mode == "fixed" and sl_price is None:
        errors.append("stop loss price required")
    if entry is not None and sl_price is not None:
        if side == "buy" and sl_price >= entry:
            errors.append("long SL must be below entry")
        if side == "sell" and sl_price <= entry:
            errors.append("short SL must be above entry")
    pcts = []
    prices = []
    for i, tp in enumerate(tps):
        price = _num((tp or {}).get("price"))
        pct = _num((tp or {}).get("size_pct"), 0.0) or 0.0
        if price is None:
            errors.append(f"tp{i + 1} price required")
        else:
            prices.append(price)
            if entry is not None:
                if side == "buy" and price <= entry:
                    errors.append(f"tp{i + 1} must be above entry")
                if side == "sell" and price >= entry:
                    errors.append(f"tp{i + 1} must be below entry")
        pcts.append(pct)
    if tps and abs(sum(pcts) - 100) > 0.01:
        errors.append("TP size_pct must sum to 100")
    if len(prices) >= 2:
        if side == "buy" and any(prices[i] >= prices[i + 1] for i in range(len(prices) - 1)):
            errors.append("long TPs must be strictly increasing")
        if side == "sell" and any(prices[i] <= prices[i + 1] for i in range(len(prices) - 1)):
            errors.append("short TPs must be strictly decreasing")
    if _notional(plan) <= 0 and not ((plan.get("size") or {}).get("qty")):
        errors.append("size notional or qty required")
    r = r_multiple(plan) if not errors else None
    return {
        "ok": not errors,
        "errors": errors,
        "r_multiple": None if r is None else round(r, 4),
        "max_loss": max_loss(plan),
    }


def would_call(plan, trigger=None):
    """Full call list a dry-run would make. Extra TPs / trailing are emulated."""
    if not plan:
        return []
    side = _side(plan)
    symbol = plan.get("symbol")
    notional = _notional(plan)
    tps = list(plan.get("tps") or [])
    sl = plan.get("sl") or {}
    first = tps[0] if tps else {}
    calls = []
    sl_mode = str(sl.get("mode") or "fixed").lower()
    trailing_only = sl_mode == "trailing" and not tps
    if trailing_only:
        calls.append(
            {
                "tool": "place_stock_order",
                "type": "trailing_stop",
                "symbol": symbol,
                "side": side,
                "notional": notional,
                "trail_percent": sl.get("trailing_distance_pct") or sl.get("trailing_distance"),
                "emulated": bool(sl.get("trailing_start") or sl.get("improve_only")),
            }
        )
    else:
        call = {
            "tool": "place_stock_order",
            "order_class": "bracket",
            "symbol": symbol,
            "side": side,
            "notional": notional,
            "entry": (plan.get("entry") or {}).get("type", "market"),
        }
        if first.get("price") is not None:
            call["take_profit"] = {"limit_price": first["price"]}
        if sl.get("price") is not None:
            call["stop_loss"] = {"stop_price": sl["price"]}
        if sl_mode == "trailing":
            call["emulated"] = True
        calls.append(call)
        for tp in tps[1:]:
            calls.append(
                {
                    "tool": "place_stock_order",
                    "type": "limit",
                    "symbol": symbol,
                    "side": "sell" if side == "buy" else "buy",
                    "qty": "<remainder>",
                    "limit_price": (tp or {}).get("price"),
                    "size_pct": (tp or {}).get("size_pct"),
                    "emulated": True,
                }
            )
    be = plan.get("break_even") or {}
    risk = {
        "r_multiple": r_multiple(plan),
        "max_loss": max_loss(plan),
        "break_even": be.get("on") or None,
    }
    return {"would_call": calls, "risk": risk, "conditional": trigger}
