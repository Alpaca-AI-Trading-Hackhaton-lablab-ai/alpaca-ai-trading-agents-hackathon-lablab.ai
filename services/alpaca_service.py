import os
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from tavily import TavilyClient

# =========================
# Load Environment Variables
# =========================

load_dotenv()


# =========================
# Alpaca Trading Client
# =========================

trading_client = TradingClient(
    os.getenv("APCA_API_KEY_ID"),
    os.getenv("APCA_API_SECRET_KEY"),
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
    os.getenv("APCA_API_KEY_ID"),
    os.getenv("APCA_API_SECRET_KEY")
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