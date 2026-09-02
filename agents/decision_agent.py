"""Decision node — rule-based if/else, plus optional ReAct loop.

Domain logic above; Agent wrappers at the bottom. The LLM (deep mode) picks
the action; position size always comes from the deterministic risk node.
"""

from agents.base import Agent, ReactAgent
from agents.feature_agent import get_market_features
from agents.indicator_engine import filter_snapshot
from agents.react_core import extract_json
from agents.technical_agent import technical_analysis
from agents import research_tools
from services import config, logs
from services.news_service import get_market_news


def make_decision(
    market_state,
    risk
):

    sentiment = market_state["sentiment"]
    technical = market_state["technical_signal"]

    max_position = risk["position_size"]   # FIX

    if (
        sentiment in ("POSITIVE", "BULLISH")
        and technical == "BUY"
    ):
        action = "BUY"

    elif (
        sentiment in ("NEGATIVE", "BEARISH")
        and technical == "SELL"
    ):
        action = "SELL"

    else:
        action = "HOLD"

    return {
        "symbol": market_state["symbol"],
        "action": action,
        "position_size": max_position,
        "technical_signal": technical,
        "sentiment": sentiment,
        "risk_level": risk["risk_level"]
    }


class DecisionAgent(Agent):
    node = "decision"

    def run(self, ctx):
        return make_decision(ctx["market_state"], ctx["risk"])

    def message(self, out):
        if self._err(out):
            return self._err(out)
        model = out.get("model") or ""
        short = model.split("/")[-1] if model else ""
        head = f"{out.get('action')} ({out.get('sentiment')}×{out.get('technical_signal')})"
        return f"{head} · {short}" if short else head


class DecisionReactAgent(ReactAgent, DecisionAgent):
    """Reasoning loop over market_state + risk. May pull more read-only
    evidence and decides when to stop; the LLM picks the action, never the size."""

    max_turns = config.DECISION_MAX_TURNS

    def system_prompt(self):
        return (
            "You are a trading decision agent. Decide BUY, SELL, or HOLD for a "
            "symbol given its market state and risk. You may gather more "
            "evidence by calling a read-only tool. To call a tool, output ONLY "
            'JSON: {"tool": "technical_analysis", "params": {"symbol": "AAPL"}}. '
            "Available tools: get_market_features, technical_analysis, "
            "get_market_news, recent_history, lookup_concept. Use lookup_concept "
            "when you do not know a term or indicator (Instant Answer, not news). "
            "Reflect on whether the action is well-supported "
            "before finalizing. Do NOT size positions. When decided, output "
            'ONLY JSON: {"action": "BUY|SELL|HOLD", "rationale": "...", '
            '"confidence": 0-100}. Output JSON only.'
        )

    def goal(self, ctx):
        ms = filter_snapshot(
            ctx.get("market_state") or {},
            ctx.get("decision_indicators"),
        )
        risk = ctx["risk"]
        bits = [
            f"Symbol {ms.get('symbol')}.",
            f"sentiment={ms.get('sentiment')}",
            f"technical_signal={ms.get('technical_signal')}",
            f"trend={ms.get('trend')}",
        ]
        for key in (
            "rsi",
            "sma20",
            "sma50",
            "ema20",
            "macd",
            "atr",
            "volume",
            "confidence",
        ):
            if key in ms and ms[key] is not None:
                bits.append(f"{key}={ms[key]}")
        goal = (
            " ".join(bits)
            + f". Risk: level={risk.get('risk_level')}, position_size=${risk.get('position_size')}. "
            "Decide the action."
        )
        history = logs.history_text(ctx.get("symbol"))
        return f"{goal}\n\n{history}" if history else goal

    def tools(self, ctx=None):
        inds = (ctx or {}).get("indicators")

        def _features(symbol, **_k):
            return get_market_features(symbol, indicators=inds)

        def _technical(symbol, **_k):
            return technical_analysis(symbol, indicators=inds)

        return {
            "get_market_features": _features,
            "technical_analysis": _technical,
            "get_market_news": get_market_news,
            "recent_history": research_tools.recent_history,
            "lookup_concept": research_tools.lookup_concept,
        }

    def finalize(self, text, ctx):
        parsed = extract_json(text)
        if not isinstance(parsed, dict) or "action" not in parsed:
            raise ValueError("no final action")
        action = str(parsed.get("action", "")).upper()
        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"
        ms = ctx["market_state"]
        risk = ctx["risk"]
        return {
            "symbol": ms.get("symbol"),
            "action": action,
            "position_size": risk.get("position_size"),  # deterministic sizing
            "technical_signal": ms.get("technical_signal"),
            "sentiment": ms.get("sentiment"),
            "risk_level": risk.get("risk_level"),
            "rationale": parsed.get("rationale", ""),
            "confidence": parsed.get("confidence"),
            "model": (ctx.get("models") or {}).get("decision"),
        }

    def fallback(self, reason, ctx):
        ms = ctx.get("market_state") or {}
        risk = ctx.get("risk") or {}
        return {
            "symbol": ms.get("symbol"),
            "action": "HOLD",
            "position_size": risk.get("position_size"),
            "technical_signal": ms.get("technical_signal"),
            "sentiment": ms.get("sentiment"),
            "risk_level": risk.get("risk_level"),
            "rationale": f"fail-closed: {reason}",
            "confidence": 0,
            "model": (ctx.get("models") or {}).get("decision"),
        }
