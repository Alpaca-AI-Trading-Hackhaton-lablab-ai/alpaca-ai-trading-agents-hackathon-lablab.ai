"""Redis cache keyed per agent. Required at FastAPI startup; mid-run misses
fall through to compute (never crash the pipeline).

Values are encoded with Pydantic TypeAdapter.dump_json (jiter/Rust), not
stdlib json. Flush uses UNLINK on the scanned key set.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from services.schemas import dump_json_bytes, load_json_bytes

load_dotenv()

PREFIX = "alc:cache"
TTL_NEWS = 600
TTL_INDICATORS = 60
TTL_ACCOUNT = 15

_client = None


def is_connected():
    return _client is not None


def connect(url=None, client=None):
    """Ping Redis (or an injected client, e.g. fakeredis in tests)."""
    global _client
    load_dotenv()
    if client is not None:
        client.ping()
        _client = client
        return _client
    url = (url or os.getenv("REDIS_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "REDIS_URL is required. Start Redis with `docker compose up -d` "
            "and set REDIS_URL (see .env.example; compose publishes Redis on 6380)."
        )
    import redis as redis_lib

    _client = redis_lib.Redis.from_url(url, decode_responses=False)
    _client.ping()
    return _client


def close():
    global _client
    _client = None


def key(agent, symbol, suffix=""):
    symbol = (symbol or "_").upper()
    extra = suffix or "_"
    return f"{PREFIX}:{agent}:{symbol}:{extra}"


def _as_str(name):
    if isinstance(name, bytes):
        return name.decode("utf-8")
    return name


def _encode(value, adapter=None):
    if adapter is None:
        return dump_json_bytes(value)
    return adapter.dump_json(adapter.validate_python(value))


def _decode(raw, adapter=None):
    if adapter is None:
        return load_json_bytes(raw)
    parsed = adapter.validate_json(raw)
    return parsed.model_dump() if hasattr(parsed, "model_dump") else parsed


def get(agent, symbol, suffix="", adapter=None):
    if _client is None:
        return None
    try:
        raw = _client.get(key(agent, symbol, suffix))
        if raw is None:
            return None
        return _decode(raw, adapter)
    except Exception:  # noqa: BLE001 - cache must never break the caller
        return None


def set(agent, symbol, suffix, value, ttl, adapter=None):
    if _client is None:
        return False
    try:
        _client.set(
            key(agent, symbol, suffix),
            _encode(value, adapter),
            ex=int(ttl),
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def cached(agent, symbol, suffix, ttl, fn, adapter=None):
    hit = get(agent, symbol, suffix, adapter=adapter)
    if hit is not None:
        return hit
    value = fn()
    set(agent, symbol, suffix, value, ttl, adapter=adapter)
    return value


def flush():
    """Drop every per-agent cache key with one UNLINK (fallback: DELETE)."""
    if _client is None:
        return 0
    try:
        names = [_as_str(name) for name in _client.scan_iter(match=f"{PREFIX}:*")]
        if not names:
            return 0
        unlink = getattr(_client, "unlink", None)
        if callable(unlink):
            unlink(*names)
        else:
            _client.delete(*names)
        return len(names)
    except Exception:  # noqa: BLE001
        return 0
