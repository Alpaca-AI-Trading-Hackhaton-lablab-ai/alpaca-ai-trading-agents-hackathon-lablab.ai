"""Tests for Groq model allowlist + per-agent resolve."""

import unittest
from unittest.mock import patch

from services import config


class ResolveModelsTest(unittest.TestCase):
    def test_role_defaults(self):
        self.assertEqual(config.GROQ_DEFAULT_SENTIMENT, "openai/gpt-oss-20b")
        self.assertEqual(config.GROQ_DEFAULT_DECISION, "qwen/qwen3.8-27b")
        with patch.object(config, "GROQ_MODEL_SENTIMENT", config.GROQ_DEFAULT_SENTIMENT):
            with patch.object(
                config, "GROQ_MODEL_DECISION", config.GROQ_DEFAULT_DECISION
            ):
                models = config.resolve_models()
        self.assertEqual(models["sentiment"], "openai/gpt-oss-20b")
        self.assertEqual(models["decision"], "qwen/qwen3.8-27b")

    def test_request_override(self):
        models = config.resolve_models(
            sentiment_model="openai/gpt-oss-120b",
            decision_model="qwen/qwen3.8-27b",
        )
        self.assertEqual(models["sentiment"], "openai/gpt-oss-120b")
        self.assertEqual(models["decision"], "qwen/qwen3.8-27b")

    def test_unknown_request_raises(self):
        with self.assertRaises(config.UnknownModelError) as ctx:
            config.resolve_models(sentiment_model="gpt-4o")
        self.assertIn("sentiment", str(ctx.exception))

    def test_catalog_ids(self):
        catalog = config.models_catalog()
        ids = {item["id"] for item in catalog["allowlist"]}
        self.assertEqual(
            ids,
            {
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "qwen/qwen3.6-27b",
                "qwen/qwen3.8-27b",
            },
        )
        with patch.object(config, "GROQ_MODEL_SENTIMENT", config.GROQ_DEFAULT_SENTIMENT):
            with patch.object(
                config, "GROQ_MODEL_DECISION", config.GROQ_DEFAULT_DECISION
            ):
                catalog = config.models_catalog()
        self.assertEqual(catalog["defaults"]["sentiment"], "openai/gpt-oss-20b")
        self.assertEqual(catalog["defaults"]["decision"], "qwen/qwen3.8-27b")


if __name__ == "__main__":
    unittest.main()
