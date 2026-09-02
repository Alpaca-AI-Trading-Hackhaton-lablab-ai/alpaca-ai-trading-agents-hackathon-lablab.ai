"""Tests for the shared indicator engine."""

import unittest

from agents.indicator_engine import compute_pack, parse_indicators, snapshot_only


class ParseIndicatorsTest(unittest.TestCase):
    def test_default_all(self):
        ids = parse_indicators(None)
        self.assertIn("rsi", ids)
        self.assertIn("sma20", ids)

    def test_filters_unknown(self):
        self.assertEqual(parse_indicators("rsi,nope,sma20"), ["rsi", "sma20"])


class ComputePackTest(unittest.TestCase):
    def test_demo_bars_pack(self):
        pack = compute_pack("SPY", ["sma20", "rsi", "volume"])
        self.assertNotIn("error", pack)
        self.assertGreater(len(pack["candles"]), 10)
        candle = pack["candles"][-1]
        for key in ("time", "open", "high", "low", "close"):
            self.assertIn(key, candle)
        self.assertTrue(pack["overlays"]["sma20"])
        self.assertTrue(pack["oscillators"]["rsi"])
        self.assertIn("rsi", pack["snapshot"])
        self.assertNotIn("macd", pack["snapshot"])

    def test_snapshot_has_signal(self):
        snap = snapshot_only("AAPL")
        self.assertIn(snap.get("signal"), ("BUY", "SELL", "HOLD"))
        self.assertIn(snap.get("trend"), ("BULLISH", "BEARISH", "NEUTRAL"))


if __name__ == "__main__":
    unittest.main()
