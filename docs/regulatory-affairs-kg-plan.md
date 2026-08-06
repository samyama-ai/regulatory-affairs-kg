# Medical-Device Regulatory Affairs Knowledge Graph — plan

> **Status: outline.** Structure fixed; sections filled in over 6–7 Aug 2026.
> Reviewed 11 Aug 2026. Follows the house plan-doc format used by
> `druginteractions-kg/docs/druginteractions-kg-plan.md`.

---

## Overview

_TODO — one paragraph: what the graph contains, which sources, and the question it answers._

The problem this graph exists to solve:

> When a regulation or standard changes — an EU MDR Annex I GSPR clause, a 21 CFR Part 820
> requirement, a recognised consensus standard — a manufacturer must determine which devices,
> technical files, notified-body certificates, market registrations and open post-market
> obligations are affected, and by when.

Today that is a manual exercise across spreadsheets, documents and email. As a graph it is a
single traversal. This is the same shape as the change-impact query in `bank-model-risk-kg`
("if this data source changes, which regulatory submissions are exposed?"), applied to device
regulation.

Scope is fixed in [`scope.md`](scope.md).

---

## Node labels

_TODO — table: label | key properties | projected count | source._

> The scaffold ships a generic placeholder ontology (`Regulation`, `Authority`, `Submission`,
> `Approval`, `Product`, `Requirement`) in `docs/schema.md` and
> `schema/regulatory_affairs_kg.cypher`. Both are replaced once source research confirms what
> the data supports.

Working set:

`Regulation` · `Clause` (incl. EU MDR Annex I GSPR) · `Guidance` · `Standard` · `Agency` ·
`NotifiedBody` · `Jurisdiction` · `Device` · `DeviceClass` · `ProductCode` · `Manufacturer` ·
`Submission` (510(k) / PMA / De Novo / HDE / CE technical file) · `PredicateDevice` ·
`Clearance` · `Registration` · `Certificate` · `UDI` · `TechnicalFile` · `Inspection` ·
`WarningLetter` · `Recall` · `AdverseEvent` · `PMSObligation` · `ChangeControl` · `Deadline`

---

## Edge types

_TODO — table: edge | from → to | properties | meaning._

Working set:

`PREDICATE_OF` · `GOVERNED_BY` · `HAS_CLAUSE` · `CONFORMS_TO` · `CERTIFIED_BY` ·
`REGISTERED_IN` · `CLEARED_VIA` · `CLASSIFIED_AS` · `ISSUED_BY` · `APPLIES_IN` ·
`TRIGGERED_RECALL` · `REPORTED_AS` · `REQUIRES_PMS` · `IMPACTED_BY` · `DUE_ON`

---

## Why these shapes

_TODO — justify each cluster of edges by the question it turns into a single traversal.
Follows the "Why these shapes" section in `bank-model-risk-kg/docs/schema.md`._

Planned justifications:

- **Change blast-radius** — `Clause ←GOVERNED_BY– Device –REGISTERED_IN→ Jurisdiction`
  and `Device –REQUIRES_PMS→ PMSObligation –DUE_ON→ Deadline`
- **Predicate lineage** — `Submission –PREDICATE_OF→ PredicateDevice`, transitively
- **Conformity evidence** — `Device –CONFORMS_TO→ Standard` and
  `TechnicalFile –CERTIFIED_BY→ NotifiedBody`
- **Post-market signal** — `Device –REPORTED_AS→ AdverseEvent`, `Recall`, `WarningLetter`
- **Market coverage** — `Registration –APPLIES_IN→ Jurisdiction`

---

## Signature query

_TODO — the change blast-radius query in Cypher, with results._

---

## Data sources

_TODO — see [`DATASET-CARD.md`](../DATASET-CARD.md) composition table for the authoritative list.
Per-source detail in [`sources/`](sources/)._

| Tier | Sources |
|---|---|
| 1 | openFDA device endpoints; 21 CFR Parts 800–898 (eCFR); FDA guidance, warning letters, inspections |
| 2 | EU MDR 2017/745 + IVDR 2017/746 (EUR-Lex); Annex I GSPR; MDCG guidance; EUDAMED; NANDO; consensus standards |
| 3 | ANVISA, CDSCO, Health Canada, TGA, MHRA/UKCA |

---

## GraphRAG over obligation text

_TODO — design detail._

`Clause.text` carries the obligation prose; `Clause.embedding` is a 384-dim vector
(`all-MiniLM-L6-v2` via fastembed) in an HNSW cosine index. A natural-language question is
embedded, the nearest clauses retrieved, and each hit grounded in the graph — which devices it
governs, which standards satisfy it, which registrations and open obligations it touches — so
the answer is citable rather than generated. Same pattern as
`bank-model-risk-kg/etl/graphrag.py`.

---

## Cross-KG bridge properties

_TODO — confirm exact property names against the sibling repos._

| This KG | Property | Bridges to |
|---|---|---|
| `Device` | `product_code`, `udi_di` | — (internal) |
| `Device` | device trial identifiers | `clinicaltrials-kg` |
| `AdverseEvent` | event term | FAERS / `druginteractions-kg` |
| `Guidance`, `Clause` | cited references | `pubmed-kg` |

Note: join keys in this domain are **government-issued unique identifiers** (K-number, PMA
number, FDA product code, UDI-DI) rather than names, so cross-source joins are exact.

---

## Known limitations

_TODO — expand. Initial list:_

- **EUDAMED** is only partially populated across EU member states; coverage is uneven.
- **MAUDE** is voluntary reporting and is known to under-report adverse events.
- **510(k) summaries** are unstructured PDFs; extraction is lossy.
- **ISO/IEC standard texts are paywalled** — modelled as identifier and clause-reference nodes
  only. No standard text is ingested.
- Secondary-market sources vary widely in machine-readability.

---

## Open questions for review (11 Aug)

_TODO — expand ahead of the conversation._

1. Are IVDs in scope for the first build, or devices only?
2. Does SaMD need separate modelling, or is it a `Device` subtype?
3. Which of the secondary markets matter most in practice?
4. Which regulatory workflow would be most valuable to make a single traversal first?

---

## Milestones

| Date | Deliverable |
|---|---|
| 6 Aug | Scope decision, spec outline, dataset-card skeleton (repo scaffolded from the KG template) |
| 7 Aug | Source research complete; composition table; ontology; spec ready for review |
| 8–10 Aug | openFDA loaders, predicate-chain edges, benchmark queries, first snapshot |
| 11 Aug | Review conversation |
