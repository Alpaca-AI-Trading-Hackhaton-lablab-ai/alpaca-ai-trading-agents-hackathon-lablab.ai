"""Risk node — deterministic position sizing from confidence tiers. Never an LLM.

Domain logic above; Agent wrapper at the bottom.
"""

from agents.base import Agent


def calculate_risk(account_balance, confidence):
    account_balance = float(account_balance or 100000)
    confidence = float(confidence or 0)

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


class RiskAgent(Agent):
    node = "risk"

    def run(self, ctx):
        return calculate_risk(
            ctx["account"].get("equity", 100000),
            ctx["sentiment"].get("confidence", 0),
        )

    def message(self, out):
        return self._err(out) or f"{out.get('risk_level')} · ${out.get('position_size')}"
