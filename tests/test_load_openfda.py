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
from uuid import uuid4

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
    """Skip at call time, not at import time — or fail, if asked to.

    `pytest.mark.skipif(not engine_available())` freezes the decision during
    collection: start the engine while the suite is collecting and the tests
    still skip, reporting green for something that was never run.

    Skipping is right for a local run. It is wrong for CI, because the claim
    these tests carry — every write is a MERGE, since a uniqueness constraint
    in this engine does not reject a duplicate CREATE — is then proved by a
    test nothing makes anyone run. Turn a MERGE back into a CREATE and the
    suite still goes green.

    So `SAMYAMA_REQUIRE_ENGINE=1` makes an unreachable engine a failure rather
    than a skip. Local runs stay easy; CI cannot silently prove nothing.
    """
    if engine_available():
        return
    message = f"no Samyama engine at {SAMYAMA_URL}"
    if os.environ.get("SAMYAMA_REQUIRE_ENGINE") == "1":
        pytest.fail(f"{message} — SAMYAMA_REQUIRE_ENGINE=1 forbids skipping this")
    pytest.skip(message)

def fixture_rows(run: str) -> tuple[list[dict], list[dict], dict]:
    """Fixture data keyed to one run, so no two runs can touch each other.

    Keys carry a random suffix rather than being fixed strings. `TST` was the
    same shape as a real FDA product code — three letters — so a stray
    SAMYAMA_URL pointing at a populated graph meant teardown could DETACH
    DELETE a real node, taking its real edges with it and leaving nothing to
    say so. Nothing requires a test product code to be three characters.

    It also makes leftovers from an interrupted run harmless: the next run
    generates different keys and cannot see them.
    """
    codes = [f"TST-{run}", f"TS2-{run}", f"TS3-{run}"]
    ks = [f"K999001-{run}", f"K999002-{run}", f"K999003-{run}"]
    section = f"999.9001-{run}"
    classifications = [
        {"product_code": codes[0], "device_name": 'Test "quoted" device',
         "device_class": "2", "regulation_number": section,
         "medical_specialty_description": "Cardiovascular"},
        {"product_code": codes[1], "device_name": "Second device",
         "device_class": "1", "regulation_number": section},
        {"product_code": codes[2], "device_name": "Unclassified", "regulation_number": ""},
    ]
    clearances = [
        {"k_number": ks[0], "device_name": "Widget A", "applicant": "Acme's Devices",
         "product_code": codes[0], "decision_date": "2024-01-01",
         "openfda": {"regulation_number": section}},
        {"k_number": ks[1], "device_name": "Widget B", "applicant": "Beta Corp",
         "product_code": codes[0], "decision_date": "2024-02-01", "openfda": {}},
        {"k_number": ks[2], "device_name": "Orphan", "applicant": "Gamma",
         "product_code": "", "openfda": {}},
    ]
    return classifications, clearances, {"codes": codes, "ks": ks, "section": section}


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


@pytest.fixture
def loaded_fixture():
    """An engine plus fixture data keyed to this run alone, cleaned up after.

    Tenants cannot isolate this — `/api/query` ignores the `graph` field — so
    every test shares one store with whatever else is loaded.

    The keys therefore carry a random per-run suffix. Fixed keys were the real
    hazard: `SAMYAMA_URL` is read from the environment with no check, `TST` was
    the same shape as a real FDA product code, and teardown is `DETACH DELETE`
    — so a variable left exported from a debugging session meant the suite
    could delete a real node and its real edges, with nothing to say so. A
    per-run suffix makes that impossible by construction rather than by
    convention, and also makes leftovers from an interrupted run invisible to
    the next one instead of failing it with a message that blames MERGE.

    Because the keys are unique per run, concurrent runs cannot collide either.

    Teardown deletes all three labels even if one delete fails, then asserts
    the graph is clean — a silently failed cleanup would otherwise let the next
    run pass while the graph filled up.

    Skips at call time when no engine is reachable — see `require_engine`.
    """
    require_engine()
    # Upper-cased: the loader normalises K-numbers with .upper(), so a
    # lower-case suffix would be stored differently from what we query for.
    run = uuid4().hex[:8].upper()
    classifications, clearances, keys = fixture_rows(run)
    engine = fresh_engine()
    yield engine, classifications, clearances, keys

    codes, ks, section = json.dumps(keys["codes"]), json.dumps(keys["ks"]), keys["section"]
    failures = []
    for query in (
        f"MATCH (p:ProductCode) WHERE p.product_code IN {codes} DETACH DELETE p",
        f"MATCH (s:Submission) WHERE s.id IN {ks} DETACH DELETE s",
        f'MATCH (r:Regulation) WHERE r.cfr_section = "{section}" DETACH DELETE r',
    ):
        try:
            engine.run(query)
        except Exception as exc:          # keep going; a later delete may still work
            failures.append(f"{query[:50]} -> {exc}")
    assert not failures, "teardown could not run: " + "; ".join(failures)
    left = (
        engine.scalar(f"MATCH (p:ProductCode) WHERE p.product_code IN {codes} RETURN count(p)"),
        engine.scalar(f"MATCH (s:Submission) WHERE s.id IN {ks} RETURN count(s)"),
        engine.scalar(f'MATCH (r:Regulation) WHERE r.cfr_section = "{section}" RETURN count(r)'),
    )
    assert left == (0, 0, 0), f"teardown left fixture nodes behind: {left}"


def fixture_counts(engine: Engine, keys: dict) -> tuple:
    codes, ks, section = json.dumps(keys["codes"]), json.dumps(keys["ks"]), keys["section"]
    return (
        engine.scalar(f"MATCH (p:ProductCode) WHERE p.product_code IN {codes} RETURN count(p)"),
        engine.scalar(f'MATCH (r:Regulation) WHERE r.cfr_section = "{section}" RETURN count(r)'),
        engine.scalar(f"MATCH (s:Submission) WHERE s.id IN {ks} RETURN count(s)"),
        engine.scalar("MATCH (p:ProductCode)-[g:GOVERNED_BY]->(r:Regulation)"
                      f' WHERE r.cfr_section = "{section}" RETURN count(g)'),
        engine.scalar("MATCH (s:Submission)-[c:CLASSIFIED_AS]->(:ProductCode)"
                      f" WHERE s.id IN {ks} RETURN count(c)"),
    )


def test_loads_and_is_idempotent(loaded_fixture):
    """The whole design rests on MERGE, because a uniqueness constraint in this
    engine does not reject a duplicate CREATE. So loading twice must not double
    anything — and must still write, which is the half a count check misses."""
    engine, classifications, clearances, keys = loaded_fixture
    load_classifications(engine, classifications, META)
    load_clearances(engine, clearances, META)
    first = fixture_counts(engine, keys)

    # 3 product codes; 1 regulation (the first two share a section); 3 submissions;
    # 2 GOVERNED_BY (the third code has no regulation); 2 CLASSIFIED_AS (the third
    # clearance has no product code).
    assert first == (3, 1, 3, 2, 2), first

    # Change a value between the loads. Counts staying equal proves nothing was
    # duplicated; this proves ON MATCH SET actually wrote, which is what makes
    # "re-run the whole thing" a real recovery plan rather than a no-op.
    changed = [dict(c) for c in classifications]
    changed[1]["device_name"] = "Second device, revised"
    load_classifications(engine, changed, META)
    load_clearances(engine, clearances, META)

    assert fixture_counts(engine, keys) == first, "loading twice changed the graph — MERGE is not holding"
    name = engine.scalar(
        f'MATCH (p:ProductCode) WHERE p.product_code = "{keys["codes"][1]}" RETURN p.device_name'
    )
    assert name == "Second device, revised", f"ON MATCH SET did not write: {name!r}"


def test_change_impact_query_traverses(loaded_fixture):
    """Q1 end to end: a rule reaches its clearances in two hops."""
    engine, classifications, clearances, keys = loaded_fixture
    load_classifications(engine, classifications, META)
    load_clearances(engine, clearances, META)

    affected = engine.scalar(
        "MATCH (r:Regulation)<-[:GOVERNED_BY]-(:ProductCode)<-[:CLASSIFIED_AS]-(s:Submission)"
        f' WHERE r.cfr_section = "{keys["section"]}" RETURN count(s)'
    )
    assert affected == 2, f"expected both clearances on the first code, got {affected}"


def test_quoted_device_name_survives_the_round_trip(loaded_fixture):
    """The fixture deliberately contains a double-quoted device name. If the
    literal encoding is wrong this is where it shows up — either as a load
    failure or as a mangled value."""
    engine, classifications, _, keys = loaded_fixture
    load_classifications(engine, classifications, META)

    name = engine.scalar(
        f'MATCH (p:ProductCode) WHERE p.product_code = "{keys["codes"][0]}" RETURN p.device_name'
    )
    assert name == 'Test "quoted" device', repr(name)
