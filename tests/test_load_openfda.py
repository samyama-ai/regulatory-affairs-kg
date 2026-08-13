"""Tests for the openFDA loader.

The offline tests cover `lit()`, which is the riskiest function in the loader:
the engine takes no query parameters, so every value is interpolated into
statement text and a bad literal is either a parse error or — worse — a silent
corruption.

The engine test loads a small fixture and asserts the graph, then loads it again
and asserts the counts did not move. It skips when no engine is reachable.

    docker run --rm -p 8080:8080 public.ecr.aws/f9f6l5u4/samyama-graph:1.1.0
    pytest tests/test_load_openfda.py
"""

import json
import os
import urllib.request
from pathlib import Path

import pytest

from etl.cypher import SANITISED, lit, merge, props, split_statements
from etl.load_openfda import Engine, load_classifications, load_clearances

SAMYAMA_URL = os.environ.get("SAMYAMA_URL", "http://localhost:8080")
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "regulatory_affairs_kg.cypher"


# --------------------------------------------------------------------------
# lit() — no engine needed
# --------------------------------------------------------------------------

def test_plain_value_is_double_quoted():
    assert lit("Catheter") == '"Catheter"'


def test_apostrophe_stays_in_double_quotes():
    """Commonest case in this corpus — 377 values carry an apostrophe."""
    assert lit("patient's device") == '"patient\'s device"'


def test_double_quote_switches_to_single_quotes():
    """The engine has no escape sequences: `\\"` is a parse error, so the quote
    style has to change rather than the value."""
    assert lit('a "personnel protective shield"') == "'a \"personnel protective shield\"'"


@pytest.fixture
def sanitised_log():
    """SANITISED is a module-level list. A test that clears it in place makes
    itself order-dependent and unsafe under -p xdist; this saves and restores
    it instead."""
    saved = list(SANITISED)
    SANITISED.clear()
    yield SANITISED
    SANITISED[:] = saved


def test_both_quote_types_is_recorded_not_silent(sanitised_log):
    """A value containing both cannot be represented. Altering it is defensible;
    doing so silently is not."""
    out = lit("""the patient's "shield" device""")
    assert out.startswith('"') and out.endswith('"')
    assert '"' not in out[1:-1]          # no bare double quote left inside
    assert "”" in out                     # replaced, not deleted
    assert len(sanitised_log) == 1        # and reported


def test_newlines_and_tabs_collapse():
    """Not escaping — a literal newline breaks the parser and the engine offers
    no way to encode one."""
    assert lit("a\nb\tc\rd") == '"a b c d"'


def test_every_control_character_collapses_not_just_the_common_three():
    """NUL, vertical tab, form feed and DEL reach the parser too. What the
    engine does with them is not worth finding out mid-load, and the docstring
    claims they are handled — so assert it rather than claim it."""
    assert lit("a\x00b\x0bc\x0cd\x7fe") == '"a b c d e"'
    assert lit("\x01\x1f") == '"  "'


# --------------------------------------------------------------------------
# split_statements() — the other hand-rolled parser, also no engine needed
# --------------------------------------------------------------------------

def test_splits_on_semicolons_and_drops_comments():
    text = "CREATE CONSTRAINT ON (n:A) ASSERT n.id IS UNIQUE;  // why\nCREATE INDEX ON :B(x);"
    assert split_statements(text) == [
        "CREATE CONSTRAINT ON (n:A) ASSERT n.id IS UNIQUE",
        "CREATE INDEX ON :B(x)",
    ]


def test_a_url_inside_a_literal_is_not_a_comment():
    """`//` in `https://` truncated the statement silently before this. Nothing
    in the schema carries a URL today; this is here so adding one is not a trap."""
    text = 'MERGE (n:S {url: "https://fda.gov/x"}) ON CREATE SET n.a = 1;'
    assert split_statements(text) == [
        'MERGE (n:S {url: "https://fda.gov/x"}) ON CREATE SET n.a = 1'
    ]


def test_a_semicolon_inside_a_literal_does_not_end_the_statement():
    assert split_statements("MERGE (n:S {v: 'a;b'});") == ["MERGE (n:S {v: 'a;b'})"]


def test_a_comment_marker_inside_a_literal_survives():
    text = 'MERGE (n:S {v: "a // not a comment"});'
    assert split_statements(text) == ['MERGE (n:S {v: "a // not a comment"})']


def test_a_trailing_comment_does_not_swallow_what_follows():
    """The failure this function exists for: dropping only lines that *start*
    with // and then flattening lets one trailing comment comment out the rest
    of the file, and the engine reports success on the shorter script."""
    text = "CREATE INDEX ON :A(x);  // trailing\nCREATE INDEX ON :B(y);"
    assert len(split_statements(text)) == 2


def test_the_real_schema_splits_into_sound_statements():
    """Structure first, then the count — so a contributor adding an index gets
    a message rather than `assert 37 == 36`."""
    schema = SCHEMA_PATH.read_text()
    statements = split_statements(schema)
    assert all(s.strip() for s in statements), "an empty statement survived the split"
    assert not [s for s in statements if "//" in s], \
        f"a comment leaked into a statement: {[s for s in statements if '//' in s][:2]}"
    assert len(statements) == 36, (
        f"{SCHEMA_PATH.name} now splits into {len(statements)} statements, not 36. "
        f"If that is a deliberate schema change, update this test, the README "
        f"and DATASET-CARD together. Statements: {[s[:60] for s in statements]}"
    )


def test_none_is_null_not_the_string_none():
    assert lit(None) == "null"


def test_backslash_is_left_alone():
    """`\\\\` is not an escape here; it passes through as two characters."""
    assert lit(r"a\b") == '"a\\b"'


def test_props_skips_empty_values_but_keeps_zero():
    """Note `n.e = "0"` — the integer is stored as a string. That is not a slip:
    /api/query takes no parameters, so every value is interpolated as text and
    the engine has no way to be told otherwise. It is why queries on this graph
    compare strings, and it is recorded in the dataset card."""
    out = props({"a": "x", "b": None, "c": "", "d": [], "e": 0}, "n")
    assert out == 'n.a = "x", n.e = "0"', out


def test_props_returns_empty_string_when_nothing_survives():
    """`ON CREATE SET ` with an empty tail is a parse error — merge() checks
    this, so props() must report emptiness rather than something truthy."""
    assert props({"a": None, "b": ""}, "n") == ""


def test_merge_omits_the_set_clause_when_there_is_nothing_to_set():
    assert merge("L", "k", "v", {}, "n") == 'MERGE (n:L {k: "v"})'
    assert "ON CREATE SET" in merge("L", "k", "v", {"a": "x"}, "n")


def test_list_values_are_joined_not_repred():
    """openFDA harmonised fields arrive as arrays. str() on one yields a Python
    repr, which is not data."""
    assert lit(["870.5150", "870.1250"]) == '"870.5150; 870.1250"'


# --------------------------------------------------------------------------
# against a live engine
# --------------------------------------------------------------------------

def engine_available() -> bool:
    try:
        with urllib.request.urlopen(f"{SAMYAMA_URL}/api/tenants", timeout=2) as response:
            response.read()          # `with`, so the socket is not left open
        return True
    except Exception:
        return False


def require_engine() -> None:
    """Skip at call time, not at import time.

    `pytest.mark.skipif(not engine_available())` freezes the decision during
    collection — start the engine while the suite is collecting and the tests
    still skip, reporting green for something that was never run.
    """
    if not engine_available():
        pytest.skip(f"no Samyama engine at {SAMYAMA_URL}")

FIXTURE_CLASSIFICATIONS = [
    {"product_code": "TST", "device_name": 'Test "quoted" device',
     "device_class": "2", "regulation_number": "999.9001",
     "medical_specialty_description": "Cardiovascular"},
    {"product_code": "TS2", "device_name": "Second device",
     "device_class": "1", "regulation_number": "999.9001"},
    {"product_code": "TS3", "device_name": "Unclassified", "regulation_number": ""},
]
FIXTURE_CLEARANCES = [
    {"k_number": "K999001", "device_name": "Widget A", "applicant": "Acme's Devices",
     "product_code": "TST", "decision_date": "2024-01-01",
     "openfda": {"regulation_number": "999.9001"}},
    {"k_number": "K999002", "device_name": "Widget B", "applicant": "Beta Corp",
     "product_code": "TST", "decision_date": "2024-02-01", "openfda": {}},
    {"k_number": "K999003", "device_name": "Orphan", "applicant": "Gamma",
     "product_code": "", "openfda": {}},
]
META = {"endpoint": "test", "retrieved_at": "2026-08-12T00:00:00+00:00"}


def fresh_engine() -> Engine:
    """A plain Engine against the default graph.

    NOTE: tenants cannot be used to isolate a test. `/api/query` accepts a
    `graph` field but **ignores it** — writes sent to a named tenant land in the
    shared store and are visible from every other tenant. Measured on 1.1.0.
    So these tests use fixture keys that cannot collide with real data
    (product codes TST/TS2/TS3, K-numbers K9990xx, regulation 999.9001) and
    assert on those rather than on global counts.
    """
    return Engine(SAMYAMA_URL, "default")


# One source of truth for the fixture keys. They are deliberately shaped so
# they cannot collide with real openFDA data: FDA product codes are three
# letters and none is "TST"/"TS2"/"TS3", K-numbers of the form K999xxx are not
# issued, and CFR part 999 does not exist. This matters because /api/query
# ignores the `graph` field (known issue 7), so tests share the default store
# with whatever else is loaded and teardown deletes by key.
FIXTURE_CODE_LIST = ["TST", "TS2", "TS3"]
FIXTURE_K_LIST = ["K999001", "K999002", "K999003"]
FIXTURE_SECTION = "999.9001"
FIXTURE_CODES = json.dumps(FIXTURE_CODE_LIST)
FIXTURE_KS = json.dumps(FIXTURE_K_LIST)


@pytest.fixture
def loaded_fixture():
    """Hand back an engine, then delete everything the fixture created.

    Tenants cannot isolate this — `/api/query` ignores the `graph` field — so
    the fixture shares the default graph with whatever else is loaded. Without
    teardown these nodes persist, which corrupts the next run's counts and, on
    a developer's machine, quietly pollutes a real graph.

    The delete is asserted, not hoped for: if it silently failed the next test
    run would still pass while the graph filled up.

    Skips at call time when no engine is reachable — see `require_engine`.

    The three tests using this fixture are marked `xdist_group("engine")`.
    They share one store and tear down by key, so running them concurrently
    would let one test's teardown delete rows another is asserting on. The
    `sanitised_log` fixture guards the same hazard for the module global; this
    is the engine-side half of it.
    """
    require_engine()
    engine = fresh_engine()
    yield engine
    for query in (
        f"MATCH (p:ProductCode) WHERE p.product_code IN {FIXTURE_CODES} DETACH DELETE p",
        f"MATCH (s:Submission) WHERE s.id IN {FIXTURE_KS} DETACH DELETE s",
        f'MATCH (r:Regulation) WHERE r.cfr_section = "{FIXTURE_SECTION}" DETACH DELETE r',
    ):
        engine.run(query)
    left = (
        engine.scalar(f"MATCH (p:ProductCode) WHERE p.product_code IN {FIXTURE_CODES} RETURN count(p)"),
        engine.scalar(f"MATCH (s:Submission) WHERE s.id IN {FIXTURE_KS} RETURN count(s)"),
        engine.scalar(f'MATCH (r:Regulation) WHERE r.cfr_section = "{FIXTURE_SECTION}" RETURN count(r)'),
    )
    assert left == (0, 0, 0), f"teardown left fixture nodes behind: {left}"


def fixture_counts(engine: Engine) -> tuple:
    return (
        engine.scalar(f"MATCH (p:ProductCode) WHERE p.product_code IN {FIXTURE_CODES} RETURN count(p)"),
        engine.scalar(f'MATCH (r:Regulation) WHERE r.cfr_section = "{FIXTURE_SECTION}" RETURN count(r)'),
        engine.scalar(f"MATCH (s:Submission) WHERE s.id IN {FIXTURE_KS} RETURN count(s)"),
        engine.scalar('MATCH (p:ProductCode)-[g:GOVERNED_BY]->(r:Regulation)'
                      f' WHERE r.cfr_section = "{FIXTURE_SECTION}" RETURN count(g)'),
        engine.scalar('MATCH (s:Submission)-[c:CLASSIFIED_AS]->(:ProductCode)'
                      f" WHERE s.id IN {FIXTURE_KS} RETURN count(c)"),
    )


@pytest.mark.xdist_group("engine")
def test_loads_and_is_idempotent(loaded_fixture):
    """The whole design rests on MERGE, because a uniqueness constraint in this
    engine does not reject a duplicate CREATE. So loading twice must not double
    anything — asserted, not assumed."""
    engine = loaded_fixture
    load_classifications(engine, FIXTURE_CLASSIFICATIONS, META)
    load_clearances(engine, FIXTURE_CLEARANCES, META)
    first = fixture_counts(engine)

    # 3 product codes; 1 regulation (TST and TS2 share 999.9001); 3 submissions;
    # 2 GOVERNED_BY (TS3 has no regulation); 2 CLASSIFIED_AS (K999003 has no code).
    assert first == (3, 1, 3, 2, 2), first

    load_classifications(engine, FIXTURE_CLASSIFICATIONS, META)
    load_clearances(engine, FIXTURE_CLEARANCES, META)
    assert fixture_counts(engine) == first, "loading twice changed the graph — MERGE is not holding"


@pytest.mark.xdist_group("engine")
def test_change_impact_query_traverses(loaded_fixture):
    """Q1 end to end: a rule reaches its clearances in two hops."""
    engine = loaded_fixture
    load_classifications(engine, FIXTURE_CLASSIFICATIONS, META)
    load_clearances(engine, FIXTURE_CLEARANCES, META)

    affected = engine.scalar(
        "MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission)"
        f' WHERE r.cfr_section = "{FIXTURE_SECTION}" RETURN count(s)'
    )
    assert affected == 2, f"expected both TST clearances, got {affected}"


@pytest.mark.xdist_group("engine")
def test_quoted_device_name_survives_the_round_trip(loaded_fixture):
    """The fixture deliberately contains a double-quoted device name. If the
    literal encoding is wrong this is where it shows up — either as a load
    failure or as a mangled value."""
    engine = loaded_fixture
    load_classifications(engine, FIXTURE_CLASSIFICATIONS, META)

    name = engine.scalar(
        f'MATCH (p:ProductCode) WHERE p.product_code = "{FIXTURE_CODE_LIST[0]}" RETURN p.device_name'
    )
    assert name == 'Test "quoted" device', repr(name)
