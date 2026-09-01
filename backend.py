import json
import os
import time
import traceback

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agents.decision_agent import make_decision
from agents.execution_agent import execute_trade
from agents.feature_agent import get_market_features
from agents.market_state_agent import build_market_state
from agents.options_agent import options_strategy
from agents.risk_manager import calculate_risk
from agents.sentiment_agent import analyze_sentiment
from agents.technical_agent import technical_analysis
from services.alpaca_service import (
    get_account_info,
    get_order_status,
    get_positions,
    get_spy_price,
)
from services.mcp_client import get_tools
from services.news_service import get_market_news

app = FastAPI()

# The frontend (Vite/bun) runs on a different origin in dev; allow CORS.
# Configurable via CORS_ORIGINS (comma-separated); defaults to "*" for the PoC.
_origins = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "*").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _symbol(symbol):
    return (symbol or "SPY").upper()


# Trace keys, in the pipeline's topological order. Each node = one agent.
_PIPELINE_KEYS = (
    "news",
    "sentiment",
    "options",
    "features",
    "technical",
    "market_state",
    "account",
    "risk",
    "decision",
)


def _msg(node, out):
    """Human-readable message (the node's 'communication') built from its output."""
    try:
        if not isinstance(out, dict) and node != "news":
            return str(out)
        if node == "news":
            n = len(out) if isinstance(out, list) else "n/a"
            return f"{n} articles"
        if node == "sentiment":
            summary = (out.get("summary") or "").strip()
            head = f"{out.get('sentiment')} · {out.get('confidence')}%"
            return f"{head} — {summary[:80]}" if summary else head
        if node == "options":
            return f"{out.get('strategy')} / {out.get('action')}"
        if node == "features":
            return f"{out.get('trend')} @ {out.get('price')}"
        if node == "technical":
            return f"{out.get('signal')} (RSI {out.get('rsi')})"
        if node == "market_state":
            return (
                f"{out.get('sentiment')} · {out.get('technical_signal')} · "
                f"{out.get('trend')}"
            )
        if node == "account":
            return f"{out.get('mode')} · {out.get('status')}"
        if node == "risk":
            return f"{out.get('risk_level')} · ${out.get('position_size')}"
        if node == "decision":
            return (
                f"{out.get('action')} "
                f"({out.get('sentiment')}×{out.get('technical_signal')})"
            )
    except Exception:  # noqa: BLE001 - a message must never crash the pipeline
        pass
    return ""


# Each step: (name, fn(ctx) -> output). ctx accumulates prior outputs, so the DAG
# edges are exactly the ctx keys each fn reads.
_STEPS = [
    ("news", lambda c: get_market_news(c["symbol"])),
    ("sentiment", lambda c: analyze_sentiment(c["news"])),
    (
        "options",
        lambda c: options_strategy(
            c["sentiment"].get("sentiment", "NEUTRAL"),
            c["sentiment"].get("confidence", 0),
            c["symbol"],
        ),
    ),
    ("features", lambda c: get_market_features(c["symbol"])),
    ("technical", lambda c: technical_analysis(c["symbol"])),
    (
        "market_state",
        lambda c: build_market_state(
            c["sentiment"], c["options"], c["features"], c["technical"]
        ),
    ),
    ("account", lambda c: get_account_info()),
    (
        "risk",
        lambda c: calculate_risk(
            c["account"].get("equity", 100000),
            c["sentiment"].get("confidence", 0),
        ),
    ),
    ("decision", lambda c: make_decision(c["market_state"], c["risk"])),
]


def run_pipeline(symbol="SPY"):
    """Run each agent in order, emitting a per-node event as it goes:
    running -> done|error. Reused by _analysis, /pipeline and /pipeline/stream.
    """
    ctx = {"symbol": _symbol(symbol)}
    for name, fn in _STEPS:
        yield {"node": name, "status": "running", "ts": time.time()}
        try:
            out = fn(ctx)
            ctx[name] = out
            yield {
                "node": name,
                "status": "done",
                "output": out,
                "message": _msg(name, out),
                "ts": time.time(),
            }
        except Exception as e:  # noqa: BLE001 - a failed node must not abort the rest
            err = {"error": str(e)}
            ctx[name] = err
            yield {
                "node": name,
                "status": "error",
                "output": err,
                "message": str(e),
                "ts": time.time(),
            }
    yield {"node": "__done__", "status": "done", "ctx": ctx, "ts": time.time()}


def _analysis(symbol="SPY"):
    ctx = {}
    for ev in run_pipeline(symbol):
        if ev["node"] == "__done__":
            ctx = ev["ctx"]
    return {key: ctx.get(key) for key in _PIPELINE_KEYS}


@app.get("/")
def home():
    return {"message": "TradeLix AI Running"}


@app.get("/account")
def account():
    return get_account_info()


@app.get("/spy")
def spy(symbol: str = "SPY"):
    return get_spy_price(symbol)


@app.get("/news")
def news(symbol: str = "SPY"):
    return get_market_news(symbol)


@app.get("/sentiment")
def sentiment(symbol: str = "SPY"):
    return _analysis(symbol)["sentiment"]


@app.get("/features")
def features(symbol: str = "SPY"):
    return get_market_features(symbol)


@app.get("/technical")
def technical(symbol: str = "SPY"):
    return technical_analysis(symbol)


@app.get("/options")
def options(symbol: str = "SPY"):
    result = _analysis(symbol)
    return result["options"]


@app.get("/risk")
def risk(symbol: str = "SPY"):
    return _analysis(symbol)["risk"]


@app.get("/market-state")
def market_state(symbol: str = "SPY"):
    return _analysis(symbol)["market_state"]


@app.get("/decision")
def decision(symbol: str = "SPY"):
    return _analysis(symbol)["decision"]


@app.get("/pipeline")
def pipeline(symbol: str = "SPY"):
    """Run the full pipeline and return the per-node trace + the analysis.
    Single (non-stream) fetch for the agent widget's initial state."""
    nodes = []
    ctx = {}
    for ev in run_pipeline(symbol):
        if ev["node"] == "__done__":
            ctx = ev["ctx"]
            continue
        if ev["status"] in ("done", "error"):
            nodes.append(
                {
                    "node": ev["node"],
                    "status": ev["status"],
                    "message": ev.get("message"),
                    "output": ev.get("output"),
                }
            )
    analysis = {key: ctx.get(key) for key in _PIPELINE_KEYS}
    return {"symbol": _symbol(symbol), "nodes": nodes, **analysis}


@app.get("/pipeline/stream")
def pipeline_stream(symbol: str = "SPY"):
    """SSE: emit one event per node (running -> done|error) as each agent
    finishes, so the graph lights up in real time."""

    def gen():
        for ev in run_pipeline(symbol):
            if ev["node"] == "__done__":
                yield "event: done\ndata: {}\n\n"
                continue
            payload = {k: v for k, v in ev.items() if k != "ctx"}
            yield f"event: node\ndata: {json.dumps(payload, default=str)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/execute")
def execute(symbol: str = "SPY"):
    result = _analysis(symbol)
    return execute_trade(result["decision"])


@app.get("/positions")
def positions():
    return get_positions()


@app.get("/order/{order_id}")
def order(order_id: str):
    return get_order_status(order_id)


@app.get("/mcp-tools")
async def mcp_tools():
    try:
        result = await get_tools()

        return {
            "success": True,
            "tools": str(result),
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


@app.get("/buy")
async def buy():
    return {
        "status": "DISABLED",
        "reason": "Use /execute only after server-side dry-run and confirmation are implemented.",
    }
