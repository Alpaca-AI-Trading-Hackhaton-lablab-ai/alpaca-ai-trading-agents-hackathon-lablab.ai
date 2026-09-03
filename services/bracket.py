"""Bracket preview + paper execute. Gate first; extras parked by the caller."""

from __future__ import annotations

from agents.execution_gate import evaluate_gate
from services import config
from services.alpaca_service import (
    submit_bracket_order,
    submit_market_order,
    submit_trailing_stop_order,
)
from services.bracket_plan import (
    break_even_price,
    validate_plan,
    would_call,
)


def decision_from_plan(plan, fallback=None):
    plan = plan or {}
    size = plan.get("size") or {}
    side = str(plan.get("side") or "").lower()
    action = "BUY" if side == "buy" else "SELL" if side == "sell" else None
    base = dict(fallback or {})
    if action:
        base["action"] = action
    if plan.get("symbol"):
        base["symbol"] = plan["symbol"]
    if size.get("notional") is not None:
        base["position_size"] = size["notional"]
    return base


def preview_plan(plan, trigger=None, decision=None):
    v = validate_plan(plan)
    built = would_call(plan, trigger)
    status = "DRY_RUN" if v["ok"] else "BLOCKED"
    return {
        "status": status,
        "ok": v["ok"],
        "reason": "Execution preview only" if v["ok"] else "; ".join(v["errors"]),
        "errors": v["errors"],
        "would_call": built["would_call"],
        "risk": built["risk"],
        "conditional": trigger,
        "r_multiple": v["r_multiple"],
        "max_loss": v["max_loss"],
        "break_even": break_even_price(plan),
        "decision": decision_from_plan(plan, decision),
    }


def emulated_rows(plan):
    """Extra TPs / BE / non-native trailing become motor rows (not Alpaca)."""
    plan = plan or {}
    rows = []
    side = str(plan.get("side") or "").lower()
    symbol = plan.get("symbol")
    tps = list(plan.get("tps") or [])
    sl = plan.get("sl") or {}
    sl_mode = str(sl.get("mode") or "fixed").lower()
    op = ">=" if side == "buy" else "<="
    for tp in tps[1:]:
        price = (tp or {}).get("price")
        if price is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "trigger": {"kind": "price", "op": op, "price": price},
                "plan": {
                    **plan,
                    "tps": [tp],
                    "sl": None,
                    "_emulated": "tp",
                },
            }
        )
    be = plan.get("break_even") or {}
    if be.get("on") == "tp1_fill" and tps:
        tp1 = (tps[0] or {}).get("price")
        be_px = break_even_price(plan)
        if tp1 is not None and be_px is not None:
            sl_be = {**sl, "price": be_px, "mode": "fixed"}
            rows.append(
                {
                    "symbol": symbol,
                    "trigger": {"kind": "price", "op": op, "price": tp1},
                    "plan": {**plan, "sl": sl_be, "_emulated": "be"},
                }
            )
    if sl_mode == "trailing" and (sl.get("trailing_start") or sl.get("improve_only") or tps):
        start = sl.get("trailing_start") or {}
        trig = start if start.get("kind") else None
        if trig is None and tps:
            trig = {"kind": "price", "op": op, "price": (tps[0] or {}).get("price")}
        if trig:
            rows.append(
                {
                    "symbol": symbol,
                    "trigger": trig,
                    "plan": {**plan, "_emulated": "trail"},
                }
            )
    return rows


def _submit_native(plan):
    symbol = plan.get("symbol")
    side = plan.get("side")
    size = plan.get("size") or {}
    notional = size.get("notional") or 0
    qty = size.get("qty")
    tps = list(plan.get("tps") or [])
    sl = plan.get("sl") or {}
    sl_mode = str(sl.get("mode") or "fixed").lower()
    entry = plan.get("entry") or {}
    if sl_mode == "trailing" and not tps:
        return submit_trailing_stop_order(
            symbol,
            side,
            notional,
            trail_percent=sl.get("trailing_distance_pct") or sl.get("trailing_distance"),
            trail_price=sl.get("trailing_distance") if sl.get("trailing_distance_pct") is None else None,
            qty=qty,
        )
    first = tps[0] if tps else {}
    tp_px = first.get("price")
    sl_px = sl.get("price")
    if tp_px is None or sl_px is None:
        return submit_market_order(symbol, side, notional)
    return submit_bracket_order(
        symbol,
        side,
        notional,
        take_profit_price=tp_px,
        stop_loss_price=sl_px,
        entry_type=entry.get("type") or "market",
        limit_price=entry.get("price") if entry.get("type") == "limit" else None,
        qty=qty,
    )


def execute_plan(
    plan,
    account,
    positions,
    open_orders,
    clock,
    decision=None,
    park_emulated=True,
):
    """Gate + Armed then paper submit. Does not change POST /execute."""
    decision = decision_from_plan(plan, decision)
    gate = evaluate_gate(decision, account, positions, open_orders, clock, plan=plan)

    built = would_call(plan)
    out = {
        "gate": gate,
        "decision": decision,
        "would_call": built["would_call"],
        "risk": built["risk"],
        "emulated": [],
    }

    if gate["verdict"] == "NO_TRADE":
        out.update({"status": "NO_TRADE", "reason": (gate["reasons"] or ["HOLD"])[0]})
        return out
    if gate["verdict"] == "BLOCK":
        out.update({"status": "BLOCKED", "reason": (gate["reasons"] or ["blocked"])[0]})
        return out
    if not config.is_armed():
        out.update(
            {
                "status": "DRY_RUN",
                "reason": "System not armed (EXECUTE_ENABLED=false)",
            }
        )
        return out

    emulated = plan.get("_emulated")
    if emulated == "be":
        out.update({"status": "be_moved", "reason": "stop moved to break-even"})
        return out
    if emulated == "tp":
        result = submit_market_order(
            plan.get("symbol"),
            "sell" if plan.get("side") == "buy" else "buy",
            ((plan.get("size") or {}).get("notional") or 0)
            * (((plan.get("tps") or [{}])[0] or {}).get("size_pct") or 100)
            / 100,
        )
        out.update(_normalize_submit(result))
        return out

    result = _submit_native(plan)
    out.update(_normalize_submit(result))
    if park_emulated:
        out["emulated"] = emulated_rows(plan)
    return out


def _normalize_submit(result):
    result = result or {}
    status = str(result.get("status") or "SUBMITTED").upper()
    if status == "DEMO":
        status = "SUBMITTED"
    return {
        "status": status,
        "order_id": result.get("order_id") or result.get("id"),
        "order_status": result.get("status"),
        "filled_qty": result.get("filled_qty"),
        "filled_avg_price": result.get("filled_avg_price"),
        "notional": result.get("notional"),
        "mode": result.get("mode"),
        "reason": result.get("warning") or result.get("reason"),
    }
