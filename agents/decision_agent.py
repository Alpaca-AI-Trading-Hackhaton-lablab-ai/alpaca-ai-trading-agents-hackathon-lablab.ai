def make_decision(
    market_state,
    risk
):

    sentiment = market_state["sentiment"]
    technical = market_state["technical_signal"]

    max_position = risk["position_size"]   # FIX

    if (
        sentiment in ("POSITIVE", "BULLISH")
        and technical == "BUY"
    ):
        action = "BUY"

    elif (
        sentiment in ("NEGATIVE", "BEARISH")
        and technical == "SELL"
    ):
        action = "SELL"

    else:
        action = "HOLD"

    return {
        "symbol": market_state["symbol"],
        "action": action,
        "position_size": max_position,
        "technical_signal": technical,
        "sentiment": sentiment,
        "risk_level": risk["risk_level"]
    }
