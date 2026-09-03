"""API usage meter, degrade ladder, and /usage redaction."""

import json
import os
import unittest
from unittest.mock import patch

from services import config, db, persist, scheduler, usage_meter


class FakeGroqMsg:
    usage_metadata = {
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
    }
    response_metadata = {
        "token_usage": {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 20,
        },
        "x-ratelimit-remaining-tokens": "900",
    }


class UsageMeterTest(unittest.TestCase):
    def setUp(self):
        db.connect("sqlite:///:memory:")
        usage_meter.reset_runtime()
        config.set_kill(False)

    def tearDown(self):
        usage_meter.reset_runtime()
        config.set_kill(False)
        db.close()

    def test_capture_groq_records_tokens(self):
        captured = usage_meter.capture_groq(FakeGroqMsg(), model="openai/gpt-oss-20b")
        self.assertEqual(captured["total_tokens"], 20)
        snap = usage_meter.snapshot("groq")
        self.assertEqual(snap["used"], 20)
        self.assertEqual(snap["state"], "OK")

    def test_warn_forces_deep_off(self):
        usage_meter.save_budgets(
            [
                {
                    "provider": "groq",
                    "scope": "window",
                    "limit_type": "tokens",
                    "limit_value": 100,
                    "warn_pct": 80,
                    "action": "block_degrade",
                }
            ]
        )
        usage_meter.record("groq", tokens=80)
        usage_meter.apply_degrade()
        self.assertTrue(usage_meter.deep_forced_off())
        opts = persist.merge_pipeline_opts(deep=True)
        self.assertFalse(opts["deep_sentiment"])
        self.assertFalse(opts["deep_decision"])

    def test_over_stops_scheduler_and_blocks_gate(self):
        usage_meter.save_budgets(
            [
                {
                    "provider": "groq",
                    "scope": "window",
                    "limit_type": "tokens",
                    "limit_value": 10,
                    "warn_pct": 80,
                    "action": "block_degrade",
                }
            ]
        )
        usage_meter.record("groq", tokens=10)
        ok, provider = usage_meter.budget_ok()
        self.assertFalse(ok)
        self.assertEqual(provider, "groq")
        scheduler.save({"enabled": True})
        with patch("services.book.run_tick") as tick:
            out = scheduler.run_once()
            tick.assert_not_called()
        self.assertFalse(out["enabled"])
        from agents.execution_gate import evaluate_gate

        gate = evaluate_gate(
            {"action": "BUY", "symbol": "MSFT", "position_size": 100},
            {"equity": 100_000, "buying_power": 50_000, "mode": "paper"},
            [],
            [],
            {"is_open": True},
            max_credit=500,
        )
        self.assertEqual(gate["verdict"], "BLOCK")

    def test_usage_payload_has_no_secrets(self):
        os.environ["GROQ_API_KEY"] = "gsk_secret_should_not_leak"
        os.environ["TAVILY_API_KEY"] = "tvly-secret-should-not-leak"
        os.environ["ALPACA_API_KEY"] = "AK_SECRET"
        os.environ["ALPACA_SECRET_KEY"] = "SEC_SECRET"
        usage_meter.record("groq", tokens=1)
        from backend import usage_get

        body = usage_get()
        blob = json.dumps(body)
        self.assertNotIn("gsk_secret", blob)
        self.assertNotIn("tvly-secret", blob)
        self.assertNotIn("AK_SECRET", blob)
        self.assertNotIn("SEC_SECRET", blob)
        self.assertNotIn("api_key", blob.lower())
        self.assertIn("entries", body)


if __name__ == "__main__":
    unittest.main()
