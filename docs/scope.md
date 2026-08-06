# Scope decision — Regulatory Affairs KG

**Status:** decided, open to challenge
**Date:** 2026-08-06

## Decision

This knowledge graph covers **medical-device regulatory affairs** — not pharmaceuticals,
and not financial/banking regulation.

**Primary jurisdictions:** United States (FDA) and European Union (MDR/IVDR).
**Secondary jurisdictions:** Brazil (ANVISA), India (CDSCO), Canada, Australia (TGA),
United Kingdom (MHRA/UKCA) — modelled for market coverage, loaded later.

## Why

"Regulatory affairs" is ambiguous — it can mean pharmaceutical regulation, medical-device
regulation, or financial-services regulation. Three inputs fixed it:

1. **The track was scoped as health context** in the 2026-08-05 planning call.
2. **The domain expert this work will be reviewed with is a medical-device specialist.**
   Dr. Abhilash Magi (RAPS Twin Cities; Associate Regulatory Affairs Specialist) works on
   FDA 510(k)/HDE submissions, EU MDR technical files and GSPR, and post-market compliance
   across 15+ international markets. His stated specialisms — *regulatory intelligence /
   monitoring* and *change control & license maintenance* — are the exact workflows this
   graph is designed to serve.
3. **Device regulation is better suited to a knowledge graph than pharma regulation**, because
   the identifiers are strong and public: 510(k) K-numbers, PMA numbers, FDA product codes,
   UDI-DIs, EUDAMED identifiers. Cross-source joins are exact rather than name-based.

## What this excludes

- Pharmaceutical approvals (NDA/ANDA/BLA, Orange Book, Purple Book)
- Banking and financial regulation — `bank-model-risk-kg` covers model risk in that domain and
  is referenced here **only** for schema and GraphRAG patterns, not for content
- Clinical-trial regulation beyond device trials (covered by `clinicaltrials-kg`)

## Relationship to existing KGs

This graph is intended to federate with the existing biomedical KGs rather than duplicate them:

| This KG | Joins to | On |
|---|---|---|
| `Device` | `clinicaltrials-kg` | device trial identifiers |
| `AdverseEvent` (MAUDE) | FAERS / `druginteractions-kg` | event terminology |
| `Guidance` / `Clause` | `pubmed-kg` | cited evidence |

It also supplies the regulatory layer that `bank-model-risk-kg` stubs out: that graph carries a
`RegulatoryRequirement` label with 16 nodes. The equivalent here is a first-class graph built
from primary sources.

## Open to challenge

The 11 Aug conversation is the right place to revisit:

- Whether IVD (in-vitro diagnostics) should be in or out of the first build
- Whether Software as a Medical Device (SaMD) deserves separate modelling
- Whether secondary markets are worth loading, or only worth modelling
