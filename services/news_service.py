import hashlib
import os

from dotenv import load_dotenv
from tavily import TavilyClient

from services import cache, secrets

# Load .env when this module is used standalone (e.g. imported before
# alpaca_service). Harmless if already loaded by another module.
load_dotenv()

# Recency window (days) and result cap. Trading sentiment should reflect fresh,
# market-moving headlines, not stale quote/overview pages. Override via env.
_NEWS_DAYS = int(os.getenv("TAVILY_NEWS_DAYS", "7") or 7)
_MAX_RESULTS = int(os.getenv("TAVILY_MAX_RESULTS", "10") or 10)


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


def _clean(results):
    """Normalize Tavily results to the shape the sentiment agent consumes,
    preserving url + published_date so downstream can weight recency."""
    items = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        items.append(
            {
                "title": r.get("title") or "Untitled",
                "content": r.get("content") or r.get("summary") or "",
                "url": r.get("url", ""),
                "published_date": r.get("published_date", ""),
                "score": r.get("score"),
            }
        )
    return items


def _fetch_market_news(symbol, query):
    api_key = secrets.tavily_api_key()
    if not api_key:
        return _demo_news(symbol)

    try:
        tavily = TavilyClient(api_key=api_key)

        # topic="news" returns dated, news-specific articles — far better
        # sentiment fuel than the generic quote pages a plain search yields.
        response = tavily.search(
            query=query,
            topic="news",
            days=_NEWS_DAYS,
            search_depth="advanced",
            max_results=_MAX_RESULTS,
        )
        results = _clean(response.get("results"))

        # Thin news window (e.g. weekend/holiday): fall back to a general search
        # so the pipeline still has something to reason over.
        if not results:
            response = tavily.search(
                query=query,
                search_depth="advanced",
                max_results=_MAX_RESULTS,
            )
            results = _clean(response.get("results"))

        return results or _demo_news(symbol)

    except Exception:
        return _demo_news(symbol)


def get_market_news(symbol="SPY", query=None):
    symbol = (symbol or "SPY").upper()
    # `query` lets the ReAct research loop pass a specific sub-query; otherwise
    # use the default symbol-driven news query.
    query = query or f"{symbol} stock latest news, earnings, and market-moving headlines"
    suffix = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return cache.cached(
        "news",
        symbol,
        suffix,
        cache.TTL_NEWS,
        lambda: _fetch_market_news(symbol, query),
    )
