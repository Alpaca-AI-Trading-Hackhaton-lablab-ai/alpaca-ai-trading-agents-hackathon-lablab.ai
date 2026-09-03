"""Interval loop: UTC window, universe scan, conditionals, wind-down."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from services import config, db
from services.db import ACCOUNT_ID, ScheduleSettings

_in_flight = False
_focus = None
_last = {
    "nodes": [],
    "intents": [],
    "results": [],
    "focus": None,
    "error": None,
}


def _now():
    return datetime.now(timezone.utc)


def _aware(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_ts(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _aware(value)
    raw = str(value).strip().replace("Z", "+00:00")
    return _aware(datetime.fromisoformat(raw))


def _iso(value):
    value = _aware(value)
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _row():
    if not db.is_connected():
        return None
    with db.session() as session:
        row = session.get(ScheduleSettings, ACCOUNT_ID)
        if row is None:
            row = ScheduleSettings(
                id=ACCOUNT_ID,
                enabled=False,
                interval_seconds=int(config.DEFAULT_INTERVAL_S),
                max_credit=float(config.DEFAULT_MAX_CREDIT),
                universe=list(config.DEFAULT_UNIVERSE),
                end_action="stop_cancel_flatten",
                wound_down=False,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
        return row


def compute_state(row, now=None):
    now = _aware(now or _now())
    if row is None:
        return "disabled"
    start = _aware(row.window_start)
    end = _aware(row.window_end)
    if end and now >= end:
        return "ended"
    if not row.enabled:
        return "disabled"
    if config.is_kill():
        return "paused"
    if start and now < start:
        return "scheduled"
    return "running"


def is_within_window(now=None):
    now = _aware(now or _now())
    row = _row()
    if row is None:
        return True
    start = _aware(row.window_start)
    end = _aware(row.window_end)
    if start is None or end is None:
        return True
    return start <= now < end


def window_id():
    row = _row()
    if row is None or not row.window_start or not row.window_end:
        return "global"
    return f"{_iso(row.window_start)}|{_iso(row.window_end)}"


def _public(row):
    state = compute_state(row)
    return {
        "enabled": bool(row.enabled),
        "interval_seconds": int(row.interval_seconds or config.DEFAULT_INTERVAL_S),
        "max_credit": float(row.max_credit or config.DEFAULT_MAX_CREDIT),
        "universe": list(row.universe or list(config.DEFAULT_UNIVERSE)),
        "window_start": _iso(row.window_start),
        "window_end": _iso(row.window_end),
        "end_action": row.end_action or "stop_cancel_flatten",
        "wound_down": bool(row.wound_down),
        "last_run_ts": _iso(row.last_run_ts),
        "next_run_ts": _iso(row.next_run_ts),
        "in_flight": _in_flight,
        "state": state,
        "nodes": _last["nodes"],
        "intents": _last["intents"],
        "results": _last["results"],
        "focus": _last["focus"],
        "error": _last["error"],
    }


def get_public():
    row = _row()
    if row is None:
        return {
            "enabled": False,
            "interval_seconds": int(config.DEFAULT_INTERVAL_S),
            "max_credit": float(config.DEFAULT_MAX_CREDIT),
            "universe": list(config.DEFAULT_UNIVERSE),
            "window_start": None,
            "window_end": None,
            "end_action": "stop_cancel_flatten",
            "wound_down": False,
            "last_run_ts": None,
            "next_run_ts": None,
            "in_flight": False,
            "state": "disabled",
            "nodes": [],
            "intents": [],
            "results": [],
            "focus": None,
            "error": "database is not connected",
        }
    return _public(row)


def set_focus(symbol):
    global _focus
    _focus = (symbol or "").upper() or None


def max_credit():
    row = _row()
    if row is None:
        return float(config.DEFAULT_MAX_CREDIT)
    return float(row.max_credit or config.DEFAULT_MAX_CREDIT)


def _clamp_interval(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(config.DEFAULT_INTERVAL_S)
    minimum = int(config.MIN_INTERVAL_S)
    if n < minimum:
        raise ValueError(f"interval_seconds must be >= {minimum}")
    return n


def save(patch: dict):
    row = _row()
    if row is None:
        raise RuntimeError("database is not connected")
    with db.session() as session:
        row = session.get(ScheduleSettings, ACCOUNT_ID)
        if "enabled" in patch and patch["enabled"] is not None:
            row.enabled = bool(patch["enabled"])
        if patch.get("interval_seconds") is not None:
            row.interval_seconds = _clamp_interval(patch["interval_seconds"])
        if patch.get("max_credit") is not None:
            row.max_credit = max(0.0, float(patch["max_credit"]))
        if patch.get("universe") is not None:
            uni = [str(s).upper() for s in patch["universe"] if s]
            row.universe = uni or list(config.DEFAULT_UNIVERSE)
        if "window_start" in patch:
            row.window_start = _parse_ts(patch.get("window_start"))
        if "window_end" in patch:
            row.window_end = _parse_ts(patch.get("window_end"))
        if patch.get("end_action"):
            row.end_action = "stop_cancel_flatten"
        start = _aware(row.window_start)
        end = _aware(row.window_end)
        if start and end and end <= start:
            raise ValueError("window_end must be after window_start")
        windows_changed = "window_start" in patch or "window_end" in patch
        if windows_changed:
            row.wound_down = False
        turned_on = bool(patch.get("enabled") is True)
        now = _now()
        if row.enabled:
            if start and now < start:
                row.next_run_ts = start
            elif row.next_run_ts is None or turned_on:
                row.next_run_ts = now
        else:
            row.next_run_ts = None
        row.updated_at = now
        session.commit()
        session.refresh(row)
        return _public(row)


def start():
    row = _row()
    if row is None:
        raise RuntimeError("database is not connected")
    if not row.window_start or not row.window_end:
        raise ValueError("window_start and window_end required")
    return save({"enabled": True})


def stop():
    return save({"enabled": False})


def wind_down():
    """Stop ticking, cancel armed conditionals, flatten paper positions."""
    from services import conditional, logs
    from services.alpaca_service import flatten_positions

    row = _row()
    if row is None:
        return get_public()
    with db.session() as session:
        rec = session.get(ScheduleSettings, ACCOUNT_ID)
        if rec is None or rec.wound_down:
            return get_public()
        rec.wound_down = True
        rec.next_run_ts = None
        rec.updated_at = _now()
        session.commit()
    cancelled = conditional.cancel_armed()
    flat = flatten_positions()
    logs.record(
        symbol=None,
        agent_id="scheduler",
        kind="execute",
        status="ended",
        summary=f"wind-down cancel={cancelled}",
        payload={"flatten": flat, "cancelled": cancelled},
    )
    return get_public()


def _eval_conditionals(universe):
    from services import conditional
    from services.alpaca_service import get_spy_price

    for symbol in universe or []:
        try:
            quote = get_spy_price(symbol)
            price = (quote or {}).get("price")
            if price is not None:
                conditional.evaluate_triggers(symbol, price)
        except Exception:  # noqa: BLE001
            continue


def run_once(focus=None):
    global _in_flight
    from services import book, usage_meter

    row = _row()
    if row is None:
        return get_public()
    if config.is_kill():
        return get_public()
    if not is_within_window():
        return get_public()
    verdict = usage_meter.allow("groq")
    if verdict.get("state") == "OVER":
        _last["error"] = verdict.get("reason") or "budget exhausted"
        stop()
        return get_public()
    _in_flight = True
    _last["error"] = None
    try:
        usage_meter.apply_degrade()
        usage_meter.reconcile_tavily()
        universe = list(row.universe or list(config.DEFAULT_UNIVERSE))
        cap = usage_meter.universe_cap()
        if cap is not None:
            universe = universe[: max(1, cap)]
        out = book.run_tick(
            focus=focus or _focus,
            max_credit=row.max_credit,
            universe=universe,
        )
        _eval_conditionals(universe)
        _last["nodes"] = out.get("nodes") or []
        _last["intents"] = out.get("intents") or []
        _last["results"] = [
            {
                "status": r.get("status"),
                "symbol": (r.get("intent") or {}).get("symbol"),
                "action": (r.get("intent") or {}).get("action"),
                "reason": r.get("reason"),
                "would_call": r.get("would_call"),
            }
            for r in (out.get("results") or [])
        ]
        _last["focus"] = out.get("focus")
        interval = int(row.interval_seconds or config.DEFAULT_INTERVAL_S)
        interval = max(int(config.MIN_INTERVAL_S), interval * usage_meter.interval_boost())
        with db.session() as session:
            rec = session.get(ScheduleSettings, ACCOUNT_ID)
            rec.last_run_ts = _now()
            rec.next_run_ts = _now() + timedelta(seconds=interval)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        _last["error"] = str(exc)
    finally:
        _in_flight = False
    return get_public()


async def loop():
    while True:
        await asyncio.sleep(1)
        if _in_flight or not db.is_connected():
            continue
        row = _row()
        if row is None:
            continue
        state = compute_state(row)
        if state == "ended":
            if not row.wound_down:
                await asyncio.to_thread(wind_down)
            continue
        if not row.enabled:
            continue
        if state != "running":
            continue
        due = row.next_run_ts is None or _aware(row.next_run_ts) <= _now()
        if not due:
            continue
        await asyncio.to_thread(run_once)
