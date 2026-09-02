"""Redis cache hit/miss + flush (fakeredis)."""

import unittest

import fakeredis

from services import cache
from services.schemas import bars_adapter


class CacheTest(unittest.TestCase):
    def setUp(self):
        cache.connect(client=fakeredis.FakeRedis(decode_responses=False))

    def tearDown(self):
        cache.close()

    def test_miss_then_hit(self):
        calls = []

        def compute():
            calls.append(1)
            return {"n": len(calls)}

        first = cache.cached("news", "SPY", "q1", 60, compute)
        second = cache.cached("news", "SPY", "q1", 60, compute)
        self.assertEqual(first, {"n": 1})
        self.assertEqual(second, {"n": 1})
        self.assertEqual(len(calls), 1)

    def test_bars_adapter_roundtrip(self):
        pack = {
            "symbol": "SPY",
            "indicators": ["rsi"],
            "candles": [
                {"time": 1, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}
            ],
            "overlays": {},
            "oscillators": {"rsi": [{"time": 1, "value": 55.0}]},
            "volume": [],
            "snapshot": {"symbol": "SPY", "signal": "HOLD"},
        }
        cache.set("technical", "SPY", "rsi", pack, 60, adapter=bars_adapter)
        hit = cache.get("technical", "SPY", "rsi", adapter=bars_adapter)
        self.assertEqual(hit["symbol"], "SPY")
        self.assertEqual(hit["candles"][0]["close"], 1.5)
        self.assertEqual(hit["oscillators"]["rsi"][0]["value"], 55.0)

    def test_flush_drops_keys(self):
        cache.cached("account", "_", "_", 60, lambda: {"ok": True})
        self.assertEqual(cache.flush(), 1)
        calls = []
        cache.cached("account", "_", "_", 60, lambda: calls.append(1) or {"ok": True})
        self.assertEqual(len(calls), 1)

    def test_disconnected_is_miss(self):
        cache.close()
        calls = []
        out = cache.cached("news", "AAPL", "x", 60, lambda: calls.append(1) or {"a": 1})
        self.assertEqual(out, {"a": 1})
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
