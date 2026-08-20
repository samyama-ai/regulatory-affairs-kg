"""MCP server exposing the Regulatory Affairs KG.

    python -m mcp_server.server

Wiring only. Every query lives in `mcp_server/queries.py`, which has no MCP
dependency — so the traversals are testable without `fastmcp` installed, and a
query can be read without the tool decorators around it.

What the loader builds, and therefore the ceiling on this server:

    Submission -[:CLASSIFIED_AS]-> ProductCode -[:GOVERNED_BY]-> Regulation

`pending_by_authority` is deliberately absent — openFDA publishes decided
clearances, so it has no answer. See `queries.py` for the full reasoning.
"""

from fastmcp import FastMCP

from mcp_server import queries

mcp = FastMCP("regulatory-affairs-kg")

for _name in (
    "clearances_under_regulation",
    "count_clearances_under_regulation",
    "regulations_for_product",
    "regulation_for_clearance",
    "busiest_regulations",
    "clearances_by_advisory_committee",
    "product_codes_by_class",
    "graph_provenance",
):
    # Registered by name rather than by eight decorators, so a query added to
    # queries.py and forgotten here fails a test rather than being silently
    # unavailable to an agent.
    mcp.tool()(getattr(queries, _name))


if __name__ == "__main__":
    mcp.run()
