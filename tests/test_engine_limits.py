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

    docker run -d --rm -p 8299:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
    SAMYAMA_TEST_URL=http://localhost:8299 pytest tests/test_engine_limits.py

**Not 8080, and not `SAMYAMA_URL`.** These tests write, and the variable they
read has no default, so they cannot find an engine by accident — see
`tests.mcp_support.writable_test_engine` for what each rule is there to stop.
Skipped when the variable is unset, unless SAMYAMA_REQUIRE_ENGINE=1.

## Which of these are allowed to fail when the engine improves

Two kinds live here and they are marked apart, because a failure means
opposite things.

**Controls** assert what the loader NEEDS to be true — that `SET` persists at
all, that a bare numeral survives as a number. A failure is a real problem.

**`@pytest.mark.engine_limitation`** marks the ones that pin a limitation the
loader works around. A failure there is GOOD NEWS: the engine has gained
something, and the docstring says which workaround to drop. They are marked so
a failure cannot be mistaken for a defect in this repo, and so
`pytest -m "not engine_limitation"` gives a clean run on any build.

That distinction stopped being cosmetic on 2026-08-27: two builds reporting
`"version": "1.7.0"` answer the same query differently (DATASET-CARD issue 11),
so "this engine cannot do X" is not a property of a version number and a test
asserting it will pass on one machine and fail on another with nothing to say
why.
"""

import json
import urllib.error
import urllib.request
from uuid import uuid4

import pytest

from mcp_server.engine import Unbounded, quoted
from tests.mcp_support import writable_test_engine

# NOT a module-level default any more.
#
# This read `SAMYAMA_URL`, falling back to `localhost:8080`, and every test
# below WRITES — `CREATE`, `MERGE`, `DETACH DELETE`. So a plain `pytest` on a
# machine with any engine on 8080 attached to it and wrote probe nodes into
# somebody else's graph, with nothing in the output naming the engine, because
# nothing had to be typed to choose one.
#
# Resolved per test now, through the one guard every writing module uses.


def require_engine() -> str:
    return writable_test_engine("tests/test_engine_limits.py")


def run(query: str, url: str | None = None) -> dict:
    request = urllib.request.Request(
        f"{url or require_engine()}/api/query",
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
@pytest.mark.engine_limitation
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
    """The control, and NOT an `engine_limitation`. Without it the test above
    could pass because SET does nothing at all rather than because null
    specifically is ignored — and a failure here is a real problem rather than
    the engine having improved."""
    run(f'MATCH (n:LimitProbe) WHERE n.id = "{node}" SET n.txt = "after"')
    assert value_of(node) == "after", "SET does not persist at all — a bigger problem"


def test_numbers_and_booleans_survive_as_themselves(node):
    """`lit()` emits these unquoted. If a future engine stopped accepting bare
    numerals this would catch it before a load did.

    NOT an `engine_limitation`: this asserts what the loader needs to stay
    true, so a failure is a regression rather than good news."""
    run(f'MATCH (n:LimitProbe) WHERE n.id = "{node}" SET n.num = 42, n.flag = true')
    records = run(
        f'MATCH (n:LimitProbe) WHERE n.id = "{node}" RETURN n.num, n.flag'
    )["records"]
    assert records[0] == [42, True], records


@pytest.mark.engine_limitation
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


def test_a_backslash_cannot_reach_the_engine_whichever_build_this_is():
    """Two builds report version 1.7.0 and disagree about backslashes.

        public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0   RETURN "C:\\temp" -> C:\\temp
        samyama:1.7.0-oss-2a86307                     RETURN "C:\\temp" -> C:<TAB>emp

    Measured on both. `/api/status` returns `"1.7.0"` for each, so nothing the
    repo can read tells them apart — and on the second, `quoted("C:\\temp")`
    sends a literal the engine reads as `C:<TAB>emp`. The term matched is not
    the term asked for: no rows, `error: None`, and an agent reads that as "no
    such device".

    **This test asserted the first behaviour and would have failed on the
    second**, turning an engine difference into what reads like a repo defect.
    Worse, it would have passed on this box while the bug was live on another.

    So the assertion moved off the engine and onto the encoder, which is the
    only part this repo controls. `quoted()` refuses a backslash, so neither
    behaviour can reach a query and the module is correct on both builds
    without knowing which it is talking to. There is no escaping that would
    be: doubling the backslash is right on the decoding build and wrong on the
    preserving one.

    The engine half is still checked, because a THIRD behaviour is the thing
    nobody would notice — it asserts the running build does one of the two
    recorded things, and names what it did if not.
    """
    require_engine()

    # Engine-independent, and the part that makes the rest moot.
    for term in ("C:\\temp", "50\\%", "a\\nb"):
        with pytest.raises(Unbounded):
            quoted(term)

    # And this is why it has to. `quoted()` will not build this literal, so
    # the statement is written out here deliberately.
    records = run('RETURN "C:\\temp"')["records"]
    assert records and records[0], (
        "the engine did not answer a backslash literal at all — a third "
        "behaviour, and the one that would go unnoticed")

    preserved, decoded = "C:\\temp", "C:\temp"
    assert records[0][0] in (preserved, decoded), (
        f"this build returned {records[0][0]!r} for a backslash literal. The "
        f"two builds measured on 2026-08-27 returned {preserved!r} and "
        f"{decoded!r}; this is neither, so a third handling has appeared and "
        f"the refusal in `quoted()` should be re-checked against it.")
