"""Engine behaviours the loader is built around, asserted against a live engine.

These tests exist to fail. Each one pins a limitation of Samyama-Graph 1.1.0
that the loader works around, so that when a future version fixes it the suite
says so instead of the workaround quietly outliving its reason.

`tests/test_schema_cypher.py::test_uses_the_syntax_the_engine_parses` is the
same idea applied to the schema file.

    docker run -d -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
    pytest tests/test_engine_limits.py

Skipped when no engine is reachable, unless SAMYAMA_REQUIRE_ENGINE=1.
"""

import json
import os
import urllib.error
import urllib.request
from uuid import uuid4

import pytest

SAMYAMA_URL = os.environ.get("SAMYAMA_URL", "http://localhost:8080")


def engine_available() -> bool:
    try:
        with urllib.request.urlopen(f"{SAMYAMA_URL}/api/tenants", timeout=2) as response:
            response.read()
        return True
    except Exception:
        return False


def require_engine() -> None:
    if engine_available():
        return
    message = f"no Samyama engine at {SAMYAMA_URL}"
    if os.environ.get("SAMYAMA_REQUIRE_ENGINE") == "1":
        pytest.fail(f"{message} — SAMYAMA_REQUIRE_ENGINE=1 forbids skipping this")
    pytest.skip(message)


def run(query: str) -> dict:
    request = urllib.request.Request(
        f"{SAMYAMA_URL}/api/query",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return {"error": exc.read().decode()[:200]}


@pytest.fixture
def node():
    """One node with a known value, cleaned up after. Keyed per run."""
    require_engine()
    key = f"LIMIT-{uuid4().hex[:8].upper()}"
    run(f'CREATE (n:LimitProbe {{id: "{key}", txt: "before"}})')
    yield key
    run(f'MATCH (n:LimitProbe) WHERE n.id = "{key}" DETACH DELETE n')


def value_of(key: str):
    records = run(f'MATCH (n:LimitProbe) WHERE n.id = "{key}" RETURN n.txt')["records"]
    return records[0][0] if records and records[0] else None


@pytest.mark.parametrize("form", [
    'MATCH (n:LimitProbe) WHERE n.id = "{key}" SET n.txt = null RETURN n.txt',
    'MATCH (n:LimitProbe) WHERE n.id = "{key}" SET n.txt = null',
    'MERGE (n:LimitProbe {{id: "{key}"}}) ON MATCH SET n.txt = null',
])
def test_a_property_cannot_be_cleared(node, form):
    """`SET x = null` is accepted, reports success, and does nothing.

    This is why `props()` skips empty values on both MERGE branches rather than
    writing nulls on the ON MATCH one: a field the FDA later clears keeps its
    old value here, and there is no statement that would remove it.

    **If this test fails, the engine has gained the ability to clear a
    property.** That is good news: make `props()` write `key = null` on the
    ON MATCH branch, and drop the "last populated value wins" caveat from
    DATASET-CARD.md.
    """
    result = run(form.format(key=node))
    assert "error" not in result, f"the statement itself was rejected: {result}"
    assert value_of(node) == "before", (
        "SET = null now clears a property — see this test's docstring, the "
        "loader can stop working around it"
    )


def test_a_property_can_be_overwritten_with_a_real_value(node):
    """The control. Without this, the test above could pass because SET does
    nothing at all rather than because null specifically is ignored."""
    run(f'MATCH (n:LimitProbe) WHERE n.id = "{node}" SET n.txt = "after"')
    assert value_of(node) == "after", "SET does not persist at all — a bigger problem"


def test_numbers_and_booleans_survive_as_themselves(node):
    """`lit()` emits these unquoted. If a future engine stopped accepting bare
    numerals this would catch it before a load did."""
    run(f'MATCH (n:LimitProbe) WHERE n.id = "{node}" SET n.num = 42, n.flag = true')
    records = run(
        f'MATCH (n:LimitProbe) WHERE n.id = "{node}" RETURN n.num, n.flag'
    )["records"]
    assert records[0] == [42, True], records


def test_numeric_comparison_needs_a_numeric_property(node):
    """The reason `lit()` stopped quoting numbers: a numeric filter works
    against a number and is rejected against the string form."""
    run(f'MATCH (n:LimitProbe) WHERE n.id = "{node}" SET n.num = 42, n.numtext = "42"')
    ok = run(f'MATCH (n:LimitProbe) WHERE n.id = "{node}" AND n.num > 40 RETURN count(n)')
    assert "error" not in ok and ok["records"][0][0] == 1, ok

    bad = run(f'MATCH (n:LimitProbe) WHERE n.id = "{node}" AND n.numtext > 40 RETURN count(n)')
    assert "error" in bad, (
        "the engine now compares a numeric string against a number — quoting "
        "numbers would no longer break filtering, though it is still wrong"
    )
