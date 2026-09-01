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

        # Feature / technical engine
        "trend": features.get("trend", "NEUTRAL"),
        "price": features.get("price") or technical.get("price", 100.0),
        "rsi": technical.get("rsi", features.get("rsi")),
        "sma20": technical.get("sma20", features.get("sma20")),
        "sma50": technical.get("sma50", features.get("sma50")),
        "ema20": technical.get("ema20", features.get("ema20")),
        "macd": technical.get("macd", features.get("macd")),
        "macd_signal": technical.get("macd_signal"),
        "macd_hist": technical.get("macd_hist"),
        "atr": technical.get("atr", features.get("atr")),
        "volume": technical.get("volume", features.get("volume")),
        "technical_signal": technical.get("signal", "HOLD"),

        # Options Agent
        "option_strategy": options.get("strategy", "NO_TRADE"),
    }
