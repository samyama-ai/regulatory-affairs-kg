# Dataset Card — Medical-Device Regulatory Affairs KG

> **Status: skeleton.** Structure follows
> `samyama-graph-competitor-benchmarks/benchmarks/large-scale/DATASET-CARD.md`.
> Counts and licences filled in once source research completes (7 Aug 2026).

> _TODO — one paragraph: a knowledge graph of medical-device regulation combining N public
> sources — submissions, clearances, classification, post-market surveillance, obligation text
> and international market registrations — into one queryable graph._

| | |
|---|---|
| **Nodes** | _TBD — produced by the loader_ |
| **Edges** | _TBD — produced by the loader_ |
| **Source datasets** | 8 openFDA endpoints probed; 10 further sources identified |
| **Engine** | Samyama-Graph OSS |
| **Snapshot format** | `.sgsnap` |
| **Build hardware** | _TBD_ |
| **License** | per-source (see table); raw rows not committed |
| **Date** | _TBD_ |

## Composition (per-source contribution)

**Source records, not graph nodes.** These are counts of records at the source, read live from
the API by [`etl/probe_openfda.py`](etl/probe_openfda.py) on 2026-08-06. One record may become
several nodes and edges; node/edge figures follow once a loader exists. Rows still marked `_TBD_`
have not been researched yet.

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
| 21 CFR 800–898 | eCFR / GPO | Obligation text | _TBD_ | XML | eCFR API | Public domain (US Gov) |
| FDA guidance documents | FDA | Guidance | _TBD_ | _TBD_ | _TBD_ | Public domain (US Gov) |
| FDA warning letters | FDA | Enforcement | _TBD_ | _TBD_ | _TBD_ | Public domain (US Gov) |
| EU MDR 2017/745 | EUR-Lex | Regulation, Annex I GSPR | _TBD_ | XML/HTML | EUR-Lex | _TBD — check reuse terms_ |
| EU IVDR 2017/746 | EUR-Lex | Regulation | _TBD_ | XML/HTML | EUR-Lex | _TBD_ |
| EUDAMED | European Commission | Registrations, certificates | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| NANDO | European Commission | Notified bodies | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| Consensus standards | ISO / IEC / FDA-recognised list | Standard identifiers **only** | _TBD_ | _TBD_ | FDA recognised-standards DB | ⚠️ **Texts are paywalled — identifiers and clause references only, no text ingested** |
| Secondary markets | ANVISA, CDSCO, Health Canada, TGA, MHRA | Registrations | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| **openFDA subtotal** | | | **31,120,490** | | | |

## Schema (node labels + key edge types)

_TODO — see [`docs/regulatory-affairs-kg-plan.md`](docs/regulatory-affairs-kg-plan.md)._

## Provenance / how it was built

_TODO — per-source download and load steps, snapshot names, reproduction command._

Per the KG-repo convention, **raw downloaded rows are not committed**. This repo ships the
downloaders, loaders, schema and a bounded demo only.

## Query benchmark

_TODO — query suite and honest timings (selective reads, multi-hop, and anything that times out)._

## Known issues

_TODO — engine issues filed against `samyama-graph`, cited by number._

## Usage

_TODO — tenant creation, snapshot import, bounded first run._

## ⚠️ Limitations

_TODO — expand. Initial list:_

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
