"""DuckDuckGo Instant Answer — concept lookup for ReAct loops.

Official, free, keyless Instant Answer API (not a news/SERP API). Agents use
this when they do not know a term, entity, or indicator. Headlines stay on
Tavily. HTML scrapers / `ddgs` are not used.

https://api.duckduckgo.com/?q=...&format=json
"""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

from services import cache

_ENDPOINT = "https://api.duckduckgo.com/"
_TIMEOUT_S = 5
_RELATED_CAP = 5
_QUERY_CAP = 200
_UA = "Tradelix/1.0 (concept-lookup)"


def _empty(query, reason="no instant answer"):
    return {
        "query": query,
        "found": False,
        "heading": "",
        "text": "",
        "source": "",
        "url": "",
        "related": [],
        "reason": reason,
    }


def _flatten_topics(topics, out, limit=_RELATED_CAP):
    for item in topics or []:
        if len(out) >= limit:
            return
        if not isinstance(item, dict):
            continue
        nested = item.get("Topics")
        if nested:
            _flatten_topics(nested, out, limit)
            continue
        text = (item.get("Text") or "").strip()
        if not text:
            continue
        out.append({"text": text[:240], "url": item.get("FirstURL") or ""})


def _normalize(query, payload):
    if not isinstance(payload, dict):
        return _empty(query)
    text = (
        (payload.get("AbstractText") or "").strip()
        or (payload.get("Definition") or "").strip()
        or (payload.get("Answer") or "").strip()
    )
    related = []
    _flatten_topics(payload.get("RelatedTopics"), related)
    if not text and not related:
        return _empty(query)
    return {
        "query": query,
        "found": True,
        "heading": (payload.get("Heading") or "").strip(),
        "text": text,
        "source": (
            payload.get("AbstractSource")
            or payload.get("DefinitionSource")
            or ""
        ).strip(),
        "url": (
            payload.get("AbstractURL") or payload.get("DefinitionURL") or ""
        ).strip(),
        "related": related,
    }


def _fetch(query):
    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
            "t": "tradelix",
        }
    )
    req = urllib.request.Request(
        f"{_ENDPOINT}?{params}",
        headers={"User-Agent": _UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
        from services import usage_meter

        usage_meter.record("ddg", requests=1)
        payload = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return _empty(query, "lookup unavailable")
    except Exception:  # noqa: BLE001 - fail-closed; never crash the ReAct loop
        return _empty(query, "lookup unavailable")
    return _normalize(query, payload)


def lookup_concept(query="", **_k):
    """Read-only Instant Answer for a concept the agent does not know.

    Extra kwargs from the LLM tool-call are ignored. Empty / failed lookups
    return found=False — never raise.
    """
    query = " ".join(str(query or "").split())[:_QUERY_CAP]
    if not query:
        return _empty("", "empty query")
    suffix = hashlib.sha256(query.lower().encode("utf-8")).hexdigest()[:16]
    return cache.cached(
        "concept",
        "_",
        suffix,
        cache.TTL_CONCEPT,
        lambda: _fetch(query),
    )
