"""MCP server exposing the Regulatory Affairs KG. Run: python -m mcp_server.server"""
from fastmcp import FastMCP
mcp = FastMCP("regulatory-affairs-kg")

@mcp.tool()
def pending_by_authority(limit: int = 5) -> list[dict]:
    """Authorities with the most pending submissions."""
    return []  # TODO: SUBMITTED_TO aggregation on status='pending'

@mcp.tool()
def regulations_for_product(product: str) -> list[dict]:
    """Regulations a given product must comply with."""
    return []  # TODO: COMPLIES_WITH / GOVERNS traversal

if __name__ == "__main__":
    mcp.run()
