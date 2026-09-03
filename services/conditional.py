"""Postgres conditional-order motor. No new async loop — called from /spy and webhook."""

from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone

from agents.execution_gate import evaluate_gate
from services import db
from services.alpaca_service import (
    get_account_info,
    get_market_clock,
    get_open_orders,
    get_positions,
)
from services.bracket import execute_plan
from services.bracket_plan import validate_plan


def _now():
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _env_token():
    return (os.getenv("WEBHOOK_TOKEN") or "").strip() or None


def token_source(row) -> str:
    if getattr(row, "token_hash", None):
        return "db"
    if _env_token():
        return "env"
    return "missing"


def _public_trigger(row):
    trigger = dict(row.trigger or {})
    trigger.pop("token", None)
    if trigger.get("kind") == "webhook":
        trigger = {"kind": "webhook", "token_source": token_source(row)}
    return trigger


def to_public(row, include_token=None):
    out = {
        "id": row.id,
        "symbol": row.symbol,
        "status": row.status,
        "trigger": _public_trigger(row),
        "plan": row.plan,
        "created_ts": row.created_ts.isoformat() if row.created_ts else None,
        "triggered_ts": row.triggered_ts.isoformat() if row.triggered_ts else None,
    }
    if include_token:
        out["webhook_token"] = include_token
    return out


def list_orders(symbol=None):
    if not db.is_connected():
        return []
    with db.session() as session:
        q = session.query(db.ConditionalOrder)
        if symbol:
            q = q.filter(db.ConditionalOrder.symbol == str(symbol).upper())
        rows = q.order_by(db.ConditionalOrder.created_ts.desc()).all()
        return [to_public(r) for r in rows]


def get_order(oid):
    if not db.is_connected():
        return None
    with db.session() as session:
        row = session.get(db.ConditionalOrder, oid)
        return to_public(row) if row else None


def create_order(plan, trigger, account=None, positions=None, open_orders=None, clock=None):
    """Static gate only. Does not send to Alpaca."""
    v = validate_plan(plan)
    if not v["ok"]:
        return {"ok": False, "status": "BLOCKED", "errors": v["errors"], "gate": None}

    decision = {
        "action": "BUY" if (plan or {}).get("side") == "buy" else "SELL",
        "symbol": (plan or {}).get("symbol"),
        "position_size": ((plan or {}).get("size") or {}).get("notional") or 0,
    }
    gate = evaluate_gate(
        decision,
        account or get_account_info(),
        positions if positions is not None else get_positions().get("positions", []),
        open_orders if open_orders is not None else get_open_orders(decision["symbol"]),
        clock or get_market_clock(),
        plan=plan,
    )
    if gate["verdict"] == "BLOCK":
        return {"ok": False, "status": "BLOCKED", "errors": gate["reasons"], "gate": gate}

    trigger = dict(trigger or {})
    token = None
    token_hash = None
    if trigger.get("kind") == "webhook":
        token = secrets.token_urlsafe(24)
        token_hash = _hash_token(token)
        trigger = {"kind": "webhook", "token_source": "db"}

    if not db.is_connected():
        return {
            "ok": False,
            "status": "FAILED",
            "errors": ["database is not connected"],
            "gate": gate,
        }

    row = db.ConditionalOrder(
        id=str(uuid.uuid4()),
        symbol=str((plan or {}).get("symbol") or "").upper(),
        status="armed",
        trigger=trigger,
        plan=plan,
        token_hash=token_hash,
        created_ts=_now(),
    )
    with db.session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"ok": True, "order": to_public(row, include_token=token), "gate": gate}


def park_rows(rows):
    created = []
    if not db.is_connected():
        return created
    with db.session() as session:
        for item in rows or []:
            plan = item.get("plan") or {}
            row = db.ConditionalOrder(
                id=str(uuid.uuid4()),
                symbol=str(item.get("symbol") or plan.get("symbol") or "").upper(),
                status="armed",
                trigger=item.get("trigger") or {},
                plan=plan,
                created_ts=_now(),
            )
            session.add(row)
            created.append(row)
        session.commit()
        return [to_public(r) for r in created]


def cancel_order(oid):
    if not db.is_connected():
        return False
    with db.session() as session:
        row = session.get(db.ConditionalOrder, oid)
        if row is None:
            return False
        if row.status in ("armed", "working", "triggered"):
            row.status = "cancelled"
            session.commit()
            return True
        return False


def cancel_armed(symbol=None):
    """Kill switch: cancel armed/working (and leftover triggered)."""
    if not db.is_connected():
        return 0
    with db.session() as session:
        q = session.query(db.ConditionalOrder).filter(
            db.ConditionalOrder.status.in_(("armed", "working", "triggered"))
        )
        if symbol:
            q = q.filter(db.ConditionalOrder.symbol == str(symbol).upper())
        n = 0
        for row in q.all():
            row.status = "cancelled"
            n += 1
        session.commit()
        return n


def _price_hit(trigger, last_price):
    if not trigger or trigger.get("kind") != "price":
        return False
    try:
        target = float(trigger.get("price"))
        last = float(last_price)
    except (TypeError, ValueError):
        return False
    op = trigger.get("op")
    if op == ">=":
        return last >= target
    if op == "<=":
        return last <= target
    return False


def _fire(row, account, positions, open_orders, clock):
    row.status = "triggered"
    row.triggered_ts = _now()
    result = execute_plan(
        row.plan,
        account,
        positions,
        open_orders,
        clock,
        park_emulated=False,
    )
    status = result.get("status")
    if status == "be_moved":
        row.status = "be_moved"
    elif status in ("SUBMITTED", "FILLED", "ACCEPTED", "PARTIALLY_FILLED"):
        row.status = "working" if status != "FILLED" else "done"
    elif status in ("BLOCKED", "NO_TRADE"):
        row.status = "triggered"
    elif status == "DRY_RUN":
        row.status = "triggered"
    return result


def evaluate_triggers(symbol, last_price, account=None, positions=None, open_orders=None, clock=None):
    """Price motor. Re-gates and uses the same path as /bracket/execute."""
    if not db.is_connected() or last_price is None:
        return []
    fired = []
    with db.session() as session:
        rows = (
            session.query(db.ConditionalOrder)
            .filter(
                db.ConditionalOrder.symbol == str(symbol).upper(),
                db.ConditionalOrder.status == "armed",
            )
            .all()
        )
        if not rows:
            return []
        account = account or get_account_info()
        positions = positions if positions is not None else get_positions().get("positions", [])
        open_orders = open_orders if open_orders is not None else get_open_orders(symbol)
        clock = clock or get_market_clock()
        for row in rows:
            if not _price_hit(row.trigger, last_price):
                continue
            fired.append({"id": row.id, "result": _fire(row, account, positions, open_orders, clock)})
        session.commit()
    return fired


def fire_webhook(token):
    if not token:
        return {"ok": False, "fired": 0}
    digest = _hash_token(token)
    env = _env_token()
    env_ok = bool(env and secrets.compare_digest(token, env))
    if not db.is_connected():
        return {"ok": False, "fired": 0, "error": "database is not connected"}
    fired = []
    with db.session() as session:
        rows = (
            session.query(db.ConditionalOrder)
            .filter(db.ConditionalOrder.status == "armed")
            .all()
        )
        account = get_account_info()
        positions = get_positions().get("positions", [])
        clock = get_market_clock()
        for row in rows:
            trig = row.trigger or {}
            if trig.get("kind") != "webhook":
                continue
            match = row.token_hash == digest or (env_ok and not row.token_hash)
            if not match:
                continue
            open_orders = get_open_orders(row.symbol)
            fired.append(
                {"id": row.id, "result": _fire(row, account, positions, open_orders, clock)}
            )
        session.commit()
    return {"ok": True, "fired": len(fired), "orders": [f["id"] for f in fired]}
