import math
import os
from datetime import datetime, timedelta

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from dotenv import load_dotenv

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
    return bool(os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"))


def _get_trading_client():
    global _trading_client
    if _trading_client is None:
        _trading_client = TradingClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            paper=True,
        )
    return _trading_client


def _get_data_client():
    global _data_client
    if _data_client is None:
        _data_client = StockHistoricalDataClient(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
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


def get_account_info():
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
