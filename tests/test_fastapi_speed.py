"""FastAPI 0.131 response models: 410 dead GETs, BarsOut, SettingsOut."""

import os
import unittest
from unittest.mock import patch

import fakeredis
from fastapi.testclient import TestClient

from services import db


class FastApiSpeedTest(unittest.TestCase):
    def setUp(self):
        self._db = os.environ.get("DATABASE_URL")
        self._redis = os.environ.get("REDIS_URL")
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"
        self._fake = fakeredis.FakeRedis(decode_responses=False)
        self._patch = patch("redis.Redis.from_url", return_value=self._fake)
        self._patch.start()
        import backend

        self._analysis = backend._analysis
        backend._analysis = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("_analysis must not run")
        )
        self._ctx = TestClient(backend.app)
        self.client = self._ctx.__enter__()

    def tearDown(self):
        import backend

        backend._analysis = self._analysis
        self._ctx.__exit__(None, None, None)
        self._patch.stop()
        db.close()
        if self._db is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._db
        if self._redis is None:
            os.environ.pop("REDIS_URL", None)
        else:
            os.environ["REDIS_URL"] = self._redis

    def test_decision_is_gone(self):
        res = self.client.get("/decision?symbol=AAPL")
        self.assertEqual(res.status_code, 410)
        body = res.json()
        self.assertEqual(body["use"], "/pipeline")
        self.assertEqual(body["status"], "gone")

    def test_settings_shape(self):
        res = self.client.get("/settings")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("keys", body)
        self.assertIn("agents", body)
        self.assertIn(body["keys"]["groq"], ("db", "env", "missing"))

    def test_bars_schema(self):
        res = self.client.get("/bars?symbol=SPY&indicators=sma20,rsi")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["symbol"], "SPY")
        self.assertTrue(body["candles"])
        candle = body["candles"][-1]
        for key in ("time", "open", "high", "low", "close"):
            self.assertIn(key, candle)


if __name__ == "__main__":
    unittest.main()
