"""Run the benchmark suite and write benchmarks/QUERY_RESULTS.md.

    docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
    curl -X POST http://localhost:8080/api/snapshot/import \\
         -F "file=@data/regulatory-affairs.sgsnap"
    python -m benchmarks.run_queries

Or load from source first with `etl.download_openfda` + `etl.load_openfda` if no
snapshot is to hand.

Every figure in QUERY_RESULTS.md is written by this script. Nothing is typed in,
which is the standard `etl/probe_openfda.py` already holds the dataset card to.

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

from benchmarks.queries import QUERIES

URL = "http://localhost:8080"
ENGINE = "Samyama-Graph 1.1.0"
OUT = Path(__file__).resolve().parent / "QUERY_RESULTS.md"

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
    before = {label: median(cypher) for label, _, cypher in measurable}
    for label, prop, _ in measurable:
        # Re-creating an existing index is accepted by 1.1.0 rather than being
        # an error — measured — so this cannot abort a run on its own. The
        # guard above exists for the FIGURES, not for the statement.
        run(f"CREATE INDEX ON :{label}({prop})", url)
    for label, prop, cypher in measurable:
        after = median(cypher)
        out.append({"key": f"{label}.{prop}", "nodes": sizes.get(label, 0),
                    "scan_ms": before[label], "indexed_ms": after,
                    "speedup": before[label] / max(after, 0.01)})
    return out


def cell(value) -> str:
    """One markdown cell. A `|` in a value splits the row into extra columns
    and the table renders wrong from that line down — device names and
    definitions are free text from openFDA, so this is not hypothetical."""
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(rows: list[dict]) -> str:
    if not rows:
        return "_no rows_\n"
    # Every key across every row, in first-seen order. Taking the columns from
    # `rows[0]` alone silently DROPPED any field the first row happened not to
    # carry — and a row missing a key is exactly what a query with an optional
    # field returns.
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    out = ["| " + " | ".join(columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        out.append("| " + " | ".join(cell(row.get(c)) for c in columns) + " |")
    return "\n".join(out) + "\n"


def report(url: str, with_index_effect: bool = True) -> str:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stats = shape(url)
    measured = [measure(q, url) for q in QUERIES]

    lines = [
        "# Regulatory Affairs KG — query results",
        "",
        f"> Measured {stamp} · {ENGINE} · local Docker",
        f"> {stats['nodes']:,} nodes, {stats['edges']:,} edges",
        "",
        "**Timings are client round-trip, not engine execution time.** The clock",
        "starts before the HTTP request and stops after the JSON is decoded, so",
        "connection setup, transfer and parsing are all inside the figure. It is",
        "what a caller waits for, which is why it is the number reported — but it",
        "is not what the engine spends inside the query.",
        "",
        "Every figure on this page is written by `python -m benchmarks.run_queries`.",
        "Nothing is typed in. That is the same standard the dataset card is held",
        "to: every figure in [`../DATASET-CARD.md`](../DATASET-CARD.md) is written",
        "by `etl/probe_openfda.py` rather than being remembered.",
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
    # The percentage too. A share derived here and a count derived there is how
    # the two stop agreeing; and it is only meaningful when there is a
    # denominator, which a graph loaded with no ProductCodes would not have —
    # the label guard above already refuses that, but this does not depend on
    # it holding.
    # What the prose is allowed to name. Keyed, not positional, so a catalogue
    # entry naming a figure that is not measured fails loudly at render rather
    # than rendering a brace.
    figures = {
        "submissions": stats["by_label"].get("Submission", 0),
        "product_codes": stats["product_codes"],
        "class_three": stats["class_three"],
        "class_three_regulated": stats["class_three_regulated"],
    }

    total_codes = stats["product_codes"]
    share = (f"{stats['blank_definitions'] / total_codes:.0%}"
             if total_codes else "no product codes loaded")

    lines += [f"| **Total edges** | **{stats['edges']:,}** |", "",
              f"**A blank `definition` cell is missing source data, not a "
              f"parse failure.** {stats['blank_definitions']:,} of "
              f"{stats['product_codes']:,} product codes carry no definition "
              f"in openFDA — {share}, counted by this run.",
        "",
        "This is a **bounded slice**, not the full 31,120,490 openFDA records —",
              "see [`../DATASET-CARD.md`](../DATASET-CARD.md) for what was loaded "
              "and why.", "", "---", ""]

    for i, q in enumerate(measured, 1):
        lines += [
            f"## {i}. {q['name']}",
            "",
            f"**{q['question']}**",
            "",
            # `why` may name measured figures, and must not TYPE them — the
            # page's own claim is that nothing on it is typed in. Rendering it
            # through `format` lets the catalogue write `{submissions:,}` and
            # get this run's number, so a stale figure becomes impossible
            # rather than merely unlikely.
            q["why"].format(**figures),
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

    lines += [
        "---",
        "",
        "## A uniqueness constraint does not create an index",
        "",
        "The single most useful thing this suite measured. `schema/regulatory_affairs_kg.cypher`",
        "declares `ASSERT s.id IS UNIQUE` for each MERGE key — and a point lookup on",
        "one of those keys still scans the whole label.",
        "",
    ]
    # Bound before the branch: it is only assigned where the comparison runs,
    # and the prose gate below reads it on every path.
    measured_keys: list[dict] = []
    if not with_index_effect:
        lines += [
            "_Not measured on this run._ The comparison CREATES INDEXES, so it "
            "changes the instance it runs against — and `--print` reads as a dry "
            "run. Pass `--with-index-effect` to measure it, against an instance "
            "you are willing to change.",
            "",
        ]
    else:
        keys = index_effect(url, stats["by_label"])
        measured_keys = [k for k in keys if k.get("scan_ms") is not None]
        lines += [
            "| Key | Nodes | Scan | Indexed | Speedup |",
            "|---|---:|---:|---:|---:|",
        ]
        for k in keys:
            if k.get("scan_ms") is None:
                lines.append(f"| `{k['key']}` | {k['nodes']:,} | — | — | "
                             f"_{k['note']}_ |")
            else:
                lines.append(f"| `{k['key']}` | {k['nodes']:,} | {k['scan_ms']:.1f} ms | "
                             f"{k['indexed_ms']:.1f} ms | **{k['speedup']:.0f}×** |")
    lines += [""]
    # The claim only holds where something was measured. It was emitted
    # unconditionally, so a run that skipped the comparison — or found every
    # key already indexed — printed "the speedup tracks label size almost
    # exactly" above a table of dashes.
    if measured_keys:
        lines += [
            "The speedup tracks label size almost exactly, which is what a full scan",
            "looks like. The schema already records that a constraint in 1.1.0 declares",
            "the key rather than guarding an insert; it does not index it either, and",
            "that had not been measured until now.",
            "",
        ]
    lines += [
        "Every timing in the queries above is the **unindexed** figure, because that",
        "is what the shipped schema produces today. The indexes, where this run",
        "created any, are created at the end, so nothing above benefits from them.",
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
                        help="Write to stdout instead of the results file. Also "
                             "skips the index measurement, which writes to the "
                             "graph — see --with-index-effect.")
    parser.add_argument("--with-index-effect", action="store_true",
                        help="Run the index comparison even with --print. It "
                             "CREATES INDEXES, so the instance is changed and "
                             "the unindexed figures cannot be measured again.")
    args = parser.parse_args(argv)

    # `--print` reads as a dry run, so it does not mutate the graph unless the
    # index measurement is asked for explicitly.
    text = report(args.url.rstrip("/"),
                  with_index_effect=args.with_index_effect or not args.print)
    if args.print:
        print(text)
    else:
        OUT.write_text(text)
        # `relative_to` RAISES when the path is not under the cwd, so running
        # this from anywhere outside the repo crashed after the file was
        # already written — the work done, the report an exception.
        try:
            where = OUT.relative_to(Path.cwd())
        except ValueError:
            where = OUT
        print(f"wrote {where} ({len(text.splitlines())} lines)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
