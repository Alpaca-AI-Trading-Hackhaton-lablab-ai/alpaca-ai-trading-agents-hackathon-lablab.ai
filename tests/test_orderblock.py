"""Order-block detector: compact snapshot, fail-closed, prior-candle zone."""

import unittest
from unittest.mock import patch

import pandas as pd

from agents.orderblock_engine import detect_from_frame, detect_orderblocks


class OrderblockTest(unittest.TestCase):
    def test_demo_snapshot_shape(self):
        out = detect_orderblocks("SPY")
        self.assertNotIn("error", out)
        self.assertEqual(out["symbol"], "SPY")
        self.assertIn("near_bullish", out)
        self.assertIn("near_bearish", out)
        self.assertIsInstance(out["ob_count"]["bullish"], int)
        self.assertIsInstance(out["ob_count"]["bearish"], int)
        self.assertNotIn("candles", out)
        self.assertNotIn("bars", out)

    def test_fail_closed_no_bars(self):
        with patch("agents.orderblock_engine.bars_frame", return_value=None):
            with patch("agents.orderblock_engine.cache.get", return_value=None):
                out = detect_orderblocks("AAPL")
        self.assertFalse(out["near_bullish"])
        self.assertFalse(out["near_bearish"])
        self.assertEqual(out["ob_count"], {"bullish": 0, "bearish": 0})
        self.assertIn("error", out)

    def test_zone_is_prior_opposing_candle(self):
        rows = []
        for _ in range(20):
            rows.append(
                {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1e6}
            )
        rows.append(
            {"open": 100.0, "high": 100.1, "low": 98.5, "close": 99.0, "volume": 1e6}
        )
        rows.append(
            {"open": 99.0, "high": 103.0, "low": 98.8, "close": 102.5, "volume": 2e6}
        )
        out = detect_from_frame(pd.DataFrame(rows), "SPY")
        self.assertIsNotNone(out["bullish_ob"])
        self.assertEqual(out["bullish_ob"]["level"], "HIGH")
        self.assertEqual(out["bullish_ob"]["price"], 100.1)
        self.assertEqual(out["bullish_ob"]["low"], 98.5)
        self.assertEqual(out["bullish_ob"]["high"], 100.1)
        self.assertNotEqual(out["bullish_ob"]["price"], 103.0)


if __name__ == "__main__":
    unittest.main()
