"""Groq reasoning wrapper + ReAct helpers.

Ports Gemini-Claw's patterns (decomposition, parallel fan-out, tool-call
parsing) onto our Groq stack. Used only by the proposal-side ReAct agents;
nothing here can trade.
"""

import concurrent.futures
import json
import re

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

from services import config, secrets


class Reasoner:
    """Thin wrapper over ChatGroq. Raises if GROQ_API_KEY is absent so callers
    fail closed instead of crashing mid-loop."""

    def __init__(self, model=None):
        api_key = secrets.groq_api_key()
        if not api_key:
            raise RuntimeError("Missing GROQ_API_KEY")
        self.model = model or config.GROQ_MODEL
        self._llm = ChatGroq(model=self.model, api_key=api_key)

    def chat(self, messages):
        """`messages` is a list of langchain messages, or a plain prompt string.
        Returns {"response": str}."""
        if isinstance(messages, str):
            messages = [HumanMessage(messages)]
        result = self._llm.invoke(messages)
        return {"response": getattr(result, "content", "") or ""}


def extract_json(text):
    """Best-effort JSON extraction: fenced block, else the outermost {...}/[...]."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if "{" in text and "}" in text:
        text = text[text.find("{") : text.rfind("}") + 1]
    elif "[" in text and "]" in text:
        text = text[text.find("[") : text.rfind("]") + 1]
    return json.loads(text)


def parse_tool_call(text):
    """Return {"tool", "params"} if the model emitted a tool call, else None
    (treated as the final answer). Never raises — fail-closed to 'done'."""
    try:
        data = extract_json(text)
        if isinstance(data, dict) and "tool" in data:
            return data
    except Exception:  # noqa: BLE001 - unparseable output means "no tool call"
        pass
    return None


def decompose(reasoner, goal, lo=3, hi=5):
    """Split a goal into lo..hi web-search sub-queries. Fail-closed to [goal]."""
    prompt = (
        f"Break the following goal into {lo}-{hi} specific, distinct web-search "
        f"queries that surface fresh, market-moving information. Return ONLY a "
        f'JSON list of strings, e.g. ["query 1", "query 2"].\n\nGoal: {goal}'
    )
    try:
        data = extract_json(reasoner.chat(prompt)["response"])
        if isinstance(data, list) and data:
            return [str(q) for q in data][:hi]
    except Exception:  # noqa: BLE001 - fall back to the original goal
        pass
    return [goal]


def parallel_map(callables, max_workers=3):
    """Run zero-arg callables concurrently, fault-isolated per task."""
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(c) for c in callables]
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:  # noqa: BLE001 - one failed task must not sink the batch
                results.append({"error": str(e)})
    return results
