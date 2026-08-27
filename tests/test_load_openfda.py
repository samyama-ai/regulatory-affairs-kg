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
from pathlib import Path
from uuid import uuid4

import pytest

from etl.cypher import SANITISED, identifier, lit, merge, props, split_statements
from tests.mcp_support import writable_test_engine
from etl.load_openfda import (Engine, load_classifications, load_clearances,
                              select_smoke_rows, unresolvable_joins)

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
# unresolvable_joins() — the stale-./data guard, no engine needed
# --------------------------------------------------------------------------

def test_matching_files_have_no_unresolvable_joins():
    klass = [{"product_code": "AAA"}, {"product_code": "BBB"}]
    clear = [{"product_code": "AAA"}, {"product_code": "BBB"}]
    assert unresolvable_joins(klass, clear) == []


def test_a_stale_classification_file_is_caught():
    """510k.json newer than classification.json. The MERGE is accepted and
    writes nothing, so without this the run looks clean and the edge count is
    quietly short."""
    klass = [{"product_code": "AAA"}]
    clear = [{"product_code": "AAA"}, {"product_code": "ZZZ"}, {"product_code": "YYY"}]
    assert unresolvable_joins(klass, clear) == ["YYY", "ZZZ"]


def test_clearances_with_no_product_code_are_not_counted_as_unresolvable():
    """A blank code is a known gap already reported separately, not a mismatch
    between the two files."""
    assert unresolvable_joins([{"product_code": "AAA"}],
                              [{"product_code": ""}, {"product_code": None}, {}]) == []


def test_the_answer_does_not_depend_on_what_is_already_in_the_graph():
    """The whole reason this check reads the input instead of the graph.

    Comparing statements issued against `measure()` only worked on an empty
    engine: `measure()` counts the entire store, so on a populated graph the
    measured figure is at least the issued one and a shortfall could never
    show — and re-running the load is the documented recovery plan, which is
    exactly that case.

    Asserted by calling it, not by reading its source: an engine that is not
    running cannot be consulted, so a passing call proves the independence
    directly.
    """
    klass = [{"product_code": "AAA"}]
    clear = [{"product_code": "AAA"}, {"product_code": "ZZZ"}]
    expected = ["ZZZ"]

    assert unresolvable_joins(klass, clear) == expected

    # Same inputs, no engine reachable at all. If the function had grown a
    # dependency on graph state this would raise rather than agree.
    #
    # `monkeypatch`, not a hand-rolled save and restore. This set
    # `os.environ` directly and put it back in a `finally`, which is correct
    # until an assertion inside the block raises through a path that skips it
    # — and the variable it leaves behind is the one that points every other
    # engine-backed test in this repo at a graph.
    #
    # This is the LOADER's variable, deliberately: `etl.load_openfda` reads
    # `SAMYAMA_URL`, and pointing it at a dead port is the whole point of the
    # check. Selecting a TEST target is what must never come from it.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SAMYAMA_URL", "http://localhost:1")
        assert unresolvable_joins(klass, clear) == expected


# --------------------------------------------------------------------------
# select_smoke_rows() — --limit must not exceed the cap it was given
# --------------------------------------------------------------------------

def test_smoke_selection_never_exceeds_the_limit():
    klass = [{"product_code": f"C{i:03d}"} for i in range(50)]
    clear = [{"product_code": f"C{i:03d}"} for i in range(50)]
    for limit in (0, 1, 5, 40, 50, 999):
        k, c = select_smoke_rows(klass, clear, limit)
        assert len(k) <= limit and len(c) <= limit, (limit, len(k), len(c))


def test_smoke_selection_caps_even_when_codes_repeat():
    """The cap on `needed` is unreachable with real openFDA data, where a
    product code appears once in classification.json. It exists for a file
    where one does not — duplicate rows would otherwise push the selection past
    the limit the caller asked for.
    """
    klass = [{"product_code": "C001", "device_name": f"row {i}"} for i in range(20)]
    clear = [{"product_code": "C001"}]
    k, c = select_smoke_rows(klass, clear, 3)
    assert len(k) == 3, f"asked for 3, got {len(k)}"
    assert len(c) == 1


def test_smoke_selection_keeps_the_join_intact():
    """Truncating both sources independently gave a smoke load whose
    CLASSIFIED_AS edges all resolved to nothing — measured at zero overlap.
    The clearances are chosen first so the join exists by construction."""
    klass = [{"product_code": f"C{i:03d}"} for i in range(50)]
    clear = [{"product_code": "C049"}, {"product_code": "C048"}]
    k, c = select_smoke_rows(klass, clear, 2)
    assert unresolvable_joins(k, c) == []


def test_smoke_selection_does_not_spend_padding_on_blank_codes():
    """`wanted` used to include "" for clearances with no product code, so
    blank-code classifications took padding slots before being skipped."""
    klass = [{"product_code": ""}, {"product_code": "C001"}, {"product_code": "C002"}]
    clear = [{"product_code": "C001"}]
    k, c = select_smoke_rows(klass, clear, 2)
    codes = [row["product_code"] for row in k]

    assert codes[0] == "C001", "the joinable code must come first"
    assert len(k) == 2, f"asked for 2, got {len(k)}"
    assert "" not in codes, f'a blank code took a padding slot: {codes}'
    assert codes == ["C001", "C002"], codes
    assert unresolvable_joins(k, c) == []


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


def test_booleans_are_emitted_bare():
    """`true`, not `"true"`. Measured on 1.1.0: the engine accepts both and
    preserves the type, and a quoted boolean cannot be filtered on."""
    assert lit(True) == "true"
    assert lit(False) == "false"


def test_numbers_keep_their_type():
    """`WHERE n.v > 40` works against a numeric property and returns 400
    against the string "42" — so this is what makes numeric filtering possible
    at all, not a tidiness preference."""
    assert lit(42) == "42"
    assert lit(-7) == "-7"
    assert lit(3.5) == "3.5"
    assert lit("42") == '"42"', "a string that looks numeric must stay a string"
    assert lit("0870") == '"0870"', "leading zeros must survive — product codes"


def test_a_block_comment_is_stripped_and_its_semicolon_ignored():
    """1.1.0 parses `/* */`, including one containing a `;`. Splitting on that
    semicolon would cut a statement in half — silently, since the engine would
    just see a shorter script and report success."""
    assert split_statements("MATCH (n) /* a ; b */ RETURN n;") == ["MATCH (n) RETURN n"]
    assert split_statements("/* leading */ CREATE INDEX ON :A(x);") == ["CREATE INDEX ON :A(x)"]
    assert len(split_statements("CREATE INDEX ON :A(x); /* mid ; comment */ CREATE INDEX ON :B(y);")) == 2


def test_an_unterminated_block_comment_does_not_leak():
    assert split_statements("CREATE INDEX ON :A(x); /* never closed") == ["CREATE INDEX ON :A(x)"]


def test_both_merge_branches_get_the_same_assignments():
    """Writing `null` on the ON MATCH branch to clear an emptied field was
    tried and reverted — the engine accepts `SET x = null`, reports success and
    leaves the value in place, which `tests/test_engine_limits.py` pins.

    So the branches are identical, and "loading twice changes nothing" means
    nothing was duplicated, not that anything was refreshed. Asserted here so
    nobody reintroduces a null-writing branch believing it works.
    """
    statement = merge("T", "id", "K1", {"a": "x", "b": ""})
    on_create = statement.split("ON CREATE SET ")[1].split(" ON MATCH SET ")[0]
    on_match = statement.split("ON MATCH SET ")[1]
    assert on_create == on_match, (on_create, on_match)
    assert "null" not in statement, statement
    assert "b" not in statement, "an empty value must not be written at all"


def test_props_skips_empty_values_on_every_branch():
    assert props({"a": "x", "b": ""}, "n") == 'n.a = "x"'
    assert props({"a": "x", "b": None}, "n") == 'n.a = "x"'
    assert props({"a": "x", "b": []}, "n") == 'n.a = "x"'


def test_non_finite_floats_become_null_and_are_recorded(sanitised_log):
    """`repr(float("nan"))` is `nan` — a bare identifier the parser rejects. As a
    literal it would 400 partway through a 54,000-statement load, from a value
    that looked ordinary. Losing one value loudly beats failing at row 12,000."""
    assert lit(float("nan")) == "null"
    assert lit(float("inf")) == "null"
    assert lit(float("-inf")) == "null"
    assert len(sanitised_log) == 3
    assert all("non-finite" in r["reason"] for r in sanitised_log), sanitised_log


def test_finite_floats_are_unaffected():
    assert lit(0.0) == "0.0"
    assert lit(-3.5) == "-3.5"


def test_a_dict_value_raises_rather_than_storing_a_repr():
    """`str({'a': 1})` is Python syntax, not data. This can only be a caller
    mistake, so the first row shows it rather than all 19,127."""
    with pytest.raises(TypeError, match="dict passed to lit"):
        lit({"a": 1})


def test_line_separators_are_swept_like_control_characters():
    """U+2028 and U+2029 are invisible in an editor and the parser treats them
    as it treats a newline."""
    assert lit("a\u2028b\u2029c") == '"a b c"'


def test_identifiers_are_checked_not_trusted():
    """Labels, property names and variables are interpolated unquoted — they
    cannot be quoted, since the engine rejects backticks. A name carrying a
    space or a brace changes the statement's shape rather than its data."""
    assert identifier("product_code", "property") == "product_code"
    for bad in ("has space", "a-b", "1abc", "a}", "", "n) DETACH DELETE (n"):
        with pytest.raises(ValueError, match="not a plain identifier"):
            identifier(bad, "property")


def test_merge_rejects_a_crafted_label_or_key():
    with pytest.raises(ValueError, match="label"):
        merge("T {x:1})--(y", "id", "K1", {})
    with pytest.raises(ValueError, match="key"):
        merge("T", "id: 1}) MATCH (m", "K1", {})


def test_props_rejects_a_crafted_property_name():
    with pytest.raises(ValueError, match="property"):
        props({"a = 1, n.b": "x"}, "n")


def test_a_trailing_comment_does_not_swallow_what_follows():
    """The failure this function exists for: dropping only lines that *start*
    with // and then flattening lets one trailing comment comment out the rest
    of the file, and the engine reports success on the shorter script."""
    text = "CREATE INDEX ON :A(x);  // trailing\nCREATE INDEX ON :B(y);"
    assert len(split_statements(text)) == 2


def test_the_real_schema_splits_into_sound_statements():
    """Structure first, then the count — so a contributor adding an index gets
    a message rather than `assert 37 == 36`."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
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
    """Zero survives; empty string, None and empty collections do not.

    Note `n.e = 0`, unquoted. Values used to be interpolated as strings without
    exception, which did not merely lose the type: measured on 1.1.0,
    `WHERE n.v > 40` works against a numeric property and returns 400 against
    the string "42". Numbers and booleans are emitted bare so numeric filtering
    is possible at all.
    """
    out = props({"a": "x", "b": None, "c": "", "d": [], "e": 0}, "n")
    assert out == 'n.a = "x", n.e = 0', out


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

def require_engine() -> str:
    """The engine these writing tests may use, or a skip naming why not.

    **This module used to find one.** It read `SAMYAMA_URL` with a
    `localhost:8080` default, so a plain `pytest` on a machine with any engine
    on that port ran the whole engine-backed half against it — `MERGE`,
    `CREATE CONSTRAINT`, then `DETACH DELETE` on teardown. The node count came
    back to where it started, which is why it looked harmless; DATASET-CARD
    issue 10 records what that delete does to the graph it ran in.

    The per-run key suffix below was the mitigation for this, and it is a good
    one — it makes a stray write impossible to confuse with real data. It does
    not stop the write. Selecting the engine is what stops it.

    Resolved at call time, not at import: `pytest.mark.skipif(not
    engine_available())` freezes the decision during collection, so starting an
    engine while the suite collects reports green for something never run. And
    `SAMYAMA_REQUIRE_ENGINE=1` turns the skip into a failure, because the claim
    these tests carry — every write is a MERGE, since a uniqueness constraint
    here does not reject a duplicate CREATE — is otherwise proved by a test
    nothing makes anyone run.
    """
    return writable_test_engine("tests/test_load_openfda.py")


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
    So every test asserts on its own fixture keys rather than on global counts,
    and `fixture_rows` gives each run a distinct suffix so two runs cannot see
    or delete each other's rows.
    """
    return Engine(require_engine(), "default")


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
    try:
        left = (
            engine.scalar(f"MATCH (p:ProductCode) WHERE p.product_code IN {codes} RETURN count(p)"),
            engine.scalar(f"MATCH (s:Submission) WHERE s.id IN {ks} RETURN count(s)"),
            engine.scalar(f'MATCH (r:Regulation) WHERE r.cfr_section = "{section}" RETURN count(r)'),
        )
    except Exception as exc:                 # same guard the deletes above have
        raise AssertionError(f"could not verify teardown: {exc}") from exc
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
