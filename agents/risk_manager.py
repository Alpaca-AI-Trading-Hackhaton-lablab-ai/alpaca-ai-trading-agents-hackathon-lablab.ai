def calculate_risk(account_balance, confidence):

    # Risk based on confidence

    if confidence >= 85:
        risk_percent = 0.03
        risk_level = "HIGH"

    elif confidence >= 75:
        risk_percent = 0.02
        risk_level = "MEDIUM"

    else:
        risk_percent = 0.01
        risk_level = "LOW"

    position_size = round(
        account_balance * risk_percent,
        2
    )

    max_loss = round(
        position_size * 0.05,
        2
    )

    take_profit = round(
        position_size * 0.10,
        2
    )

    return {
        "account_balance": account_balance,
        "confidence": confidence,
        "risk_level": risk_level,
        "position_size": position_size,
        "max_loss": max_loss,
        "take_profit": take_profit
    }