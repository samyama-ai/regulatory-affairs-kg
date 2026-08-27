"""Engine behaviours the loader is built around, asserted against a live engine.

These tests exist to fail. Each one pins a behaviour the loader works around,
so that when a future build changes it the suite says so instead of the
workaround quietly outliving its reason.

Pinned to the behaviour, never to a release number. `1.1.0` below is the IMAGE
TAG; the engine behind it reports **1.7.0** on `/api/status`. A limitation
recorded as "1.1.0 cannot do X" therefore never went stale visibly — it named
a version nobody was running. Every check here asks the engine instead.

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

from mcp_server.engine import quoted

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


def test_a_backslash_is_not_an_escape_character():
    """The fact every Cypher literal in this repo is encoded around.

    `etl.cypher.lit` chooses the quote style per value rather than escaping,
    because a backslash escapes nothing here: `\\n` inside a literal is a
    backslash followed by an n, not a newline. Nothing else in the suite would
    notice if that changed.

    And it would change silently. If a build starts decoding escapes,
    `quoted("C:\\temp")` sends `C:` then `\\t`, the engine reads a TAB, and the
    term matched is not the term asked for: no rows, no error, and an agent
    reads the empty answer as "no such device". The whole of `mcp_server`
    exists to keep that third meaning out of an empty list, and this is the
    door it would come through.

    Both directions, because they can diverge: what the parser accepts inside
    a literal, and what a stored value matches back against. Measured
    2026-08-27 on the engine reporting 1.7.0 — both hold.
    """
    require_engine()
    for term in ("C:\\temp", "50\\%", "a\\nb"):
        literal = quoted(term)
        returned = run(f"RETURN {literal}")["records"]
        assert returned and returned[0][0] == term, (
            f"the engine decoded an escape in {literal}: it returned "
            f"{returned and returned[0][0]!r} for the term {term!r}. Every "
            f"literal in this repo is written on the assumption that it does "
            f"not, so `quoted()` must now refuse a backslash rather than "
            f"send one that changes the value silently.")

        key = f"ESC-{uuid4().hex[:8].upper()}"
        run(f'CREATE (n:EscapeProbe {{id: "{key}", txt: {literal}}})')
        try:
            back = run(f"MATCH (n:EscapeProbe) WHERE n.id = \"{key}\" AND "
                       f"n.txt = {literal} RETURN count(n)")["records"]
            assert back and back[0][0] == 1, (
                f"{term!r} was stored as something the literal it was written "
                f"with no longer matches, so a search for it returns an empty "
                f"answer with no error.")
        finally:
            run(f'MATCH (n:EscapeProbe) WHERE n.id = "{key}" DETACH DELETE n')
