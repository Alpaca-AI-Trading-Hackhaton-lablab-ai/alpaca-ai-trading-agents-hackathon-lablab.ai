"""Deterministic API usage meter + budget guard (block + degrade)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from services import cache, config, db
from services.db import ApiBudget, ApiUsage

PROVIDERS = ("groq", "tavily", "alpaca", "ddg")
CHEAP_MODEL = config.GROQ_CHEAP_MODEL

_degrade_level = 0
_ttl_boost = 1
_universe_cap = None
_interval_boost = 1
_deep_off = False
_cheap_model = False


def _now():
    return datetime.now(timezone.utc)


def _window_id():
    try:
        from services import scheduler

        return scheduler.window_id()
    except Exception:  # noqa: BLE001
        return "global"


def reset_runtime():
    """Tests: clear in-process degrade state."""
    global _degrade_level, _ttl_boost, _universe_cap, _interval_boost
    global _deep_off, _cheap_model
    _degrade_level = 0
    _ttl_boost = 1
    _universe_cap = None
    _interval_boost = 1
    _deep_off = False
    _cheap_model = False
    cache.set_ttl_multiplier(1)


def deep_forced_off():
    return bool(_deep_off)


def cheap_model_id():
    return CHEAP_MODEL if _cheap_model else None


def universe_cap():
    return _universe_cap


def interval_boost():
    return max(1, int(_interval_boost))


def degrade_level():
    return int(_degrade_level)


def _est_cost(model, prompt_tokens, completion_tokens):
    prices = config.GROQ_PRICE_PER_1M.get(model) or {"prompt": 0.0, "completion": 0.0}
    return (prompt_tokens / 1_000_000) * float(prices.get("prompt") or 0) + (
        completion_tokens / 1_000_000
    ) * float(prices.get("completion") or 0)


def _redis_key(provider, window, field):
    return f"alc:usage:{window}:{provider}:{field}"


def _bump_redis(provider, window, requests, tokens, credits, cost):
    if not cache.is_connected():
        return
    client = cache._client
    try:
        pipe = client.pipeline()
        pipe.incrby(_redis_key(provider, window, "requests"), int(requests))
        pipe.incrby(_redis_key(provider, window, "tokens"), int(tokens))
        pipe.incrby(_redis_key(provider, window, "credits"), int(credits))
        if cost:
            pipe.incrbyfloat(_redis_key(provider, window, "cost"), float(cost))
        pipe.execute()
    except Exception:  # noqa: BLE001
        return


def record(
    provider,
    *,
    requests=0,
    tokens=0,
    credits=0,
    est_cost_usd=0.0,
    remaining=None,
    reset_ts=None,
    model=None,
    run_id=None,
    window_id=None,
    prompt_tokens=None,
    completion_tokens=None,
):
    provider = str(provider or "").lower()
    if provider not in PROVIDERS:
        return
    window = window_id or _window_id()
    tokens = int(tokens or 0)
    if prompt_tokens is not None or completion_tokens is not None:
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        if not tokens:
            tokens = prompt_tokens + completion_tokens
        if not est_cost_usd and model:
            est_cost_usd = _est_cost(model, prompt_tokens, completion_tokens)
    requests = int(requests or 0)
    credits = int(credits or 0)
    est_cost_usd = float(est_cost_usd or 0)
    _bump_redis(provider, window, requests, tokens, credits, est_cost_usd)
    if not db.is_connected():
        return
    try:
        with db.session() as session:
            row = (
                session.query(ApiUsage)
                .filter(ApiUsage.provider == provider, ApiUsage.window_id == window)
                .one_or_none()
            )
            if row is None:
                row = ApiUsage(
                    provider=provider,
                    window_id=window,
                    requests=0,
                    tokens=0,
                    credits=0,
                    est_cost_usd=0,
                )
                session.add(row)
            row.requests = int(row.requests or 0) + requests
            row.tokens = int(row.tokens or 0) + tokens
            row.credits = int(row.credits or 0) + credits
            row.est_cost_usd = float(row.est_cost_usd or 0) + est_cost_usd
            if remaining is not None:
                row.remaining_reported = int(remaining)
            if reset_ts is not None:
                if isinstance(reset_ts, (int, float)):
                    row.reset_ts = datetime.fromtimestamp(float(reset_ts), tz=timezone.utc)
                elif isinstance(reset_ts, datetime):
                    row.reset_ts = reset_ts
            row.updated_at = _now()
            session.commit()
    except Exception:  # noqa: BLE001
        return
    _ = run_id  # reserved for invocation_logs join


def capture_groq(msg, model=None, run_id=None):
    """Read LangChain AIMessage usage + Groq rate-limit headers."""
    usage = getattr(msg, "usage_metadata", None) or {}
    meta = getattr(msg, "response_metadata", None) or {}
    token_usage = meta.get("token_usage") or {}
    prompt = int(usage.get("input_tokens") or token_usage.get("prompt_tokens") or 0)
    completion = int(
        usage.get("output_tokens") or token_usage.get("completion_tokens") or 0
    )
    total = int(usage.get("total_tokens") or token_usage.get("total_tokens") or 0)
    if not total:
        total = prompt + completion
    remaining = None
    reset_ts = None
    headers = meta.get("headers") if isinstance(meta.get("headers"), dict) else meta
    if isinstance(headers, dict):
        raw_rem = headers.get("x-ratelimit-remaining-tokens") or headers.get(
            "x-ratelimit-remaining-requests"
        )
        try:
            remaining = int(str(raw_rem).split(".")[0]) if raw_rem is not None else None
        except (TypeError, ValueError):
            remaining = None
        raw_reset = headers.get("x-ratelimit-reset-tokens") or headers.get(
            "retry-after"
        )
        if raw_reset is not None:
            try:
                reset_ts = time.time() + float(str(raw_reset).replace("s", "").replace("ms", ""))
            except (TypeError, ValueError):
                reset_ts = None
    record(
        "groq",
        requests=1,
        tokens=total,
        prompt_tokens=prompt,
        completion_tokens=completion,
        remaining=remaining,
        reset_ts=reset_ts,
        model=model,
        run_id=run_id,
    )
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def load_budgets():
    if not db.is_connected():
        return []
    with db.session() as session:
        rows = session.query(ApiBudget).order_by(ApiBudget.provider).all()
        return [
            {
                "provider": r.provider,
                "scope": r.scope or "window",
                "limit_type": r.limit_type,
                "limit_value": float(r.limit_value or 0),
                "warn_pct": int(r.warn_pct or 80),
                "action": r.action or "block_degrade",
            }
            for r in rows
        ]


def save_budgets(items):
    if not db.is_connected():
        raise RuntimeError("database is not connected")
    with db.session() as session:
        session.query(ApiBudget).delete()
        for item in items or []:
            session.add(
                ApiBudget(
                    provider=str(item.get("provider") or ""),
                    scope=str(item.get("scope") or "window"),
                    limit_type=str(item.get("limit_type") or "tokens"),
                    limit_value=float(item.get("limit_value") or 0),
                    warn_pct=int(item.get("warn_pct") or 80),
                    action=str(item.get("action") or "block_degrade"),
                    updated_at=_now(),
                )
            )
        session.commit()
    return load_budgets()


def _used_for(row, limit_type):
    if limit_type == "tokens":
        return int(row.tokens or 0)
    if limit_type == "credits":
        return int(row.credits or 0)
    if limit_type == "requests":
        return int(row.requests or 0)
    if limit_type == "cost":
        return float(row.est_cost_usd or 0)
    return 0


def _usage_row(provider, window):
    if not db.is_connected():
        return None
    with db.session() as session:
        return (
            session.query(ApiUsage)
            .filter(ApiUsage.provider == provider, ApiUsage.window_id == window)
            .one_or_none()
        )


def snapshot(provider=None):
    window = _window_id()
    budgets = load_budgets()
    by_provider = {}
    for prov in PROVIDERS if provider is None else (provider,):
        row = _usage_row(prov, window)
        budget = next((b for b in budgets if b["provider"] == prov), None)
        used = 0
        limit = None
        remaining = None
        state = "OK"
        if row is not None:
            if budget:
                used = _used_for(row, budget["limit_type"])
            else:
                used = int(row.tokens or 0) or int(row.requests or 0) or int(row.credits or 0)
            remaining = row.remaining_reported
        if budget:
            limit = float(budget["limit_value"])
            warn = max(1, int(budget["warn_pct"] or 80)) / 100.0
            if limit > 0:
                ratio = used / limit
                if ratio >= 1:
                    state = "OVER"
                elif ratio >= warn:
                    state = "WARN"
            remaining = max(0, limit - used) if remaining is None else remaining
        by_provider[prov] = {
            "provider": prov,
            "used": used,
            "remaining": remaining,
            "limit": limit,
            "credits": int(row.credits or 0) if row else 0,
            "est_cost_usd": float(row.est_cost_usd or 0) if row else 0.0,
            "reset_ts": row.reset_ts.isoformat() if row and row.reset_ts else None,
            "state": state,
            "degrade_level": _degrade_level,
            "window_id": window,
        }
    if provider:
        return by_provider[provider]
    return list(by_provider.values())


def apply_degrade(state=None):
    """Climb the degrade ladder. Idempotent."""
    global _degrade_level, _ttl_boost, _universe_cap, _interval_boost
    global _deep_off, _cheap_model
    if state is None:
        snaps = snapshot()
        if any(s["state"] == "OVER" for s in snaps):
            state = "OVER"
        elif any(s["state"] == "WARN" for s in snaps):
            state = "WARN"
        else:
            state = "OK"
    if state == "OK":
        return _degrade_level
    _deep_off = True
    _degrade_level = max(_degrade_level, 1)
    if state == "OVER" or _degrade_level >= 1:
        _cheap_model = True
        _degrade_level = max(_degrade_level, 2)
    if state == "OVER" or _degrade_level >= 2:
        _ttl_boost = 4
        cache.set_ttl_multiplier(_ttl_boost)
        _universe_cap = 2
        _degrade_level = max(_degrade_level, 3)
    if state == "OVER":
        _interval_boost = 2
        _degrade_level = 5
    return _degrade_level


def allow(provider="groq", est_cost=0):
    """Pre-call verdict. OVER on groq/alpaca is a hard block."""
    snap = snapshot(provider)
    used = float(snap.get("used") or 0) + float(est_cost or 0)
    limit = snap.get("limit")
    state = snap.get("state") or "OK"
    if limit is not None and limit > 0 and used / float(limit) >= 1:
        state = "OVER"
    elif limit is not None and limit > 0:
        budget = next((b for b in load_budgets() if b["provider"] == provider), None)
        warn = max(1, int((budget or {}).get("warn_pct") or 80)) / 100.0
        if used / float(limit) >= warn:
            state = "WARN"
    if state in ("WARN", "OVER"):
        apply_degrade(state)
    ok = state != "OVER"
    return {
        "ok": ok,
        "action": "block" if state == "OVER" else "degrade" if state == "WARN" else "ok",
        "reason": "budget exhausted" if state == "OVER" else ("budget warn" if state == "WARN" else "ok"),
        "state": state,
        "degrade_level": _degrade_level,
    }


def budget_ok():
    """Hard check for the execution gate (Groq / Alpaca OVER)."""
    for provider in ("groq", "alpaca"):
        snap = snapshot(provider)
        if snap.get("state") == "OVER":
            return False, provider
    return True, None


def reconcile_tavily():
    """Authoritative remaining from Tavily GET /usage. Fail closed to local count."""
    from services import secrets

    key = secrets.tavily_api_key()
    if not key:
        return None
    try:
        import urllib.request

        req = urllib.request.Request(
            "https://api.tavily.com/usage",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            import json

            body = json.loads(resp.read().decode("utf-8"))
        account = body.get("account") or {}
        used = int(account.get("usage") or 0)
        limit = account.get("plan_limit")
        remaining = None
        if limit is not None:
            remaining = max(0, int(limit) - used)
        record("tavily", remaining=remaining)
        return body
    except Exception:  # noqa: BLE001
        return None
