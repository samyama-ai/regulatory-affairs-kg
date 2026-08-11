# Source — openFDA device endpoints

**Publisher:** US Food and Drug Administration
**Access:** `https://api.fda.gov/device/*.json` — public REST API, no key required for light use
**License:** Public domain (US Government work)
**Probed:** 2026-08-06

All record counts below were read from live API responses by
[`etl/probe_openfda.py`](../../etl/probe_openfda.py). Re-run it to verify; counts are not
hand-entered.

```bash
python -m etl.probe_openfda            # counts
python -m etl.probe_openfda --fields   # counts + field inventory
```

---

## 1. Endpoint inventory

openFDA returns `meta.results.total` on every response — the exact number of records matching a
query. With `limit=1` that gives a count in one request.

| Endpoint | Records | Contains |
|---|---:|---|
| `device/event` | 25,368,161 | MAUDE adverse event reports |
| `device/udi` | 5,083,948 | Unique Device Identification (GUDID) |
| `device/registrationlisting` | 330,251 | Establishment registrations and device listings |
| `device/510k` | 175,686 | Premarket notifications (substantial-equivalence clearances) |
| `device/recall` | 58,871 | Device recalls |
| `device/pma` | 56,853 | Premarket approvals (Class III) |
| `device/enforcement` | 39,635 | Enforcement reports for recalls |
| `device/classification` | 7,085 | Product codes, device class, regulation number |
| **TOTAL** | **31,120,490** | |

Record counts are **not** node counts. One record may become several nodes and edges; a loader
will produce the graph figures.

## 2. Field inventory — `device/510k`

29 fields, sampled across several records (openFDA omits empty fields, so a single record
under-reports the schema):

```
address_1, address_2, advisory_committee, advisory_committee_description,
applicant, city, clearance_type, contact, country_code, date_received,
decision_code, decision_date, decision_description, device_name,
expedited_review_flag, k_number, postal_code, product_code,
review_advisory_committee, state, statement_or_summary, third_party_flag,
zip_code,
openfda.device_class, openfda.device_name, openfda.fei_number,
openfda.medical_specialty_description, openfda.registration_number,
openfda.regulation_number
```

Example record (`K233820`, Edwards Lifesciences):

| Field | Value |
|---|---|
| `k_number` | `K233820` |
| `device_name` | Fogarty Arterial Embolectomy Catheter with Gate Valve |
| `applicant` | Edwards Lifesciences, LLC |
| `product_code` | `DXE` |
| `decision_date` | 2024-… |
| `decision_description` | Substantially Equivalent |
| `clearance_type` | Traditional |
| `openfda.regulation_number` | `870.5150` |
| `openfda.device_class` | 2 |

## 3. Join keys — how the endpoints connect

These are the identifiers that make a graph possible. All are government-issued, so joins are
exact rather than name-based.

| Key | Example | Links | Enables |
|---|---|---|---|
| **`regulation_number`** | `870.5150` | 510(k) / PMA / classification → **21 CFR** | **Device → the legal text that governs it.** Backbone of the change-impact question. |
| `product_code` | `DXE` | 510(k), PMA, classification, UDI, recall, event | Device family across every endpoint |
| `registration_number` / `fei_number` | `3002807314` | → registrationlisting | Device → manufacturer → facility |
| `k_number` / PMA number | `K233820` | → recall, enforcement | Clearance → what went wrong afterwards |

`regulation_number` is the most valuable finding here. It is a direct, reliable pointer from a
cleared device into a specific section of 21 CFR, which means *"this rule changed — which devices
are affected?"* is a real join and not an approximation.

## 4. ⚠️ The predicate device is NOT in the API

**Finding:** the 510(k) endpoint does not expose the predicate device. All 29 fields are listed
above; none identifies the device a submission claims substantial equivalence to.

This matters because predicate chains — each clearance citing an older device, transitively back
to 1976 — are the most graph-native structure in the domain and the strongest demonstration of
the engine. They cannot be built from the API alone.

### Where the predicate actually is

In the 510(k) Summary, published as a PDF:

```
https://www.accessdata.fda.gov/cdrh_docs/pdf{YY}/{K_NUMBER}.pdf
```

where `{YY}` is the two-digit year from the K-number (`K233820` → `pdf23`).

**Verified** by downloading `K233820.pdf` (1,071,473 bytes) and extracting text with
`pdftotext -layout`. The document contains a `510(K) SUMMARY` section with an explicitly
labelled field:

```
 Regulation
                   21 CFR 870.5150 / Embolectomy catheter
 Number / Name
 Product Code      DXE
 Regulation Class  Class II
 Predicate
                   Preamendment Fogarty Arterial Embolectomy Catheter
 Device
 Comparative       The subject device is identical to the predicate device in terms of the
 Analysis          intended use, indications for use, and technological characteristics...
 Device Testing    Biocompatibility testing was performed in accordance with
                   ISO 10993-1: 2018 ...
```

The PDF carries a real text layer — no OCR was needed for this sample.

### The naming problem

The predicate is given as **a device name, not a K-number**. In the sample above it is
*"Preamendment Fogarty Arterial Embolectomy Catheter"* — a pre-1976 device that has no K-number
at all, because it predates the clearance system.

So building chains requires resolving a free-text device name to a clearance record. That is
fuzzy matching, and some predicates have no record to resolve to. **Chains will be partial, and
the graph must represent an unresolved predicate rather than silently dropping it.**

### Coverage

Not every clearance has a summary PDF. Submitters may file a *statement* instead of a *summary*,
in which case no document is published. Of three URLs probed, two returned PDFs (1.0 MB and
2.6 MB) and one returned 404.

### Bonus: the summaries carry more than predicates

The sampled summary also names the **consensus standards the device was tested against**
(`ISO 10993-1:2018`). That is the data behind *"this standard was revised — which technical files
reference the superseded version?"*, which was not otherwise available from any endpoint.

## 5. Access mechanics

| Aspect | Detail |
|---|---|
| Auth | None required for light use; an API key raises the rate limit |
| Rate limit | 240 requests/minute unauthenticated (the probe script pauses 0.4 s between calls) |
| Pagination | `limit` (max 1000) + `skip`; `skip` is capped, so large pulls need `search`-partitioning by date range rather than deep paging |
| Query syntax | `search=field:value`, ranges as `search=decision_date:[2024-01-01+TO+2024-12-31]` |
| Response shape | `{ "meta": { "results": { "total": N } }, "results": [ ... ] }` |
| Empty fields | Omitted rather than null — sample several records to see the full schema |
| Bulk download | openFDA also publishes downloadable JSON archives, which will be cheaper than paging for a full load |

PDF endpoint: `accessdata.fda.gov/cdrh_docs/pdf{YY}/{K}.pdf`, no auth, 404 where no summary was
filed.

## 6. Measured vs estimated

Stated separately on purpose — the spec should not present estimates as measurements.

| Claim | Basis |
|---|---|
| All record counts in §1 | **Measured** — live API, `meta.results.total` |
| 29 fields, no predicate field | **Measured** — sampled records, keys unioned |
| Predicate appears in the summary PDF | **Measured** — `K233820.pdf` downloaded and text extracted |
| Summaries name consensus standards | **Measured** — same document |
| Predicates given as names, not K-numbers | **Measured** on one sample; expected to be common, not yet quantified |
| Some clearances have no summary PDF | **Measured** — 1 of 3 probed returned 404; proportion unknown |
| Full 510(k) PDF corpus ≈ 90–500 GB | **Estimated** — 175,686 clearances × 0.5–3 MB, extrapolated from two files. Wide range on purpose. |

## 7. Gaps and open items

- **Predicate resolution rate is unknown.** What fraction of predicates name a device that maps
  to a K-number? This determines how complete chains can be. Needs a sample of ~100 summaries.
- **Summary availability is unknown.** What fraction of the 175,686 clearances publish a PDF?
- **Scanned PDFs.** The sample had a text layer; older filings are likely scans requiring OCR.
- **PMA predicates.** PMAs do not use predicates, so chains cover the 510(k) route only.
- **De Novo.** `decision_code: DENG` and K-numbers of the form `DEN240007` appear in the 510(k)
  endpoint, so De Novo authorisations are mixed in and need separating.
- **Bulk archives not yet evaluated** against the paged API for the first load.

## 8. What this source supports

| Question | Supported? |
|---|---|
| Rule changes — which devices, markets, deadlines? | ✅ via `regulation_number` (US only; deadlines need other sources) |
| Standard revised — which files affected? | ⚠️ via summary PDFs only |
| Predicate ancestry | ⚠️ PDF extraction + fuzzy name resolution; partial chains |
| Adverse events, recalls, enforcement patterns | ✅ directly |
| Manufacturer and facility links | ✅ via registration/FEI numbers |
| Market access outside the US | ❌ not covered — needs EU MDR / EUDAMED and national sources |
| AI/ML governance attributes | ❌ not exposed as structured fields |
