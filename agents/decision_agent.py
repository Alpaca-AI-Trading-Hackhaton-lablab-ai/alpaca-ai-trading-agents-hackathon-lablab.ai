def make_decision(option_data, risk_data):

    if option_data["action"] == "WAIT":
        return {
            "decision": "NO_TRADE",
            "reason": "Low confidence signal",
            "risk_level": risk_data["risk_level"]
        }

    return {
        "decision": option_data["action"],
        "strategy": option_data["strategy"],
        "position_size": risk_data["position_size"],
        "max_loss": risk_data["max_loss"],
        "take_profit": risk_data["take_profit"],
        "risk_level": risk_data["risk_level"]
    }