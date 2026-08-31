from services.alpaca_service import get_spy_price


def get_market_features():

    market = get_spy_price()

    price = market["ask"]

    # Simple dummy indicators for now
    sma20 = price * 0.99
    sma50 = price * 0.97

    if sma20 > sma50:
        trend = "BULLISH"
    else:
        trend = "BEARISH"

    return {
        "symbol": "SPY",
        "price": round(price, 2),
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2),
        "trend": trend
    }