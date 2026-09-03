"""Sentiment node — Groq one-shot, plus optional ReAct research loop.

Domain logic above; Agent wrappers at the bottom.
"""

import json

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

from agents.base import Agent, ReactAgent
from agents.react_core import extract_json
from agents import research_tools
from services import config, logs, secrets, usage_meter


def _neutral(reason="Sentiment unavailable", model=None):
    out = {
        "sentiment": "NEUTRAL",
        "confidence": 0,
        "summary": reason,
        "trade_bias": "WAIT",
        "key_points": [],
    }
    if model:
        out["model"] = model
    return out


def _normalize_news(news):
    if isinstance(news, dict):
        news = news.get("results", [])
    if not isinstance(news, list):
        return []

    items = []
    for item in news:
        if isinstance(item, dict):
            items.append(
                {
                    "title": item.get("title", "Untitled"),
                    "content": item.get("content") or item.get("summary") or "",
                    "published_date": item.get("published_date", ""),
                }
            )
    return items


def _strip_json_fence(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`").removeprefix("json").strip()
    return content


def analyze_sentiment(news, model=None, history=None):
    model_id = model or config.resolve_models()["sentiment"]
    cheap = usage_meter.cheap_model_id()
    if cheap:
        model_id = cheap
    items = _normalize_news(news)
    if not items:
        return _neutral("No news available", model=model_id)

    api_key = secrets.groq_api_key()
    if not api_key:
        return _neutral("Missing GROQ_API_KEY", model=model_id)

    raw_news = ""
    for item in items:
        date = item.get("published_date") or "date unknown"
        raw_news += f"""
Date: {date}
Headline: {item['title']}
Summary: {item['content']}

"""

    history_block = ""
    if history:
        history_block = f"\n{history}\n"

    try:
        llm = ChatGroq(
            model=model_id,
            api_key=api_key,
            max_retries=2,
        )

        result = llm.invoke(
            [
                HumanMessage(
                    content=f"""
You are a professional stock market analyst.

Analyze these stock market news articles. Weight more recent headlines
(see each article's Date) more heavily than older ones.
{history_block}
Return ONLY valid JSON.

{{
  "sentiment": "BULLISH | BEARISH | NEUTRAL",
  "confidence": 0,
  "summary": "",
  "trade_bias": "CALL | PUT | WAIT",
  "key_points": [
    "...",
    "...",
    "..."
  ]
}}

News:

{raw_news}
"""
                )
            ]
        )

        usage_meter.capture_groq(result, model=model_id)
        parsed = json.loads(_strip_json_fence(result.content))
        return {
            "sentiment": parsed.get("sentiment", "NEUTRAL"),
            "confidence": parsed.get("confidence", 0),
            "summary": parsed.get("summary", ""),
            "trade_bias": parsed.get("trade_bias", "WAIT"),
            "key_points": parsed.get("key_points", []),
            "model": model_id,
        }

    except Exception as e:
        return _neutral(str(e), model=model_id)


class SentimentAgent(Agent):
    node = "sentiment"

    def run(self, ctx):
        model = (ctx.get("models") or {}).get("sentiment")
        history = logs.history_text(ctx.get("symbol"), agent_id="sentiment")
        return analyze_sentiment(ctx.get("news"), model=model, history=history)

    def message(self, out):
        if self._err(out):
            return self._err(out)
        summary = (out.get("summary") or "").strip()
        model = out.get("model") or ""
        short = model.split("/")[-1] if model else ""
        head = f"{out.get('sentiment')} · {out.get('confidence')}%"
        if short:
            head = f"{head} · {short}"
        return f"{head} — {summary[:80]}" if summary else head


class SentimentReactAgent(ReactAgent, SentimentAgent):
    """Research loop: decompose 'what's driving {symbol}?' -> parallel Tavily
    fan-out -> the model gathers more or finalizes a sentiment verdict."""

    max_turns = config.RESEARCH_MAX_TURNS

    def system_prompt(self):
        return (
            "You are a professional stock-market analyst assessing sentiment "
            "from recent news. You may gather more evidence by calling a tool. "
            'To call a tool, output ONLY JSON: {"tool": "get_market_news", '
            '"params": {"symbol": "AAPL", "query": "..."}}. When a term, entity, '
            "or indicator is unfamiliar, call lookup_concept "
            '{"tool": "lookup_concept", "params": {"query": "..."}} — Instant '
            "Answers only, not live news. Use get_market_news for headlines. "
            "When you have enough, output ONLY the final sentiment as JSON: "
            '{"sentiment": "BULLISH|BEARISH|NEUTRAL", "confidence": 0-100, '
            '"summary": "...", "trade_bias": "CALL|PUT|WAIT", '
            '"key_points": ["..."]}. Weight recent headlines more heavily. '
            "Output JSON only."
        )

    def goal(self, ctx):
        return f"What is driving {ctx['symbol']} right now? Assess market sentiment."

    def tools(self, ctx=None):
        tools = research_tools.subset(
            "get_market_news", "recent_history", "lookup_concept"
        )
        tools["recent_history"] = lambda symbol="SPY", limit=10, **_k: (
            research_tools.recent_history(symbol, limit=limit, agent_id="sentiment")
        )
        return tools

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
        history = logs.history_text(ctx.get("symbol"), agent_id="sentiment")
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
