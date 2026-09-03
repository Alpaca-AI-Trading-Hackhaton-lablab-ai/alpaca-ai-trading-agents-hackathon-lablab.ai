"""Deterministic execution gate (the 'hard gate').

The LLM proposes a decision; this pure-Python governor authorizes it. No model
is in this loop. It runs as a read-only preview inside the pipeline stream and
again authoritatively right before submit, so it must stay idempotent and free
of side effects. Domain logic above; Agent wrapper at the bottom.

evaluate_gate(...) -> {
    "verdict": "ALLOW" | "BLOCK" | "NO_TRADE",
    "action", "symbol", "notional",
    "checks": [{"name", "ok", "detail", "hard"}],
    "reasons": [str],  # failed hard checks (why it was blocked)
}
"""

import os

from agents.base import Agent
from services import config
from services.alpaca_service import get_market_clock, get_open_orders, get_positions


def _is_live_requested():
    flag = os.getenv("ALPACA_PAPER_TRADE", "true").strip().lower()
    return flag in ("false", "0", "no")


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def evaluate_gate(
    decision,
    account,
    positions,
    open_orders,
    clock,
    plan=None,
    max_credit=None,
):
    account = account or {}
    positions = positions or []
    open_orders = open_orders or []
    clock = clock or {}
    if max_credit is None:
        max_credit = config.DEFAULT_MAX_CREDIT

    if plan:
        side = str((plan or {}).get("side") or "").lower()
        size = (plan or {}).get("size") or {}
        decision = {
            **(decision or {}),
            "action": "BUY" if side == "buy" else "SELL" if side == "sell" else (decision or {}).get("action"),
            "symbol": (plan or {}).get("symbol") or (decision or {}).get("symbol"),
            "position_size": size.get("notional")
            if size.get("notional") is not None
            else (decision or {}).get("position_size"),
        }

    action = (decision or {}).get("action")
    symbol = (decision or {}).get("symbol")
    notional = _num((decision or {}).get("position_size"), 0.0)

    # Fail-closed: a degraded/invalid proposal never trades.
    if not decision or decision.get("error") or not symbol or action is None:
        return {
            "verdict": "NO_TRADE",
            "action": action or "HOLD",
            "symbol": symbol,
            "notional": notional,
            "checks": [],
            "reasons": ["degraded decision (fail-closed)"],
        }

    # HOLD is a legitimate no-op, not a block.
    if action == "HOLD":
        return {
            "verdict": "NO_TRADE",
            "action": "HOLD",
            "symbol": symbol,
            "notional": notional,
            "checks": [],
            "reasons": ["decision is HOLD"],
        }

    checks = []

    def add(name, ok, detail, hard=True):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "hard": hard})

    add("paper", not _is_live_requested(),
        "paper-only" if not _is_live_requested() else "LIVE requested")
    add("kill_switch", not config.is_kill(),
        "engaged" if config.is_kill() else "off")
    add("window", config.is_within_window(),
        "in window" if config.is_within_window() else "outside UTC window")
    from services import usage_meter

    ok_budget, over_provider = usage_meter.budget_ok()
    add(
        "budget_ok",
        ok_budget,
        "ok" if ok_budget else f"{over_provider} OVER",
    )

    if action == "CANCEL":
        order_id = str((decision or {}).get("order_id") or "")
        ours = any(
            str(o.get("order_id") or o.get("id") or "") == order_id
            for o in open_orders
        )
        add("cancel_own", bool(order_id) and ours,
            "own working order" if ours else "unknown order_id")
        add("session", bool(clock.get("is_open")),
            "open" if clock.get("is_open") else "closed (order queued)", hard=False)
        add("armed", config.is_armed(),
            "armed" if config.is_armed() else "safe (dry-run)", hard=False)
        reasons = [f"{c['name']}: {c['detail']}" for c in checks if c["hard"] and not c["ok"]]
        return {
            "verdict": "BLOCK" if reasons else "ALLOW",
            "action": "CANCEL",
            "symbol": symbol,
            "notional": 0.0,
            "checks": checks,
            "reasons": reasons,
        }

    equity = _num(account.get("equity"), 0.0)
    buying_power = _num(account.get("buying_power"), 0.0)

    pos = next((p for p in positions if p.get("symbol") == symbol), None)
    pos_side = (pos or {}).get("side")
    pos_value = abs(_num((pos or {}).get("market_value"), 0.0))
    total_exposure = sum(abs(_num(p.get("market_value"), 0.0)) for p in positions)

    # A SELL against a long (or BUY against a short) reduces exposure.
    reducing = (action == "SELL" and pos_side == "long") or (
        action == "BUY" and pos_side == "short"
    )
    increasing = not reducing

    symbol_cap = config.MAX_SYMBOL_EXPOSURE_PCT * equity
    total_cap = config.MAX_TOTAL_EXPOSURE_PCT * equity
    projected_symbol = pos_value + notional if increasing else pos_value
    projected_total = total_exposure + notional if increasing else total_exposure

    working_symbol = [o for o in open_orders if o.get("symbol") == symbol]
    mode = account.get("mode")
    cap = _num(max_credit, config.DEFAULT_MAX_CREDIT)

    add("buying_power",
        (notional <= buying_power) if increasing else True,
        f"${notional:,.0f} / ${buying_power:,.0f}")
    add("max_credit",
        notional <= cap,
        f"${notional:,.0f} / ${cap:,.0f}")
    add("symbol_exposure",
        (projected_symbol <= symbol_cap) if increasing else True,
        f"${projected_symbol:,.0f} / ${symbol_cap:,.0f} "
        f"({config.MAX_SYMBOL_EXPOSURE_PCT:.0%})")
    add("total_exposure",
        (projected_total <= total_cap) if increasing else True,
        f"${projected_total:,.0f} / ${total_cap:,.0f} "
        f"({config.MAX_TOTAL_EXPOSURE_PCT:.0%})")
    add("one_per_symbol", len(working_symbol) == 0,
        "none resting" if not working_symbol else f"{len(working_symbol)} working — no pile-on")
    add("active_orders", len(open_orders) < config.MAX_WORKING_ORDERS,
        f"{len(open_orders)} / {config.MAX_WORKING_ORDERS} working")
    add("session", bool(clock.get("is_open")),
        "open" if clock.get("is_open") else "closed (order queued)", hard=False)
    add("armed", config.is_armed(),
        "armed" if config.is_armed() else "safe (dry-run)", hard=False)
    add("credentials", mode == "paper",
        "paper" if mode == "paper" else "demo (no broker)", hard=False)

    if plan:
        from services.bracket_plan import validate_plan

        v = validate_plan(plan)
        add(
            "plan",
            v["ok"],
            "; ".join(v["errors"]) if v["errors"] else "valid",
        )
        r = v.get("r_multiple")
        add(
            "r_multiple",
            r is not None and r >= 1,
            f"{r:.2f}" if r is not None else "n/a",
        )
        ml = v.get("max_loss")
        raw_cap = (decision or {}).get("max_loss")
        try:
            risk_cap = float(raw_cap) if raw_cap is not None else None
        except (TypeError, ValueError):
            risk_cap = None
        if risk_cap is None or risk_cap <= 0:
            risk_cap = ml
        add(
            "max_loss",
            ml is None or risk_cap is None or ml <= risk_cap,
            f"${ml or 0:,.2f} / ${risk_cap or 0:,.2f}" if ml is not None else "n/a",
        )

    reasons = [f"{c['name']}: {c['detail']}" for c in checks if c["hard"] and not c["ok"]]
    verdict = "BLOCK" if reasons else "ALLOW"

    return {
        "verdict": verdict,
        "action": action,
        "symbol": symbol,
        "notional": notional,
        "checks": checks,
        "reasons": reasons,
    }


class GateAgent(Agent):
    node = "gate"

    def run(self, ctx):
        return evaluate_gate(
            ctx["decision"],
            ctx["account"],
            get_positions().get("positions", []),
            get_open_orders(),
            get_market_clock(),
            max_credit=ctx.get("max_credit"),
        )

    def message(self, out):
        verdict = out.get("verdict")
        if verdict == "BLOCK":
            reasons = out.get("reasons") or []
            return f"BLOCK — {reasons[0] if reasons else 'blocked'}"
        if verdict == "NO_TRADE":
            return "NO_TRADE"
        return f"ALLOW · {len(out.get('checks') or [])} checks"
