"""Pipeline nodes as an Agent class hierarchy.

Every node is an `Agent` subclass whose `run()` delegates to the existing
(tested) function and whose `message()` is the per-node "communication". ReAct
is applied only where it helps the proposal — `SentimentReactAgent` and
`DecisionReactAgent` inherit the plain agents and redefine `run()` with a
bounded, self-terminating loop. The gate stays a deterministic `Agent`.
"""

from agents.base import Agent, ReactAgent
from agents.decision_agent import make_decision
from agents.execution_gate import evaluate_gate
from agents.feature_agent import get_market_features
from agents.indicator_engine import filter_snapshot
from agents.market_state_agent import build_market_state
from agents.options_agent import options_strategy
from agents.react_core import extract_json
from agents.risk_manager import calculate_risk
from agents.sentiment_agent import _neutral, analyze_sentiment
from agents.technical_agent import technical_analysis
from agents import research_tools
from services import config, logs
from services.alpaca_service import (
    get_account_info,
    get_market_clock,
    get_open_orders,
    get_positions,
)
from services.news_service import get_market_news

# Trace keys, in topological order. Each node = one agent.
PIPELINE_KEYS = (
    "news",
    "sentiment",
    "options",
    "features",
    "technical",
    "market_state",
    "account",
    "risk",
    "decision",
    "gate",
)


def _err(out):
    if isinstance(out, dict) and out.get("error"):
        return str(out["error"])
    return None


# --- Deterministic nodes (run() delegates; message() is the old _msg branch) ---


class NewsAgent(Agent):
    node = "news"

    def run(self, ctx):
        return get_market_news(ctx["symbol"])

    def message(self, out):
        n = len(out) if isinstance(out, list) else "n/a"
        return f"{n} articles"


class SentimentAgent(Agent):
    node = "sentiment"

    def run(self, ctx):
        model = (ctx.get("models") or {}).get("sentiment")
        history = logs.history_text(ctx.get("symbol"))
        return analyze_sentiment(ctx.get("news"), model=model, history=history)

    def message(self, out):
        if _err(out):
            return _err(out)
        summary = (out.get("summary") or "").strip()
        model = out.get("model") or ""
        short = model.split("/")[-1] if model else ""
        head = f"{out.get('sentiment')} · {out.get('confidence')}%"
        if short:
            head = f"{head} · {short}"
        return f"{head} — {summary[:80]}" if summary else head


class OptionsAgent(Agent):
    node = "options"

    def run(self, ctx):
        sentiment = ctx["sentiment"]
        return options_strategy(
            sentiment.get("sentiment", "NEUTRAL"),
            sentiment.get("confidence", 0),
            ctx["symbol"],
        )

    def message(self, out):
        return _err(out) or f"{out.get('strategy')} / {out.get('action')}"


class FeatureAgent(Agent):
    node = "features"

    def run(self, ctx):
        return get_market_features(ctx["symbol"], ctx.get("indicators"))

    def message(self, out):
        return _err(out) or f"{out.get('trend')} @ {out.get('price')}"


class TechnicalAgent(Agent):
    node = "technical"

    def run(self, ctx):
        return technical_analysis(ctx["symbol"], ctx.get("indicators"))

    def message(self, out):
        rsi = out.get("rsi")
        extra = f" (RSI {rsi})" if rsi is not None else ""
        return _err(out) or f"{out.get('signal')}{extra}"


class MarketStateAgent(Agent):
    node = "market_state"

    def run(self, ctx):
        return build_market_state(
            ctx["sentiment"], ctx["options"], ctx["features"], ctx["technical"]
        )

    def message(self, out):
        return _err(out) or (
            f"{out.get('sentiment')} · {out.get('technical_signal')} · {out.get('trend')}"
        )


class AccountAgent(Agent):
    node = "account"

    def run(self, ctx):
        return get_account_info()

    def message(self, out):
        return _err(out) or f"{out.get('mode')} · {out.get('status')}"


class RiskAgent(Agent):
    node = "risk"

    def run(self, ctx):
        return calculate_risk(
            ctx["account"].get("equity", 100000),
            ctx["sentiment"].get("confidence", 0),
        )

    def message(self, out):
        return _err(out) or f"{out.get('risk_level')} · ${out.get('position_size')}"


class DecisionAgent(Agent):
    node = "decision"

    def run(self, ctx):
        return make_decision(ctx["market_state"], ctx["risk"])

    def message(self, out):
        if _err(out):
            return _err(out)
        model = out.get("model") or ""
        short = model.split("/")[-1] if model else ""
        head = f"{out.get('action')} ({out.get('sentiment')}×{out.get('technical_signal')})"
        return f"{head} · {short}" if short else head


class GateAgent(Agent):
    node = "gate"

    def run(self, ctx):
        return evaluate_gate(
            ctx["decision"],
            ctx["account"],
            get_positions().get("positions", []),
            get_open_orders(ctx["symbol"]),
            get_market_clock(),
        )

    def message(self, out):
        verdict = out.get("verdict")
        if verdict == "BLOCK":
            reasons = out.get("reasons") or []
            return f"BLOCK — {reasons[0] if reasons else 'blocked'}"
        if verdict == "NO_TRADE":
            return "NO_TRADE"
        return f"ALLOW · {len(out.get('checks') or [])} checks"


# --- ReAct variants (inherit the plain agent, redefine run() with the loop) ---


class SentimentReactAgent(ReactAgent, SentimentAgent):
    """Research loop: decompose 'what's driving {symbol}?' -> parallel Tavily
    fan-out -> the model gathers more or finalizes a sentiment verdict."""

    max_turns = config.RESEARCH_MAX_TURNS

    def system_prompt(self):
        return (
            "You are a professional stock-market analyst assessing sentiment "
            "from recent news. You may gather more evidence by calling a tool. "
            'To call a tool, output ONLY JSON: {"tool": "get_market_news", '
            '"params": {"symbol": "AAPL", "query": "..."}}. When you have '
            "enough, output ONLY the final sentiment as JSON: "
            '{"sentiment": "BULLISH|BEARISH|NEUTRAL", "confidence": 0-100, '
            '"summary": "...", "trade_bias": "CALL|PUT|WAIT", '
            '"key_points": ["..."]}. Weight recent headlines more heavily. '
            "Output JSON only."
        )

    def goal(self, ctx):
        return f"What is driving {ctx['symbol']} right now? Assess market sentiment."

    def tools(self, ctx=None):
        return research_tools.subset("get_market_news", "recent_history")

    def seed(self, ctx, _reasoner):
        # Reuse NewsAgent output so deep mode does not fan-out Tavily twice.
        news = ctx.get("news")
        items = news if isinstance(news, list) else (news or {}).get("results") or []
        lines = []
        for art in items[:12]:
            if isinstance(art, dict):
                date = art.get("published_date") or "?"
                lines.append(f"- ({date}) {art.get('title', '')}")
        seed = "Initial evidence:\n" + "\n".join(lines) if lines else ""
        history = logs.history_text(ctx.get("symbol"))
        if history:
            seed = f"{seed}\n\n{history}" if seed else history
        return seed

    def finalize(self, text, ctx):
        parsed = extract_json(text)
        if not isinstance(parsed, dict) or "sentiment" not in parsed:
            raise ValueError("no final sentiment")
        return {
            "sentiment": parsed.get("sentiment", "NEUTRAL"),
            "confidence": parsed.get("confidence", 0),
            "summary": parsed.get("summary", ""),
            "trade_bias": parsed.get("trade_bias", "WAIT"),
            "key_points": parsed.get("key_points", []),
            "model": (ctx.get("models") or {}).get("sentiment"),
        }

    def fallback(self, reason, ctx):
        return _neutral(reason, model=(ctx.get("models") or {}).get("sentiment"))


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
            "get_market_news, recent_history. Reflect on whether the action is well-supported "
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


def build_pipeline(deep=False, deep_sentiment=None, deep_decision=None):
    """The ordered list of Agent instances for one run. `deep` swaps in the
    ReAct variants for sentiment and decision; everything else is deterministic.
    Per-node deep flags override the global `deep` when provided."""
    use_sent = deep if deep_sentiment is None else deep_sentiment
    use_dec = deep if deep_decision is None else deep_decision
    sentiment = SentimentReactAgent() if use_sent else SentimentAgent()
    decision = DecisionReactAgent() if use_dec else DecisionAgent()
    return [
        NewsAgent(),
        sentiment,
        OptionsAgent(),
        FeatureAgent(),
        TechnicalAgent(),
        MarketStateAgent(),
        AccountAgent(),
        RiskAgent(),
        decision,
        GateAgent(),
    ]
