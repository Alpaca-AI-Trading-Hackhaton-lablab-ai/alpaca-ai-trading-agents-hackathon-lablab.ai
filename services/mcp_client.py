from mcp import ClientSession
from mcp.client.stdio import (
    stdio_client,
    StdioServerParameters
)

async def place_order(
    symbol,
    side,
    qty
):

    server = StdioServerParameters(
        command="alpaca-mcp-server",
        args=["--env-file", ".env"]
    )

    async with stdio_client(server) as (read, write):

        async with ClientSession(read, write) as session:

            await session.initialize()

            result = await session.call_tool(
                "place_stock_order",
                {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "type": "market",
                    "time_in_force": "day"
                }
            )

            return result