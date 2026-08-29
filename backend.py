from fastapi import FastAPI

from services.alpaca_service import (
    get_account_info,
    get_spy_price
)

from services.news_service import get_market_news

app = FastAPI()


@app.get("/")
def home():
    return {"message": "TradeLix AI Running"}


@app.get("/account")
def account():
    return get_account_info()


@app.get("/spy")
def spy():
    return get_spy_price()


@app.get("/news")
def news():
    return get_market_news()