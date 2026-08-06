// Regulatory Affairs Knowledge Graph — schema
// Node labels: Regulation, Authority, Submission, Approval, Product, Requirement
// Edge types:  ISSUED_BY, SUBMITTED_TO, GRANTED_APPROVAL, GOVERNS, REQUIRES, COMPLIES_WITH

CREATE CONSTRAINT regulation_id IF NOT EXISTS FOR (r:Regulation)  REQUIRE r.id IS UNIQUE;
CREATE CONSTRAINT authority_id  IF NOT EXISTS FOR (a:Authority)   REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT submission_id IF NOT EXISTS FOR (s:Submission)  REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT approval_id   IF NOT EXISTS FOR (ap:Approval)   REQUIRE ap.id IS UNIQUE;
CREATE CONSTRAINT product_id    IF NOT EXISTS FOR (p:Product)     REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT requirement_id IF NOT EXISTS FOR (rq:Requirement) REQUIRE rq.id IS UNIQUE;

// (:Regulation)-[:ISSUED_BY]->(:Authority)
// (:Submission)-[:SUBMITTED_TO]->(:Authority)
// (:Submission)-[:GRANTED_APPROVAL]->(:Approval)
// (:Regulation)-[:GOVERNS]->(:Product)
// (:Regulation)-[:REQUIRES]->(:Requirement)
// (:Product)-[:COMPLIES_WITH]->(:Regulation)
