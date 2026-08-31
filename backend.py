from fastapi import FastAPI

# Services
from services.alpaca_service import (
    get_account_info,
    get_spy_price
)
from services.news_service import get_market_news

# Agents
from agents.sentiment_agent import analyze_sentiment
from agents.feature_agent import get_market_features
from agents.technical_agent import technical_analysis
from agents.options_agent import options_strategy
from agents.risk_manager import calculate_risk
from agents.market_state_agent import build_market_state
from agents.decision_agent import make_decision
from agents.execution_agent import execute_trade

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


@app.get("/sentiment")
def sentiment():
    news = get_market_news()
    return analyze_sentiment(news)


@app.get("/features")
def features():
    return get_market_features()


@app.get("/technical")
def technical():
    return technical_analysis()


@app.get("/options")
def options():
    news = get_market_news()
    sentiment = analyze_sentiment(news)

    return options_strategy(
        sentiment["sentiment"],
        sentiment["confidence"]
    )


@app.get("/risk")
def risk():
    news = get_market_news()
    sentiment = analyze_sentiment(news)
    account = get_account_info()

    return calculate_risk(
        account["equity"],
        sentiment["confidence"]
    )


@app.get("/market-state")
def market_state():

    news = get_market_news()
    sentiment = analyze_sentiment(news)

    options = options_strategy(
        sentiment["sentiment"],
        sentiment["confidence"]
    )

    features = get_market_features()
    technical = technical_analysis()

    return build_market_state(
        sentiment,
        options,
        features,
        technical
    )


@app.get("/decision")
def decision():

    try:
        news = get_market_news()
        sentiment = analyze_sentiment(news)

        options = options_strategy(
            sentiment["sentiment"],
            sentiment["confidence"]
        )

        features = get_market_features()
        technical = technical_analysis()

        market_state = build_market_state(
            sentiment,
            options,
            features,
            technical
        )

        account = get_account_info()

        risk = calculate_risk(
            account["equity"],
            sentiment["confidence"]
        )

        return make_decision(
            market_state,
            risk
        )

    except Exception as e:
        return {"error": str(e)}


@app.get("/execute")
def execute():

    news = get_market_news()
    sentiment = analyze_sentiment(news)

    options = options_strategy(
        sentiment["sentiment"],
        sentiment["confidence"]
    )

    features = get_market_features()
    technical = technical_analysis()

    market_state = build_market_state(
        sentiment,
        options,
        features,
        technical
    )

    account = get_account_info()

    risk = calculate_risk(
        account["equity"],
        sentiment["confidence"]
    )

    decision = make_decision(
        market_state,
        risk
    )

    return execute_trade(decision)


# MCP TEST ENDPOINT
from services.mcp_client import get_tools
import traceback

@app.get("/mcp-tools")
async def mcp_tools():

    try:
        result = await get_tools()

        return {
            "success": True,
            "tools": str(result)
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }
        
#----------------------------
from services.mcp_client import place_order

@app.get("/buy")
async def buy():

    return await place_order()