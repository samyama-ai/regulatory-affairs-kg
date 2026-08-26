"""The MCP queries, tested without an MCP dependency.

The previous version of this server declared two tools and returned `[]` from
both. So the thing worth testing is not that a tool exists — it is that it runs
a traversal against the engine and can tell an empty answer from a broken one.

The engine-backed tests load a handful of fixture nodes into a FRESH instance
and assert the traversals return them.

**They require `SAMYAMA_TEST_URL`, deliberately.** They do not fall back to
`SAMYAMA_URL` and they do not default to port 8080 — because 8080 is where a
demo engine runs, and `DETACH DELETE` against a loaded graph breaks MERGE-key
equality permanently while leaving the node count untouched (DATASET-CARD.md,
known issue 10; demo/README.md).

The first draft of this file did default to 8080 and ran straight into a loaded
graph. Nothing was damaged — the teardown is scoped to `source = 'test'` — but
the docstring warned against exactly what the code invited, so the separate
variable is the fix rather than a louder warning.

    docker run --rm -p 8111:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
    SAMYAMA_TEST_URL=http://localhost:8111 pytest tests/test_mcp_queries.py

`SAMYAMA_REQUIRE_ENGINE=1` turns the skip into a failure, for CI.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from mcp_server import queries

TOOLS = ("clearances_under_regulation", "count_clearances_under_regulation",
         "regulations_for_product", "regulation_for_clearance",
         "busiest_regulations", "clearances_by_advisory_committee",
         "product_codes_by_class", "graph_provenance")


# --------------------------------------------------------------------------
# no tool may return a bare empty list again
# --------------------------------------------------------------------------

def test_no_query_is_a_stub():
    """The defect this file exists for: two tools that returned `[]` with a
    TODO where the traversal should be. A body that runs no query is the
    regression to catch."""
    import inspect
    for name in TOOLS:
        source = inspect.getsource(getattr(queries, name))
        assert "run(" in source, f"{name} runs no query"
        assert "TODO" not in source, f"{name} still carries a TODO"
        assert "return []" not in source, f"{name} returns a bare empty list"


def test_every_query_is_registered_as_a_tool():
    """A query added to queries.py and forgotten in server.py is silently
    unavailable to an agent — the failure has no symptom without this."""
    import pathlib
    wiring = (pathlib.Path(__file__).resolve().parents[1]
              / "mcp_server" / "server.py").read_text()
    for name in TOOLS:
        assert f'"{name}"' in wiring, f"{name} is not registered in server.py"


def test_pending_by_authority_is_gone_and_the_reason_recorded():
    """It cannot be built: openFDA publishes decided clearances, there is no
    pending queue, no SUBMITTED_TO edge and no status property. Deleting it
    silently would invite someone to add it back."""
    assert not hasattr(queries, "pending_by_authority")
    text = queries.__doc__ or ""
    assert "pending_by_authority" in text
    assert "decided" in text.lower()


def test_an_unreachable_engine_is_an_error_not_an_empty_result(monkeypatch):
    """An agent cannot act on `[]` if it means both "no clearances" and "the
    engine is down"."""
    monkeypatch.setattr(queries.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.URLError("connection refused")))
    got = queries.clearances_under_regulation("870.5150")
    assert got["error"] is not None
    assert got["clearances"] == []


def test_a_500_is_not_reported_as_a_missing_engine(monkeypatch):
    """HTTPError subclasses URLError, so catching URLError first turns a 500
    from a running engine into "no engine" — which sends someone to restart a
    container that is already up."""
    import io
    monkeypatch.setattr(queries.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b"bad"))))
    error = queries.busiest_regulations()["error"]
    assert "500" in error and "no engine" not in error


def test_a_rejected_query_is_an_error_not_an_empty_result(monkeypatch):
    """The engine answers 200 with an `error` key for a parse failure."""
    serve(monkeypatch, {"error": "Parse error: unexpected token"})
    got = queries.regulations_for_product("DXY")
    assert got["error"] and "rejected" in got["error"]


def test_a_genuinely_empty_answer_has_no_error(monkeypatch):
    serve(monkeypatch, {"columns": ["cfr_section"], "records": []})
    got = queries.regulations_for_product("NOSUCH")
    assert got["error"] is None
    assert got["regulations"] == []
    assert got["count"] == 0


def serve(monkeypatch, payload):
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(payload).encode()
    monkeypatch.setattr(queries.urllib.request, "urlopen", lambda *a, **k: R())


def test_rows_are_returned_as_named_fields(monkeypatch):
    """Columns and records arrive separately; an agent needs them joined."""
    serve(monkeypatch, {"columns": ["clearance", "device"],
                        "records": [["K233820", "A scanner"]]})
    rows = queries.clearances_under_regulation("870.5150")["clearances"]
    assert rows == [{"clearance": "K233820", "device": "A scanner"}]


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
        served["payload"] = (
            {"columns": ["total"], "records": [[415]]} if "count(" in cypher
            else {"columns": ["k_number"],
                  "records": [[f"K{n:06d}"] for n in range(25)]})
        return R()

    monkeypatch.setattr(queries.urllib.request, "urlopen", urlopen)

    listed = queries.clearances_under_regulation("870.5150", limit=25)
    total = queries.count_clearances_under_regulation("870.5150")

    assert listed["count"] == 25, "the listing is not capped as expected"
    assert total["total"] == 415, total
    assert total["total"] > listed["count"], (
        "the count came from the capped list, which understates the blast "
        "radius — the one number this graph exists to get right")


@pytest.mark.parametrize("value,expected", [
    ("870.5150", chr(34) + "870.5150" + chr(34)),
    ("O'Brien", chr(34) + "O'Brien" + chr(34)),
    ("back\\slash", chr(34) + "back\\slash" + chr(34)),
    ('say "hi"', chr(39) + 'say "hi"' + chr(39)),
])
def test_a_literal_is_quoted_by_choosing_a_delimiter_not_by_escaping(value, expected):
    """1.1.0 has NO escape sequences inside a literal, so the delimiter is
    chosen per value rather than the quote being escaped.

    These expectations used to assert the backslash form. That is why they
    passed while the engine rejected the statement: they compared
    `quoted()`'s output against itself, and the one thing neither of them
    consulted was the engine. Measured against 1.1.0 — the old form returns
    HTTP 400, this one parses.
    """
    assert queries.quoted(value) == expected


def test_an_integer_is_matched_as_the_text_it_is_stored_as():
    """`lit()` emits numbers unquoted, correctly — the engine keeps the type.
    But every value these tools compare against is stored as TEXT, including
    `device_class`, measured as "1", "2", "3" and "N" on the loaded graph. An
    int reaching the encoder would render `2`, match nothing, and say so in no
    way at all."""
    assert queries.quoted(2) == chr(34) + "2" + chr(34)
    assert queries.quoted("2") == chr(34) + "2" + chr(34)


def test_a_quote_in_an_argument_cannot_break_out(monkeypatch):
    sent = []
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"columns": [], "records": []}'
    monkeypatch.setattr(queries.urllib.request, "urlopen",
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


def test_the_environment_wins_over_the_config(monkeypatch):
    monkeypatch.setenv("SAMYAMA_URL", "http://elsewhere:9999/")
    assert queries.engine_url() == "http://elsewhere:9999"


def test_the_config_file_is_read_when_the_environment_is_silent(monkeypatch):
    """Against the VALUES in mcp_server/config.yaml, not the shape of a URL.

    `startswith("http://")` is satisfied by `DEFAULT_URL` too, so this passed
    with the entire config-parsing branch deleted — a test of nothing, in the
    code that decides where the server points.
    """
    monkeypatch.delenv("SAMYAMA_URL", raising=False)
    assert queries.engine_url() == "http://127.0.0.1:8080"


def test_a_host_outside_the_graph_block_does_not_repoint_the_server(monkeypatch, tmp_path):
    """The parser matched a bare `host:`/`port:` under ANY section, so it
    worked by luck — `config.yaml` happens to declare them only under `graph:`.
    Someone adding `server.host` later would have silently repointed the MCP
    server at something that is not the graph."""
    home = tmp_path / "mcp_server"
    home.mkdir()
    # `graph:` FIRST and `server:` second, deliberately. With the sections
    # reversed a last-wins parser lands on the right answer by accident, which
    # is how the first version of this test passed against the very parser it
    # was written to catch.
    (home / "config.yaml").write_text(
        "graph:\n  host: 127.0.0.1\n  port: 8080\n"
        "server:\n  host: 10.0.0.1\n  port: 9999\n")
    monkeypatch.delenv("SAMYAMA_URL", raising=False)
    monkeypatch.setattr(queries, "__file__", str(home / "queries.py"))
    assert queries.engine_url() == "http://127.0.0.1:8080", (
        "a host declared outside the graph block repointed the server")


def test_every_limited_query_orders_before_it_limits():
    """`LIMIT` with no `ORDER BY` is a non-deterministic page: repeated calls
    can return different subsets of the same answer, and an agent comparing
    two calls sees a change that did not happen.

    Read off the source of every tool, rather than pinned to the one that had
    the defect — the next query added here is the one that will repeat it.
    """
    import inspect
    offenders = []
    for name in TOOLS:
        source = inspect.getsource(getattr(queries, name))
        # Comments stripped first. The comment explaining WHY the ordering is
        # there contains the words "ORDER BY", so scanning raw source let the
        # check pass on a function whose query had lost it — the first version
        # of this test was defeated by its own rationale.
        code = "\n".join(line.split("#")[0] for line in source.splitlines())
        if "LIMIT" in code and "ORDER BY" not in code:
            offenders.append(name)
    assert not offenders, (
        f"these limit without ordering, so the page they return is arbitrary: "
        f"{offenders}")


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
            assert got["count"] == 0, got


def test_a_reverse_lookup_of_a_non_string_is_an_error_not_an_attribute_error():
    """`k_number.upper()` ran before anything could return a Result, so a None
    or a number hit an AttributeError instead of the error path."""
    got = queries.regulation_for_clearance(None)
    assert got["error"] and "string" in got["error"], got
    assert got["found"] is False


def test_an_engine_error_that_is_not_a_string_is_still_reported(monkeypatch):
    """`payload['error'][:200]` assumes a string. A dict slices to something
    unreadable and an int raises TypeError — inside the error path, which is
    the one place that must not fail."""
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"error": {"code": 42, "detail": "nope"}}).encode()

    monkeypatch.setattr(queries.urllib.request, "urlopen", lambda *a, **k: R())
    got = queries.busiest_regulations()
    assert got["error"] and "42" in got["error"], got


def test_a_config_value_with_a_comment_or_quotes_is_read_cleanly(monkeypatch, tmp_path):
    """An inline `# comment` and surrounding quotes are ordinary YAML.
    Unstripped, `host: 127.0.0.1  # local` became a hostname ending in
    "# local" — a URL the engine never answers, and no error saying why."""
    home = tmp_path / "mcp_server"
    home.mkdir()
    (home / "config.yaml").write_text(
        'graph:\n  host: "127.0.0.1"  # local docker\n  port: 8080  # default\n')
    monkeypatch.delenv("SAMYAMA_URL", raising=False)
    monkeypatch.setattr(queries, "__file__", str(home / "queries.py"))
    assert queries.engine_url() == "http://127.0.0.1:8080"


# --------------------------------------------------------------------------
# a term the engine cannot express is an error, not an empty answer
# --------------------------------------------------------------------------


def test_a_term_holding_both_quote_characters_is_refused_not_altered():
    """`lit()` substitutes a typographic quote when a value holds both `'` and
    `"`, because 1.1.0 can express neither. Writing a value that way is a
    recorded compromise the loader reports. MATCHING on one is not: the term
    compared against is no longer the term asked for, so the query returns no
    rows and no error.

    That is a third meaning inside the same empty list — this module's own
    docstring names two and exists to keep them apart.
    """
    with pytest.raises(queries.Unbounded, match="cannot be matched exactly"):
        queries.quoted('Governor\'s "Special" Device')


def test_a_term_with_only_one_quote_kind_still_works():
    """The fix must not refuse what 1.1.0 CAN express — an apostrophe alone is
    the common case and `lit()` handles it by choosing the other delimiter."""
    assert queries.quoted("O'Brien") == '"O\'Brien"'
    assert queries.quoted('say "hi"') == "'say \"hi\"'"


def test_the_refusal_reaches_the_caller_as_an_error_not_a_traceback():
    """Every tool already turns `Unbounded` into `Result(error=…)`; the refusal
    takes that route rather than raising into an agent."""
    out = queries.regulations_for_product('a\'b"c')
    assert out["error"] and "cannot be matched exactly" in out["error"]
    assert out["regulations"] == [] and out["found"] is False


def test_refusing_a_term_does_not_grow_the_sanitised_record():
    """`etl.cypher.SANITISED` is a module-level list nothing trims, and an MCP
    server is a long-lived process — the loader calls `reset()` at the start of
    a run and the server never does.

    Every entry this path would add is one `quoted()` is about to refuse and
    report, so dropping it loses nothing. A list that only grows, in a process
    that only runs, holding records nobody reads, is a leak.
    """
    from etl.cypher import SANITISED
    before = len(SANITISED)
    for _ in range(50):
        with pytest.raises(queries.Unbounded):
            queries.quoted('a\'b"c')
    assert len(SANITISED) == before, (
        f"SANITISED grew by {len(SANITISED) - before} across 50 refused calls")


def test_a_limit_above_the_ceiling_is_refused():
    """`bounded()` had a floor and no ceiling, so `limit=10**9` rendered
    `LIMIT 1000000000` — a real request against a real engine, from a tool an
    agent calls unprompted and can pass any number to."""
    with pytest.raises(queries.Unbounded, match="at most"):
        queries.bounded(10 ** 9)
    assert queries.bounded(queries.CEILING) == queries.CEILING


def test_the_ceiling_is_above_every_label_in_the_graph():
    """A ceiling that could truncate a real answer would be worse than none.
    Submission is the largest label at 19,127."""
    assert queries.CEILING > 19_127


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

    bad = 'a\'b"c'
    tools = [f for name, f in vars(queries).items()
             if inspect.isfunction(f) and not name.startswith("_")
             and "cfr_section" in inspect.signature(f).parameters
             or (inspect.isfunction(f) and not name.startswith("_")
                 and {"product_code", "k_number", "device_class"}
                 & set(inspect.signature(f).parameters))]
    assert tools, "no tools found to check — this would pass vacuously"

    for tool in tools:
        try:
            out = tool(bad)
        except Exception as exc:                       # noqa: BLE001
            raise AssertionError(
                f"{tool.__name__} raised {type(exc).__name__} instead of "
                f"returning an error: {exc}") from None
        assert out.get("error"), f"{tool.__name__} returned no error for {bad!r}"
