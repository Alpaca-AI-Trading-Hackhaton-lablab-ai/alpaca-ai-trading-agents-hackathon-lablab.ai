import math
import os
from datetime import datetime, timedelta

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
    TrailingStopOrderRequest,
)
from dotenv import load_dotenv

from services import cache, secrets

load_dotenv()

DEMO_PRICES = {
    "SPY": 512.34,
    "QQQ": 438.72,
    "AAPL": 226.18,
    "MSFT": 421.56,
    "NVDA": 129.44,
    "TSLA": 246.91,
}

_trading_client = None
_data_client = None


def _symbol(symbol):
    return (symbol or "SPY").upper()


def _has_alpaca_credentials():
    return secrets.has_alpaca_credentials()


def reset_clients():
    """Drop cached alpaca-py clients after API keys change in the DB."""
    global _trading_client, _data_client
    _trading_client = None
    _data_client = None


def _assert_paper():
    # Invariante de hardening: nunca operar live por error.
    flag = os.getenv("ALPACA_PAPER_TRADE", "true").strip().lower()
    if flag in ("false", "0", "no"):
        raise RuntimeError(
            "ALPACA_PAPER_TRADE=false: ejecución live deshabilitada en el PoC. "
            "Usar credenciales paper y ALPACA_PAPER_TRADE=true."
        )


def _get_trading_client():
    from services import usage_meter

    usage_meter.record("alpaca", requests=1)
    global _trading_client
    if _trading_client is None:
        _assert_paper()
        _trading_client = TradingClient(
            secrets.alpaca_api_key(),
            secrets.alpaca_secret_key(),
            paper=True,
        )
    return _trading_client


def _get_data_client():
    from services import usage_meter

    usage_meter.record("alpaca", requests=1)
    global _data_client
    if _data_client is None:
        _data_client = StockHistoricalDataClient(
            secrets.alpaca_api_key(),
            secrets.alpaca_secret_key(),
        )
    return _data_client


def _demo_price(symbol):
    return DEMO_PRICES.get(_symbol(symbol), 100.0)


_TF_MAP = {
    "1Day": TimeFrame.Day,
    "1Hour": TimeFrame.Hour,
}


def _demo_bars(symbol, timeframe="1Day", limit=200):
    end = datetime.now()
    base = _demo_price(symbol)
    step = timedelta(hours=1) if timeframe == "1Hour" else timedelta(days=1)
    rows = []
    count = max(2, int(limit))
    for i in range(count):
        stamp = end - step * (count - 1 - i)
        drift = i * 0.18
        wobble = math.sin(i / 5) * (base * 0.015)
        close = round(base - 9 + drift + wobble, 2)
        rows.append(
            {
                "timestamp": stamp,
                "symbol": _symbol(symbol),
                "open": round(close * 0.995, 2),
                "high": round(close * 1.01, 2),
                "low": round(close * 0.99, 2),
                "close": close,
                "volume": 1_000_000 + i * 10_000,
            }
        )
    return pd.DataFrame(rows)


def _fetch_account_info():
    if not _has_alpaca_credentials():
        return {
            "equity": 100000.0,
            "cash": 100000.0,
            "buying_power": 200000.0,
            "status": "DEMO",
            "mode": "demo",
            "warning": "Missing Alpaca paper credentials",
        }

    try:
        account = _get_trading_client().get_account()

        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "status": str(account.status).split(".")[-1],
            "mode": "paper",
        }

    except Exception as e:
        return {
            "equity": 100000.0,
            "cash": 100000.0,
            "buying_power": 200000.0,
            "status": "DEMO",
            "mode": "demo",
            "warning": str(e),
        }


def get_account_info():
    return cache.cached("account", "_", "_", cache.TTL_ACCOUNT, _fetch_account_info)


def get_spy_price(symbol="SPY"):
    symbol = _symbol(symbol)

    if not _has_alpaca_credentials():
        price = _demo_price(symbol)
        return {
            "symbol": symbol,
            "bid": round(price - 0.02, 2),
            "ask": round(price + 0.02, 2),
            "price": price,
            "mode": "demo",
        }

    try:
        request = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
        quote = _get_data_client().get_stock_latest_quote(request)
        bid = float(quote[symbol].bid_price)
        ask = float(quote[symbol].ask_price)

        return {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "price": round((bid + ask) / 2, 2),
            "mode": "paper",
        }

    except Exception as e:
        price = _demo_price(symbol)
        return {
            "symbol": symbol,
            "bid": round(price - 0.02, 2),
            "ask": round(price + 0.02, 2),
            "price": price,
            "mode": "demo",
            "warning": str(e),
        }


def get_spy_bars(symbol="SPY", timeframe="1Day", limit=200):
    """OHLC from Alpaca IEX (or demo). `timeframe` is 1Day or 1Hour."""
    symbol = _symbol(symbol)
    tf_key = timeframe if timeframe in _TF_MAP else "1Day"
    limit = max(2, int(limit or 200))

    if not _has_alpaca_credentials():
        return _demo_bars(symbol, timeframe=tf_key, limit=limit)

    try:
        end = datetime.now()
        if tf_key == "1Hour":
            start = end - timedelta(days=max(21, limit // 5 + 7))
        else:
            start = end - timedelta(days=max(limit, 200))

        request = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=_TF_MAP[tf_key],
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )

        bars = _get_data_client().get_stock_bars(request)
        df = bars.df.reset_index()
        if df.empty:
            return _demo_bars(symbol, timeframe=tf_key, limit=limit)
        if len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)
        return df

    except Exception:
        return _demo_bars(symbol, timeframe=tf_key, limit=limit)


def _leg_ids(order):
    ids = []
    for leg in getattr(order, "legs", None) or []:
        lid = getattr(leg, "id", None)
        if lid:
            ids.append(str(lid))
    return ids


def _order_dict(order):
    # Normaliza un objeto Order de alpaca-py a un dict serializable.
    return {
        "order_id": str(order.id),
        "client_order_id": getattr(order, "client_order_id", None),
        "symbol": order.symbol,
        "side": str(order.side).split(".")[-1].lower(),
        "status": str(order.status).split(".")[-1].lower(),
        "notional": float(order.notional) if order.notional is not None else None,
        "qty": float(order.qty) if order.qty is not None else None,
        "filled_qty": float(order.filled_qty) if order.filled_qty is not None else 0.0,
        "filled_avg_price": (
            float(order.filled_avg_price)
            if order.filled_avg_price is not None
            else None
        ),
        "submitted_at": (
            order.submitted_at.isoformat() if order.submitted_at else None
        ),
        "leg_ids": _leg_ids(order),
    }


def submit_market_order(symbol, side, notional):
    """Envía una orden market/day por MONTO EN DÓLARES (notional) en la MISMA
    cuenta paper que se usa para leer cuenta/posiciones."""
    symbol = _symbol(symbol)

    if not _has_alpaca_credentials():
        return {
            "status": "demo",
            "mode": "demo",
            "symbol": symbol,
            "side": str(side).lower(),
            "notional": float(notional),
            "warning": "Missing Alpaca paper credentials",
        }

    order_side = OrderSide.BUY if str(side).lower() == "buy" else OrderSide.SELL

    request = MarketOrderRequest(
        symbol=symbol,
        notional=float(notional),
        side=order_side,
        time_in_force=TimeInForce.DAY,
    )

    order = _get_trading_client().submit_order(order_data=request)
    result = _order_dict(order)
    result["mode"] = "paper"
    return result


def submit_bracket_order(
    symbol,
    side,
    notional,
    take_profit_price,
    stop_loss_price,
    entry_type="market",
    limit_price=None,
    qty=None,
):
    """Paper bracket: 1 entry + 1 TP (limit) + 1 SL (stop). Integer qty only.

    Alpaca rejects notional/fractional combined with order_class=bracket.
    """
    symbol = _symbol(symbol)
    _assert_paper()

    share_qty = int(float(qty)) if qty is not None else 0
    if share_qty < 1:
        return {
            "status": "rejected",
            "mode": "paper",
            "symbol": symbol,
            "side": str(side).lower(),
            "qty": share_qty,
            "notional": float(notional) if notional is not None else None,
            "order_class": "bracket",
            "reason": "bracket needs ≥1 share (Alpaca rejects notional brackets)",
        }

    if not _has_alpaca_credentials():
        return {
            "status": "demo",
            "mode": "demo",
            "symbol": symbol,
            "side": str(side).lower(),
            "qty": share_qty,
            "notional": float(notional) if notional is not None else None,
            "order_class": "bracket",
            "leg_ids": [],
            "take_profit": {"limit_price": take_profit_price},
            "stop_loss": {"stop_price": stop_loss_price},
            "warning": "Missing Alpaca paper credentials",
        }

    order_side = OrderSide.BUY if str(side).lower() == "buy" else OrderSide.SELL
    tp = TakeProfitRequest(limit_price=float(take_profit_price))
    sl = StopLossRequest(stop_price=float(stop_loss_price))
    common = {
        "symbol": symbol,
        "side": order_side,
        "qty": float(share_qty),
        "time_in_force": TimeInForce.DAY,
        "order_class": OrderClass.BRACKET,
        "take_profit": tp,
        "stop_loss": sl,
    }

    if str(entry_type).lower() == "limit" and limit_price is not None:
        request = LimitOrderRequest(limit_price=float(limit_price), **common)
    else:
        request = MarketOrderRequest(**common)

    order = _get_trading_client().submit_order(order_data=request)
    result = _order_dict(order)
    result["mode"] = "paper"
    result["order_class"] = "bracket"
    return result


def submit_trailing_stop_order(
    symbol,
    side,
    notional,
    trail_percent=None,
    trail_price=None,
    qty=None,
):
    """Standalone trailing stop. Native only when the plan is trailing-only."""
    symbol = _symbol(symbol)
    _assert_paper()

    if not _has_alpaca_credentials():
        return {
            "status": "demo",
            "mode": "demo",
            "symbol": symbol,
            "side": str(side).lower(),
            "notional": float(notional) if notional is not None else None,
            "type": "trailing_stop",
            "warning": "Missing Alpaca paper credentials",
        }

    order_side = OrderSide.BUY if str(side).lower() == "buy" else OrderSide.SELL
    kwargs = {
        "symbol": symbol,
        "side": order_side,
        "time_in_force": TimeInForce.GTC,
    }
    if qty is not None:
        kwargs["qty"] = float(qty)
    else:
        kwargs["notional"] = float(notional)
    if trail_percent is not None:
        kwargs["trail_percent"] = float(trail_percent)
    if trail_price is not None:
        kwargs["trail_price"] = float(trail_price)
    request = TrailingStopOrderRequest(**kwargs)
    order = _get_trading_client().submit_order(order_data=request)
    result = _order_dict(order)
    result["mode"] = "paper"
    result["type"] = "trailing_stop"
    return result


def get_order_status(order_id):
    if not _has_alpaca_credentials():
        return {"order_id": str(order_id), "status": "demo", "mode": "demo"}

    try:
        order = _get_trading_client().get_order_by_id(order_id)
        result = _order_dict(order)
        result["mode"] = "paper"
        reason = getattr(order, "rejected_reason", None) or getattr(
            order, "canceled_at", None
        )
        if reason:
            result["reason"] = str(reason)
        return result

    except Exception as e:
        return {"order_id": str(order_id), "status": "unknown", "warning": str(e)}


def cancel_order(order_id):
    """Cancel a paper working order. _assert_paper intact."""
    _assert_paper()
    if not _has_alpaca_credentials():
        return {
            "status": "demo",
            "mode": "demo",
            "order_id": str(order_id),
            "warning": "Missing Alpaca paper credentials",
        }
    try:
        _get_trading_client().cancel_order_by_id(str(order_id))
        return {"status": "CANCELED", "mode": "paper", "order_id": str(order_id)}
    except Exception as e:
        return {
            "status": "FAILED",
            "mode": "paper",
            "order_id": str(order_id),
            "reason": str(e),
        }


def get_open_orders(symbol=None):
    """Open (working) orders resting at the paper broker, normalized to dicts.
    Optionally filtered by symbol. Demo mode has no broker -> empty list.
    Used by the execution gate to enforce one working order per symbol."""
    if not _has_alpaca_credentials():
        return []

    try:
        symbols = [_symbol(symbol)] if symbol else None
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=symbols)
        orders = _get_trading_client().get_orders(filter=request)
        return [_order_dict(o) for o in orders]

    except Exception:
        # Fail-closed is the caller's job; here we just report "unknown" safely.
        return []


def get_market_clock():
    """Market session state. Informational for the gate (a closed market still
    ALLOWs; the paper broker queues the DAY order). Demo -> weekday heuristic."""
    if not _has_alpaca_credentials():
        now = datetime.now()
        weekday = now.weekday() < 5
        regular = 9 <= now.hour < 16
        return {"is_open": bool(weekday and regular), "mode": "demo"}

    try:
        clock = _get_trading_client().get_clock()
        return {
            "is_open": bool(clock.is_open),
            "next_open": clock.next_open.isoformat() if clock.next_open else None,
            "next_close": (
                clock.next_close.isoformat() if clock.next_close else None
            ),
            "mode": "paper",
        }

    except Exception as e:
        return {"is_open": False, "mode": "demo", "warning": str(e)}


def get_positions():
    if not _has_alpaca_credentials():
        return {"mode": "demo", "positions": []}

    try:
        positions = _get_trading_client().get_all_positions()
        return {
            "mode": "paper",
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "side": str(p.side).split(".")[-1].lower(),
                    "avg_entry_price": float(p.avg_entry_price),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                }
                for p in positions
            ],
        }

    except Exception as e:
        return {"mode": "demo", "positions": [], "warning": str(e)}


def flatten_positions():
    """Market-close every paper position. Reduce-only wind-down. _assert_paper intact."""
    _assert_paper()
    if not _has_alpaca_credentials():
        return {"status": "demo", "mode": "demo", "closed": []}
    try:
        _get_trading_client().close_all_positions(cancel_orders=True)
        return {"status": "flattened", "mode": "paper"}
    except Exception as e:
        return {"status": "FAILED", "mode": "paper", "reason": str(e)}
