# Medical-Device Regulatory Affairs Knowledge Graph

**28,496 nodes. 25,310 edges. Every FDA device classification and every cardiovascular
clearance, joined to the law that governs each one.**

![Regulatory Affairs KG demo](demo/regulatory-affairs.gif)

> Part of the **Samyama** ecosystem — loaded into and queried via the graph engine at [samyama-ai/samyama-graph](https://github.com/samyama-ai/samyama-graph).
> This repo holds the loader and source-data specifics for the KG.

<a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-blue" alt="License"></a>

> **A bounded slice is loaded and measured — 28,496 nodes, 25,310 edges.** Predicate chains
> are designed but not loaded; the source data for them is not in the FDA's API. Counts,
> licences and known limitations are in [`DATASET-CARD.md`](DATASET-CARD.md).

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
// Change blast-radius: a 21 CFR section is amended — who is affected?
MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission)
WHERE r.cfr_section = '870.5150'
RETURN s.applicant AS applicant, count(s) AS clearances
ORDER BY clearances DESC LIMIT 5
```

| applicant | clearances |
|---|---:|
| Penumbra, Inc. | 27 |
| Inari Medical | 24 |
| Inari Medical, Inc. | 23 |
| Possis Medical, Inc. | 20 |
| Ekos Corp. | 17 |

**415 clearances** sit under that one rule — and the FDA's own API returns the same number for
the same filter, arrived at independently.

This works because **the FDA stamps the regulation number onto every clearance, approval and
classification record.** The device-to-law join is exact and government-issued, not a name match.

Note *Inari Medical* and *Inari Medical, Inc.* arriving separately — **applicant names are not
normalised at source and this graph does not normalise them either.** That is why the schema keys
on `product_code` and treats names as properties, never as keys. Entity resolution over applicants
is not modelled.

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
MHRA/UKCA. The scope decision and its open challenges are an internal design record.

## Data sources

Device-first, in three tiers. Full table with licences in [`DATASET-CARD.md`](DATASET-CARD.md);
measured live by `python -m etl.probe_openfda`.

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

[`schema/regulatory_affairs_kg.cypher`](schema/regulatory_affairs_kg.cypher) is the executable
ontology — **17 questions, 34 node labels, 39 edge types, 8 design decisions**, derived from the
questions a regulatory-affairs professional actually asks rather than from the shape of any one
source. Every statement executes against Samyama-Graph 1.1.0, and
[`tests/test_schema_cypher.py`](tests/test_schema_cypher.py) keeps it that way. The reasoning
behind each shape is inline in the cypher.

Two tiers: **loadable from public data today** — `Submission`, `ProductCode`, `Regulation`,
`Manufacturer`, `Establishment`, `MarketedDevice`, `Recall`, `AdverseEvent`, `PredicateClaim`,
`Standard` — and **modelled but deliberately unpopulated**: regulation text, EU market structures,
obligations, AI governance, cybersecurity, privacy and data quality. The second tier exists so the
ontology does not need redesigning when a source appears.

## Quick Start

**Needs Python 3.10+ and Docker.**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

docker run -d --name samyama-reg -p 8080:8080 \
  public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
until curl -sf http://localhost:8080/api/tenants >/dev/null; do sleep 1; done

python -m etl.probe_openfda          # measure the sources, live
python -m etl.download_openfda       # fetch into data/  (~6 min)
python -m etl.load_openfda           # build the graph   (~10 min)
pytest -rs                           # run tests; -rs shows anything skipped
python -m demo.demo                  # six questions, narrated
python -m mcp_server.server          # expose the KG over MCP — 8 tools

docker rm -f samyama-reg             # when you are done
```

`-rs` matters: the engine-backed tests skip silently without one running, and a
green run that skipped them proves nothing.

**They skip here on purpose.** The tests that write — probe nodes, schema
constraints, fixture rows, then `DETACH DELETE` — read `SAMYAMA_TEST_URL`,
which has no default, so they cannot reach the engine above. That engine is the
one this quick start just spent sixteen minutes loading, and a stray
`DETACH DELETE` in it does more than remove what it names (see
`DATASET-CARD.md` known issue 10). Run them against a throwaway:

```bash
docker run -d --rm --name samyama-test -p 8299:8080 \
  public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
SAMYAMA_TEST_URL=http://localhost:8299 pytest -rs
docker rm -f samyama-test
```

Port 8080 is refused outright even if you name it. In CI, add
`SAMYAMA_REQUIRE_ENGINE=1` so an unreachable engine fails the run instead of
skipping it — a guard that skips is indistinguishable from one that passes.

Some tests are marked `engine_limitation`: they pin a limitation the loader
works around and are EXPECTED to fail when the engine gains the ability, with
the docstring naming the workaround to drop. A failure there is good news, not
a defect in this repo — `pytest -m "not engine_limitation"` runs clean on any
build. The distinction matters because two builds reporting the same version
answer the same query differently (`DATASET-CARD.md` known issue 11), so "this
engine cannot do X" is not a property of a version number.

That loads **28,496 nodes and 25,310 edges** — all 7,085 device classifications plus every
21 CFR part 870 (cardiovascular) clearance. Then:

```cypher
MATCH (r:Regulation)<-[:GOVERNED_BY]-(p:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission)
WHERE r.cfr_section = '870.5150'
RETURN s.applicant AS applicant, count(s) AS clearances
ORDER BY clearances DESC LIMIT 5
```

| applicant | clearances |
|---|---:|
| Penumbra, Inc. | 27 |
| Inari Medical | 24 |
| Inari Medical, Inc. | 23 |
| Possis Medical, Inc. | 20 |
| Ekos Corp. | 17 |

**415 clearances** sit under that one rule.

Note *Inari Medical* and *Inari Medical, Inc.* arriving separately — **applicant names are not
normalised at source and this graph does not normalise them either.** That is why the schema keys
on `product_code` and treats names as properties, never as keys. Entity resolution over applicants
is not modelled.

## Structure
```
etl/          # openFDA probe, downloader and loader
schema/       # cypher schema / ontology
mcp_server/   # MCP server — queries.py holds the traversals, server.py the wiring
demo/         # narrated demo (cast + gif)
benchmarks/   # the query catalogue, the runner, and the page it writes
tests/        # pytest
pyproject.toml
```

## Status

| | |
|---|---|
| Repo scaffold | ✅ |
| Scope decided | ✅ |
| Source research | ✅ 8 endpoints, 31,120,490 records, measured live |
| Ontology | ✅ 17 questions, 34 labels, 39 edges |
| Schema verified against the engine | ✅ 36/36 statements, with a test |
| Downloader + loader | ✅ classifications + part 870 clearances |
| **Graph loaded** | ✅ **28,496 nodes, 25,310 edges** — a bounded slice |
| Predicate chains | ⬜ need PDF extraction; resolution rate unmeasured |
| **Demo** | ✅ six questions, sub-25 ms, every number read at run time |
| Snapshot | 🟢 **2,107,181 bytes** (2.11 MB) `.sgsnap` v2, published as [`snapshot-2026-08-24`](https://git.samyama.ai/Samyama.ai/regulatory-affairs-kg/releases) — imports in 0.54 s |
| Query suite | ✅ nine measured queries, and a page the runner writes |

## License

Apache 2.0.
