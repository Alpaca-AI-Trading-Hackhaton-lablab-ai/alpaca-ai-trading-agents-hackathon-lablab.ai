"""Scored decision + ATR-aware risk. Synthetic market_state only."""

import unittest

from agents.decision_agent import SCORE_THRESHOLD, make_decision, score_setup
from agents.nodes import PIPELINE_KEYS, build_pipeline
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
