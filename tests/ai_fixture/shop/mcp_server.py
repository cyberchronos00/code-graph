from mcp.server.fastmcp import FastMCP

from .agent import refund_order

mcp = FastMCP("orders")


@mcp.tool()
def refund(order_id: int, amount: int) -> str:
    """Refund an order through the shop."""
    return refund_order(order_id, amount)


@mcp.resource("orders://{order_id}")
def order_resource(order_id: str) -> str:
    return f"order {order_id}"


@mcp.prompt()
def refund_prompt(order_id: str) -> str:
    return f"Refund order {order_id}"


if __name__ == "__main__":
    mcp.run()
