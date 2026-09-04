"""Paper trade_updates listener. Activates parked emulated rows on parent fill.

No credentials → no-op. Price motor in /spy and the scheduler stays as fallback.
"""

from __future__ import annotations

import logging
import threading

from services import secrets
from services.conditional import fire_parent

log = logging.getLogger(__name__)

_FILL_EVENTS = {"fill", "partial_fill"}

_stream = None
_thread = None


def _event_name(data):
    ev = getattr(data, "event", None)
    if ev is None and isinstance(data, dict):
        ev = data.get("event")
    return str(ev or "").split(".")[-1].lower().replace("tradeevent", "")


def _order_ids(data):
    order = getattr(data, "order", None)
    if order is None and isinstance(data, dict):
        order = data.get("order") or data
    if order is None:
        return None, None
    oid = getattr(order, "id", None) or getattr(order, "order_id", None)
    if oid is None and isinstance(order, dict):
        oid = order.get("id") or order.get("order_id")
    cid = getattr(order, "client_order_id", None)
    if cid is None and isinstance(order, dict):
        cid = order.get("client_order_id")
    return (str(oid) if oid else None), (str(cid) if cid else None)


def handle_trade_update(data):
    """Testable: fire parked rows when the parent fills. No broker I/O here."""
    if _event_name(data) not in _FILL_EVENTS:
        return []
    order_id, client_order_id = _order_ids(data)
    if not order_id and not client_order_id:
        return []
    return fire_parent(order_id, client_order_id)


async def _on_trade_update(data):
    try:
        handle_trade_update(data)
    except Exception as exc:
        log.warning("fill_listener handle: %s", exc)


def start():
    """Subscribe in a daemon thread. Missing keys or import errors → no-op."""
    global _stream, _thread
    if _thread is not None:
        return _stream
    if not secrets.has_alpaca_credentials():
        log.info("fill_listener: no Alpaca credentials, skipped")
        return None
    try:
        from alpaca.trading.stream import TradingStream

        stream = TradingStream(
            secrets.alpaca_api_key(),
            secrets.alpaca_secret_key(),
            paper=True,
        )
        stream.subscribe_trade_updates(_on_trade_update)

        def _run():
            try:
                stream.run()
            except Exception as exc:
                log.warning("fill_listener stopped: %s", exc)

        _stream = stream
        _thread = threading.Thread(target=_run, name="alpaca-fill-listener", daemon=True)
        _thread.start()
        log.info("fill_listener: trade_updates thread started")
        return stream
    except Exception as exc:
        _stream = None
        _thread = None
        log.warning("fill_listener: not started (%s)", exc)
        return None


def stop():
    global _stream, _thread
    stream, _stream = _stream, None
    _thread = None
    if stream is None:
        return
    try:
        stream.stop()
    except Exception as exc:
        log.warning("fill_listener stop: %s", exc)
