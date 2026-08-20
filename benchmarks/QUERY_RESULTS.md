# Regulatory Affairs KG — query results

> Measured 2026-08-20T06:17:40+00:00 · Samyama-Graph 1.1.0 · local Docker
> 28,496 nodes, 25,310 edges

Every figure on this page is written by `python -m benchmarks.run_queries`.
Nothing is typed in — the same standard `etl/probe_openfda.py` holds the
dataset card to.

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

This is a **bounded slice**, not the full 31,120,490 openFDA records —
see `DATASET-CARD.md` for what was loaded and why.

---

## 1. Change impact — every clearance under one rule

**A rule changes. Which clearances are affected?**

The question this graph exists for. Two hops, because `regulation_number` is on the clearance record itself — an exact government-issued join, not a name match. In a relational schema this is the query that needs the join written by hand each time the shape of the question changes.

```cypher
MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission) WHERE r.cfr_section = '870.5150' RETURN count(s) AS clearances
```

**6.9 ms** median of 5 (min 4.7, max 8.8)

| clearances |
|---|
| 415 |


## 2. Change impact — the affected clearances themselves

**Which specific devices are affected, and whose are they?**

The same traversal, returning rows rather than a count.

```cypher
MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission) WHERE r.cfr_section = '870.5150' RETURN s.id AS clearance, s.applicant AS applicant, s.decision_date AS decided ORDER BY s.decision_date DESC LIMIT 5
```

**9.4 ms** median of 5 (min 7.6, max 9.9)

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

**22.1 ms** median of 5 (min 21.3, max 29.5)

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

**28.5 ms** median of 5 (min 24.1, max 37.3)

| device_category | class | cfr_section |
|---|---|---|
| None | 3 | 870.3610 |


## 5. Law to device categories

**Which device categories does one rule govern?**

The reverse of the above, and the first half of a change-impact answer.

```cypher
MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) WHERE r.cfr_section = '870.5150' RETURN p.product_code AS product_code, p.definition AS category LIMIT 10
```

**1.9 ms** median of 5 (min 1.9, max 2.4)

| product_code | category |
|---|---|
| MMX | None |
| QEX | To mechanically disrupt thrombus and/or debris prior to removal from the coronary vasculature through aspiration. |
| QEZ | To remove thrombus from the peripheral and/or coronary vasculature through aspiration. |
| QEW | To mechanically disrupt thrombus and/or debris prior to removal from the peripheral vasculature through aspiration. |
| DXE | None |
| QEY | To mechanically disrupt thrombus and/or debris in the peripheral vasculature. |


## 6. Clearance to rule

**Which rule governs this one clearance?**

A point lookup through two hops — the query an inspector runs.

```cypher
MATCH (s:Submission)-[:CLASSIFIED_AS]->(p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) WHERE s.id = 'K233820' RETURN s.device_name AS device, p.product_code AS product_code, r.cfr_section AS cfr_section
```

**81.2 ms** median of 5 (min 79.8, max 82.5)

| device | product_code | cfr_section |
|---|---|---|
| Fogarty Arterial Embolectomy Catheter with Gate Valve | DXE | 870.5150 |


## 7. Clearances by reviewing authority

**Which FDA advisory committees review the most clearances?**

A grouping over 19,127 submissions. Note this is *decided* clearances — openFDA publishes no pending queue, so 'pending by authority' has no answer in this data.

```cypher
MATCH (s:Submission) WHERE s.advisory_committee IS NOT NULL RETURN s.advisory_committee AS committee, count(s) AS clearances ORDER BY clearances DESC LIMIT 10
```

**14.2 ms** median of 5 (min 13.8, max 14.4)

| committee | clearances |
|---|---|
| Cardiovascular | 19126 |
| Anesthesiology | 1 |


## 8. High-risk device categories

**Which device categories take the Class III route?**

A filtered scan with a join — the population a reviewer starts from.

```cypher
MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation) WHERE p.device_class = '3' RETURN count(p) AS class_three_categories
```

**11.0 ms** median of 5 (min 9.1, max 11.5)

| class_three_categories |
|---|
| 145 |


## 9. Provenance

**What is in this graph, and where did it come from?**

The question a reviewer asks before trusting any answer above. Every node carries the openFDA endpoint it was built from.

```cypher
MATCH (n) WHERE n.source IS NOT NULL RETURN n.source AS source, count(n) AS nodes ORDER BY nodes DESC
```

**21.4 ms** median of 5 (min 20.3, max 23.9)

| source | nodes |
|---|---|
| device/510k | 19127 |
| device/classification | 9369 |


---

## A uniqueness constraint does not create an index

The single most useful thing this suite measured. `schema/regulatory_affairs_kg.cypher`
declares `ASSERT s.id IS UNIQUE` for each MERGE key — and a point lookup on
one of those keys still scans the whole label.

| Key | Nodes | Scan | Indexed | |
|---|---:|---:|---:|---:|
| `Submission.id` | 19,127 | 160.1 ms | 1.3 ms | **124×** |
| `ProductCode.product_code` | 7,085 | 65.6 ms | 1.2 ms | **55×** |
| `Regulation.cfr_section` | 2,284 | 19.7 ms | 1.2 ms | **16×** |

The speedup tracks label size almost exactly, which is what a full scan
looks like. The schema already records that a constraint in 1.1.0 declares
the key rather than guarding an insert; it does not index it either, and
that had not been measured until now.

Every timing in the queries above is the **unindexed** figure, because that
is what the shipped schema produces today. The indexes are created at the
end of this run, so nothing above benefits from them.

---

## What the timings mean

The slowest query here is **clearance to rule** at 81.2 ms. Every query is a median of 5 runs,
because a single reading on a warm cache is not a measurement.

These are **not** a comparison against another database. Nothing here has
been run against Postgres, so no claim about relative speed appears on this
page. What the timings show is that the change-impact traversal is
interactive on this data — which is the property the demo depends on.

The graph is small by design. A bounded slice was loaded so the demo starts
quickly; these figures say nothing about behaviour at 31 million records.
