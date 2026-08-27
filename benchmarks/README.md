# Benchmarks

**`QUERY_RESULTS.md` is generated. Do not hand-edit it.**

`python -m benchmarks.run_queries` overwrites it on every run, so a manual
correction is silently lost — and it is the one file here where that would
happen quietly. If a figure on it is wrong, the query that produces it is
wrong; fix `queries.py`, `measure.py` or `run_queries.py` and re-run.

## Layout

| | |
|---|---|
| `queries.py` | the catalogue — nine questions, their Cypher, and the commentary |
| `measure.py` | talking to the engine: the request, the guards that refuse a graph this report would misdescribe, the timings, and the index comparison |
| `run_queries.py` | the rendering and the CLI |

`run_queries.py` was one file until it passed the 500-line review limit; review
skips a file over it, so an oversized module is an unread one.

Commentary in `queries.py` names measured figures with placeholders
(`{submissions:,}`) rather than typing them, so the page cannot go stale while
claiming it was measured this run. A placeholder naming a figure the run does
not measure is refused by name, and so is a literal brace — `{870.5150}` is an
ordinary thing to write on a page about CFR sections, and `str.format` reads
it as a positional field.

## This changes the graph, by default

The index comparison **creates indexes**, so a plain run changes the engine it
is pointed at — and changes it irreversibly for measurement, because the
unindexed timings cannot be taken again on that instance. The run says so on
stderr before it starts.

`--print` writes to stdout and skips the comparison, so it is a true dry run.
`--with-index-effect` asks for it anyway.


Nine queries against the loaded graph, with measured timings.
[`QUERY_RESULTS.md`](QUERY_RESULTS.md) is written by the runner — nothing on it
is typed in.

**Load from source.** There is no published snapshot — `README.md` and
`DATASET-CARD.md` both say so, and `data/` is not in the tree, so importing one
cannot be the primary path:

```bash
docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
python -m etl.download_openfda      # ~6 min
python -m etl.load_openfda          # ~10 min
python -m benchmarks.run_queries
```

Those two figures come from the root [`README.md`](../README.md), and the load
is the 598 seconds [`DATASET-CARD.md`](../DATASET-CARD.md) records for 53,811
statements at ~90/sec. This page used to say 4 and 16 minutes, which agreed
with neither.

Once a snapshot exists — a release is not cut yet — importing one is faster
than reloading:

```bash
curl -X POST http://localhost:8080/api/snapshot/import \
     -F "file=@data/regulatory-affairs.sgsnap"
```

**Run it against a fresh instance.** The index comparison below CREATES the
indexes it measures, so a second run on the same engine compares an indexed
lookup against an indexed lookup and reports a scan time that is not a scan.
The runner refuses that rather than printing it, but a fresh engine is the
only way to get the figures.

## What it found

The timings are the point of running it, but the most useful thing it measures
is not a timing: **does a uniqueness constraint also index the key?** If it does
not, every point lookup on a MERGE key scans the whole label.

This page used to answer that in prose — "it does not" — and the answer **had
stopped being true**. On the engine these containers run, a constraint produces
an index entry immediately, and a point lookup on a keyed label is fast on the
constraint alone, with a subsequent `CREATE INDEX` adding nothing.

Whatever the run establishes is on the generated page, not here — and on an
instance whose keys are already indexed, what that page reports is **dashes and
the reason for them**, not a speedup. There is no unindexed figure left to
measure once the constraints exist, so the comparison refuses rather than
timing one indexed lookup against another and calling the difference a speedup.
The unindexed figures need a fresh instance, and the page says so where the
dashes are.

Two things made that easy to miss. The claim was written once and never
re-checked, and the version it was scoped to was read off the **image tag** —
every container from `samyama-graph:1.1.0` reports engine **1.7.0** on
`/api/status`, so "1.1.0" named the tag and read as the engine.

So the runner **measures it every run** now, against a namespaced probe label,
and [`QUERY_RESULTS.md`](QUERY_RESULTS.md) reports whichever answer it finds
along with the version the engine reported. A finding that can go stale
silently is one this repo should not be carrying.

**They are not repeated here on purpose.** A measured number copied into a
second file drifts the moment the first is regenerated, and this table had
already drifted — it quoted a run whose scan times were a third lower than the
current ones. One source for a figure, and it is the one a program writes.

**#21 is the issue this retracts.** It was raised on the prose claim and it
reports a cost that nothing is paying: on a graph loaded through `etl/`, those
keys are indexed by their own constraints. Its table is a real measurement of a
`CREATE INDEX` against a label that had none — which is not the same question
as whether the constraint indexes the key. It should be closed against this run
rather than left open, and it is named here so it is not worked from in the
meantime. Its figures are not repeated here, for the reason in the paragraph
above.

## It refuses to run against the wrong graph

The queries are read-only and cannot damage anything. But a benchmark page is
only worth having if the graph it describes is the graph it names, and one
engine can hold two KGs — the instance used while writing this held the
regulatory graph *and* a drug-interactions graph, 273,279 nodes between them.

So the runner counts the labels first and stops if they do not account for every
node. That failure has no other symptom: the page would simply have been wrong.

## What the page does not claim

Nothing here has been run against another database, so **no comparison against
one appears**. The timings show the change-impact traversal is interactive on
this data, which is the property the demo depends on — not that it beats
anything.

The graph is a bounded slice, loaded so the demo starts quickly. These figures
say nothing about 31 million records.
