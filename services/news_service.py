import os

from tavily import TavilyClient


def _demo_news(symbol):
    symbol = (symbol or "SPY").upper()
    return [
        {
            "title": f"{symbol} holds steady as traders wait for macro data",
            "content": "Markets are mixed, with investors balancing momentum against rate uncertainty.",
        },
        {
            "title": f"Analysts see cautious demand around {symbol}",
            "content": "Technical signals remain neutral while liquidity conditions stay orderly.",
        },
    ]


def get_market_news(symbol="SPY"):
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return _demo_news(symbol)

    try:
        tavily = TavilyClient(api_key=api_key)
        response = tavily.search(
            query=f"{(symbol or 'SPY').upper()} stock market latest news today",
            max_results=10,
        )
        return response.get("results") or _demo_news(symbol)

    except Exception:
        return _demo_news(symbol)
