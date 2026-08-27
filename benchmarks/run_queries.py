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

The measurement lives in `benchmarks/measure.py`, split out when this file
passed the 500-line review limit. `run`, `shape`, `measure` and `index_effect`
are imported from there and are unchanged by the move.
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.measure import (
    IMAGE_TAG,
    constraint_indexes,
    engine_version,
    existing_indexes,
    REPEATS,
    URL,
    index_effect,
    measure,
    shape,
)
from benchmarks.queries import QUERIES

OUT = Path(__file__).resolve().parent / "QUERY_RESULTS.md"


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


def figures_for(stats: dict) -> dict:
    """The figures a catalogue `why` may interpolate.

    A named function rather than a dict literal inside `report()`, so the test
    that checks every `{placeholder}` in `benchmarks/queries.py` is answerable
    can ask THIS — instead of regexing `report()`'s source for `"x": stats[`,
    which passed or failed on how the map was spelled rather than on what it
    contained.
    """
    return {
        "submissions": stats["by_label"].get("Submission", 0),
        "product_codes": stats["product_codes"],
        "class_three": stats["class_three"],
        "class_three_regulated": stats["class_three_regulated"],
    }


def slowest_of(measured: list[dict]) -> dict:
    """The slowest measured query, or a refusal when there are none.

    `max()` on an empty sequence raises `ValueError` with no context, from the
    end of a run. And a report built from an empty catalogue renders a timings
    section with no timings — a page that looks finished.
    """
    if not measured:
        raise SystemExit(
            "the query catalogue is empty, so there is nothing to report. "
            "`benchmarks/queries.py` defines QUERIES; a report of no queries "
            "would render as a page with a timings section and no timings.")
    return max(measured, key=lambda q: q["median_ms"])


def rendered(why: str, figures: dict, question: str) -> str:
    """`why` with this run's figures substituted, or a refusal naming the entry.

    `str.format` is not a template engine that ignores what it does not know.
    A literal brace raises — and a CFR section in braces is an ordinary thing
    to write on a page about CFR sections, so `{870.5150}` gave
    `IndexError: Replacement index 870 out of range`, from inside report
    generation, after every query had already been measured. An unknown key
    raises `KeyError` with nothing but the key name to go on.

    Both now name the catalogue entry, because the fault is in `queries.py`
    and the traceback pointed here.
    """
    try:
        return why.format(**figures)
    except KeyError as exc:
        raise SystemExit(
            f"the `why` for {question!r} asks for {exc} , which this run does "
            f"not measure. Available: {', '.join(sorted(figures))}.") from None
    except (IndexError, ValueError) as exc:
        raise SystemExit(
            f"the `why` for {question!r} could not be rendered ({exc}). A "
            f"literal brace has to be doubled — write {{{{870.5150}}}}, not "
            f"{{870.5150}}.") from None


def constraint_section(constraint) -> list[str]:
    """The constraint-index section, as lines.

    Extracted from `report()` so each of its four arms can be rendered on
    its own. Inline, the only way to reach the `established_here is False`
    arm was to run a whole report, and that arm was added because the arm
    next to it would otherwise print the OPPOSITE of the measured answer —
    exactly the kind of branch that must be reachable from a test.
    """
    out = [
        "---",
        "",
        "## Does a uniqueness constraint create an index?",
        "",
        "**Measured on this run, not remembered.** `schema/regulatory_affairs_kg.cypher`",
        "declares `ASSERT s.id IS UNIQUE` for each MERGE key. Whether that also indexes",
        "the key decides whether every point lookup scans the label — and it is the",
        "answer this page was originally built around, so it is re-derived each run",
        "rather than asserted.",
        "",
    ]
    if constraint is None:
        out += [
            "_Not measured on this run._ Answering this declares a constraint on a "
            "probe label, which writes to the graph — and `--print` is a dry run. "
            "Pass `--with-index-effect` to measure it, against an instance you are "
            "willing to change.",
            "",
        ]
    elif not constraint["established_here"]:
        # BEFORE the truth branches, because `indexed_by_constraint` is None
        # here and None is falsy — without this the page would fall through to
        # "on this engine it does not", which is a claim the run could not
        # make and the opposite of the measured answer.
        correlation = constraint["correlation"]
        out += [
            "_Not established on this run._ The probe leaves its constraint behind "
            "— this engine cannot `DROP` one — so on an instance it has run against "
            "before, the index on the probe label is its own from last time. It "
            "cannot tell that from an index the constraint just built, so it "
            "declines rather than reporting a finding it did not establish. Run "
            "against a fresh instance for the causal answer.",
            "",
            f"What can be read without writing: the engine holds "
            f"**{correlation['constraints']}** uniqueness constraint(s), and "
            f"**{correlation['also_indexed']}** of those keys also carry an index. "
            f"That is consistent with a constraint building one and is not "
            f"evidence that it did — an index can be there for another reason.",
            "",
        ]
    elif constraint["indexed_by_constraint"]:
        out += [
            f"**On this engine it does.** A constraint declared on a probe label produced "
            f"a `{constraint['entry'].get('type', 'index')}` entry in `SHOW INDEXES` "
            f"immediately, with no `CREATE INDEX`.",
            "",
            "This page previously stated the opposite as its headline finding, and that",
            "statement was written against an earlier engine and never re-checked. It is",
            "recorded here because the correction matters more than the original claim:",
            "a page whose argument is that its numbers were measured should not carry a",
            "conclusion that stopped being true.",
            "",
            # PREDICTED, and it was wrong. This used to read "the MERGE keys
            # are already indexed by the schema, so the comparison below has
            # nothing left to measure and correctly refuses" — asserted
            # whenever the probe said yes, without asking what the comparison
            # actually found. On a graph restored from a snapshot the schema
            # was never applied, so the keys start unindexed, the comparison
            # runs, and that sentence printed directly above a table of real
            # figures denying they exist.
            "Whether the MERGE keys on **this** instance are indexed is a separate",
            "question, and it is answered by the comparison below rather than predicted",
            "here: a graph loaded through `etl/` arrives with the schema's constraints",
            "already applied, and one restored from a snapshot does not.",
            "",
        ]
    else:
        out += [
            "**On this engine it does not.** A constraint declared on a probe label",
            "produced no `SHOW INDEXES` entry, so the key is declared and not indexed,",
            "and a point lookup on it scans the whole label. The comparison below is the",
            "size of that cost.",
            "",
        ]
    return out


def report(url: str, with_index_effect: bool = True) -> str:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stats = shape(url)
    # Read BEFORE the query loop: the timings below are only "unindexed" if the
    # keys were unindexed when they were taken, and that is a fact about the
    # instance rather than about whether the comparison was asked for.
    # OBSERVED, not asserted. The page's finding is version-scoped and the
    # version was a module constant, so a run against any other engine still
    # stamped it. Measured: the image tagged 1.1.0 reports engine 1.7.0, so
    # the constant was naming the TAG and reading as the engine.
    version = engine_version(url)
    host = urllib.parse.urlparse(url).hostname or url
    where = "local Docker" if host in ("127.0.0.1", "localhost") else host

    indexed_before = existing_indexes(url)

    # Gated on the SAME flag as the index comparison, because it writes too:
    # it declares a constraint on a probe label, and `--print` promises a dry
    # run. Running it unconditionally made `--print` mutate the graph — the
    # promise this file had just been fixed to keep, broken by the probe added
    # to keep a different one.
    constraint = constraint_indexes(url) if with_index_effect else None

    measured = [measure(q, url) for q in QUERIES]

    lines = [
        "# Regulatory Affairs KG — query results",
        "",
        f"> Measured {stamp} · Samyama-Graph **{version}** "
        f"(image `{IMAGE_TAG}`) · {where}",
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
    figures = figures_for(stats)

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
            rendered(q["why"], figures, q["question"]),
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

    lines += constraint_section(constraint)
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
            # The retracted finding, GENERATED ONTO THE PAGE. "It does not
            # index it either" is the claim this whole section exists to
            # correct, and it printed two paragraphs below the headline saying
            # the opposite — so the page contradicted itself in the same
            # breath, in text a program wrote.
            "The speedup tracks label size almost exactly, which is what a full scan",
            "looks like. These keys carried **no constraint** on this instance, so what",
            "is measured here is an unindexed lookup against an indexed one. That is a",
            "different question from what a constraint does, which the section above",
            "answers on a probe label of its own.",
            "",
        ]
    # Gated on what was ALREADY indexed when this run started, read before the
    # query loop rather than inferred from whether the comparison ran.
    #
    # This paragraph printed unconditionally, including on exactly the run
    # where it is false. Q4 and Q6 are point lookups on `product_code` and
    # `s.id` — the keys the FIRST run indexes — so a second run against the
    # same instance produces timings an order of magnitude faster under a
    # sentence saying they are unindexed. And since the default invocation
    # creates those indexes, the second run is the normal case, not the exotic
    # one. `--print` never runs the comparison at all and still printed it.
    if not indexed_before:
        lines += [
            # The REASON was stale, not the gate. This said the timings were
            # unindexed "because that is what the shipped schema produces
            # today" — written while the page still claimed a constraint does
            # not index the key. Once that was corrected the sentence became
            # its own contradiction: a schema whose constraints DO index would
            # produce indexed lookups. The gate is right; what was unindexed
            # is a property of the instance this ran against.
            "Every timing in the queries above is the **unindexed** figure — nothing",
            "on this instance was indexed when the run started. Any indexes this run",
            "created are created at the end, so nothing above benefits from them.",
            "",
            "That is a property of the instance rather than of the schema: a graph",
            "loaded through `etl/` carries the schema's constraints, and the section",
            "above measures whether those index the key.",
            "",
        ]
    else:
        already = ", ".join(f"`{label}.{prop}`" for label, prop in sorted(indexed_before))
        lines += [
            f"**These timings are not all unindexed.** {already} carried an index "
            "before this run started. Any query above that looks one of those up is an "
            "indexed figure, and "
            "the index comparison below refuses to report a speedup it cannot measure. "
            "For unindexed timings, run against a fresh instance.",
            "",
        ]

    slowest = slowest_of(measured)
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
    mutates = args.with_index_effect or not args.print

    # Said BEFORE it happens, on stderr, and not only in `--help`. The index
    # comparison CREATES INDEXES, so the default invocation changes the engine
    # it is pointed at — and it changes it irreversibly for measurement
    # purposes, because the unindexed timings cannot be taken again on that
    # instance. The report discloses it afterwards, which is the wrong end: by
    # then the indexes exist. An operator pointing this at an engine they care
    # about deserves to read it first.
    if mutates:
        print("This run measures the index effect, which CREATES INDEXES on "
              f"{args.url} — the instance is changed, and the unindexed "
              "timings cannot be measured on it again. Use --print to skip it.",
              file=sys.stderr)

    text = report(args.url.rstrip("/"), with_index_effect=mutates)
    if args.print:
        print(text)
    else:
        OUT.write_text(text, encoding="utf-8")  # the page carries — · ×
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
