"""Alpaca MCP helpers. Trading never goes through alpaca-mcp-server.

`place_order` converts qty to notional and calls `dispatch()` (gate, arm,
DRY_RUN). `get_tools` only lists server tools for GET /mcp-tools (debug).
"""

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_price(symbol, last_price=None):
    px = _num(last_price)
    if px is not None and px > 0:
        return px
    try:
        from services.alpaca_service import get_spy_price

        quote = get_spy_price(symbol)
        if isinstance(quote, dict):
            px = _num(quote.get("price"))
            if px is not None and px > 0:
                return px
    except Exception:
        return None
    return None


def place_order(symbol, side, qty=None, notional=None, last_price=None):
    """qty * last_price → notional, then dispatch. Never calls alpaca-mcp-server."""
    from agents.execution_agent import dispatch
    from services.alpaca_service import (
        get_account_info,
        get_market_clock,
        get_open_orders,
        get_positions,
    )

    symbol = str(symbol or "SPY").upper()
    action = "BUY" if str(side or "").lower() in ("buy", "long") else "SELL"

    size = _num(notional)
    if size is None:
        qty_n = _num(qty)
        price = _last_price(symbol, last_price)
        if qty_n is None or qty_n <= 0 or price is None:
            return {
                "status": "NO_TRADE",
                "reason": "invalid qty or price",
                "decision": {"symbol": symbol, "action": action, "position_size": 0},
            }
        size = qty_n * price
    if size <= 0:
        return {
            "status": "NO_TRADE",
            "reason": "invalid notional",
            "decision": {"symbol": symbol, "action": action, "position_size": 0},
        }

    return dispatch(
        decision={"symbol": symbol, "action": action, "position_size": size},
        account=get_account_info(),
        positions=get_positions().get("positions", []),
        open_orders=get_open_orders(symbol),
        clock=get_market_clock(),
    )


async def get_tools():
    server = StdioServerParameters(
        command="alpaca-mcp-server",
        args=["--env-file", ".env"],
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.list_tools()
