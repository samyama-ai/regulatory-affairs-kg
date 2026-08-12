# Dataset Card — Medical-Device Regulatory Affairs KG

> **Status: sources measured, schema designed, not yet loaded.** Every openFDA figure below is a
> live measurement. Node and edge counts are deliberately absent — they do not exist until a
> loader runs, and this repo does not publish numbers it has not measured.

A knowledge graph of **medical-device regulatory affairs**, combining public sources — clearances
and approvals, device classification and the regulation governing it, manufacturers and their
registered facilities, post-market surveillance, and the predicate lineage published in 510(k)
Summary PDFs — into one queryable graph.

The problem it addresses: when a rule or a consensus standard changes, a manufacturer must work
out which devices, technical files, certificates, registrations and post-market obligations are
affected, and by when. Today that is manual work across spreadsheets and email. As a graph it is
one traversal.

The structure that makes it graph-native is the **510(k) predicate chain** — every US clearance
names the older device it claims substantial equivalence to, forming a public citation chain back
to 1976. Chains of unknown depth are what graph databases do well and relational databases do
badly.

| | |
|---|---|
| **Nodes** | _not yet measured — produced by the loader_ |
| **Edges** | _not yet measured — produced by the loader_ |
| **Source records available** | **31,120,490** across 8 openFDA endpoints; **668,381** are the authorisation and oversight record — see below |
| **Source datasets** | 8 openFDA endpoints measured; 10 further sources identified, not yet researched |
| **Schema** | Two tiers — see [`schema/regulatory_affairs_kg.cypher`](schema/regulatory_affairs_kg.cypher); rationale is inline |
| **Engine** | Samyama-Graph OSS 1.1.0 |
| **Snapshot format** | `.sgsnap` |
| **Build hardware** | _n/a — nothing built yet_ |
| **License** | per-source (see table); raw rows not committed |
| **Date** | sources measured 2026-08-06; schema verified against the engine 2026-08-11 |

## Composition — Tier 1, measured

**Source records, not graph nodes.** These are counts of records at the source, read live from
the API by [`etl/probe_openfda.py`](etl/probe_openfda.py) on **2026-08-06**. Not one figure is
hand-entered. One source record may become several nodes and edges, so node and edge figures
follow only once a loader exists.

| Source | Publisher | Doc types | Source records | Format | Access | License |
|---|---|---|---:|---|---|---|
| openFDA — MAUDE adverse events | FDA | Adverse event reports | **25,368,161** | JSON API | `api.fda.gov/device/event` | Public domain (US Gov) |
| openFDA — UDI (GUDID) | FDA | Device identifiers | **5,083,948** | JSON API | `api.fda.gov/device/udi` | Public domain (US Gov) |
| openFDA — registration & listing | FDA | Establishments, listings | **330,251** | JSON API | `api.fda.gov/device/registrationlisting` | Public domain (US Gov) |
| openFDA — 510(k) | FDA | Clearances | **175,686** | JSON API | `api.fda.gov/device/510k` | Public domain (US Gov) |
| openFDA — recall | FDA | Recalls | **58,871** | JSON API | `api.fda.gov/device/recall` | Public domain (US Gov) |
| openFDA — PMA | FDA | Premarket approvals | **56,853** | JSON API | `api.fda.gov/device/pma` | Public domain (US Gov) |
| openFDA — enforcement | FDA | Enforcement reports | **39,635** | JSON API | `api.fda.gov/device/enforcement` | Public domain (US Gov) |
| openFDA — classification | FDA | Product codes, class, regulation number | **7,085** | JSON API | `api.fda.gov/device/classification` | Public domain (US Gov) |
| **510(k) Summary PDFs** | FDA | Predicate device, standards conformed to | ≤ 175,686 (not all filed) | PDF | `accessdata.fda.gov/cdrh_docs/` | Public domain (US Gov) |
| **openFDA subtotal** | | | **31,120,490** | | | |

### Which number to quote

**81.5% of the total is MAUDE adverse-event reports**, so 31 million overstates
graph-relevant scale. But 5,752,329 — the obvious next figure — is **88.4% UDI**
(5,083,948), and UDI does not reliably join to clearances. That is decision §2.2,
not a data-quality complaint: the FDA publishes no link between a clearance and
the device as sold, so we do not assert one.

| Figure | What it covers |
|---:|---|
| **31,120,490** | Everything. 81.5% adverse-event reports. |
| **5,752,329** | Excluding adverse events. **88.4% of this is UDI.** |
| **668,381** | Clearances, approvals, classification, registrations, recalls and enforcement — **the authorisation and oversight record** |

**Quote 668,381 when the question is about the regulatory backbone.**

## Composition — Tier 2, identified but not yet researched

Named in scope and modelled in the schema; **no counts, because none has been measured**. Each
needs the same live-probe treatment the openFDA endpoints received before it earns a number here.

| Source | Publisher | What it would contribute | Format | Licence position |
|---|---|---|---|---|
| 21 CFR 800–898 | eCFR / GPO | Paragraph-level obligation text — the prose GraphRAG retrieves | XML | Public domain (US Gov) |
| FDA guidance documents | FDA | Guidance applicability by product line | — | Public domain (US Gov) |
| FDA warning letters | FDA | Enforcement history | — | Public domain (US Gov) |
| EU MDR 2017/745 | EUR-Lex | Regulation, Annex I GSPR | XML/HTML | Reuse terms to confirm |
| EU IVDR 2017/746 | EUR-Lex | In-vitro diagnostics regulation | XML/HTML | Reuse terms to confirm |
| EUDAMED | European Commission | EU registrations and certificates | — | To confirm |
| NANDO | European Commission | Notified bodies | — | To confirm |
| Consensus standards | ISO / IEC / FDA-recognised list | Standard **identifiers only** | — | ⚠️ **Texts are paywalled — identifiers and clause references only, no text is ingested** |
| Secondary markets | ANVISA, CDSCO, Health Canada, TGA, MHRA | Registrations outside the US and EU | — | Varies; several are not machine-readable |

## Schema (node labels + key edge types)

The executable form is
[`schema/regulatory_affairs_kg.cypher`](schema/regulatory_affairs_kg.cypher), which carries the
rationale for each shape as inline comments. Counts are not repeated here so the two cannot
drift.

The long-form ontology document — the 17 questions, the 8 design decisions with the alternatives
considered, and "why these shapes" — is an internal design record and is not published with this
repo.

The schema is derived from **the questions the graph must answer**, written in regulatory-affairs
language. Every node and edge exists because it turns one of them into a single traversal;
anything serving none of them was left out. It is split into two tiers:

- **Tier 1 — loadable from public data today.** `Submission` (510(k), PMA, De Novo), `ProductCode`
  (the join hub), `Regulation` (a 21 CFR section), `Manufacturer`, `Establishment`,
  `MarketedDevice` (UDI), `Recall`, `AdverseEvent`, plus `PredicateClaim` and `Standard` from the
  510(k) Summary PDFs.
- **Tier 2 — modelled, deliberately unpopulated.** Regulation text and guidance, EU market
  structures, obligations and deadlines, AI governance, cybersecurity/SBOM, and the privacy and
  data-quality layers. These are designed so the ontology does not need redesigning when a source
  is found, and are marked unpopulated rather than implied to hold data.

Two shapes carry most of the value:

```
Regulation ←GOVERNED_BY– ProductCode ←CLASSIFIED_AS– Submission –SUBMITTED_BY→ Manufacturer
Submission –CITES_PREDICATE→ PredicateClaim –RESOLVES_TO→ Submission → (repeat)
```

The first is the change blast-radius, and it is exact rather than approximate because
`regulation_number` is a government-issued identifier present on every clearance, approval and
classification record. The second is predicate lineage, and it is deliberately routed through a
`PredicateClaim` node so that an **unresolved** predicate stays visible instead of silently
truncating a chain.

## Provenance / how it was built

**Nothing is built yet.** What exists today:

| Step | Status |
|---|---|
| Source measurement | ✅ [`etl/probe_openfda.py`](etl/probe_openfda.py), run 2026-08-06 |
| Schema design | ✅ 17 questions, 34 labels, 39 edges, 8 recorded decisions |
| Schema verified against a live engine | ✅ every statement executes; [`tests/test_schema_cypher.py`](tests/test_schema_cypher.py) |
| Downloaders | ❌ [`etl/download_data.py`](etl/download_data.py) is a stub — nothing implemented |
| Loaders | ❌ [`etl/loader.py`](etl/loader.py) is a stub — nothing implemented |
| Snapshot | ❌ none |

Reproduce the measurements:

```bash
python -m etl.probe_openfda            # record counts, read live from api.fda.gov
python -m etl.probe_openfda --fields   # counts plus a field inventory per endpoint
```

Verify the schema against an engine:

```bash
docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
pytest tests/test_schema_cypher.py
```

Per the KG-repo convention, **raw downloaded rows are not committed**. This repo ships the
downloaders, loaders, schema and a bounded demo only.

## Query benchmark

**None yet** — a benchmark without a loaded graph would be a fabrication. The query suite is
scoped in [`benchmarks/`](benchmarks/) and lands with the first load, with honest timings
including anything that times out.

Two engine behaviours already constrain how those queries must be written; see Known issues.

## Known issues

Found while executing the schema against **Samyama-Graph 1.1.0**. All four are engine issues, not
schema issues. **None is yet filed upstream** — they are recorded here so the numbering can be
added when they are.

| # | Issue | Consequence here |
|---|---|---|
| 1 | `CREATE CONSTRAINT … FOR … REQUIRE` does not parse, though the engine's own `CYPHER_COMPATIBILITY.md` documents it as supported | The schema uses the `ON (n:L) ASSERT` form; do not "modernise" it back. **Pinned by `test_uses_the_syntax_the_engine_parses`** — that test starts failing when the engine gains support |
| 2 | A uniqueness constraint does **not** reject a duplicate `CREATE` | **Every loader must `MERGE` on the key.** A constraint here declares the key, it does not guard the insert |
| 3 | An inline property pattern combined with an aggregate ignores the filter — `MATCH (x:L {id:'B'}) RETURN count(x)` returns the whole-label count. A regression against the engine's ADR-029 | Use `WHERE`, never inline property maps, in anything that aggregates |
| 4 | `nodes(path)` returns nodes with unresolved properties — path lengths are correct, every property is `null` | Predicate-chain queries must bind the claim to its own variable rather than reach into a named path. **Fails silently**, reading as missing data rather than a broken query |

## Usage

Nothing to load yet. Once a snapshot exists:

```bash
# start the engine
docker run --rm -p 8080:8080 -p 6379:6379 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0

# create the tenant
curl -X POST http://localhost:8080/api/tenants \
  -H 'Content-Type: application/json' \
  -d '{"id":"regulatory","name":"Regulatory Affairs KG"}'

# apply the schema
# (then import the .sgsnap snapshot — command lands with the first build)
```

## ⚠️ Limitations

Recorded before anyone finds them, per the house standard.

- **The predicate device is not exposed by the openFDA API.** It is published only in the 510(k)
  Summary PDF, and given as a free-text device name rather than a K-number — sometimes naming a
  pre-1976 device that has no clearance record at all. Predicate chains will therefore be
  **partial**, and unresolved predicates must be represented rather than silently dropped.
  Reproduce the endpoint measurements with `python -m etl.probe_openfda`.
- **Not every clearance publishes a summary PDF** — submitters may file a statement instead.
  The proportion is not yet quantified.
- **Older summary PDFs are likely scans** requiring OCR; the sampled 2024 filing had a text layer.

- **EUDAMED** is only partially populated across EU member states; coverage is uneven and this
  graph will reflect that unevenness.
- **MAUDE** is a voluntary reporting system and is known to under-report adverse events. Counts
  are not incidence rates.
- **510(k) summaries** are unstructured PDFs; any structured extraction from them is lossy.
- **ISO/IEC standard texts are paywalled.** Standards appear as identifier nodes with clause
  references. No standard text is ingested, so GraphRAG cannot retrieve standard prose.
- Secondary-market sources vary widely in machine-readability; some may end up modelled but
  not loaded.
