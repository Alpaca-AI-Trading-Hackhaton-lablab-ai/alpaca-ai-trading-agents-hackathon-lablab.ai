import os
import json

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage

llm = ChatGroq(
    model="openai/gpt-oss-120b",
    api_key=os.getenv("GROQ_API_KEY")
)

def analyze_sentiment(news):

    raw_news = ""

    for item in news:
        raw_news += f"""
Headline: {item['title']}
Summary: {item['content']}

"""

    result = llm.invoke([
        HumanMessage(content=f"""
You are a professional stock market analyst.

Analyze these SPY news articles.

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
""")
    ])

    return json.loads(result.content)