"""Building and sending one `/api/query` POST.

Four callers construct this request — `benchmarks/measure.py`, `demo/demo.py`,
`etl/load_openfda.py` and `mcp_server/engine.py` — and their **error policies
genuinely differ**. The loader retries and gives up loudly; the MCP tools turn
every failure into a `Result(error=...)` an agent can read; the benchmark
runner exits; the demo prints and stops. That part is not duplication, it is
four callers wanting different things, and it stays with each of them.

What was duplicated is the ten lines that build the request and time it, and it
has already cost something: a fix landed in one file and not another, and
`measure.py` documents that as a comment about a sibling it cannot enforce.

So this raises whatever `urllib` raises and catches nothing. A shared helper
that decided what a failure meant would be the fifth policy rather than the end
of the duplication.
"""

from __future__ import annotations

import json
import time
import urllib.request

CONTENT_TYPE = {"Content-Type": "application/json"}


def post_query(url: str, cypher: str, *, graph: str | None = None,
               timeout: int = 120) -> tuple[dict, float]:
    """Send one statement. Returns the decoded payload and the elapsed ms.

    `graph` is omitted from the body unless given, because it is not the same
    request otherwise — only the loader sends it, and `/api/query` ignores it
    anyway (measured), so adding it for everyone would change four requests to
    make one shorter.

    **The clock covers the round trip**, not the engine's execution: it starts
    before the request and stops after the JSON is decoded, so it includes
    connection setup, transfer and parsing. That is what a caller waits for,
    and it is what `benchmarks/QUERY_RESULTS.md` says it reports.

    Timeouts stay per-caller, and the spread is deliberate rather than
    accidental: a load statement is allowed longer than an agent's query,
    because a caller waiting on an answer and a script waiting on a batch have
    different amounts of patience. The default is the loader's.
    """
    body = {"query": cypher}
    if graph is not None:
        body["graph"] = graph

    request = urllib.request.Request(
        url.rstrip("/") + "/api/query",
        data=json.dumps(body).encode(),
        headers=CONTENT_TYPE,
    )
    started = time.perf_counter()
    # `with`: an unclosed response holds its socket until the garbage collector
    # gets to it, and every caller here runs this in a loop — one per repeat
    # per query in the benchmarks, one per statement in a 54,000-statement
    # load, one per tool call in a long-lived MCP server.
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    return payload, (time.perf_counter() - started) * 1000
