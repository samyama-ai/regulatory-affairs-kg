# Regulatory Affairs KG — Design Notes

## Node labels
| Label | Key fields |
|-------|-----------|
| Regulation | id, title, jurisdiction, effective_date |
| Authority | id, name, country |
| Submission | id, type, status, filed_date |
| Approval | id, status, decision_date |
| Product | id, name, category |
| Requirement | id, description |

## Edge types
| Edge | From -> To | Meaning |
|------|-----------|---------|
| ISSUED_BY | Regulation -> Authority | who issued the regulation |
| SUBMITTED_TO | Submission -> Authority | filing target |
| GRANTED_APPROVAL | Submission -> Approval | outcome |
| GOVERNS | Regulation -> Product | scope |
| REQUIRES | Regulation -> Requirement | obligations |
| COMPLIES_WITH | Product -> Regulation | compliance link |

## Data sources
List each source, license, and contribution. Fill in `{{SOURCES}}`.
