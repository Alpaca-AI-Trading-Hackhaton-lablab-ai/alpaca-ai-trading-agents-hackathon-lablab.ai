"""Agent base class + generic ReAct loop.

Every pipeline node is an `Agent`: `run(ctx)` computes its output from the
accumulated context, `message(out)` is its human-readable "communication".
`ReactAgent` adds a bounded reason -> act -> observe loop (the agent decides
when to stop; `max_turns` caps it) for the proposal-side agents.

Invariant: the deterministic authority (the execution gate) is a plain `Agent`,
never a `ReactAgent`. A ReAct loop only ever sees read-only tools — it proposes,
it never trades.
"""

import concurrent.futures
import json
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.react_core import Reasoner, parse_tool_call
from services import config, logs

_OBS_TRUNC = 8000  # cap a tool observation fed back into the prompt
_SSE_TRUNC = 400  # shorter cap for SSE sub-events


class Agent:
    node: str = ""

    def run(self, ctx: dict) -> Any:
        raise NotImplementedError

    def message(self, out: Any) -> str:
        return ""

    @staticmethod
    def _err(out):
        if isinstance(out, dict) and out.get("error"):
            return str(out["error"])
        return None


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

    def tools(self, ctx=None):
        """name -> read-only callable available to this loop."""
        return {}

    def seed(self, ctx: dict, reasoner: Reasoner) -> str:
        """Optional evidence gathered once before the loop (e.g. news already
        in ctx). Default: none."""
        return ""

    def finalize(self, text: str, ctx: dict) -> Any:
        """Parse the model's final answer into the node's structured output.
        Raise to trigger the fail-closed fallback."""
        raise NotImplementedError

    def fallback(self, reason: str, ctx: dict) -> Any:
        """Fail-closed output when the loop can't run or can't produce a valid
        answer (no API key, exception, unparseable final answer)."""
        raise NotImplementedError

    def _model_id(self, ctx: dict) -> str:
        models = ctx.get("models") or {}
        return models.get(self.node) or config.resolve_models().get(self.node)

    # --- the loop (do not override) ---
    def run(self, ctx: dict) -> Any:
        out = None
        for ev in self.iter_run(ctx):
            if ev.get("kind") == "result":
                out = ev["output"]
        return out

    def iter_run(self, ctx: dict):
        """Yield ReAct turn events, then a final `{kind: result, output}`.
        `run()` drains this; `run_pipeline` streams the turn events as SSE."""
        try:
            reasoner = Reasoner(model=self._model_id(ctx))
            tools = self.tools(ctx)
            messages = [SystemMessage(self.system_prompt())]
            seed = self.seed(ctx, reasoner)
            goal = self.goal(ctx)
            messages.append(HumanMessage(f"{goal}\n\n{seed}".strip()))

            text = ""
            for turn in range(max(1, self.max_turns)):
                t0 = time.perf_counter()
                text = self._chat(reasoner, messages)
                logs.record(
                    run_id=ctx.get("run_id"),
                    symbol=ctx.get("symbol"),
                    agent_id=self.node,
                    kind="llm",
                    model=getattr(reasoner, "model", None),
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    status="ok",
                    summary=(text or "")[:200],
                )
                call = parse_tool_call(text)
                if not call:
                    yield {
                        "kind": "react",
                        "turn": turn,
                        "thought": (text or "")[:_SSE_TRUNC],
                    }
                    yield {
                        "kind": "result",
                        "output": self.finalize(text, ctx),
                    }
                    return
                tool_name = call.get("tool")
                yield {
                    "kind": "react",
                    "turn": turn,
                    "thought": (text or "")[:_SSE_TRUNC],
                    "tool": tool_name,
                }
                messages.append(AIMessage(text))
                t0 = time.perf_counter()
                obs = self._dispatch(call, tools)
                logs.record(
                    run_id=ctx.get("run_id"),
                    symbol=ctx.get("symbol"),
                    agent_id=self.node,
                    kind="tool",
                    model=getattr(reasoner, "model", None),
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    status="error" if isinstance(obs, dict) and obs.get("error") else "ok",
                    summary=str(tool_name),
                    payload={"tool": tool_name, "observation": obs},
                )
                observation = json.dumps(obs, default=str)[:_OBS_TRUNC]
                yield {
                    "kind": "react",
                    "turn": turn,
                    "tool": tool_name,
                    "observation": observation[:_SSE_TRUNC],
                }
                messages.append(HumanMessage(f"TOOL_OUTPUT: {observation}"))

            yield {"kind": "result", "output": self.finalize(text, ctx)}
        except Exception as e:  # noqa: BLE001 - the proposal side must fail closed
            yield {"kind": "result", "output": self.fallback(str(e), ctx)}

    def _chat(self, reasoner: Reasoner, messages: list) -> str:
        return reasoner.chat(messages).get("response", "") or ""

    @staticmethod
    def _dispatch(call: dict, tools: dict) -> dict:
        name = call.get("tool")
        params = call.get("params") or {}
        fn = tools.get(name)
        if fn is None:
            return {"error": f"tool not allowed: {name}"}
        timeout = max(1, config.REACT_TOOL_TIMEOUT_S)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(fn, **params)
                return {"result": future.result(timeout=timeout)}
        except concurrent.futures.TimeoutError:
            return {"error": f"tool timeout after {timeout}s: {name}"}
        except Exception as e:  # noqa: BLE001 - a bad tool call must not crash the loop
            return {"error": str(e)}
