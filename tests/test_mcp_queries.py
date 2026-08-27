"""The MCP queries, tested WITHOUT an engine and without an MCP dependency.

The previous version of this server declared two tools and returned `[]` from
both. So the thing worth testing is not that a tool exists — it is that it runs
a traversal against the engine and can tell an empty answer from a broken one.

Everything here is pure: argument handling, the literal encoder, where the
server points, and the shape of the Cypher each tool builds. Nothing in this
file reaches an engine, so nothing in it skips.

**The engine-backed tests live in `tests/test_mcp_live.py`**, split out when
this file passed the 500-line review limit. `SAMYAMA_TEST_URL` and
`SAMYAMA_REQUIRE_ENGINE` belong to that file and are read nowhere in this one —
this docstring described them for several rounds after the split, and told the
reader to run

    SAMYAMA_TEST_URL=... pytest tests/test_mcp_queries.py

which sets a variable this file ignores and runs no engine test at all. Run
`tests/test_mcp_live.py` for those; its own docstring carries the invocation
and the reason it refuses to default to port 8080.
"""

from __future__ import annotations

import json

import inspect

import pytest

from _pytest.outcomes import Failed

from mcp_server import engine, queries
from tests.mcp_support import return_aliases, server_tools

# Imported from the wiring, not restated. This file kept a third copy of the
# same eight names — `queries.py` defines them, `server.py` registers them,
# and the tests listed them again — so every check below was really a check
# that this tuple matched itself.
TOOLS = server_tools()

# Every parameter these tools compare against as TEXT, and therefore every
# one that reaches `quoted()`. One set, not two clauses joined by `or`.
TEXT_ARGUMENTS = {"cfr_section", "product_code", "k_number", "device_class"}


def test_the_count_is_not_taken_from_the_capped_list(monkeypatch):
    """`clearances_under_regulation` is LIMITed. Reading its length as the
    total understates the blast radius of a rule change, which is the one
    number this graph exists to get right.

    Asserted by CALLING both, not by reading their source for the word
    "LIMIT". The source scan claimed a guarantee it could not give: it never
    established that the count is larger than the cap, only that one function
    mentions a keyword and the other does not — and it broke the moment
    `count(s)` became `count(DISTINCT s)`, which changed nothing about the
    property it says it protects.
    """
    served = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(served["payload"]).encode()

    def urlopen(request, *a, **k):
        cypher = json.loads(request.data)["query"]
        # Columns READ OFF the statement, never listed here. A stub with its
        # own hardcoded column names answers a query it is no longer testing:
        # renaming `count(DISTINCT s) AS total` to `AS c` left this fake still
        # sending `total`, the tool still found its figure, and the suite
        # stayed green against a query the real engine would answer with a
        # different column. It also once answered `k_number` where the query
        # aliases `clearance`, which stayed invisible until the listing began
        # deduplicating.
        columns = return_aliases(cypher)
        served["payload"] = (
            {"columns": columns, "records": [[415]]} if "count(" in cypher
            else {"columns": columns,
                  "records": [[f"K{n:06d}", f"Device {n}", "Acme",
                               "2024-01-02", "DXY"] for n in range(25)]})
        return R()

    monkeypatch.setattr(engine.urllib.request, "urlopen", urlopen)

    listed = queries.clearances_under_regulation("870.5150", limit=25)
    total = queries.count_clearances_under_regulation("870.5150")

    assert listed["count"] == 25, "the listing is not capped as expected"
    assert total["total"] == 415, total
    assert total["total"] > listed["count"], (
        "the count came from the capped list, which understates the blast "
        "radius — the one number this graph exists to get right")


def test_a_quote_in_an_argument_cannot_break_out(monkeypatch):
    sent = []
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"columns": [], "records": []}'
    monkeypatch.setattr(engine.urllib.request, "urlopen",
                        lambda rq, *a, **k: (sent.append(json.loads(rq.data)["query"]), R())[1])
    queries.regulations_for_product("' RETURN 1 //")
    cypher = sent[0]
    # Counting keywords cannot tell safe from unsafe — the injected text is
    # present either way. What matters is that the literal does not TERMINATE
    # at the injected quote, and with no escape sequences in this engine the
    # only way to achieve that is to delimit with the other quote character.
    payload = chr(34) + "' RETURN 1 //" + chr(34)
    assert payload in cypher, "the payload is not inside one literal: " + cypher
    assert cypher.rstrip().endswith("specialty"), "query truncated: " + cypher[-60:]


def test_a_bad_limit_is_an_error_not_a_traceback_or_a_negative_limit():
    """`int(limit)` turned "ten" into a ValueError escaping to the caller, and
    `-5` into `LIMIT -5`, which the engine rejects with a message about the
    whole statement. Every other bad input in this module returns an error;
    a bad argument should not be the one case that raises."""
    for tool, args in ((queries.busiest_regulations, ()),
                       (queries.clearances_by_advisory_committee, ()),
                       (queries.clearances_under_regulation, ("870.5150",)),
                       (queries.product_codes_by_class, ("3",))):
        for bad in (-5, 0, "ten", None):
            got = tool(*args, bad)
            assert got["error"], f"{tool.__name__}({bad!r}) returned no error"
            assert "limit" in got["error"], got["error"]

            # Whichever count key the tool uses, on the path it refuses.
            # `product_codes_by_class` reports `rows_returned` rather than
            # `count` because its number is a page size and not a total — the
            # names differ because the meanings do. Asserting one hardcoded
            # key made a rename look like a regression, and asserting the
            # error path returns SOMETHING zero-valued is the property that
            # actually matters.
            counts = [v for k, v in got.items()
                      if k in ("count", "total", "rows_returned")]
            assert counts, (
                f"{tool.__name__} returns no count key at all on the error "
                f"path, so a caller cannot tell an empty answer from a "
                f"refused one: {sorted(got)}")
            assert all(v == 0 for v in counts), got


def test_a_reverse_lookup_of_a_non_string_is_an_error_not_an_attribute_error():
    """`k_number.upper()` ran before anything could return a Result, so a None
    or a number hit an AttributeError instead of the error path."""
    got = queries.regulation_for_clearance(None)
    assert got["error"] and "string" in got["error"], got
    assert got["found"] is False


# --------------------------------------------------------------------------
# a term the engine cannot express is an error, not an empty answer
# --------------------------------------------------------------------------


def test_the_refusal_reaches_the_caller_as_an_error_not_a_traceback():
    """Every tool already turns `Unbounded` into `Result(error=…)`; the refusal
    takes that route rather than raising into an agent."""
    out = queries.regulations_for_product('a\'b"c')
    assert out["error"] and "cannot be matched exactly" in out["error"]
    assert out["regulations"] == [] and out["found"] is False


def test_no_tool_raises_when_a_term_cannot_be_expressed():
    """Every public tool must return `Result(error=…)`, never raise.

    Two of them called `quoted()` outside their try block, so making `quoted()`
    refuse a term turned a returned error into an escaping exception — a
    regression introduced by the fix in the same commit, and caught only by
    asking the question of every tool rather than the two the review named.

    Driven across all of them by inspection, so a tool added later is covered
    without anyone remembering to.
    """
    import inspect

    # Every parameter these tools compare against as TEXT, and therefore every
    # one that reaches `quoted()`. Named in one set rather than split across
    # two clauses joined by `or`: `and` binds tighter, so the previous spelling
    # read as `(A and B and C) or (A and B and D)` — the same test written
    # twice, where the second copy silently carried the whole condition.
    # Verified to select the same five tools.
    bad = 'a\'b"c'
    tools = [f for name, f in vars(queries).items()
             if inspect.isfunction(f)
             and not name.startswith("_")
             and TEXT_ARGUMENTS & set(inspect.signature(f).parameters)]
    assert tools, "no tools found to check — this would pass vacuously"
    # Derived, not counted. `len(tools) == 5` deliberately failed when a tool
    # was added, but its message asked the author to extend TEXT_ARGUMENTS —
    # which is the manual-sync coupling the rest of this file was rewritten to
    # remove. Every registered tool taking a text argument must be covered, and
    # that set comes from the wiring.
    covered = {f.__name__ for f in tools}
    expected = {name for name in TOOLS
                if TEXT_ARGUMENTS & set(inspect.signature(
                    getattr(queries, name)).parameters)}
    assert covered == expected, (
        f"the sweep covers {sorted(covered)} but the registered tools taking a "
        f"text argument are {sorted(expected)}")

    for tool in tools:
        try:
            out = tool(bad)
        except Exception as exc:                       # noqa: BLE001
            raise AssertionError(
                f"{tool.__name__} raised {type(exc).__name__} instead of "
                f"returning an error: {exc}") from None
        assert out.get("error"), f"{tool.__name__} returned no error for {bad!r}"


def test_an_unreadable_node_count_is_treated_as_a_graph_worth_protecting(monkeypatch):
    """The guard that failed open, and the wipe behind it.

    `engine.run()` returns `Result(rows=[], error=...)` on any failure, so the
    live fixture's emptiness probe fell through to 0 and concluded the engine
    was empty — and the teardown behind it is an unconditional
    `MATCH (n) DETACH DELETE n`. Reproduced with a proxy returning 500 for one
    query: five nodes before, the test passed, zero nodes after.

    One flaky response between SAMYAMA_TEST_URL and a loaded graph was enough,
    and the green result meant nobody looked.
    """
    from tests import test_mcp_live as live

    monkeypatch.setattr(live.engine, "run",
                        lambda cypher: engine.Result(error="engine returned 500"))
    with pytest.raises(Failed, match="could not be read"):
        live.held_or_fail("test")


def test_a_populated_engine_is_still_refused(monkeypatch):
    """The case the guard was always meant to catch, kept alongside the one it
    missed — so a fix for the first cannot quietly disable the second."""
    from tests import test_mcp_live as live

    monkeypatch.setattr(live.engine, "run",
                        lambda cypher: engine.Result(rows=[{"n": 17168}]))
    with pytest.raises(Failed, match="17,168 nodes"):
        live.held_or_fail("test")

    # And an empty one passes, so the guard is not simply refusing everything.
    monkeypatch.setattr(live.engine, "run",
                        lambda cypher: engine.Result(rows=[{"n": 0}]))
    live.held_or_fail("test")


def test_provenance_reports_this_dataset_not_whatever_the_store_holds(monkeypatch):
    """The tool whose job is telling a reviewer what the graph contains.

    It reported `MATCH (n) RETURN count(n)` — the whole store. Pointed at an
    engine carrying an unrelated KG it answered **17,168 nodes** for a store
    with zero Submissions, zero ProductCodes and zero Regulations, and the only
    hint was an empty `by_source`.

    Sending `graph` would not fix it (1.1.0 ignores the field), so the dataset
    is counted by label and the store total is reported beside it under a name
    that says what it is.
    """
    # ROWS here, columns read off each statement. A fake that names its own
    # columns answers a query it is no longer testing: renaming `count(n) AS
    # nodes` to `AS c` would leave this fixture still sending `nodes`, the
    # tool still finding its figure, and the suite green against a statement
    # the real engine would answer differently.
    answers = {
        "MATCH (n) RETURN count(n) AS nodes": [[17168]],
        "MATCH (n:Submission)": [[0]],
        "MATCH (n:ProductCode)": [[0]],
        "MATCH (n:Regulation)": [[0]],
        "n.source IS NOT NULL": [],
    }

    class R:
        def __init__(self, payload): self._p = payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(self._p).encode()

    def dispatch(request, *a, **k):
        cypher = json.loads(request.data)["query"]
        for needle, records in answers.items():
            if needle in cypher:
                return R({"columns": return_aliases(cypher),
                          "records": records})
        raise AssertionError(f"no fixture answer for {cypher!r}")
    monkeypatch.setattr(engine.urllib.request, "urlopen", dispatch)

    got = queries.graph_provenance()
    assert got["error"] is None
    assert got["nodes"] == 0, (
        "this dataset holds nothing, but the tool reported the store's count "
        "as the dataset's — a reviewer reads that as a loaded graph")
    assert got["nodes_in_store"] == 17168, (
        "the store total is gone; a reader now cannot tell an empty engine "
        "from one holding somebody else's graph")
    assert {row["label"] for row in got["by_label"]} == {
        "Submission", "ProductCode", "Regulation"}


def test_provenance_survives_a_failed_second_query(monkeypatch):
    """The two-query path had no behavioural test at all — replacing the body
    with a hardcoded dict left the whole suite green, because the stub check
    only looks for a literally empty return."""
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"error": "engine went away"}).encode()
    monkeypatch.setattr(engine.urllib.request, "urlopen", lambda *a, **k: R())

    got = queries.graph_provenance()
    assert got["error"], "a failed count was reported as a successful answer"
    assert got["nodes"] is None
    assert got["nodes_in_store"] is None
    assert got["by_label"] == []


def test_every_tool_turns_a_none_argument_into_an_error_not_an_empty_answer():
    """The refusal has to reach the caller as `error`, from all of them —
    the same sweep that found two tools calling `quoted()` outside their try
    block last round."""
    for name in TOOLS:
        function = getattr(queries, name)
        params = inspect.signature(function).parameters
        text_arg = next((p for p in params if p in TEXT_ARGUMENTS), None)
        if text_arg is None:
            continue
        got = function(**{text_arg: None})
        assert got.get("error"), (
            f"{name} returned no error for a None argument — an agent reads "
            f"the empty answer as 'no such device exists'")


def test_provenance_reports_nothing_when_only_the_last_query_fails(monkeypatch):
    """Counts alongside a non-null error read as "here is the answer, and also
    something went wrong" — and a caller checking `error` last has already
    used the figures.

    The existing failure test fails EVERY query, so it returns at the first
    guard and never reaches this path. This one lets the label counts succeed
    and fails only `by_source`, which is the shape that produced a
    half-filled answer.
    """
    def dispatch(request, *a, **k):
        cypher = json.loads(request.data)["query"]
        failing = "n.source IS NOT NULL" in cypher
        # Columns off the statement, for the same reason as the fixture above.
        payload = ({"error": "the source rollup failed"} if failing
                   else {"columns": return_aliases(cypher),
                         "records": [[7]]})

        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(payload).encode()
        return R()
    monkeypatch.setattr(engine.urllib.request, "urlopen", dispatch)

    got = queries.graph_provenance()
    assert got["error"], "the failed rollup was not reported"
    assert got["nodes"] is None, (
        f"node counts were returned alongside an error ({got['nodes']}) — a "
        f"caller that checks `error` last has already used them")
    assert got["by_label"] == []
    assert got["nodes_in_store"] is None
