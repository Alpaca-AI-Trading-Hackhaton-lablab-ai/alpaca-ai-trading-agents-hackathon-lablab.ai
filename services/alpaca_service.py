import math
import os
from datetime import datetime, timedelta

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
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
    global _data_client
    if _data_client is None:
        _data_client = StockHistoricalDataClient(
            secrets.alpaca_api_key(),
            secrets.alpaca_secret_key(),
        )
    return _data_client


def _demo_price(symbol):
    return DEMO_PRICES.get(_symbol(symbol), 100.0)


def _demo_bars(symbol):
    end = datetime.now()
    base = _demo_price(symbol)
    rows = []

    for i in range(90):
        day = end - timedelta(days=89 - i)
        drift = i * 0.18
        wobble = math.sin(i / 5) * (base * 0.015)
        close = round(base - 9 + drift + wobble, 2)
        rows.append(
            {
                "timestamp": day,
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


def get_spy_bars(symbol="SPY"):
    symbol = _symbol(symbol)

    if not _has_alpaca_credentials():
        return _demo_bars(symbol)

    try:
        end = datetime.now()
        start = end - timedelta(days=90)

        request = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )

        bars = _get_data_client().get_stock_bars(request)
        df = bars.df.reset_index()
        if df.empty:
            return _demo_bars(symbol)
        return df

    except Exception:
        return _demo_bars(symbol)


def _order_dict(order):
    # Normaliza un objeto Order de alpaca-py a un dict serializable.
    return {
        "order_id": str(order.id),
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
