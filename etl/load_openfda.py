"""Load the downloaded openFDA slices into Samyama-Graph.

    python -m etl.download_openfda      # first — writes ./data
    python -m etl.load_openfda          # then — loads into the engine

What it builds (Q1, the change-impact backbone):

    Submission -[:CLASSIFIED_AS]-> ProductCode -[:GOVERNED_BY]-> Regulation

Three engine facts shape every line below. All were measured against
Samyama-Graph 1.1.0, not assumed:

1. **A uniqueness constraint does not reject a duplicate CREATE.** So every
   write here is a MERGE on the key. A constraint in this engine declares the
   key; it does not guard the insert.

2. **UNWIND does not parse** — not even `UNWIND [1,2,3] AS x RETURN x`, despite
   the engine's CYPHER_COMPATIBILITY.md listing it as supported. Neither do
   semicolon-separated statements. So there is no batch form: one statement per
   request. Measured at ~700 statements/sec locally, which is fast enough that
   batching is not worth a workaround.

3. **`MERGE ... SET` does not parse.** `MERGE ... ON CREATE SET ... ON MATCH SET`
   does. That is why properties are written through ON CREATE / ON MATCH rather
   than a trailing SET.

There is deliberately NO Submission -> Manufacturer edge. See `no SUBMITTED_BY`
below.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from etl.cypher import SANITISED, lit, merge, split_statements

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "regulatory_affairs_kg.cypher"
DEFAULT_URL = "http://localhost:8080"


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

class Engine:
    def __init__(self, url: str, graph: str = "default") -> None:
        self.url = url.rstrip("/")
        self.graph = graph
        self.statements = 0
        self.retries = 0

    def run(self, query: str, attempts: int = 4) -> dict:
        """One statement, with backoff on transient failures.

        A load is ~54,000 statements. Without this, a single blip leaves a
        half-populated graph — and because every write is a MERGE, the safe
        recovery is to re-run the whole thing, which costs ten minutes.
        Retrying in place is cheaper and equally safe.

        Parse errors and other 4xx are NOT retried: they will fail identically
        every time, and hiding them behind four attempts only delays the report.
        """
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        payload = json.dumps({"query": query, "graph": self.graph}).encode()
        for attempt in range(attempts):
            request = urllib.request.Request(
                f"{self.url}/api/query", data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                result = json.loads(urllib.request.urlopen(request, timeout=120).read())
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode()[:300]
                if exc.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                    self.retries += 1
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"{exc.code} on: {query[:140]}\n{body}") from exc
            # OSError and JSONDecodeError as well as URLError: urllib wraps a
            # failure to *connect*, but not one while reading the response body.
            # ConnectionResetError, RemoteDisconnected and IncompleteRead are
            # OSErrors, and a proxy returning an HTML error page raises
            # JSONDecodeError. Over 53,847 statements these are the ones that
            # actually happen, and unhandled each leaves a half-loaded graph.
            except (urllib.error.URLError, OSError, TimeoutError,
                    json.JSONDecodeError) as exc:
                if attempt < attempts - 1:
                    self.retries += 1
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"unreachable on: {query[:140]}\n{exc}") from exc
        if "error" in result:
            raise RuntimeError(f"{result['error'][:300]}\n  on: {query[:140]}")
        self.statements += 1
        return result

    def scalar(self, query: str):
        records = self.run(query)["records"]
        return records[0][0] if records and records[0] else None

    def apply_schema(self) -> int:
        """Apply schema/regulatory_affairs_kg.cypher.

        Comments are stripped BEFORE joining. Dropping only lines that *start*
        with `//` and then flattening would let one trailing comment swallow
        every statement after it — silently, since the engine would just see a
        shorter script and report success.

        Both `//` and `;` are honoured only outside string literals. Splitting
        on them blindly truncates any statement containing a URL or a semicolon
        inside a quoted value — again silently. Nothing in the schema does that
        today; this is here so that adding one is not a trap.
        """
        statements = split_statements(SCHEMA.read_text())
        for statement in statements:
            self.run(statement)
        return len(statements)


# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------

def read(name: str) -> tuple[list[dict], dict]:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Run `python -m etl.download_openfda` first."
        )
    payload = json.loads(path.read_text())
    if "results" not in payload:
        raise SystemExit(f"{path} has no 'results' — re-run the downloader.")
    meta = payload.get("meta") or {}
    meta.setdefault("endpoint", f"device/{name}")
    meta.setdefault("retrieved_at", "unknown")
    return payload["results"], meta


def load_classifications(engine: Engine, rows: list[dict], meta: dict) -> dict:
    """ProductCode, Regulation, and the GOVERNED_BY edge between them.

    7,085 records carrying the entire device-category -> 21 CFR map. This is the
    smallest endpoint and the most load-bearing: it is what makes change impact
    an exact lookup rather than an inference.
    """
    provenance = {
        "source": meta["endpoint"],
        "retrieved_at": meta["retrieved_at"],
        "extraction_method": "api",
    }
    seen_regulations: set[str] = set()
    # Statements ISSUED, not rows written. A MERGE that matches an existing
    # node still counts here, and a MATCH...MERGE whose endpoints are absent
    # no-ops while still counting. `measure()` reads the graph itself and is
    # the only figure reported as a node or edge count.
    counts = {"ProductCode": 0, "Regulation": 0, "GOVERNED_BY": 0, "no_regulation": 0}

    for index, row in enumerate(rows, 1):
        code = (row.get("product_code") or "").strip()
        if not code:
            continue

        attributes = {
            "device_name": row.get("device_name"),
            "device_class": row.get("device_class"),
            "medical_specialty": row.get("medical_specialty_description"),
            "definition": row.get("definition"),
            "submission_type": row.get("submission_type_id"),
            "implant_flag": row.get("implant_flag"),
            "life_sustain_flag": row.get("life_sustain_support_flag"),
            **provenance,
        }
        engine.run(merge("ProductCode", "product_code", code, attributes, "p"))
        counts["ProductCode"] += 1

        section = (row.get("regulation_number") or "").strip()
        if not section:
            # Real and expected: some categories are unclassified or exempt.
            counts["no_regulation"] += 1
        else:
            if section not in seen_regulations:
                part = section.split(".")[0]
                regulation = {"part": part, "section": section, **provenance}
                engine.run(merge("Regulation", "cfr_section", section, regulation, "r"))
                seen_regulations.add(section)
                counts["Regulation"] += 1
            engine.run(
                f'MATCH (p:ProductCode), (r:Regulation)'
                f' WHERE p.product_code = {lit(code)} AND r.cfr_section = {lit(section)}'
                f' MERGE (p)-[:GOVERNED_BY]->(r)'
            )
            counts["GOVERNED_BY"] += 1

        if index % 500 == 0:
            print(f"  classifications {index:,}/{len(rows):,}", end="\r", flush=True)
    print(f"  classifications {len(rows):,}/{len(rows):,}        ")
    return counts


def load_clearances(engine: Engine, rows: list[dict], meta: dict) -> dict:
    """Submission, and CLASSIFIED_AS to its ProductCode.

    no SUBMITTED_BY
    ---------------
    A 510(k) record's `openfda` block carries `fei_number` and
    `registration_number` arrays, which look like a link to the applicant. They
    are not: openFDA harmonises that block **by product code**. Measured —
    K201705 (Vetex Medical) and K252612 (Penumbra), different companies, carry
    identical 72-entry fei arrays because both are product code QEW.

    Building Submission -> Manufacturer from it would assert that every
    clearance was submitted by all 72 companies listing the code. That is the
    fabricated join the schema's §2.2 exists to forbid, so `applicant` stays a
    name property and no edge is drawn. Manufacturer and Establishment come
    from device/registrationlisting, where registration.fei_number is
    authoritative per record.
    """
    provenance = {
        "source": meta["endpoint"],
        "retrieved_at": meta["retrieved_at"],
        "extraction_method": "api",
    }
    # Statements issued — see the note in load_classifications.
    counts = {"Submission": 0, "CLASSIFIED_AS": 0, "no_product_code": 0}

    for index, row in enumerate(rows, 1):
        k_number = (row.get("k_number") or "").strip().upper()
        if not k_number:
            continue

        openfda = row.get("openfda") or {}
        attributes = {
            # `id` is the FDA number verbatim — K/DEN from the 510(k) endpoint,
            # P from PMA. The namespaces cannot collide, so no synthetic prefix.
            "type": "DeNovo" if k_number.startswith("DEN") else "510k",
            "k_number": k_number,
            "device_name": row.get("device_name"),
            "applicant": row.get("applicant"),
            "date_received": row.get("date_received"),
            "decision_date": row.get("decision_date"),
            "decision_code": row.get("decision_code"),
            "decision_description": row.get("decision_description"),
            "clearance_type": row.get("clearance_type"),
            "advisory_committee": row.get("advisory_committee_description"),
            "country_code": row.get("country_code"),
            "statement_or_summary": row.get("statement_or_summary"),
            "regulation_number": openfda.get("regulation_number"),
            **provenance,
        }
        engine.run(merge("Submission", "id", k_number, attributes, "s"))
        counts["Submission"] += 1

        code = (row.get("product_code") or "").strip()
        if not code:
            counts["no_product_code"] += 1
        else:
            engine.run(
                f'MATCH (s:Submission), (p:ProductCode)'
                f' WHERE s.id = {lit(k_number)} AND p.product_code = {lit(code)}'
                f' MERGE (s)-[:CLASSIFIED_AS]->(p)'
            )
            counts["CLASSIFIED_AS"] += 1

        if index % 500 == 0:
            print(f"  clearances {index:,}/{len(rows):,}", end="\r", flush=True)
    print(f"  clearances {len(rows):,}/{len(rows):,}        ")
    return counts


def measure(engine: Engine) -> dict:
    """Count what is actually in the graph. Never inferred from input rows."""
    labels = ["ProductCode", "Regulation", "Submission"]
    edges = ["GOVERNED_BY", "CLASSIFIED_AS"]
    result = {"nodes": {}, "edges": {}}
    for label in labels:
        result["nodes"][label] = engine.scalar(f"MATCH (n:{label}) RETURN count(n)")
    for edge in edges:
        result["edges"][edge] = engine.scalar(f"MATCH ()-[r:{edge}]->() RETURN count(r)")
    result["total_nodes"] = sum(result["nodes"].values())
    result["total_edges"] = sum(result["edges"].values())
    return result


def select_smoke_rows(classifications: list[dict], clearances: list[dict],
                      limit: int) -> tuple[list[dict], list[dict]]:
    """Rows for a `--limit` smoke load, chosen so the join actually exists.

    Truncating both sources independently produced a smoke load whose
    CLASSIFIED_AS edges all resolved to nothing — measured, the overlap between
    the first 50 of each was zero. The MATCH then no-ops silently and the run
    still looks fine, which is the worst kind of green.

    So the clearances are taken first and the classifications they need follow,
    padded back up to the limit so the classification path is exercised too.
    Blank product codes are excluded on both sides. `product_code` is the merge
    key, so a classification without one cannot be loaded at all — letting them
    into the padding meant a smoke load asked for 40 rows and silently got
    fewer usable ones.

    Neither list ever exceeds `limit`.
    """
    clearances = clearances[:limit]
    wanted = {code for c in clearances
              if (code := (c.get("product_code") or "").strip())}
    needed, rest = [], []
    for c in classifications:
        code = (c.get("product_code") or "").strip()
        if not code:
            continue          # keyless: cannot be loaded, and cannot ever join
        (needed if code in wanted else rest).append(c)
    needed = needed[:limit]
    classifications = needed + rest[: max(0, limit - len(needed))]
    return classifications, clearances


def unresolvable_joins(classifications: list[dict], clearances: list[dict]) -> list[str]:
    """Clearances naming a product code no classification will create.

    This is the check that actually catches a stale ./data — 510k.json newer
    than classification.json, clearances pointing at codes that were never
    downloaded. `MATCH (s), (p) WHERE ... MERGE (s)-[..]->(p)` is accepted by
    the engine and writes nothing when either endpoint is absent, so the run
    looks clean and the edge count is quietly short.

    It reads the input files, not the graph, and that is the point. Comparing
    statements issued against `measure()` only works on an empty engine:
    `measure()` counts the whole store, so on any graph that already holds
    data the measured figure is at least the issued one and a shortfall can
    never show. Re-running the whole load is the documented recovery plan,
    which is precisely the case a graph-based check is blind to.

    Returns the offending product codes, deduplicated.
    """
    loadable = {(c.get("product_code") or "").strip()
                for c in classifications if (c.get("product_code") or "").strip()}
    missing = {code for c in clearances
               if (code := (c.get("product_code") or "").strip()) and code not in loadable}
    return sorted(missing)


def report_counts(issued: dict, graph: dict) -> None:
    """Statements issued beside rows measured.

    `measure()` counts the whole store, so on a shared or re-run graph the
    measured column is everything present, not everything this run wrote. Both
    are printed because the pair is informative; neither is used to decide
    whether the load was sound — `unresolvable_joins` does that, from the
    input.
    """
    for kind in ("nodes", "edges"):
        print(f"  {kind}")
        print(f"    {'':14} {'issued':>9}  {'in graph':>9}")
        for name, measured in graph[kind].items():
            sent = issued.get(name)
            shown = f"{sent:,}" if sent is not None else "-"
            print(f"    {name:14} {shown:>9}  {measured:>9,}")
        print(f"    {'TOTAL':14} {'':>9}  {graph[f'total_{kind}']:>9,}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help="Engine base URL.")
    parser.add_argument("--graph", default="default", help="Tenant / graph name.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap rows per source, for a fast smoke load.")
    parser.add_argument("--skip-schema", action="store_true",
                        help="Do not apply schema/regulatory_affairs_kg.cypher first.")
    args = parser.parse_args(argv)

    engine = Engine(args.url, args.graph)
    started = time.time()
    print(f"loading into {args.url} (graph={args.graph})\n")

    # Read the data first. Applying the schema takes a few seconds and 36
    # writes; discovering only afterwards that ./data is empty wastes both and
    # leaves constraints on an engine the caller may not have wanted touched.
    classifications, class_meta = read("classification")
    clearances, clear_meta = read("510k")

    if not args.skip_schema:
        applied = engine.apply_schema()
        print(f"  schema: {applied} statements applied")
    if args.limit is not None:   # `--limit 0` is falsy but means zero, not "no limit"
        classifications, clearances = select_smoke_rows(
            classifications, clearances, args.limit)
        needed = unresolvable_joins(classifications, clearances)
        print(f"  --limit {args.limit}: smoke load only — {len(clearances):,} "
              f"clearances and {len(classifications):,} product codes, chosen so "
              f"the join exists\n")

    unresolvable = unresolvable_joins(classifications, clearances)

    class_counts = load_classifications(engine, classifications, class_meta)
    clear_counts = load_clearances(engine, clearances, clear_meta)

    elapsed = time.time() - started
    graph = measure(engine)

    rate = f"{engine.statements / elapsed:.0f}/sec" if elapsed > 0 else "instant"
    retried = f", {engine.retries} retried" if engine.retries else ""
    print(f"\n  {engine.statements:,} statements in {elapsed:.0f}s ({rate}){retried}\n")
    issued = {**class_counts, **clear_counts}
    report_counts(issued, graph)

    if unresolvable:
        print(f"\n  {len(unresolvable):,} product code(s) named by clearances are not in "
              f"classification.json.\n  Those CLASSIFIED_AS edges cannot be written — the "
              f"engine accepts the statement\n  and does nothing. This is what a stale "
              f"./data looks like: check both files\n  came from the same download. "
              f"First few: {', '.join(unresolvable[:5])}")

    if class_counts["no_regulation"]:
        print(f"\n  {class_counts['no_regulation']:,} product codes carry no "
              f"regulation number (unclassified or exempt) — no GOVERNED_BY edge")
    if clear_counts["no_product_code"]:
        print(f"  {clear_counts['no_product_code']:,} clearances carry no "
              f"product code — no CLASSIFIED_AS edge")
    if SANITISED:
        print(f"\n  {len(SANITISED)} value(s) altered to be representable — the engine has no\n"
              f"  string escaping, so a value containing BOTH quote types cannot be encoded.\n"
              f"  Inner double quotes replaced with typographic quotes:")
        for text in SANITISED[:5]:
            print(f"    {text}")

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (DATA_DIR / "load-report.json").write_text(
        json.dumps({"measured_at": stamp, "elapsed_seconds": round(elapsed, 1),
                    "statements": engine.statements, "retries": engine.retries,
                    "sanitised_values": len(SANITISED),
                    "statements_issued": issued,
                    "unresolvable_product_codes": unresolvable, **graph}, indent=2)
    )
    print(f"\n  report -> data/load-report.json")
    return 0


def cli(argv: list[str] | None = None) -> int:
    """main(), with engine failures reported rather than raised.

    A RuntimeError here means the engine rejected a statement or went away
    mid-load. As a traceback that reads as a bug in this script; as a message
    it reads as what it is. Matches how etl.download_openfda reports the same
    class of failure.
    """
    try:
        return main(argv)
    except (RuntimeError, urllib.error.URLError, OSError,
            json.JSONDecodeError) as exc:
        print(f"\nengine error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
