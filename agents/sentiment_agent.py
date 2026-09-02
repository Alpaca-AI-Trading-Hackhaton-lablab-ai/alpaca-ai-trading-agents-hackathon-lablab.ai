import json

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

from services import config, secrets


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
