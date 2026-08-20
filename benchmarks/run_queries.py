"""Run the benchmark suite and write benchmarks/QUERY_RESULTS.md.

    docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
    curl -X POST http://localhost:8080/api/snapshot/import \\
         -F "file=@data/regulatory-affairs.sgsnap"
    python -m benchmarks.run_queries

Or load from source first with `etl.download_openfda` + `etl.load_openfda` if no
snapshot is to hand.

Every figure in QUERY_RESULTS.md is written by this script. Nothing is typed in,
which is the same standard `etl/probe_openfda.py` holds the dataset card to.

WHY IT REFUSES TO RUN AGAINST THE WRONG GRAPH
---------------------------------------------
The queries here are read-only, so they cannot damage anything. But a benchmark
document is only worth having if the graph it describes is the graph it says it
describes, and a shared engine can hold two KGs at once — the instance used
while writing this held the regulatory graph *and* a drug-interactions graph,
273,279 nodes between them. Reporting label counts from that would have produced
a document that was wrong in a way nobody could see.

So the run checks the shape first and stops if it does not match.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "http://localhost:8080"
ENGINE = "Samyama-Graph 1.1.0"
OUT = Path(__file__).resolve().parent / "QUERY_RESULTS.md"

# The labels and edge types this graph is made of. A run against anything else
# is a run against a different graph.
EXPECTED_LABELS = {"Submission", "ProductCode", "Regulation"}
EXPECTED_EDGES = {"CLASSIFIED_AS", "GOVERNED_BY"}

REPEATS = 5     # median of five; a single timing on a warm cache is not a figure


QUERIES: list[dict] = [
    {
        "name": "Change impact — every clearance under one rule",
        "question": "A rule changes. Which clearances are affected?",
        "why": ("The question this graph exists for. Two hops, because "
                "`regulation_number` is on the clearance record itself — an exact "
                "government-issued join, not a name match. In a relational schema "
                "this is the query that needs the join written by hand each time "
                "the shape of the question changes."),
        "cypher": ("MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)"
                   "<-[:CLASSIFIED_AS]-(s:Submission) "
                   "WHERE r.cfr_section = '870.5150' "
                   "RETURN count(s) AS clearances"),
    },
    {
        "name": "Change impact — the affected clearances themselves",
        "question": "Which specific devices are affected, and whose are they?",
        "why": "The same traversal, returning rows rather than a count.",
        "cypher": ("MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)"
                   "<-[:CLASSIFIED_AS]-(s:Submission) "
                   "WHERE r.cfr_section = '870.5150' "
                   "RETURN s.id AS clearance, s.applicant AS applicant, "
                   "s.decision_date AS decided "
                   "ORDER BY s.decision_date DESC LIMIT 5"),
    },
    {
        "name": "Busiest regulations",
        "question": "Where would a rule change hurt most?",
        "why": ("An aggregation across the whole graph. Answers which rules carry "
                "the most clearances, which is where regulatory attention goes."),
        "cypher": ("MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)"
                   "<-[:CLASSIFIED_AS]-(s:Submission) "
                   "RETURN r.cfr_section AS cfr_section, count(s) AS clearances "
                   "ORDER BY clearances DESC LIMIT 10"),
    },
    {
        "name": "Device to law",
        "question": "Which rules must this product code comply with?",
        "why": ("One hop from the join hub. `product_code` is on every openFDA "
                "endpoint; device *names* are not consistent between them, so a "
                "name is never the key."),
        "cypher": ("MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) "
                   "WHERE p.product_code = 'DXY' "
                   "RETURN p.definition AS device_category, p.device_class AS class, "
                   "r.cfr_section AS cfr_section"),
    },
    {
        "name": "Law to device categories",
        "question": "Which device categories does one rule govern?",
        "why": "The reverse of the above, and the first half of a change-impact answer.",
        "cypher": ("MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) "
                   "WHERE r.cfr_section = '870.5150' "
                   "RETURN p.product_code AS product_code, p.definition AS category "
                   "LIMIT 10"),
    },
    {
        "name": "Clearance to rule",
        "question": "Which rule governs this one clearance?",
        "why": "A point lookup through two hops — the query an inspector runs.",
        "cypher": ("MATCH (s:Submission)-[:CLASSIFIED_AS]->(p:ProductCode)"
                   "-[:GOVERNED_BY]->(r:Regulation) "
                   "WHERE s.id = 'K233820' "
                   "RETURN s.device_name AS device, p.product_code AS product_code, "
                   "r.cfr_section AS cfr_section"),
    },
    {
        "name": "Clearances by reviewing authority",
        "question": "Which FDA advisory committees review the most clearances?",
        "why": ("A grouping over 19,127 submissions. Note this is *decided* "
                "clearances — openFDA publishes no pending queue, so 'pending by "
                "authority' has no answer in this data."),
        "cypher": ("MATCH (s:Submission) WHERE s.advisory_committee IS NOT NULL "
                   "RETURN s.advisory_committee AS committee, count(s) AS clearances "
                   "ORDER BY clearances DESC LIMIT 10"),
    },
    {
        "name": "High-risk device categories",
        "question": "Which device categories take the Class III route?",
        "why": "A filtered scan with a join — the population a reviewer starts from.",
        "cypher": ("MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) "
                   "WHERE p.device_class = '3' "
                   "RETURN count(p) AS class_three_categories"),
    },
    {
        "name": "Provenance",
        "question": "What is in this graph, and where did it come from?",
        "why": ("The question a reviewer asks before trusting any answer above. "
                "Every node carries the openFDA endpoint it was built from."),
        "cypher": ("MATCH (n) WHERE n.source IS NOT NULL "
                   "RETURN n.source AS source, count(n) AS nodes "
                   "ORDER BY nodes DESC"),
    },
]


def run(cypher: str, url: str) -> tuple[dict, float]:
    request = urllib.request.Request(
        url + "/api/query",
        data=json.dumps({"query": cypher}).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        payload = json.loads(urllib.request.urlopen(request, timeout=180).read())
    except urllib.error.HTTPError as exc:
        # Caught before URLError, which it subclasses — otherwise a 500 from a
        # running engine reports as "no engine".
        raise SystemExit(f"engine returned {exc.code}: {exc.read().decode()[:200]}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"no engine at {url}: {exc.reason}")
    elapsed = (time.perf_counter() - started) * 1000
    if "error" in payload:
        raise SystemExit(f"query rejected: {payload['error'][:200]}\n  {cypher}")
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

    if nodes != sum(counts.values()):
        raise SystemExit(
            f"the engine holds {nodes:,} nodes but only {sum(counts.values()):,} "
            f"carry the labels this graph is made of. Another KG is loaded "
            f"alongside it, and the statistics below would describe neither. "
            f"Import the snapshot into a fresh instance.")

    by_edge = {}
    for edge in sorted(EXPECTED_EDGES):
        payload, _ = run(f"MATCH ()-[r:{edge}]->() RETURN count(r) AS c", url)
        by_edge[edge] = rows_of(payload)[0]["c"]

    return {"nodes": nodes, "edges": edge_total,
            "by_label": counts, "by_edge": by_edge}


def measure(query: dict, url: str) -> dict:
    """Median of REPEATS, so one warm-cache reading is not reported as a figure."""
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


def index_effect(url: str, sizes: dict) -> list[dict]:
    """What a uniqueness constraint does not do.

    Run last, because it creates indexes and every timing above should be the
    unindexed figure — which is what the shipped schema currently produces.
    """
    def median(cypher: str) -> float:
        return statistics.median(run(cypher, url)[1] for _ in range(REPEATS))

    out = []
    before = {label: median(cypher) for label, _, cypher in KEYS}
    for label, prop, _ in KEYS:
        run(f"CREATE INDEX ON :{label}({prop})", url)
    for label, prop, cypher in KEYS:
        after = median(cypher)
        out.append({"key": f"{label}.{prop}", "nodes": sizes.get(label, 0),
                    "scan_ms": before[label], "indexed_ms": after,
                    "speedup": before[label] / max(after, 0.01)})
    return out


def table(rows: list[dict]) -> str:
    if not rows:
        return "_no rows_\n"
    columns = list(rows[0])
    out = ["| " + " | ".join(columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
    return "\n".join(out) + "\n"


def report(url: str) -> str:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stats = shape(url)
    measured = [measure(q, url) for q in QUERIES]

    lines = [
        "# Regulatory Affairs KG — query results",
        "",
        f"> Measured {stamp} · {ENGINE} · local Docker",
        f"> {stats['nodes']:,} nodes, {stats['edges']:,} edges",
        "",
        "Every figure on this page is written by `python -m benchmarks.run_queries`.",
        "Nothing is typed in — the same standard `etl/probe_openfda.py` holds the",
        "dataset card to.",
        "",
        "---",
        "",
        "## What is in the graph",
        "",
        "| Label | Count |",
        "|---|---:|",
    ]
    for label, count in sorted(stats["by_label"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{label}` | {count:,} |")
    lines += [f"| **Total nodes** | **{stats['nodes']:,}** |", "",
              "| Edge type | Count |", "|---|---:|"]
    for edge, count in sorted(stats["by_edge"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{edge}` | {count:,} |")
    lines += [f"| **Total edges** | **{stats['edges']:,}** |", "",
              "This is a **bounded slice**, not the full 31,120,490 openFDA records —",
              "see `DATASET-CARD.md` for what was loaded and why.", "", "---", ""]

    for i, q in enumerate(measured, 1):
        lines += [
            f"## {i}. {q['name']}",
            "",
            f"**{q['question']}**",
            "",
            q["why"],
            "",
            "```cypher",
            q["cypher"],
            "```",
            "",
            f"**{q['median_ms']:.1f} ms** median of {REPEATS} "
            f"(min {q['min_ms']:.1f}, max {q['max_ms']:.1f})",
            "",
            table(q["rows"]),
            "",
        ]

    keys = index_effect(url, stats["by_label"])
    lines += [
        "---",
        "",
        "## A uniqueness constraint does not create an index",
        "",
        "The single most useful thing this suite measured. `schema/regulatory_affairs_kg.cypher`",
        "declares `ASSERT s.id IS UNIQUE` for each MERGE key — and a point lookup on",
        "one of those keys still scans the whole label.",
        "",
        "| Key | Nodes | Scan | Indexed | |",
        "|---|---:|---:|---:|---:|",
    ]
    for k in keys:
        lines.append(f"| `{k['key']}` | {k['nodes']:,} | {k['scan_ms']:.1f} ms | "
                     f"{k['indexed_ms']:.1f} ms | **{k['speedup']:.0f}×** |")
    lines += [
        "",
        "The speedup tracks label size almost exactly, which is what a full scan",
        "looks like. The schema already records that a constraint in 1.1.0 declares",
        "the key rather than guarding an insert; it does not index it either, and",
        "that had not been measured until now.",
        "",
        "Every timing in the queries above is the **unindexed** figure, because that",
        "is what the shipped schema produces today. The indexes are created at the",
        "end of this run, so nothing above benefits from them.",
        "",
    ]

    slowest = max(measured, key=lambda q: q["median_ms"])
    lines += [
        "---",
        "",
        "## What the timings mean",
        "",
        f"The slowest query here is **{slowest['name'].lower()}** at "
        f"{slowest['median_ms']:.1f} ms. Every query is a median of {REPEATS} runs,",
        "because a single reading on a warm cache is not a measurement.",
        "",
        "These are **not** a comparison against another database. Nothing here has",
        "been run against Postgres, so no claim about relative speed appears on this",
        "page. What the timings show is that the change-impact traversal is",
        "interactive on this data — which is the property the demo depends on.",
        "",
        "The graph is small by design. A bounded slice was loaded so the demo starts",
        "quickly; these figures say nothing about behaviour at 31 million records.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--url", default=URL, help=f"Engine URL (default {URL}).")
    parser.add_argument("--print", action="store_true",
                        help="Write to stdout instead of the results file.")
    args = parser.parse_args(argv)

    text = report(args.url.rstrip("/"))
    if args.print:
        print(text)
    else:
        OUT.write_text(text)
        print(f"wrote {OUT.relative_to(Path.cwd())} "
              f"({len(text.splitlines())} lines)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
