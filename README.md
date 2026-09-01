# TradeLix backend (paper-only)

FastAPI pipeline for the Alpaca AI Trading Agents hackathon PoC. It turns a symbol into a **gated, paper-only** trade proposal. The LLM **proposes**; a deterministic gate **authorizes**. Nothing reaches Alpaca unless the system is armed and the gate returns `ALLOW`.

Dashboard (separate repo): [alpacore-tradelix-web](https://github.com/Alpaca-AI-Trading-Hackhaton-lablab-ai/alpacore-tradelix-web) (local sibling: `../tradelix-poc-web`).

## What this repo has

| Piece | Role |
|---|---|
| `backend.py` | FastAPI app, `/pipeline` + SSE `/pipeline/stream`, `/execute`, `/control`, `/settings`, `/bars`, `/logs` |
| `agents/` | Linear pipeline: news → sentiment → features/technical → market state → risk → decision → gate |
| `services/` | Alpaca paper client, news, Postgres (`db` / `persist` / `logs` / `secrets`), Redis cache, Pydantic schemas |
| `docker-compose.yml` | Postgres 16 (**5433**), Redis 7 (**6380**), API **tradelix-backend** (**8000**) |
| `tests/` | `python -m unittest discover -s tests -v` |

**Not used (on purpose):** LangGraph, Streamlit, live trading. `ALPACA_PAPER_TRADE` must stay `true`.

JSON responses use FastAPI 0.131 + Pydantic v2 (Rust / jiter). `GET /sentiment`, `/options`, `/risk`, `/market-state`, `/decision` return **410** — run `/pipeline` instead.

## Prerequisites

- Docker + Compose (primary way to run)
- Optional: Python **3.11+** only if you run uvicorn on the host
- Optional keys in `.env`: Alpaca **paper**, Groq, Tavily. Missing Groq/Tavily fails closed to `NEUTRAL` / demo news — the API still starts.

## Configure

```bash
cd alpaca-ai-trading-agents-hackathon-lablab.ai
cp -n .env.example .env
# Edit .env: ALPACA_API_KEY, ALPACA_SECRET_KEY, GROQ_API_KEY, TAVILY_API_KEY
# Leave ALPACA_PAPER_TRADE=true
```

The API container **overrides** `DATABASE_URL` / `REDIS_URL` to `postgres:5432` and `redis:6379` (Docker DNS). Host ports stay **5433** / **6380** so a local uvicorn can still reach Compose.

Keys saved later in the UI (`PUT /settings`) override `.env`. `GET /settings` never returns secret values — only `db` / `env` / `missing`.

## Run with Docker (recommended)

Do **not** also run host uvicorn on port 8000.

```bash
docker compose up --build -d
docker compose ps    # postgres + redis healthy, tradelix-backend healthy
```

- API: http://127.0.0.1:8000
- Docs: http://127.0.0.1:8000/docs
- Health: `curl http://127.0.0.1:8000/`

This publishes the `tradelix` Docker network. The dashboard container joins it and proxies `/api` → `tradelix-backend:8000`.

Stop: `docker compose down` (add `-v` only if you want to wipe Postgres data).

## Run the dashboard too

```bash
cd ../tradelix-poc-web
docker compose up --build -d
# UI: http://127.0.0.1:3200   (nginx /api → tradelix-backend)
```

Start **this** repo first so the `tradelix` network exists.

## Host uvicorn (optional, no API container)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

docker compose up -d postgres redis
uvicorn backend:app --reload --host 127.0.0.1 --port 8000
```

If you use this path, stop `tradelix-backend` (`docker compose stop tradelix-backend`) so port 8000 is free. The Vite dev server (`bun run dev` in the web repo) proxies `/api` to this process.

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

## Useful endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/pipeline?symbol=SPY` | Full run (blocking) |
| GET | `/pipeline/stream?symbol=SPY` | SSE: `node` / `react` / `done` |
| GET | `/bars?symbol=SPY` | Candles + overlays (chart) |
| GET / PUT | `/settings` | Keys (redacted) + agent models |
| GET / POST | `/control`, `/control/arm`, `/control/kill` | Arm / kill stay off the LLM path |
| POST | `/execute?symbol=SPY` | Gate then paper order; default is `DRY_RUN` |

Do not point this at a live Alpaca account.
