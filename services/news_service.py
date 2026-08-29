import os
import json

from tavily import TavilyClient
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage

tavily = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)

llm = ChatGroq(
    model="openai/gpt-oss-120b",
    api_key=os.getenv("GROQ_API_KEY")
)


def get_market_news():
    try:

        response = tavily.search(
            query="SPY stock market latest news today",
            max_results=10
        )

        raw_news = ""

        for item in response["results"]:
            raw_news += f"""
Headline: {item['title']}
Summary: {item['content']}

"""

        result = llm.invoke([
            HumanMessage(content=f"""
You are a professional stock market analyst.

Analyze these SPY news articles.

{raw_news}

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
""")
])

        # Convert JSON string to Python dict
        analysis = json.loads(result.content)

        return {
            "market": "SPY",
            "news_count": len(response["results"]),
            "analysis": analysis
        }

    except Exception as e:
        return {
            "error": str(e)
        }