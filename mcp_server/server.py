"""MCP server exposing the Regulatory Affairs KG.

    python -m mcp_server.server

Wiring only. The traversals live in `mcp_server/queries.py` and the transport
in `mcp_server/engine.py`, neither of which has an MCP dependency — so a query
is testable without `fastmcp` installed, and readable without the tool
decorators around it.

What the loader builds, and therefore the ceiling on this server:

    Submission -[:CLASSIFIED_AS]-> ProductCode -[:GOVERNED_BY]-> Regulation

`pending_by_authority` is deliberately absent — openFDA publishes decided
clearances, so it has no answer. See `queries.py` for the full reasoning.
"""

from fastmcp import FastMCP

from mcp_server import queries

mcp = FastMCP("regulatory-affairs-kg")

TOOLS = (
    "clearances_under_regulation",
    "count_clearances_under_regulation",
    "regulations_for_product",
    "regulation_for_clearance",
    "busiest_regulations",
    "clearances_by_advisory_committee",
    "product_codes_by_class",
    "graph_provenance",
)


def register(server) -> None:
    """Bind every tool in TOOLS to the server.

    Registered by name rather than by eight decorators, so a query added to
    queries.py and forgotten here fails a test rather than being silently
    unavailable to an agent.

    That was not true until this round. The guarding test iterated its own
    hardcoded copy of this tuple, so a query missing from BOTH files passed —
    which is the only way it goes missing. The test derives the expected set
    from queries.py now, so the sentence above is checkable.

    In a function, not a bare module-level loop: the loop variable outlived it
    and sat in the module namespace, where `getattr`-style introspection picks
    it up — in the one file whose whole job is wiring.
    """
    for name in TOOLS:
        server.tool()(getattr(queries, name))


register(mcp)


if __name__ == "__main__":
    # `transport="stdio"` explicitly. fastmcp otherwise resolves it from
    # `settings.transport`, which is env-configurable — so `FASTMCP_TRANSPORT=http`
    # turns this into an unauthenticated listener on 127.0.0.1:8000 exposing
    # eight graph-read tools, with no code change and nothing in the README
    # about it. Naming it makes the variable inert.
    mcp.run(transport="stdio")
