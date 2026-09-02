# DuckDuckGo concept lookup

How the **official Instant Answer API** is used as a read-only ReAct tool when a
proposal-side agent does not know a term, entity, or indicator.

This is **not** a news source. Headlines stay on Tavily
(`docs/tavily-news-integration.md`). DuckDuckGo Instant Answer returns
Wikipedia-style abstracts, definitions, and related topics — no ranked SERP,
no API key.

## Role

Wired as `lookup_concept` in `agents/research_tools.py`. Deep-mode sentiment and
decision loops may call it; the gate and executor never see it.

```
{"tool": "lookup_concept", "params": {"query": "relative strength index"}}
```

## Endpoint

`services/concept_lookup.py` calls:

```
GET https://api.duckduckgo.com/?q=...&format=json&no_html=1&skip_disambig=1&t=tradelix
```

No key. Fail-closed: network/parse errors return `{found: false}` and the loop
continues. Results are cached in Redis (`TTL_CONCEPT`, 1 hour). HTML search
pages and unofficial `ddgs` scrapers are not used.

## Output shape

```json
{
  "query": "relative strength index",
  "found": true,
  "heading": "Relative strength index",
  "text": "A momentum oscillator …",
  "source": "Wikipedia",
  "url": "https://en.wikipedia.org/wiki/Relative_strength_index",
  "related": [{"text": "…", "url": "https://…"}]
}
```

Empty Instant Answers (most ticker-news queries) come back `found: false`. The
prompt tells the model to use `get_market_news` for headlines.
