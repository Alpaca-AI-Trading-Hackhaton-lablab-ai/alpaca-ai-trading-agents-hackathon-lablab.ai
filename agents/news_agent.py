"""News pipeline node. Fetches Tavily headlines; no LLM.

Agent wrapper at the bottom. Fetch logic lives in services.news_service.
"""

from agents.base import Agent
from services.news_service import get_market_news


class NewsAgent(Agent):
    node = "news"

    def run(self, ctx):
        return get_market_news(ctx["symbol"])

    def message(self, out):
        n = len(out) if isinstance(out, list) else "n/a"
        return f"{n} articles"
