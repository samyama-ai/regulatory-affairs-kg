# Medical-Device Regulatory Affairs KG — schema

The ontology is derived from **the questions the graph must answer**, not from the shape of any
one data source. Every edge below exists because it turns a question a regulatory-affairs
professional asks into a single traversal.

Where a design choice had more than one reasonable answer, the options and the reasoning are
recorded in [§2 Design decisions](#2-design-decisions) rather than left implicit.

Source reality is documented in [`sources/openfda-devices.md`](sources/openfda-devices.md);
scope in [`scope.md`](scope.md).

---

## 1. The questions

Written in regulatory-affairs language. Anything in the schema that serves none of these does not
belong.

| # | Question | Group |
|---|---|---|
| Q1 | A clause of 21 CFR or EU MDR is amended — **which devices are affected, in which markets, by when?** | Change impact |
| Q2 | A consensus standard is revised — **which technical files reference the superseded version?** | Change impact |
| Q3 | New guidance is published — **which product lines and submission types does it apply to?** | Change impact |
| Q4 | We are modifying a cleared device — **does this need a new 510(k), and what did we claim last time?** | Change control |
| Q5 | **Which registrations and certificates expire in the next 90 days, in which markets?** | Licence maintenance |
| Q6 | **What is the full predicate ancestry of this clearance?** | Lineage |
| Q7 | **Has anything in that ancestry been recalled or subject to enforcement?** | Lineage |
| Q8 | **What has gone wrong with this device — and does it cluster by manufacturer or component?** | Post-market |
| Q9 | **Which post-market obligations are open, and when are they due?** | Post-market |
| Q10 | **Which markets is this device cleared in, and what is missing for the rest?** | Market access |
| Q11 | For an AI-enabled device — **what was it trained on, what did we assume, when was it last validated, what findings are open?** | AI governance |
| Q12 | **Which AI-enabled devices may be retrained without a new submission** (predetermined change control)? | AI governance |
| Q13 | **What is in this device's software bill of materials, and does any component carry a reportable vulnerability?** | Cybersecurity |

**Built first:** Q1 (the value), Q6 (the demonstration), Q11 (the direction).
The rest are modelled so they are reachable without redesign.

---

## 2. Design decisions

### 2.1 `product_code` is the hub, not the device name

Every openFDA endpoint carries `product_code` — a three-letter FDA code (`DXE`, `QML`) naming a
device category, mapped by the classification endpoint to a regulation number and a device class.
Device *names* vary between endpoints for the same device; product codes do not.

**Decision:** joins go through `ProductCode`. Names are properties, never keys.

### 2.2 A clearance and a marketed device are different things

openFDA carries two notions of "device" that do not reliably join:

- a **clearance** — a device as submitted for market authorisation, keyed by `k_number`
- a **UDI record** — a device as actually sold, keyed by `udi_di`

UDI records generally do not carry the K-number of the clearance that authorised them.

| Option | Consequence |
|---|---|
| One `Device` node for both | Asserts a link the FDA does not publish — fabricated data |
| **Two node types, joined via `ProductCode`** | Honest; one extra hop between authorisation and market |

**Decision:** separate `Submission` and `MarketedDevice`, both linked to `ProductCode`. We do not
manufacture a join that the source does not support.

### 2.3 A submission and its decision are one node

A 510(k) record carries both `date_received` and `decision_date`; there is one record per
submission-and-outcome. Splitting them would create a `Decision` node holding two fields and no
independent identity.

**Decision:** one `Submission` node with decision properties.

**Revisit when** pre-submissions (Q-Sub) are added — those have no decision, and at that point a
submission without an outcome becomes a real state rather than an edge case.

### 2.4 An unresolved predicate must be representable

Predicates are not in the API (see the source doc). They appear in 510(k) Summary PDFs as
**free-text device names**, and sometimes name a pre-1976 device that has no clearance record at
all — the chain genuinely ends there.

| Option | Consequence |
|---|---|
| `Submission -[:PREDICATE_OF]-> Submission`, drop unresolved | Silently deletes data; a truncated chain looks complete |
| Edge with a `resolved` flag | An unresolved edge has no valid endpoint |
| **`PredicateClaim` node holding the raw name, with an optional `RESOLVES_TO`** | Uniform; unresolved links stay visible and queryable |

**Decision:** the third.

```
Submission -[:CITES_PREDICATE]-> PredicateClaim {raw_name}
                                       |
                                       +-[:RESOLVES_TO]-> Submission     (when resolvable)
```

This makes *"how far back does this chain go before it dead-ends, and why?"* a first-class
question rather than an invisible data-quality problem.

`PredicateClaim.id` is deterministic — `sha1("<source_k_number>|<normalised raw_name>")` — so
re-running the extractor merges rather than duplicates. There is **one claim node per citing
submission**: two clearances naming the same predecessor create two claims that both resolve to
the same `Submission`. A claim is the citation as filed, not a shared entity. Ancestry is
therefore a **DAG, not a tree**, and *"how many devices trace back to this one?"* is a count of
incoming `RESOLVES_TO`.

### 2.5 A recall and its enforcement report are one node

`device/recall` and `device/enforcement` are two views of the same event and share
`recall_number`. Modelling them as two `Recall` nodes joined by an `ENFORCED_AS` edge would
collide on `ASSERT rc.recall_number IS UNIQUE` — the first pair loaded would fail.

| Option | Consequence |
|---|---|
| Two nodes joined by `ENFORCED_AS` | Breaks the uniqueness constraint at load time |
| Two labels with different keys | A join with no question behind it; nothing asks for the report separately from the recall |
| **One `Recall` node carrying the enforcement view as properties** | One key, no collision, no lost fields |

**Decision:** the third — the same argument as §2.3. `ENFORCED_AS` is removed.

**Revisit when** a recall is found with multiple distinct enforcement reports, which would make
the report an entity in its own right.

### 2.6 Regulation has two levels

`regulation_number` from openFDA gives a CFR **section** (`870.5150`). The prose that carries the
actual obligation — and that GraphRAG must retrieve — lives at **paragraph** level in the eCFR
text, which is a separate source.

**Decision:** `Regulation` (section, populated now) and `Clause` (paragraph, populated when eCFR
is ingested), linked by `HAS_CLAUSE`. `Clause.text` carries the prose and `Clause.embedding` the
384-dim vector for retrieval.

### 2.7 The AI-governance layer is modelled before it is populated

openFDA exposes no AI/ML fields. The FDA publishes an AI/ML-Enabled Medical Device List
separately; it has not yet been evaluated as a source.

**Decision:** model the layer now, mark it unpopulated, and track the source as an open item. The
shapes are taken directly from
[`bank-model-risk-kg`](https://github.com/samyama-ai/bank-model-risk-kg) rather than reinvented —
model governance is domain-independent, and reusing a validated structure is the point.

| `bank-model-risk-kg` | Here |
|---|---|
| `Model` | `AIModel` |
| `DataSource`, `Feature` | `TrainingDataset` |
| `Assumption` | `Assumption` (intended-use population, operating conditions) |
| `Validation`, `ValidationFinding` | `Validation`, `Finding` |
| `Control` | `Control` (GMLP controls, bias evaluation) |
| `RegulatoryRequirement` | `Regulation`, `Clause` |

---

## 3. Node labels

### Populated from openFDA

| Label | Key | Key properties | Source |
|---|---|---|---|
| `Submission` | `id` | `type` (510k/PMA/DeNovo), `k_number` \| `pma_number`, `device_name`, `applicant`, `date_received`, `decision_date`, `decision_code`, `decision_description`, `clearance_type`, `advisory_committee` | `device/510k`, `device/pma` |
| `ProductCode` | `product_code` | `device_name`, `device_class`, `medical_specialty`, `definition`, `submission_type`, `implant_flag`, `life_sustain_flag` | `device/classification` |
| `Regulation` | `cfr_section` | `title`, `part`, `section` | `device/classification` |
| `Manufacturer` | `fei_number` | `name`, `country` | `device/registrationlisting` |
| `Establishment` | `registration_number` | `name`, `address`, `country`, `establishment_type` | `device/registrationlisting` |
| `MarketedDevice` | `udi_di` | `brand_name`, `company_name`, `description`, `sterilisation`, `mri_safety`, `version_model` | `device/udi` |
| `Recall` | `recall_number` | `event_id`, `classification` (I/II/III), `reason`, `root_cause`, `status`, `initiation_date`, `distribution_pattern`, plus the enforcement view: `enforcement_status`, `report_date`, `voluntary_mandated` | `device/recall`, `device/enforcement` |
| `AdverseEvent` | `report_number` | `event_type` (malfunction/injury/death), `date_received`, `patient_outcome`, `brand_name` | `device/event` |

### Populated from 510(k) Summary PDFs

| Label | Key | Key properties |
|---|---|---|
| `PredicateClaim` | `id` — `sha1("<source_k_number>\|<normalised raw_name>")` | `raw_name` (as printed), `source_k_number`, `resolved` |
| `Standard` | designation | `body` (ISO/IEC/ASTM), `number`, `edition`, `title` — **identifiers and clause references only; texts are paywalled and are not ingested** |

### Modelled, not yet populated

| Label | Serves | Source needed |
|---|---|---|
| `Clause` | Q1, Q2, GraphRAG | eCFR XML; EU MDR via EUR-Lex |
| `Guidance` | Q3 | FDA guidance database |
| `Jurisdiction` | Q1, Q5, Q10 | derived + national registries |
| `Registration` | Q5, Q10 | EUDAMED, national registries |
| `NotifiedBody`, `Certificate` | Q2, Q5 | NANDO, EUDAMED |
| `TechnicalFile` | Q2, Q4 | company-internal — customer data, not public |
| `PMSObligation`, `Deadline` | Q5, Q9 | EU MDR; FDA post-market commitments |
| `AIModel`, `TrainingDataset`, `Assumption`, `Validation`, `Finding`, `Control` | Q11, Q12 | FDA AI/ML-Enabled Device List (unevaluated) |
| `SoftwareComponent`, `Vulnerability` | Q13 | SBOM submissions; CVE/NVD |

`Deadline` is a node rather than a date property so that *"what is due in the next 90 days across
every obligation type"* is one traversal rather than a scan of every obligation label. The date is
`Deadline.due_date` (ISO 8601), and it carries its own index —
`CREATE INDEX ON :Deadline(due_date)` — because the uniqueness constraint on `id` does nothing for
a date range, and without the index the traversal claim is not true.

Tier-2 edge endpoints are **provisional**: they are the shape each question requires, recorded so
no label is declared without a path to the question it serves. They are settled when the source
that populates them is ingested.

---

## 4. Edge types

| Edge | From → To | Meaning |
|---|---|---|
| `CLASSIFIED_AS` | Submission → ProductCode | The category a submission was cleared under |
| `GOVERNED_BY` | ProductCode → Regulation | The CFR section governing the category |
| `HAS_CLAUSE` | Regulation → Clause | Section to paragraph |
| `SUBMITTED_BY` | Submission → Manufacturer | Applicant |
| `MANUFACTURED_AT` | Manufacturer → Establishment | Registered facility |
| `LISTS` | Establishment → ProductCode | Device categories a facility lists |
| `CITES_PREDICATE` | Submission → PredicateClaim | Substantial-equivalence claim as filed |
| `RESOLVES_TO` | PredicateClaim → Submission | The claim matched to a clearance record |
| `CONFORMS_TO` | Submission → Standard | Standard the device was tested against |
| `MARKETED_AS` | MarketedDevice → ProductCode | Category of a marketed device |
| `AFFECTS` | Recall → ProductCode \| MarketedDevice | What a recall covers |
| `REPORTED_AGAINST` | AdverseEvent → ProductCode \| MarketedDevice | Subject of the report |
| `REFERENCES_STANDARD` | TechnicalFile → Standard | Edition of a standard a file relies on — the Q2 join |
| `DOCUMENTS` | TechnicalFile → Submission \| MarketedDevice | What the file is the evidence for |
| `APPLIES_TO` | Guidance → ProductCode \| Regulation | Product lines and rules a guidance covers |
| `REGISTERS` | Registration → MarketedDevice \| ProductCode | What is registered in a market |
| `APPLIES_IN` | Registration → Jurisdiction | Market coverage |
| `COVERS` | Certificate → TechnicalFile \| MarketedDevice | What the certificate certifies |
| `CERTIFIED_BY` | Certificate → NotifiedBody | EU conformity assessment |
| `REQUIRES_PMS` | Regulation → PMSObligation | Post-market obligation created by a rule |
| `DUE_ON` | PMSObligation \| Certificate → Deadline | When it falls due |
| `EMBEDDED_IN` | AIModel → MarketedDevice | An AI model inside a device |
| `TRAINED_ON` | AIModel → TrainingDataset | Training provenance |
| `MAKES_ASSUMPTION` | AIModel → Assumption | Intended-use and population assumptions |
| `VALIDATED_BY` | AIModel → Validation | Validation event |
| `RAISED` | Validation → Finding | Issue raised by a validation |
| `SATISFIES` | Control → Regulation \| Clause | Control-to-requirement mapping |
| `CONTAINS_COMPONENT` | MarketedDevice → SoftwareComponent | SBOM entry |
| `HAS_VULNERABILITY` | SoftwareComponent → Vulnerability | Known CVE |

---

## 5. Why these shapes

Each cluster exists because it collapses a question into one traversal.

### Change blast-radius — Q1, Q2, Q3

```
Regulation ←GOVERNED_BY– ProductCode ←CLASSIFIED_AS– Submission –SUBMITTED_BY→ Manufacturer
```

A rule change starts at `Regulation` and reaches every affected submission and manufacturer in
two hops. This works because `regulation_number` is present on every clearance, approval and
classification record — an exact, government-issued join, not a name match. **Q1 is fully
supported by data available today.**

`Submission -[:CONFORMS_TO]-> Standard` gives the same shape for a revised standard (Q2) on the
public side, from the summary PDFs. `TechnicalFile -[:REFERENCES_STANDARD]-> Standard` is the
same traversal on the customer's own side — the half of Q2 that asks *"which of **our** technical
files cite the superseded edition?"* — and is the reason `TechnicalFile` is in the schema at all.

`Guidance -[:APPLIES_TO]-> ProductCode | Regulation` does the same for Q3.

Today this question is answered by reading a rule and checking a product portfolio by hand. It is
the most expensive routine task in the domain, and the reason this graph exists.

### Predicate lineage — Q6, Q7

```
Submission –CITES_PREDICATE→ PredicateClaim –RESOLVES_TO→ Submission → (repeat)
```

Chains of unknown depth are the case relational databases handle worst and graphs handle best —
"keep going until the beginning" cannot be written as a fixed join. This is the structure that
most clearly justifies a graph engine, and the FDA publishes the underlying documents while
providing no way to traverse them.

Crossing from any ancestor into `Recall` answers Q7 — *was anything in this device's lineage later
recalled?* The data to check that is public, and almost nobody checks it.

### Post-market signal — Q8

```
ProductCode ←REPORTED_AGAINST– AdverseEvent
ProductCode ←AFFECTS– Recall
Manufacturer –MANUFACTURED_AT→ Establishment –LISTS→ ProductCode
```

Routing adverse events and recalls through `ProductCode` — rather than through inconsistent device
names — is what makes clustering by manufacturer or facility possible at all.

### Obligations and deadlines — Q5, Q9

```
Regulation –REQUIRES_PMS→ PMSObligation –DUE_ON→ Deadline
Certificate –DUE_ON→ Deadline
```

`Deadline` as a shared node means one query answers "what is due" across every obligation type.

### AI governance — Q11, Q12

```
AIModel –EMBEDDED_IN→ MarketedDevice
AIModel –TRAINED_ON→ TrainingDataset
AIModel –MAKES_ASSUMPTION→ Assumption
AIModel –VALIDATED_BY→ Validation –RAISED→ Finding
Control –SATISFIES→ Regulation
```

Deliberately the same shape as `bank-model-risk-kg`. A regulator asking *"what was this model
trained on, what did you assume, when did you last validate it, what is still open?"* is asking
what a bank supervisor asks about a credit model. Reusing the structure is the design, not a
shortcut.

### Cybersecurity — Q13

```
MarketedDevice –CONTAINS_COMPONENT→ SoftwareComponent –HAS_VULNERABILITY→ Vulnerability
```

FDA premarket submissions now require a software bill of materials. A published vulnerability in a
common component reaches every device containing it in two hops.

---

## 6. What the schema does not claim

- **Predicate chains are partial.** Unresolved claims are represented, not hidden. Coverage is
  bounded by which clearances publish a summary PDF and by name-resolution success — neither is
  yet quantified.
- **Authorisation and market are separate.** There is no reliable `Submission → MarketedDevice`
  edge, because the FDA does not publish one.
- **Standards carry identifiers only.** ISO/IEC texts are paywalled, so `Standard` nodes hold
  designations and clause references. GraphRAG cannot retrieve standard prose.
- **The AI-governance and cybersecurity layers are unpopulated.** They are modelled so the ontology
  does not need redesigning when a source is found.
- **`TechnicalFile` is customer data**, not public. It is modelled because Q2 and Q4 need it in a
  deployed system, and it marks where a customer's own data would attach.

---

## 7. Open items

1. **Predicate resolution rate** — what fraction of `PredicateClaim.raw_name` values resolve to a
   K-number? Determines how complete Q6 can be. Needs a ~100-summary sample.
2. **FDA AI/ML-Enabled Device List** — is it published as data? Decides whether Q11 and Q12 are
   populated or design-only.
3. **De Novo** — `DEN…` authorisations appear inside the 510(k) endpoint and need separating, since
   by definition they have no predicate.
4. **PMA supplements** — one PMA has many supplements; is a supplement a `Submission` or a property
   of one?
5. **EU jurisdiction modelling** — the EU is one regulation but many national registrations;
   whether `Jurisdiction` is the EU, the member state, or both.
6. **`ProductCode` granularity** — is a product code sometimes too coarse to be a useful join for
   post-market clustering?
7. **`Deadline.due_date` index behaviour is declared, not benchmarked.** The index is created and
   the statement executes; whether the engine plans a range scan over it has not been measured.
   Settle it with the benchmark suite, not by assertion.

---

## 8. Verified against the engine

`schema/regulatory_affairs_kg.cypher` was executed statement by statement against a clean
**Samyama-Graph 1.1.0** instance. All 28 statements run clean; `tests/test_schema_cypher.py` keeps
it that way and skips when no engine is reachable.

Reading the file twice did not catch what running it once did. Three things changed as a result:

**The Neo4j-5 constraint syntax does not parse.** The file used
`CREATE CONSTRAINT <name> IF NOT EXISTS FOR (n:L) REQUIRE n.p IS UNIQUE` — inherited from the
scaffold. All 27 constraints failed with a parse error. 1.1.0 accepts only:

```cypher
CREATE CONSTRAINT ON (n:Label) ASSERT n.prop IS UNIQUE
```

Worth reporting upstream: the engine's own `docs/CYPHER_COMPATIBILITY.md` documents the
`FOR … REQUIRE` form as supported. It is not.

**A constraint is not an insert guard.** With a uniqueness constraint in place, two `CREATE`
statements carrying the same key both succeed — the duplicate is only detected when the constraint
is next created. `MERGE` deduplicates correctly. **Loaders must `MERGE` on the key**; nothing here
prevents a double insert. This is the finding with the most consequence for the ETL work.

**`CREATE VECTOR INDEX` does not exist in the parser.** The vector index is created over REST —
`POST /api/vector/indexes` with `{"label":…,"property_key":…,"dimensions":384,"metric":"cosine"}` —
so it belongs in the eCFR loader, not in this file.

One engine bug found in passing, unrelated to the schema but relevant to the benchmark suite: an
inline property pattern combined with an aggregate ignores the filter. `MATCH (x:L {id:'B'})
RETURN count(x)` returns the count for the whole label, while `MATCH (x:L) WHERE x.id = 'B'`
returns the correct count. **Use `WHERE`, not inline property maps, in anything that aggregates.**
