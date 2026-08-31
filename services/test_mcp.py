# test_mcp.py

import asyncio
from services.mcp_client import get_tools


async def main():
    tools = await get_tools()
    print(tools)


asyncio.run(main())