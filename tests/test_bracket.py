"""Bracket plan validation, preview would_call, webhook redaction, kill cancel."""

import json
import unittest

from agents.execution_gate import evaluate_gate
from services import config, db
from services.bracket import preview_plan
from services.bracket_plan import seed_plan, validate_plan
from services.conditional import cancel_armed, create_order, list_orders


ACCOUNT = {"equity": 100_000, "buying_power": 100_000, "mode": "paper"}
CLOCK = {"is_open": True}


def _buy_plan(**overrides):
    plan = seed_plan(
        {"action": "BUY", "symbol": "SPY", "position_size": 500},
        100.0,
        atr=1.0,
    )
    plan.update(overrides)
    return plan


class BracketPlanTest(unittest.TestCase):
    def setUp(self):
        config.set_kill(False)
        config.set_armed(False)

    def test_invalid_sl_side_blocks_gate(self):
        plan = _buy_plan()
        plan["sl"]["price"] = 110.0
        self.assertFalse(validate_plan(plan)["ok"])
        gate = evaluate_gate(
            {"action": "BUY", "symbol": "SPY", "position_size": 500},
            ACCOUNT,
            [],
            [],
            CLOCK,
            plan=plan,
        )
        self.assertEqual(gate["verdict"], "BLOCK")
        names = [c["name"] for c in gate["checks"] if not c["ok"] and c.get("hard")]
        self.assertIn("plan", names)

    def test_size_pct_must_sum_100(self):
        plan = _buy_plan()
        plan["tps"] = [
            {"role": "tp", "type": "limit", "price": 102.0, "size_pct": 40},
            {"role": "tp", "type": "limit", "price": 104.0, "size_pct": 40},
        ]
        v = validate_plan(plan)
        self.assertFalse(v["ok"])
        self.assertTrue(any("100" in e for e in v["errors"]))

    def test_preview_would_call_is_bracket(self):
        plan = _buy_plan()
        plan["tps"] = [
            {"role": "tp", "type": "limit", "price": 102.0, "size_pct": 50},
            {"role": "tp", "type": "limit", "price": 104.0, "size_pct": 50},
        ]
        out = preview_plan(plan)
        self.assertTrue(out["ok"])
        self.assertIsInstance(out["would_call"], list)
        self.assertEqual(out["would_call"][0]["order_class"], "bracket")
        self.assertTrue(out["would_call"][1].get("emulated"))
        self.assertEqual(out["would_call"][1]["type"], "limit")


class ConditionalMotorTest(unittest.TestCase):
    def setUp(self):
        db.connect("sqlite:///:memory:")
        config.set_kill(False)
        config.set_armed(False)

    def tearDown(self):
        db.close()

    def test_webhook_token_redacted_on_get(self):
        created = create_order(
            _buy_plan(),
            {"kind": "webhook"},
            account=ACCOUNT,
            positions=[],
            open_orders=[],
            clock=CLOCK,
        )
        self.assertTrue(created["ok"])
        token = created["order"]["webhook_token"]
        self.assertTrue(token)
        listed = list_orders("SPY")
        self.assertEqual(len(listed), 1)
        self.assertNotIn("webhook_token", listed[0])
        self.assertEqual(listed[0]["trigger"]["kind"], "webhook")
        self.assertEqual(listed[0]["trigger"]["token_source"], "db")
        blob = json.dumps(listed)
        self.assertNotIn(token, blob)
        self.assertNotIn("token", listed[0]["trigger"])

    def test_kill_cancels_armed(self):
        created = create_order(
            _buy_plan(),
            {"kind": "price", "op": ">=", "price": 101},
            account=ACCOUNT,
            positions=[],
            open_orders=[],
            clock=CLOCK,
        )
        self.assertTrue(created["ok"])
        self.assertEqual(created["order"]["status"], "armed")
        n = cancel_armed()
        self.assertEqual(n, 1)
        self.assertEqual(list_orders("SPY")[0]["status"], "cancelled")
