# Regulatory Affairs KG — query results

> Measured 2026-08-27T06:55:31+00:00 · Samyama-Graph **1.7.0** (image `public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0`) · local Docker
> 28,496 nodes, 25,310 edges

**Timings are client round-trip, not engine execution time.** The clock
starts before the HTTP request and stops after the JSON is decoded, so
connection setup, transfer and parsing are all inside the figure. It is
what a caller waits for, which is why it is the number reported — but it
is not what the engine spends inside the query.

Every figure on this page is written by `python -m benchmarks.run_queries`.
Nothing is typed in. That is the same standard the dataset card is held
to: every figure in [`../DATASET-CARD.md`](../DATASET-CARD.md) is written
by `etl/probe_openfda.py` rather than being remembered.

---

## What is in the graph

| Label | Count |
|---|---:|
| `Submission` | 19,127 |
| `ProductCode` | 7,085 |
| `Regulation` | 2,284 |
| **Total nodes** | **28,496** |

| Edge type | Count |
|---|---:|
| `CLASSIFIED_AS` | 19,127 |
| `GOVERNED_BY` | 6,183 |
| **Total edges** | **25,310** |

**A blank `definition` cell is missing source data, not a parse failure.** 4,487 of 7,085 product codes carry no definition in openFDA — 63%, counted by this run.

This is a **bounded slice**, not the full 31,120,490 openFDA records —
see [`../DATASET-CARD.md`](../DATASET-CARD.md) for what was loaded and why.

---

## 1. Change impact — every clearance under one rule

**A rule changes. Which clearances are affected?**

The question this graph exists for. Two hops, THROUGH THE PRODUCT CODE — this text used to say the join runs on the clearance's own `regulation_number`, and the Cypher below has never touched that property. Both are exact joins on government-issued codes, but they are different paths. Measured on the loaded graph: all 19,127 clearances are reachable this way and the regulation reached matches the one on the record every time. In a relational schema this is the query that needs the join written by hand each time the shape of the question changes.

```cypher
MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission) WHERE r.cfr_section = '870.5150' RETURN count(s) AS clearances
```

**6.2 ms** median of 5 (min 5.3, max 7.5)

| clearances |
|---|
| 415 |


## 2. Change impact — the affected clearances themselves

**Which specific devices are affected, and whose are they?**

The same traversal, returning rows rather than a count.

```cypher
MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission) WHERE r.cfr_section = '870.5150' RETURN s.id AS clearance, s.applicant AS applicant, s.decision_date AS decided ORDER BY s.decision_date DESC LIMIT 5
```

**7.3 ms** median of 5 (min 4.6, max 9.0)

| clearance | applicant | decided |
|---|---|---|
| K261105 | Inquis Medical | 2026-07-27 |
| K262030 | Inquis Medical | 2026-07-15 |
| K260599 | Penumbra, Inc. | 2026-04-24 |
| K253589 | Medtronic Interventional Vascular, Inc. | 2026-04-23 |
| K254208 | Expanse Medical, Inc. | 2026-03-20 |


## 3. Busiest regulations

**Where would a rule change hurt most?**

An aggregation across the whole graph. Answers which rules carry the most clearances, which is where regulatory attention goes.

```cypher
MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission) RETURN r.cfr_section AS cfr_section, count(s) AS clearances ORDER BY clearances DESC LIMIT 10
```

**21.4 ms** median of 5 (min 21.2, max 22.1)

| cfr_section | clearances |
|---|---|
| 870.1250 | 1685 |
| 870.1130 | 1219 |
| 870.1025 | 894 |
| 870.2700 | 832 |
| 870.1330 | 807 |
| 870.2300 | 771 |
| 870.1340 | 714 |
| 870.1200 | 626 |
| 870.3680 | 619 |
| 870.2340 | 578 |


## 4. Device to law

**Which rules must this product code comply with?**

One hop from the join hub. `product_code` is on every openFDA endpoint; device *names* are not consistent between them, so a name is never the key.

```cypher
MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) WHERE p.product_code = 'DXY' RETURN p.definition AS device_category, p.device_class AS class, r.cfr_section AS cfr_section
```

**1.3 ms** median of 5 (min 1.2, max 1.3)

| device_category | class | cfr_section |
|---|---|---|
|  | 3 | 870.3610 |


## 5. Law to device categories

**Which device categories does one rule govern?**

The reverse of the above, and the first half of a change-impact answer.

```cypher
MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) WHERE r.cfr_section = '870.5150' RETURN p.product_code AS product_code, p.definition AS category LIMIT 10
```

**1.2 ms** median of 5 (min 1.2, max 1.4)

| product_code | category |
|---|---|
| MMX |  |
| QEX | To mechanically disrupt thrombus and/or debris prior to removal from the coronary vasculature through aspiration. |
| QEZ | To remove thrombus from the peripheral and/or coronary vasculature through aspiration. |
| QEW | To mechanically disrupt thrombus and/or debris prior to removal from the peripheral vasculature through aspiration. |
| DXE |  |
| QEY | To mechanically disrupt thrombus and/or debris in the peripheral vasculature. |


## 6. Clearance to rule

**Which rule governs this one clearance?**

A point lookup through two hops — the query an inspector runs.

```cypher
MATCH (s:Submission)-[:CLASSIFIED_AS]->(p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) WHERE s.id = 'K233820' RETURN s.device_name AS device, p.product_code AS product_code, r.cfr_section AS cfr_section
```

**1.2 ms** median of 5 (min 1.2, max 1.3)

| device | product_code | cfr_section |
|---|---|---|
| Fogarty Arterial Embolectomy Catheter with Gate Valve | DXE | 870.5150 |


## 7. Clearances by reviewing authority

**Which FDA advisory committees review the most clearances?**

A grouping over 19,127 submissions. **The distribution is an artifact of the slice, not a finding about the FDA**: this graph holds 21 CFR part 870 clearances, so Cardiovascular leads by construction. What the query demonstrates is the grouping, not the ranking. Note also this is *decided* clearances — openFDA publishes no pending queue, so 'pending by authority' has no answer in this data.

```cypher
MATCH (s:Submission) WHERE s.advisory_committee IS NOT NULL RETURN s.advisory_committee AS committee, count(s) AS clearances ORDER BY clearances DESC LIMIT 10
```

**22.0 ms** median of 5 (min 16.9, max 25.1)

| committee | clearances |
|---|---|
| Cardiovascular | 19126 |
| Anesthesiology | 1 |


## 8. High-risk device categories with a rule attached

**How many Class III categories carry a regulation in this slice?**

A filtered scan with a join — the population a reviewer starts from. It is NOT the count of Class III categories: this slice holds 531, and only the ones with a GOVERNED_BY edge are joinable here.

```cypher
MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) WHERE p.device_class = '3' RETURN count(DISTINCT p) AS class_three_with_a_regulation
```

**9.7 ms** median of 5 (min 8.9, max 10.9)

| class_three_with_a_regulation |
|---|
| 145 |


## 9. Provenance

**What is in this graph, and where did it come from?**

The question a reviewer asks before trusting any answer above. Every node carries the openFDA endpoint it was built from.

```cypher
MATCH (n) WHERE n.source IS NOT NULL RETURN n.source AS source, count(n) AS nodes ORDER BY nodes DESC
```

**26.4 ms** median of 5 (min 22.1, max 47.2)

| source | nodes |
|---|---|
| device/510k | 19127 |
| device/classification | 9369 |


---

## Does a uniqueness constraint create an index?

**Measured on this run, not remembered.** `schema/regulatory_affairs_kg.cypher`
declares `ASSERT s.id IS UNIQUE` for each MERGE key. Whether that also indexes
the key decides whether every point lookup scans the label — and it is the
answer this page was originally built around, so it is re-derived each run
rather than asserted.

**On this engine it does.** A constraint declared on a probe label produced a `BTREE` entry in `SHOW INDEXES` immediately, with no `CREATE INDEX`.

This page previously stated the opposite as its headline finding, and that
statement was written against an earlier engine and never re-checked. It is
recorded here because the correction matters more than the original claim:
a page whose argument is that its numbers were measured should not carry a
conclusion that stopped being true.

The practical consequence is that the MERGE keys are **already indexed** by
the schema on a freshly-loaded graph, so the before/after comparison below
has nothing unindexed left to measure and correctly refuses.

| Key | Nodes | Scan | Indexed | Speedup |
|---|---:|---:|---:|---:|
| `Submission.id` | 19,127 | — | — | _already indexed — unindexed figure not measurable here_ |
| `ProductCode.product_code` | 7,085 | — | — | _already indexed — unindexed figure not measurable here_ |
| `Regulation.cfr_section` | 2,284 | — | — | _already indexed — unindexed figure not measurable here_ |

**These timings are not all unindexed.** `ConstraintIndexProbe.probe_id`, `ProductCode.product_code`, `Regulation.cfr_section`, `Submission.id` carried an index before this run started. Any query above that looks one of those up is an indexed figure, and the index comparison below refuses to report a speedup it cannot measure. For unindexed timings, run against a fresh instance.

---

## What the timings mean

The slowest query here is **provenance** at 26.4 ms. Every query is a median of 5 runs,
because a single reading on a warm cache is not a measurement.

These are **not** a comparison against another database. Nothing here has
been run against Postgres, so no claim about relative speed appears on this
page. What the timings show is that the change-impact traversal is
interactive on this data — which is the property the demo depends on.

The graph is small by design. A bounded slice was loaded so the demo starts
quickly; these figures say nothing about behaviour at 31 million records.
