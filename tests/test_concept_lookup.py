"""DuckDuckGo Instant Answer concept lookup (mocked network)."""

import json
import unittest
from unittest.mock import patch

from services import cache, concept_lookup


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class ConceptLookupTest(unittest.TestCase):
    def test_empty_query(self):
        out = concept_lookup.lookup_concept("")
        self.assertFalse(out["found"])
        self.assertEqual(out["reason"], "empty query")

    def test_abstract(self):
        payload = {
            "Heading": "Relative strength index",
            "AbstractText": "A momentum oscillator.",
            "AbstractSource": "Wikipedia",
            "AbstractURL": "https://en.wikipedia.org/wiki/RSI",
            "RelatedTopics": [],
        }
        with patch(
            "services.concept_lookup.urllib.request.urlopen",
            return_value=_Resp(payload),
        ):
            out = concept_lookup.lookup_concept("RSI indicator")
        self.assertTrue(out["found"])
        self.assertEqual(out["heading"], "Relative strength index")
        self.assertIn("momentum", out["text"])
        self.assertEqual(out["source"], "Wikipedia")

    def test_related_when_no_abstract(self):
        payload = {
            "AbstractText": "",
            "RelatedTopics": [
                {
                    "Name": "Finance",
                    "Topics": [
                        {
                            "FirstURL": "https://example.com/macd",
                            "Text": "MACD — moving average convergence",
                        }
                    ],
                }
            ],
        }
        with patch(
            "services.concept_lookup.urllib.request.urlopen",
            return_value=_Resp(payload),
        ):
            out = concept_lookup.lookup_concept("MACD")
        self.assertTrue(out["found"])
        self.assertEqual(out["related"][0]["text"][:4], "MACD")

    def test_outage_fail_closed(self):
        with patch(
            "services.concept_lookup.urllib.request.urlopen",
            side_effect=OSError("down"),
        ):
            out = concept_lookup.lookup_concept("Federal Reserve")
        self.assertFalse(out["found"])
        self.assertEqual(out["reason"], "lookup unavailable")

    def test_registry_exposes_tool(self):
        from agents import research_tools

        self.assertIn("lookup_concept", research_tools.REGISTRY)
        tools = research_tools.subset("lookup_concept")
        self.assertEqual(set(tools), {"lookup_concept"})


class ConceptCacheTest(unittest.TestCase):
    def setUp(self):
        import fakeredis

        cache.connect(client=fakeredis.FakeRedis(decode_responses=False))

    def tearDown(self):
        cache.close()

    def test_second_call_skips_network(self):
        calls = []
        payload = {"AbstractText": "A central bank.", "Heading": "Fed"}

        def fake_open(*_a, **_k):
            calls.append(1)
            return _Resp(payload)

        with patch(
            "services.concept_lookup.urllib.request.urlopen",
            side_effect=fake_open,
        ):
            first = concept_lookup.lookup_concept("Federal Reserve")
            second = concept_lookup.lookup_concept("federal reserve")
        self.assertEqual(first["text"], second["text"])
        self.assertEqual(len(calls), 1)
