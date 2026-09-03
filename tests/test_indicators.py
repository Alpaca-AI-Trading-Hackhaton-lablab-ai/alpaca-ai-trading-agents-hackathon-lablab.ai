"""Tests for the shared indicator engine."""

import unittest

from agents.indicator_engine import TF_HOUR, compute_pack, parse_indicators, snapshot_only
from services.alpaca_service import _demo_bars


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

    def test_ema_stack_and_rsi3(self):
        pack = compute_pack("SPY", ["ema3", "ema10", "ema50", "ema100", "rsi3"])
        self.assertNotIn("error", pack)
        snap = pack["snapshot"]
        for key in ("ema3", "ema10", "ema50", "ema100", "rsi3"):
            self.assertIn(key, snap)
        self.assertIn(snap.get("ema_trend"), ("BULLISH", "BEARISH", "NEUTRAL"))
        self.assertIn(snap.get("rsi_signal"), ("OVERSOLD", "OVERBOUGHT", "NEUTRAL"))
        self.assertTrue(pack["overlays"]["ema3"])
        self.assertTrue(pack["oscillators"]["rsi3"])
        self.assertNotIn("sma20", snap)

    def test_new_ids_allowlisted(self):
        ids = parse_indicators("ema3,rsi3,nope")
        self.assertEqual(ids, ["ema3", "rsi3"])

    def test_hourly_demo_bars(self):
        daily = _demo_bars("SPY", timeframe="1Day", limit=30)
        hourly = _demo_bars("SPY", timeframe="1Hour", limit=30)
        self.assertEqual(len(daily), 30)
        self.assertEqual(len(hourly), 30)
        self.assertEqual(compute_pack("SPY", ["rsi3"]).get("symbol"), "SPY")
        self.assertEqual(TF_HOUR, "1Hour")


if __name__ == "__main__":
    unittest.main()
