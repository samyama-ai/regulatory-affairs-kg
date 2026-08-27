"""The shape of the tool set, checked without an engine or a fake one.

Split out of `tests/test_mcp_queries.py` when that file passed the 500-line
review limit — and split by SUBJECT rather than at a convenient line. Everything
here reads the tools as declarations: is each one wired to the server, does each
statement order before it limits, does each `limit` refuse a flag, is anything
still a stub. Nothing here asks what a traversal ANSWERS; `test_mcp_queries.py`
keeps those, and they drive a fake engine to do it.

The distinction earns the split: these are the checks that must keep working on
a tool nobody has written yet. Each derives its subject from `queries.py` rather
than listing it, so a ninth tool is covered the day it is added.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
import typing

import pydantic
import pytest

from mcp_server import engine, queries
from tests.mcp_support import queries_that_run, server_tools, statements_of

TOOLS = server_tools()


# --------------------------------------------------------------------------
# no tool may return a bare empty list again
# --------------------------------------------------------------------------

def test_no_query_is_a_stub():
    """The defect this file exists for: two tools that returned `[]` with a
    TODO where the traversal should be. A body that runs no query is the
    regression to catch."""
    # Parsed, not string-scanned. `"run(" in source` matched the word inside a
    # docstring or a comment, and `"return []" not in source` is a negative
    # substring assertion — the false-negative trap, satisfied by writing
    # `return list()` or `return []  # noqa`. Both passed for a stub that
    # merely mentioned the right words.
    for name in TOOLS:
        function = getattr(queries, name)
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))

        calls_run = any(
            isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run"
            for node in ast.walk(tree))
        assert calls_run, f"{name} does not call run() — it sends no query"

        returns_empty = any(
            isinstance(node, ast.Return)
            and isinstance(node.value, (ast.List, ast.Dict, ast.Set))
            and not getattr(node.value, "elts", getattr(node.value, "keys", [1]))
            for node in ast.walk(tree))
        assert not returns_empty, (
            f"{name} has a `return []` — the exact stub this file exists to "
            f"stop coming back")

        assert "TODO" not in inspect.getsource(function), f"{name} carries a TODO"


def test_every_query_is_registered_as_a_tool():
    """A query added to queries.py and forgotten in server.py is silently
    unavailable to an agent — the failure has no symptom without this.

    The direction matters, and the previous version had it backwards. It
    iterated a hardcoded tuple in this file and asserted each name appeared in
    `server.py`, so a query added to `queries.py` and forgotten in BOTH places
    passed — which is the only way it gets forgotten. `register()`'s docstring
    claimed such a query "fails a test"; it did not.

    The expected set is derived from `queries.py` instead: every public
    function that runs a query. Nothing here is written twice, so nothing can
    agree with itself.
    """
    discovered = queries_that_run()
    registered = set(server_tools())

    assert discovered == registered, (
        f"queries.py and server.py disagree about the tool list. "
        f"In queries.py and not registered: {sorted(discovered - registered)}. "
        f"Registered but not a query: {sorted(registered - discovered)}.")
    assert discovered, "no query functions were discovered — the parse is broken"


def test_pending_by_authority_is_gone_and_the_reason_recorded():
    """It cannot be built: openFDA publishes decided clearances, there is no
    pending queue, no SUBMITTED_TO edge and no status property. Deleting it
    silently would invite someone to add it back."""
    assert not hasattr(queries, "pending_by_authority")
    text = queries.__doc__ or ""
    assert "pending_by_authority" in text
    assert "decided" in text.lower()


def test_every_limited_query_orders_before_it_limits():
    """`LIMIT` with no `ORDER BY` is a non-deterministic page: repeated calls
    can return different subsets of the same answer, and an agent comparing
    two calls sees a change that did not happen.

    Read off the source of every tool, rather than pinned to the one that had
    the defect — the next query added here is the one that will repeat it.
    """
    offenders = []
    for name in TOOLS:
        # The STATEMENTS, read from the AST — not the source text. Scanning
        # raw source was defeated by the comment explaining why the ordering
        # is there, which contains the words the check looks for. Stripping
        # `#` comments closed that and left the docstrings, so the clause
        # could be deleted from the query and written into the prose above it
        # and this test still passed. The AST sees only what is sent.
        for statement in statements_of(getattr(queries, name)):
            if "LIMIT" in statement and "ORDER BY" not in statement:
                offenders.append(name)
    assert not offenders, (
        f"these limit without ordering, so the page they return is arbitrary: "
        f"{offenders}")


def test_a_true_limit_is_refused_where_an_agent_actually_reaches_it():
    """`bounded()` refuses a bool. Through the server it never sees one.

    fastmcp builds a pydantic model from these signatures, and pydantic reads
    `True` as a valid `int` — `TypeAdapter(int).validate_python(True)` returns
    `1`. So `limit=true` from an agent arrived at `bounded()` already a plain
    `1`, and came back as a one-row page with `error: None`: indistinguishable
    from the end of the data, which is the answer this whole module exists to
    keep out.

    Driven through the annotation each tool actually declares, so reverting it
    to a bare `int` fails here — and so does adding a ninth tool that takes a
    limit and misses it.
    """
    checked = []
    for name in TOOLS:
        hints = typing.get_type_hints(getattr(queries, name), include_extras=True)
        if "limit" not in hints:
            continue
        checked.append(name)
        validate = pydantic.TypeAdapter(hints["limit"]).validate_python
        for flag in (True, False):
            with pytest.raises(pydantic.ValidationError):
                validate(flag)
        assert validate(7) == 7
        assert validate("7") == 7, (
            f"{name} stopped accepting a limit sent as a JSON string; the "
            f"bool guard was meant to cost nothing else")
    assert checked, "no tool takes a limit, so this asserted nothing"


def test_the_listing_asks_the_engine_to_deduplicate_before_it_limits():
    """The bug this replaced: dedup after the cap returns a short page.

    A Submission reaches a Regulation once per product code, so duplicates are
    real. Removing them in Python ran AFTER the Cypher `LIMIT`, so a page of 25
    came back with fewer than 25 distinct clearances and the caller could not
    tell a short page from the end of the data.

    Asserted on the Cypher the builder SENDS, captured through a recording
    fake — not by reading the function's source, which agrees with how the
    query is spelled rather than with what it asks for.
    """
    sent = []

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"columns": ["clearance"], "records": []}).encode()

    def capture(request, *a, **k):
        sent.append(json.loads(request.data)["query"])
        return R()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine.urllib.request, "urlopen", capture)
        queries.clearances_under_regulation("870.5150", limit=25)

    cypher = sent[0]
    grouped = cypher.index("min(p.product_code)")
    limited = cypher.index("LIMIT")
    assert grouped < limited, (
        "the grouping that removes duplicates runs after LIMIT, so the page is "
        "capped before duplicates are dropped and comes back short")
    assert "RETURN DISTINCT" not in cypher, (
        "RETURN DISTINCT is a silent no-op on this engine — measured — so a "
        "query relying on it looks deduplicated and is not")
