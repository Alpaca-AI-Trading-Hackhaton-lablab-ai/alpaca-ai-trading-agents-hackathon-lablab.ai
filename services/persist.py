"""Account + per-agent settings persisted in Postgres.

GET never returns API key values. PUT empty string clears a DB key so env wins.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.indicator_engine import parse_indicators
from services import config, db, secrets
from services.db import ACCOUNT_ID, AccountSettings, AgentSettings, EDITABLE_AGENTS

_KEY_FIELDS = {
    "groq": "groq_api_key",
    "tavily": "tavily_api_key",
    "alpaca_api_key": "alpaca_api_key",
    "alpaca_secret_key": "alpaca_secret_key",
}

_DEFAULT_INDICATORS = parse_indicators(None)


def _now():
    return datetime.now(timezone.utc)


def _agent_defaults():
    models = config.resolve_models()
    return {
        "sentiment": {
            "model": models["sentiment"],
            "deep": bool(config.DEEP_RESEARCH_DEFAULT),
        },
        "decision": {
            "model": models["decision"],
            "deep": bool(config.DEEP_RESEARCH_DEFAULT),
            "indicators": list(_DEFAULT_INDICATORS),
        },
        "technical": {"indicators": list(_DEFAULT_INDICATORS)},
        "features": {"indicators": list(_DEFAULT_INDICATORS)},
    }


def _row_to_agent(row: AgentSettings, defaults: dict):
    base = dict(defaults)
    if row.model:
        base["model"] = row.model
    if "deep" in base:
        base["deep"] = bool(row.deep)
    if row.indicators:
        base["indicators"] = parse_indicators(",".join(row.indicators))
    return base


def load_agents():
    defaults = _agent_defaults()
    if not db.is_connected():
        return defaults
    with db.session() as session:
        rows = {r.agent_id: r for r in session.query(AgentSettings).all()}
    out = {}
    for agent_id in EDITABLE_AGENTS:
        out[agent_id] = (
            _row_to_agent(rows[agent_id], defaults[agent_id])
            if agent_id in rows
            else defaults[agent_id]
        )
    return out


def public_view():
    """One session: account row + agent rows. Primes the secrets memory map."""
    defaults = _agent_defaults()
    if not db.is_connected():
        return {"keys": secrets.sources(), "agents": defaults}
    with db.session() as session:
        account = session.get(AccountSettings, ACCOUNT_ID)
        rows = {r.agent_id: r for r in session.query(AgentSettings).all()}
    secrets.prime_from_row(account)
    agents = {}
    for agent_id in EDITABLE_AGENTS:
        agents[agent_id] = (
            _row_to_agent(rows[agent_id], defaults[agent_id])
            if agent_id in rows
            else defaults[agent_id]
        )
    return {"keys": secrets.sources_from_row(account), "agents": agents}


def merge_pipeline_opts(
    sentiment_model=None,
    decision_model=None,
    indicators=None,
    decision_indicators=None,
    deep_sentiment=None,
    deep_decision=None,
    deep=None,
):
    """Request query wins; missing fields fill from persisted agent_settings."""
    stored = load_agents()
    sent = sentiment_model or stored["sentiment"].get("model")
    dec = decision_model or stored["decision"].get("model")
    inds = indicators or ",".join(stored["technical"].get("indicators") or [])
    dinds = decision_indicators or ",".join(
        stored["decision"].get("indicators") or []
    )
    if deep_sentiment is None:
        deep_sentiment = True if deep else bool(stored["sentiment"].get("deep"))
    if deep_decision is None:
        deep_decision = True if deep else bool(stored["decision"].get("deep"))
    from services import usage_meter

    if usage_meter.deep_forced_off():
        deep_sentiment = False
        deep_decision = False
    cheap = usage_meter.cheap_model_id()
    if cheap:
        sent = cheap
        dec = cheap
    return {
        "sentiment_model": sent,
        "decision_model": dec,
        "indicators": inds,
        "decision_indicators": dinds,
        "deep_sentiment": deep_sentiment,
        "deep_decision": deep_decision,
    }


def _upsert_agent(session, agent_id, body):
    if agent_id not in EDITABLE_AGENTS:
        return
    row = session.get(AgentSettings, agent_id)
    if row is None:
        row = AgentSettings(agent_id=agent_id, deep=False)
        session.add(row)
    if "model" in body and body["model"] is not None:
        raw = str(body["model"]).strip()
        if agent_id in ("sentiment", "decision"):
            if raw:
                role = agent_id
                kwargs = {f"{role}_model": raw}
                resolved = config.resolve_models(**kwargs)
                row.model = resolved[role]
            else:
                row.model = None
        else:
            row.model = None
    if "deep" in body and agent_id in ("sentiment", "decision"):
        row.deep = bool(body["deep"])
    if "indicators" in body and agent_id in ("technical", "features", "decision"):
        raw = body["indicators"]
        if isinstance(raw, list):
            raw = ",".join(str(x) for x in raw)
        row.indicators = parse_indicators(raw)
    row.updated_at = _now()


def update(payload):
    """Apply a PUT /settings body. Returns the public view.
    Returns which key names were written (for cache flush)."""
    payload = payload or {}
    keys_body = payload.get("keys") or {}
    agents_body = payload.get("agents") or {}
    changed_keys = []
    with db.session() as session:
        account = session.get(AccountSettings, ACCOUNT_ID)
        if account is None:
            account = AccountSettings(id=ACCOUNT_ID)
            session.add(account)
        for logical, column in _KEY_FIELDS.items():
            if logical not in keys_body:
                continue
            raw = keys_body[logical]
            if raw is None:
                continue
            text = str(raw).strip()
            setattr(account, column, text or None)
            changed_keys.append(logical)
        account.updated_at = _now()
        if isinstance(agents_body, dict):
            for agent_id, body in agents_body.items():
                if isinstance(body, dict):
                    _upsert_agent(session, agent_id, body)
        session.commit()
    secrets.invalidate()
    return public_view(), changed_keys
