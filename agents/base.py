"""Agent base class + generic ReAct loop.

Every pipeline node is an `Agent`: `run(ctx)` computes its output from the
accumulated context, `message(out)` is its human-readable "communication".
`ReactAgent` adds a bounded reason -> act -> observe loop (the agent decides
when to stop; `max_turns` caps it) for the proposal-side agents.

Invariant: the deterministic authority (the execution gate) is a plain `Agent`,
never a `ReactAgent`. A ReAct loop only ever sees read-only tools — it proposes,
it never trades.
"""

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.react_core import Reasoner, parse_tool_call

_OBS_TRUNC = 8000  # cap a tool observation fed back into the prompt


class Agent:
    node: str = ""

    def run(self, ctx: dict) -> Any:
        raise NotImplementedError

    def message(self, out: Any) -> str:
        return ""


class ReactAgent(Agent):
    """Bounded reason -> act -> observe loop on Groq. Subclasses override the
    hooks below; the loop, termination, and fail-closed handling live here and
    should not be overridden."""

    max_turns: int = 3

    # --- hooks a subclass overrides ---
    def system_prompt(self) -> str:
        raise NotImplementedError

    def goal(self, ctx: dict) -> str:
        raise NotImplementedError

    def tools(self) -> dict:
        """name -> read-only callable available to this loop."""
        return {}

    def seed(self, ctx: dict, reasoner: Reasoner) -> str:
        """Optional evidence gathered once before the loop (e.g. decompose +
        parallel fan-out). Default: none."""
        return ""

    def finalize(self, text: str, ctx: dict) -> Any:
        """Parse the model's final answer into the node's structured output.
        Raise to trigger the fail-closed fallback."""
        raise NotImplementedError

    def fallback(self, reason: str, ctx: dict) -> Any:
        """Fail-closed output when the loop can't run or can't produce a valid
        answer (no API key, exception, unparseable final answer)."""
        raise NotImplementedError

    # --- the loop (do not override) ---
    def run(self, ctx: dict) -> Any:
        try:
            reasoner = Reasoner()  # raises if GROQ_API_KEY is missing
            tools = self.tools()
            messages = [SystemMessage(self.system_prompt())]
            seed = self.seed(ctx, reasoner)
            goal = self.goal(ctx)
            messages.append(HumanMessage(f"{goal}\n\n{seed}".strip()))

            text = ""
            for _ in range(max(1, self.max_turns)):
                text = self._chat(reasoner, messages)
                call = parse_tool_call(text)
                if not call:
                    return self.finalize(text, ctx)  # the agent decided it's done
                messages.append(AIMessage(text))
                obs = self._dispatch(call, tools)
                observation = json.dumps(obs, default=str)[:_OBS_TRUNC]
                messages.append(HumanMessage(f"TOOL_OUTPUT: {observation}"))

            # Hit max_turns: try to finalize the last answer, else fail closed.
            return self.finalize(text, ctx)
        except Exception as e:  # noqa: BLE001 - the proposal side must fail closed
            return self.fallback(str(e), ctx)

    @staticmethod
    def _chat(reasoner: Reasoner, messages: list) -> str:
        return reasoner.chat(messages).get("response", "") or ""

    @staticmethod
    def _dispatch(call: dict, tools: dict) -> dict:
        name = call.get("tool")
        params = call.get("params") or {}
        fn = tools.get(name)
        if fn is None:
            return {"error": f"tool not allowed: {name}"}
        try:
            return {"result": fn(**params)}
        except Exception as e:  # noqa: BLE001 - a bad tool call must not crash the loop
            return {"error": str(e)}
