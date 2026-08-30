from tavily import TavilyClient
import os

tavily = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)

def get_market_news():

    response = tavily.search(
        query="SPY stock market latest news today",
        max_results=10
    )

    return response["results"]