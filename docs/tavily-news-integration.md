# Tavily News Integration

How the Tavily web-search API is coupled into the **news → sentiment** stage of
the TradeLix agent pipeline.

## Role in the pipeline

Tavily is the **news source** feeding the sentiment agent. It is the first node
in the pipeline defined in `backend.py`:

```
news → sentiment → options → features → technical → market_state
     → account → risk → decision → gate → execute
```

- `services/news_service.py` — calls Tavily, returns normalized articles.
- `agents/news_agent.py` — `NewsAgent` pipeline wrapper (Tavily fetch; no LLM).
- `agents/sentiment_agent.py` — `analyze_sentiment()` plus `SentimentAgent` /
  `SentimentReactAgent` at the bottom of the file. Consumes articles and produces a
  sentiment verdict via Groq.

```
get_market_news(symbol)  ──▶  analyze_sentiment(news)  ──▶  { sentiment, confidence, trade_bias, ... }
   (Tavily, news_service)        (Groq, sentiment_agent)
```

## Configuration

Set in the project `.env` (see `.env.example`):

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TAVILY_API_KEY` | no* | — | Tavily API key (`tvly-...`). |
| `TAVILY_NEWS_DAYS` | no | `7` | Recency window for the news search, in days. |
| `TAVILY_MAX_RESULTS` | no | `10` | Max articles returned per query. |

\* If `TAVILY_API_KEY` is absent, `news_service` returns **demo news** so the
pipeline still runs offline. The rest of the pipeline degrades gracefully:
without `GROQ_API_KEY` the sentiment agent returns a `NEUTRAL` verdict.

`.env` is loaded via `load_dotenv()`, called in both `services/alpaca_service.py`
and `services/news_service.py` (so the news module works even when imported on
its own).

## How the search is issued

`get_market_news(symbol)` in `services/news_service.py`:

```python
tavily.search(
    query=f"{symbol} stock latest news, earnings, and market-moving headlines",
    topic="news",            # dated, news-specific articles (not quote pages)
    days=_NEWS_DAYS,         # recency window (default 7)
    search_depth="advanced", # richer content snippets
    max_results=_MAX_RESULTS,
)
```

`topic="news"` is what makes the results useful for sentiment: it returns dated,
market-moving headlines rather than static quote/overview landing pages.

### Fallback chain

1. **News search** (`topic="news"`, time-bounded) — the primary path.
2. **General search** — if the news window is thin (weekend/holiday) and returns
   nothing, retry without the news topic.
3. **Demo news** — if both are empty, or on any exception, or when no API key is
   set.

The function never raises: a Tavily outage or bad key falls back to demo news
instead of breaking the pipeline.

## Providers evaluated and rejected

Tavily stays the only **news** source. These were checked and **not** wired as
headline search:

- **X (Twitter) API** — no free search for new developers (2026 pay-per-use;
  search was never on the old free tier). Scraping x.com is not an option.
- **DuckDuckGo HTML / `ddgs` scrapers** — violate DuckDuckGo's terms; they do
  not syndicate full search results.

DuckDuckGo's **official Instant Answer API** is used separately as
`lookup_concept` for jargon and entities the ReAct loops do not know — not for
news. See `docs/ddg-concept-lookup.md`.

## Output shape

Each article is normalized by `_clean()` to:

```json
{
  "title": "Stocks Rally as Fed Chair Warsh Vows to Tackle Inflation",
  "content": "…snippet of the article body…",
  "url": "https://…",
  "published_date": "Fri, 28 Aug 2026",
  "score": 0.94
}
```

`title` and `content` are what the sentiment agent reads; `url`, `published_date`
and `score` are carried through for recency weighting and the frontend.

## How sentiment uses it

`agents/sentiment_agent.py`:

- `_normalize_news()` keeps `published_date` alongside title/content.
- Each article is rendered into the prompt with its date, and the analyst is
  instructed to **weight more recent headlines more heavily**.
- Returns strict JSON:

```json
{
  "sentiment": "BULLISH | BEARISH | NEUTRAL",
  "confidence": 0.0,
  "summary": "…",
  "trade_bias": "CALL | PUT | WAIT",
  "key_points": ["…", "…", "…"]
}
```

## Endpoints

The news and sentiment outputs are exposed by `backend.py`:

- `GET /news?symbol=SPY` — raw Tavily articles for a symbol.
- `GET /sentiment?symbol=SPY` — the sentiment verdict derived from them.
- `GET /pipeline?symbol=SPY` and `GET /pipeline/stream?symbol=SPY` — the full
  agent trace, with `news` and `sentiment` as the first two nodes.

## Quick check

From the project directory, with `.env` populated:

```bash
PYTHONPATH="$(pwd)" python3 - <<'PY'
from dotenv import load_dotenv; load_dotenv()
from services.news_service import get_market_news
from agents.sentiment_agent import analyze_sentiment
news = get_market_news("SPY")
print(len(news), "articles")
print(analyze_sentiment(news)["sentiment"])
PY
```

A working key returns ~10 dated articles and a non-`NEUTRAL` sentiment; without a
key it returns 2 demo articles.
