"""Measuring the graph: what it holds, and how long a query takes.

Split out of `benchmarks/run_queries.py` when that file passed the 500-line
review limit — review skips a file over it and blocks the PR, so an oversized
module is an unread one.

Split by SUBJECT, on the seam already there: everything here TALKS TO THE
ENGINE — the request, the shape guards that refuse a graph this report would
misdescribe, the per-query timing, and the index comparison. Nothing here
formats anything.

`run_queries.py` keeps the rendering and the CLI, and imports these.
"""


from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.request


URL = "http://localhost:8080"
ENGINE = "Samyama-Graph 1.1.0"

# The labels and edge types this graph is made of. A run against anything else
# is a run against a different graph.
EXPECTED_LABELS = {"Submission", "ProductCode", "Regulation"}
EXPECTED_EDGES = {"CLASSIFIED_AS", "GOVERNED_BY"}

REPEATS = 5     # median of five; a single timing on a warm cache is not a figure


def run(cypher: str, url: str) -> tuple[dict, float]:
    request = urllib.request.Request(
        url + "/api/query",
        data=json.dumps({"query": cypher}).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        # `with`: an unclosed response holds its socket until the garbage
        # collector gets to it, and this runs one request per repeat per query.
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # Caught before URLError, which it subclasses — otherwise a 500 from a
        # running engine reports as "no engine".
        raise SystemExit(f"engine returned {exc.code}: {exc.read().decode()[:200]}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"no engine at {url}: {exc.reason}")
    elapsed = (time.perf_counter() - started) * 1000
    if "error" in payload:
        # `str(...)` first. `payload['error'][:200]` assumes a string: a dict
        # or a list slices to something unreadable, and an int raises
        # TypeError inside the error path — the one place that must not fail.
        # `mcp_server/queries.py` already had this; the same payload, the same
        # engine, one file fixed and the other not.
        raise SystemExit(f"query rejected: {str(payload['error'])[:200]}\n  {cypher}")
    return payload, elapsed


def rows_of(payload: dict) -> list[dict]:
    columns = payload.get("columns") or []
    return [dict(zip(columns, row)) for row in payload.get("records") or []]


def shape(url: str) -> dict:
    """Counts per label and edge type, and a refusal if this is the wrong graph."""
    counts = {}
    for label in sorted(EXPECTED_LABELS):
        payload, _ = run(f"MATCH (n:{label}) RETURN count(n) AS c", url)
        counts[label] = rows_of(payload)[0]["c"]

    total, _ = run("MATCH (n) RETURN count(n) AS c", url)
    edges, _ = run("MATCH ()-[r]->() RETURN count(r) AS c", url)
    nodes = rows_of(total)[0]["c"]
    edge_total = rows_of(edges)[0]["c"]

    # Every expected label must be PRESENT, checked before the total.
    # The total on its own cannot see a missing label: a graph where
    # `Regulation` failed to load still sums to its own node count, and the
    # report would describe a graph with no law in it as healthy.
    empty = [label for label, count in counts.items() if not count]
    if empty:
        raise SystemExit(
            f"the engine holds no {', '.join(empty)} nodes at all. Either the "
            f"load did not finish or this is not the regulatory graph — "
            f"either way the figures below would describe something else.")

    # Then the total, in BOTH directions, and each one means something
    # different.
    #
    # `sum < nodes` is foreign nodes: another KG loaded alongside this one.
    #
    # `sum > nodes` means a node carries two expected labels and was counted
    # twice. This schema declares no such node, so it means the schema changed
    # or something else is loaded — and it is not cosmetic, because the
    # inflation can HIDE the first case. Measured on 1.1.0: a graph of one
    # Alpha, one Beta, one Alpha:Beta and one foreign node has 4 nodes and
    # sums to 4 over {Alpha, Beta}, so an equality test passes with a foreign
    # node present. Checking both directions is what closes that.
    #
    # The exact set — nodes carrying AT LEAST ONE expected label — is not
    # available: `MATCH (n) WHERE n:Alpha OR n:Beta` is a parse error in 1.1.0
    # (measured), and returning every node to count ids in Python would pull
    # 19,127 Submissions across the wire to answer a question about totals.
    labelled = sum(counts.values())
    if labelled < nodes:
        raise SystemExit(
            f"the engine holds {nodes:,} nodes but only {labelled:,} carry the "
            f"labels this graph is made of. Another KG is loaded alongside it, "
            f"and the statistics below would describe neither. Import the "
            f"snapshot into a fresh instance.")
    if labelled > nodes:
        raise SystemExit(
            f"the per-label counts sum to {labelled:,} against {nodes:,} nodes, "
            f"so {labelled - nodes:,} node(s) carry two of the labels this "
            f"graph is made of. This schema declares none, so either it changed "
            f"or another KG is loaded — and the double counting can hide a "
            f"foreign node, which is why this is refused rather than noted.")

    by_edge = {}
    for edge in sorted(EXPECTED_EDGES):
        payload, _ = run(f"MATCH ()-[r:{edge}]->() RETURN count(r) AS c", url)
        by_edge[edge] = rows_of(payload)[0]["c"]

    # Enforced, not merely declared. The same rationale as the label guard
    # above: an edge type this graph is made of returning zero means the wrong
    # snapshot is loaded, and every traversal figure below would describe a
    # graph nobody asked about. It was written down and never checked.
    empty = sorted(edge for edge, count in by_edge.items() if not count)
    if empty:
        raise SystemExit(
            f"the engine holds no {', '.join(empty)} edges, which this graph is "
            f"made of. The traversal timings below would be measuring nothing. "
            f"Import the snapshot into a fresh instance.")

    # Measured, not typed. The page says "nothing is typed in" and then carried
    # `4,487 of 7,085` as Python string literals, attributed to the provenance
    # query — which groups nodes by source endpoint and says nothing about
    # definitions. Correct today and stale on the next refresh, under a
    # sentence promising it was measured this run.
    blank, _ = run("MATCH (p:ProductCode) WHERE p.definition IS NULL "
                   "RETURN count(p) AS c", url)
    # The two figures the class-three commentary rests on. Its whole point is
    # that the population and the joinable subset differ by nearly four times,
    # so both have to be measured or the argument quotes itself.
    c3, _ = run("MATCH (p:ProductCode) WHERE p.device_class = '3' "
                "RETURN count(p) AS c", url)
    c3r, _ = run("MATCH (p:ProductCode)-[:GOVERNED_BY]->(:Regulation) "
                 "WHERE p.device_class = '3' RETURN count(DISTINCT p) AS c", url)
    return {"nodes": nodes, "edges": edge_total,
            "by_label": counts, "by_edge": by_edge,
            "product_codes": counts.get("ProductCode", 0),
            "blank_definitions": rows_of(blank)[0]["c"],
            "class_three": rows_of(c3)[0]["c"],
            "class_three_regulated": rows_of(c3r)[0]["c"]}


def measure(query: dict, url: str) -> dict:
    """Median of REPEATS, so one warm-cache reading is not reported as a figure.

    **This is round-trip time, not query time.** The clock starts before the
    HTTP request and stops after the JSON is decoded, so it includes connection
    setup, transfer and parsing. That is the honest thing to measure from a
    client — it is what a caller waits for — but it is not the engine's
    internal execution time, and the page says so rather than letting a reader
    take these as query latency.
    """
    timings, payload = [], None
    for _ in range(REPEATS):
        payload, elapsed = run(query["cypher"], url)
        timings.append(elapsed)
    return {**query, "rows": rows_of(payload),
            "median_ms": statistics.median(timings),
            "min_ms": min(timings), "max_ms": max(timings)}


# The MERGE keys, and a point lookup on each. Measured before and after an
# index, because a uniqueness constraint in 1.1.0 declares the key WITHOUT
# indexing it — so every lookup on a key scans the label.
KEYS = [
    ("Submission", "id", "MATCH (s:Submission) WHERE s.id = 'K233820' "
                         "RETURN s.device_name"),
    ("ProductCode", "product_code", "MATCH (p:ProductCode) "
                                    "WHERE p.product_code = 'DXY' RETURN p.definition"),
    ("Regulation", "cfr_section", "MATCH (r:Regulation) "
                                  "WHERE r.cfr_section = '870.5150' RETURN r.cfr_section"),
]


def existing_indexes(url: str) -> set[tuple[str, str]]:
    """`(label, property)` for every index the engine already holds.

    `SHOW INDEXES` parses in 1.1.0 — measured — and it is what makes the
    refusal below possible.
    """
    payload, _ = run("SHOW INDEXES", url)
    columns = payload.get("columns") or []
    # Validated, not assumed. If `SHOW INDEXES` ever renames its columns,
    # `row.get("label")` returns None for every row, the set holds
    # `(None, None)`, nothing clashes, and the guard below silently stops
    # guarding — reporting an indexed instance as though it were fresh.
    missing = [name for name in ("label", "property") if name not in columns]
    if missing:
        raise SystemExit(
            f"SHOW INDEXES no longer returns {missing} — it returns {columns}. "
            f"The already-indexed guard reads those columns, and without them "
            f"it would report an indexed instance as though it were fresh.")
    return {(row.get("label"), row.get("property")) for row in rows_of(payload)}


def index_effect(url: str, sizes: dict) -> list[dict]:
    """What a uniqueness constraint does not do.

    **This mutates the graph, and that makes it run-once.** It creates the
    indexes it measures, so on a SECOND run against the same instance the
    "before" figure is already an indexed lookup — and the table would report
    a scan time that is not a scan, with a speedup near 1, as though the index
    did nothing. Nothing about the output would say so.

    So the state is checked first and the measurement is REFUSED rather than
    reported wrongly. Re-running is a fresh instance, or nothing.

    Run last, because it creates indexes and every timing above should be the
    unindexed figure — which is what the shipped schema currently produces.
    """
    already = existing_indexes(url)

    def median(cypher: str) -> float:
        return statistics.median(run(cypher, url)[1] for _ in range(REPEATS))

    # Per key, not all-or-nothing. Returning only the clashes dropped every key
    # that was still measurable — so one stale index on one label silently
    # removed the other two rows from the report, and a reader saw a shorter
    # table with nothing saying why.
    measurable = [(label, prop, cypher) for label, prop, cypher in KEYS
                  if (label, prop) not in already]
    unmeasurable = [{"key": f"{label}.{prop}", "nodes": sizes.get(label, 0),
                     "scan_ms": None, "indexed_ms": None, "speedup": None,
                     "note": "already indexed — unindexed figure not measurable here"}
                    for label, prop, _ in KEYS if (label, prop) in already]
    if not measurable:
        return unmeasurable

    out = list(unmeasurable)
    # Keyed by (label, property), not by label. Two keys on the same label —
    # which the schema permits and this list will grow into — collapsed to
    # one entry, and the second reported the first's baseline as its own.
    before = {(label, prop): median(cypher)
              for label, prop, cypher in measurable}
    for label, prop, _ in measurable:
        # Re-creating an existing index is accepted by 1.1.0 rather than being
        # an error — measured — so this cannot abort a run on its own. The
        # guard above exists for the FIGURES, not for the statement.
        run(f"CREATE INDEX ON :{label}({prop})", url)
    for label, prop, cypher in measurable:
        after = median(cypher)
        out.append({"key": f"{label}.{prop}", "nodes": sizes.get(label, 0),
                    "scan_ms": before[(label, prop)], "indexed_ms": after,
                    "speedup": before[(label, prop)] / max(after, 0.01)})
    return out


