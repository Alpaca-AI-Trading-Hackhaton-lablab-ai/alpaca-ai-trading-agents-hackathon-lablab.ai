import json
import os

from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq


def _neutral(reason="Sentiment unavailable"):
    return {
        "sentiment": "NEUTRAL",
        "confidence": 0,
        "summary": reason,
        "trade_bias": "WAIT",
        "key_points": [],
    }


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
                }
            )
    return items


def _strip_json_fence(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`").removeprefix("json").strip()
    return content


def analyze_sentiment(news):
    items = _normalize_news(news)
    if not items:
        return _neutral("No news available")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return _neutral("Missing GROQ_API_KEY")

    raw_news = ""
    for item in items:
        raw_news += f"""
Headline: {item['title']}
Summary: {item['content']}

"""

    try:
        llm = ChatGroq(
            model="openai/gpt-oss-120b",
            api_key=api_key,
        )

        result = llm.invoke(
            [
                HumanMessage(
                    content=f"""
You are a professional stock market analyst.

Analyze these stock market news articles.

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
        }

    except Exception as e:
        return _neutral(str(e))
