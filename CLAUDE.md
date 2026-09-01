# CLAUDE.md

Guide for code agents in this **FastAPI** backend (TradeLix PoC, Python).
Complements `AGENTS.md`; its rules are strict and **take precedence**. Read it in full.

## Repo context

Backend of the Alpaca hackathon trading-agent system. A linear multi-agent pipeline (not a
framework: no LangGraph) that goes from market data to a trade proposal **authorized by a
deterministic gate** and **paper-only**. The React dashboard `tradelix-poc-web` consumes it via
`/api`. Stack: FastAPI + Uvicorn, `alpaca-py` (paper), `langchain-groq` (sentiment),
`tavily-python` (news). Dependencies in `requirements.txt` (`pip`, not `uv`).

## Before touching code

1. Read `README.md`, `AGENTS.md`, and the doc for the area you change:
   `docs/tavily-news-integration.md` (news→sentiment), `docs/research/react-agentic-patterns.md`,
   `docs/plans/react-integration-plan.md` (approved ReAct plan), and the parent-monorepo
   architecture notes.
2. If you use FastAPI, `alpaca-py`, `langchain-groq`, `tavily-python`, Pydantic, or Uvicorn, check
   its official docs and apply the pattern already present in the repo.
3. If the instruction is ambiguous, state concrete assumptions and say the user can correct you.

## Architecture (map)

```txt
backend.py            FastAPI app: run_pipeline() (step runner), /pipeline(+/stream SSE),
                      /execute, /control*, /audit, and the _STEPS / _PIPELINE_KEYS contract.
agents/               One node per function: sentiment, options, feature, technical,
                      market_state, risk, decision, execution (pure executor),
                      execution_gate (the deterministic governor).
services/             alpaca_service (paper client, orders, positions, clock, open orders),
                      news_service (Tavily), config (flags + runtime arm/kill state), mcp_client,
                      db / cache / secrets / persist / logs (Postgres + Redis).
docs/                 Living documentation (obey it; update it when behavior changes).
.env / .env.example   Secrets (gitignored) / empty template.
```

Core pattern: **the LLM proposes → the deterministic gate authorizes → the executor executes → the
broker reconciles**. Nothing reaches the broker except via `POST /execute`, always through
`evaluate_gate()`.

## Pipeline flow

`news → sentiment → options; features; technical; account → market_state → risk → decision → gate`.
Execution (`execution`) runs only in `POST /execute`. SSE emits one event per node
(`running → done|error`); the `gate` shows up as a node, surfacing ALLOW/BLOCK/NO_TRADE live.

## Verification commands (before closing)

```bash
python -m py_compile backend.py agents/*.py services/*.py
uvicorn backend:app --host 127.0.0.1 --port 8000       # local dev (paper)
curl -N "http://127.0.0.1:8000/pipeline/stream?symbol=AAPL"
```

Health: `GET /` → `{"message": "TradeLix AI Running"}`. Control: `GET/POST /control*`.
Audit: `GET /audit`. **Never against a live account.** Do not leave uvicorn/vite running without
saying so.

## Configuration

- `.env` (see `.env.example`): Alpaca paper keys (required for fills), `GROQ_API_KEY` /
  `TAVILY_API_KEY` (optional → degrade to NEUTRAL/demo), `CORS_ORIGINS`, `DATABASE_URL`,
  `REDIS_URL` (required at uvicorn startup; `docker compose up -d`).
- Keys in Postgres override `.env` when set. `GET /settings` never returns key values.
- Execution edge: `EXECUTE_ENABLED` (arm-to-execute, default false), `KILL_SWITCH`,
  `MAX_SYMBOL_EXPOSURE_PCT` (0.10), `MAX_TOTAL_EXPOSURE_PCT` (0.30), `TAVILY_NEWS_DAYS`,
  `TAVILY_MAX_RESULTS`. Read them through `services/config.py`.

## Frontend contract

The UI types are coupled to these responses: `PocMarketState`, `PocRisk`, `PocDecision`,
`PocAccount`, `PocOrderResult`, `PocGate`, `PocControl`, `PocPipelineNode`, `PocSettings`,
`PocInvocation`. Keep the shapes and the SSE event names (`node`, `react`, `done`) stable, or
update `tradelix-poc-web/src/api/market-client.ts` in the same step. CORS via `CORS_ORIGINS`
(Vite proxies `/api` → `:8000`).

## Git & operation

- **Never `main`.** Feature branches; the human pushes/merges (or asks). Commits in English with the
  `Co-Authored-By:` and `Claude-Session:` trailers. No push/force-push unless asked.
- Do not store secrets in `.env.example`, docs, or tests. `audit.log` is a local artifact
  (gitignored).
