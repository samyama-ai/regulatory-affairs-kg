# Dataset Card — Medical-Device Regulatory Affairs KG

> **Status: skeleton.** Structure follows
> `samyama-graph-competitor-benchmarks/benchmarks/large-scale/DATASET-CARD.md`.
> Counts and licences filled in once source research completes (7 Aug 2026).

> _TODO — one paragraph: a knowledge graph of medical-device regulation combining N public
> sources — submissions, clearances, classification, post-market surveillance, obligation text
> and international market registrations — into one queryable graph._

| | |
|---|---|
| **Nodes** | _TBD_ |
| **Edges** | _TBD_ |
| **Source datasets** | _TBD_ |
| **Engine** | Samyama-Graph OSS |
| **Snapshot format** | `.sgsnap` |
| **Build hardware** | _TBD_ |
| **License** | per-source (see table); raw rows not committed |
| **Date** | _TBD_ |

## Composition (per-source contribution)

| Source | Publisher | Doc types | Nodes | Edges | Format | Cadence | Access | License |
|---|---|---|---:|---:|---|---|---|---|
| openFDA — 510(k) | FDA | Clearances | _TBD_ | _TBD_ | JSON API | _TBD_ | `api.fda.gov` | Public domain (US Gov) |
| openFDA — PMA | FDA | Approvals | _TBD_ | _TBD_ | JSON API | _TBD_ | `api.fda.gov` | Public domain (US Gov) |
| openFDA — classification | FDA | Device classes, product codes | _TBD_ | _TBD_ | JSON API | _TBD_ | `api.fda.gov` | Public domain (US Gov) |
| openFDA — registration & listing | FDA | Establishments, listings | _TBD_ | _TBD_ | JSON API | _TBD_ | `api.fda.gov` | Public domain (US Gov) |
| openFDA — MAUDE | FDA | Adverse events | _TBD_ | _TBD_ | JSON API | _TBD_ | `api.fda.gov` | Public domain (US Gov) |
| openFDA — recall / enforcement | FDA | Recalls | _TBD_ | _TBD_ | JSON API | _TBD_ | `api.fda.gov` | Public domain (US Gov) |
| openFDA — UDI | FDA | Device identifiers | _TBD_ | _TBD_ | JSON API | _TBD_ | `api.fda.gov` | Public domain (US Gov) |
| 21 CFR 800–898 | eCFR / GPO | Obligation text | _TBD_ | _TBD_ | XML | _TBD_ | eCFR API | Public domain (US Gov) |
| FDA guidance documents | FDA | Guidance | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | Public domain (US Gov) |
| FDA warning letters | FDA | Enforcement | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | Public domain (US Gov) |
| EU MDR 2017/745 | EUR-Lex | Regulation, Annex I GSPR | _TBD_ | _TBD_ | XML/HTML | Static + amendments | EUR-Lex | _TBD — check reuse terms_ |
| EU IVDR 2017/746 | EUR-Lex | Regulation | _TBD_ | _TBD_ | XML/HTML | Static + amendments | EUR-Lex | _TBD_ |
| EUDAMED | European Commission | Registrations, certificates | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| NANDO | European Commission | Notified bodies | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| Consensus standards | ISO / IEC / FDA-recognised list | Standard identifiers **only** | _TBD_ | _TBD_ | _TBD_ | _TBD_ | FDA recognised-standards DB | ⚠️ **Standard texts are paywalled — identifiers and clause references only, no text ingested** |
| Secondary markets | ANVISA, CDSCO, Health Canada, TGA, MHRA | Registrations | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| **TOTAL** | | | _TBD_ | _TBD_ | | | | |

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

- **EUDAMED** is only partially populated across EU member states; coverage is uneven and this
  graph will reflect that unevenness.
- **MAUDE** is a voluntary reporting system and is known to under-report adverse events. Counts
  are not incidence rates.
- **510(k) summaries** are unstructured PDFs; any structured extraction from them is lossy.
- **ISO/IEC standard texts are paywalled.** Standards appear as identifier nodes with clause
  references. No standard text is ingested, so GraphRAG cannot retrieve standard prose.
- Secondary-market sources vary widely in machine-readability; some may end up modelled but
  not loaded.
