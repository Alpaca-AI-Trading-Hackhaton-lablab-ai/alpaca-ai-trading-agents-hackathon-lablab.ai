import os
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest


load_dotenv()

client = TradingClient(
    os.getenv("APCA_API_KEY_ID"),
    os.getenv("APCA_API_SECRET_KEY"),
    paper=True
)

def get_account_info():
    account = client.get_account()

    return {
        "equity": float(account.equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "status": account.status
    }
    
    #________________Market Data Agent


data_client = StockHistoricalDataClient(
    os.getenv("APCA_API_KEY_ID"),
    os.getenv("APCA_API_SECRET_KEY")
)

def get_spy_price():
    request = StockLatestQuoteRequest(
        symbol_or_symbols=["SPY"]
    )

    quote = data_client.get_stock_latest_quote(request)

    return {
        "symbol": "SPY",
        "bid": quote["SPY"].bid_price,
        "ask": quote["SPY"].ask_price
    }
    
    #_________________ Tavily News Agent
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

client = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)

def get_market_news():

    result = client.search(
        query="SPY stock market news today",
        max_results=5
    )

    return result