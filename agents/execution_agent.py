from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
import os


client = TradingClient(
    os.getenv("APCA_API_KEY_ID"),
    os.getenv("APCA_API_SECRET_KEY"),
    paper=True
)


def execute_trade(decision):

    if decision["decision"] == "NO_TRADE":
        return {
            "status": "SKIPPED",
            "reason": "No trading signal"
        }

    if decision["decision"] == "BUY_CALL":
        side = OrderSide.BUY

    elif decision["decision"] == "BUY_PUT":
        side = OrderSide.SELL

    else:
        return {
            "status": "INVALID_SIGNAL"
        }

    order = MarketOrderRequest(
        symbol="SPY",
        qty=1,
        side=side,
        time_in_force=TimeInForce.DAY
    )

    result = client.submit_order(order)

    return {
        "status": "EXECUTED",
        "order_id": str(result.id),
        "symbol": result.symbol,
        "qty": result.qty,
        "side": str(side)
    }