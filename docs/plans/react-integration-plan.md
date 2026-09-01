# ReAct integration plan — TradeLix / Alpaca hardening backend

**Status:** implemented on the proposal side (v1). Loop + `?deep=true` + per-agent Groq
models + SSE `react` sub-events. Companion to
[`../research/react-agentic-patterns.md`](../research/react-agentic-patterns.md).

## Goal

Raise the quality of the *proposal side* of the pipeline by porting Gemini-Claw's ReAct patterns
onto our Groq stack: a **bounded research loop** and a **decision reasoning loop**, both of which
the agent can **choose to continue or stop** (dynamic self-termination), backstopped by hard caps.

## The one invariant (non-negotiable)

```
   probabilistic intelligence (ReAct loops)      ← may reason, gather, iterate, self-terminate
 ─────────────────────────────────────────────
   deterministic authority (execution gate)      ← UNCHANGED: sole authority to trade
   executor + broker reconciliation              ← UNCHANGED
```

- The ReAct loops run **entirely above `agents/execution_gate.py`**. They never call an order tool.
- `evaluate_gate()`, `execute_trade()`, `POST /execute`, `/control`, `/audit` are **untouched**.
- The decision loop still emits a proposal `{action, position_size, …}` that flows into the
  **same deterministic gate**, which still authorizes / blocks / dry-runs exactly as today.

## Component 1 — Research ReAct agent (replaces single-shot `news → sentiment`)

Today: `get_market_news()` (one Tavily query or demo) → `analyze_sentiment()` (one Groq call).
Proposed: a bounded loop that researches "what is driving {symbol} right now?"

1. **Decompose** (port P4): Groq turns the goal into 3–5 sub-queries; fail-closed fallback to
   `[base_query]` on parse error.
2. **Fan-out** (port P5): run the sub-queries through Tavily concurrently (`ThreadPoolExecutor`,
   `max_workers=3`), fault-isolated per query.
3. **Reason/iterate** (port P1+P2): Groq reads the gathered evidence and either requests one more
   targeted sub-query (**agent decides to continue**) or emits a final structured
   sentiment `{sentiment, confidence, summary, trade_bias, key_points}` (**agent decides it's done**).
4. **Bounds**: `RESEARCH_MAX_TURNS` (default 3–4), per-tool timeout, observation truncation,
   no-progress exit. Any failure → **NEUTRAL** (fail-closed), never blocks the pipeline.

Read-only tools available to this loop: `get_market_news` (Tavily). Output shape stays compatible
with the existing `sentiment` contract so `build_market_state` / `make_decision` don't change.

## Component 2 — Decision reasoning loop (upgrades `make_decision`)

Today: `agents/decision_agent.py` is a pure `if/else` on sentiment × technical → BUY/SELL/HOLD.
Proposed: a bounded ReAct/Reflexion loop (Groq) on top of the same inputs.

- The loop may **request more evidence** via read-only tools (`get_market_features`,
  `technical_analysis`, re-run a research sub-query) and **decides when to stop**.
- A **reflection step** checks the draft proposal against the evidence ("is BUY well-supported given
  RSI/trend/sentiment?") before finalizing.
- Emits a structured proposal `{action, position_size, technical_signal, sentiment, risk_level,
  rationale, confidence}` — a superset of today's decision dict (adds `rationale`, `confidence`).
- **Fail-closed**: invalid/low-confidence/degraded output → `HOLD`. `position_size` still comes from
  the deterministic `calculate_risk` (the LLM does not size positions).

The proposal then hits `evaluate_gate()` unchanged.

## Read-only tool registry (port P6/P7)

A new `agents/research_tools.py` exposing a whitelist that wraps existing service functions:

| Tool | Wraps | Read-only |
|------|-------|-----------|
| `get_market_news(symbol, query?)` | `services/news_service.py` | ✅ |
| `get_market_features(symbol)` | `agents/feature_agent.py` | ✅ |
| `technical_analysis(symbol)` | `agents/technical_agent.py` | ✅ |
| `get_account_info()` | `services/alpaca_service.py` | ✅ |

**No `submit_market_order`, no `execute_trade`, no gate bypass.** The registry validates the tool
name against the allowlist and returns `{"error": …}` for anything else (mirror `ToolRegistry.execute`).

## Model wrapper (port P8, on Groq)

A small `agents/react_core.py` `ReactAgent` around the existing `ChatGroq` usage:
`run(prompt, system_prompt) -> {response, meta}` with robust JSON extraction (first `{`…last `}`),
one place for retries/backoff, timeout, and telemetry (turns, tokens, latency). No `gemini-cli`.

## Where it plugs into the pipeline

- `run_pipeline` (`backend.py`) keeps its node contract. The `sentiment` step calls the research
  agent; the `decision` step calls the reasoning loop. `market_state`, `risk`, `account`, **`gate`**
  are unchanged.
- **SSE**: optionally surface the loop's turns as sub-events (thought/tool/observation) so the widget
  can show the research/decision "thinking" — but the graph's node set can stay as-is for v1.
- **Trigger / cost control**: the ReAct path is **opt-in**, NOT auto-run on every symbol change (the
  current auto-stream would otherwise multiply Groq/Tavily spend). Gate it behind an explicit
  "Deep research" action or a `?deep=true` flag on `/pipeline`, defaulting to the cheap single-shot
  path. Add a per-run token/tool-call budget.

## Config (new flags, consistent with `services/config.py`)

`RESEARCH_MAX_TURNS=3`, `DECISION_MAX_TURNS=3`, `REACT_TOOL_TIMEOUT_S=8`,
`DEEP_RESEARCH_DEFAULT=false`, plus per-agent Groq IDs (`GROQ_MODEL_SENTIMENT`,
`GROQ_MODEL_DECISION`, fallback `GROQ_MODEL`) constrained to the Free allowlist.
Requires `GROQ_API_KEY` (and `TAVILY_API_KEY` for real news) to do anything beyond
the fail-closed NEUTRAL/HOLD path. Dashboard: `GET /models` + query
`sentiment_model` / `decision_model`.

## Phased implementation (follow-up pass, after this plan is approved)

1. `react_core.py` (Groq wrapper) + `research_tools.py` (read-only registry) + unit tests.
2. Research agent (decompose → fan-out → iterate → sentiment), fail-closed to NEUTRAL; wire behind
   `?deep=true`, keep single-shot default.
3. Decision reasoning loop + reflection, fail-closed to HOLD; keep `calculate_risk` for sizing.
4. Optional SSE sub-events for the loop trace; UI "Deep research" toggle.
5. Verify the gate/executor path is byte-for-byte unchanged (proposal still gated).

## Risks & implications (call-outs for the decision-maker)

- **Cost/latency**: loops multiply Groq/Tavily calls. Mitigated by opt-in trigger + caps + budget,
  but real spend rises when "deep" is on.
- **More LLM surface near the trade**: a reasoning loop sits closer to the decision than today's
  `if/else`. Mitigated by: no order tools in the loop, fail-closed to HOLD, deterministic sizing, and
  the unchanged gate as the true authority. This is the deliberate tension the user accepted.
- **Determinism/repeatability**: LLM loops are non-deterministic; the audit trail (`/audit`) and the
  gate verdict remain the deterministic record of *why* an order was sent or blocked.
- **Reference bugs to avoid**: build the turn input unconditionally (Gemini-Claw `core.py` NameError
  on resumed turns); add no-progress detection (absent in the reference).

## Open decisions for the follow-up
1. `RESEARCH_MAX_TURNS` / `DECISION_MAX_TURNS` values (default 3 each?).
2. Exactly which read-only tools the decision loop may call (features + technical + re-research, or a
   subset?).
3. Should the loop trace stream as SSE sub-nodes in the agent graph, or stay server-side for v1?
4. Cache research results per symbol for N seconds to blunt cost (ties to the earlier TTL-cache idea)?
