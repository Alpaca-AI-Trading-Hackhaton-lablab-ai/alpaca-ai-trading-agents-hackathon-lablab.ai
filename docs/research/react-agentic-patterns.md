# ReAct & Agentic Patterns — Gemini-Claw study + investigation

Research notes for the TradeLix / Alpaca hardening backend. Source study of
[gyunggyung/Gemini-Claw](https://github.com/gyunggyung/Gemini-Claw) (cloned read-only to
`../Gemini-Claw`) plus a general ReAct investigation. The synthesis for *our* backend lives in
[`../plans/react-integration-plan.md`](../plans/react-integration-plan.md); this file is the
"what and why", that file is the "how here".

> Guardrail for everything below: in our system the LLM (even when it loops) only **proposes**. The
> deterministic execution gate (`agents/execution_gate.py`) and the executor stay the authority.
> ReAct raises the quality of the *proposal*, never the *authority to trade*.

---

## 1. Gemini-Claw at a glance

An autonomous research/synthesis agent — "give it a goal, and it claws its way to the answer".
Pure-Python ReAct loop over the **Gemini CLI**, no heavy framework (LangChain/LangGraph absent by
design). Python 3.10+, `uv`, `rich` for the console.

| File | Role |
|------|------|
| `src/agent/loop.py` | **The ReAct loop** (`AgentLoop.execute_with_retry`) — primary logic |
| `src/agent/prompts.py` | System prompt: JSON tool schema + tool catalog + rules |
| `src/agent/tools.py` | `ToolRegistry` — whitelisted tools (fs, git, safe shell) |
| `src/agent/core.py` | `GeminiAgent` — CLI wrapper, JSON parse, session resume, telemetry |
| `src/agent/decomposition.py` | `QueryDecomposer.decompose()` — goal → 3–5 sub-queries |
| `src/agent/parallel.py` | `ParallelExecutor.execute()` — ThreadPool fan-out |
| `src/agent/search.py`, `fetch.py` | web search / fetch prompt wrappers |
| `src/main.py` | CLI entry + telemetry dashboard |

---

## 2. Strong patterns to port ("acople")

| # | Pattern | Where (Gemini-Claw) | Why it matters for us |
|---|---------|---------------------|-----------------------|
| P1 | **ReAct loop with agent-controlled termination** | `loop.py:17` `execute_with_retry(query, max_turns=15)` | The agent keeps acting until it emits *no tool call* (it decided it's done); a hard `max_turns` cap backstops it. This is exactly the "agent decides whether to continue" behavior we want. |
| P2 | **Fail-closed tool-call parsing** | `loop.py:93` `_parse_tool_call` | JSON from ```json fences``` or brace-delimited text; unparseable → `None` → treated as the final answer. The loop never crashes on malformed model output. |
| P3 | **Observation feedback + truncation** | `loop.py:72-76` | Tool result re-injected as `TOOL_OUTPUT: {json}`; truncated at 100k chars to bound context/cost. |
| P4 | **Query decomposition** | `decomposition.py:10` `decompose(query) -> List[str]` | LLM splits a goal into 3–5 distinct search queries (JSON list); **falls back to `[query]`** on parse failure. Turns one vague question into targeted retrieval. |
| P5 | **Fault-isolated parallel fan-out** | `parallel.py:6` `execute(tasks, max_workers=3)` | ThreadPool; a failing task returns `{"error": …}` instead of killing the batch. Good for running the sub-queries concurrently. |
| P6 | **Whitelisted tool registry** | `tools.py:14` `allowed_commands` + `safe_binaries` + `_validate_path` | Deterministic allowlist, path sandboxing, `rm -rf /` guard, non-interactive git (`GIT_PAGER=cat`). The tool layer is where safety is enforced, not the prompt. |
| P7 | **Explicit tool contract in the system prompt** | `prompts.py:13-53` | Rigid JSON schema `{"tool", "params"}`, "JSON only for tools", "read before you assume". A clear contract is what makes P2 parseable. |
| P8 | **Model wrapper + session continuity** | `core.py:23` `run()` | Subprocess wrapper, robust JSON extraction (first `{` … last `}`), captures `sessionId` and resumes with `--resume` so later turns keep context cheaply. |

---

## 3. ReAct — the general pattern (investigation)

**Origin.** ReAct (Yao et al., 2022) interleaves *reasoning traces* and *actions*: at each step the
agent first articulates a **Thought** (why), emits an **Action** (tool + args), receives an
**Observation** (tool output injected back), and repeats until a final answer. Reasoning helps it
decompose tasks, track progress, handle exceptions, and re-plan; acting grounds the reasoning in real
data — reducing the hallucination/error-propagation seen in pure chain-of-thought. It beat imitation/
RL baselines on ALFWorld (+34%) and WebShop (+10%). ([paper](https://arxiv.org/pdf/2210.03629),
[site](https://react-lm.github.io/), [Google](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/))

**Loop shape (2026 practice).** Thought → Action → Observation → … → Final Answer. Termination is
either the agent emitting a "final answer" (no tool) **or** a hard cap.
([Data Science Dojo](https://datasciencedojo.com/blog/agentic-loops-explained-from-react-to-loop-engineering-2026-guide/),
[apxml](https://apxml.com/courses/agentic-llm-memory-architectures/chapter-2-advanced-agent-architectures-reasoning/implementing-react-agents))

**Production guardrails (must-haves).** Hard iteration cap (typ. 10–25); token/cost budget from day
one; **no-progress detection** (exit when iterations stop producing new info); circuit breakers /
retry limits on tool calls with backoff; termination criteria defined up front (prefer verifiable
checks over agent self-assessment); and **human-in-the-loop before irreversible actions**
(DB writes, deployments, external calls — *for us, that's placing an order*).
([MindStudio](https://www.mindstudio.ai/blog/agent-loops-explained-trigger-action-stop-condition),
[Arthur guardrails](https://www.arthur.ai/blog/best-practices-for-building-agents-guardrails),
[RockB 2026 guide](https://baeseokjae.github.io/posts/react-agent-pattern-guide-2026/))

**Reflection / Reflexion.** A post-run self-evaluation step turns one-shot ReAct into a self-improving
loop: Run → Evaluate output vs task criteria → if weak, generate a reflection → retry with updated
context. Useful for the decision step ("is this proposal well-supported?").

---

## 4. Robustness lessons & anti-patterns observed

Port the *patterns*, not the rough edges. Things to **do better than** the reference:

- **`--approval-mode yolo`** (`core.py:33`) runs every tool with no confirmation — acceptable for a
  local research toy, unacceptable near trading. Our answer: the ReAct loop gets **read-only tools
  only**; the one irreversible action (order submit) lives *outside* the loop, behind the gate.
- **Turn>0 prompt bug** (`core.py:38-39`): `full_prompt` is only assigned when `system_prompt` is
  truthy, so resumed turns would raise `NameError`. Lesson: always construct the turn input
  unconditionally, and unit-test the multi-turn path.
- **No no-progress / cost budget**: Gemini-Claw caps only `max_turns`. We should add no-progress
  detection and a token/tool-call budget (see the plan).
- **Truncation at 100k** (`loop.py:75`) is coarse; fine for a demo, but we should summarize
  observations rather than hard-cut where it matters.
- **Regex URL/JSON scraping** (`search.py`, `core.py`) is brittle; we have structured Alpaca/Tavily
  responses, so we can parse typed data instead.

---

## 5. Mapping to the Alpaca pipeline

Current pipeline (`backend.py:run_pipeline`) runs each agent **once**, linearly:
`news → sentiment → options; features; technical; account → market_state → risk → decision → gate`.
No agent loops. Mapping the ported patterns:

| Pattern | Applies to | Effect |
|---------|-----------|--------|
| P4 decomposition | `news`/`sentiment` (`agents/sentiment_agent.py`, `services/news_service.py`) | "What's driving {symbol}?" → 3–5 Tavily sub-queries instead of one blind search |
| P5 parallel fan-out | the sub-queries | fetch concurrently, fault-isolated |
| P1 ReAct loop + P2 fail-closed | a new **research agent** and the **decision step** (`agents/decision_agent.py`) | iterate until confident (agent-terminated), capped; invalid output → NEUTRAL/HOLD |
| P6/P7 tool registry + contract | a **read-only** proposal toolset (`get_market_news`, `get_market_features`, `technical_analysis`, `get_account_info`) | the loop can *gather*, never *trade* |
| P8 model wrapper | our **Groq** client (not gemini-cli) | one place for retries/telemetry/JSON extraction |
| — (stays deterministic) | `execution_gate.py`, executor, `/execute` | **unchanged**; still the sole authority |

**Net:** ReAct improves the *proposal* (richer research, self-checked decision); the deterministic
governor still authorizes. Details, contracts, and cost controls: the plan doc.

---

## Sources
- ReAct: Synergizing Reasoning and Acting in LMs — [arXiv 2210.03629](https://arxiv.org/pdf/2210.03629) · [project site](https://react-lm.github.io/) · [Google Research](https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/)
- Agentic loops / loop engineering (2026) — [Data Science Dojo](https://datasciencedojo.com/blog/agentic-loops-explained-from-react-to-loop-engineering-2026-guide/)
- Implementing ReAct agents — [apxml](https://apxml.com/courses/agentic-llm-memory-architectures/chapter-2-advanced-agent-architectures-reasoning/implementing-react-agents)
- Agent loops: trigger/action/stop — [MindStudio](https://www.mindstudio.ai/blog/agent-loops-explained-trigger-action-stop-condition)
- Agent guardrails best practices — [Arthur](https://www.arthur.ai/blog/best-practices-for-building-agents-guardrails)
- ReAct pattern developer guide (2026) — [RockB](https://baeseokjae.github.io/posts/react-agent-pattern-guide-2026/)
- Reference implementation — [gyunggyung/Gemini-Claw](https://github.com/gyunggyung/Gemini-Claw)
