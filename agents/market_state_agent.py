def build_market_state(
    sentiment,
    options,
    features,
    technical
):

    return {
        "symbol": "SPY",

        # Sentiment
        "sentiment": sentiment["sentiment"],
        "confidence": sentiment["confidence"],
        "trade_bias": sentiment["trade_bias"],

        # Feature Agent
        "trend": features["trend"],
        "price": features["price"],

        # Technical Agent
        "rsi": technical["rsi"],
        "sma20": technical["sma20"],
        "sma50": technical["sma50"],
        "technical_signal": technical["signal"],

        # Options Agent
        "option_strategy": options["strategy"]
    }