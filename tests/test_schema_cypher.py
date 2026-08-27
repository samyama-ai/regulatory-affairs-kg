"""The schema file must actually execute.

`schema/regulatory_affairs_kg.cypher` is the executable ontology, not prose. It was
reviewed by reading for two rounds before anyone ran it, and reading missed both a
constraint collision and a syntax form the engine does not parse. So it gets a test.

The engine-backed test is skipped unless one is reachable:

    docker run --rm -p 8299:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
    SAMYAMA_TEST_URL=http://localhost:8299 pytest tests/test_schema_cypher.py

**`SAMYAMA_TEST_URL`, with no default, and never 8080.** The schema statements
this executes CREATE CONSTRAINTS, and a constraint cannot be dropped on this
engine — so a run that finds an engine by accident changes it permanently. It
read `SAMYAMA_URL` with a `localhost:8080` fallback, which is exactly finding
one by accident. See `tests.mcp_support.writable_test_engine`.

The parse test below needs no engine and runs everywhere.
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from tests.mcp_support import writable_test_engine

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "regulatory_affairs_kg.cypher"


def require_engine() -> str:
    return writable_test_engine("tests/test_schema_cypher.py")


def statements():
    """Executable statements, comments stripped."""
    body = " ".join(
        line for line in SCHEMA.read_text().splitlines() if not line.strip().startswith("//")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


def run(query, url=None):
    req = urllib.request.Request(
        f"{url or require_engine()}/api/query",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as exc:
        return {"error": exc.read().decode()}


def test_every_label_is_constrained_once():
    """No duplicate keys, and no label constrained twice — a second constraint on a
    label is how the Recall/enforcement collision would come back."""
    labels = re.findall(r"CREATE CONSTRAINT ON \(\w+:(\w+)\)", SCHEMA.read_text())
    assert labels, "no constraints found — did the file move?"
    duplicates = {label for label in labels if labels.count(label) > 1}
    assert not duplicates, f"label constrained more than once: {duplicates}"


def test_uses_the_syntax_the_engine_parses():
    """Samyama-Graph 1.1.0 does not parse `CREATE CONSTRAINT ... FOR ... REQUIRE`,
    despite it appearing in the engine's own CYPHER_COMPATIBILITY.md. Guard against
    someone helpfully modernising the file back to Neo4j-5 syntax."""
    executable = " ".join(statements())  # comments explain the rule, so exclude them
    assert "REQUIRE" not in executable, "use `ON (n:L) ASSERT n.p IS UNIQUE` — REQUIRE does not parse"
    assert "IF NOT EXISTS" not in executable, "`IF NOT EXISTS` does not parse in 1.1.0"


def test_schema_executes_against_the_engine():
    """Every statement runs clean against a live instance.

    Run this against a FRESH instance: the constraints are validated against existing
    data, so leftover nodes from an earlier run surface here as a false failure.

    Decided at call time, and `SAMYAMA_REQUIRE_ENGINE=1` turns an unreachable
    engine into a failure. This is the test that certifies the schema runs at
    all; skipping it silently is how "verified against the engine" ends up in
    the README on the strength of a run nobody made.
    """
    url = require_engine()
    failures = []
    for statement in statements():
        result = run(statement, url)
        if "error" in result:
            failures.append(f"{statement[:70]} -> {result['error'][:150]}")
    assert not failures, "statements failed:\n" + "\n".join(failures)
