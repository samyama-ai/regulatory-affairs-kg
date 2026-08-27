#!/usr/bin/env python3
"""Narrated demo of the Medical-Device Regulatory Affairs KG.

Press Enter between steps so the pacing is yours while narrating.
No dependencies beyond the standard library.

    docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
    python -m etl.download_openfda && python -m etl.load_openfda
    python -m demo.demo

Every number printed comes from the graph at run time. Nothing is hard-coded,
so if the load is wrong the demo shows it rather than hiding it.
"""

from __future__ import annotations

import os
import sys
import urllib.error

from etl.transport import post_query

URL = os.environ.get("SAMYAMA_URL", "http://localhost:8080")

BOLD, DIM, CYAN, GREEN, YELLOW, RED, OFF = (
    "\033[1m", "\033[2m", "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[0m",
)


def query(cypher: str) -> tuple[dict, float]:
    """One statement, or a printed message and exit 1.

    The request comes from `etl.transport.post_query`; what a failure means is
    decided here. A demo runs in front of people, so a failure prints in colour
    and stops rather than raising a traceback over the slide.
    """
    try:
        result, elapsed = post_query(cypher=cypher, url=URL, timeout=180)
    except urllib.error.HTTPError as exc:
        # HTTPError subclasses URLError, so it must be caught first — otherwise a
        # 500 from a running engine is reported as "no engine", which is the one
        # wrong diagnosis a presenter cannot afford.
        print(f"\n  {RED}engine returned {exc.code}{OFF} — {exc.read().decode()[:200]}\n")
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"\n  {RED}no engine at {URL}{OFF} — {exc.reason}\n")
        sys.exit(1)
    return result, elapsed


def table(columns: list[str], records: list[list]) -> None:
    # Pad short rows rather than IndexError halfway through a live demo.
    rows = [columns] + [
        [str(r[i]) if i < len(r) else "" for i in range(len(columns))] for r in records
    ]
    widths = [max(len(r[i]) for r in rows) for i in range(len(columns))]
    print("  " + BOLD + "  ".join(c.ljust(widths[i]) for i, c in enumerate(columns)) + OFF)
    print(f"  {DIM}" + "  ".join("-" * w for w in widths) + OFF)
    for row in rows[1:]:
        print("  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))


def pause() -> None:
    """Wait for Enter, unless there is nobody to press it.

    `python -m demo.demo < /dev/null`, a CI smoke check or a piped run has no
    stdin, and input() then raises EOFError at the first step. The demo should
    run start to finish in that case, not die — it is also how the recording is
    driven.
    """
    if not sys.stdin or not sys.stdin.isatty():
        return
    try:
        input(f"\n{DIM}  [enter]{OFF}")
    except EOFError:
        pass


def step(number: int, question: str, why: str, cypher: str, limit: int = 6) -> None:
    pause()
    print(f"\n{BOLD}{YELLOW}  {number}. {question}{OFF}")
    for line in why.splitlines():
        print(f"{DIM}  {line}{OFF}")
    print()
    for line in cypher.strip().splitlines():
        print(f"  {CYAN}{line.strip()}{OFF}")
    print()
    result, ms = query(cypher)
    if "error" in result:
        print(f"  {RED}{result['error'][:200]}{OFF}")
        return
    if not result.get("records"):
        print(f"  {RED}no rows — is the graph loaded?{OFF}")
        return
    table(result.get("columns", []), result["records"][:limit])
    print(f"\n  {GREEN}{ms:.0f} ms{OFF}")


def main() -> None:
    print(f"\n{BOLD}  Medical-Device Regulatory Affairs KG{OFF}")
    print(f"{DIM}  FDA device data as a graph — clearances, product codes, 21 CFR{OFF}\n")

    def count(cypher: str) -> int:
        result, _ = query(cypher)
        if "error" in result or not result.get("records"):
            print(f"\n  {RED}{result.get('error', 'no rows')}{OFF}\n")
            sys.exit(1)
        return result["records"][0][0]

    total_nodes = count("MATCH (n) RETURN count(n)")
    total_edges = count("MATCH ()-[r]->() RETURN count(r)")
    if not total_nodes:
        print(f"  {RED}graph is empty{OFF} — run "
              f"`python -m etl.download_openfda && python -m etl.load_openfda`\n")
        sys.exit(1)

    # A node count is not enough to know the graph is usable.
    #
    # Running the test suite against this engine leaves the node count intact
    # and silently breaks equality on every MERGE-key property — `cfr_section`,
    # `product_code`, `id`. Two engine defects combine to do it: DETACH DELETE
    # does not clear property columns, and /api/query ignores the `graph` field
    # so tests cannot be isolated into their own tenant. The demo then runs, the
    # header looks right, and the questions return nothing.
    #
    # So the preflight asks the graph the thing the demo depends on, and refuses
    # rather than presenting an empty answer to an audience.
    probe, _ = query("MATCH (r:Regulation) WHERE r.cfr_section = '870.5150' "
                     "RETURN count(r) AS found")
    if "error" in probe or not probe["records"] or not probe["records"][0][0]:
        print(f"\n  {RED}this graph cannot answer its own questions{OFF}")
        print(f"{DIM}  {total_nodes:,} nodes are present, but a lookup by MERGE key "
              f"returns nothing.{OFF}")
        print(f"{DIM}  Almost certainly the test suite was run against this engine — see{OFF}")
        print(f"{DIM}  demo/README.md. Restart the engine and re-import the snapshot.{OFF}\n")
        sys.exit(1)

    print(f"  {BOLD}{total_nodes:,}{OFF} nodes   {BOLD}{total_edges:,}{OFF} edges")

    step(
        1,
        "What is in this graph, and where did it come from?",
        "Before any answer is worth trusting. Every node carries the FDA endpoint\n"
        "it came from, how it was obtained and when — so the graph answers this\n"
        "itself rather than asking you to take a caption on trust.",
        """MATCH (n) WHERE n.source IS NOT NULL
        RETURN n.source AS fda_endpoint, n.extraction_method AS method,
               n.retrieved_at AS retrieved, count(n) AS records
        ORDER BY records DESC""",
    )
    step(
        2,
        "21 CFR 870.5150 is amended. Who is affected?",
        "The question this graph exists for. Today it is someone reading a rule\n"
        "and going through a portfolio by hand.",
        """MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission)
        WHERE r.cfr_section = '870.5150'
        RETURN s.applicant AS applicant, count(s) AS clearances
        ORDER BY clearances DESC LIMIT 6""",
    )

    step(
        3,
        "How many clearances in total sit under that one rule?",
        "The FDA stamps the regulation number onto every clearance, so this is an\n"
        "exact count rather than a name match. It agrees with what the API returns\n"
        "for the same filter, arrived at independently.",
        """MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission)
        WHERE r.cfr_section = '870.5150'
        RETURN count(s) AS clearances_affected""",
    )

    step(
        4,
        "Which cardiovascular rules carry the most clearances?",
        "Where the regulatory weight actually sits — useful for deciding which\n"
        "rule changes to watch. The part-870 filter is explicit rather than\n"
        "relying on the load happening to be bounded to cardiovascular.",
        """MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission)
        WHERE r.part = '870'
        RETURN r.cfr_section AS cfr_section, count(s) AS clearances
        ORDER BY clearances DESC LIMIT 6""",
    )

    step(
        5,
        "Take one device — what governs it?",
        "K233820, the Fogarty Arterial Embolectomy Catheter. The device whose\n"
        "summary PDF names a pre-1976 predecessor with no clearance record at all.",
        """MATCH (s:Submission)-[:CLASSIFIED_AS]->(p:ProductCode)-[:GOVERNED_BY]->(r:Regulation)
        WHERE s.id = 'K233820'
        RETURN s.device_name AS device, s.applicant AS applicant,
               p.product_code AS code, r.cfr_section AS governed_by""",
    )

    step(
        6,
        "Which device categories are Class III — the high-risk route?",
        "Class III needs full PMA approval rather than a 510(k). Knowing which\n"
        "categories those are shapes how a portfolio is managed.",
        """MATCH (p:ProductCode)-[:GOVERNED_BY]->(r:Regulation)
        WHERE p.device_class = '3'
        RETURN r.cfr_section AS cfr_section, p.product_code AS code,
               p.device_name AS category
        ORDER BY cfr_section LIMIT 6""",
    )

    pause()
    print(f"\n{BOLD}  What this is not, yet{OFF}")
    print(f"{DIM}  Predicate chains — the fifty-year citation network — are designed but not{OFF}")
    print(f"{DIM}  loaded. The predecessor device is not in the FDA's API; it exists only{OFF}")
    print(f"{DIM}  inside the 510(k) Summary PDF, as free text. That extraction is next, and{OFF}")
    print(f"{DIM}  how often a name resolves to a real clearance is still unmeasured.{OFF}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(0)
