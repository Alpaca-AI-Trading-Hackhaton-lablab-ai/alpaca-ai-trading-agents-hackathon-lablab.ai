"""P1: tick seeds a qty bracket, risk sees position, fill listener fires parent."""

import unittest
from unittest.mock import patch

from agents.decision_agent import make_decision
from agents.risk_manager import calculate_risk
from services import config, db
from services.book import apply_intents
from services.bracket_plan import seed_plan, share_qty
from services.conditional import fire_parent, park_rows
from services.fill_listener import handle_trade_update


ACCOUNT = {"equity": 100_000, "buying_power": 100_000, "mode": "paper"}
CLOCK = {"is_open": True}


def _ms(**extra):
    out = {
        "symbol": "SPY",
        "technical_signal": "BUY",
        "sentiment": "BULLISH",
        "price": 100,
        "atr": 1,
    }
    out.update(extra)
    return out


class ShareQtyTest(unittest.TestCase):
    def test_floor_notional(self):
        self.assertEqual(share_qty(500, 100), 5)
        self.assertEqual(share_qty(50, 100), 0)
        self.assertEqual(share_qty(500, 0), 0)

    def test_seed_plan_sets_qty(self):
        plan = seed_plan(
            {"action": "BUY", "symbol": "SPY", "position_size": 500}, 100.0, atr=1
        )
        self.assertEqual(plan["size"]["qty"], 5)
        self.assertEqual(plan["size"]["notional"], 500)


class TickBracketTest(unittest.TestCase):
    def setUp(self):
        config.set_kill(False)
        config.set_armed(False)

    def test_apply_intents_passes_plan_not_naked_market(self):
        with patch("services.book.get_open_orders", return_value=[]):
            with patch(
                "services.book.dispatch",
                return_value={"status": "DRY_RUN", "decision": {}},
            ) as dispatched:
                apply_intents(
                    [
                        {
                            "action": "BUY",
                            "symbol": "SPY",
                            "position_size": 500,
                            "last_price": 100,
                            "atr": 1,
                        }
                    ],
                    ACCOUNT,
                    [],
                    CLOCK,
                    500,
                )
        plan = dispatched.call_args.kwargs["plan"]
        self.assertEqual(plan["size"]["qty"], 5)
        self.assertEqual(plan["sl"]["mode"], "fixed")
        self.assertTrue(plan["tps"])

    def test_qty_below_one_does_not_dispatch(self):
        with patch("services.book.get_open_orders", return_value=[]):
            with patch("services.book.dispatch") as dispatched:
                rows = apply_intents(
                    [
                        {
                            "action": "BUY",
                            "symbol": "SPY",
                            "position_size": 50,
                            "last_price": 100,
                        }
                    ],
                    ACCOUNT,
                    [],
                    CLOCK,
                    500,
                )
        dispatched.assert_not_called()
        self.assertEqual(rows[0]["status"], "NO_TRADE")
        self.assertIn("1 share", rows[0]["reason"])

    def test_missing_price_does_not_dispatch(self):
        with patch("services.book.get_open_orders", return_value=[]):
            with patch("services.book.dispatch") as dispatched:
                rows = apply_intents(
                    [{"action": "BUY", "symbol": "SPY", "position_size": 500}],
                    ACCOUNT,
                    [],
                    CLOCK,
                    500,
                )
        dispatched.assert_not_called()
        self.assertEqual(rows[0]["status"], "NO_TRADE")


class RiskPositionTest(unittest.TestCase):
    def test_same_direction_long_zeros_size(self):
        out = calculate_risk(
            100000,
            90,
            atr=1,
            price=100,
            scores={"buy": 10, "sell": 0},
            positions=[{"symbol": "SPY", "qty": 12, "side": "long"}],
            symbol="SPY",
            intended_action="BUY",
        )
        self.assertEqual(out["position_size"], 0)
        self.assertEqual(out["existing_qty"], 12)
        self.assertEqual(out["blocked_reason"], "already long")

    def test_opposite_short_still_sizes(self):
        out = calculate_risk(
            100000,
            90,
            atr=1,
            price=100,
            scores={"buy": 10, "sell": 0},
            positions=[{"symbol": "SPY", "qty": 12, "side": "long"}],
            symbol="SPY",
            intended_action="SELL",
        )
        self.assertGreater(out["position_size"], 0)
        self.assertIsNone(out["blocked_reason"])

    def test_make_decision_holds_when_already_long(self):
        out = make_decision(
            _ms(),
            {"position_size": 500, "risk_level": "LOW", "existing_qty": 12},
        )
        self.assertEqual(out["action"], "HOLD")
        self.assertEqual(out["position_size"], 0)
        self.assertEqual(out["blocked_reason"], "already long")


class FillListenerTest(unittest.TestCase):
    def setUp(self):
        db.connect("sqlite:///:memory:")
        config.set_kill(False)
        config.set_armed(False)

    def tearDown(self):
        db.close()

    def test_handle_fill_fires_parked_parent(self):
        parked = park_rows(
            [
                {
                    "symbol": "SPY",
                    "trigger": {"kind": "price", "op": ">=", "price": 102},
                    "plan": {
                        "symbol": "SPY",
                        "side": "buy",
                        "parent_order_id": "parent-1",
                        "parent_client_order_id": "cli-1",
                        "_emulated": "be",
                        "size": {"notional": 500, "qty": 5},
                        "entry": {"price": 100, "type": "market"},
                        "tps": [{"price": 102, "size_pct": 100}],
                        "sl": {"price": 99, "mode": "fixed"},
                    },
                }
            ]
        )
        self.assertEqual(len(parked), 1)
        with patch(
            "services.conditional.execute_plan",
            return_value={"status": "be_moved"},
        ) as fired:
            out = handle_trade_update(
                {"event": "fill", "order": {"id": "parent-1", "client_order_id": "cli-1"}}
            )
        self.assertEqual(len(out), 1)
        fired.assert_called_once()

    def test_other_event_does_not_fire(self):
        park_rows(
            [
                {
                    "symbol": "SPY",
                    "trigger": {"kind": "price", "op": ">=", "price": 102},
                    "plan": {"parent_order_id": "parent-1", "symbol": "SPY", "side": "buy"},
                }
            ]
        )
        with patch("services.conditional.execute_plan") as fired:
            out = handle_trade_update(
                {"event": "new", "order": {"id": "parent-1"}}
            )
        self.assertEqual(out, [])
        fired.assert_not_called()
        self.assertEqual(fire_parent("nope"), [])


if __name__ == "__main__":
    unittest.main()
