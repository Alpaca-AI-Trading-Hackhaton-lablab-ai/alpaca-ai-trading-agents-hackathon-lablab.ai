def options_strategy(sentiment, confidence):

    if sentiment == "BULLISH" and confidence >= 75:
        return {
            "symbol": "SPY",
            "strategy": "LONG_CALL",
            "action": "BUY_CALL"
        }

    elif sentiment == "BEARISH" and confidence >= 75:
        return {
            "symbol": "SPY",
            "strategy": "LONG_PUT",
            "action": "BUY_PUT"
        }

    else:
        return {
            "symbol": "SPY",
            "strategy": "NO_TRADE",
            "action": "WAIT"
        }