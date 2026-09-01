"""DB vs env secret resolution + GET /settings never leaks key values."""

import json
import os
import unittest
from unittest.mock import patch

import fakeredis
from fastapi.testclient import TestClient

from services import db, persist, secrets


class SecretResolverTest(unittest.TestCase):
    def setUp(self):
        self._env = {
            name: os.environ.get(name)
            for name in (
                "GROQ_API_KEY",
                "TAVILY_API_KEY",
                "ALPACA_API_KEY",
                "ALPACA_SECRET_KEY",
            )
        }
        os.environ["GROQ_API_KEY"] = "env-groq"
        os.environ["TAVILY_API_KEY"] = ""
        db.connect("sqlite:///:memory:")
        secrets.invalidate()

    def tearDown(self):
        db.close()
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_env_when_db_empty(self):
        self.assertEqual(secrets.groq_api_key(), "env-groq")
        self.assertEqual(secrets.source("groq"), "env")
        self.assertEqual(secrets.source("tavily"), "missing")

    def test_db_overrides_env(self):
        persist.update({"keys": {"groq": "db-groq"}})
        self.assertEqual(secrets.groq_api_key(), "db-groq")
        self.assertEqual(secrets.source("groq"), "db")

    def test_empty_string_clears_db(self):
        persist.update({"keys": {"groq": "db-groq"}})
        persist.update({"keys": {"groq": ""}})
        self.assertEqual(secrets.groq_api_key(), "env-groq")
        self.assertEqual(secrets.source("groq"), "env")

    def test_public_view_never_includes_secret(self):
        marker = "SECRETVALUE_GROQ_XYZ"
        persist.update({"keys": {"groq": marker}})
        view = persist.public_view()
        blob = json.dumps(view)
        self.assertNotIn(marker, blob)
        self.assertNotIn("env-groq", blob)
        self.assertEqual(view["keys"]["groq"], "db")
        self.assertIn("sentiment", view["agents"])
        self.assertIn("decision", view["agents"])

    def test_second_get_secret_skips_db(self):
        secrets.groq_api_key()
        with patch("services.secrets._account_row") as mock_row:
            again = secrets.groq_api_key()
            mock_row.assert_not_called()
        self.assertEqual(again, "env-groq")


class SettingsApiTest(unittest.TestCase):
    def setUp(self):
        self._db = os.environ.get("DATABASE_URL")
        self._redis = os.environ.get("REDIS_URL")
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"
        self._fake = fakeredis.FakeRedis(decode_responses=False)
        self._patch = patch("redis.Redis.from_url", return_value=self._fake)
        self._patch.start()
        import backend

        self._ctx = TestClient(backend.app)
        self.client = self._ctx.__enter__()

    def tearDown(self):
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

    def test_get_settings_redacts_keys(self):
        marker = "SECRETVALUE_API_TEST"
        put = self.client.put("/settings", json={"keys": {"groq": marker}})
        self.assertEqual(put.status_code, 200)
        got = self.client.get("/settings")
        self.assertEqual(got.status_code, 200)
        blob = got.text
        self.assertNotIn(marker, blob)
        self.assertEqual(got.json()["keys"]["groq"], "db")


if __name__ == "__main__":
    unittest.main()
