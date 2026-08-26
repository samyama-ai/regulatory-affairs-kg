# CLAUDE.md — regulatory-affairs-kg

## What this is

A knowledge graph of **medical-device regulatory affairs** — FDA and EU MDR. Part of the Samyama
ecosystem: each `*-kg` repo proves the Samyama graph engine works in a real domain. This is the
first in regulatory.

**In scope:** medical devices (scanners, implants, diagnostic software, AI-enabled devices).
US FDA and EU MDR primary; ANVISA, CDSCO, Health Canada, TGA, MHRA secondary.
**Not in scope:** pharmaceuticals, banking/financial regulation.

Full reasoning in the scope decision — see **Key documents** below.

## The point of it

When a rule or standard changes, a manufacturer must work out which devices, technical files,
certificates, registrations and post-market obligations are affected, and by when. Today that is
manual work across spreadsheets and email. As a graph it is one traversal.

The showcase structure is **510(k) predicate chains** — every US clearance names the older device
it claims equivalence to, forming a public citation chain back to 1976. Chains of unknown depth
are what graphs do well and relational databases do badly.

## Key documents — read these before changing anything

**The analysis moved out of this repo on 2026-08-12.** It now lives in
`Samyama.ai/samyama-graph-solutions` under `regulatory-affairs/`, per the
knowledge/implementation separation — this repo is going public on GitHub and
the reasoning does not travel with it.

| Document | Where | What |
|---|---|---|
| Scope decision | `wiki/sources/kg-scope.md` | What's in and out, and why |
| **The ontology** | `wiki/sources/kg-schema-ontology.md` | 17 questions, 8 design decisions, node/edge tables, "Why these shapes" |
| openFDA source research | `wiki/sources/kg-openfda-devices-source-research.md` | What the FDA data actually contains and does not |
| Plan / spec | `wiki/sources/kg-plan.md` | The spec |
| `DATASET-CARD.md` | **this repo** | Source table with measured record counts and licences |

The compiled wiki around those sources — entities, concepts, decisions,
positioning — is in the same folder. Start at `wiki/index.md`.

## Hard facts about the data (measured, not assumed)

- **8 openFDA device endpoints, 31,120,490 records**, US-government public domain.
  Verify with `python -m etl.probe_openfda`.
- **`regulation_number` (e.g. `870.5150`) is on every clearance, approval and classification
  record** — an exact device-to-law join. This is the backbone of the change-impact question.
- **`product_code` is the join hub.** Every endpoint carries it. Device *names* vary between
  endpoints and must never be used as keys.
- **The predicate device is NOT in the openFDA API.** It appears only in the 510(k) Summary PDF
  (`accessdata.fda.gov/cdrh_docs/pdf{YY}/{K}.pdf`), as a **free-text device name**, sometimes
  naming a pre-1976 device with no clearance record. Chains are partial by nature.
- **Clearances and UDI records do not reliably join.** Do not assert a
  `Submission → MarketedDevice` edge; the FDA does not publish one.
- **ISO/IEC standard texts are paywalled.** `Standard` nodes hold identifiers and clause
  references only. Never ingest standard text.

## Working rules

- **Push to Gitea** — `git.samyama.ai/Samyama.ai/regulatory-affairs-kg`. Not GitHub.
- **One issue → one branch → one PR.** Never bundle.
- **Every issue needs acceptance criteria.**
- Branch names: `<type>/<ISSUE-ID>/<kebab-desc>` — types `feat` `fix` `chore` `docs` `refactor`
  `test`. Example: `docs/3/device-regulatory-ontology`.
- PR body must contain `Closes #<N>` so the issue auto-closes on merge.
- **Never commit raw downloaded data.** `data/` is gitignored. This repo ships downloaders,
  loaders, schema and a bounded demo only — the house convention across all `*-kg` repos.
- **No images or binary files in commits.**
- Target cadence: 5–10 clean PRs per day, reviewed at the 12:00 sync.

## Evidence standard

State how a fact was obtained. Anything measured is labelled measured; anything extrapolated is
labelled an estimate. The openFDA source research §6 is the pattern. Counts go in the docs
only via `etl/probe_openfda.py`, never hand-typed.

Write limitations down before anyone finds them — see the ontology §6 ("What the schema does
not claim"). This matches the house style: the flagship dataset card openly documents what its
own build got wrong.

## Reference repos (siblings in the ecosystem)

| Repo | Use it for |
|---|---|
| `druginteractions-kg` | The plan-doc format; one downloader + one loader + one test per source; the openFDA loader pattern to adapt |
| `clinicaltrials-kg` | Richest template — scenarios, evaluation harness, demo, bounded-vs-full load paths |
| `bank-model-risk-kg` | The "Why these shapes" section; GraphRAG over regulation text; **the AI-governance shapes reused here** (`Model` → `AIModel`, `Validation`, `Finding`, `Assumption`, `Control`) |
| `samyama-cloud` | Ecosystem docs, architecture, acronyms |

Local clones: `~/samyama/samyama-graph` (OSS engine), `~/samyama/samyama-cloud`.

## Repo layout

```
etl/          downloaders + loaders (one per source) + probe_openfda.py
schema/       regulatory_affairs_kg.cypher — executable ontology
docs/         scope, schema, plan, sources/
benchmarks/   query suite
mcp_server/   MCP tools exposing the graph
demo/         narrated demo
tests/        pytest, one file per loader
```

## Running the engine

```bash
docker run --rm -p 8080:8080 -p 6379:6379 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
curl -X POST http://localhost:8080/api/tenants \
  -H 'Content-Type: application/json' \
  -d '{"id":"regulatory","name":"Regulatory Affairs KG"}'
```

## Current state (7 Aug 2026)

- PR #2 merged — scope, spec outline, dataset card
- PR #5 open — openFDA source research (issue #4)
- PR #6 open — the ontology (issue #3)
- Next: downloaders and loaders, then a working change-impact query, then predicate chains for one
  product area

**Deadline:** conversation with Dr. Abhilash Magi (medical-device regulatory specialist, RAPS
Twin Cities) on **11 Aug 2026**. It is a pitch, not a review — he will not be validating the
ontology, so design decisions must be justified from public sources. A working demo beats another
document.
