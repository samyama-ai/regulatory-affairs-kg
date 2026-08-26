"""The MCP tools against a loaded engine.

Split out of `tests/test_mcp_queries.py` when it passed the 500-line review
limit — review skips a file over it and blocks the PR, so an oversized test
file is an unread one.

Split by SUBJECT, on the line that already divided the file: these nine need a
real engine holding the graph, and everything in `test_mcp_queries.py` does
not. That boundary matters more than length — these are the only tests that can
fail because of the DATA rather than the code, and the only ones that skip when
no engine is reachable.
"""

from __future__ import annotations
import os
import pytest
from mcp_server import engine, queries
from tests.mcp_support import FIXTURE, configured_test_url, engine_available


@pytest.fixture
def loaded_engine(monkeypatch):
    url = configured_test_url()
    if not url or not engine_available(url):
        message = ("no engine at SAMYAMA_TEST_URL"
                   if not url else f"no engine at {url}")
        if os.environ.get("SAMYAMA_REQUIRE_ENGINE") == "1":
            pytest.fail(f"{message} — SAMYAMA_REQUIRE_ENGINE=1 forbids skipping this")
        pytest.skip(f"{message} — set it to a FRESH instance, never the demo engine")

    # Point the queries at the test engine only for the duration of the test.
    monkeypatch.setenv("SAMYAMA_URL", url)

    before = engine.run("MATCH (n) RETURN count(n) AS n").rows
    existing = before[0]["n"] if before else 0
    if existing:
        pytest.fail(
            f"SAMYAMA_TEST_URL points at an engine holding {existing:,} nodes. "
            f"These tests write and DETACH DELETE; run them against a fresh "
            f"instance. See DATASET-CARD.md issue 10.")

    # Every load is checked. An unchecked `run()` returning an error left the
    # graph half-built, and the first assertion in the test then failed with
    # "K999001 not found" — which reads as a broken traversal rather than a
    # fixture that never loaded.
    #
    # In a try/finally, because the check above can now RAISE: without it a
    # failed load skips the teardown, leaves `source='test'` nodes behind, and
    # every later run aborts on the "engine holding N nodes" guard — one bad
    # statement poisoning the suite until someone clears the engine by hand.
    try:
        for statement in FIXTURE:
            result = engine.run(statement)
            assert result.ok, (
                f"the fixture did not load, so nothing below is testing what "
                f"it claims: {result.error}\n  statement: {statement[:120]}")
        yield
    finally:
        # UNCONDITIONAL, not scoped to `source = 'test'`, and that is a
        # correction rather than a shortcut.
        #
        # The scoped form was believed to be the safety net — "point this at a
        # loaded graph and only test nodes die". Measured on 1.1.0, it is not:
        # after a DETACH DELETE, a node created later with NO `source`
        # property at all is still returned by `WHERE n.source = 'test'`, so
        # the scoped delete removes nodes it never wrote. It deletes MORE than
        # it claims, not less. (DATASET-CARD.md known issue 10, whose stated
        # mechanism this reproduces.)
        #
        # The real protection is the emptiness guard above, which refuses to
        # run at all unless the engine holds zero nodes. Given that, deleting
        # everything is exactly what "remove what this fixture wrote" means —
        # and it does not rest on a WHERE clause this engine gets wrong.
        engine.run("MATCH (n) DETACH DELETE n")

        left = engine.run("MATCH (n) RETURN count(n) AS n").rows
        remaining = left[0]["n"] if left else 0
        assert not remaining, (
            f"{remaining:,} node(s) survived the teardown, so the next run "
            f"will fail on the emptiness guard instead of here.")


def test_the_change_impact_query_returns_the_clearance(loaded_engine):
    """The question this graph exists for, run end to end."""
    got = queries.clearances_under_regulation("870.5150")
    assert got["error"] is None
    assert any(row["clearance"] == "K999001" for row in got["clearances"])


def test_the_count_is_the_total_and_the_listing_is_the_page(loaded_engine):
    """The two tools answer different questions, and the old test could not
    tell them apart.

    `clearances_under_regulation` takes `limit` (25 by default) and applies it;
    `count_clearances_under_regulation` does not. Asserting they are equal held
    only because the fixture carries ONE clearance — 1 == 1 passes whether the
    count is right, wrong, or also limited. An agent that reads a page of 25
    and a total of 25 concludes it has seen everything.

    So: a second clearance, and a limit that bites.
    """
    for statement in (
        "CREATE (s:Submission {id: 'K999002', device_name: 'A second device', "
        "applicant: 'Acme', decision_date: '2024-02-03', "
        "advisory_committee: 'Cardiovascular', source: 'test'})",
        "MATCH (s:Submission {id: 'K999002'}), (p:ProductCode {product_code: 'DXY'}) "
        "CREATE (s)-[:CLASSIFIED_AS]->(p)",
    ):
        assert engine.run(statement).ok

    counted = queries.count_clearances_under_regulation("870.5150")
    assert counted["error"] is None
    assert counted["total"] == 2, (
        f"two clearances reach 870.5150; the count says {counted['total']}")

    full = queries.clearances_under_regulation("870.5150")
    assert full["error"] is None
    assert len(full["clearances"]) == 2, "the unlimited listing lost a row"

    paged = queries.clearances_under_regulation("870.5150", limit=1)
    assert paged["error"] is None
    assert len(paged["clearances"]) == 1, "the limit was not applied"
    assert counted["total"] == 2, (
        "the count is limited too — an agent reading a full page would have "
        "no way to learn there is more")


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


def test_an_apostrophe_in_an_argument_reaches_the_engine(loaded_engine):
    """The path the whole of this PR's literal handling turns on, run against
    a real 1.1.0 rather than against the encoder's own output.

    The previous implementation escaped with backslashes, which this engine
    does not have: `'O\\'BRIEN'` is a parse error, so the tool returned
    "query rejected" for a perfectly ordinary product code. Every engine-backed
    test used quote-free fixture values, so nothing caught it.

    Measured before fixing: the old form returns HTTP 400 from the engine.
    """
    got = queries.regulations_for_product("O'BRIEN")
    assert got["error"] is None, got["error"]
    assert [r["cfr_section"] for r in got["regulations"]] == ["870.5150"], got


def test_a_double_quote_in_an_argument_reaches_the_engine(loaded_engine):
    """The other delimiter. A value containing a double quote must be wrapped
    in single quotes — there is no third option, and no escape."""
    got = queries.regulations_for_product('say "hi"')
    assert got["error"] is None, got["error"]
    assert got["regulations"] == [], got


def test_a_clearance_counted_once_even_if_it_reaches_a_rule_twice(loaded_engine):
    """`count(s)` counts PATH MATCHES. A Submission reaching one Regulation
    through two ProductCodes is one clearance, not two — and this is the figure
    an agent quotes as blast radius.

    Measured on the loaded graph: at most one ProductCode per Submission, and
    zero regulations where the two counts differ. So the fixture builds the
    fan-out the real data does not have, which is the only way to see it.
    """
    for statement in (
        "CREATE (p2:ProductCode {product_code: 'DXZ', device_class: '2', "
        "source: 'test'})",
        "MATCH (p:ProductCode {product_code: 'DXZ'}), "
        "(r:Regulation {cfr_section: '870.5150'}) CREATE (p)-[:GOVERNED_BY]->(r)",
        "MATCH (s:Submission {id: 'K999001'}), (p:ProductCode {product_code: 'DXZ'}) "
        "CREATE (s)-[:CLASSIFIED_AS]->(p)",
    ):
        assert engine.run(statement).ok

    got = queries.count_clearances_under_regulation("870.5150")
    assert got["total"] == 1, (
        f"one clearance reaching the rule through two product codes was counted "
        f"{got['total']} times")

    # The same fan-out through the other count. Both quote blast radius, so
    # both have to survive it — the first version of this test covered one and
    # the second went on double-counting.
    ranked = queries.busiest_regulations(limit=5)
    assert ranked["error"] is None, ranked
    for row in ranked["regulations"]:
        if row["cfr_section"] == "870.5150":
            assert row["clearances"] == 1, (
                f"busiest_regulations counted the same clearance "
                f"{row['clearances']} times")
            break
    else:
        raise AssertionError(f"870.5150 is missing from the ranking: {ranked}")


def test_a_product_code_with_no_regulation_is_found_not_missing(loaded_engine):
    """The inner join made "this product code is unregulated" and "there is no
    such product code" the same empty list — the exact ambiguity this module
    exists to prevent, in the tool an agent reaches for first.

    Measured against the loaded graph before the fix: 902 product codes carry
    no regulation number, so the first case is common rather than theoretical.
    """
    unregulated = engine.run(
        "CREATE (p:ProductCode {product_code: 'NOREG', device_class: '3', "
        "definition: 'No regulation published', source: 'test'})")
    assert unregulated.ok, unregulated.error

    exists = queries.regulations_for_product("NOREG")
    missing = queries.regulations_for_product("ZZZZ")

    assert exists["found"] is True, "a real but unregulated product code read as absent"
    assert exists["regulations"] == [], "it has no regulation, so none should be listed"
    assert missing["found"] is False, "a product code that does not exist read as present"
    assert exists["error"] is None and missing["error"] is None


def test_a_class_is_counted_whole_not_only_its_regulated_part(loaded_engine):
    """`product_codes_by_class` inner-joined GOVERNED_BY, so it answered with
    only the product codes that carry a regulation.

    Measured on the loaded graph: class 3 holds 531 product codes and 145 have
    a regulation — the tool returned 145 and called it the answer, losing 73%
    of the class its own docstring calls the high-risk route.
    """
    for statement in (
        "CREATE (p:ProductCode {product_code: 'CLSA', device_class: '9', "
        "definition: 'regulated', source: 'test'})",
        "CREATE (p:ProductCode {product_code: 'CLSB', device_class: '9', "
        "definition: 'unregulated', source: 'test'})",
        "MATCH (p:ProductCode {product_code: 'CLSA'}), "
        "(r:Regulation {cfr_section: '870.5150'}) CREATE (p)-[:GOVERNED_BY]->(r)",
    ):
        assert engine.run(statement).ok

    out = queries.product_codes_by_class("9", limit=100)
    codes = {r["product_code"] for r in out["product_codes"]}
    assert codes == {"CLSA", "CLSB"}, (
        f"the unregulated product code was dropped: {sorted(codes)}")
    assert out["count"] == 2
    assert out["unregulated"] == 1, "the caller is not told how many carry no regulation"


def test_a_clearance_whose_category_is_unregulated_is_still_found(loaded_engine):
    """Same defect one hop further along: the three-hop inner chain made a real
    clearance whose product code has no regulation indistinguishable from a
    k-number that was never issued."""
    for statement in (
        "CREATE (p:ProductCode {product_code: 'UNREG', device_class: '2', "
        "definition: 'no regulation', source: 'test'})",
        "CREATE (s:Submission {id: 'K999777', device_name: 'Orphan device', "
        "applicant: 'Acme', decision_date: '2024-05-05', source: 'test'})",
        "MATCH (s:Submission {id: 'K999777'}), (p:ProductCode {product_code: 'UNREG'}) "
        "CREATE (s)-[:CLASSIFIED_AS]->(p)",
    ):
        assert engine.run(statement).ok

    real = queries.regulation_for_clearance("K999777")
    absent = queries.regulation_for_clearance("K000000")

    assert real["found"] is True, "a real clearance read as not found"
    assert real["governed_by"][0]["cfr_section"] is None, (
        "its category carries no regulation, which is a fact, not an absence")
    assert absent["found"] is False
