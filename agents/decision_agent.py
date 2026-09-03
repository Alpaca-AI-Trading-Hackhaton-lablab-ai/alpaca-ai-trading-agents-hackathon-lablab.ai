"""Decision node — scored setup (EMA/RSI3/OB/flow) plus optional ReAct loop.

Domain logic above; Agent wrappers at the bottom. The LLM (deep mode) picks
the action; position size always comes from the deterministic risk node.
"""

from agents.base import Agent, ReactAgent
from agents.feature_agent import get_market_features
from agents.indicator_engine import filter_snapshot
from agents.institutional_flow import detect_smart_money
from agents.orderblock_engine import detect_orderblocks
from agents.react_core import extract_json
from agents.technical_agent import technical_analysis
from agents import research_tools
from services import config, logs
from services.news_service import get_market_news

SCORE_THRESHOLD = 2.5


def score_setup(market_state):
    """Deterministic buy/sell scores. Missing fields add 0 (fail-closed)."""
    ms = market_state or {}
    buy = 0.0
    sell = 0.0

    tech = ms.get("technical_signal") or ms.get("signal")
    if tech == "BUY":
        buy += 2
    elif tech == "SELL":
        sell += 2

    rsi_sig = ms.get("rsi_signal")
    if rsi_sig == "OVERSOLD":
        buy += 1.5
    elif rsi_sig == "OVERBOUGHT":
        sell += 1.5

    if ms.get("near_bullish"):
        buy += 3
    if ms.get("near_bearish"):
        sell += 3

    if ms.get("smart_money_buying"):
        buy += 2
    if ms.get("smart_money_selling"):
        sell += 2

    sentiment = ms.get("sentiment")
    if sentiment in ("POSITIVE", "BULLISH"):
        buy += 1
    elif sentiment in ("NEGATIVE", "BEARISH"):
        sell += 1

    return {"buy": buy, "sell": sell}


def _orderblock_label(ms):
    if ms.get("near_bullish"):
        return "BULLISH"
    if ms.get("near_bearish"):
        return "BEARISH"
    if (ms.get("bullish_ob") or {}).get("price"):
        return "BULLISH"
    if (ms.get("bearish_ob") or {}).get("price"):
        return "BEARISH"
    return "NONE"


def _signals(ms):
    return {
        "technical": ms.get("technical_signal"),
        "rsi": ms.get("rsi_signal"),
        "orderblock": _orderblock_label(ms),
        "institutional": ms.get("institutional_signal"),
        "sentiment": ms.get("sentiment"),
    }


def make_decision(market_state, risk):
    market_state = market_state or {}
    risk = risk or {}
    scores = score_setup(market_state)
    buy, sell = scores["buy"], scores["sell"]

    if market_state.get("error"):
        action = "HOLD"
    elif buy > sell and buy >= SCORE_THRESHOLD:
        action = "BUY"
    elif sell > buy and sell >= SCORE_THRESHOLD:
        action = "SELL"
    else:
        action = "HOLD"

    return {
        "symbol": market_state.get("symbol"),
        "action": action,
        "position_size": risk.get("position_size"),
        "technical_signal": market_state.get("technical_signal"),
        "sentiment": market_state.get("sentiment"),
        "risk_level": risk.get("risk_level"),
        "scores": scores,
        "signals": _signals(market_state),
    }


class DecisionAgent(Agent):
    node = "decision"

    def run(self, ctx):
        return make_decision(ctx.get("market_state") or {}, ctx.get("risk") or {})

    def message(self, out):
        if self._err(out):
            return self._err(out)
        model = out.get("model") or ""
        short = model.split("/")[-1] if model else ""
        scores = out.get("scores") or {}
        head = (
            f"{out.get('action')} ({out.get('sentiment')}×{out.get('technical_signal')}"
            f" · {scores.get('buy', 0)}/{scores.get('sell', 0)})"
        )
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
            "get_market_news, recent_history, lookup_concept, detect_orderblocks, "
            "detect_smart_money. Use lookup_concept when you do not know a term "
            "or indicator (Instant Answer, not news). Reflect on whether the "
            "action is well-supported before finalizing. Do NOT size positions. "
            "When decided, output ONLY JSON: "
            '{"action": "BUY|SELL|HOLD", "rationale": "...", "confidence": 0-100}. '
            "Output JSON only."
        )

    def goal(self, ctx):
        ms = filter_snapshot(
            ctx.get("market_state") or {},
            ctx.get("decision_indicators"),
        )
        risk = ctx.get("risk") or {}
        scores = score_setup(ms)
        bits = [
            f"Symbol {ms.get('symbol')}.",
            f"sentiment={ms.get('sentiment')}",
            f"technical_signal={ms.get('technical_signal')}",
            f"trend={ms.get('trend')}",
            f"ema_trend={ms.get('ema_trend')}",
            f"rsi_signal={ms.get('rsi_signal')}",
            f"near_bullish={ms.get('near_bullish')}",
            f"near_bearish={ms.get('near_bearish')}",
            f"institutional_signal={ms.get('institutional_signal')}",
            f"scores buy={scores['buy']} sell={scores['sell']}",
        ]
        for key in (
            "rsi",
            "rsi3",
            "sma20",
            "sma50",
            "ema3",
            "ema10",
            "ema20",
            "ema50",
            "ema100",
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

        def _ob(symbol, **_k):
            return detect_orderblocks(symbol)

        def _flow(symbol, **_k):
            return detect_smart_money(symbol)

        return {
            "get_market_features": _features,
            "technical_analysis": _technical,
            "get_market_news": get_market_news,
            "recent_history": research_tools.recent_history,
            "lookup_concept": research_tools.lookup_concept,
            "detect_orderblocks": _ob,
            "detect_smart_money": _flow,
        }

    def finalize(self, text, ctx):
        parsed = extract_json(text)
        if not isinstance(parsed, dict) or "action" not in parsed:
            raise ValueError("no final action")
        action = str(parsed.get("action", "")).upper()
        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"
        ms = ctx.get("market_state") or {}
        risk = ctx.get("risk") or {}
        return {
            "symbol": ms.get("symbol"),
            "action": action,
            "position_size": risk.get("position_size"),
            "technical_signal": ms.get("technical_signal"),
            "sentiment": ms.get("sentiment"),
            "risk_level": risk.get("risk_level"),
            "scores": score_setup(ms),
            "signals": _signals(ms),
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
            "scores": score_setup(ms),
            "signals": _signals(ms),
            "rationale": f"fail-closed: {reason}",
            "confidence": 0,
            "model": (ctx.get("models") or {}).get("decision"),
        }
