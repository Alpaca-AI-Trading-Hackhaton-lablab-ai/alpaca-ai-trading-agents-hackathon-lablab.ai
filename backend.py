import json
import os
import time
import traceback
from collections import deque
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agents.execution_agent import execute_trade
from agents.execution_gate import evaluate_gate
from agents.feature_agent import get_market_features
from agents.nodes import PIPELINE_KEYS, build_pipeline
from agents.technical_agent import technical_analysis
from services import config
from services.alpaca_service import (
    get_account_info,
    get_market_clock,
    get_open_orders,
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


# Audit trail: why an order was sent or blocked. In-memory ring + JSONL file.
_AUDIT = deque(maxlen=50)
_AUDIT_FILE = os.path.join(os.path.dirname(__file__), "audit.log")


def _audit(entry):
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    _AUDIT.appendleft(entry)
    try:
        with open(_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:  # noqa: BLE001 - auditing must never break execution
        pass
    return entry


def _safe_message(agent, out):
    try:
        return agent.message(out)
    except Exception:  # noqa: BLE001 - a message must never crash the pipeline
        return ""


def run_pipeline(symbol="SPY", deep=False):
    """Run each Agent in order, emitting a per-node event as it goes:
    running -> done|error. Reused by _analysis, /pipeline and /pipeline/stream.
    `deep` swaps in the ReAct variants for sentiment and decision (proposal
    side only; the gate stays deterministic).
    """
    ctx = {"symbol": _symbol(symbol)}
    for agent in build_pipeline(deep):
        name = agent.node
        yield {"node": name, "status": "running", "ts": time.time()}
        try:
            out = agent.run(ctx)
            ctx[name] = out
            yield {
                "node": name,
                "status": "done",
                "output": out,
                "message": _safe_message(agent, out),
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


def _analysis(symbol="SPY", deep=False):
    ctx = {}
    for ev in run_pipeline(symbol, deep):
        if ev["node"] == "__done__":
            ctx = ev["ctx"]
    return {key: ctx.get(key) for key in PIPELINE_KEYS}


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
def pipeline(symbol: str = "SPY", deep: bool = config.deep_research_default()):
    """Run the full pipeline and return the per-node trace + the analysis.
    Single (non-stream) fetch for the agent widget's initial state.
    `deep=true` enables the ReAct research/decision loops (opt-in, needs GROQ)."""
    nodes = []
    ctx = {}
    for ev in run_pipeline(symbol, deep):
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
    analysis = {key: ctx.get(key) for key in PIPELINE_KEYS}
    return {"symbol": _symbol(symbol), "nodes": nodes, **analysis}


@app.get("/pipeline/stream")
def pipeline_stream(symbol: str = "SPY", deep: bool = config.deep_research_default()):
    """SSE: emit one event per node (running -> done|error) as each agent
    finishes, so the graph lights up in real time."""

    def gen():
        for ev in run_pipeline(symbol, deep):
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
def execute(symbol: str = "SPY", deep: bool = config.deep_research_default()):
    """LLM proposes -> deterministic gate authorizes -> executor executes.
    Nothing reaches the broker unless the gate ALLOWs and the system is armed.
    `deep` only affects how the proposal is formed; the gate is unchanged."""
    analysis = _analysis(symbol, deep)
    decision = analysis["decision"]
    gate = evaluate_gate(
        decision,
        analysis["account"],
        get_positions().get("positions", []),
        get_open_orders(_symbol(symbol)),
        get_market_clock(),
    )

    def _record(status, extra=None):
        _audit(
            {
                "symbol": _symbol(symbol),
                "action": (decision or {}).get("action"),
                "verdict": gate["verdict"],
                "status": status,
                "notional": gate.get("notional"),
                "order_id": (extra or {}).get("order_id"),
                "reasons": gate.get("reasons"),
            }
        )

    if gate["verdict"] == "NO_TRADE":
        _record("NO_TRADE")
        return {
            "status": "NO_TRADE",
            "reason": (gate["reasons"] or ["HOLD"])[0],
            "gate": gate,
            "decision": decision,
        }

    if gate["verdict"] == "BLOCK":
        _record("BLOCKED")
        return {
            "status": "BLOCKED",
            "reason": (gate["reasons"] or ["blocked"])[0],
            "gate": gate,
            "decision": decision,
        }

    # verdict == ALLOW
    if not config.is_armed():
        _record("DRY_RUN")
        side = "sell" if decision["action"] == "SELL" else "buy"
        return {
            "status": "DRY_RUN",
            "reason": "System not armed (EXECUTE_ENABLED=false)",
            "would_call": {
                "tool": "place_stock_order",
                "symbol": decision["symbol"],
                "side": side,
                "notional_position_size": decision["position_size"],
            },
            "gate": gate,
            "decision": decision,
        }

    result = execute_trade(decision)
    result["gate"] = gate
    _record(result.get("status"), result)
    return result


@app.get("/control")
def control():
    return config.control_state()


@app.post("/control/arm")
def control_arm(enabled: bool = True):
    config.set_armed(enabled)
    _audit(
        {
            "symbol": None,
            "action": "ARM",
            "verdict": None,
            "status": "ARMED" if enabled else "SAFE",
            "notional": None,
            "order_id": None,
            "reasons": None,
        }
    )
    return config.control_state()


@app.post("/control/kill")
def control_kill(enabled: bool = True):
    config.set_kill(enabled)
    _audit(
        {
            "symbol": None,
            "action": "KILL",
            "verdict": None,
            "status": "KILL" if enabled else "CLEARED",
            "notional": None,
            "order_id": None,
            "reasons": None,
        }
    )
    return config.control_state()


@app.get("/audit")
def audit(limit: int = 20):
    return {"entries": list(_AUDIT)[:limit]}


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
