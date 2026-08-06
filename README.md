# Medical-Device Regulatory Affairs Knowledge Graph

**Device regulation as a graph — submissions, clearances, predicate chains, obligation text,
conformity evidence, market registrations and post-market surveillance.**

> Part of the **Samyama** ecosystem — loaded into and queried via the graph engine at [samyama-ai/samyama-graph](https://github.com/samyama-ai/samyama-graph).
> This repo holds the loader and source-data specifics for the KG.

<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-blue" alt="License"></a>

> 🚧 **Spec in progress.** Scope is decided; source research and the ontology land next.
> Start with [`docs/scope.md`](docs/scope.md), then
> [`docs/regulatory-affairs-kg-plan.md`](docs/regulatory-affairs-kg-plan.md) and
> [`DATASET-CARD.md`](DATASET-CARD.md).

---

When a regulation or a recognised standard changes, a manufacturer has to answer one question
fast:

> *"Which of our devices, technical files, notified-body certificates, market registrations and
> open post-market obligations are affected — and by when?"*

Today that is a manual exercise across spreadsheets, documents and email. Every fact needed to
answer it is already public — FDA clearances, device classifications, recalls, adverse events,
the text of 21 CFR and EU MDR, notified-body certificates — but it is scattered across a dozen
systems that don't reference each other.

As a graph it is a single traversal.

```cypher
-- planned signature query: change blast-radius
MATCH (c:Clause)<-[:GOVERNED_BY]-(d:Device)-[:REGISTERED_IN]->(j:Jurisdiction)
WHERE c.id = $changed_clause
RETURN j.name AS market, count(DISTINCT d) AS devices_affected
ORDER BY devices_affected DESC
```

This is the same shape as the change-impact query in
[`bank-model-risk-kg`](https://github.com/samyama-ai/bank-model-risk-kg) — *"if this data source
changes, which regulatory submissions are exposed?"* — applied to device regulation.

## Why a graph

Device regulation has unusually strong identifiers — 510(k) K-numbers, PMA numbers, FDA product
codes, UDI-DIs, EUDAMED identifiers. Joins across sources are **exact rather than name-based**,
which makes federation reliable instead of approximate.

It also contains a structure that is natively a graph and poorly served elsewhere: **510(k)
predicate chains.** Each clearance cites the predicate device it claims substantial equivalence
to, forming a public citation DAG stretching back decades.

## Scope

**Medical devices** — not pharmaceuticals, not financial regulation.
**Primary:** US (FDA) and EU (MDR/IVDR). **Secondary:** ANVISA, CDSCO, Health Canada, TGA,
MHRA/UKCA. Reasoning and open challenges in [`docs/scope.md`](docs/scope.md).

## Data sources

Device-first, in three tiers. Full table with licences in [`DATASET-CARD.md`](DATASET-CARD.md);
per-source detail in [`docs/sources/`](docs/sources/).

- **Tier 1** — openFDA device endpoints (510(k), PMA, classification, registration & listing,
  recalls, enforcement, MAUDE, UDI); 21 CFR Parts 800–898 via eCFR; FDA guidance and warning
  letters
- **Tier 2** — EU MDR 2017/745 and IVDR 2017/746 via EUR-Lex, Annex I GSPR, MDCG guidance,
  EUDAMED, NANDO notified bodies, recognised consensus standards
- **Tier 3** — ANVISA, CDSCO, Health Canada, TGA, MHRA/UKCA

Licensing is tracked per source. Notably, **ISO/IEC standard texts are paywalled** and are
modelled as identifier and clause-reference nodes only — no standard text is ingested. Per the
KG-repo convention, raw downloaded data is not committed; this repo ships loaders, schema and a
bounded demo.

## Schema

The ontology is drafted in
[`docs/regulatory-affairs-kg-plan.md`](docs/regulatory-affairs-kg-plan.md) and lands in
[`docs/schema.md`](docs/schema.md) and
[`schema/regulatory_affairs_kg.cypher`](schema/regulatory_affairs_kg.cypher) once source research
confirms what the data actually supports. Both currently hold the generic placeholder ontology
from the scaffold.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

python -m etl.download_data          # fetch source data into data/
python -m etl.loader                 # build + load the graph
python -m mcp_server.server          # expose the KG over MCP
pytest                               # run tests
```

## Structure
```
etl/          # downloaders + graph loader
schema/       # cypher schema / ontology
mcp_server/   # MCP server exposing the KG
demo/         # narrated demo (cast + gif)
benchmarks/   # benchmark queries
docs/         # design + source notes
tests/        # pytest
pyproject.toml
```

## Status

| | |
|---|---|
| Repo scaffold | ✅ |
| Scope decided | ✅ |
| Spec | 🚧 outline in place, content in progress |
| Data-source list | 🚧 sources identified, counts pending |
| Loaders | ⬜ stubs only |
| Query suite | ⬜ not started |

## License

Apache 2.0.
