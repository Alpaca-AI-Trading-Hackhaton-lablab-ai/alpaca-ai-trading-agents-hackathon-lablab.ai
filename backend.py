from fastapi import FastAPI

# Services
from services.alpaca_service import (
    get_account_info,
    get_spy_price
)

from services.news_service import get_market_news

# Agents
from agents.sentiment_agent import analyze_sentiment
from agents.options_agent import options_strategy
from agents.risk_manager import calculate_risk
from agents.decision_agent import make_decision
from agents.execution_agent import execute_trade

app = FastAPI()


# ---------------- HOME ----------------

@app.get("/")
def home():
    return {"message": "TradeLix AI Running"}


# ---------------- ACCOUNT AGENT ----------------

@app.get("/account")
def account():
    return get_account_info()


# ---------------- MARKET DATA AGENT ----------------

@app.get("/spy")
def spy():
    return get_spy_price()


# ---------------- NEWS AGENT ----------------

@app.get("/news")
def news():
    return get_market_news()


# ---------------- SENTIMENT AGENT ----------------

@app.get("/sentiment")
def sentiment():

    news = get_market_news()

    return analyze_sentiment(news)


# ---------------- OPTIONS AGENT ----------------
@app.get("/options")
def options():

    news = get_market_news()

    sentiment = analyze_sentiment(news)

    return options_strategy(
        sentiment["sentiment"],
        sentiment["confidence"]
    )

# ---------------- RISK MANAGER ----------------

@app.get("/risk")
def risk():

    news = get_market_news()

    sentiment = analyze_sentiment(news)

    account = get_account_info()

    return calculate_risk(
        account["equity"],
        sentiment["confidence"]
    )

# ---------------- DECISION AGENT ----------------

@app.get("/decision")
def decision():

    news = get_market_news()

    sentiment = analyze_sentiment(news)

    option = options_strategy(
        sentiment["sentiment"],
        sentiment["confidence"]
    )

    account = get_account_info()

    risk = calculate_risk(
        account["equity"],
        sentiment["confidence"]
    )

    return make_decision(
        option,
        risk
    )

# ---------------- EXECUTION AGENT ----------------

@app.get("/execute")
def execute():

    news = get_market_news()

    sentiment = analyze_sentiment(news)

    option = options_strategy(
        sentiment["sentiment"],
        sentiment["confidence"]
    )

    account = get_account_info()

    risk = calculate_risk(
        account["equity"],
        sentiment["confidence"]
    )

    decision = make_decision(
        option,
        risk
    )

    return execute_trade(decision)