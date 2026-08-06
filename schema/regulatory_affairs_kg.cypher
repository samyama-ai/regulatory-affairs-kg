// Medical-Device Regulatory Affairs Knowledge Graph — schema
//
// Rationale for every shape below is in docs/schema.md ("Why these shapes").
// Source reality — what the data actually supports — is in docs/sources/openfda-devices.md.
//
// Two tiers:
//   TIER 1  populated from openFDA and the 510(k) Summary PDFs
//   TIER 2  modelled, not yet populated (no source identified or not yet ingested)
//
// =============================================================================
// TIER 1 — uniqueness constraints on populated labels
// =============================================================================

// Submission — a 510(k), PMA or De Novo. Decision is a property, not a separate
// node: one source record carries both date_received and decision_date.
CREATE CONSTRAINT submission_id      IF NOT EXISTS FOR (s:Submission)      REQUIRE s.id IS UNIQUE;

// ProductCode — the join hub. Every openFDA endpoint carries product_code;
// device names vary between endpoints, product codes do not.
CREATE CONSTRAINT productcode_code   IF NOT EXISTS FOR (p:ProductCode)     REQUIRE p.product_code IS UNIQUE;

// Regulation — a 21 CFR section (e.g. 870.5150), from openfda.regulation_number.
CREATE CONSTRAINT regulation_section IF NOT EXISTS FOR (r:Regulation)      REQUIRE r.cfr_section IS UNIQUE;

CREATE CONSTRAINT manufacturer_fei   IF NOT EXISTS FOR (m:Manufacturer)    REQUIRE m.fei_number IS UNIQUE;
CREATE CONSTRAINT establishment_reg  IF NOT EXISTS FOR (e:Establishment)   REQUIRE e.registration_number IS UNIQUE;

// MarketedDevice — a UDI record. Deliberately NOT merged with Submission:
// UDI records do not reliably carry the K-number of the clearance that
// authorised them, so no Submission -> MarketedDevice edge is asserted.
CREATE CONSTRAINT marketeddevice_udi IF NOT EXISTS FOR (d:MarketedDevice)  REQUIRE d.udi_di IS UNIQUE;

CREATE CONSTRAINT recall_number      IF NOT EXISTS FOR (rc:Recall)         REQUIRE rc.recall_number IS UNIQUE;
CREATE CONSTRAINT adverseevent_id    IF NOT EXISTS FOR (ae:AdverseEvent)   REQUIRE ae.report_number IS UNIQUE;

// PredicateClaim — the predicate as printed in the 510(k) Summary PDF, before
// resolution. Modelled as a node so an UNRESOLVED predicate is representable:
// predicates are free-text device names and may name a pre-1976 device with no
// clearance record. Dropping them would make a truncated chain look complete.
CREATE CONSTRAINT predicateclaim_id  IF NOT EXISTS FOR (pc:PredicateClaim) REQUIRE pc.id IS UNIQUE;

// Standard — identifier and clause references ONLY. ISO/IEC texts are
// paywalled and are never ingested.
CREATE CONSTRAINT standard_id        IF NOT EXISTS FOR (st:Standard)       REQUIRE st.designation IS UNIQUE;

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
// (:Recall)-[:ENFORCED_AS]->(:Recall)
// (:AdverseEvent)-[:REPORTED_AGAINST]->(:ProductCode|:MarketedDevice)

// =============================================================================
// TIER 2 — modelled, not yet populated
// =============================================================================

// Clause — paragraph-level obligation text from eCFR / EUR-Lex. Regulation is
// section-level (from openFDA); Clause carries the prose GraphRAG retrieves.
// Clause.text holds the obligation, Clause.embedding a 384-dim vector.
CREATE CONSTRAINT clause_id          IF NOT EXISTS FOR (c:Clause)          REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT guidance_id        IF NOT EXISTS FOR (g:Guidance)        REQUIRE g.id IS UNIQUE;
CREATE CONSTRAINT jurisdiction_id    IF NOT EXISTS FOR (j:Jurisdiction)    REQUIRE j.id IS UNIQUE;
CREATE CONSTRAINT registration_id    IF NOT EXISTS FOR (rg:Registration)   REQUIRE rg.id IS UNIQUE;
CREATE CONSTRAINT notifiedbody_id    IF NOT EXISTS FOR (nb:NotifiedBody)   REQUIRE nb.id IS UNIQUE;
CREATE CONSTRAINT certificate_id     IF NOT EXISTS FOR (ct:Certificate)    REQUIRE ct.id IS UNIQUE;
CREATE CONSTRAINT technicalfile_id   IF NOT EXISTS FOR (tf:TechnicalFile)  REQUIRE tf.id IS UNIQUE;
CREATE CONSTRAINT pmsobligation_id   IF NOT EXISTS FOR (po:PMSObligation)  REQUIRE po.id IS UNIQUE;

// Deadline is a NODE, not a date property, so "what is due in the next 90 days
// across every obligation type" is one traversal rather than a scan per label.
CREATE CONSTRAINT deadline_id        IF NOT EXISTS FOR (dl:Deadline)       REQUIRE dl.id IS UNIQUE;

// AI governance (Q11, Q12) — shapes taken from bank-model-risk-kg rather than
// reinvented. Model governance is domain-independent: "what was it trained on,
// what did you assume, when was it last validated, what is still open" is the
// same question a bank supervisor asks about a credit model.
CREATE CONSTRAINT aimodel_id         IF NOT EXISTS FOR (am:AIModel)        REQUIRE am.id IS UNIQUE;
CREATE CONSTRAINT trainingdataset_id IF NOT EXISTS FOR (td:TrainingDataset) REQUIRE td.id IS UNIQUE;
CREATE CONSTRAINT assumption_id      IF NOT EXISTS FOR (a:Assumption)      REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT validation_id      IF NOT EXISTS FOR (v:Validation)      REQUIRE v.id IS UNIQUE;
CREATE CONSTRAINT finding_id         IF NOT EXISTS FOR (f:Finding)         REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT control_id         IF NOT EXISTS FOR (co:Control)        REQUIRE co.id IS UNIQUE;

// Cybersecurity (Q13) — FDA premarket submissions now require an SBOM.
CREATE CONSTRAINT softwarecomponent_id IF NOT EXISTS FOR (sc:SoftwareComponent) REQUIRE sc.id IS UNIQUE;
CREATE CONSTRAINT vulnerability_id   IF NOT EXISTS FOR (vu:Vulnerability)  REQUIRE vu.id IS UNIQUE;

// Tier-2 edges
// (:Regulation)-[:HAS_CLAUSE]->(:Clause)
// (:Registration)-[:APPLIES_IN]->(:Jurisdiction)
// (:Certificate)-[:CERTIFIED_BY]->(:NotifiedBody)
// (:Regulation)-[:REQUIRES_PMS]->(:PMSObligation)
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
