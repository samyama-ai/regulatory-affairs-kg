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
import os
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


# --------------------------------------------------------------------------
# an empty answer and a broken one are different facts
# --------------------------------------------------------------------------

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
    monkeypatch.setattr(queries, "run", queries.run)
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


# --------------------------------------------------------------------------
# the counting tool is separate for a reason
# --------------------------------------------------------------------------

def test_the_count_is_not_taken_from_the_capped_list(monkeypatch):
    """`clearances_under_regulation` is LIMITed. Reading its length as the
    total understates the blast radius of a rule change, which is the one
    number this graph exists to get right."""
    import inspect
    listing = inspect.getsource(queries.clearances_under_regulation)
    counting = inspect.getsource(queries.count_clearances_under_regulation)
    assert "LIMIT" in listing
    assert "LIMIT" not in counting
    assert "count(s)" in counting


# --------------------------------------------------------------------------
# values are inlined, because 1.1.0 takes no parameters
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("870.5150", "'870.5150'"),
    ("O'Brien", "'O\\'Brien'"),
    ("back\\slash", "'back\\\\slash'"),
])
def test_a_literal_is_escaped_before_it_is_inlined(value, expected):
    """1.1.0's /api/query takes no parameters, so values are inlined. That
    makes escaping the whole of the defence, and it is done in one place."""
    assert queries.quoted(value) == expected


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
    # present either way. What matters is that the quote immediately before the
    # payload is escaped, so the literal does not terminate there.
    assert "\\' RETURN" in cypher, f"the injected quote was not escaped: {cypher}"
    assert cypher.rstrip().endswith("specialty"), f"query truncated: {cypher[-60:]}"


# --------------------------------------------------------------------------
# where the engine is
# --------------------------------------------------------------------------

def test_the_environment_wins_over_the_config(monkeypatch):
    monkeypatch.setenv("SAMYAMA_URL", "http://elsewhere:9999/")
    assert queries.engine_url() == "http://elsewhere:9999"


def test_the_config_file_is_read_when_the_environment_is_silent(monkeypatch):
    monkeypatch.delenv("SAMYAMA_URL", raising=False)
    url = queries.engine_url()
    assert url.startswith("http://"), url


# --------------------------------------------------------------------------
# against a live engine
# --------------------------------------------------------------------------

FIXTURE = [
    "CREATE (r:Regulation {cfr_section: '870.5150', source: 'test'})",
    "CREATE (p:ProductCode {product_code: 'DXY', device_class: '2', "
    "definition: 'A test category', medical_specialty: 'CV', source: 'test'})",
    "CREATE (s:Submission {id: 'K999001', device_name: 'A test device', "
    "applicant: 'Acme', decision_date: '2024-01-02', "
    "advisory_committee: 'Cardiovascular', source: 'test'})",
    "MATCH (p:ProductCode {product_code: 'DXY'}), (r:Regulation {cfr_section: '870.5150'}) "
    "CREATE (p)-[:GOVERNED_BY]->(r)",
    "MATCH (s:Submission {id: 'K999001'}), (p:ProductCode {product_code: 'DXY'}) "
    "CREATE (s)-[:CLASSIFIED_AS]->(p)",
]


def test_url() -> str | None:
    """Only `SAMYAMA_TEST_URL`. Never `SAMYAMA_URL`, never a default.

    A test that writes and deletes must not be able to find an engine by
    accident. Requiring its own variable means pointing these at a loaded graph
    has to be a decision somebody typed.
    """
    return os.environ.get("SAMYAMA_TEST_URL")


def engine_available(url: str) -> bool:
    try:
        urllib.request.urlopen(f"{url}/api/tenants", timeout=2).read()
        return True
    except Exception:
        return False


@pytest.fixture
def loaded_engine(monkeypatch):
    url = test_url()
    if not url or not engine_available(url):
        message = ("no engine at SAMYAMA_TEST_URL"
                   if not url else f"no engine at {url}")
        if os.environ.get("SAMYAMA_REQUIRE_ENGINE") == "1":
            pytest.fail(f"{message} — SAMYAMA_REQUIRE_ENGINE=1 forbids skipping this")
        pytest.skip(f"{message} — set it to a FRESH instance, never the demo engine")

    # Point the queries at the test engine only for the duration of the test.
    monkeypatch.setenv("SAMYAMA_URL", url)

    before = queries.run("MATCH (n) RETURN count(n) AS n").rows
    existing = before[0]["n"] if before else 0
    if existing:
        pytest.fail(
            f"SAMYAMA_TEST_URL points at an engine holding {existing:,} nodes. "
            f"These tests write and DETACH DELETE; run them against a fresh "
            f"instance. See DATASET-CARD.md issue 10.")

    for statement in FIXTURE:
        queries.run(statement)
    yield
    queries.run("MATCH (n) WHERE n.source = 'test' DETACH DELETE n")


def test_the_change_impact_query_returns_the_clearance(loaded_engine):
    """The question this graph exists for, run end to end."""
    got = queries.clearances_under_regulation("870.5150")
    assert got["error"] is None
    assert any(row["clearance"] == "K999001" for row in got["clearances"])


def test_the_count_matches_the_listing(loaded_engine):
    listed = queries.clearances_under_regulation("870.5150")
    counted = queries.count_clearances_under_regulation("870.5150")
    assert counted["error"] is None
    assert counted["total"] == len(listed["clearances"])


def test_the_product_to_law_join_resolves(loaded_engine):
    got = queries.regulations_for_product("DXY")
    assert got["error"] is None
    assert got["regulations"][0]["cfr_section"] == "870.5150"


def test_the_reverse_lookup_resolves(loaded_engine):
    got = queries.regulation_for_clearance("k999001")
    assert got["found"] is True, "a lower-case k-number should still resolve"
    assert got["governed_by"][0]["cfr_section"] == "870.5150"


def test_an_unknown_clearance_is_empty_not_an_error(loaded_engine):
    got = queries.regulation_for_clearance("K000000")
    assert got["error"] is None
    assert got["found"] is False


def test_the_advisory_committee_grouping_returns_rows(loaded_engine):
    got = queries.clearances_by_advisory_committee()
    assert got["error"] is None
    assert any(row["committee"] == "Cardiovascular" for row in got["committees"])
