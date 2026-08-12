# Dataset Card — Medical-Device Regulatory Affairs KG

> **Status: first load complete — the Q1 change-impact backbone, on real FDA data.**
> Every figure below is a live measurement. Node and edge counts are now measured from the graph
> itself rather than inferred from input rows, and cover a **bounded slice**: all device
> classifications, plus 21 CFR part 870 (cardiovascular) clearances. See *Provenance*.

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
| **Nodes** | **28,496** — measured 2026-08-12 (bounded slice, see *Provenance*) |
| **Edges** | **25,310** — measured 2026-08-12 |
| **Source records available** | **31,120,490** across 8 openFDA endpoints; **668,381** are the authorisation and oversight record — see below |
| **Source datasets** | 8 openFDA endpoints measured; 10 further sources identified, not yet researched |
| **Schema** | Two tiers — see [`docs/schema.md`](docs/schema.md) for labels, edges and the design decisions behind them |
| **Engine** | Samyama-Graph OSS 1.1.0 |
| **Snapshot format** | `.sgsnap` |
| **Build hardware** | Local Docker, Samyama-Graph 1.1.0; load took 598s (53,811 statements, ~90/sec) |
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
graph-relevant scale. But the obvious next figure — 5,752,329 excluding adverse
events — needs its own caveat: **88.4% of *that* is UDI** (5,083,948), and UDI
does not reliably join to clearances. That is not a data-quality complaint; it is
a deliberate schema decision, because the FDA publishes no link between a
clearance and the device as sold.

Three honest figures, not one:

| Figure | What it covers |
|---:|---|
| **31,120,490** | Everything. 81.5% adverse-event reports. |
| **5,752,329** | Excluding adverse events. **88.4% of this is UDI.** |
| **668,381** | Clearances, approvals, classification, registrations, recalls and enforcement — **the authorisation and oversight record** |

**Quote 668,381 when the question is about the regulatory backbone.** Quote
5,752,329 only where UDI genuinely belongs in the answer, and say that it
dominates. The 31M headline is true and misleading.

This correction was made on 2026-08-12: the card previously called 5,752,329 the
"regulation-relevant core", which overstated what is usable for the questions
this graph exists to answer.

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

Full definitions, the questions each shape serves, and the design decisions behind them are in
[`docs/schema.md`](docs/schema.md); the executable form is
[`schema/regulatory_affairs_kg.cypher`](schema/regulatory_affairs_kg.cypher). Counts are not
repeated here so the two cannot drift.

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

**The first load is done, and it is deliberately bounded.** What exists today:

### What is loaded

| | Scope | Why |
|---|---|---|
| **Classifications** | all **7,085** | The complete product-code → device-class → 21 CFR map. The entire device-to-law backbone. |
| **510(k) clearances** | **19,127** — 21 CFR **part 870 (cardiovascular) only** | openFDA caps paging at `skip=25000` without a key, so the full 175,686 cannot be walked page by page. Part 870 fits under the cap and contains `870.5150`, the worked example. |

| Label / edge | Count |
|---|---:|
| `ProductCode` | 7,085 |
| `Regulation` | 2,284 |
| `Submission` | 19,127 |
| **Total nodes** | **28,496** |
| `GOVERNED_BY` | 6,183 |
| `CLASSIFIED_AS` | 19,127 |
| **Total edges** | **25,310** |

**902 product codes carry no regulation number** (unclassified or exempt), which is why
`GOVERNED_BY` is 6,183 rather than 7,085. Not a load failure — the FDA does not classify them.

**There is no `SUBMITTED_BY` edge, deliberately.** A 510(k) record's `openfda` block carries
`fei_number` and `registration_number` arrays that look like a link to the applicant. They are
harmonised **by product code**, not by applicant: `K201705` (Vetex Medical) and `K252612`
(Penumbra) carry identical 72-entry arrays because both are product code `QEW`. Building an edge
from that would assert every clearance was submitted by all 72 companies listing the code.
`applicant` stays a name property; `Manufacturer` waits for `device/registrationlisting`.

### Verified

The change-impact query returns **415** clearances for `870.5150` — matching the count the API
returns for the same filter, arrived at independently. Loading the same data twice leaves both
counts unchanged.



| Step | Status |
|---|---|
| Source measurement | ✅ [`etl/probe_openfda.py`](etl/probe_openfda.py), run 2026-08-06 |
| Schema design | ✅ [`docs/schema.md`](docs/schema.md) |
| Schema verified against a live engine | ✅ every statement executes; [`tests/test_schema_cypher.py`](tests/test_schema_cypher.py) |
| Downloader | ✅ [`etl/download_openfda.py`](etl/download_openfda.py) — classifications + a scoped 510(k) slice |
| Loader | ✅ [`etl/load_openfda.py`](etl/load_openfda.py) — MERGE-based, idempotence proven by test |
| Predicate chains | ❌ needs PDF extraction; resolution rate unmeasured |
| Snapshot, demo | ❌ not built |
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

**Not yet a suite** — one query is verified end to end, which is not a benchmark. Timings and the
full suite land with [`benchmarks/`](benchmarks/).

Q1, change impact, against the loaded graph:

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

**415 clearances in total** under that rule.

Note "Inari Medical" and "Inari Medical, Inc." arriving as separate applicants. Applicant names
are not normalised at source — which is exactly why the schema keys on `product_code` and treats
names as properties, never as keys.

Six engine behaviours constrain how these queries and the loader must be written; see Known
issues.

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
| 5 | **`UNWIND` does not parse at all** — not even `UNWIND [1,2,3] AS x RETURN x` — despite `CYPHER_COMPATIBILITY.md` listing it as supported. Semicolon-separated statements are also rejected, and `MERGE … SET` is a parse error (`MERGE … ON CREATE SET … ON MATCH SET` works) | No batch write form exists. The loader issues **one statement per HTTP request**, ~90–700/sec depending on statement size. Workable, but it is why a 26k-row load takes ten minutes rather than seconds |
| 6 | **No string escaping inside literals.** `\"` and `\'` are parse errors; `\n`, `\t`, `\\` pass through as literal backslash sequences rather than being decoded. A literal's own delimiter cannot appear inside it, and `/api/query` accepts **no parameters** | Quote style is chosen per value. A value containing *both* quote types cannot be represented at all — 2 of 553,281 in this corpus. Those are altered and **reported**, never silently changed |
| 7 | **`/api/query` ignores the `graph` field.** Writes sent to a named tenant land in the shared store and are visible from every other tenant | Tenants cannot isolate a test or a dataset through this API. The loader tests use fixture keys that cannot collide with real data instead |

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
  See [`docs/sources/openfda-devices.md`](docs/sources/openfda-devices.md).
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
