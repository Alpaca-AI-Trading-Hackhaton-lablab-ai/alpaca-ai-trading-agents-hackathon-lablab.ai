"""Tests for the ReAct loop: parse, allowlist, timeout, max_turns, HOLD fallback."""

import time
import unittest
from unittest.mock import patch

from agents.base import ReactAgent
from agents.nodes import DecisionReactAgent
from agents.react_core import parse_tool_call
from services import config


class ParseToolCallTest(unittest.TestCase):
    def test_tool_json(self):
        call = parse_tool_call('{"tool": "get_market_news", "params": {"symbol": "AAPL"}}')
        self.assertEqual(call["tool"], "get_market_news")
        self.assertEqual(call["params"]["symbol"], "AAPL")

    def test_final_answer_is_none(self):
        self.assertIsNone(parse_tool_call('{"sentiment": "NEUTRAL", "confidence": 0}'))

    def test_garbage_is_none(self):
        self.assertIsNone(parse_tool_call("not json at all"))


class DispatchTest(unittest.TestCase):
    def test_unknown_tool_rejected(self):
        obs = ReactAgent._dispatch({"tool": "place_order", "params": {}}, {})
        self.assertIn("not allowed", obs["error"])

    def test_tool_timeout(self):
        original = config.REACT_TOOL_TIMEOUT_S
        config.REACT_TOOL_TIMEOUT_S = 1

        def slow(**_kwargs):
            time.sleep(5)
            return "late"

        try:
            obs = ReactAgent._dispatch({"tool": "slow", "params": {}}, {"slow": slow})
            self.assertIn("timeout", obs["error"])
        finally:
            config.REACT_TOOL_TIMEOUT_S = original


class _LoopAgent(ReactAgent):
    node = "sentiment"
    max_turns = 2

    def system_prompt(self):
        return "sys"

    def goal(self, ctx):
        return "goal"

    def tools(self, ctx=None):
        return {"echo": lambda **_k: {"ok": True}}

    def finalize(self, text, ctx):
        if "HOLD" in (text or "") or '"action"' in (text or ""):
            return {"action": "HOLD", "text": text}
        return {"done": True, "text": text}

    def fallback(self, reason, ctx):
        return {"action": "HOLD", "rationale": f"fail-closed: {reason}", "confidence": 0}


class LoopTest(unittest.TestCase):
    def test_stops_when_no_tool_call(self):
        agent = _LoopAgent()
        chats = []

        def fake_chat(_reasoner, _messages):
            chats.append(1)
            return '{"sentiment": "NEUTRAL"}'

        agent._chat = fake_chat
        with patch("agents.base.Reasoner") as mock_reasoner:
            mock_reasoner.return_value = object()
            out = agent.run({"models": {"sentiment": "openai/gpt-oss-20b"}})
        self.assertEqual(len(chats), 1)
        self.assertTrue(out.get("done") or out.get("sentiment") or out.get("action"))

    def test_max_turns_caps_tool_loop(self):
        agent = _LoopAgent()
        chats = []

        def fake_chat(_reasoner, _messages):
            chats.append(1)
            return '{"tool": "echo", "params": {}}'

        agent._chat = fake_chat
        with patch("agents.base.Reasoner") as mock_reasoner:
            mock_reasoner.return_value = object()
            agent.run({"models": {"sentiment": "openai/gpt-oss-20b"}})
        self.assertEqual(len(chats), 2)

    def test_decision_fallback_is_hold(self):
        agent = DecisionReactAgent()
        out = agent.fallback(
            "boom",
            {
                "market_state": {
                    "symbol": "AAPL",
                    "technical_signal": "BUY",
                    "sentiment": "BULLISH",
                },
                "risk": {"position_size": 1000, "risk_level": "LOW"},
                "models": {"decision": "openai/gpt-oss-120b"},
            },
        )
        self.assertEqual(out["action"], "HOLD")
        self.assertIn("fail-closed", out["rationale"])
        self.assertEqual(out["confidence"], 0)
        self.assertEqual(out["model"], "openai/gpt-oss-120b")


if __name__ == "__main__":
    unittest.main()
