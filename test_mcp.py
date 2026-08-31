import asyncio

from mcp import ClientSession
from mcp.client.stdio import (
    stdio_client,
    StdioServerParameters
)

async def main():

    server = StdioServerParameters(
        command="alpaca-mcp-server",
        args=["--env-file", ".env"]
    )

    async with stdio_client(server) as (read, write):

        async with ClientSession(read, write) as session:

            print("Initializing...")

            await session.initialize()

            print("Connected!")

            tools = await session.list_tools()

            print("TOOLS:")
            print(tools)

asyncio.run(main())