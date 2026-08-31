import asyncio

from services.mcp_client import place_order


def execute_trade(decision):

    if decision["action"] == "HOLD":
        return {
            "status": "NO_TRADE",
            "reason": "Decision Agent returned HOLD"
        }

    try:

        result = asyncio.run(
            place_order(
                symbol=decision["symbol"],
                side=decision["action"].lower(),
                qty=str(decision["qty"])
            )
        )

        return {
            "status": "ORDER_SUBMITTED",
            "decision": decision,
            "alpaca_result": result
        }

    except Exception as e:

        return {
            "status": "FAILED",
            "error": str(e)
        }