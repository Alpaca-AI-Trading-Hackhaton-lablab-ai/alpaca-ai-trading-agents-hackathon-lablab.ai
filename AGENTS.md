# AGENTS

Strict rules for agents modifying this **FastAPI** backend (TradeLix, Alpaca AI Trading Agents
hackathon PoC). A linear multi-agent pipeline that turns market data into a **gated, paper-only**
trade proposal. The React dashboard ([`tradelix-poc-web`](../tradelix-poc-web)) consumes it through
its `/api` proxy. Style modeled on the `ap-base-fastapi-uv-service` base, focused on this repo.

## Before deploying or making breaking changes

- Read **[`pendiente-alpacorp.md`](pendiente-alpacorp.md)** — P1 is done (tick brackets with
  integer qty, position-aware risk, fill listener). Remaining work is P2. Research it
  with a lighter model + web search,
  then implement with a capable model. **Edit and `git push` to `main`** these files when
  the backlog or deploy contract changes: `pendiente-alpacorp.md`, this file, `CLAUDE.md`,
  and `../tradelix-poc-web/AGENTS.md`. Local-only edits do not count.
- **Deploy target:** one Amazon EC2 **`t3.small`** running **both** the backend Compose
  stack and the frontend Compose stack (shared `tradelix` network). Do not split them
  across two instances. Push both `main` branches before pulling on that box. The type
  is `t3.small`, not the originally planned `t3.medium`: the AWS account is on the Free
  Plan and `RunInstances` rejects non-free-tier types. Live instance details (EIP,
  security group, key pair) are in `pendiente-alpacorp.md`.

## Before changing code

- Read `README.md`, this file, `CLAUDE.md`, and **the docs for the area you touch**:
  `docs/tavily-news-integration.md`, `docs/ddg-concept-lookup.md`,
  `docs/strategy-smart-money.md`,
  `docs/research/react-agentic-patterns.md`,
  `docs/plans/react-integration-plan.md`, and the parent-monorepo architecture notes
  (`../consideraciones-encontradas.md`, `../analisis-de-integrabilidad.md`).
- If the change uses FastAPI, `alpaca-py`, `langchain-groq`, `tavily-python`, Pydantic or Uvicorn,
  check its official docs and apply the pattern already used in this repo before implementing.
- If the instruction is ambiguous, state concrete assumptions. In plan mode note the user can
  correct them; in a one-off prompt, note they can stop or correct you.

## Hard invariants (do not violate)

1. **Paper only. Never live.** `ALPACA_PAPER_TRADE=true`; `services/alpaca_service.py:_assert_paper()`
   must keep guarding client creation. Never remove, weaken, or bypass it.
2. **Probabilistic intelligence above, deterministic authority below.** Agents/LLMs only **propose**;
   `agents/execution_gate.py:evaluate_gate()` authorizes. No agent, tool, or LLM loop may submit an
   order or bypass the gate. Nothing reaches the broker except via
   `agents/execution_agent.py:dispatch()` after `evaluate_gate()`. Surfaces: `POST /execute`,
   armed scheduler tick, `POST /bracket/execute`, and conditionals (via `execute_plan` → `dispatch`).
   The graph SSE (`/pipeline`) is preview only and does not dispatch.
3. **One working order per symbol — no pile-on.** The gate hard-blocks a symbol that already has a
   resting order. Keep that check.
4. **Arm-to-execute.** `EXECUTE_ENABLED=false` by default → an ALLOW returns `DRY_RUN` (nothing
   sent). Never change the default or auto-arm; arming is an explicit action (`POST /control/arm`).
5. **Fail-closed.** Degraded/invalid inputs resolve to `NEUTRAL` / `HOLD` / `NO_TRADE`, never toward
   trading. A missing `GROQ_API_KEY`/`TAVILY_API_KEY` degrades gracefully, never crashes.
6. **No LangGraph / heavy infra** (documented decision). Keep the pure-Python pipeline (`langgraph`
   is in `requirements.txt` but intentionally unused). Read `docs/plans/react-integration-plan.md`
   before adding any agent autonomy.
7. **English only** in all code, comments, logs, node messages — and these `.md` files.
8. **Secrets never leave.** `.env` is gitignored and holds real paper keys — never read, print, or
   commit it. Only `.env.example` (empty placeholders) is tracked. `audit.log` is gitignored.

## Structure & design

- Do not break the separation: `backend.py` (FastAPI app + `run_pipeline` + endpoints), `agents/`
  (domain logic then `Agent` subclass at the **end of each file**; `nodes.py` is the registry
  (`PIPELINE_KEYS` + `build_pipeline`); `execution_agent` = pure executor, `execution_gate` = governor),
  `services/` (`alpaca_service`, `news_service`, `config`, `mcp_client`, `db`, `cache`, `secrets`,
  `persist`, `logs`), `docs/`.
- Pipeline topological order:
  `news → sentiment → options; features; technical; orderblock; institutional; account → market_state → risk → decision → gate`
  (execution is not a pipeline node; broker submit is only via `dispatch()`).
- Each node is an `Agent` whose `run(ctx)` writes into the accumulated `ctx` under `agent.node`.
  Keep `PIPELINE_KEYS` in `agents/nodes.py` in sync when adding/renaming a node. Do not put Agent
  classes in `nodes.py` — they belong at the bottom of the domain module.
- SSE (`/pipeline/stream`) uses a **synchronous** generator (threadpool). Events: `node`, `react` (ReAct thought/tool/observation), `done`. Do not make the pipeline
  `async` or block the event loop; do not re-split the Alpaca client/account (all reads and writes
  share one paper account — that bug is fixed, keep it fixed).
- New env flags go in **both** `services/config.py` and `.env.example` (commented). Read them through
  `config`, not scattered `os.getenv`. API keys resolve DB-then-env via `services/secrets.py`.
  `DATABASE_URL` and `REDIS_URL` are required at uvicorn startup. Money is **notional dollars**
  (`position_size`), sent as `notional=`; never confuse it with share `qty`.

## Verification (required before closing)

```bash
python -m py_compile backend.py agents/*.py services/*.py
curl -N "http://127.0.0.1:8000/pipeline/stream?symbol=AAPL"   # watch nodes stream
```

- Exercise the gate logic when you touch it (working-order, buying-power, exposure → BLOCK;
  HOLD/degraded → NO_TRADE; clean BUY → ALLOW).
- **Never run against a live account.** Recommend a permanent test when a change covers a contract,
  bug, or expected behavior.

## Documentation & operation

- When a change affects behavior, update the associated docs (that folder's and the frontend
  contract) in the same commit. Changing a response shape breaks the UI types
  (`tradelix-poc-web/src/api/market-client.ts`): `PocMarketState`, `PocDecision`, `PocOrderResult`,
  `PocGate`, `PocControl`, `PocPipelineNode`, `PocReactTurn`, `PocModelsCatalog`, and the SSE event names (`node`, `react`, `done`). Do not change
  them without updating the UI in the same step.
- Do not store secrets in `.env.example`, docs, or tests.
- **Do not leave servers or processes (uvicorn, vite) running without saying so.**
- **Never commit or push to `main`.** Work on feature branches; the human pushes and merges (or asks).
  Commit messages in English with the `Co-Authored-By:` and `Claude-Session:` trailers. No push /
  force-push unless asked.
