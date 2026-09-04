"""Paper executor: gate then dispatch. Not in the SSE pipeline.

Nothing reaches the broker except via `dispatch()` after `evaluate_gate()`.
HTTP/tick surfaces: POST /execute (alias POST /bracket/execute), armed
scheduler tick, conditionals, mcp_client.place_order. The graph SSE only
previews the gate.
"""

import time

from agents.base import Agent
from services import config
from services.alpaca_service import cancel_order, get_order_status, submit_market_order

# Estados terminales de una orden Alpaca.
_TERMINAL = {"filled", "rejected", "canceled", "expired", "done_for_day"}

# Cuántas veces reconciliar el estado tras enviar, y espera entre intentos (s).
_POLL_ATTEMPTS = 6
_POLL_DELAY = 0.5


def _classify(status):
    status = (status or "").lower()
    if status == "filled":
        return "FILLED"
    if status in ("rejected", "canceled", "expired", "done_for_day"):
        return "REJECTED"
    if status in ("accepted", "new", "pending_new", "accepted_for_bidding"):
        return "ACCEPTED"
    if status == "partially_filled":
        return "PARTIALLY_FILLED"
    return "SUBMITTED"


def execute_trade(decision):

    if decision["action"] == "HOLD":
        return {
            "status": "NO_TRADE",
            "reason": "Decision Agent returned HOLD",
            "decision": decision,
        }

    try:
        # position_size es un MONTO EN DÓLARES -> se envía como notional,
        # nunca como cantidad de acciones.
        submitted = submit_market_order(
            symbol=decision["symbol"],
            side=decision["action"].lower(),
            notional=decision["position_size"],
        )

        order_id = submitted.get("order_id")
        final = submitted

        # Reconciliación: sondea el estado real hasta que sea terminal.
        if order_id and submitted.get("mode") == "paper":
            for _ in range(_POLL_ATTEMPTS):
                if (final.get("status") or "").lower() in _TERMINAL:
                    break
                time.sleep(_POLL_DELAY)
                final = get_order_status(order_id)

        return {
            "status": _classify(final.get("status")),
            "order_id": order_id,
            "order_status": final.get("status"),
            "filled_qty": final.get("filled_qty", 0.0),
            "filled_avg_price": final.get("filled_avg_price"),
            "notional": submitted.get("notional"),
            "reason": final.get("reason"),
            "mode": submitted.get("mode"),
            "decision": decision,
        }

    except Exception as e:
        return {
            "status": "FAILED",
            "error": str(e),
            "decision": decision,
        }


def _would_call_market(decision):
    action = str((decision or {}).get("action") or "").upper()
    if action == "CANCEL":
        return {"tool": "cancel_order", "order_id": (decision or {}).get("order_id")}
    side = "sell" if action == "SELL" else "buy"
    notional = (decision or {}).get("position_size")
    return {
        "tool": "place_stock_order",
        "symbol": (decision or {}).get("symbol"),
        "side": side,
        "notional": notional,
        "notional_position_size": notional,
    }


def dispatch(
    *,
    decision,
    account,
    positions,
    open_orders,
    clock,
    plan=None,
    max_credit=None,
    park_emulated=True,
):
    """Sole broker entry: evaluate_gate, then DRY_RUN or cancel/market/bracket."""
    from agents.execution_gate import evaluate_gate

    decision = dict(decision or {})
    if plan:
        from services.bracket import decision_from_plan

        decision = decision_from_plan(plan, decision)

    gate = evaluate_gate(
        decision,
        account,
        positions,
        open_orders,
        clock,
        plan=plan,
        max_credit=max_credit,
    )
    out = {"gate": gate, "decision": decision}

    if plan:
        from services.bracket import would_call

        built = would_call(plan)
        out["would_call"] = built["would_call"]
        out["risk"] = built["risk"]
        out["emulated"] = []

    if gate["verdict"] == "NO_TRADE":
        out.update(
            {
                "status": "NO_TRADE",
                "reason": (gate["reasons"] or ["HOLD"])[0],
            }
        )
        return out
    if gate["verdict"] == "BLOCK":
        out.update(
            {
                "status": "BLOCKED",
                "reason": (gate["reasons"] or ["blocked"])[0],
            }
        )
        return out

    action = str(decision.get("action") or "").upper()

    if not config.is_armed():
        if not plan:
            out["would_call"] = _would_call_market(decision)
        out.update(
            {
                "status": "DRY_RUN",
                "reason": "System not armed (EXECUTE_ENABLED=false)",
            }
        )
        return out

    if action == "CANCEL":
        result = cancel_order(decision.get("order_id"))
        out["result"] = result
        out["status"] = str((result or {}).get("status") or "CANCELED").upper()
        out["order_id"] = (result or {}).get("order_id") or decision.get("order_id")
        return out

    if plan:
        from services.bracket import submit_armed

        submitted = submit_armed(plan, park_emulated=park_emulated)
        out.update(submitted)
        return out

    traded = execute_trade(decision)
    out.update(traded)
    return out


class ExecutionAgent(Agent):
    """Broker submit + poll via dispatch. Not in build_pipeline()."""

    node = "execution"

    def run(self, ctx):
        return dispatch(
            decision=ctx["decision"],
            account=ctx.get("account"),
            positions=ctx.get("positions") or [],
            open_orders=ctx.get("open_orders") or [],
            clock=ctx.get("clock") or {},
            plan=ctx.get("plan"),
            max_credit=ctx.get("max_credit"),
        )

    def message(self, out):
        return self._err(out) or out.get("status") or "n/a"
