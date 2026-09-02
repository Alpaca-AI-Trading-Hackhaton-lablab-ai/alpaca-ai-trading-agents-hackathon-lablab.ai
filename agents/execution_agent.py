"""Paper executor. Not in the SSE pipeline — POST /execute only.

Domain logic above; Agent wrapper at the bottom. The gate must have already
authorized the decision; this module never bypasses it.
"""

import time

from agents.base import Agent
from services.alpaca_service import get_order_status, submit_market_order

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


class ExecutionAgent(Agent):
    """Broker submit + poll. Instantiated only by POST /execute, never by
    build_pipeline()."""

    node = "execution"

    def run(self, ctx):
        return execute_trade(ctx["decision"])

    def message(self, out):
        return self._err(out) or out.get("status") or "n/a"
