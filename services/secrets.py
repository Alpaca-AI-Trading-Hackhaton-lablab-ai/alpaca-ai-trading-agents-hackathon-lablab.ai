"""Resolve API keys: non-empty DB value, else .env, else missing.

ALPACA_PAPER_TRADE is never stored in the database — paper-only stays env.
GET /settings uses `source()` and must never return the secret itself.

Resolved values live in process memory after the first SELECT. Call
`invalidate()` after a PUT that changes keys.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from services import db
from services.db import ACCOUNT_ID, AccountSettings

load_dotenv()

# logical name -> (db column, env var)
_KEYS = {
    "groq": ("groq_api_key", "GROQ_API_KEY"),
    "tavily": ("tavily_api_key", "TAVILY_API_KEY"),
    "alpaca_api_key": ("alpaca_api_key", "ALPACA_API_KEY"),
    "alpaca_secret_key": ("alpaca_secret_key", "ALPACA_SECRET_KEY"),
}

# name -> (source, value). None means "not loaded".
_cache = None


def _strip(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def invalidate():
    """Drop the in-memory map so the next read hits Postgres (or env)."""
    global _cache
    _cache = None


def _from_row(row):
    out = {}
    for name, (column, env_name) in _KEYS.items():
        db_val = _strip(getattr(row, column, None) if row is not None else None)
        if db_val:
            out[name] = ("db", db_val)
            continue
        env_val = _strip(os.getenv(env_name))
        if env_val:
            out[name] = ("env", env_val)
        else:
            out[name] = ("missing", None)
    return out


def _account_row():
    if not db.is_connected():
        return None
    try:
        with db.session() as session:
            return session.get(AccountSettings, ACCOUNT_ID)
    except Exception:  # noqa: BLE001 - resolver must fail open to env
        return None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    _cache = _from_row(_account_row())
    return _cache


def prime_from_row(row):
    """Fill the memory map from a row already loaded (GET /settings)."""
    global _cache
    _cache = _from_row(row)


def sources_from_row(row):
    return {name: src for name, (src, _val) in _from_row(row).items()}


def get_secret(name):
    """Return the resolved secret or None. Unknown names raise KeyError."""
    if name not in _KEYS:
        raise KeyError(name)
    _src, value = _load()[name]
    return value


def source(name):
    """Where the current value comes from: db | env | missing."""
    if name not in _KEYS:
        raise KeyError(name)
    src, _value = _load()[name]
    return src


def sources():
    return {name: src for name, (src, _val) in _load().items()}


def groq_api_key():
    return get_secret("groq")


def tavily_api_key():
    return get_secret("tavily")


def alpaca_api_key():
    return get_secret("alpaca_api_key")


def alpaca_secret_key():
    return get_secret("alpaca_secret_key")


def has_alpaca_credentials():
    return bool(alpaca_api_key() and alpaca_secret_key())
