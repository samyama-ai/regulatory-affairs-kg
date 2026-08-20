# Benchmarks

Nine queries against the loaded graph, with measured timings.
[`QUERY_RESULTS.md`](QUERY_RESULTS.md) is written by the runner — nothing on it
is typed in.

```bash
docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
curl -X POST http://localhost:8080/api/snapshot/import \
     -F "file=@data/regulatory-affairs.sgsnap"
python -m benchmarks.run_queries
```

Without a snapshot, load from source first — `python -m etl.download_openfda`
then `python -m etl.load_openfda`, about 16 minutes.

## What it found

The timings are the point of running it, but the most useful thing it measured
was not a timing. **A uniqueness constraint in 1.1.0 declares the key and does
not index it**, so every point lookup on a MERGE key scans the whole label:

| Key | Nodes | Scan | Indexed | |
|---|---:|---:|---:|---:|
| `Submission.id` | 19,127 | 160.1 ms | 1.3 ms | **124×** |
| `ProductCode.product_code` | 7,085 | 65.6 ms | 1.2 ms | **55×** |
| `Regulation.cfr_section` | 2,284 | 19.7 ms | 1.2 ms | **16×** |

The speedup tracks label size, which is what a scan looks like. Raised as **#21**;
the demo, the MCP tools and the loader are all paying it.

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
