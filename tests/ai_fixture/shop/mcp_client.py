from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def refund_via_mcp(order_id: int):
    params = StdioServerParameters(command="python", args=["-m", "shop.mcp_server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.read_resource("orders://42")
            return await session.call_tool("refund", {"order_id": order_id, "amount": 5})
