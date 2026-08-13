# Medical-Device Regulatory Affairs Knowledge Graph

**Device regulation as a graph — clearances, predicate chains, the law that governs each device,
manufacturers and facilities, recalls and adverse events.**

> Part of the **Samyama** ecosystem — loaded into and queried via the graph engine at
> [samyama-ai/samyama-graph](https://github.com/samyama-ai/samyama-graph).

<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-blue" alt="License"></a>

> 🚧 **Sources measured, schema designed and engine-verified, not yet loaded.**
> Node and edge counts do not exist until a loader runs, and this repo does not
> publish numbers it has not measured. See [`DATASET-CARD.md`](DATASET-CARD.md).

---

When a regulation or a recognised standard changes, a manufacturer has to answer one question
fast:

> *"Which of our devices, technical files, certificates, market registrations and open post-market
> obligations are affected — and by when?"*

Today that is manual work across spreadsheets, documents and email. Every fact needed to answer it
is already public — FDA clearances, device classifications, recalls, adverse events, the text of
21 CFR — but it is scattered across sources that do not reference each other.

As a graph it is one traversal:

```cypher
// Change blast-radius: a 21 CFR section is amended — who is affected?
MATCH (r:Regulation)<-[:GOVERNED_BY]-(p:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission)
      -[:SUBMITTED_BY]->(m:Manufacturer)
WHERE r.cfr_section = '870.5150'
RETURN m.name AS manufacturer, count(DISTINCT s) AS clearances_affected
ORDER BY clearances_affected DESC
```

**This works because the FDA stamps the regulation number onto every clearance, approval and
classification record.** The device-to-law join is exact and government-issued, not a name match.

*Results table lands with the loader — this repo does not print numbers it has not run.*

## Why a graph

Device regulation has unusually strong identifiers — 510(k) K-numbers, PMA numbers, FDA product
codes, UDI-DIs. Joins across sources are **exact rather than name-based**.

It also contains a structure that is natively a graph and poorly served elsewhere: **510(k)
predicate chains.** Every US clearance names the older device it claims substantial equivalence
to, forming a public citation chain back to the 1976 Medical Device Amendments.

Chains of unknown depth are what graph databases do well and relational databases do badly — you
cannot write "keep going until the beginning" as a fixed join.

**Those chains are partial by construction, and this repo says so.** The predicate device is not
published in the FDA's API; it appears only inside the 510(k) Summary PDF, as a free-text device
name, and sometimes names a pre-1976 device with no clearance record at all. Unresolved
predecessors are modelled explicitly rather than dropped — a truncated chain that looks complete
is worse than no chain.

## Scope

**Medical devices** — not pharmaceuticals, not financial regulation.
**Primary:** US (FDA) and EU (MDR/IVDR). **Secondary:** ANVISA, CDSCO, Health Canada, TGA,
MHRA/UKCA.

## Data sources

**8 openFDA device endpoints, 31,120,490 records**, all US-government public domain — measured
live, never hand-entered. Reproduce with `python -m etl.probe_openfda`.

**Which number to quote.** 81.5% of the total is MAUDE adverse-event reports, so 31 million
overstates graph-relevant scale. And 5,752,329 — the obvious next figure — is **88.4% UDI**,
which does not reliably join to clearances (the FDA publishes no such link, so we do not assert
one).

The authorisation and oversight record — clearances, approvals, classification, registrations,
recalls and enforcement — is **668,381**. That is the figure for the regulatory backbone.

Full table with per-source licences in [`DATASET-CARD.md`](DATASET-CARD.md).

- **Tier 1** — openFDA (510(k), PMA, classification, registration & listing, recalls, enforcement,
  MAUDE, UDI) plus the 510(k) Summary PDFs
- **Tier 2** — 21 CFR via eCFR, FDA guidance and warning letters, EU MDR/IVDR via EUR-Lex,
  EUDAMED, NANDO, recognised consensus standards
- **Tier 3** — ANVISA, CDSCO, Health Canada, TGA, MHRA/UKCA

**ISO/IEC standard texts are paywalled** and are modelled as identifier and clause-reference nodes
only — no standard text is ingested. Per the KG-repo convention, raw downloaded data is not
committed; this repo ships downloaders, loaders, schema and a bounded demo.

## Schema

[`schema/regulatory_affairs_kg.cypher`](schema/regulatory_affairs_kg.cypher) — the executable
ontology. **17 questions, 34 node labels, 39 edge types, 8 design decisions.**

Derived from the questions a regulatory-affairs professional actually asks, in their language, not
from the shape of any one source. Every node and edge exists because it turns one of those
questions into a single traversal; anything serving none of them was left out.

Two tiers: **loadable from public data today** (`Submission`, `ProductCode`, `Regulation`,
`Manufacturer`, `Establishment`, `MarketedDevice`, `Recall`, `AdverseEvent`, `PredicateClaim`,
`Standard`) and **modelled but deliberately unpopulated** — regulation text, EU market structures,
obligations, AI governance, cybersecurity, privacy and data quality. The second tier is designed
so the ontology does not need redesigning when a source appears.

Every statement executes against Samyama-Graph 1.1.0, and
[`tests/test_schema_cypher.py`](tests/test_schema_cypher.py) keeps it that way.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

python -m etl.probe_openfda          # measure the sources, live
pytest                               # run tests
```

Verify the schema against a live engine:

```bash
docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
pytest tests/test_schema_cypher.py
```

Downloaders and loaders are stubs; `python -m etl.download_data` and `python -m etl.loader` do
nothing yet.

## Structure

```
etl/          # source probe (working); downloaders + loader (stubs)
schema/       # the executable ontology
mcp_server/   # MCP server exposing the KG (scaffold — nothing to run yet)
demo/         # narrated demo (not started)
benchmarks/   # benchmark queries (not started)
tests/        # pytest
```

## Status

| | |
|---|---|
| Scope decided | ✅ |
| Source research | ✅ 8 endpoints measured live |
| Ontology | ✅ 17 questions, 34 labels, 39 edges |
| Schema verified against the engine | ✅ 36/36 statements, with a test |
| Downloaders / loaders | ⬜ stubs only |
| Data loaded | ⬜ none — no node or edge counts exist |
| Query suite, snapshot, demo | ⬜ not started |

## License

Apache 2.0.
