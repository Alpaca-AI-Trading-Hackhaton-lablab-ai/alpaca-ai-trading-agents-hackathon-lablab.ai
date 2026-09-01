def options_strategy(sentiment, confidence, symbol="SPY"):
    symbol = (symbol or "SPY").upper()

    if sentiment == "BULLISH" and confidence >= 75:
        return {
            "symbol": symbol,
            "strategy": "LONG_CALL",
            "action": "BUY_CALL"
        }

    elif sentiment == "BEARISH" and confidence >= 75:
        return {
            "symbol": symbol,
            "strategy": "LONG_PUT",
            "action": "BUY_PUT"
        }

    else:
        return {
            "symbol": symbol,
            "strategy": "NO_TRADE",
            "action": "WAIT"
        }
