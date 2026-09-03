"""Execution gate: 3 working orders, max credit, cancel-own."""

import unittest

from agents.execution_gate import evaluate_gate
from services import config


ACCOUNT = {"equity": 100_000, "buying_power": 50_000, "mode": "paper"}
CLOCK = {"is_open": True}


def _buy(symbol="MSFT", size=100):
    return {"action": "BUY", "symbol": symbol, "position_size": size}


class GateBookTest(unittest.TestCase):
    def setUp(self):
        config.set_kill(False)
        config.set_armed(False)

    def test_fourth_working_order_blocks(self):
        open_orders = [
            {"symbol": "SPY", "order_id": "1", "side": "buy"},
            {"symbol": "QQQ", "order_id": "2", "side": "buy"},
            {"symbol": "AAPL", "order_id": "3", "side": "buy"},
        ]
        gate = evaluate_gate(_buy(), ACCOUNT, [], open_orders, CLOCK, max_credit=500)
        self.assertEqual(gate["verdict"], "BLOCK")
        names = [c["name"] for c in gate["checks"] if not c["ok"] and c.get("hard")]
        self.assertIn("active_orders", names)

    def test_notional_over_max_credit_blocks(self):
        gate = evaluate_gate(
            _buy(size=1000), ACCOUNT, [], [], CLOCK, max_credit=500
        )
        self.assertEqual(gate["verdict"], "BLOCK")
        names = [c["name"] for c in gate["checks"] if not c["ok"] and c.get("hard")]
        self.assertIn("max_credit", names)

    def test_cancel_own_order_allows(self):
        open_orders = [{"symbol": "SPY", "order_id": "abc", "side": "buy"}]
        gate = evaluate_gate(
            {"action": "CANCEL", "symbol": "SPY", "order_id": "abc", "position_size": 0},
            ACCOUNT,
            [],
            open_orders,
            CLOCK,
        )
        self.assertEqual(gate["verdict"], "ALLOW")

    def test_cancel_unknown_blocks(self):
        gate = evaluate_gate(
            {"action": "CANCEL", "symbol": "SPY", "order_id": "nope", "position_size": 0},
            ACCOUNT,
            [],
            [{"symbol": "SPY", "order_id": "abc", "side": "buy"}],
            CLOCK,
        )
        self.assertEqual(gate["verdict"], "BLOCK")


class GateWindowBudgetTest(unittest.TestCase):
    def setUp(self):
        from services import db, usage_meter

        db.connect("sqlite:///:memory:")
        usage_meter.reset_runtime()
        config.set_kill(False)
        config.set_armed(False)

    def tearDown(self):
        from services import db, usage_meter

        usage_meter.reset_runtime()
        db.close()
        config.set_kill(False)

    def _past_window(self):
        from datetime import datetime, timedelta, timezone

        from services import scheduler

        start = datetime.now(timezone.utc) - timedelta(hours=2)
        end = start + timedelta(hours=1)
        scheduler.save(
            {
                "window_start": start.isoformat().replace("+00:00", "Z"),
                "window_end": end.isoformat().replace("+00:00", "Z"),
            }
        )

    def test_outside_window_blocks_place(self):
        self._past_window()
        gate = evaluate_gate(_buy(), ACCOUNT, [], [], CLOCK, max_credit=500)
        self.assertEqual(gate["verdict"], "BLOCK")
        names = [c["name"] for c in gate["checks"] if not c["ok"] and c.get("hard")]
        self.assertIn("window", names)

    def test_outside_window_blocks_cancel(self):
        self._past_window()
        gate = evaluate_gate(
            {"action": "CANCEL", "symbol": "SPY", "order_id": "abc", "position_size": 0},
            ACCOUNT,
            [],
            [{"symbol": "SPY", "order_id": "abc", "side": "buy"}],
            CLOCK,
        )
        self.assertEqual(gate["verdict"], "BLOCK")
        names = [c["name"] for c in gate["checks"] if not c["ok"] and c.get("hard")]
        self.assertIn("window", names)

    def test_outside_window_blocks_conditional_create(self):
        from services.bracket_plan import seed_plan
        from services.conditional import create_order

        self._past_window()
        plan = seed_plan(
            {"action": "BUY", "symbol": "SPY", "position_size": 100},
            100.0,
            atr=1.0,
        )
        out = create_order(
            plan,
            {"kind": "price", "op": ">=", "price": 101.0},
            account=ACCOUNT,
            positions=[],
            open_orders=[],
            clock=CLOCK,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "BLOCKED")

    def test_budget_over_blocks(self):
        from services import usage_meter

        usage_meter.save_budgets(
            [
                {
                    "provider": "groq",
                    "scope": "window",
                    "limit_type": "tokens",
                    "limit_value": 10,
                    "warn_pct": 80,
                    "action": "block_degrade",
                }
            ]
        )
        usage_meter.record("groq", tokens=10)
        gate = evaluate_gate(_buy(), ACCOUNT, [], [], CLOCK, max_credit=500)
        self.assertEqual(gate["verdict"], "BLOCK")
        names = [c["name"] for c in gate["checks"] if not c["ok"] and c.get("hard")]
        self.assertIn("budget_ok", names)


if __name__ == "__main__":
    unittest.main()
