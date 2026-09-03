"""Invocation logs in Postgres. Compact summaries for the LLM; never secrets,
raw OHLCV, or option chains.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from services import db
from services.db import InvocationLog

_SUMMARY_MAX = 400
_PAYLOAD_MAX = 2000
_DROP_KEYS = {
    "candles",
    "overlays",
    "oscillators",
    "volume",
    "results",
    "option_chain",
    "chain",
    "api_key",
    "secret",
    "secret_key",
    "token",
    "password",
    "authorization",
    "groq_api_key",
    "tavily_api_key",
    "alpaca_api_key",
    "alpaca_secret_key",
}


def _now():
    return datetime.now(timezone.utc)


def compact_payload(value, max_chars=_PAYLOAD_MAX):
    if value is None:
        return None
    if isinstance(value, list):
        value = value[:8]
    if isinstance(value, dict):
        value = {
            k: v
            for k, v in value.items()
            if str(k).lower() not in _DROP_KEYS
            and not any(part in str(k).lower() for part in ("secret", "api_key", "token"))
        }
    try:
        raw = json.dumps(value, default=str)
    except TypeError:
        raw = str(value)
    if len(raw) > max_chars:
        raw = raw[: max_chars - 1] + "…"
        try:
            return json.loads(raw[: raw.rfind("}") + 1] or "{}")
        except Exception:  # noqa: BLE001
            return {"truncated": raw[:200]}
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"text": raw[:200]}


def _summary(text, fallback=""):
    text = (text or fallback or "").strip().replace("\n", " ")
    if len(text) > _SUMMARY_MAX:
        return text[: _SUMMARY_MAX - 1] + "…"
    return text


_buffers: dict[str, list] = {}


def _row_kwargs(
    *,
    run_id=None,
    symbol=None,
    agent_id,
    kind,
    model=None,
    latency_ms=None,
    status=None,
    summary=None,
    payload=None,
    prompt_tokens=None,
    completion_tokens=None,
    total_tokens=None,
    credits=None,
    est_cost_usd=None,
):
    return {
        "run_id": run_id,
        "ts": _now(),
        "symbol": (symbol or None) and str(symbol).upper(),
        "agent_id": str(agent_id)[:32],
        "kind": str(kind)[:16],
        "model": (model or None) and str(model)[:80],
        "latency_ms": int(latency_ms) if latency_ms is not None else None,
        "status": (status or None) and str(status)[:32],
        "summary": _summary(summary),
        "payload": compact_payload(payload),
        "prompt_tokens": int(prompt_tokens) if prompt_tokens is not None else None,
        "completion_tokens": int(completion_tokens) if completion_tokens is not None else None,
        "total_tokens": int(total_tokens) if total_tokens is not None else None,
        "credits": int(credits) if credits is not None else None,
        "est_cost_usd": float(est_cost_usd) if est_cost_usd is not None else None,
    }


def _commit_rows(kwargs_list):
    if not db.is_connected() or not kwargs_list:
        return []
    rows = [InvocationLog(**kw) for kw in kwargs_list]
    with db.session() as session:
        session.add_all(rows)
        session.commit()
        return [_to_dict(row) for row in rows]


def record(
    *,
    run_id=None,
    symbol=None,
    agent_id,
    kind,
    model=None,
    latency_ms=None,
    status=None,
    summary=None,
    payload=None,
    prompt_tokens=None,
    completion_tokens=None,
    total_tokens=None,
    credits=None,
    est_cost_usd=None,
):
    """Buffer when `run_id` is set (flushed once per pipeline). Immediate
    commit for control/execute rows that have no run."""
    kw = _row_kwargs(
        run_id=run_id,
        symbol=symbol,
        agent_id=agent_id,
        kind=kind,
        model=model,
        latency_ms=latency_ms,
        status=status,
        summary=summary,
        payload=payload,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        credits=credits,
        est_cost_usd=est_cost_usd,
    )
    if run_id:
        _buffers.setdefault(str(run_id), []).append(kw)
        return None
    try:
        written = _commit_rows([kw])
        return written[0] if written else None
    except Exception:  # noqa: BLE001 - logging must never break the pipeline
        return None


def flush_run(run_id):
    """One commit for every buffered row of this pipeline run, plus a
    per-agent run_summary (no full payload)."""
    items = _buffers.pop(str(run_id), []) if run_id else []
    if not items:
        return []
    by_agent: dict[str, list] = {}
    for kw in items:
        by_agent.setdefault(kw["agent_id"], []).append(kw)
    extra = []
    for agent_id, rows in by_agent.items():
        last = rows[-1]
        extra.append(
            _row_kwargs(
                run_id=run_id,
                symbol=last.get("symbol"),
                agent_id=agent_id,
                kind="run_summary",
                model=last.get("model"),
                status=last.get("status"),
                summary=last.get("summary"),
                payload={"rows": len(rows)},
            )
        )
    try:
        return _commit_rows(items + extra)
    except Exception:  # noqa: BLE001
        return []


def _to_dict(row: InvocationLog):
    return {
        "id": row.id,
        "ts": row.ts.isoformat() if row.ts else None,
        "run_id": row.run_id,
        "symbol": row.symbol,
        "agent_id": row.agent_id,
        "kind": row.kind,
        "model": row.model,
        "latency_ms": row.latency_ms,
        "status": row.status,
        "summary": row.summary,
        "payload": row.payload,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.total_tokens,
        "credits": row.credits,
        "est_cost_usd": float(row.est_cost_usd) if row.est_cost_usd is not None else None,
    }


def query_logs(symbol=None, agent=None, limit=50):
    if not db.is_connected():
        return []
    limit = max(1, min(int(limit or 50), 200))
    with db.session() as session:
        q = session.query(InvocationLog).order_by(InvocationLog.id.desc())
        if symbol:
            q = q.filter(InvocationLog.symbol == str(symbol).upper())
        if agent:
            q = q.filter(InvocationLog.agent_id == agent)
        rows = q.limit(limit).all()
    return [_to_dict(r) for r in rows]


def recent_for_agents(symbol, limit=10, agent_id=None):
    """Compact history snapshot for sentiment/decision prompts and the
    recent_history tool. Prefers node/gate/execute rows over raw llm dumps."""
    if agent_id:
        return history_for_agent(symbol, agent_id, limit=limit)
    if not db.is_connected():
        return []
    limit = max(1, min(int(limit or 10), 20))
    symbol = (symbol or "SPY").upper()
    with db.session() as session:
        q = (
            session.query(InvocationLog)
            .filter(InvocationLog.symbol == symbol)
            .filter(
                InvocationLog.kind.in_(
                    ("run", "gate", "execute", "tool", "run_summary")
                )
            )
            .order_by(InvocationLog.id.desc())
            .limit(limit)
        )
        rows = q.all()
    return [_to_dict(r) for r in rows]


def history_for_agent(symbol, agent_id, limit=20, full_runs=None):
    """Last `full_runs` pipeline runs in detail; older runs as run_summary only."""
    from services import config as _config

    if not db.is_connected():
        return []
    full_runs = int(full_runs if full_runs is not None else _config.HISTORY_FULL_RUNS)
    limit = max(1, min(int(limit or 20), 50))
    symbol = (symbol or "SPY").upper()
    agent_id = str(agent_id)
    with db.session() as session:
        q = (
            session.query(InvocationLog)
            .filter(InvocationLog.symbol == symbol)
            .filter(InvocationLog.agent_id == agent_id)
            .filter(InvocationLog.kind != "llm")
            .order_by(InvocationLog.id.desc())
            .limit(200)
        )
        rows = q.all()
    run_ids = []
    for row in rows:
        rid = row.run_id
        if rid and rid not in run_ids:
            run_ids.append(rid)
    keep_full = set(run_ids[:full_runs])
    out = []
    for row in rows:
        if row.run_id in keep_full:
            if row.kind != "run_summary":
                out.append(_to_dict(row))
        elif row.kind == "run_summary":
            out.append(_to_dict(row))
        elif row.run_id is None:
            out.append(_to_dict(row))
        if len(out) >= limit:
            break
    return out


def history_text(symbol, limit=10, agent_id=None):
    if agent_id:
        rows = history_for_agent(symbol, agent_id, limit=limit)
        label = f"Recent {agent_id} history for this symbol (older runs summarized):\n"
    else:
        rows = recent_for_agents(symbol, limit=limit)
        label = "Recent invocations for this symbol:\n"
    if not rows:
        return ""
    lines = []
    for row in reversed(rows):
        bits = [row.get("ts") or "", row.get("agent_id") or "", row.get("kind") or ""]
        if row.get("status"):
            bits.append(row["status"])
        if row.get("summary"):
            bits.append(row["summary"])
        lines.append("- " + " · ".join(b for b in bits if b))
    return label + "\n".join(lines)


def audit_entries(limit=20):
    """Shape expected by GET /audit (execute/gate/control)."""
    if not db.is_connected():
        return []
    limit = max(1, min(int(limit or 20), 100))
    with db.session() as session:
        q = (
            session.query(InvocationLog)
            .filter(InvocationLog.kind.in_(("execute", "gate")))
            .order_by(InvocationLog.id.desc())
            .limit(limit)
        )
        rows = q.all()
    out = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        out.append(
            {
                "ts": row.ts.isoformat() if row.ts else None,
                "symbol": row.symbol,
                "action": payload.get("action") or row.summary,
                "verdict": payload.get("verdict"),
                "status": row.status,
                "notional": payload.get("notional"),
                "order_id": payload.get("order_id"),
                "reasons": payload.get("reasons"),
            }
        )
    return out
