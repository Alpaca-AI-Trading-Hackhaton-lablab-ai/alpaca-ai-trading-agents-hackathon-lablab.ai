"""Sole broker entry: dispatch() after evaluate_gate(). SSE does not submit."""

import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.execution_agent import dispatch
from agents.nodes import PIPELINE_KEYS, build_pipeline
from services import config
from services.book import apply_intents
from services.bracket import execute_plan
from services.bracket_plan import seed_plan


ACCOUNT = {"equity": 100_000, "buying_power": 100_000, "mode": "paper"}
CLOCK = {"is_open": True}
ROOT = Path(__file__).resolve().parents[1]

_BUY = {"action": "BUY", "symbol": "SPY", "position_size": 500}
_CANCEL = {
    "action": "CANCEL",
    "symbol": "SPY",
    "order_id": "ord-1",
    "position_size": 0,
}


def _gate(verdict, **extra):
    out = {
        "verdict": verdict,
        "reasons": extra.pop("reasons", []),
        "notional": extra.pop("notional", 500),
        "checks": [],
        "action": extra.pop("action", "BUY"),
        "symbol": extra.pop("symbol", "SPY"),
    }
    out.update(extra)
    return out


def _plan():
    return seed_plan(_BUY, 100.0, atr=1.0)


class DispatchCoreTest(unittest.TestCase):
    def setUp(self):
        config.set_kill(False)
        config.set_armed(False)

    def tearDown(self):
        config.set_armed(False)
        config.set_kill(False)

    def _dispatch(self, decision=_BUY, plan=None, **kwargs):
        return dispatch(
            decision=decision,
            account=ACCOUNT,
            positions=[],
            open_orders=[],
            clock=CLOCK,
            plan=plan,
            max_credit=500,
            **kwargs,
        )

    @patch("agents.execution_agent.execute_trade")
    @patch("agents.execution_agent.cancel_order")
    @patch("services.bracket.submit_armed")
    @patch("agents.execution_gate.evaluate_gate", return_value=_gate("BLOCK", reasons=["nope"]))
    def test_block_does_not_submit(self, _gate_fn, submit_armed, cancel_order, execute_trade):
        config.set_armed(True)
        out = self._dispatch()
        self.assertEqual(out["status"], "BLOCKED")
        execute_trade.assert_not_called()
        cancel_order.assert_not_called()
        submit_armed.assert_not_called()

    @patch("agents.execution_agent.execute_trade")
    @patch("agents.execution_agent.cancel_order")
    @patch("services.bracket.submit_armed")
    @patch("agents.execution_gate.evaluate_gate", return_value=_gate("ALLOW"))
    def test_dry_run_disarmed_does_not_submit(
        self, _gate_fn, submit_armed, cancel_order, execute_trade
    ):
        out = self._dispatch()
        self.assertEqual(out["status"], "DRY_RUN")
        self.assertEqual(out["would_call"]["tool"], "place_stock_order")
        self.assertEqual(out["would_call"]["notional_position_size"], 500)
        execute_trade.assert_not_called()
        cancel_order.assert_not_called()
        submit_armed.assert_not_called()

    @patch("agents.execution_agent.execute_trade", return_value={"status": "SUBMITTED", "order_id": "x"})
    @patch("agents.execution_agent.cancel_order")
    @patch("services.bracket.submit_armed")
    @patch("agents.execution_gate.evaluate_gate", return_value=_gate("ALLOW"))
    def test_armed_market_calls_execute_trade(
        self, _gate_fn, submit_armed, cancel_order, execute_trade
    ):
        config.set_armed(True)
        out = self._dispatch()
        self.assertEqual(out["status"], "SUBMITTED")
        execute_trade.assert_called_once()
        cancel_order.assert_not_called()
        submit_armed.assert_not_called()

    @patch("agents.execution_agent.execute_trade")
    @patch(
        "agents.execution_agent.cancel_order",
        return_value={"status": "canceled", "order_id": "ord-1"},
    )
    @patch("services.bracket.submit_armed")
    @patch(
        "agents.execution_gate.evaluate_gate",
        return_value=_gate("ALLOW", action="CANCEL"),
    )
    def test_armed_cancel_calls_cancel_order(
        self, _gate_fn, submit_armed, cancel_order, execute_trade
    ):
        config.set_armed(True)
        out = self._dispatch(decision=_CANCEL)
        self.assertEqual(out["status"], "CANCELED")
        cancel_order.assert_called_once_with("ord-1")
        execute_trade.assert_not_called()
        submit_armed.assert_not_called()

    @patch("agents.execution_agent.execute_trade")
    @patch("agents.execution_agent.cancel_order")
    @patch(
        "services.bracket.submit_armed",
        return_value={"status": "SUBMITTED", "order_id": "br-1", "emulated": []},
    )
    @patch("agents.execution_gate.evaluate_gate", return_value=_gate("ALLOW"))
    def test_armed_plan_calls_submit_armed(
        self, _gate_fn, submit_armed, cancel_order, execute_trade
    ):
        config.set_armed(True)
        plan = _plan()
        out = self._dispatch(plan=plan)
        self.assertEqual(out["status"], "SUBMITTED")
        submit_armed.assert_called_once()
        self.assertIs(submit_armed.call_args.args[0], plan)
        execute_trade.assert_not_called()
        cancel_order.assert_not_called()


class DispatchCallersTest(unittest.TestCase):
    def test_apply_intents_delegates_to_dispatch(self):
        src = inspect.getsource(apply_intents)
        self.assertIn("dispatch(", src)
        self.assertNotIn("evaluate_gate(", src)
        with patch("services.book.get_open_orders", return_value=[]) as open_orders:
            with patch(
                "services.book.dispatch",
                return_value={
                    "status": "DRY_RUN",
                    "gate": _gate("ALLOW"),
                    "decision": _BUY,
                },
            ) as dispatched:
                with patch("agents.execution_gate.evaluate_gate") as gate_fn:
                    rows = apply_intents(
                        [{"action": "BUY", "symbol": "SPY", "position_size": 500}],
                        ACCOUNT,
                        [],
                        CLOCK,
                        500,
                    )
        gate_fn.assert_not_called()
        dispatched.assert_called_once()
        self.assertEqual(rows[0]["intent"]["symbol"], "SPY")
        self.assertEqual(open_orders.call_count, 1)

    def test_apply_intents_rereads_open_orders_after_armed_allow(self):
        with patch("services.book.get_open_orders", return_value=[]) as open_orders:
            with patch(
                "services.book.dispatch",
                return_value={
                    "status": "SUBMITTED",
                    "gate": _gate("ALLOW"),
                    "decision": _BUY,
                },
            ):
                apply_intents(
                    [{"action": "BUY", "symbol": "SPY", "position_size": 500}],
                    ACCOUNT,
                    [],
                    CLOCK,
                    500,
                )
        self.assertEqual(open_orders.call_count, 2)

    def test_execute_plan_delegates_to_dispatch(self):
        src = inspect.getsource(execute_plan)
        self.assertIn("dispatch(", src)
        self.assertNotIn("evaluate_gate(", src)
        plan = _plan()
        with patch(
            "agents.execution_agent.dispatch",
            return_value={"status": "DRY_RUN", "gate": _gate("ALLOW")},
        ) as dispatched:
            with patch("agents.execution_gate.evaluate_gate") as gate_fn:
                execute_plan(plan, ACCOUNT, [], [], CLOCK)
        gate_fn.assert_not_called()
        dispatched.assert_called_once()
        self.assertIs(dispatched.call_args.kwargs["plan"], plan)

    def test_execute_http_uses_dispatch_not_local_gate(self):
        text = (ROOT / "backend.py").read_text()
        start = text.index('@app.post("/execute")')
        end = text.index('@app.get("/control")')
        body = text[start:end]
        self.assertIn("dispatch(", body)
        self.assertNotIn("evaluate_gate(", body)
        self.assertNotIn("execute_trade(", body)


class PipelinePreviewTest(unittest.TestCase):
    def test_sse_graph_does_not_dispatch(self):
        nodes = (ROOT / "agents" / "nodes.py").read_text()
        self.assertNotIn("from agents.execution_agent", nodes)
        self.assertNotIn("ExecutionAgent", nodes)
        self.assertNotIn("execution", PIPELINE_KEYS)
        self.assertFalse(any(agent.node == "execution" for agent in build_pipeline()))

        text = (ROOT / "backend.py").read_text()
        start = text.index("def run_pipeline(")
        end = text.index("\ndef _analysis(")
        self.assertNotIn("dispatch(", text[start:end])


if __name__ == "__main__":
    unittest.main()
