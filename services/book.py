"""Universe scan + intent book. Deterministic per symbol; one sentiment/decision."""

from __future__ import annotations

import uuid

from agents.decision_agent import (
    DecisionAgent,
    DecisionReactAgent,
    compact_working_book,
    compact_positions,
    intents_from_book,
)
from agents.execution_agent import dispatch
from agents.execution_gate import evaluate_gate
from agents.feature_agent import FeatureAgent
from agents.institutional_flow import InstitutionalAgent
from agents.market_state_agent import MarketStateAgent
from agents.news_agent import NewsAgent
from agents.options_agent import OptionsAgent
from agents.orderblock_engine import OrderblockAgent
from agents.risk_manager import RiskAgent
from agents.sentiment_agent import SentimentAgent, SentimentReactAgent
from agents.technical_agent import TechnicalAgent
from services import config, logs, persist
from services.alpaca_service import (
    get_account_info,
    get_market_clock,
    get_open_orders,
    get_positions,
)
from services.bracket_plan import seed_plan


def _compact_ms(ms):
    ms = dict(ms or {})
    for key in ("candles", "overlays", "oscillators", "volume", "bars"):
        ms.pop(key, None)
    return ms


def snapshot_symbol(symbol, account, sentiment, indicators, max_credit, positions=None):
    ctx = {
        "symbol": symbol,
        "account": account,
        "sentiment": sentiment or {},
        "indicators": indicators,
        "max_credit": max_credit,
        "models": {},
        "positions": positions or [],
    }
    ctx["features"] = FeatureAgent().run(ctx)
    ctx["technical"] = TechnicalAgent().run(ctx)
    ctx["orderblock"] = OrderblockAgent().run(ctx)
    ctx["institutional"] = InstitutionalAgent().run(ctx)
    ctx["options"] = OptionsAgent().run(ctx)
    ctx["market_state"] = MarketStateAgent().run(ctx)
    ctx["risk"] = RiskAgent().run(ctx)
    return {
        "symbol": symbol,
        "market_state": _compact_ms(ctx["market_state"]),
        "risk": ctx["risk"],
        "ctx": ctx,
    }


def _decision_from_intent(intent):
    return {
        "symbol": intent.get("symbol"),
        "action": intent.get("action"),
        "position_size": intent.get("position_size") or intent.get("notional") or 0,
        "order_id": intent.get("order_id"),
    }


def _plan_for_intent(intent, market_by_symbol):
    action = str((intent or {}).get("action") or "").upper()
    if action not in ("BUY", "SELL"):
        return None
    symbol = str((intent or {}).get("symbol") or "").upper()
    ms = (market_by_symbol or {}).get(symbol) or {}
    last_price = intent.get("last_price") or ms.get("price")
    atr = intent.get("atr") if intent.get("atr") is not None else ms.get("atr")
    return seed_plan(_decision_from_intent(intent), last_price, atr)


def apply_intents(intents, account, positions, clock, max_credit, market_by_symbol=None):
    from services import conditional

    results = []
    open_orders = get_open_orders()
    cancels = [i for i in intents if (i or {}).get("action") == "CANCEL"]
    places = [i for i in intents if (i or {}).get("action") in ("BUY", "SELL")]
    market_by_symbol = market_by_symbol or {}

    def _one(intent):
        nonlocal open_orders
        plan = _plan_for_intent(intent, market_by_symbol)
        if str((intent or {}).get("action") or "").upper() in ("BUY", "SELL"):
            if plan is None:
                return {
                    "intent": intent,
                    "status": "NO_TRADE",
                    "reason": "no price to seed a bracket",
                    "decision": _decision_from_intent(intent),
                }
            qty = ((plan.get("size") or {}).get("qty")) or 0
            if int(qty) < 1:
                return {
                    "intent": intent,
                    "status": "NO_TRADE",
                    "reason": "bracket needs ≥1 share",
                    "decision": _decision_from_intent(intent),
                    "plan": plan,
                }
        out = dispatch(
            decision=_decision_from_intent(intent),
            account=account,
            positions=positions,
            open_orders=open_orders,
            clock=clock,
            plan=plan,
            max_credit=max_credit,
        )
        if out.get("status") not in ("NO_TRADE", "BLOCKED", "DRY_RUN"):
            parked = conditional.park_rows(out.get("emulated") or [])
            if parked:
                out["parked"] = parked
            open_orders = get_open_orders()
        return {"intent": intent, **out}

    for intent in cancels:
        results.append(_one(intent))
    for intent in places:
        results.append(_one(intent))
    return results


def run_tick(focus=None, max_credit=None, universe=None, indicators=None, models=None):
    """Scan universe, one sentiment+decision on the compact book, gate, maybe submit."""
    from agents.indicator_engine import parse_indicators

    universe = [
        str(s).upper()
        for s in (universe or list(config.DEFAULT_UNIVERSE))
        if s
    ]
    if not universe:
        universe = list(config.DEFAULT_UNIVERSE)
    focus = (focus or universe[0]).upper()
    if focus not in universe:
        universe = [focus] + universe
    max_credit = float(max_credit if max_credit is not None else config.DEFAULT_MAX_CREDIT)
    merged = persist.merge_pipeline_opts(
        indicators=indicators,
        sentiment_model=(models or {}).get("sentiment") if models else None,
        decision_model=(models or {}).get("decision") if models else None,
    )
    indicators = parse_indicators(
        ",".join(indicators) if isinstance(indicators, (list, tuple)) else merged["indicators"]
    )
    models = models or config.resolve_models(
        merged["sentiment_model"], merged["decision_model"]
    )
    account = get_account_info()
    positions = get_positions().get("positions", [])
    open_orders = get_open_orders()
    clock = get_market_clock()
    run_id = str(uuid.uuid4())

    news_ctx = {
        "symbol": focus,
        "models": models,
        "indicators": indicators,
        "max_credit": max_credit,
        "run_id": run_id,
    }
    news = NewsAgent().run(news_ctx)
    news_ctx["news"] = news
    sent_cls = (
        SentimentReactAgent if merged["deep_sentiment"] else SentimentAgent
    )
    sentiment = sent_cls().run(news_ctx)

    book = []
    focus_row = None
    for symbol in universe:
        row = snapshot_symbol(
            symbol, account, sentiment, indicators, max_credit, positions=positions
        )
        book.append(
            {
                "symbol": symbol,
                "market_state": row["market_state"],
                "risk": row["risk"],
            }
        )
        if symbol == focus:
            focus_row = row

    decision_ctx = {
        "symbol": focus,
        "models": models,
        "account": account,
        "sentiment": sentiment,
        "market_state": (focus_row or {}).get("market_state") or {},
        "risk": (focus_row or {}).get("risk") or {},
        "book": book,
        "open_orders": open_orders,
        "positions": compact_positions(positions),
        "working_book": compact_working_book(open_orders),
        "max_credit": max_credit,
        "run_id": run_id,
        "decision_indicators": parse_indicators(merged["decision_indicators"]),
    }
    dec_cls = DecisionReactAgent if merged["deep_decision"] else DecisionAgent
    decision = dec_cls().run(decision_ctx)
    intents = decision.get("intents") or intents_from_book(
        book, open_orders, config.MAX_WORKING_ORDERS
    )
    market_by_symbol = {row["symbol"]: row["market_state"] for row in book}
    results = apply_intents(
        intents, account, positions, clock, max_credit, market_by_symbol=market_by_symbol
    )

    focus_ctx = (focus_row or {}).get("ctx") or {}
    focus_intents = [i for i in intents if i.get("symbol") == focus]
    if focus_intents:
        gate_out = evaluate_gate(
            _decision_from_intent(focus_intents[0]),
            account,
            positions,
            open_orders,
            clock,
            max_credit=max_credit,
        )
    else:
        gate_out = evaluate_gate(
            decision,
            account,
            positions,
            open_orders,
            clock,
            max_credit=max_credit,
        )

    def _msg(node, output, fallback):
        if node == "news":
            n = output.get("n") if isinstance(output, dict) else None
            return f"{n} articles" if n is not None else fallback
        if node == "sentiment":
            return f"{(output or {}).get('sentiment')} · {(output or {}).get('confidence')}%"
        if node == "technical":
            return (output or {}).get("signal") or fallback
        if node == "market_state":
            return (output or {}).get("technical_signal") or fallback
        if node == "account":
            return (output or {}).get("mode") or fallback
        if node == "risk":
            return f"${(output or {}).get('position_size')}"
        if node == "decision":
            return f"{(output or {}).get('action')} · {len(intents)} intents"
        if node == "gate":
            return (output or {}).get("verdict") or fallback
        return fallback

    node_payloads = [
        ("news", news, "news"),
        ("sentiment", sentiment, "sentiment"),
        ("options", focus_ctx.get("options"), "options"),
        ("features", focus_ctx.get("features"), "features"),
        ("technical", focus_ctx.get("technical"), "technical"),
        ("orderblock", focus_ctx.get("orderblock"), "orderblock"),
        ("institutional", focus_ctx.get("institutional"), "flow"),
        ("market_state", focus_ctx.get("market_state"), "state"),
        ("account", account, account.get("mode") if isinstance(account, dict) else "account"),
        ("risk", focus_ctx.get("risk"), "risk"),
        ("decision", decision, "decision"),
        ("gate", gate_out, "gate"),
    ]
    nodes = []
    for name, output, fallback in node_payloads:
        message = _msg(name, output, fallback)
        nodes.append(
            {"node": name, "status": "done", "output": output, "message": message}
        )
        logs.record(
            run_id=run_id,
            symbol=focus,
            agent_id=name,
            kind="run",
            status="done",
            summary=message,
            payload=output if isinstance(output, dict) else None,
        )
    logs.record(
        run_id=run_id,
        symbol=focus,
        agent_id="book",
        kind="execute",
        status="done",
        summary=f"{len(intents)} intents · {len(results)} results",
        payload={
            "intents": intents,
            "results": [
                {
                    "status": r.get("status"),
                    "symbol": (r.get("intent") or {}).get("symbol"),
                }
                for r in results
            ],
        },
    )
    logs.flush_run(run_id)

    return {
        "focus": focus,
        "book": book,
        "decision": decision,
        "intents": intents,
        "results": results,
        "nodes": nodes,
        "account": account,
        "gate": gate_out,
        "risk": (focus_row or {}).get("risk"),
        "market_state": (focus_row or {}).get("market_state"),
    }
