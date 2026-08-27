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

The transport and the literal encoder live in `mcp_server/engine.py`, split out
when this file passed the 500-line review limit. `run`, `bounded`, `quoted` and
`Result` are imported from there and are unchanged by the move.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BeforeValidator

from mcp_server.engine import Unbounded, bounded, quoted, run


def _not_a_flag(value):
    """Refuse `true`/`false` before anything can turn it into a number.

    `bounded()` already refuses a bool, and on a direct Python call it does.
    Through the MCP server it never sees one: fastmcp builds a pydantic model
    from these signatures, and pydantic reads `True` as a valid `int` and
    hands `bounded()` a plain `1` (measured — `TypeAdapter(int)
    .validate_python(True)` returns `1`). So `limit=true` from an agent
    silently became a one-row page with `error: None`: an answer that looks
    like the end of the data rather than a rejected argument, which is the
    one outcome this module exists to prevent.

    Only the bool is refused here. Bounds and their messages stay in
    `bounded()`, so there is one place that decides what a limit may be
    rather than two that can disagree.
    """
    if isinstance(value, bool):
        raise ValueError(
            "limit must be a whole number, not true/false. `true` would be "
            "read as 1 and return a one-row page that reads like the end of "
            "the data.")
    return value


#: Every tool's `limit`. Named once so a tool added later cannot quietly get
#: the unguarded `int` that this annotation exists to replace.
Limit = Annotated[int, BeforeValidator(_not_a_flag)]


def clearances_under_regulation(cfr_section: str, limit: Limit = 25) -> dict:
    """Every clearance governed by one 21 CFR section.

    The change-impact query: a rule changes, and this is what is affected.

    **The join runs through the product code**, not through the clearance's own
    `regulation_number`. The text here used to claim the latter — "an exact
    government-issued join" — and the query never touched that property. Both
    are exact joins on government-issued codes, but they are different paths,
    and a clearance whose product code carries no `GOVERNED_BY` edge is
    invisible to this one.

    Measured on the loaded graph rather than assumed, **on 2026-08-27**: all
    19,127 Submissions are reachable this way, and for every one of them the
    regulation the traversal reaches is the same as the `regulation_number` on
    the record — **0 disagreements**. 902 ProductCodes carry no regulation
    edge, but no Submission classifies as one of them, which is why the gap is
    zero rather than small.

    That is a property of one LOAD, not of this query, so it is stated with a
    date and re-measured rather than carried forward. `tests/test_mcp_live.py
    ::test_no_loaded_clearance_names_a_rule_the_traversal_cannot_reach` runs it
    against whatever is loaded: the day a clearance arrives whose product code
    is unregulated, this tool starts understating blast radius and the suite
    says so instead of the answer quietly shrinking. On a graph with no loaded
    data that test reports itself unmeasured rather than passing — a zero
    orphan count out of a zero denominator was how it used to pass having read
    nothing.

    Returns `clearance`, `device`, `applicant`, `decided`, `product_code`;
    `count` is the number of rows RETURNED, capped by `limit` — use
    `count_clearances_under_regulation` for the total.

    cfr_section: e.g. "870.5150"
    limit: rows to return, 1..50,000, default 25. A bad value returns `error`
        rather than a silently rounded number.
    """
    # `quoted()` inside the try as well as `bounded()`. Both refuse an
    # argument this module will not send, both raise `Unbounded`, and an agent
    # gets `Result(error=…)` for everything else here — a refused argument must
    # not be the one case that raises into the caller.
    try:
        rows_wanted = bounded(limit)
        where = quoted(cfr_section)
    except Unbounded as exc:
        return {"cfr_section": cfr_section, "clearances": [], "count": 0,
                "error": str(exc)}

    result = run(
        f"MATCH (r:Regulation)<-[:GOVERNED_BY]-(p:ProductCode)"
        f"<-[:CLASSIFIED_AS]-(s:Submission) "
        f"WHERE r.cfr_section = {where} "
        # Deduplicated in the ENGINE, before the LIMIT.
        #
        # A Submission reaches a Regulation once per product code it
        # classifies as, so a device carrying two codes under one rule
        # produced two rows. Removing those in Python after `LIMIT` capped
        # the rows FIRST and dropped duplicates SECOND — so a page of 25
        # came back with fewer than 25 distinct clearances and nothing said
        # why. The caller cannot tell a short page from the end of the data.
        #
        # `RETURN DISTINCT` is a silent no-op on 1.1.0 (measured: two
        # identical rows go in, two come back), which is what sent the first
        # attempt into Python. `WITH DISTINCT` is not — measured on the same
        # engine, it collapses them. `min()` groups the same way and keeps a
        # product code in the row.
        #
        # `min(p.product_code)` rather than an arbitrary one: with several
        # codes the answer is the lowest, which is stable across calls.
        # Measured on the loaded graph, every Submission carries exactly one
        # (max fan-out 1, zero with more), so this is a guard rather than a
        # correction today.
        f"WITH s, min(p.product_code) AS product_code "
        f"RETURN s.id AS clearance, s.device_name AS device, "
        f"s.applicant AS applicant, s.decision_date AS decided, product_code "
        # Two keys, because `decision_date` is not unique: many clearances
        # share a date, so a single-key ORDER BY leaves LIMIT free to return a
        # different subset each call.
        f"ORDER BY s.decision_date DESC, s.id LIMIT {rows_wanted}"
    )
    clearances = result.rows

    return {"cfr_section": cfr_section, "clearances": clearances,
            "count": len(clearances), "error": result.error}


def count_clearances_under_regulation(cfr_section: str) -> dict:
    """How many clearances sit under one rule, without listing them.

    Separate from the listing tool because the count is the first thing asked
    and the list is capped — an agent reading `count: 25` off a limited list
    would understate the blast radius.
    """
    # Guarded for the same reason as the listing tool: a term `quoted()` will
    # not send must come back as an error, not as an exception.
    try:
        where = quoted(cfr_section)
    except Unbounded as exc:
        return {"cfr_section": cfr_section, "total": None, "error": str(exc)}

    result = run(
        f"MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)"
        f"<-[:CLASSIFIED_AS]-(s:Submission) "
        f"WHERE r.cfr_section = {where} "
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

    `found` has three values, and the third is the point of it: `True` the code
    exists, `False` the FDA publishes no such code, `None` the question was not
    answered — check `error`. An empty `regulations` list with `found: True` is
    a code that exists and carries no regulation number, which is a fact about
    the source and not a gap in the load.

    product_code: e.g. "DXY"
    """
    # OPTIONAL, so "this product code is unregulated" and "there is no such
    # product code" stop being the same empty list. 902 of the loaded product
    # codes carry no regulation number, so the first case is common — and an
    # agent told `[]` for both cannot act on either.
    try:
        where = quoted(product_code)
    except Unbounded as exc:
        return {"product_code": product_code, "regulations": [], "count": 0,
                "found": None, "error": str(exc)}

    result = run(
        f"MATCH (p:ProductCode) WHERE p.product_code = {where} "
        f"OPTIONAL MATCH (p)-[:GOVERNED_BY]->(r:Regulation) "
        f"RETURN r.cfr_section AS cfr_section, p.definition AS device_category, "
        f"p.device_class AS device_class, p.medical_specialty AS specialty"
    )
    regulated = [r for r in result.rows if r.get("cfr_section") is not None]
    # `found` is None when the query FAILED, not False.
    #
    # `bool(result.rows)` is False on an engine error too, so a failed call
    # answered `found: False, regulations: []` — which reads as "there is no
    # such product code", a statement about the FDA's catalogue, from a call
    # that never reached the graph. That is the third meaning in an empty
    # answer this module exists to keep out, arriving through the one field
    # added to keep it out.
    return {"product_code": product_code, "regulations": regulated,
            "count": len(regulated),
            "found": None if result.error else bool(result.rows),
            "error": result.error}


def regulation_for_clearance(k_number: str) -> dict:
    """Which rule governs one clearance — the reverse lookup.

    `found` has three values, same as `regulations_for_product`: `True` the
    clearance exists, `False` the FDA published no such k-number, `None` the
    question was not answered — check `error`. A row with a null `cfr_section`
    is a clearance that exists whose product code carries no regulation.

    k_number: e.g. "K233820"
    """
    # `.upper()` ran before anything could return a Result, so a None or a
    # number reached an AttributeError instead of the error path every other
    # bad input in this module takes.
    if not isinstance(k_number, str):
        return {"k_number": k_number, "governed_by": [], "found": None,
                "error": f"k_number must be a string, got {type(k_number).__name__}"}
    k_number = k_number.upper()

    # Same reason as `regulations_for_product`: the inner three-hop chain made
    # a real clearance whose product code is unregulated indistinguishable from
    # a k-number that does not exist. `found` now means the CLEARANCE was
    # found; a null `cfr_section` means it exists and its category carries no
    # regulation number; `None` means the question was not answered at all.
    try:
        where = quoted(k_number)
    except Unbounded as exc:
        return {"k_number": k_number, "governed_by": [], "found": None,
                "error": str(exc)}

    result = run(
        f"MATCH (s:Submission) WHERE s.id = {where} "
        f"OPTIONAL MATCH (s)-[:CLASSIFIED_AS]->(p:ProductCode)"
        f"-[:GOVERNED_BY]->(r:Regulation) "
        f"RETURN s.device_name AS device, s.applicant AS applicant, "
        f"s.decision_date AS decided, p.product_code AS product_code, "
        f"p.device_class AS device_class, r.cfr_section AS cfr_section"
    )
    # `found` is None on a failure, for the same reason as
    # `regulations_for_product`: `bool(result.rows)` is False when the engine
    # never answered, and `found: False` is a claim about the FDA's catalogue.
    return {"k_number": k_number, "governed_by": result.rows,
            "found": None if result.error else bool(result.rows),
            "error": result.error}


# ---------------------------------------------------------------------------
# shape of the graph — what a reviewer asks before trusting an answer
# ---------------------------------------------------------------------------

def busiest_regulations(limit: Limit = 10) -> dict:
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
        f"ORDER BY clearances DESC, r.cfr_section LIMIT {rows_wanted}"
    )
    return {"regulations": result.rows, "count": len(result.rows),
            "error": result.error}


def clearances_by_advisory_committee(limit: Limit = 15) -> dict:
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
        # Secondary key: counts tie, and a single-key ORDER BY leaves LIMIT
        # free to return a different subset on each call.
        f"ORDER BY clearances DESC, s.advisory_committee LIMIT {rows_wanted}"
    )
    return {"committees": result.rows, "count": len(result.rows),
            "error": result.error}


def product_codes_by_class(device_class: str, limit: Limit = 25) -> dict:
    """Device categories at one risk class.

    device_class: measured values are "1", "2", "3", "N", "U" and "f" — not
    the three the first version of this docstring named. Class III is the
    high-risk route.

    **OPTIONAL MATCH, because the inner join was dropping most of the answer.**
    `(p)-[:GOVERNED_BY]->(r)` returns only product codes that carry a
    regulation, and for the class this tool exists to answer about, most do
    not. Measured against the loaded graph:

        class   product codes   with a regulation   dropped
        1               2,401               2,399         2
        2               3,633               3,628         5
        3                 531                 145       386
        N                 383                   1       382

    Class III lost 73% of its answer, silently, under a count the caller reads
    as complete. The benchmark suite already found this and renamed its
    own query `class_three_with_a_regulation` to say so; this tool kept the
    join and the misleading name.

    `cfr_section` is None where the FDA publishes no regulation number for a
    product code, which is a fact about the source rather than a gap in the
    load — `unregulated_in_page` counts them so the caller need not, and it
    counts them IN THE PAGE rather than in the class, which is why it is named
    that way: with a limit of 25 it says nothing about the rest.
    """
    try:
        rows_wanted = bounded(limit)
        where = quoted(device_class)
    except Unbounded as exc:
        # `rows_returned` on BOTH paths. The rename reached the success path
        # and not this one, so a caller reading one key got `None` on the
        # other — and the error path is where a caller is least able to guess.
        return {"device_class": device_class, "product_codes": [],
                "rows_returned": 0, "unregulated_in_page": 0,
                "error": str(exc)}

    result = run(
        f"MATCH (p:ProductCode) WHERE p.device_class = {where} "
        f"OPTIONAL MATCH (p)-[:GOVERNED_BY]->(r:Regulation) "
        f"RETURN p.product_code AS product_code, p.definition AS device_category, "
        f"r.cfr_section AS cfr_section "
        # ORDER BY before LIMIT, or repeated calls return different subsets of
        # the same answer — the hazard `clearances_under_regulation` already
        # guards against, missing here.
        f"ORDER BY p.product_code LIMIT {rows_wanted}"
    )
    return {"device_class": device_class, "product_codes": result.rows,
            # `rows_returned`, not `count`: this is the size of the PAGE, and
            # the docstring's table is about whole classes. With the default
            # limit of 25, a `count` of 25 says nothing about the class.
            "rows_returned": len(result.rows),
            "unregulated_in_page": sum(1 for r in result.rows if r.get("cfr_section") is None),
            "error": result.error}


# The labels this dataset is made of. Counted individually, because
# `MATCH (n) RETURN count(n)` counts THE STORE and not this graph — see below.
DATASET_LABELS = ("Submission", "ProductCode", "Regulation")


def graph_provenance() -> dict:
    """What is in this graph and where it came from.

    The first question a reviewer asks before trusting any answer above, and
    the one the demo opens with.

    **`nodes_in_store` is not this dataset.** It used to be reported as
    `nodes`, from `MATCH (n) RETURN count(n)` — which counts everything the
    engine holds. Pointed at an engine carrying an unrelated KG, this tool
    reported **17,168 nodes** for a store with zero Submissions, zero
    ProductCodes and zero Regulations, and the only hint was an empty
    `by_source`. That is the one tool whose job is telling a reviewer what the
    graph contains before they trust anything else.

    Sending `graph` in the payload would not fix it: DATASET-CARD.md known
    issue 7 records that 1.1.0 ignores the field. So the fix is to count what
    this dataset is made of, by label, and to report the store total beside it
    under a name that says what it is. An unrelated store now reads as zeros
    with a non-zero `nodes_in_store`, which is the honest shape of "you have
    pointed me at something else".

    `nodes` is the SUM of the per-label counts, so a node carrying two of
    these labels counts twice. This schema declares none, and the engine
    cannot answer `WHERE n:A OR n:B` (parse error, measured), so the sum is
    the closest available figure rather than the exact one — said here rather
    than left for a reader to discover from `by_label` not adding up.

    Returns `by_label`, `nodes` (this dataset), `nodes_in_store` (everything
    the engine holds), `by_source`, and `error`. On any failure every figure
    is `None` rather than partly filled.
    """
    totals = run("MATCH (n) RETURN count(n) AS nodes")
    # Early: the second query against an engine already known to be down is a
    # second round trip and a second timeout, for an answer we have.
    if not totals.ok:
        return {"nodes": None, "nodes_in_store": None, "by_label": [],
                "by_source": [], "error": totals.error}

    by_label, ours = [], 0
    for label in DATASET_LABELS:
        counted = run(f"MATCH (n:{label}) RETURN count(n) AS nodes")
        if not counted.ok:
            return {"nodes": None, "nodes_in_store": None, "by_label": [],
                    "by_source": [], "error": counted.error}
        held = counted.rows[0].get("nodes", 0) if counted.rows else 0
        by_label.append({"label": label, "nodes": held})
        ours += held

    sourced = run("MATCH (n) WHERE n.source IS NOT NULL "
                  "RETURN n.source AS source, count(n) AS nodes "
                  "ORDER BY nodes DESC")
    if not sourced.ok:
        # Counts alongside a non-null error read as "here is the answer, and
        # also something went wrong" — and a caller that checks `error` last
        # has already used the figures. If any part failed, nothing is
        # reported.
        return {"nodes": None, "nodes_in_store": None, "by_label": [],
                "by_source": [], "error": sourced.error}

    return {"nodes": ours,
            "nodes_in_store": totals.rows[0].get("nodes") if totals.rows else None,
            "by_label": by_label,
            "by_source": sourced.rows, "error": None}
