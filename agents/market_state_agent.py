def build_market_state(
    sentiment,
    options,
    features,
    technical
):

    return {
        "symbol": features.get("symbol", options.get("symbol", "SPY")),

        # Sentiment
        "sentiment": sentiment.get("sentiment", "NEUTRAL"),
        "confidence": sentiment.get("confidence", 0),
        "trade_bias": sentiment.get("trade_bias", "WAIT"),

        # Feature Agent
        "trend": features.get("trend", "NEUTRAL"),
        "price": features.get("price", 100.0),

        # Technical Agent
        "rsi": technical.get("rsi", 50),
        "sma20": technical.get("sma20", features.get("sma20", 99)),
        "sma50": technical.get("sma50", features.get("sma50", 97)),
        "technical_signal": technical.get("signal", "HOLD"),

        # Options Agent
        "option_strategy": options.get("strategy", "NO_TRADE")
    }
