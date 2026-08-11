// Medical-Device Regulatory Affairs Knowledge Graph — schema
//
// Rationale for every shape below is in docs/schema.md ("Why these shapes").
// Source reality — what the data actually supports — is in docs/sources/openfda-devices.md.
//
// Two tiers:
//   TIER 1  populated from openFDA and the 510(k) Summary PDFs
//   TIER 2  modelled, not yet populated (no source identified or not yet ingested)
//
// SYNTAX — every statement here has been executed against Samyama-Graph 1.1.0
// (see tests/test_schema_cypher.py). Constraints use the `ON (n:L) ASSERT`
// form: the Neo4j-5 `CREATE CONSTRAINT <name> IF NOT EXISTS FOR (n:L) REQUIRE`
// form does NOT parse in 1.1.0, despite appearing in the engine's
// CYPHER_COMPATIBILITY.md. Do not "modernise" these back.
//
// A constraint here is a DECLARATION OF THE KEY, not an insert guard —
// 1.1.0 does not reject a duplicate CREATE. Loaders must MERGE on the key.
//
// =============================================================================
// TIER 1 — uniqueness constraints on populated labels
// =============================================================================

// Submission — a 510(k), PMA or De Novo. Decision is a property, not a separate
// node: one source record carries both date_received and decision_date.
//
// Key is `id`, not k_number/pma_number, because one label carries three FDA
// identifier namespaces. The FDA already prefixes them and they cannot collide
// (K/DEN = 510(k) endpoint, P = PMA endpoint), so `id` is the source number
// VERBATIM, uppercased, no synthetic prefix:
//     K233820    510(k) clearance
//     DEN160001  De Novo authorisation
//     P980012    PMA approval
// The originating number is also kept in k_number | pma_number for provenance.
CREATE CONSTRAINT ON (s:Submission) ASSERT s.id IS UNIQUE;

// ProductCode — the join hub. Every openFDA endpoint carries product_code;
// device names vary between endpoints, product codes do not.
CREATE CONSTRAINT ON (p:ProductCode) ASSERT p.product_code IS UNIQUE;

// Regulation — a 21 CFR section (e.g. 870.5150), from openfda.regulation_number.
CREATE CONSTRAINT ON (r:Regulation) ASSERT r.cfr_section IS UNIQUE;

CREATE CONSTRAINT ON (m:Manufacturer) ASSERT m.fei_number IS UNIQUE;
CREATE CONSTRAINT ON (e:Establishment) ASSERT e.registration_number IS UNIQUE;

// MarketedDevice — a UDI record. Deliberately NOT merged with Submission:
// UDI records do not reliably carry the K-number of the clearance that
// authorised them, so no Submission -> MarketedDevice edge is asserted.
CREATE CONSTRAINT ON (d:MarketedDevice) ASSERT d.udi_di IS UNIQUE;

// Recall — ONE node per recall_number, carrying the enforcement report as
// properties. device/recall and device/enforcement are two views of the same
// event sharing recall_number; modelling them as two nodes joined by an edge
// would collide on this constraint at load time. Same argument as Submission
// and its decision (docs/schema.md §2.3).
CREATE CONSTRAINT ON (rc:Recall) ASSERT rc.recall_number IS UNIQUE;
CREATE CONSTRAINT ON (ae:AdverseEvent) ASSERT ae.report_number IS UNIQUE;

// PredicateClaim — the predicate as printed in the 510(k) Summary PDF, before
// resolution. Modelled as a node so an UNRESOLVED predicate is representable:
// predicates are free-text device names and may name a pre-1976 device with no
// clearance record. Dropping them would make a truncated chain look complete.
//
// id is DETERMINISTIC, not a counter — sha1("<source_k_number>|<normalised
// raw_name>") where normalisation is casefold + collapse whitespace + strip
// punctuation. Re-running the extractor therefore MERGEs rather than
// duplicating, and the constraint enforces something real.
//
// One claim node PER CITING SUBMISSION: two clearances naming the same
// predicate produce two PredicateClaim nodes that both RESOLVE_TO the same
// Submission. Claims are the citation as filed, not a shared entity — so
// ancestry (Q6) is a DAG, and "how many devices cite this predecessor" is a
// count of incoming RESOLVES_TO.
CREATE CONSTRAINT ON (pc:PredicateClaim) ASSERT pc.id IS UNIQUE;

// Standard — identifier and clause references ONLY. ISO/IEC texts are
// paywalled and are never ingested.
CREATE CONSTRAINT ON (st:Standard) ASSERT st.designation IS UNIQUE;

// =============================================================================
// TIER 1 — edges
// =============================================================================

// Change blast-radius (Q1): a rule change reaches every affected submission in
// two hops. Works because regulation_number is on every clearance, approval and
// classification record — an exact government-issued join, not a name match.
// (:Submission)-[:CLASSIFIED_AS]->(:ProductCode)
// (:ProductCode)-[:GOVERNED_BY]->(:Regulation)

// Applicant and facility
// (:Submission)-[:SUBMITTED_BY]->(:Manufacturer)
// (:Manufacturer)-[:MANUFACTURED_AT]->(:Establishment)
// (:Establishment)-[:LISTS]->(:ProductCode)

// Predicate lineage (Q6, Q7) — chains of unknown depth
// (:Submission)-[:CITES_PREDICATE]->(:PredicateClaim)
// (:PredicateClaim)-[:RESOLVES_TO]->(:Submission)      // only when resolvable

// Conformity evidence (Q2) — from the 510(k) Summary PDFs
// (:Submission)-[:CONFORMS_TO]->(:Standard)

// Market side
// (:MarketedDevice)-[:MARKETED_AS]->(:ProductCode)

// Post-market signal (Q8) — routed through ProductCode, not device names,
// which is what makes clustering by manufacturer or facility possible
// (:Recall)-[:AFFECTS]->(:ProductCode|:MarketedDevice)
// (:AdverseEvent)-[:REPORTED_AGAINST]->(:ProductCode|:MarketedDevice)
//
// No ENFORCED_AS edge: the enforcement report is the same Recall node, not a
// second one (see the constraint above).

// =============================================================================
// TIER 2 — modelled, not yet populated
// =============================================================================

// Clause — paragraph-level obligation text from eCFR / EUR-Lex. Regulation is
// section-level (from openFDA); Clause carries the prose GraphRAG retrieves.
// Clause.text holds the obligation, Clause.embedding a 384-dim vector.
CREATE CONSTRAINT ON (c:Clause) ASSERT c.id IS UNIQUE;
CREATE CONSTRAINT ON (g:Guidance) ASSERT g.id IS UNIQUE;
CREATE CONSTRAINT ON (j:Jurisdiction) ASSERT j.id IS UNIQUE;
CREATE CONSTRAINT ON (rg:Registration) ASSERT rg.id IS UNIQUE;
CREATE CONSTRAINT ON (nb:NotifiedBody) ASSERT nb.id IS UNIQUE;
CREATE CONSTRAINT ON (ct:Certificate) ASSERT ct.id IS UNIQUE;
CREATE CONSTRAINT ON (tf:TechnicalFile) ASSERT tf.id IS UNIQUE;
CREATE CONSTRAINT ON (po:PMSObligation) ASSERT po.id IS UNIQUE;

// Deadline is a NODE, not a date property, so "what is due in the next 90 days
// across every obligation type" is one traversal rather than a scan per label.
// The date lives in dl.due_date (ISO 8601, YYYY-MM-DD). The uniqueness
// constraint is on id and does nothing for a date range, so the range scan
// needs its own index — otherwise Q5 is a full label scan and the "one
// traversal" claim is not true.
CREATE CONSTRAINT ON (dl:Deadline) ASSERT dl.id IS UNIQUE;
CREATE INDEX ON :Deadline(due_date);

// AI governance (Q11, Q12) — shapes taken from bank-model-risk-kg rather than
// reinvented. Model governance is domain-independent: "what was it trained on,
// what did you assume, when was it last validated, what is still open" is the
// same question a bank supervisor asks about a credit model.
CREATE CONSTRAINT ON (am:AIModel) ASSERT am.id IS UNIQUE;
CREATE CONSTRAINT ON (td:TrainingDataset) ASSERT td.id IS UNIQUE;
CREATE CONSTRAINT ON (a:Assumption) ASSERT a.id IS UNIQUE;
CREATE CONSTRAINT ON (v:Validation) ASSERT v.id IS UNIQUE;
CREATE CONSTRAINT ON (f:Finding) ASSERT f.id IS UNIQUE;
CREATE CONSTRAINT ON (co:Control) ASSERT co.id IS UNIQUE;

// Cybersecurity (Q13) — FDA premarket submissions now require an SBOM.
CREATE CONSTRAINT ON (sc:SoftwareComponent) ASSERT sc.id IS UNIQUE;
CREATE CONSTRAINT ON (vu:Vulnerability) ASSERT vu.id IS UNIQUE;

// Privacy (Q14, Q15) — the obligation attaches to the DATA, never to the device.
// A device is not subject to HIPAA; a dataset is. The same scanner yields a
// de-identified extract in one hospital and identifiable records in another.
// DataCategory is the hinge: evidence -> category -> regime (docs/schema.md §2.7).
CREATE CONSTRAINT ON (pr:PrivacyRegime) ASSERT pr.id IS UNIQUE;
CREATE CONSTRAINT ON (dc:DataCategory) ASSERT dc.id IS UNIQUE;
CREATE CONSTRAINT ON (pp:ProcessingPurpose) ASSERT pp.id IS UNIQUE;
CREATE CONSTRAINT ON (lb:LawfulBasis) ASSERT lb.id IS UNIQUE;
CREATE CONSTRAINT ON (dp:DPIA) ASSERT dp.id IS UNIQUE;
CREATE CONSTRAINT ON (dt:DataTransfer) ASSERT dt.id IS UNIQUE;

// Data quality, device-facing (Q16) — was the training data representative of
// the intended-use population? `dimension` carries representativeness |
// completeness | accuracy | timeliness | consistency, so a bias evaluation is a
// check with dimension='representativeness', not a label of its own. Raises the
// SAME Finding node the AI-governance layer raises, so "what is open on this
// model" returns validation and data-quality findings together.
CREATE CONSTRAINT ON (dq:DataQualityCheck) ASSERT dq.id IS UNIQUE;

// Data quality, graph-facing (Q17) — NO node. Provenance rides as properties on
// Tier-1 nodes: source, retrieved_at, extraction_method
// (api | pdf-text | pdf-ocr | derived); PredicateClaim additionally carries
// resolution_confidence and resolution_method.
//
// Provenance is FILTERED, findings are TRAVERSED — shape follows use. A
// provenance node per record would roughly double the node count to carry four
// fields nobody walks through (docs/schema.md §2.8).
//
// The loader must write these or leave them NULL. A defaulted confidence would
// look like a measurement while being a guess.
CREATE INDEX ON :PredicateClaim(resolution_confidence);

// Tier-2 edges
//
// Endpoints here are PROVISIONAL — they are the shape the question requires,
// and the source that will populate each is named in docs/schema.md §3. They
// are written down rather than left out so that no Tier-2 label is declared
// without a path to the question it serves.
// (:Regulation)-[:HAS_CLAUSE]->(:Clause)
// (:Registration)-[:APPLIES_IN]->(:Jurisdiction)
// (:Certificate)-[:CERTIFIED_BY]->(:NotifiedBody)
// (:Regulation)-[:REQUIRES_PMS]->(:PMSObligation)

// Attaching edges — without these, four Tier-2 labels are unreachable and the
// questions they exist for cannot be traversed at all.
// (:TechnicalFile)-[:REFERENCES_STANDARD]->(:Standard)   // Q2 — the superseded-edition question
// (:TechnicalFile)-[:DOCUMENTS]->(:Submission|:MarketedDevice)  // Q4
// (:Guidance)-[:APPLIES_TO]->(:ProductCode|:Regulation)  // Q3
// (:Registration)-[:REGISTERS]->(:MarketedDevice|:ProductCode)  // Q10 — market coverage
// (:Certificate)-[:COVERS]->(:TechnicalFile|:MarketedDevice)    // Q5 — what the certificate is for

// Privacy (Q14) — obligation reached through the data, not the device
// (:TrainingDataset|:TechnicalFile|:AdverseEvent)-[:CONTAINS_DATA]->(:DataCategory)
// (:DataCategory)-[:REGULATED_BY]->(:PrivacyRegime)
// (:PrivacyRegime)-[:ENFORCED_IN]->(:Jurisdiction)
// (:TrainingDataset|:TechnicalFile)-[:PROCESSED_FOR]->(:ProcessingPurpose)
// (:ProcessingPurpose)-[:JUSTIFIED_BY]->(:LawfulBasis)
// (:DPIA)-[:ASSESSES]->(:ProcessingPurpose)

// Cross-border transfer (Q15) — same shape rotated. Both endpoints are the
// Jurisdiction nodes the market-access questions already use: privacy and
// market access share the jurisdiction spine, which is why this layer is cheap.
// (:DataTransfer)-[:MOVES]->(:DataCategory)
// (:DataTransfer)-[:ORIGINATES_IN]->(:Jurisdiction)
// (:DataTransfer)-[:LANDS_IN]->(:Jurisdiction)

// Data quality, device-facing (Q16)
// (:TrainingDataset|:Submission|:MarketedDevice)-[:ASSESSED_BY]->(:DataQualityCheck)
// (:DataQualityCheck)-[:RAISED]->(:Finding)     // same Finding as Validation raises
// (:PMSObligation|:Certificate)-[:DUE_ON]->(:Deadline)
// (:AIModel)-[:EMBEDDED_IN]->(:MarketedDevice)
// (:AIModel)-[:TRAINED_ON]->(:TrainingDataset)
// (:AIModel)-[:MAKES_ASSUMPTION]->(:Assumption)
// (:AIModel)-[:VALIDATED_BY]->(:Validation)
// (:Validation)-[:RAISED]->(:Finding)
// (:Control)-[:SATISFIES]->(:Regulation|:Clause)
// (:MarketedDevice)-[:CONTAINS_COMPONENT]->(:SoftwareComponent)
// (:SoftwareComponent)-[:HAS_VULNERABILITY]->(:Vulnerability)

// =============================================================================
// Vector index — GraphRAG over obligation text (Tier 2, with Clause)
// =============================================================================
// Clause.embedding: 384-dim (all-MiniLM-L6-v2 via fastembed), cosine, HNSW.
// A natural-language question is embedded, the nearest clauses retrieved, then
// each hit grounded via GOVERNED_BY / SATISFIES so the answer is citable rather
// than generated. Same pattern as bank-model-risk-kg/etl/graphrag.py.
//
// NOT a Cypher statement. Verified against Samyama-Graph 1.1.0: there is no
// `CREATE VECTOR INDEX` in the parser — the vector index is created over REST:
//
//   POST /api/vector/indexes
//   {"label":"Clause","property_key":"embedding","dimensions":384,"metric":"cosine"}
//
// The eCFR loader creates it after Clause is populated; creating it earlier
// indexes nothing.
