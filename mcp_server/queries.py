"""The queries behind the MCP tools — no MCP dependency.

Separated from `server.py` so the traversals can be tested without `fastmcp`
installed, and so a query can be read without the tool wiring around it. This
is the same split `clinicaltrials-kg` uses (`mcp_server/tools/`).

Every query here runs a traversal that `demo/demo.py` already proves works
against the loaded graph. Nothing promises a shape the loader does not build.

WHAT THE LOADER BUILDS, and therefore the ceiling on this file:

    Submission -[:CLASSIFIED_AS]-> ProductCode -[:GOVERNED_BY]-> Regulation

`regulation_number` is on every clearance, approval and classification record,
which is what makes the device-to-law join exact rather than a name match. That
join is the change-impact question, and it is why this graph exists.

WHAT IS DELIBERATELY ABSENT:

  * `pending_by_authority`, which the previous version of this file declared and
    returned `[]` from. It is not answerable. openFDA publishes *decided*
    clearances — every record carries `decision_date` and `decision_code`, there
    is no pending queue in the public data, no `SUBMITTED_TO` edge in the schema
    and no `status` property on Submission. `clearances_by_advisory_committee`
    is the nearest honest thing: the advisory committee IS the FDA's reviewing
    authority, and it is loaded.

  * Predicate chains. `PredicateClaim` and `CITES_PREDICATE` are in the schema
    but the loader does not build them — they need the 510(k) Summary PDF
    extractor, which does not exist. A tool for them would be a second stub.

An empty result and a failed one are different answers. Every tool returns a
`Result` carrying `rows` and `error`, because an agent cannot act on `[]` if it
means both "no clearances" and "the engine is down".
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# The loader's literal encoder, not a second one. `etl/cypher.py` is pure text
# functions with no engine dependency, so this costs nothing and keeps one
# measured implementation of "what 1.1.0 accepts inside a literal".
from etl.cypher import lit

DEFAULT_URL = "http://127.0.0.1:8080"


def engine_url() -> str:
    """Where the graph is. Environment first, then config.yaml, then the default.

    Read at call time rather than at import, so a server started before the
    engine moved does not keep talking to the old address.
    """
    if os.environ.get("SAMYAMA_URL"):
        return os.environ["SAMYAMA_URL"].rstrip("/")
    config = Path(__file__).resolve().parent / "config.yaml"
    if config.exists():
        host = port = None
        # Deliberately naive — this is two keys out of a small file we own, and
        # a YAML dependency for that would be the larger cost. But it tracks
        # WHICH BLOCK it is in: matching bare `host:`/`port:` anywhere meant a
        # `server.host` added later would silently repoint the MCP server at
        # something that is not the graph.
        section = None
        for line in config.read_text().splitlines():
            if line.strip() and not line.startswith((" ", "\t")):
                section = line.strip().rstrip(":")
                continue
            if section != "graph":
                continue
            key, _, value = line.strip().partition(":")
            # An inline `# comment` and surrounding quotes are both ordinary
            # YAML. Unstripped, `host: 127.0.0.1  # local` became a hostname
            # ending in "# local" and `port: "8080"` a port with quotes in it —
            # a URL the engine never answers, and no error saying why.
            value = value.split("#")[0].strip().strip("\"'")
            if key == "host":
                host = value
            elif key == "port":
                port = value
        if host and port:
            return f"http://{host}:{port}"
    return DEFAULT_URL


@dataclass
class Result:
    """Rows, or the reason there are none.

    An agent asking "which clearances are affected by this rule" must be able to
    tell an empty answer from a broken one. Returning a bare list makes those
    identical, and the wrong one of them is a fact about the world.
    """

    rows: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def run(cypher: str) -> Result:
    request = urllib.request.Request(
        engine_url() + "/api/query",
        data=json.dumps({"query": cypher}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        # `with`: an unclosed response holds its socket until the garbage
        # collector gets to it, and an MCP server is long-lived.
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # HTTPError subclasses URLError and must be caught first, or a 500 from
        # a running engine is reported as "no engine" — the one wrong diagnosis
        # that sends someone to restart a container that is already up.
        return Result(error=f"engine returned {exc.code}: {exc.read().decode()[:200]}")
    except urllib.error.URLError as exc:
        return Result(error=f"no engine at {engine_url()}: {exc.reason}")
    except (TimeoutError, OSError) as exc:
        return Result(error=f"engine unreachable: {exc}")

    if "error" in payload:
        # `payload['error'][:200]` assumes a string. A dict or a list slices to
        # something unreadable, and an int raises TypeError inside the error
        # path — the one place that must not fail.
        return Result(error=f"query rejected: {str(payload['error'])[:200]}")
    columns = payload.get("columns") or []
    return Result(rows=[dict(zip(columns, row)) for row in payload.get("records") or []])


def bounded(limit) -> int:
    """A LIMIT the engine will accept, or a reason it will not.

    `int(limit)` turned "ten" into a ValueError escaping to the caller, and
    `-5` into `LIMIT -5`, which the engine rejects with a message about the
    whole statement. An agent gets `Result(error=…)` for everything else in
    this module; a bad argument should not be the one case that raises.
    """
    try:
        value = int(limit)
    except (TypeError, ValueError):
        raise Unbounded(f"limit must be a whole number, got {limit!r}") from None
    if value < 1:
        raise Unbounded(f"limit must be at least 1, got {value}")
    return value


class Unbounded(Exception):
    """A limit this module will not send."""


def quoted(value) -> str:
    """A Cypher literal, via the encoder the loader already uses.

    **This used to escape with backslashes, which this engine does not have.**
    `\\'` is a parse error in 1.1.0, not an escaped quote — measured, and
    recorded in DATASET-CARD.md known issue 6 and in `etl/cypher.lit`. So
    `regulations_for_product("O'Brien")` did not inline safely, it produced a
    statement the engine rejects, surfaced to the agent as "query rejected".
    Fail-closed, but not what the code claimed. And `\\\\` did not double a
    backslash; it inserted two literal ones, silently changing the value
    matched.

    `etl.cypher.lit` chooses the quote style per value instead, which is the
    only thing that works against an engine with no escape sequences. It is a
    pure text function with no engine dependency, so importing it costs
    nothing — and two divergent literal encoders in one repo is how the second
    one drifts, which is exactly what happened here.

    **Everything reaching this function is forced to `str` first.** `lit()`
    emits numbers unquoted, correctly — but every value these tools compare
    against is stored as text, `device_class` included ("1", "2", "3" and "N",
    measured against the loaded graph). An int reaching `lit()` would render
    `2` and match nothing, silently.
    """
    return lit("" if value is None else str(value))


# ---------------------------------------------------------------------------
# the change-impact question — the one this graph exists for
# ---------------------------------------------------------------------------

def clearances_under_regulation(cfr_section: str, limit: int = 25) -> dict:
    """Every clearance governed by one 21 CFR section.

    The change-impact query: a rule changes, and this is what is affected. Two
    hops, because `regulation_number` is on the clearance record itself — an
    exact government-issued join, not a name match.

    cfr_section: e.g. "870.5150"
    """
    try:
        rows_wanted = bounded(limit)
    except Unbounded as exc:
        return {"cfr_section": cfr_section, "clearances": [], "count": 0,
                "error": str(exc)}

    result = run(
        f"MATCH (r:Regulation)<-[:GOVERNED_BY]-(p:ProductCode)"
        f"<-[:CLASSIFIED_AS]-(s:Submission) "
        f"WHERE r.cfr_section = {quoted(cfr_section)} "
        f"RETURN s.id AS clearance, s.device_name AS device, "
        f"s.applicant AS applicant, s.decision_date AS decided, "
        f"p.product_code AS product_code "
        f"ORDER BY s.decision_date DESC LIMIT {rows_wanted}"
    )
    return {"cfr_section": cfr_section, "clearances": result.rows,
            "count": len(result.rows), "error": result.error}


def count_clearances_under_regulation(cfr_section: str) -> dict:
    """How many clearances sit under one rule, without listing them.

    Separate from the listing tool because the count is the first thing asked
    and the list is capped — an agent reading `count: 25` off a limited list
    would understate the blast radius.
    """
    result = run(
        f"MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)"
        f"<-[:CLASSIFIED_AS]-(s:Submission) "
        f"WHERE r.cfr_section = {quoted(cfr_section)} "
        # DISTINCT for the same reason `busiest_regulations` uses it: a
        # Submission reaching this Regulation through two ProductCodes is one
        # clearance, not two. This is the figure an agent quotes as blast
        # radius, so it must not fan out.
        f"RETURN count(DISTINCT s) AS total"
    )
    total = result.rows[0].get("total") if result.rows else None
    return {"cfr_section": cfr_section, "total": total, "error": result.error}


def regulations_for_product(product_code: str) -> dict:
    """The rules a product code must comply with.

    `product_code` is the join hub — every openFDA endpoint carries it, and
    device *names* vary between endpoints, so a name is never the key.

    product_code: e.g. "DXY"
    """
    result = run(
        f"MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) "
        f"WHERE p.product_code = {quoted(product_code)} "
        f"RETURN r.cfr_section AS cfr_section, p.definition AS device_category, "
        f"p.device_class AS device_class, p.medical_specialty AS specialty"
    )
    return {"product_code": product_code, "regulations": result.rows,
            "count": len(result.rows), "error": result.error}


def regulation_for_clearance(k_number: str) -> dict:
    """Which rule governs one clearance — the reverse lookup.

    k_number: e.g. "K233820"
    """
    # `.upper()` ran before anything could return a Result, so a None or a
    # number reached an AttributeError instead of the error path every other
    # bad input in this module takes.
    if not isinstance(k_number, str):
        return {"k_number": k_number, "governed_by": [], "found": False,
                "error": f"k_number must be a string, got {type(k_number).__name__}"}
    k_number = k_number.upper()

    result = run(
        f"MATCH (s:Submission)-[:CLASSIFIED_AS]->(p:ProductCode)"
        f"-[:GOVERNED_BY]->(r:Regulation) "
        f"WHERE s.id = {quoted(k_number)} "
        f"RETURN s.device_name AS device, s.applicant AS applicant, "
        f"s.decision_date AS decided, p.product_code AS product_code, "
        f"p.device_class AS device_class, r.cfr_section AS cfr_section"
    )
    return {"k_number": k_number, "governed_by": result.rows,
            "found": bool(result.rows), "error": result.error}


# ---------------------------------------------------------------------------
# shape of the graph — what a reviewer asks before trusting an answer
# ---------------------------------------------------------------------------

def busiest_regulations(limit: int = 10) -> dict:
    """The rules carrying the most clearances — where a change hurts most."""
    try:
        rows_wanted = bounded(limit)
    except Unbounded as exc:
        return {"regulations": [], "count": 0, "error": str(exc)}

    result = run(
        f"MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)"
        f"<-[:CLASSIFIED_AS]-(s:Submission) "
        # `count(DISTINCT s)`, not `count(s)`: a Submission reaching one
        # Regulation through two ProductCodes would be counted twice. Measured
        # on the loaded graph — at most one ProductCode per Submission, and
        # zero regulations where the two counts differ — so this is correct by
        # construction rather than by a property of today's data.
        f"RETURN r.cfr_section AS cfr_section, count(DISTINCT s) AS clearances "
        f"ORDER BY clearances DESC LIMIT {rows_wanted}"
    )
    return {"regulations": result.rows, "count": len(result.rows),
            "error": result.error}


def clearances_by_advisory_committee(limit: int = 15) -> dict:
    """Clearances grouped by the FDA advisory committee that reviewed them.

    This replaces a `pending_by_authority` tool that could not be built.
    openFDA publishes *decided* clearances — there is no pending queue in the
    public data — so "pending by authority" has no answer. The advisory
    committee is the reviewing authority, and it is loaded, so this is the
    question that can actually be answered.
    """
    try:
        rows_wanted = bounded(limit)
    except Unbounded as exc:
        return {"committees": [], "count": 0, "error": str(exc)}

    result = run(
        f"MATCH (s:Submission) WHERE s.advisory_committee IS NOT NULL "
        f"RETURN s.advisory_committee AS committee, count(s) AS clearances "
        f"ORDER BY clearances DESC LIMIT {rows_wanted}"
    )
    return {"committees": result.rows, "count": len(result.rows),
            "error": result.error}


def product_codes_by_class(device_class: str, limit: int = 25) -> dict:
    """Device categories at one risk class.

    device_class: "1", "2" or "3" — Class III is the high-risk route.
    """
    try:
        rows_wanted = bounded(limit)
    except Unbounded as exc:
        return {"device_class": device_class, "product_codes": [], "count": 0,
                "error": str(exc)}

    result = run(
        f"MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) "
        f"WHERE p.device_class = {quoted(device_class)} "
        f"RETURN p.product_code AS product_code, p.definition AS device_category, "
        f"r.cfr_section AS cfr_section "
        # ORDER BY before LIMIT, or repeated calls return different subsets of
        # the same answer — the hazard `clearances_under_regulation` already
        # guards against, missing here.
        f"ORDER BY p.product_code LIMIT {rows_wanted}"
    )
    return {"device_class": device_class, "product_codes": result.rows,
            "count": len(result.rows), "error": result.error}


def graph_provenance() -> dict:
    """What is in this graph and where it came from.

    The first question a reviewer asks before trusting any answer above, and
    the one the demo opens with. Every node carries the openFDA endpoint it was
    built from.
    """
    totals = run("MATCH (n) RETURN count(n) AS nodes")
    # Early: the second query against an engine already known to be down is a
    # second round trip and a second timeout, for an answer we have.
    if not totals.ok:
        return {"nodes": None, "by_source": [], "error": totals.error}
    sourced = run("MATCH (n) WHERE n.source IS NOT NULL "
                  "RETURN n.source AS source, count(n) AS nodes "
                  "ORDER BY nodes DESC")
    return {"nodes": totals.rows[0].get("nodes") if totals.rows else None,
            "by_source": sourced.rows, "error": sourced.error}
