"""The schema file must actually execute.

`schema/regulatory_affairs_kg.cypher` is the executable ontology, not prose. It was
reviewed by reading for two rounds before anyone ran it, and reading missed both a
constraint collision and a syntax form the engine does not parse. So it gets a test.

The engine-backed test is skipped unless one is reachable:

    docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
    pytest tests/test_schema_cypher.py

Point it elsewhere with SAMYAMA_URL. The parse test below needs no engine and runs
everywhere.
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "regulatory_affairs_kg.cypher"
SAMYAMA_URL = os.environ.get("SAMYAMA_URL", "http://localhost:8080")


def statements():
    """Executable statements, comments stripped."""
    body = " ".join(
        line for line in SCHEMA.read_text().splitlines() if not line.strip().startswith("//")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


def engine_available():
    try:
        urllib.request.urlopen(f"{SAMYAMA_URL}/api/tenants", timeout=2).read()
        return True
    except Exception:
        return False


def run(query):
    req = urllib.request.Request(
        f"{SAMYAMA_URL}/api/query",
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


@pytest.mark.skipif(not engine_available(), reason=f"no Samyama engine at {SAMYAMA_URL}")
def test_schema_executes_against_the_engine():
    """Every statement runs clean against a live instance.

    Run this against a FRESH instance: the constraints are validated against existing
    data, so leftover nodes from an earlier run surface here as a false failure.
    """
    failures = []
    for statement in statements():
        result = run(statement)
        if "error" in result:
            failures.append(f"{statement[:70]} -> {result['error'][:150]}")
    assert not failures, "statements failed:\n" + "\n".join(failures)
