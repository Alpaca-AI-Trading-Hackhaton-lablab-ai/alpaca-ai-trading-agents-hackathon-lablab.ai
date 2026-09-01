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
from agents.market_state_agent import build_market_state
from agents.options_agent import options_strategy
from agents.react_core import decompose, extract_json, parallel_map
from agents.risk_manager import calculate_risk
from agents.sentiment_agent import _neutral, analyze_sentiment
from agents.technical_agent import technical_analysis
from agents import research_tools
from services import config
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
        return analyze_sentiment(ctx["news"])

    def message(self, out):
        if _err(out):
            return _err(out)
        summary = (out.get("summary") or "").strip()
        head = f"{out.get('sentiment')} · {out.get('confidence')}%"
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
        return get_market_features(ctx["symbol"])

    def message(self, out):
        return _err(out) or f"{out.get('trend')} @ {out.get('price')}"


class TechnicalAgent(Agent):
    node = "technical"

    def run(self, ctx):
        return technical_analysis(ctx["symbol"])

    def message(self, out):
        return _err(out) or f"{out.get('signal')} (RSI {out.get('rsi')})"


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
        return f"{out.get('action')} ({out.get('sentiment')}×{out.get('technical_signal')})"


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

    def tools(self):
        return research_tools.subset("get_market_news")

    def seed(self, ctx, reasoner):
        symbol = ctx["symbol"]
        subqueries = decompose(reasoner, f"What is moving {symbol} stock right now?")
        batches = parallel_map(
            [(lambda q=q: get_market_news(symbol, query=q)) for q in subqueries],
            max_workers=3,
        )
        lines = []
        for batch in batches:
            if isinstance(batch, list):
                for art in batch[:3]:
                    date = art.get("published_date") or "?"
                    lines.append(f"- ({date}) {art.get('title', '')}")
        return "Initial evidence:\n" + "\n".join(lines[:20]) if lines else ""

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
        }

    def fallback(self, reason, ctx):
        return _neutral(reason)


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
            "get_market_news. Reflect on whether the action is well-supported "
            "before finalizing. Do NOT size positions. When decided, output "
            'ONLY JSON: {"action": "BUY|SELL|HOLD", "rationale": "...", '
            '"confidence": 0-100}. Output JSON only.'
        )

    def goal(self, ctx):
        ms = ctx["market_state"]
        risk = ctx["risk"]
        return (
            f"Symbol {ms.get('symbol')}. Market state: "
            f"sentiment={ms.get('sentiment')}, technical_signal={ms.get('technical_signal')}, "
            f"trend={ms.get('trend')}, rsi={ms.get('rsi')}, confidence={ms.get('confidence')}. "
            f"Risk: level={risk.get('risk_level')}, position_size=${risk.get('position_size')}. "
            "Decide the action."
        )

    def tools(self):
        return research_tools.subset(
            "get_market_features", "technical_analysis", "get_market_news"
        )

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
        }

    def fallback(self, reason, ctx):
        base = make_decision(ctx["market_state"], ctx["risk"])
        base["rationale"] = f"fail-closed: {reason}"
        return base


def build_pipeline(deep=False):
    """The ordered list of Agent instances for one run. `deep` swaps in the
    ReAct variants for sentiment and decision; everything else is deterministic."""
    sentiment = SentimentReactAgent() if deep else SentimentAgent()
    decision = DecisionReactAgent() if deep else DecisionAgent()
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
