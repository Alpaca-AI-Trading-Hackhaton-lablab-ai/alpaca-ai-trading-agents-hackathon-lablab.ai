import os
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from tavily import TavilyClient

#--------dummy technical agent to Alpaca historical candles
from alpaca.data.timeframe import TimeFrame
from alpaca.data.requests import StockBarsRequest
from datetime import datetime, timedelta
import pandas as pd
from alpaca.data.enums import DataFeed

# =========================
# Load Environment Variables
# =========================

load_dotenv()


# =========================
# Alpaca Trading Client
# =========================

trading_client = TradingClient(
    os.getenv("ALPACA_API_KEY"),
    os.getenv("ALPACA_SECRET_KEY"),
    paper=True
)

# =========================
# Account Agent
# =========================

def get_account_info():
    try:
        account = trading_client.get_account()

        return {
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
           "status": str(account.status).split(".")[-1]
        }

    except Exception as e:
        return {
            "error": str(e)
        }


# =========================
# Market Data Agent
# =========================

data_client = StockHistoricalDataClient(
    os.getenv("ALPACA_API_KEY"),
    os.getenv("ALPACA_SECRET_KEY")
)

def get_spy_price():
    try:
        request = StockLatestQuoteRequest(
            symbol_or_symbols=["SPY"]
        )

        quote = data_client.get_stock_latest_quote(request)

        return {
            "symbol": "SPY",
            "bid": quote["SPY"].bid_price,
            "ask": quote["SPY"].ask_price
        }

    except Exception as e:
        return {
            "error": str(e)
        }


# =========================
# News Agent
# =========================

tavily_client = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)

def get_market_news():
    try:

        result = tavily_client.search(
            query="SPY stock market news today",
            max_results=5
        )

        return result

    except Exception as e:
        return {
            "error": str(e)
        }
        
#---------------
def get_spy_bars():

    try:
        end = datetime.now()
        start = end - timedelta(days=90)

        request = StockBarsRequest(
            symbol_or_symbols=["SPY"],
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX
        )

        bars = data_client.get_stock_bars(request)

        return bars.df.reset_index()

    except Exception as e:
        return {
            "error": str(e)
        }