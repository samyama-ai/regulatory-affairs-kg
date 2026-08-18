# Demo

A narrated walkthrough of the loaded graph. Six questions, each one a single
traversal, on real FDA data.

```bash
docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0

# either load from source (~16 min) …
python -m etl.download_openfda
python -m etl.load_openfda

# … or, once a release exists, import the snapshot (0.54 s, measured).
# Download regulatory-affairs.sgsnap from the Releases page first —
# no release is published yet, so this route is not available.
curl -X POST http://localhost:8080/api/snapshot/import \
     -F "file=@regulatory-affairs.sgsnap"

python -m demo.demo
```

It waits for **Enter** between steps, so the pacing is yours while narrating.

## ⚠️ Never point the test suite at a demo engine

`SAMYAMA_URL=http://localhost:8080 pytest` **destroys a loaded graph**, silently.

The engine-backed tests create fixture nodes and clean up with `DETACH DELETE`.
That leaves the node count untouched — still 28,496 — while equality on every
MERGE-key property (`cfr_section`, `product_code`, `id`) stops matching. The
demo then runs, the header looks right, and question 2 returns nothing.

Two engine defects combine to cause it: `DETACH DELETE` does not clear property
columns, and `/api/query` ignores the `graph` field, so tests cannot be isolated
into their own tenant. Both are in [`../DATASET-CARD.md`](../DATASET-CARD.md).

**Run tests against their own engine on a different port.** The demo now
refuses to start on a graph in this state rather than presenting empty answers
to an audience — it asks the graph its own headline question before showing
anything, and exits 1 if the answer is empty.

**Every number it prints comes from the graph at run time.** Nothing is
hard-coded, so a bad load shows up in the demo rather than being papered over.

## What it walks through

| | Question | Why it is there |
|---|---|---|
| 1 | What is in this graph, and where did it come from? | Provenance first. Every node carries its FDA endpoint, method and retrieval date, so the graph proves this rather than a caption asserting it |
| 2 | 21 CFR 870.5150 is amended — who is affected? | The question the graph exists for |
| 3 | How many clearances under that one rule? | **415** — and the API returns the same number for the same filter, arrived at independently |
| 4 | Which cardiovascular rules carry the most clearances? | Where the regulatory weight actually sits |
| 5 | Take one device — what governs it? | `K233820`, the Fogarty catheter from the source research |
| 6 | Which categories are Class III? | The high-risk route; PMA rather than 510(k) |

It closes by saying what is **not** there: predicate chains are designed but not
loaded, because the predecessor device is not in the FDA's API — it exists only
inside the 510(k) Summary PDF, as free text, and the resolution rate is still
unmeasured.

## Recording

`regulatory-affairs.gif` and `.cast` **are committed** — the README shows the
demo working, and every sibling KG does the same. The snapshot is not: it is a
data artefact and ships on a release.

Regenerate after any change that alters what the demo prints:

```bash
asciinema rec --overwrite --cols 92 --rows 34 --idle-time-limit 2.0 \
  -c "python -m demo.demo" demo/regulatory-affairs.cast
agg demo/regulatory-affairs.cast demo/regulatory-affairs.gif
```

## Snapshot

`data/regulatory-affairs.sgsnap` — **2.2 MB**, 28,496 nodes, 25,310 edges,
`.sgsnap` v2. Produced by `POST /api/snapshot/export` after a full load.

Not committed either: `data/` is gitignored and raw data never enters a KG repo.
It belongs on a release, which is how the sibling KGs distribute theirs.
