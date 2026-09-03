"""Scored decision + ATR-aware risk. Synthetic market_state only."""

import unittest

from agents.decision_agent import (
    SCORE_THRESHOLD,
    DecisionAgent,
    DecisionReactAgent,
    apply_focus_action,
    book_prompt,
    compact_working_book,
    intents_from_book,
    make_decision,
    score_setup,
)
from agents.nodes import PIPELINE_KEYS, build_pipeline
from agents.research_tools import REGISTRY
from agents.risk_manager import calculate_risk


def _ms(**kwargs):
    base = {
        "symbol": "SPY",
        "sentiment": "NEUTRAL",
        "technical_signal": "HOLD",
        "rsi_signal": "NEUTRAL",
        "near_bullish": False,
        "near_bearish": False,
        "smart_money_buying": False,
        "smart_money_selling": False,
    }
    base.update(kwargs)
    return base


class ScoreSetupTest(unittest.TestCase):
    def test_empty_is_zero(self):
        self.assertEqual(score_setup({}), {"buy": 0.0, "sell": 0.0})

    def test_weights(self):
        scores = score_setup(
            _ms(
                technical_signal="BUY",
                rsi_signal="OVERSOLD",
                near_bullish=True,
                smart_money_buying=True,
                sentiment="BULLISH",
            )
        )
        self.assertEqual(scores["buy"], 2 + 1.5 + 3 + 2 + 1)
        self.assertEqual(scores["sell"], 0.0)


class MakeDecisionTest(unittest.TestCase):
    def test_hold_below_threshold(self):
        out = make_decision(_ms(sentiment="BULLISH"), {"position_size": 1000, "risk_level": "LOW"})
        self.assertEqual(out["action"], "HOLD")
        self.assertLess(out["scores"]["buy"], SCORE_THRESHOLD)

    def test_buy_when_aligned(self):
        out = make_decision(
            _ms(technical_signal="BUY", sentiment="BULLISH"),
            {"position_size": 1000, "risk_level": "LOW"},
        )
        self.assertEqual(out["action"], "BUY")
        self.assertGreaterEqual(out["scores"]["buy"], SCORE_THRESHOLD)
        self.assertEqual(out["position_size"], 1000)

    def test_near_ob_is_enough(self):
        out = make_decision(
            _ms(near_bullish=True),
            {"position_size": 500, "risk_level": "LOW"},
        )
        self.assertEqual(out["action"], "BUY")

    def test_error_fail_closed(self):
        out = make_decision(
            _ms(technical_signal="BUY", sentiment="BULLISH", error="degraded"),
            {"position_size": 1000, "risk_level": "LOW"},
        )
        self.assertEqual(out["action"], "HOLD")


class IntentsFromBookTest(unittest.TestCase):
    def test_hold_does_not_take_a_slot(self):
        book = [
            {
                "symbol": "SPY",
                "market_state": _ms(symbol="SPY", sentiment="BULLISH"),
                "risk": {"position_size": 100},
            },
            {
                "symbol": "QQQ",
                "market_state": _ms(
                    symbol="QQQ", technical_signal="BUY", sentiment="BULLISH"
                ),
                "risk": {"position_size": 100},
            },
        ]
        intents = intents_from_book(book, [], max_active=3)
        actions = {i["symbol"]: i["action"] for i in intents}
        self.assertNotIn("SPY", actions)
        self.assertEqual(actions.get("QQQ"), "BUY")

    def test_cancels_working_when_hold(self):
        book = [
            {
                "symbol": "SPY",
                "market_state": _ms(symbol="SPY"),
                "risk": {"position_size": 100},
            }
        ]
        intents = intents_from_book(
            book,
            [{"symbol": "SPY", "order_id": "ord-1", "side": "buy"}],
            max_active=3,
        )
        self.assertEqual(intents[0]["action"], "CANCEL")
        self.assertEqual(intents[0]["order_id"], "ord-1")


class DecisionOmsSnapshotTest(unittest.TestCase):
    """SSE path has no book; proposer still sees working orders."""

    def test_hold_with_working_cancels_without_book(self):
        out = DecisionAgent().run(
            {
                "symbol": "SPY",
                "market_state": _ms(symbol="SPY"),
                "risk": {"position_size": 100, "risk_level": "LOW"},
                "open_orders": [
                    {"symbol": "SPY", "order_id": "ord-1", "side": "buy"}
                ],
            }
        )
        self.assertEqual(out["action"], "HOLD")
        self.assertEqual(out["intents"][0]["action"], "CANCEL")
        self.assertEqual(out["intents"][0]["order_id"], "ord-1")

    def test_buy_does_not_pile_on(self):
        out = DecisionAgent().run(
            {
                "symbol": "SPY",
                "market_state": _ms(
                    symbol="SPY", technical_signal="BUY", sentiment="BULLISH"
                ),
                "risk": {"position_size": 100, "risk_level": "LOW"},
                "open_orders": [
                    {"symbol": "SPY", "order_id": "ord-1", "side": "buy"}
                ],
            }
        )
        self.assertEqual(out["action"], "BUY")
        self.assertFalse(any(i.get("action") == "BUY" for i in out["intents"]))

    def test_book_prompt_has_working_and_pos(self):
        text = book_prompt(
            None,
            open_orders=[
                {
                    "symbol": "SPY",
                    "order_id": "abcdefgh-uuid",
                    "side": "buy",
                    "notional": 500,
                }
            ],
            positions=[
                {
                    "symbol": "SPY",
                    "side": "long",
                    "qty": 12,
                    "market_value": 1200,
                }
            ],
            focus="SPY",
        )
        self.assertIn("working=buy:abcdefgh", text)
        self.assertIn("pos=long:12", text)
        self.assertNotIn("place_", text)

    def test_focus_hold_drops_scored_place(self):
        intents = [
            {"symbol": "SPY", "action": "BUY", "notional": 100},
            {"symbol": "QQQ", "action": "BUY", "notional": 100},
        ]
        out = apply_focus_action(intents, "HOLD", "SPY", [])
        self.assertEqual([i["symbol"] for i in out], ["QQQ"])

    def test_focus_hold_cancels_working(self):
        intents = [{"symbol": "SPY", "action": "BUY", "notional": 100}]
        out = apply_focus_action(
            intents,
            "HOLD",
            "SPY",
            [{"symbol": "SPY", "order_id": "ord-9", "side": "buy"}],
        )
        self.assertEqual(out[0]["action"], "CANCEL")
        self.assertEqual(out[0]["order_id"], "ord-9")

    def test_compact_working_short_id(self):
        rows = compact_working_book(
            [{"symbol": "aapl", "order_id": "1234567890", "side": "sell", "notional": 1}]
        )
        self.assertEqual(rows[0]["order_id"], "12345678")
        self.assertEqual(rows[0]["symbol"], "AAPL")

    def test_deep_goal_includes_oms_snapshot(self):
        from unittest.mock import patch

        agent = DecisionReactAgent()
        ctx = {
            "symbol": "SPY",
            "market_state": _ms(symbol="SPY"),
            "risk": {"position_size": 100, "risk_level": "LOW"},
            "open_orders": [
                {"symbol": "SPY", "order_id": "ord-aaaa", "side": "buy"}
            ],
            "positions": [
                {"symbol": "SPY", "side": "long", "qty": 12, "market_value": 1200}
            ],
        }
        with patch("agents.decision_agent.logs.history_text", return_value=""):
            goal = agent.goal(ctx)
        self.assertIn("working=buy:ord-aaaa", goal)
        self.assertIn("pos=long:12", goal)

    def test_decision_tools_have_no_place(self):
        names = set(DecisionReactAgent().tools().keys()) | set(REGISTRY)
        banned = [n for n in names if "place" in n or n.endswith("_order")]
        self.assertEqual(banned, [])


class RiskSizingTest(unittest.TestCase):
    def test_high_atr_reduces_notional(self):
        scores = {"buy": 5, "sell": 0}
        low_vol = calculate_risk(100000, 50, atr=0.5, price=100, scores=scores)
        high_vol = calculate_risk(100000, 50, atr=10, price=100, scores=scores)
        self.assertLess(high_vol["position_size"], low_vol["position_size"])

    def test_cap_at_ten_percent(self):
        out = calculate_risk(
            100000, 90, atr=0.1, price=100, scores={"buy": 10, "sell": 0}
        )
        self.assertLessEqual(out["position_size"], 10000)

    def test_two_arg_still_works(self):
        out = calculate_risk(100000, 50)
        self.assertEqual(out["risk_level"], "LOW")
        self.assertGreater(out["position_size"], 0)

    def test_max_credit_clamps_notional(self):
        out = calculate_risk(
            100000,
            90,
            atr=0.1,
            price=100,
            scores={"buy": 10, "sell": 0},
            max_credit=500,
        )
        self.assertLessEqual(out["position_size"], 500)


class PipelineRegistryTest(unittest.TestCase):
    def test_new_nodes_registered(self):
        self.assertEqual(tuple(a.node for a in build_pipeline()), PIPELINE_KEYS)
        self.assertIn("orderblock", PIPELINE_KEYS)
        self.assertIn("institutional", PIPELINE_KEYS)
        self.assertLess(
            PIPELINE_KEYS.index("orderblock"), PIPELINE_KEYS.index("market_state")
        )


if __name__ == "__main__":
    unittest.main()
