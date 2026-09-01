import os
import traceback

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.decision_agent import make_decision
from agents.execution_agent import execute_trade
from agents.feature_agent import get_market_features
from agents.market_state_agent import build_market_state
from agents.options_agent import options_strategy
from agents.risk_manager import calculate_risk
from agents.sentiment_agent import analyze_sentiment
from agents.technical_agent import technical_analysis
from services.alpaca_service import (
    get_account_info,
    get_order_status,
    get_positions,
    get_spy_price,
)
from services.mcp_client import get_tools
from services.news_service import get_market_news

app = FastAPI()

# El frontend (Vite/bun) corre en otro origen en desarrollo; permitir CORS.
# Configurable con CORS_ORIGINS (coma-separado); "*" por defecto para el PoC.
_origins = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "*").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _symbol(symbol):
    return (symbol or "SPY").upper()


def _analysis(symbol="SPY"):
    symbol = _symbol(symbol)
    news = get_market_news(symbol)
    sentiment = analyze_sentiment(news)
    options = options_strategy(
        sentiment.get("sentiment", "NEUTRAL"),
        sentiment.get("confidence", 0),
        symbol,
    )
    features = get_market_features(symbol)
    technical = technical_analysis(symbol)
    market_state = build_market_state(
        sentiment,
        options,
        features,
        technical,
    )
    account = get_account_info()
    risk = calculate_risk(
        account.get("equity", 100000),
        sentiment.get("confidence", 0),
    )
    decision = make_decision(market_state, risk)

    return {
        "news": news,
        "sentiment": sentiment,
        "options": options,
        "features": features,
        "technical": technical,
        "market_state": market_state,
        "account": account,
        "risk": risk,
        "decision": decision,
    }


@app.get("/")
def home():
    return {"message": "TradeLix AI Running"}


@app.get("/account")
def account():
    return get_account_info()


@app.get("/spy")
def spy(symbol: str = "SPY"):
    return get_spy_price(symbol)


@app.get("/news")
def news(symbol: str = "SPY"):
    return get_market_news(symbol)


@app.get("/sentiment")
def sentiment(symbol: str = "SPY"):
    return _analysis(symbol)["sentiment"]


@app.get("/features")
def features(symbol: str = "SPY"):
    return get_market_features(symbol)


@app.get("/technical")
def technical(symbol: str = "SPY"):
    return technical_analysis(symbol)


@app.get("/options")
def options(symbol: str = "SPY"):
    result = _analysis(symbol)
    return result["options"]


@app.get("/risk")
def risk(symbol: str = "SPY"):
    return _analysis(symbol)["risk"]


@app.get("/market-state")
def market_state(symbol: str = "SPY"):
    return _analysis(symbol)["market_state"]


@app.get("/decision")
def decision(symbol: str = "SPY"):
    return _analysis(symbol)["decision"]


@app.post("/execute")
def execute(symbol: str = "SPY"):
    result = _analysis(symbol)
    return execute_trade(result["decision"])


@app.get("/positions")
def positions():
    return get_positions()


@app.get("/order/{order_id}")
def order(order_id: str):
    return get_order_status(order_id)


@app.get("/mcp-tools")
async def mcp_tools():
    try:
        result = await get_tools()

        return {
            "success": True,
            "tools": str(result),
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


@app.get("/buy")
async def buy():
    return {
        "status": "DISABLED",
        "reason": "Use /execute only after server-side dry-run and confirmation are implemented.",
    }
