# Regulatory Affairs KG — query results

> Measured 2026-08-21T09:36:43+00:00 · Samyama-Graph 1.1.0 · local Docker
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

**A blank `definition` cell is missing source data, not a parse failure.** 4,487 of 7,085 product codes carry no definition in openFDA — 63%, measured by this run's own provenance query.

This is a **bounded slice**, not the full 31,120,490 openFDA records —
see [`../DATASET-CARD.md`](../DATASET-CARD.md) for what was loaded and why.

---

## 1. Change impact — every clearance under one rule

**A rule changes. Which clearances are affected?**

The question this graph exists for. Two hops, because `regulation_number` is on the clearance record itself — an exact government-issued join, not a name match. In a relational schema this is the query that needs the join written by hand each time the shape of the question changes.

```cypher
MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission) WHERE r.cfr_section = '870.5150' RETURN count(s) AS clearances
```

**8.9 ms** median of 5 (min 7.4, max 9.9)

| clearances |
|---|
| 415 |


## 2. Change impact — the affected clearances themselves

**Which specific devices are affected, and whose are they?**

The same traversal, returning rows rather than a count.

```cypher
MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission) WHERE r.cfr_section = '870.5150' RETURN s.id AS clearance, s.applicant AS applicant, s.decision_date AS decided ORDER BY s.decision_date DESC LIMIT 5
```

**7.0 ms** median of 5 (min 6.3, max 9.7)

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

**21.8 ms** median of 5 (min 21.1, max 24.7)

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

**35.6 ms** median of 5 (min 32.7, max 65.1)

| device_category | class | cfr_section |
|---|---|---|
|  | 3 | 870.3610 |


## 5. Law to device categories

**Which device categories does one rule govern?**

The reverse of the above, and the first half of a change-impact answer.

```cypher
MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) WHERE r.cfr_section = '870.5150' RETURN p.product_code AS product_code, p.definition AS category LIMIT 10
```

**2.2 ms** median of 5 (min 1.9, max 3.5)

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

**126.6 ms** median of 5 (min 77.4, max 140.3)

| device | product_code | cfr_section |
|---|---|---|
| Fogarty Arterial Embolectomy Catheter with Gate Valve | DXE | 870.5150 |


## 7. Clearances by reviewing authority

**Which FDA advisory committees review the most clearances?**

A grouping over 19,127 submissions. **The distribution is an artifact of the slice, not a finding about the FDA**: this graph holds 21 CFR part 870 clearances, so Cardiovascular leads by construction. What the query demonstrates is the grouping, not the ranking. Note also this is *decided* clearances — openFDA publishes no pending queue, so 'pending by authority' has no answer in this data.

```cypher
MATCH (s:Submission) WHERE s.advisory_committee IS NOT NULL RETURN s.advisory_committee AS committee, count(s) AS clearances ORDER BY clearances DESC LIMIT 10
```

**15.6 ms** median of 5 (min 14.2, max 18.3)

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

**11.0 ms** median of 5 (min 8.7, max 11.7)

| class_three_with_a_regulation |
|---|
| 145 |


## 9. Provenance

**What is in this graph, and where did it come from?**

The question a reviewer asks before trusting any answer above. Every node carries the openFDA endpoint it was built from.

```cypher
MATCH (n) WHERE n.source IS NOT NULL RETURN n.source AS source, count(n) AS nodes ORDER BY nodes DESC
```

**21.8 ms** median of 5 (min 19.3, max 23.2)

| source | nodes |
|---|---|
| device/510k | 19127 |
| device/classification | 9369 |


---

## A uniqueness constraint does not create an index

The single most useful thing this suite measured. `schema/regulatory_affairs_kg.cypher`
declares `ASSERT s.id IS UNIQUE` for each MERGE key — and a point lookup on
one of those keys still scans the whole label.

| Key | Nodes | Scan | Indexed | Speedup |
|---|---:|---:|---:|---:|
| `Submission.id` | 19,127 | 143.6 ms | 1.3 ms | **110×** |
| `ProductCode.product_code` | 7,085 | 50.9 ms | 1.2 ms | **44×** |
| `Regulation.cfr_section` | 2,284 | 20.0 ms | 1.1 ms | **17×** |

The speedup tracks label size almost exactly, which is what a full scan
looks like. The schema already records that a constraint in 1.1.0 declares
the key rather than guarding an insert; it does not index it either, and
that had not been measured until now.

Every timing in the queries above is the **unindexed** figure, because that
is what the shipped schema produces today. The indexes, where this run
created any, are created at the end, so nothing above benefits from them.

---

## What the timings mean

The slowest query here is **clearance to rule** at 126.6 ms. Every query is a median of 5 runs,
because a single reading on a warm cache is not a measurement.

These are **not** a comparison against another database. Nothing here has
been run against Postgres, so no claim about relative speed appears on this
page. What the timings show is that the change-impact traversal is
interactive on this data — which is the property the demo depends on.

The graph is small by design. A bounded slice was loaded so the demo starts
quickly; these figures say nothing about behaviour at 31 million records.
