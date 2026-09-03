"""Institutional flow from bars: AD line + volume, fail-closed."""

import unittest
from unittest.mock import patch

from agents.institutional_flow import detect_smart_money
from agents.research_tools import REGISTRY


class InstitutionalTest(unittest.TestCase):
    def test_demo_snapshot_shape(self):
        out = detect_smart_money("SPY")
        self.assertNotIn("error", out)
        self.assertIn(out["institutional_signal"], ("BUY", "SELL", "NEUTRAL"))
        self.assertIn(out["ad_line_trend"], ("ACCUMULATING", "DISTRIBUTING", "NEUTRAL"))
        self.assertIsInstance(out["smart_money_buying"], bool)
        self.assertIsInstance(out["smart_money_selling"], bool)
        self.assertNotIn("candles", out)

    def test_fail_closed_no_bars(self):
        with patch("agents.institutional_flow.bars_frame", return_value=None):
            with patch("agents.institutional_flow.cache.get", return_value=None):
                out = detect_smart_money("AAPL")
        self.assertFalse(out["smart_money_buying"])
        self.assertFalse(out["smart_money_selling"])
        self.assertEqual(out["institutional_signal"], "NEUTRAL")
        self.assertIn("error", out)

    def test_in_tool_registry(self):
        self.assertIn("detect_smart_money", REGISTRY)
        self.assertIn("detect_orderblocks", REGISTRY)


if __name__ == "__main__":
    unittest.main()
