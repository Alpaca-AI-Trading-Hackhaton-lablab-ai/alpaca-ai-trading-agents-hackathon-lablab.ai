import hashlib
import os

from dotenv import load_dotenv
from tavily import TavilyClient

from services import cache, secrets, usage_meter

# Load .env when this module is used standalone (e.g. imported before
# alpaca_service). Harmless if already loaded by another module.
load_dotenv()

# Recency window (days) and result cap. Trading sentiment should reflect fresh,
# market-moving headlines, not stale quote/overview pages. Override via env.
_NEWS_DAYS = int(os.getenv("TAVILY_NEWS_DAYS", "7") or 7)
_MAX_RESULTS = int(os.getenv("TAVILY_MAX_RESULTS", "10") or 10)

# Tavily returns whole scraped pages: prose interleaved with nav menus, cookie
# banners and unrelated ticker tables. Only the prose carries sentiment signal,
# and every junk character is a Groq token we pay for — the sentiment prompt
# interpolates this field verbatim. Condense once here, where the article
# enters ctx, so the LLM prompt and the API response both shrink.
_CONTENT_CHARS = int(os.getenv("TAVILY_CONTENT_CHARS", "400") or 400)

# Chrome arrives as short fragments ("Latest News", "More", "Back to the Top");
# real sentences run long. Length alone separates them well enough.
_MIN_PROSE_CHARS = 60


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


def _condense(text):
    """Drop page chrome from a scraped article and cap what survives."""
    if not text:
        return ""
    lines = [" ".join(raw.split()) for raw in str(text).splitlines()]
    prose = " ".join(line for line in lines if len(line) >= _MIN_PROSE_CHARS)
    # Every line was short: the article is a stub, not chrome. Keep it whole
    # rather than returning nothing.
    prose = prose or " ".join(str(text).split())
    if len(prose) <= _CONTENT_CHARS:
        return prose
    head, sep, _ = prose[:_CONTENT_CHARS].rpartition(" ")
    return (head if sep else prose[:_CONTENT_CHARS]) + "…"


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
                "content": _condense(r.get("content") or r.get("summary") or ""),
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
        usage_meter.record("tavily", requests=1, credits=2)
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
            usage_meter.record("tavily", requests=1, credits=2)

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
