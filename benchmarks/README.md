# Benchmarks

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

The timings are the point of running it, but the most useful thing it measured
was not a timing. **A uniqueness constraint in 1.1.0 declares the key and does
not index it**, so every point lookup on a MERGE key scans the whole label.

The speedup tracks label size, which is what a scan looks like — the figures
are in [`QUERY_RESULTS.md`](QUERY_RESULTS.md#a-uniqueness-constraint-does-not-create-an-index),
written by the runner.

**They are not repeated here on purpose.** A measured number copied into a
second file drifts the moment the first is regenerated, and this table had
already drifted — it quoted a run whose scan times were a third lower than the
current ones. One source for a figure, and it is the one a program writes.

Raised as **#21**; the demo, the MCP tools and the loader are all paying it.

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
