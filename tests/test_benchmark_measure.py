"""What the runner measures, and what it refuses to measure.

Split out of `tests/test_benchmarks.py` when `benchmarks/run_queries.py` was
split into `run_queries.py` and `measure.py` — and the tests followed the code
rather than staying in a file named for the half they no longer exercise.

Split by SUBJECT: everything here drives `benchmarks/measure.py` — the request,
the guards that refuse a graph the report would misdescribe, the per-query
timing, and the index comparison. `test_benchmarks.py` keeps the rendering and
the CLI.
"""

from __future__ import annotations

import inspect
import json

import pytest

from benchmarks import measure as bench
from benchmarks import measure as bench_measure
from tests.benchmark_support import counts, fake_stats, serve


def test_the_fake_stats_match_shape(monkeypatch):
    """`fake_stats()` stands in for `shape()` in every render test, so a key in
    one and not the other makes those tests pass against a dict the runner
    never produces.

    Compared by CALLING `shape()` against a fake engine, not by regexing its
    source for `"key":` — that read whichever dict literal came last and would
    have missed a key added any other way. And compared in BOTH directions: a
    key `fake_stats()` invents is as much of a lie as one it omits.
    """
    serve(monkeypatch, counts(total=5, submissions=3, product_codes=1,
                              regulations=1, edges=2))
    real = set(bench.shape("http://x"))
    fake = set(fake_stats())
    assert real == fake, (
        f"shape() returns {sorted(real - fake)} which fake_stats() does not, "
        f"and fake_stats() invents {sorted(fake - real)}")


def test_a_co_mingled_engine_is_refused(monkeypatch):
    """The engine used while writing this held the regulatory graph and a
    drug-interactions graph — 273,279 nodes between them. Label counts from
    that describe neither, and nothing in the output would have shown it."""
    serve(monkeypatch, counts(total=273_279, submissions=19_127,
                              product_codes=7_085, regulations=2_284))
    with pytest.raises(SystemExit, match="Another KG is loaded"):
        bench.shape("http://x")


def test_a_clean_graph_is_accepted(monkeypatch):
    serve(monkeypatch, counts(total=28_496, submissions=19_127,
                              product_codes=7_085, regulations=2_284, edges=25_310))
    got = bench.shape("http://x")
    assert got["nodes"] == 28_496
    assert got["by_label"]["Submission"] == 19_127


def test_an_engine_holding_unlabelled_nodes_is_refused(monkeypatch):
    """Co-mingling: this graph loaded, and something else loaded beside it.

    The fixture used to set every expected label to zero, which is not what a
    co-mingled engine looks like — it is what an EMPTY one looks like, and it
    is now caught earlier and more precisely by the missing-label check. A
    graph with another KG in it has these labels populated AND extra nodes on
    top, so that is what this drives: 5 labelled against 8 total.
    """
    serve(monkeypatch, counts(total=8, submissions=3, product_codes=1,
                              regulations=1, edges=2))
    with pytest.raises(SystemExit, match="Another KG is loaded"):
        bench.shape("http://x")


def test_a_node_carrying_two_expected_labels_is_refused(monkeypatch):
    """The direction the equality test could not see, and the reason it matters.

    A node with two of these labels is counted twice, so the per-label sum runs
    ahead of the node count — and that inflation can absorb a foreign node and
    make the totals agree. Measured on 1.1.0: one Alpha, one Beta, one
    Alpha:Beta and one foreign node is 4 nodes summing to 4 over {Alpha, Beta},
    so an equality test passes with a foreign node present.

    Here: 6 labelled against 5 nodes.
    """
    serve(monkeypatch, counts(total=5, submissions=3, product_codes=2,
                              regulations=1, edges=2))
    with pytest.raises(SystemExit, match="carry two of the labels"):
        bench.shape("http://x")


def test_a_label_that_did_not_load_is_refused_even_when_the_totals_agree(monkeypatch):
    """A missing label is invisible to any check on the total.

    If `Regulation` fails to load, the remaining nodes still sum to the node
    count and the old guard passed — producing a page about a regulatory graph
    holding no regulations, with every figure technically correct.
    """
    serve(monkeypatch, counts(total=4, submissions=3, product_codes=1,
                              regulations=0, edges=2))
    with pytest.raises(SystemExit, match="holds no Regulation nodes"):
        bench.shape("http://x")


def test_a_genuinely_empty_engine_is_refused(monkeypatch):
    """A snapshot that failed to import leaves ZERO nodes, which passes any
    check on the total (0 == 0) and would produce a page of zeroes that reads
    as a finding.

    It is now caught by the missing-label check, which names what is absent
    rather than reporting it as an edge problem — an empty engine has no
    Submissions, and saying so points at the import. The edge guard still
    covers the case this cannot: nodes present, edges not.
    """
    serve(monkeypatch, counts(total=0, submissions=0, product_codes=0,
                              regulations=0, edges=0))
    with pytest.raises(SystemExit, match="holds no ProductCode, Regulation, Submission"):
        bench.shape("http://x")


def test_nodes_without_their_edges_are_still_refused(monkeypatch):
    """The case the missing-label check cannot see, and the reason the edge
    guard stays: every label loaded, no edges between them. `EXPECTED_EDGES`
    was declared and never checked before this suite."""
    serve(monkeypatch, counts(total=5, submissions=3, product_codes=1,
                              regulations=1, edges=0))
    with pytest.raises(SystemExit, match="no CLASSIFIED_AS, GOVERNED_BY edges"):
        bench.shape("http://x")


def test_a_rejected_query_stops_the_run(monkeypatch):
    """A parse error must not become an empty results table."""
    serve(monkeypatch, {"MATCH": {"error": "Parse error: unexpected token"}})
    with pytest.raises(SystemExit, match="rejected"):
        bench.run("MATCH (n) RETURN n", "http://x")


def test_a_500_is_not_reported_as_a_missing_engine(monkeypatch):
    import io
    import urllib.error
    monkeypatch.setattr(bench.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b"x"))))
    with pytest.raises(SystemExit, match="500"):
        bench.run("MATCH (n) RETURN n", "http://x")


def test_the_timings_are_a_median_not_a_single_reading():
    assert bench.REPEATS >= 3


def test_the_index_measurement_refuses_a_graph_it_has_already_indexed(monkeypatch):
    """It creates the indexes it measures, so a SECOND run against the same
    instance compares an indexed lookup against an indexed lookup — a scan
    time that is not a scan, and a speedup near 1 that reads as "the index did
    nothing". Refused rather than reported wrongly."""
    serve(monkeypatch, {"SHOW INDEXES": {
        "columns": ["label", "property", "type"],
        "records": [[label, prop, "BTREE"] for label, prop, _ in bench.KEYS]}})
    got = bench.index_effect("http://x", {"Submission": 19_127})
    assert got, "nothing reported at all"
    assert all(row["scan_ms"] is None for row in got), got
    assert "already indexed" in got[0]["note"], got


def test_one_stale_index_does_not_remove_the_keys_still_measurable(monkeypatch):
    """Returning only the clashing keys dropped every key that could still be
    measured, so one stale index on one label silently shortened the table and
    nothing in the output said why.

    Every key is reported: the indexed one carries its note, the rest carry
    figures.
    """
    serve(monkeypatch, {"SHOW INDEXES": {
        "columns": ["label", "property", "type"],
        "records": [["Submission", "id", "BTREE"]]}})
    got = bench.index_effect("http://x", {label: 1 for label, _, _ in bench.KEYS})
    assert len(got) == len(bench.KEYS), got
    noted = [row for row in got if row["scan_ms"] is None]
    measured = [row for row in got if row["scan_ms"] is not None]
    assert [row["key"] for row in noted] == ["Submission.id"], noted
    assert len(measured) == len(bench.KEYS) - 1, measured


def test_a_renamed_show_indexes_column_is_refused_not_ignored(monkeypatch):
    """`row.get("label")` returns None for every row if the column is renamed,
    the set holds `(None, None)`, nothing clashes — and the guard silently
    stops guarding, reporting an indexed instance as though it were fresh."""
    serve(monkeypatch, {"SHOW INDEXES": {
        "columns": ["name", "prop", "type"],
        "records": [["Submission", "id", "BTREE"]]}})
    with pytest.raises(SystemExit, match="SHOW INDEXES no longer returns"):
        bench.existing_indexes("http://x")


def test_the_response_is_closed(monkeypatch):
    """An unclosed response holds its socket until the garbage collector gets
    to it, and this issues one request per repeat per query — nine queries at
    five repeats before the index comparison even starts.

    Watched, not read out of the source: a `with` in a comment satisfies a
    source scan, and a correct refactor that closes it another way fails one.
    """
    closed = []

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            closed.append(True)
            return False

        def read(self):
            return json.dumps({"columns": ["c"], "records": [[1]]}).encode()

    monkeypatch.setattr(bench.urllib.request, "urlopen", lambda *a, **k: R())
    bench.run("MATCH (n) RETURN count(n)", "http://x")
    assert closed, "the response was never closed"


def test_the_response_is_closed_on_the_path_that_stops_the_run(monkeypatch):
    """The success path was watched and the failure path was not.

    A rejected query raises `SystemExit` out of `run()`, and that is the path
    where a leaked socket matters most: the run is ending, nothing else will
    trigger a collection, and the process may sit in a shell for a while
    afterwards. `with` covers it — this is what says so.
    """
    closed = []

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            closed.append(True)
            return False

        def read(self):
            return json.dumps({"error": "Parse error: unexpected token"}).encode()

    monkeypatch.setattr(bench.urllib.request, "urlopen", lambda *a, **k: R())
    with pytest.raises(SystemExit):
        bench.run("MATCH (n) RETURN count(n)", "http://x")
    assert closed, (
        "the response was not closed on the path that raises — the socket is "
        "held until the collector runs, at the end of a run that is stopping")


def test_shape_reports_what_the_page_needs_to_state_it(monkeypatch):
    """`shape()` is where the page's figures come from, so the two keys the
    prose interpolates have to exist — a KeyError at render time would be found
    only by running the whole suite against a live engine."""
    serve(monkeypatch, counts(total=5, submissions=3, product_codes=1,
                              regulations=1, edges=2))
    got = bench.shape("http://x")
    for key in ("blank_definitions", "product_codes"):
        assert key in got, f"shape() no longer returns {key}"
        assert isinstance(got[key], int), (
            f"{key} came back as {type(got[key]).__name__}; the page formats "
            f"it as a number")


def test_a_structured_engine_error_does_not_crash_the_error_path(monkeypatch):
    """`payload['error'][:200]` assumes a string. A dict slices to something
    unreadable and an int raises TypeError inside the error handler — the one
    place that must not fail. `mcp_server/queries.py` had this fix; this file
    did not, on the same engine and the same payload shape.
    """
    for shape_of_error in ({"code": 42, "detail": "nope"}, 42, ["a", "b"]):
        serve(monkeypatch, {"MATCH": {"error": shape_of_error}})
        with pytest.raises(SystemExit) as raised:
            bench.run("MATCH (n) RETURN n", "http://x")
        assert str(shape_of_error)[:20] in str(raised.value), (
            f"a {type(shape_of_error).__name__} error was not reported "
            f"legibly: {raised.value}")


def test_two_keys_on_one_label_each_report_their_own_baseline(monkeypatch):
    """The baseline was keyed by LABEL while `KEYS` is (label, property).

    Two keys on the same label — which the schema permits and this list will
    grow into — collapsed to one entry, and the second silently reported the
    first's scan time as its own. Both the `scan_ms` column and the speedup
    would be wrong, with nothing saying so.

    `KEYS` has three distinct labels today, so this drives the case the shipped
    list does not contain.
    """
    monkeypatch.setattr(bench, "KEYS", [
        ("Submission", "id", "MATCH (s:Submission) WHERE s.id = 'A' RETURN s"),
        ("Submission", "device_name",
         "MATCH (s:Submission) WHERE s.device_name = 'B' RETURN s"),
    ])
    monkeypatch.setattr(bench, "existing_indexes", lambda url: set())
    monkeypatch.setattr(bench, "REPEATS", 1)

    # Two distinguishable timings, one per key, so a collapsed baseline shows.
    timings = {"s.id = 'A'": 10.0, "s.device_name = 'B'": 40.0}

    def fake_run(cypher, url):
        for needle, ms in timings.items():
            if needle in cypher:
                return {"records": []}, ms
        return {"records": []}, 1.0
    monkeypatch.setattr(bench, "run", fake_run)

    got = {row["key"]: row for row in bench.index_effect("http://x", {"Submission": 5})}
    assert got["Submission.id"]["scan_ms"] == 10.0
    assert got["Submission.device_name"]["scan_ms"] == 40.0, (
        "the second key on this label reported the first key's baseline — the "
        "map is keyed by label again")


def test_a_warm_up_round_is_discarded_and_the_median_is_the_median(monkeypatch):
    """The median had no test: rewriting the loop to take one reading while
    leaving `REPEATS = 5` passed green, as did swapping `median` for `fmean`.
    The only guard was `REPEATS >= 3`, which pins a constant rather than a
    behaviour.

    It matters because two figures for the same operation disagreed by 65% on
    the committed page — 30.1 ms as Q4 against 51.1 ms as the index baseline —
    and cold start sitting inside the medians is the cause.
    """
    readings = iter([999.0, 10.0, 50.0, 20.0, 90.0, 30.0])
    calls = []

    def fake_run(cypher, url):
        calls.append(cypher)
        return {"columns": [], "records": []}, next(readings)
    monkeypatch.setattr(bench_measure, "run", fake_run)
    monkeypatch.setattr(bench_measure, "REPEATS", 5)

    got = bench_measure.measure({"name": "Q", "cypher": "MATCH (n) RETURN n"},
                                "http://x")
    assert len(calls) == 6, (
        "the warm-up round was not run, or REPEATS readings were not taken")

    # Asserted on what `measure()` REPORTS, not on the raw timings. Checking
    # the list only proved the warm-up was dropped; swapping
    # `statistics.median` for `fmean` still passed, which is exactly the
    # weakness this test was added for. The mean of these readings is 40 and
    # the median is 30, so the two are now distinguishable.
    assert got["median_ms"] == 30.0, (
        f"the reported figure is {got['median_ms']}, which is the mean of the "
        f"readings and not the median — a single slow round now moves the "
        f"published number")
    assert got["min_ms"] == 10.0 and got["max_ms"] == 90.0, (
        "min/max no longer describe the readings the median came from")

    # And the warm-up must not be inside any of the three.
    assert 999.0 not in (got["median_ms"], got["min_ms"], got["max_ms"]), (
        "the warm-up reading reached the page — cold start is being reported "
        "as the figure, which is what made two numbers for one operation "
        "disagree by 65%")


def test_both_timing_callers_use_the_same_helper():
    """They each kept their own loop, and that is how they drifted apart — one
    measured the same point lookup 65% slower than the other on one page. The
    warm-up fix has to apply to both or the disagreement returns."""
    source = inspect.getsource(bench_measure)
    body = source.split("def timed(")[1]
    assert body.count("statistics.median(run(") == 0, (
        "a second timing loop has reappeared alongside `timed()`")
    assert "timed(" in inspect.getsource(bench_measure.measure)
    assert "timed(" in inspect.getsource(bench_measure.index_effect)


def test_a_version_that_cannot_be_read_stops_the_run(monkeypatch):
    """The finding is version-scoped, so the page is not written against an
    engine that cannot say what it is."""
    def refuse(*a, **k):
        raise bench_measure.urllib.error.URLError("no status endpoint")
    monkeypatch.setattr(bench_measure.urllib.request, "urlopen", refuse)
    with pytest.raises(SystemExit, match="could not read the engine version"):
        bench_measure.engine_version("http://x")


def test_a_count_query_that_answers_with_nothing_is_refused(monkeypatch):
    """`rows_of(payload)[0]["c"]` was written at seven call sites, and every one
    raised `IndexError` on an empty result or `KeyError` on a renamed alias —
    from inside a run, as a traceback rather than a message.

    The alias case is the live one: the stub answers any unmatched query with
    `{"columns": ["c"], ...}`, so renaming `count(p) AS c` to `AS total` left
    the suite green and would have been a KeyError against a real engine.
    """
    with pytest.raises(SystemExit, match="no rows for a count query"):
        bench_measure.one_count({"columns": ["c"], "records": []}, "MATCH ...")
    with pytest.raises(SystemExit, match="without its `c` column"):
        bench_measure.one_count({"columns": ["total"], "records": [[7]]}, "MATCH ...")
    assert bench_measure.one_count({"columns": ["c"], "records": [[7]]}, "x") == 7
