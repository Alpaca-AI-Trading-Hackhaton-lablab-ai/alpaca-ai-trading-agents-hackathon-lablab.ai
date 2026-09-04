import asyncio
import os
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agents.base import ReactAgent
from agents.decision_agent import oms_snapshot
from agents.execution_agent import dispatch
from agents.feature_agent import get_market_features
from agents.indicator_engine import compute_pack, parse_indicators
from agents.nodes import PIPELINE_KEYS, build_pipeline
from agents.technical_agent import technical_analysis
from services import bracket, cache, conditional, config, db, fill_listener, logs, persist, scheduler, usage_meter
from services.schemas import (
    AccountOut,
    AuditOut,
    BarsOut,
    ControlOut,
    GoneOut,
    HealthOut,
    LogsOut,
    ModelsOut,
    NewsItem,
    QuoteOut,
    SettingsOut,
    SettingsUpdate,
    SnapshotOut,
    dump_stream_event,
)
from services.alpaca_service import (
    get_account_info,
    get_market_clock,
    get_open_orders,
    get_order_status,
    get_positions,
    get_spy_price,
    reset_clients,
)
from services.bracket_plan import seed_plan
from services.mcp_client import get_tools
from services.news_service import get_market_news

load_dotenv()


@asynccontextmanager
async def lifespan(_app):
    load_dotenv()
    db.connect()
    cache.connect()
    fill_listener.start()
    task = asyncio.create_task(scheduler.loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    fill_listener.stop()
    cache.close()
    db.close()


app = FastAPI(lifespan=lifespan)

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


# Audit trail lives in Postgres (invocation_logs). `_audit` writes kind=execute.


def _audit(entry):
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    logs.record(
        symbol=entry.get("symbol"),
        agent_id="gate" if entry.get("verdict") else "control",
        kind="execute",
        status=entry.get("status"),
        summary=str(entry.get("action") or entry.get("status") or ""),
        payload=entry,
    )
    return entry


def _safe_message(agent, out):
    try:
        return agent.message(out)
    except Exception:  # noqa: BLE001 - a message must never crash the pipeline
        return ""


def _opt_model(value):
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _resolve_models(sentiment_model=None, decision_model=None):
    try:
        return config.resolve_models(
            _opt_model(sentiment_model),
            _opt_model(decision_model),
        )
    except config.UnknownModelError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _merged_opts(
    sentiment_model=None,
    decision_model=None,
    indicators=None,
    decision_indicators=None,
    deep_sentiment=None,
    deep_decision=None,
    deep=None,
):
    return persist.merge_pipeline_opts(
        sentiment_model,
        decision_model,
        indicators,
        decision_indicators,
        deep_sentiment,
        deep_decision,
        deep=deep,
    )


def run_pipeline(
    symbol="SPY",
    deep=False,
    models=None,
    indicators=None,
    decision_indicators=None,
    deep_sentiment=None,
    deep_decision=None,
):
    """Run each Agent in order, emitting a per-node event as it goes:
    running -> done|error. Reused by _analysis, /pipeline and /pipeline/stream.
    `deep` swaps in the ReAct variants for sentiment and decision (proposal
    side only; the gate stays deterministic). ReAct agents also yield `react`
    turn events (thought/tool/observation) between running and done.
    """
    models = models or config.resolve_models()
    indicators = parse_indicators(
        ",".join(indicators) if isinstance(indicators, (list, tuple)) else indicators
    )
    decision_indicators = parse_indicators(
        ",".join(decision_indicators)
        if isinstance(decision_indicators, (list, tuple))
        else decision_indicators
    )
    ctx = {
        "symbol": _symbol(symbol),
        "models": models,
        "indicators": indicators,
        "decision_indicators": decision_indicators,
        "run_id": str(uuid.uuid4()),
        "max_credit": scheduler.max_credit(),
        "open_orders": get_open_orders(),
        "positions": get_positions().get("positions", []),
    }
    oms = oms_snapshot(ctx["open_orders"], ctx["positions"])
    ctx["working_book"] = oms["working_book"]
    ctx["positions"] = oms["positions"]
    for agent in build_pipeline(deep, deep_sentiment, deep_decision):
        name = agent.node
        yield {
            "kind": "node",
            "node": name,
            "status": "running",
            "model": models.get(name),
            "ts": time.time(),
        }
        t0 = time.perf_counter()
        try:
            out = None
            if isinstance(agent, ReactAgent):
                for ev in agent.iter_run(ctx):
                    if ev.get("kind") == "result":
                        out = ev["output"]
                        continue
                    yield {
                        "kind": "react",
                        "node": name,
                        "status": "running",
                        "turn": ev.get("turn"),
                        "thought": ev.get("thought"),
                        "tool": ev.get("tool"),
                        "observation": ev.get("observation"),
                        "ts": time.time(),
                    }
            else:
                out = agent.run(ctx)
            ctx[name] = out
            elapsed = int((time.perf_counter() - t0) * 1000)
            msg = _safe_message(agent, out)
            logs.record(
                run_id=ctx["run_id"],
                symbol=ctx["symbol"],
                agent_id=name,
                kind="gate" if name == "gate" else "run",
                model=(out or {}).get("model") if isinstance(out, dict) else models.get(name),
                latency_ms=elapsed,
                status="done",
                summary=msg,
                payload=out if isinstance(out, dict) else {"value": out},
            )
            yield {
                "kind": "node",
                "node": name,
                "status": "done",
                "output": out,
                "message": msg,
                "model": (out or {}).get("model") if isinstance(out, dict) else models.get(name),
                "ts": time.time(),
            }
        except Exception as e:  # noqa: BLE001 - a failed node must not abort the rest
            err = {"error": str(e)}
            ctx[name] = err
            logs.record(
                run_id=ctx["run_id"],
                symbol=ctx["symbol"],
                agent_id=name,
                kind="run",
                latency_ms=int((time.perf_counter() - t0) * 1000),
                status="error",
                summary=str(e),
            )
            yield {
                "kind": "node",
                "node": name,
                "status": "error",
                "output": err,
                "message": str(e),
                "ts": time.time(),
            }
    logs.flush_run(ctx["run_id"])
    yield {"kind": "done", "node": "__done__", "status": "done", "ctx": ctx, "ts": time.time()}


def _analysis(
    symbol="SPY",
    deep=False,
    models=None,
    indicators=None,
    decision_indicators=None,
    deep_sentiment=None,
    deep_decision=None,
):
    ctx = {}
    for ev in run_pipeline(
        symbol,
        deep,
        models,
        indicators,
        decision_indicators,
        deep_sentiment,
        deep_decision,
    ):
        if ev.get("node") == "__done__":
            ctx = ev["ctx"]
    return {key: ctx.get(key) for key in PIPELINE_KEYS}


@app.get("/")
def home() -> HealthOut:
    return HealthOut(message="TradeLix AI Running")


@app.get("/account")
def account() -> AccountOut:
    return get_account_info()


@app.get("/spy")
def spy(symbol: str = "SPY") -> QuoteOut:
    quote = get_spy_price(symbol)
    price = quote.get("price") if isinstance(quote, dict) else None
    if price is not None:
        try:
            conditional.evaluate_triggers(_symbol(symbol), float(price))
        except Exception:
            pass
    return quote


@app.get("/news")
def news(symbol: str = "SPY") -> list[NewsItem]:
    return get_market_news(symbol)


def _gone() -> GoneOut:
    return GoneOut()


@app.get("/sentiment", status_code=410)
def sentiment() -> GoneOut:
    return _gone()


@app.get("/features")
def features(symbol: str = "SPY", indicators: str | None = None) -> SnapshotOut:
    return get_market_features(symbol, indicators)


@app.get("/technical")
def technical(symbol: str = "SPY", indicators: str | None = None) -> SnapshotOut:
    return technical_analysis(symbol, indicators)


@app.get("/bars")
def bars(symbol: str = "SPY", indicators: str | None = None) -> BarsOut:
    """OHLC + overlay series for the candlestick chart. Does not run agents."""
    return compute_pack(symbol, indicators)


@app.get("/options", status_code=410)
def options() -> GoneOut:
    return _gone()


@app.get("/risk", status_code=410)
def risk() -> GoneOut:
    return _gone()


@app.get("/market-state", status_code=410)
def market_state() -> GoneOut:
    return _gone()


@app.get("/decision", status_code=410)
def decision() -> GoneOut:
    return _gone()


@app.get("/models")
def models() -> ModelsOut:
    """Free-tier Groq chat allowlist + per-agent defaults for the dashboard."""
    return config.models_catalog()


@app.get("/settings")
def get_settings() -> SettingsOut:
    if not db.is_connected():
        raise HTTPException(status_code=503, detail="database is not connected")
    return persist.public_view()


@app.put("/settings")
def put_settings(body: SettingsUpdate) -> SettingsOut:
    if not db.is_connected():
        raise HTTPException(status_code=503, detail="database is not connected")
    try:
        view, changed_keys = persist.update(body.model_dump())
    except config.UnknownModelError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if changed_keys:
        cache.flush()
        reset_clients()
    return view


@app.get("/logs")
def list_logs(
    symbol: str | None = None, agent: str | None = None, limit: int = 50
) -> LogsOut:
    if not db.is_connected():
        raise HTTPException(status_code=503, detail="database is not connected")
    return {"entries": logs.query_logs(symbol=symbol, agent=agent, limit=limit)}


@app.get("/pipeline")
def pipeline(
    symbol: str = "SPY",
    deep: bool = config.deep_research_default(),
    sentiment_model: str | None = None,
    decision_model: str | None = None,
    indicators: str | None = None,
    decision_indicators: str | None = None,
    deep_sentiment: bool | None = None,
    deep_decision: bool | None = None,
):
    """Run the full pipeline and return the per-node trace + the analysis.
    Single (non-stream) fetch for the agent widget's initial state.
    `deep=true` enables the ReAct research/decision loops (opt-in, needs GROQ)."""
    merged = _merged_opts(
        sentiment_model,
        decision_model,
        indicators,
        decision_indicators,
        deep_sentiment,
        deep_decision,
        deep=deep,
    )
    resolved = _resolve_models(merged["sentiment_model"], merged["decision_model"])
    nodes = []
    ctx = {}
    for ev in run_pipeline(
        symbol,
        deep,
        resolved,
        merged["indicators"],
        merged["decision_indicators"],
        merged["deep_sentiment"],
        merged["deep_decision"],
    ):
        if ev.get("node") == "__done__":
            ctx = ev["ctx"]
            continue
        if ev.get("kind") == "react":
            continue
        if ev["status"] in ("done", "error"):
            nodes.append(
                {
                    "node": ev["node"],
                    "status": ev["status"],
                    "message": ev.get("message"),
                    "output": ev.get("output"),
                    "model": ev.get("model"),
                }
            )
    analysis = {key: ctx.get(key) for key in PIPELINE_KEYS}
    return {
        "symbol": _symbol(symbol),
        "nodes": nodes,
        "models": resolved,
        "deep": deep,
        **analysis,
    }


@app.get("/pipeline/stream")
def pipeline_stream(
    symbol: str = "SPY",
    deep: bool = config.deep_research_default(),
    sentiment_model: str | None = None,
    decision_model: str | None = None,
    indicators: str | None = None,
    decision_indicators: str | None = None,
    deep_sentiment: bool | None = None,
    deep_decision: bool | None = None,
):
    """SSE: emit one event per node (running -> done|error) as each agent
    finishes, so the graph lights up in real time. ReAct turns arrive as
    `event: react`."""
    merged = _merged_opts(
        sentiment_model,
        decision_model,
        indicators,
        decision_indicators,
        deep_sentiment,
        deep_decision,
        deep=deep,
    )
    resolved = _resolve_models(merged["sentiment_model"], merged["decision_model"])

    def gen():
        for ev in run_pipeline(
            symbol,
            deep,
            resolved,
            merged["indicators"],
            merged["decision_indicators"],
            merged["deep_sentiment"],
            merged["deep_decision"],
        ):
            if ev.get("node") == "__done__":
                yield "event: done\ndata: {}\n\n"
                continue
            payload = {k: v for k, v in ev.items() if k != "ctx"}
            name = "react" if ev.get("kind") == "react" else "node"
            yield f"event: {name}\ndata: {dump_stream_event(payload)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _extract_plan(body):
    if not isinstance(body, dict) or not body:
        return None
    if isinstance(body.get("plan"), dict):
        return body["plan"]
    if body.get("side") and (body.get("size") or body.get("tps") or body.get("sl")):
        return body
    return None


def _finish_execute(result, symbol=None, decision=None):
    result = dict(result or {})
    parked = []
    if result.get("status") not in ("BLOCKED", "NO_TRADE", "DRY_RUN"):
        parked = conditional.park_rows(result.get("emulated") or [])
    if parked:
        result["parked"] = parked
    gate = result.get("gate") or {}
    dec = result.get("decision") or decision or {}
    _audit(
        {
            "symbol": _symbol(symbol or dec.get("symbol")),
            "action": dec.get("action"),
            "verdict": gate.get("verdict"),
            "status": result.get("status"),
            "notional": gate.get("notional"),
            "order_id": result.get("order_id"),
            "reasons": gate.get("reasons"),
        }
    )
    return result


def _execute_plan(plan):
    result = bracket.execute_plan(
        plan,
        get_account_info(),
        get_positions().get("positions", []),
        get_open_orders(_symbol((plan or {}).get("symbol"))),
        get_market_clock(),
        park_emulated=True,
    )
    return _finish_execute(result, symbol=(plan or {}).get("symbol"))


@app.post("/execute")
def execute(
    symbol: str = "SPY",
    deep: bool = config.deep_research_default(),
    sentiment_model: str | None = None,
    decision_model: str | None = None,
    indicators: str | None = None,
    decision_indicators: str | None = None,
    deep_sentiment: bool | None = None,
    deep_decision: bool | None = None,
    body: dict | None = Body(None),
):
    """One execute surface: optional `plan` in the body, else pipeline + seed_plan.
    POST /bracket/execute is an alias. Gate + dispatch only."""
    plan = _extract_plan(body)
    if plan:
        return _execute_plan(plan)

    merged = _merged_opts(
        sentiment_model,
        decision_model,
        indicators,
        decision_indicators,
        deep_sentiment,
        deep_decision,
        deep=deep,
    )
    resolved = _resolve_models(merged["sentiment_model"], merged["decision_model"])
    analysis = _analysis(
        symbol,
        deep,
        resolved,
        merged["indicators"],
        merged["decision_indicators"],
        merged["deep_sentiment"],
        merged["deep_decision"],
    )
    decision = analysis["decision"]
    ms = analysis.get("market_state") or {}
    seeded = seed_plan(decision, ms.get("price"), ms.get("atr"))
    qty = ((seeded or {}).get("size") or {}).get("qty") or 0
    plan = seeded if seeded and int(qty) >= 1 else None
    result = dispatch(
        decision=decision,
        account=analysis["account"],
        positions=get_positions().get("positions", []),
        open_orders=get_open_orders(),
        clock=get_market_clock(),
        plan=plan,
        max_credit=scheduler.max_credit(),
    )
    return _finish_execute(result, symbol=symbol, decision=decision)


@app.get("/control")
def control() -> ControlOut:
    return config.control_state()


@app.post("/control/arm")
def control_arm(enabled: bool = True) -> ControlOut:
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
def control_kill(enabled: bool = True) -> ControlOut:
    config.set_kill(enabled)
    cancelled = conditional.cancel_armed() if enabled else 0
    _audit(
        {
            "symbol": None,
            "action": "KILL",
            "verdict": None,
            "status": "KILL" if enabled else "CLEARED",
            "notional": None,
            "order_id": None,
            "reasons": [f"cancelled {cancelled} conditionals"] if cancelled else None,
        }
    )
    return config.control_state()


@app.get("/audit")
def audit(limit: int = 20) -> AuditOut:
    return {"entries": logs.audit_entries(limit)}


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


@app.post("/bracket/preview")
def bracket_preview(body: dict = Body(...)):
    plan = body.get("plan") or body
    trigger = body.get("trigger")
    return bracket.preview_plan(plan, trigger)


@app.post("/bracket/execute")
def bracket_execute(body: dict = Body(...)):
    """Alias of POST /execute with a plan body."""
    return _execute_plan(_extract_plan(body) or body)


@app.get("/conditional-orders")
def conditional_orders_list(symbol: str | None = None):
    return {"orders": conditional.list_orders(symbol)}


@app.post("/conditional-orders")
def conditional_orders_create(body: dict = Body(...)):
    plan = body.get("plan")
    trigger = body.get("trigger")
    if not plan or not trigger:
        raise HTTPException(status_code=400, detail="plan and trigger required")
    created = conditional.create_order(plan, trigger)
    if not created.get("ok"):
        return {
            "status": created.get("status") or "BLOCKED",
            "errors": created.get("errors"),
            "gate": created.get("gate"),
        }
    return created["order"]


@app.delete("/conditional-orders/{oid}")
def conditional_orders_delete(oid: str):
    ok = conditional.cancel_order(oid)
    if not ok:
        raise HTTPException(status_code=404, detail="not found or not cancellable")
    return {"ok": True, "id": oid}


@app.post("/webhook/{token}")
def webhook_fire(token: str):
    return conditional.fire_webhook(token)


@app.get("/schedule")
def schedule_get():
    return scheduler.get_public()


@app.put("/schedule")
def schedule_put(body: dict = Body(...)):
    try:
        return scheduler.save(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/schedule/start")
def schedule_start(body: dict | None = Body(default=None)):
    try:
        if body:
            patch = {
                k: body[k]
                for k in (
                    "interval_seconds",
                    "max_credit",
                    "universe",
                    "window_start",
                    "window_end",
                    "end_action",
                )
                if k in body
            }
            if patch:
                scheduler.save(patch)
            if body.get("focus"):
                scheduler.set_focus(body["focus"])
        return scheduler.start()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/schedule/stop")
def schedule_stop():
    try:
        return scheduler.stop()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/usage")
def usage_get():
    return {"entries": usage_meter.snapshot()}


@app.get("/usage/budgets")
def usage_budgets_get():
    return {"budgets": usage_meter.load_budgets()}


@app.put("/usage/budgets")
def usage_budgets_put(body: dict | list = Body(...)):
    items = body if isinstance(body, list) else body.get("budgets") or body.get("items") or []
    try:
        return {"budgets": usage_meter.save_budgets(items)}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/buy")
async def buy():
    return {
        "status": "DISABLED",
        "reason": "Use /execute only after server-side dry-run and confirmation are implemented.",
    }
